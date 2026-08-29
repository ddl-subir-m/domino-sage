"""#117 — the brand image mount is a security boundary, not a tidiness preference.

`/opt/sage/brand/` is published. `/opt/sage/` is not, and the difference is `opencode.json`:
the gateway configuration this process runs on, one directory up from the partner's logo. A
static mount one level too high serves it to anyone who can reach the shell, so this file names
that file and walks at it from every direction the route offers.

Two clients, on purpose. `TestClient` is what a browser sends, and httpx normalises `..` out of
a path before it leaves — so a test written only through it would prove the browser is well
behaved and nothing about the route. `_through_the_route` hands the mounted app the raw path
instead, which is what an attacker with a socket gets to do.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

import sage.orchestrator.app as appmod

SECRET = "opencode.json"


@pytest.fixture
def brand_dir(tmp_path, monkeypatch) -> Path:
    """A stand-in for /opt/sage, laid out the way the Environment image lays it out.

    `all_directories` is computed once in StaticFiles.__init__, so pointing `directory` alone
    would leave the mount serving the real /opt/sage/brand and the test asserting nothing.
    """
    opt_sage = tmp_path / "opt" / "sage"
    brand = opt_sage / "brand"
    (brand / "sub").mkdir(parents=True)
    (opt_sage / SECRET).write_text('{"provider": {"sage-gateway": {"options": {"apiKey": "s3cret"}}}}')
    (opt_sage / "brand.json").write_text('{"productName": "Acme"}')
    (brand / "acme-logo.svg").write_text("<svg/>")
    (brand / "acme-favicon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (brand / "notes.txt").write_text("not an image")
    monkeypatch.setattr(appmod._brand_images, "directory", str(brand))
    monkeypatch.setattr(appmod._brand_images, "all_directories", [str(brand)])
    return brand


@pytest.fixture
def client(brand_dir) -> TestClient:
    return TestClient(appmod.control_app)


def _through_the_route(sub_path: str) -> int:
    """The status for a raw request path, with no HTTP client in between to tidy it up first.

    A browser resolves `..` before it sends; a script does not have to, and the route has to be
    right for the script. This is exactly what the Mount hands the static app.
    """
    scope = {
        "type": "http",
        "method": "GET",
        "path": sub_path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    app = appmod._brand_images

    async def go() -> int:
        try:
            return (await app.get_response(app.get_path(scope), scope)).status_code
        except HTTPException as exc:
            return exc.status_code

    return asyncio.run(go())


# --- what the mount is for ------------------------------------------------------------------


def test_a_partners_svg_and_png_are_served(client):
    svg = client.get("/brand/acme-logo.svg")
    assert svg.status_code == 200
    assert svg.text == "<svg/>"
    assert client.get("/brand/acme-favicon.png").status_code == 200


def test_a_served_image_revalidates_rather_than_going_stale(client):
    """The pack names one filename and a partner replaces the bytes under it."""
    assert client.get("/brand/acme-logo.svg").headers["cache-control"] == "no-cache"


# --- opencode.json is unreachable ------------------------------------------------------------


@pytest.mark.parametrize("path", [
    f"/brand/{SECRET}",                      # straight at it, if the mount were one level up
    f"/brand/%2e%2e/{SECRET}",               # encoded, so httpx does not resolve it away first
    f"/brand/%2e%2e%2f{SECRET}",
    f"/brand/sub/%2e%2e/%2e%2e/{SECRET}",
    f"/brand/..%2f{SECRET}",
    f"/brand/....//{SECRET}",                # the sanitiser that strips one ".." and stops
    f"/brand/%2e%2e/{SECRET}.svg",           # an allowed extension does not buy a walk
    "/brand/%2e%2e/brand.json",              # the pack itself is not a brand image either
])
def test_opencode_json_is_unreachable_over_http(client, path):
    r = client.get(path)
    assert r.status_code == 404, path
    assert "s3cret" not in r.text
    assert "sage-gateway" not in r.text


@pytest.mark.parametrize("path", [
    f"/{SECRET}",
    f"/../{SECRET}",
    f"/../../{SECRET}",
    f"/sub/../../{SECRET}",
    f"/./../{SECRET}",
    f"//../{SECRET}",
    f"/../{SECRET}.svg",
    f"/../{SECRET}.png",
    f"/..%2f{SECRET}",
    "/../brand.json",
    "/../brand.json.svg",
])
def test_opencode_json_is_unreachable_on_a_raw_path(brand_dir, path):
    """No HTTP client in the way, so the path reaches the route exactly as written."""
    assert _through_the_route(path) == 404, path


def test_an_absolute_path_does_not_escape(brand_dir):
    """`/opt/sage/opencode.json` as the whole request path, in case the join is naive."""
    assert _through_the_route(str(brand_dir.parent / SECRET)) == 404


def test_a_symlink_planted_in_the_brand_directory_cannot_reach_out_of_it(brand_dir):
    """Allowed extension, allowed name, and the bytes on the end of it are the gateway config.

    Symlinks are resolved before the containment check, which is the whole reason this route
    does not set `follow_symlink`.
    """
    (brand_dir / "innocent.svg").symlink_to(brand_dir.parent / SECRET)
    r = TestClient(appmod.control_app).get("/brand/innocent.svg")
    assert r.status_code == 404
    assert "s3cret" not in r.text
    assert _through_the_route("/innocent.svg") == 404


def test_a_symlinked_directory_cannot_reach_out_either(brand_dir):
    (brand_dir / "up").symlink_to(brand_dir.parent, target_is_directory=True)
    assert TestClient(appmod.control_app).get(f"/brand/up/{SECRET}").status_code == 404
    assert _through_the_route(f"/up/{SECRET}") == 404


# --- the allowlist --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["notes.txt", "notes", "acme-logo.SVG.txt", "acme-logo.svg.json"])
def test_only_svg_and_png_are_served(client, name):
    assert client.get(f"/brand/{name}").status_code == 404


def test_the_extension_check_is_case_insensitive(brand_dir, client):
    (brand_dir / "ACME.SVG").write_text("<svg/>")
    assert client.get("/brand/ACME.SVG").status_code == 200


def test_a_refused_extension_looks_exactly_like_a_miss(client):
    """A probe must not learn what is up there from the difference between the two answers."""
    refused = client.get("/brand/notes.txt")
    missing = client.get("/brand/nothing-here.txt")
    assert (refused.status_code, refused.text) == (missing.status_code, missing.text)


# --- no listing, no fallthrough ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/brand/", "/brand/sub/"])
def test_the_directory_does_not_list(client, path):
    r = client.get(path)
    assert r.status_code == 404
    assert "acme-logo.svg" not in r.text
    assert "notes.txt" not in r.text


def test_a_miss_does_not_fall_through_to_the_shell(client):
    """A Mount is terminal; the 404 must not turn into the entry page with a 200 on it."""
    r = client.get("/brand/no-such-image.svg")
    assert r.status_code == 404
    assert "<!DOCTYPE html>" not in r.text


def test_a_missing_brand_directory_still_boots_and_answers_404(monkeypatch, tmp_path):
    """A laptop has no /opt/sage at all, and the Workbench has to run there anyway."""
    absent = tmp_path / "never-created"
    monkeypatch.setattr(appmod._brand_images, "directory", str(absent))
    monkeypatch.setattr(appmod._brand_images, "all_directories", [str(absent)])
    monkeypatch.setattr(appmod._brand_images, "config_checked", False)
    assert TestClient(appmod.control_app).get("/brand/acme-logo.svg").status_code == 404


def test_the_mount_is_the_brand_directory_and_never_its_parent():
    """The one-line version of everything above, asserted against the constant itself."""
    from sage.orchestrator.brand import BRAND_DIR

    assert BRAND_DIR == Path("/opt/sage/brand")
    assert Path(appmod._brand_images.directory) == BRAND_DIR
    assert BRAND_DIR.parent == Path("/opt/sage")
