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


def test_open_app_relaunches_stopped_workspace(tmp_path):
    cp = FakeControlPlane()
    # The v4 list DTO has no isRestartable field — restartability comes from `state` alone.
    cp.workspaces["proj-1"] = [{"id": "ws-1", "state": "Stopped"}]
    hub = _hub(tmp_path, cp)

    result = hub.open_app("proj-1")
    assert result["launched"] is True
    # Restarted the SAME workspace in place, not created a new one.
    assert result["workspace"]["id"] == "ws-1"
    assert cp.workspaces["proj-1"] == [cp.workspaces["proj-1"][0]]  # no new workspace appended
    assert cp.workspaces["proj-1"][0]["state"] == "running"


def test_open_app_creates_when_only_workspace_is_terminal(tmp_path):
    cp = FakeControlPlane()
    # Deleted/failed workspaces aren't relaunchable — fall through to a fresh one.
    cp.workspaces["proj-1"] = [{"id": "ws-old", "state": "Deleted", "deleted": True}]
    hub = _hub(tmp_path, cp)

    result = hub.open_app("proj-1")
    assert result["launched"] is True
    assert result["workspace"]["id"] == "ws-proj-1"  # a fresh workspace, not the terminal one


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
    assert status == {"running": False, "open_url": None, "state": None, "workspace_id": None}


def test_workspace_status_reports_workspace_id(tmp_path):
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [{"id": "ws-1", "createdAt": "2026-07-24T10:00:00Z", "state": "Started"}]
    assert _hub(tmp_path, cp).workspace_status("proj-1")["workspace_id"] == "ws-1"


def test_workspace_status_prefers_running_over_newer_stopped(tmp_path):
    # A stopped leftover created more recently must not mask the actually-running builder.
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [
        {"id": "ws-run", "createdAt": "2026-07-24T09:00:00Z", "state": "Started",
         "mostRecentSession": {"sessionStatusInfo": {"isRunning": True}}},
        {"id": "ws-stopped", "createdAt": "2026-07-24T11:00:00Z", "state": "Stopped"},
    ]
    status = _hub(tmp_path, cp).workspace_status("proj-1")
    assert status["running"] is True
    assert status["workspace_id"] == "ws-run"


def test_stop_app_stops_named_workspace(tmp_path):
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [{
        "id": "ws-1", "state": "Started",
        "mostRecentSession": {"sessionStatusInfo": {"isRunning": True}},
    }]
    result = _hub(tmp_path, cp).stop_app("proj-1", "ws-1")
    assert result == {"stopped": True, "workspace_id": "ws-1"}
    assert cp.workspaces["proj-1"][0]["state"] == "Stopped"
    assert cp.workspaces["proj-1"][0]["mostRecentSession"]["sessionStatusInfo"]["isRunning"] is False


def test_stop_app_targets_newest_running_when_id_omitted(tmp_path):
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [
        {"id": "ws-old", "createdAt": "2026-07-24T09:00:00Z", "state": "running"},
        {"id": "ws-new", "createdAt": "2026-07-24T11:00:00Z", "state": "running"},
    ]
    assert _hub(tmp_path, cp).stop_app("proj-1")["workspace_id"] == "ws-new"


def _running_ws(wid="ws-1"):
    return {
        "id": wid, "state": "Started", "ownerName": "u",
        "mostRecentSession": {"executionId": "run-9", "sessionStatusInfo": {"isRunning": True}},
    }


def test_stop_app_saves_running_builder_before_stopping(tmp_path):
    cp = FakeControlPlane()
    ref = cp.create_project("My App", git_url="https://github.com/me/sage-my-app.git")
    cp.workspaces[ref.id] = [_running_ws()]

    order = []
    orig_save, orig_stop = cp.save_workspace_work, cp.stop_workspace
    cp.save_workspace_work = lambda p: order.append(("save", p)) or orig_save(p)
    cp.stop_workspace = lambda p, w: order.append(("stop", w)) or orig_stop(p, w)

    result = _hub(tmp_path, cp).stop_app(ref.id, "ws-1")
    assert result == {"stopped": True, "workspace_id": "ws-1"}
    # Committed + pushed (via the builder's own sync) BEFORE the workspace was stopped.
    assert order == [("save", "/u/My%20App/notebookSession/run-9/"), ("stop", "ws-1")]


def test_stop_app_skips_save_when_builder_not_running(tmp_path):
    cp = FakeControlPlane()
    ref = cp.create_project("Idle", git_url="https://github.com/me/sage-idle.git")
    cp.workspaces[ref.id] = [{"id": "ws-1", "state": "Stopped", "ownerName": "u",
                             "mostRecentSession": {"executionId": "run-9"}}]
    _hub(tmp_path, cp).stop_app(ref.id, "ws-1")
    assert cp.saved_paths == []  # nothing running to save


def test_stop_app_stops_even_when_pre_stop_save_fails(tmp_path):
    cp = FakeControlPlane()
    ref = cp.create_project("My App", git_url="https://github.com/me/sage-my-app.git")
    cp.workspaces[ref.id] = [_running_ws()]

    def boom(open_path):
        raise RuntimeError("builder unreachable")

    cp.save_workspace_work = boom
    assert _hub(tmp_path, cp).stop_app(ref.id, "ws-1") == {"stopped": True, "workspace_id": "ws-1"}
    assert cp.workspaces[ref.id][0]["state"] == "Stopped"  # stopped despite the save failing


def test_delete_app_saves_stops_then_removes_running_workspace(tmp_path):
    cp = FakeControlPlane()
    ref = cp.create_project("My App", git_url="https://github.com/me/sage-my-app.git")
    cp.workspaces[ref.id] = [_running_ws()]

    order = []
    for m in ("save_workspace_work", "stop_workspace", "archive_workspace"):
        orig = getattr(cp, m)
        setattr(cp, m, (lambda name, fn: (lambda *a: order.append(name) or fn(*a)))(m, orig))

    assert _hub(tmp_path, cp).delete_app(ref.id) == {"deleted": True}
    # Push work, stop the session, THEN remove the workspace so the project can be archived.
    assert order == ["save_workspace_work", "stop_workspace", "archive_workspace"]
    assert cp.saved_paths == ["/u/My%20App/notebookSession/run-9/"]
    assert cp.list_apps() == []


def test_delete_app_removes_stopped_workspace_so_archive_succeeds(tmp_path):
    # The bug: a stopped-but-still-present workspace made archive_project 500 ("contains 1 workspace").
    cp = FakeControlPlane()
    ref = cp.create_project("Probe", git_url="https://github.com/me/sage-probe.git")
    cp.workspaces[ref.id] = [{"id": "ws-1", "state": "Stopped"}]

    removed = []
    orig = cp.archive_workspace
    cp.archive_workspace = lambda p, w: removed.append(w) or orig(p, w)

    assert _hub(tmp_path, cp).delete_app(ref.id) == {"deleted": True}
    assert removed == ["ws-1"]   # removed even though it wasn't running
    assert cp.saved_paths == []  # nothing running -> no pre-stop save
    assert cp.list_apps() == []


def test_delete_app_falls_back_to_delete_when_archive_rejected(tmp_path):
    cp = FakeControlPlane()
    ref = cp.create_project("Probe", git_url="https://github.com/me/sage-probe.git")
    cp.workspaces[ref.id] = [{"id": "ws-1", "state": "Stopped"}]

    def reject(p, w):
        raise RuntimeError("400: workspace cannot be archived")

    cp.archive_workspace = reject
    deleted = []
    orig = cp.delete_workspace
    cp.delete_workspace = lambda p, w: deleted.append(w) or orig(p, w)

    assert _hub(tmp_path, cp).delete_app(ref.id) == {"deleted": True}
    assert deleted == ["ws-1"]  # archive failed -> delete removed it
    assert cp.list_apps() == []


def test_delete_app_surfaces_real_error_when_workspace_cant_be_removed(tmp_path):
    # Neither archive nor delete works -> raise the real reasons, not the misleading archive 500.
    cp = FakeControlPlane()
    ref = cp.create_project("Probe", git_url="https://github.com/me/sage-probe.git")
    cp.workspaces[ref.id] = [{"id": "ws-1", "state": "Stopped"}]

    def reject_archive(p, w):
        raise RuntimeError("403: not the owner")

    def reject_delete(p, w):
        raise RuntimeError("409: workspace is still running")

    cp.archive_workspace = reject_archive
    cp.delete_workspace = reject_delete
    archived = []
    cp.archive_project = lambda p: archived.append(p)  # must NOT be reached

    with pytest.raises(RuntimeError) as ei:
        _hub(tmp_path, cp).delete_app(ref.id)
    msg = str(ei.value)
    assert "ws-1" in msg and "403: not the owner" in msg and "409: workspace is still running" in msg
    assert archived == []  # project archive not attempted while a workspace remains


def test_stop_app_noop_when_nothing_to_stop(tmp_path):
    result = _hub(tmp_path, FakeControlPlane()).stop_app("proj-x")
    assert result == {"stopped": False, "workspace_id": None, "detail": "no workspace to stop"}


def test_delete_app_stops_running_builder_then_archives_project(tmp_path):
    cp = FakeControlPlane()
    ref = cp.create_project("My App", git_url="https://github.com/me/sage-my-app.git")
    cp.workspaces[ref.id] = [{
        "id": "ws-1", "state": "Started",
        "mostRecentSession": {"sessionStatusInfo": {"isRunning": True}},
    }]

    stops = []
    orig_stop = cp.stop_workspace
    cp.stop_workspace = lambda p, w: stops.append((p, w)) or orig_stop(p, w)

    result = _hub(tmp_path, cp).delete_app(ref.id)

    assert result == {"deleted": True}
    assert cp.list_apps() == []  # project archived (gone from the list)
    assert ref.id not in cp.workspaces  # workspaces cleared with it
    assert stops == [(ref.id, "ws-1")]  # running builder stopped first


def test_delete_app_archives_even_with_no_workspace(tmp_path):
    cp = FakeControlPlane()
    ref = cp.create_project("Idle App", git_url="https://github.com/me/sage-idle-app.git")
    assert _hub(tmp_path, cp).delete_app(ref.id) == {"deleted": True}
    assert cp.list_apps() == []
