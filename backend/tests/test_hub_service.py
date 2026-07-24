import pytest

from sage.provision.domino import FakeControlPlane, ProjectRef
from sage.provision.github import FakeRepoProvider
from sage.provision.service import HubService


@pytest.fixture
def no_network_seed():
    """A recording no-op seeder so create_app runs without touching git or the network."""
    calls = []
    yield calls


def _hub(tmp_path, cp=None, repo=None, seed_calls=None):
    seed = (lambda url, tmpl, **kw: seed_calls.append((url, kw))) if seed_calls is not None else (lambda *a, **k: None)
    return HubService(cp or FakeControlPlane(), repo or FakeRepoProvider(), tmp_path, seed=seed)


def test_create_app_provisions_repo_project_workspace(tmp_path, no_network_seed):
    cp, repo = FakeControlPlane(), FakeRepoProvider()
    hub = _hub(tmp_path, cp, repo, seed_calls=no_network_seed)

    created = hub.create_app("My App")

    assert created.repo.full_name == "test-owner/sage-my-app"
    assert created.repo.private is True
    assert created.project.name == "My App"
    assert created.project.git_url == created.repo.clone_url
    assert created.workspace["id"] == f"ws-{created.project.id}"
    # seed was invoked with the new repo's clone URL
    assert no_network_seed and no_network_seed[0][0] == created.repo.clone_url


def test_create_app_resolves_repo_name_collision(tmp_path, no_network_seed):
    repo = FakeRepoProvider()
    repo.create_repo("sage-my-app")  # occupy the base name
    hub = _hub(tmp_path, FakeControlPlane(), repo, seed_calls=no_network_seed)

    created = hub.create_app("My App")
    assert created.repo.full_name == "test-owner/sage-my-app-2"


def test_create_app_requires_name(tmp_path, no_network_seed):
    with pytest.raises(ValueError):
        _hub(tmp_path).create_app("   ")


def test_rollback_deletes_repo_when_seed_fails(tmp_path):
    repo = FakeRepoProvider()

    def failing_seed(url, tmpl, **kw):
        raise RuntimeError("push failed")

    hub = HubService(FakeControlPlane(), repo, tmp_path, seed=failing_seed)
    with pytest.raises(RuntimeError, match="push failed"):
        hub.create_app("My App")
    # the orphaned repo was cleaned up
    assert repo.created == []


def test_rollback_deletes_repo_when_project_create_fails(tmp_path):
    class FailingCP(FakeControlPlane):
        def create_project(self, name, *, git_url, branch="main", description=""):
            raise RuntimeError("project rejected")

    repo = FakeRepoProvider()
    hub = HubService(FailingCP(), repo, tmp_path, seed=lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="project rejected"):
        hub.create_app("My App")
    assert repo.created == []


def test_no_rollback_once_project_exists(tmp_path):
    """A workspace-launch failure must not delete the repo — the app already exists."""
    class WsFailCP(FakeControlPlane):
        def create_workspace(self, project_id, *, branch="main"):
            raise RuntimeError("Workspace start wasn't completed")

    repo = FakeRepoProvider()
    hub = HubService(WsFailCP(), repo, tmp_path, seed=lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="Workspace start"):
        hub.create_app("My App")
    # repo (and the created project) are kept so the user can retry opening it
    assert [r.full_name for r in repo.created] == ["test-owner/sage-my-app"]


def test_open_app_reuses_running_workspace(tmp_path):
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [{"id": "ws-existing", "state": "running"}]
    hub = _hub(tmp_path, cp)

    result = hub.open_app("proj-1")
    assert result["launched"] is False
    assert result["workspace"]["id"] == "ws-existing"


def test_open_app_launches_when_none(tmp_path):
    hub = _hub(tmp_path, FakeControlPlane())
    result = hub.open_app("proj-9")
    assert result["launched"] is True
    assert result["workspace"]["id"] == "ws-proj-9"


def test_workspace_status_reports_running_and_open_url(tmp_path):
    cp = FakeControlPlane()
    cp.projects.append(ProjectRef(id="proj-1", name="My App", git_url="https://github.com/o/sage-my-app.git"))
    cp.workspaces["proj-1"] = [{
        "id": "ws-1", "createdAt": "2026-07-24T10:00:00Z", "state": "Started", "ownerName": "u",
        "mostRecentSession": {"executionId": "run-9", "sessionStatusInfo": {"isRunning": True}},
    }]
    hub = _hub(tmp_path, cp)

    status = hub.workspace_status("proj-1", "ws-1")
    assert status["running"] is True
    assert status["open_url"] == "/u/My%20App/notebookSession/run-9/"


def test_workspace_status_not_running_while_booting(tmp_path):
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [{
        "id": "ws-1", "state": "Started",
        "mostRecentSession": {"sessionStatusInfo": {"isRunning": False}},
    }]
    hub = _hub(tmp_path, cp)
    assert hub.workspace_status("proj-1", "ws-1")["running"] is False


def test_workspace_status_none_when_no_workspace(tmp_path):
    status = _hub(tmp_path, FakeControlPlane()).workspace_status("proj-x")
    assert status == {"running": False, "open_url": None, "state": None}
