"""#116 — both entry pages are templated by the server, so no browser paints the unbranded name.

`orchestrator/app.py` serves `index.html` or `door.html` from one route, so substituting there
covers both. Patching the title from JS on boot does not: the browser paints whatever the HTML
literally said first, and the flash lands on the door — the first page a viewer ever sees
(ADR-0004) — where it would show Sage's name over a partner's product.

The response body is what these assert against, not the rendered DOM, because the flash is exactly
the gap between the two.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
SHELL = (_WB / "index.html").read_text()
DOOR = (_WB / "door.html").read_text()


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)


def _acme(tmp_path, monkeypatch) -> None:
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({"productName": "Acme", "pageTitle": "Acme Studio"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))


def _client(monkeypatch, *, door: bool):
    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "proxy_is_app", lambda: door)
    return TestClient(appmod.control_app)


@pytest.mark.parametrize("door", [False, True])
def test_the_pack_page_title_is_in_the_html_the_server_sends(tmp_path, monkeypatch, door):
    _acme(tmp_path, monkeypatch)
    r = _client(monkeypatch, door=door).get("/")
    assert r.status_code == 200
    assert "<title>Acme Studio</title>" in r.text
    assert "Sage Workspace" not in r.text


@pytest.mark.parametrize("door", [False, True])
def test_an_unset_pack_still_gets_the_domino_default(monkeypatch, door):
    r = _client(monkeypatch, door=door).get("/")
    assert "<title>Sage Workspace</title>" in r.text


def test_neither_entry_page_carries_a_static_title():
    """The source, not the response: a static title is what the browser would paint first."""
    for page in (SHELL, DOOR):
        assert "<title>{pageTitle}</title>" in page
        assert "Sage Workspace" not in page


def test_both_entry_pages_carry_an_icon_slot():
    """#117 fills the value. The slot has to exist first, or there is nothing to fill."""
    for page in (SHELL, DOOR):
        assert 'rel="icon"' in page


def test_no_boot_time_js_rewrites_the_title_on_the_door():
    """The door is the flash that matters, and it read /api/brand to set its own title."""
    assert "document.title" not in DOOR


@pytest.mark.parametrize("door", [False, True])
def test_the_entry_pages_still_serve_no_store(monkeypatch, door):
    r = _client(monkeypatch, door=door).get("/")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-type"].startswith("text/html")
