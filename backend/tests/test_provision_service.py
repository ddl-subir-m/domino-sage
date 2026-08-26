import httpx
import pytest

from sage.provision.domino import DominoControlPlane, FakeControlPlane
from sage.provision.github import FakeRepoProvider
from sage.provision.service import ProvisionService


@pytest.fixture
def no_network_seed():
    """A recording no-op seeder so create_app runs without touching git or the network."""
    calls = []
    yield calls


def _service(tmp_path, cp=None, repo=None, seed_calls=None):
    seed = (lambda url, tmpl, **kw: seed_calls.append((url, kw))) if seed_calls is not None else (lambda *a, **k: None)
    return ProvisionService(cp or FakeControlPlane(), repo or FakeRepoProvider(), tmp_path, seed=seed)


def test_create_app_provisions_repo_project_workspace(tmp_path, no_network_seed):
    cp, repo = FakeControlPlane(), FakeRepoProvider()
    svc = _service(tmp_path, cp, repo, seed_calls=no_network_seed)

    created = svc.create_app("My App")

    assert created.repo.full_name == "test-owner/sage-my-app"
    assert created.repo.private is True
    assert created.project.name == "My App"
    assert created.project.git_url == created.repo.clone_url
    assert created.workspace["id"] == f"ws-{created.project.id}"
    # seed was invoked with the new repo's clone URL
    assert no_network_seed and no_network_seed[0][0] == created.repo.clone_url


def test_create_app_returns_an_open_url_for_the_new_builder(tmp_path, no_network_seed):
    # The workspace DTO carries owner + run id and the project name comes from the ProjectRef, so
    # the caller gets a host-relative path it can send the browser to.
    created = _service(tmp_path, seed_calls=no_network_seed).create_app("My App")
    assert created.open_url == f"/tester/My%20App/notebookSession/run-{created.project.id}/"


def test_create_app_resolves_repo_name_collision(tmp_path, no_network_seed):
    repo = FakeRepoProvider()
    repo.create_repo("sage-my-app")  # occupy the base name
    svc = _service(tmp_path, FakeControlPlane(), repo, seed_calls=no_network_seed)

    created = svc.create_app("My App")
    assert created.repo.full_name == "test-owner/sage-my-app-2"


def test_create_app_requires_name(tmp_path, no_network_seed):
    with pytest.raises(ValueError):
        _service(tmp_path).create_app("   ")


def test_rollback_deletes_repo_when_seed_fails(tmp_path):
    repo = FakeRepoProvider()

    def failing_seed(url, tmpl, **kw):
        raise RuntimeError("push failed")

    svc = ProvisionService(FakeControlPlane(), repo, tmp_path, seed=failing_seed)
    with pytest.raises(RuntimeError, match="push failed"):
        svc.create_app("My App")
    # the orphaned repo was cleaned up
    assert repo.created == []


def test_rollback_deletes_repo_when_project_create_fails(tmp_path):
    class FailingCP(FakeControlPlane):
        def create_project(self, name, *, git_url, branch="main", description=""):
            raise RuntimeError("project rejected")

    repo = FakeRepoProvider()
    svc = ProvisionService(FailingCP(), repo, tmp_path, seed=lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="project rejected"):
        svc.create_app("My App")
    assert repo.created == []


def test_no_rollback_once_project_exists(tmp_path):
    """A workspace-launch failure must not delete the repo — the app already exists."""
    class WsFailCP(FakeControlPlane):
        def create_workspace(self, project_id, *, branch="main"):
            raise RuntimeError("Workspace start wasn't completed")

    repo = FakeRepoProvider()
    svc = ProvisionService(WsFailCP(), repo, tmp_path, seed=lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="Workspace start"):
        svc.create_app("My App")
    # repo (and the created project) are kept so the user can retry opening it
    assert [r.full_name for r in repo.created] == ["test-owner/sage-my-app"]


def test_open_app_reuses_running_workspace(tmp_path):
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [{"id": "ws-existing", "state": "running"}]
    svc = _service(tmp_path, cp)

    result = svc.open_app("proj-1")
    assert result["launched"] is False
    assert result["workspace"]["id"] == "ws-existing"
    assert len(cp.workspaces["proj-1"]) == 1  # no second builder launched


def test_open_app_ignores_non_builder_workspace_in_same_project(tmp_path):
    # A user's VS Code session in the project must not be reused or reported as their Sage Builder.
    cp = FakeControlPlane()
    vscode = {"id": "vscode-1", "state": "running", "name": "my-vscode",
              "mostRecentSession": {"sessionStatusInfo": {"isRunning": True}}}
    cp.workspaces["proj-1"] = [vscode]
    svc = _service(tmp_path, cp)

    # launches a fresh builder rather than reusing the VS Code session
    result = svc.open_app("proj-1")
    assert result["launched"] is True
    assert result["workspace"]["id"] == "ws-proj-1"
    assert vscode in cp.workspaces["proj-1"]  # the VS Code workspace was left untouched


def test_open_app_launches_when_none(tmp_path):
    svc = _service(tmp_path, FakeControlPlane())
    result = svc.open_app("proj-9")
    assert result["launched"] is True
    assert result["workspace"]["id"] == "ws-proj-9"


def test_open_app_relaunches_stopped_workspace(tmp_path):
    cp = FakeControlPlane()
    # The v4 list DTO has no isRestartable field — restartability comes from `state` alone.
    cp.workspaces["proj-1"] = [{"id": "ws-1", "state": "Stopped"}]
    svc = _service(tmp_path, cp)

    result = svc.open_app("proj-1")
    assert result["launched"] is True
    # Restarted the SAME workspace in place, not created a new one.
    assert result["workspace"]["id"] == "ws-1"
    assert len(cp.workspaces["proj-1"]) == 1  # no new workspace appended
    assert cp.workspaces["proj-1"][0]["state"] == "running"


def test_open_app_creates_when_only_workspace_is_terminal(tmp_path):
    cp = FakeControlPlane()
    # Deleted/failed workspaces aren't relaunchable — fall through to a fresh one.
    cp.workspaces["proj-1"] = [{"id": "ws-old", "state": "Deleted", "deleted": True}]
    svc = _service(tmp_path, cp)

    result = svc.open_app("proj-1")
    assert result["launched"] is True
    assert result["workspace"]["id"] == "ws-proj-1"  # a fresh workspace, not the terminal one


def test_list_apps_keeps_only_sage_repos(tmp_path):
    # Against the real control plane (no network — MockTransport): an ordinary Domino project in the
    # same account never reaches the caller, because its git repo isn't a sage-* repo.
    projects = {"projects": [
        {"project": {"id": "p1", "name": "Sage One",
                     "mainRepository": {"uri": "https://github.com/me/sage-one.git"}}},
        {"project": {"id": "p2", "name": "Analytics",
                     "mainRepository": {"uri": "https://github.com/me/analytics.git"}}},
        {"project": {"id": "p3", "name": "No Repo"}},
    ], "metadata": {}}
    cp = DominoControlPlane(
        "https://domino.example.com",
        lambda: "tok",
        environment_id="env-1",
        hardware_tier_id="tier-1",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=projects)),
    )

    apps = _service(tmp_path, cp).list_apps()
    assert [a.id for a in apps] == ["p1"]
    assert apps[0].git_url == "https://github.com/me/sage-one.git"
