"""The scope chip picks another project (#47).

Switching Project is a same-origin navigation now (ONE-APP-PLAN.md §2.3) — every project this one
process knows about is a path it already serves, `/p/<slug>/`. What the chip does with the registry's
listing is pinned as source, and one route test covers the container that can't provision at all.
"""
from pathlib import Path

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"


def test_picking_another_project_is_a_same_origin_navigation():
    """Every project this ONE process knows about is a path it already serves, `/p/<slug>/`
    (ONE-APP-PLAN.md §2.3), so switching Project is a plain link now, not a bounce to another
    container's workspace and not a local state swap either."""
    picker = (WB / "components" / "scope-picker.js").read_text()

    assert "window.location.assign(`../${project.slug}/`)" in picker
    assert "setScope" not in picker             # the chip no longer just relabels itself
    assert "SW.store.attachProject" not in picker
    assert "SW.api.openProject" not in picker


def test_the_chip_describes_only_the_project_it_can_read():
    """A Sage overlay lives in the builder that owns it. The other rows are a Domino name and a
    slug to open by, so the row says what picking it does instead of inventing members and app
    counts."""
    api = (WB / "api.js").read_text()
    picker = (WB / "components" / "scope-picker.js").read_text()

    assert "request('/projects')" in api
    assert "openProject" not in api and "projectStatus" not in api
    assert "You are here" in picker
    assert "memberCount" not in picker and "appCount" not in picker


def test_a_container_with_no_local_projects_offers_nothing_to_switch_to(monkeypatch):
    # A fresh laptop run has no Projects to switch between yet, and says so honestly.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    # Forced, not assumed: this sandbox is itself a real Domino workspace, so `_provision` is
    # genuinely non-None here, and `GET /api/projects` reads `_REGISTRY`'s own CAPTURED
    # `_control_plane` (set once at import time, not re-read from the module-level name) — so both
    # have to be patched, not just the module-level name this test used to only assert about.
    monkeypatch.setattr(appmod, "_provision", None)
    monkeypatch.setattr(appmod._REGISTRY, "_control_plane", None)
    client = TestClient(appmod.control_app)
    assert appmod._provision is None
    assert client.get("/api/projects").json() == {"items": []}
