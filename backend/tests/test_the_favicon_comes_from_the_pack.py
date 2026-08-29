"""#117 — `faviconUrl` resolves onto both entry pages, server-side, or falls back to Domino's.

The value is not free text. It is written into `href="…"` on the first HTML a viewer ever sees,
and the only images this process serves are the shell's own and the ones under `/opt/sage/brand/`.
A remote URL is refused rather than fetched (ADR-0014): it breaks an air-gapped install and hands
the partner's CDN a log of every user's session. Every refusal falls back to the Domino default,
because a brand pack must never be able to stop the Workbench booting.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator.brand import DEFAULT, load

_WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
SHELL = (_WB / "index.html").read_text()
DOOR = (_WB / "door.html").read_text()

# The drawn favicon does not exist yet, so the default is the logo (#117). This constant is the
# one place to change when the asset lands.
DOMINO_DEFAULT = "./img/domino-logo.svg"


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)
    # A refusal is logged once per process; each test is its own first time.
    monkeypatch.setattr("sage.orchestrator.brand._WARNED", set())


def _pack(tmp_path, monkeypatch, **keys) -> dict:
    path = tmp_path / "brand.json"
    path.write_text(json.dumps(keys))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    return load()


def _client(monkeypatch, *, door: bool) -> TestClient:
    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "proxy_is_app", lambda: door)
    return TestClient(appmod.control_app)


# --- the slot and the default -----------------------------------------------------------------


def test_the_default_pack_names_the_domino_favicon():
    assert DEFAULT["faviconUrl"] == DOMINO_DEFAULT
    assert load()["faviconUrl"] == DOMINO_DEFAULT


def test_both_entry_pages_fill_the_icon_slot_from_the_pack():
    """The source, not the response: a literal href is what the browser would paint first."""
    for page in (SHELL, DOOR):
        assert '<link rel="icon" href="{faviconUrl}" />' in page


@pytest.mark.parametrize("door", [False, True])
def test_an_unset_favicon_serves_the_domino_default_on_both_pages(monkeypatch, door):
    r = _client(monkeypatch, door=door).get("/")
    assert r.status_code == 200
    assert f'<link rel="icon" href="{DOMINO_DEFAULT}" />' in r.text


@pytest.mark.parametrize("door", [False, True])
def test_the_packs_favicon_is_in_the_html_the_server_sends(tmp_path, monkeypatch, door):
    _pack(tmp_path, monkeypatch, faviconUrl="./brand/acme-favicon.svg")
    r = _client(monkeypatch, door=door).get("/")
    assert '<link rel="icon" href="./brand/acme-favicon.svg" />' in r.text
    # Scoped to the icon link, not the whole document: the default favicon IS the logo until the
    # asset is drawn, and the door renders the logo in an <img> that this pack does not change.
    assert f'<link rel="icon" href="{DOMINO_DEFAULT}"' not in r.text


# --- what a pack may name ----------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "./brand/acme.svg",
    "./brand/acme.png",
    "brand/acme.svg",
    "./brand/sub/acme.SVG",
    "./img/domino-logo.svg",
])
def test_a_relative_image_under_our_own_origin_is_taken(tmp_path, monkeypatch, url):
    assert _pack(tmp_path, monkeypatch, faviconUrl=url)["faviconUrl"] == url


@pytest.mark.parametrize("url", [
    "https://cdn.acme.example/favicon.svg",
    "http://cdn.acme.example/favicon.svg",
    "//cdn.acme.example/favicon.svg",
    "data:image/svg+xml;base64,PHN2Zy8+",
])
def test_a_remote_url_is_refused_and_the_default_stands(tmp_path, monkeypatch, url):
    assert _pack(tmp_path, monkeypatch, faviconUrl=url)["faviconUrl"] == DOMINO_DEFAULT


@pytest.mark.parametrize("url", [
    "https://cdn.acme.example/logo.svg",
    "http://cdn.acme.example/logo.svg",
    "//cdn.acme.example/logo.svg",
    "data:image/svg+xml;base64,PHN2Zy8+",
])
def test_the_logo_takes_the_same_boundary_as_the_favicon(tmp_path, monkeypatch, url):
    """`logoUrl` reaches an <img src> in the shell exactly as `faviconUrl` reaches a <link href>.
    Validating one and not the other left the beacon and the air-gap break open one key over, so
    the rule is asserted on both keys and not just the one that happened to be written second."""
    assert _pack(tmp_path, monkeypatch, logoUrl=url)["logoUrl"] == DEFAULT["logoUrl"]


def test_the_logo_keeps_a_relative_url(tmp_path, monkeypatch):
    assert _pack(tmp_path, monkeypatch, logoUrl="./brand/acme-logo.svg")["logoUrl"] == "./brand/acme-logo.svg"


@pytest.mark.parametrize("door", [False, True])
def test_a_remote_url_never_reaches_the_page(tmp_path, monkeypatch, door):
    """Refused rather than fetched — and refused before it can be handed to a browser to fetch."""
    _pack(tmp_path, monkeypatch, faviconUrl="https://cdn.acme.example/favicon.svg")
    r = _client(monkeypatch, door=door).get("/")
    assert "cdn.acme.example" not in r.text
    assert f'<link rel="icon" href="{DOMINO_DEFAULT}" />' in r.text


@pytest.mark.parametrize("url", [
    "./brand/acme.ico",
    "./brand/acme.gif",
    "./brand/acme.json",
    "./brand/opencode.json",
    "./brand/acme",
])
def test_only_svg_and_png_may_be_named(tmp_path, monkeypatch, url):
    assert _pack(tmp_path, monkeypatch, faviconUrl=url)["faviconUrl"] == DOMINO_DEFAULT


@pytest.mark.parametrize("url", [
    "../opencode.json.svg",
    "./brand/../../opencode.json.svg",
    "/brand/acme.svg",            # absolute: walks out of the platform's proxy prefix
    "./brand/acme.svg?v=2",
    './brand/acme.svg" onload="alert(1)',
    "./brand/ac me.svg",
    "./brand/acme.svg\nx.svg",
    "",
    "   ",
])
def test_anything_that_is_not_a_plain_relative_path_is_refused(tmp_path, monkeypatch, url):
    assert _pack(tmp_path, monkeypatch, faviconUrl=url)["faviconUrl"] == DOMINO_DEFAULT


@pytest.mark.parametrize("value", [None, 5, ["./brand/acme.svg"], {"url": "./brand/acme.svg"}])
def test_a_value_that_is_not_a_string_is_refused(tmp_path, monkeypatch, value):
    assert _pack(tmp_path, monkeypatch, faviconUrl=value)["faviconUrl"] == DOMINO_DEFAULT


def test_a_refused_favicon_leaves_the_rest_of_the_pack_alone(tmp_path, monkeypatch):
    """A typo in one key must not cost a partner the name they set in another."""
    pack = _pack(tmp_path, monkeypatch, productName="Acme", faviconUrl="https://acme.example/f.svg")
    assert pack["productName"] == "Acme"
    assert pack["faviconUrl"] == DOMINO_DEFAULT


def test_a_refusal_says_so_out_loud_and_names_the_value(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        _pack(tmp_path, monkeypatch, faviconUrl="https://cdn.acme.example/favicon.svg")
    assert "faviconUrl" in caplog.text
    assert "cdn.acme.example" in caplog.text


def test_the_complaint_is_made_once_however_often_the_pack_is_read(tmp_path, monkeypatch, caplog):
    """`load()` runs per request; a bad key nobody is going to change must not fill the log."""
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        for _ in range(4):
            _pack(tmp_path, monkeypatch, faviconUrl="./brand/acme.ico")
    assert caplog.text.count("faviconUrl") == 1


def test_faviconurl_is_a_known_key_and_draws_no_unknown_key_warning(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="sage.orchestrator.brand"):
        _pack(tmp_path, monkeypatch, faviconUrl="./brand/acme.svg")
    assert "unknown key" not in caplog.text


def test_the_resolved_pack_is_on_the_api(tmp_path, monkeypatch):
    _pack(tmp_path, monkeypatch, faviconUrl="./brand/acme.png")
    r = _client(monkeypatch, door=False).get("/api/brand")
    assert r.json()["faviconUrl"] == "./brand/acme.png"
