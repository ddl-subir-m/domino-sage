"""Chip switch attaches this viewer's Sage Builder (#47).

Attach itself is driven at the service seam against the Fake Control Plane, per spec 43's testing
decision — no UI, no browser. What the chip does with the answer is pinned as source, and one route
test covers the container that can't provision at all.

The chip's sage-*-only list is the same read the door uses, already covered by
test_provision_domino.test_list_apps_filters_by_repo_prefix and
test_provision_service.test_list_apps_keeps_only_sage_repos. What is new here is *attach*: which
workspace in a Project this viewer is sent to, and whose is left alone.
"""
from pathlib import Path

from sage.provision.domino import BUILDER_WORKSPACE_NAME, FakeControlPlane, ProjectRef, UserRef
from sage.provision.github import FakeRepoProvider
from sage.provision.service import ProvisionService

ALICE = UserRef(id="507f1f77bcf86cd799439011", name="alice")
PROJECT = "p-sales"


def _cp() -> FakeControlPlane:
    cp = FakeControlPlane(user=ALICE)
    cp.projects.append(ProjectRef(id=PROJECT, name="sage-sales", git_url="https://g/me/sage-sales.git"))
    return cp


def _service(tmp_path, cp) -> ProvisionService:
    return ProvisionService(cp, FakeRepoProvider(), tmp_path, seed=lambda *a, **k: None)


def _builder(owner: str, *, id: str, state: str = "running", name: str = BUILDER_WORKSPACE_NAME) -> dict:
    return {
        "id": id,
        "ownerName": owner,
        "name": name,
        "state": state,
        "createdAt": "2026-01-01T00:00:00Z",
        "project": {"name": "sage-sales"},
        "mostRecentSession": {
            "executionId": f"run-{id}",
            "sessionStatusInfo": {"isRunning": state == "running"},
        },
    }


def test_picking_a_project_starts_this_viewers_builder_and_returns_where_to_go(tmp_path):
    cp = _cp()

    out = _service(tmp_path, cp).open_app(PROJECT, owner="alice")

    assert out["launched"] is True  # they have none there yet, so one is created
    assert len(cp.workspaces[PROJECT]) == 1
    assert out["open_url"] == "/alice/sage-sales/notebookSession/run-p-sales/"


def test_a_running_builder_of_theirs_is_reused(tmp_path):
    cp = _cp()
    cp.workspaces[PROJECT] = [_builder("alice", id="ws-alice")]

    out = _service(tmp_path, cp).open_app(PROJECT, owner="alice")

    assert out["launched"] is False  # nothing to wait for
    assert out["workspace"]["id"] == "ws-alice"
    assert len(cp.workspaces[PROJECT]) == 1  # and no second builder


def test_a_stopped_builder_of_theirs_is_resumed_in_place(tmp_path):
    cp = _cp()
    cp.workspaces[PROJECT] = [_builder("alice", id="ws-alice", state="Stopped")]

    out = _service(tmp_path, cp).open_app(PROJECT, owner="alice")

    assert out["launched"] is True  # they wait, but only because it was down
    assert len(cp.workspaces[PROJECT]) == 1  # resumed, not piled up
    assert cp.workspaces[PROJECT][0]["state"] == "running"


def test_a_collaborators_running_builder_is_left_alone(tmp_path):
    # Two people in one container would hand alice bob's session. She gets her own.
    cp = _cp()
    cp.workspaces[PROJECT] = [_builder("bob", id="ws-bob")]

    out = _service(tmp_path, cp).open_app(PROJECT, owner="alice")

    assert out["workspace"]["id"] != "ws-bob"
    assert out["launched"] is True
    bob = next(w for w in cp.workspaces[PROJECT] if w["id"] == "ws-bob")
    assert bob["state"] == "running"  # untouched


def test_a_collaborators_stopped_builder_is_not_resumed(tmp_path):
    cp = _cp()
    cp.workspaces[PROJECT] = [_builder("bob", id="ws-bob", state="Stopped")]

    _service(tmp_path, cp).open_app(PROJECT, owner="alice")

    bob = next(w for w in cp.workspaces[PROJECT] if w["id"] == "ws-bob")
    assert bob["state"] == "Stopped"  # starting someone else's builder spends their compute
    assert len(cp.workspaces[PROJECT]) == 2  # alice got her own


def test_a_jupyter_session_in_the_same_project_is_not_a_sage_builder(tmp_path):
    cp = _cp()
    cp.workspaces[PROJECT] = [_builder("alice", id="ws-jupyter", name="jupyter")]

    out = _service(tmp_path, cp).open_app(PROJECT, owner="alice")

    assert out["workspace"]["id"] != "ws-jupyter"
    assert len(cp.workspaces[PROJECT]) == 2


def test_the_status_poll_answers_about_this_viewers_builder(tmp_path):
    # Without the owner scope the newest workspace wins, and that can be a collaborator's — the
    # viewer would then be told "running" and sent to a URL that is not theirs to open.
    cp = _cp()
    bob = _builder("bob", id="ws-bob")
    bob["createdAt"] = "2026-06-01T00:00:00Z"
    cp.workspaces[PROJECT] = [_builder("alice", id="ws-alice", state="Stopped"), bob]

    status = _service(tmp_path, cp).workspace_status(PROJECT, owner="alice")

    assert status["workspace_id"] == "ws-alice"
    assert status["running"] is False


# --- What the chip does with that, pinned in source (no browser in this suite) ---------------

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"


def test_picking_another_project_hands_the_browser_over_rather_than_swapping_state():
    """One Sage Builder is bound to one project volume, so switching Project is a bounce, not a
    local state swap — the same move the door makes, from inside the Workbench."""
    store = (WB / "store.js").read_text()
    picker = (WB / "components" / "scope-picker.js").read_text()

    assert "async attachProject(project)" in store
    assert "SW.api.openProject(project.id)" in store
    assert "SW.api.projectStatus(" in store     # waits for the session
    assert "window.location.replace(url)" in store
    assert "SW.store.attachProject(project)" in picker
    assert "setScope" not in picker             # the chip no longer just relabels itself


def test_the_chip_describes_only_the_project_it_can_read():
    """A Sage overlay lives in the builder that owns it. The other rows are a Domino name and an id
    to attach by, so the row says what picking it does instead of inventing members and app counts.
    """
    api = (WB / "api.js").read_text()
    picker = (WB / "components" / "scope-picker.js").read_text()

    assert "request('/projects')" in api
    assert "openProject:" in api and "projectStatus:" in api
    assert "You are here" in picker
    assert "memberCount" not in picker and "appCount" not in picker


def test_a_container_that_cannot_provision_offers_nothing_to_switch_to():
    # A laptop run has no Projects to switch between, and says so rather than failing on click.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    assert appmod._provision is None
    assert client.get("/api/projects").json() == {"items": [], "provisioning": False}
    assert client.post("/api/projects/p-1/open").status_code == 503
    assert client.get("/api/projects/status?project_id=p-1").status_code == 503
