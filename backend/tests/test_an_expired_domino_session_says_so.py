"""An expired Domino login says so, instead of reading as a broken app (#754).

When the login in front of a Workbench lapses, Domino's proxy answers every path under the session
with its own HTML page. Sage never writes HTML on its API — FastAPI answers JSON there — so the
page can only be the proxy's. The fetch layer used to read it three wrong ways: a 2xx became `{}`,
which is an empty app list ("No Built Apps yet"); a 4xx kept only its status line ("404 Not
Found"); and a direct `fetch().json()` in the store threw a parse error, which surfaced as "Preview
is unavailable". Signing in again restored everything on the same boot.

The harness boots the real app.js against a serving proxy, lets the login lapse, and reads each of
those three paths.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "session_expired_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

_node = pytest.mark.skipif(shutil.which("node") is None,
                           reason="node is not on PATH (it is in the Sage image)")

LAPSED = ["html-404", "html-401", "html-200"]


def _run(mode: str) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"mode": mode}), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@_node
@pytest.mark.parametrize("mode", LAPSED)
def test_the_app_list_is_not_read_as_empty_or_as_a_status_line(mode):
    """`request()`, which every listing goes through. A 200 page was an empty list, and a 404 or
    401 page was an error carrying only "404 Not Found"."""
    out = _run(mode)

    assert "value" not in out["apps"], out["apps"]
    assert out["apps"]["error"]["typed"] is True, out["apps"]
    assert out["buildState"]["error"]["typed"] is True, out["buildState"]


@_node
@pytest.mark.parametrize("mode", LAPSED)
def test_the_preview_status_is_not_reported_as_a_failed_preview(mode):
    """The store's own fetch of /api/preview/status. Its parse error was drawn as "Preview didn't
    start" with "Unexpected token '<'" under it. The preview still records a failed read; the
    screen test below is what proves nothing draws it."""
    out = _run(mode)

    assert "Unexpected token" not in str(out["preview"]["error"]), out["preview"]
    assert out["sessionExpired"] is True


@_node
@pytest.mark.parametrize("mode", LAPSED)
def test_healthz_is_read_through_the_same_check(mode):
    """/healthz sits outside /api with a fetch of its own, and is still Sage's JSON route."""
    assert _run(mode)["healthz"]["error"]["typed"] is True


@_node
@pytest.mark.parametrize("mode", LAPSED)
def test_the_screen_says_the_session_looks_expired_and_offers_to_sign_in(mode):
    """One state, in place of the whole Workbench: neither the empty app list nor the failed preview
    card can be drawn under it, because the Shell they live in is not drawn."""
    out = _run(mode)

    assert "Shell" in out["bootTags"]
    assert "Shell" not in out["tags"]
    said = " ".join(out["words"])
    assert "session looks expired" in said, said
    assert "Sign in again" in said, said
    assert "No Built Apps yet" not in said
    assert "Preview didn't start" not in said
    assert out["reloaded"] == 1


@_node
@pytest.mark.parametrize("mode", ["html-502", "json-404"])
def test_a_warming_proxy_or_sages_own_refusal_is_not_an_expired_session(mode):
    """The two answers that look nearest. A 502 page is the proxy waiting on a container that is
    still starting, which ADR-0027 waits out; a JSON 404 is Sage itself answering."""
    out = _run(mode)

    assert out["sessionExpired"] is False
    assert out["apps"]["error"]["typed"] is False
    assert "Shell" in out["tags"]


def test_every_fetch_of_the_workbenchs_own_routes_goes_through_one_check():
    """A direct `fetch()` is how the parse error in #754 got past `request()`. The only ones left
    are the one inside `sageFetch` and the preview page probe, which fetches the person's own app —
    whose pages are HTML by design — rather than a Sage route."""
    bare = re.compile(r"(?<![\w.])fetch\(")
    found = {}
    for path in sorted(_JS.rglob("*.js")):
        hits = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if bare.search(line)]
        if hits:
            found[path.relative_to(_JS).as_posix()] = hits

    assert found == {
        "api.js": ["const res = await fetch(url, options);"],
        "store.js": ["const res = await fetch(url, { cache: 'no-store' });"],
    }, found
