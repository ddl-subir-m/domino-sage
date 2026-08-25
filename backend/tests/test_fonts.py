"""Nothing Sage ships asks Google for a font (#19).

Every page Sage puts in a browser used to <link> fonts.googleapis.com, which then pulled woff2 files
from fonts.gstatic.com. `display=swap` made that failure quiet rather than loud: on a tenant with
locked-down egress the request just fails, the page falls back to system-ui, and nothing says why —
so the thing worth pinning is the absence of the request, which no screenshot would ever show.

The other half is that the font actually arrives. A page asking for a file nobody serves fails the
same silent way, so each test resolves the url OUT OF the page and fetches THAT, rather than
restating a path the page might no longer use.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]

# Every entry page Sage ships: the builder, the hub, and the one that lands in dist/index.html of
# every published app. The third is the one a customer's viewer loads, and the reason this matters.
PAGES = [
    REPO / "backend" / "sage" / "ui" / "index.html",
    REPO / "backend" / "sage" / "hub" / "ui" / "index.html",
    REPO / "backend" / "sage" / "workbench" / "index.html",
    REPO / "template" / "react-vite" / "index.html",
]

FONT_URL = re.compile(r"src:\s*url\(['\"]?([^'\")]+\.woff2)")


def _asked_for(text: str) -> str:
    """The woff2 the @font-face in this file asks for."""
    found = FONT_URL.search(text)
    assert found, "no @font-face src"
    return found.group(1)


def test_no_page_sage_ships_asks_google_for_a_font():
    # "//" and not the bare host, so this catches a URL and not a sentence about one — the comments
    # these pages now carry explain what was removed, and name both hosts to do it.
    for page in PAGES:
        text = page.read_text()
        assert "//fonts.googleapis.com" not in text, page
        assert "//fonts.gstatic.com" not in text, page


def test_the_builder_serves_the_font_its_page_asks_for():
    import sage.orchestrator.app as appmod

    url = _asked_for(appmod._UI.read_text())
    r = TestClient(appmod.control_app).get(f"/{url}")

    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"
    assert r.content[:4] == b"wOF2"


def test_the_hub_serves_the_font_its_page_asks_for():
    from sage.hub import app as hubmod

    url = _asked_for(hubmod._UI.read_text())
    r = TestClient(hubmod.app).get(f"/{url}")

    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"
    assert r.content[:4] == b"wOF2"


def test_the_published_app_ships_the_font_its_css_asks_for():
    # No build step here — `vite build` is what turns this url into a hashed dist/assets entry, and
    # what would fail loudly if the file were missing. This only pins that the file it reaches for
    # is in the repo, which is the part a tidy-up of src/assets/ could quietly take away.
    css = REPO / "template" / "react-vite" / "src" / "index.css"
    font = (css.parent / _asked_for(css.read_text())).resolve()

    assert font.is_file()
    assert font.read_bytes()[:4] == b"wOF2"


def test_the_two_copies_are_the_same_font_under_its_license():
    # Two copies because a published app is a separate deployment carrying only its own repo, so
    # there is no one place both could read. Same bytes, or the builder and the app it builds drift
    # apart in a way only a careful eye would catch. OFL 1.1 asks that the licence travel with the
    # font, and each copy is its own distribution.
    copies = [REPO / "backend" / "sage" / "ui" / "fonts",
              REPO / "template" / "react-vite" / "src" / "assets" / "fonts"]
    digests = set()
    for d in copies:
        font = d / "inter-latin-var.woff2"
        digests.add(hashlib.sha256(font.read_bytes()).hexdigest())
        assert (d / "OFL.txt").is_file(), d

    assert len(digests) == 1
