import json

import httpx
import pytest

from sage.provision.domino import CredentialRef, DominoControlPlane, FakeControlPlane
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
    # The Domino project is named after the repo, never after what was typed (#46) — Sage looks a
    # Project up by that name, and the typed name rides in as the chip overlay instead.
    assert created.project.name == "sage-my-app"
    assert created.project.git_url == created.repo.clone_url
    assert created.workspace["id"] == f"ws-{created.project.id}"
    # seed was invoked with the new repo's clone URL
    assert no_network_seed and no_network_seed[0][0] == created.repo.clone_url


def test_create_app_returns_an_open_url_for_the_new_builder(tmp_path, no_network_seed):
    # The workspace DTO carries owner + run id and the project name comes from the ProjectRef, so
    # the caller gets a host-relative path it can send the browser to.
    created = _service(tmp_path, seed_calls=no_network_seed).create_app("My App")
    assert created.open_url == f"/tester/sage-my-app/notebookSession/run-{created.project.id}/"


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
        def create_project(self, name, *, git_url, git_credential_id="cred-1", branch="main",
                           description=""):
            raise RuntimeError("project rejected")

    repo = FakeRepoProvider()
    svc = ProvisionService(FailingCP(), repo, tmp_path, seed=lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="project rejected"):
        svc.create_app("My App")
    assert repo.created == []


def test_a_dead_credential_does_not_win_the_pick(tmp_path):
    """ADR-0033: Sage cannot tell a live credential from a dead one, so it lets Domino say. The
    dead one is listed first, exactly the #157 case that used to fail the whole create."""
    cp = FakeControlPlane(
        credentials=[
            CredentialRef(id="dead", label="old PAT (github.com)", domain="github.com",
                          protocol="https", usable=True),
            CredentialRef(id="live", label="new PAT (github.com)", domain="github.com",
                          protocol="https", usable=True),
        ],
        dead_credentials={"dead"},
    )
    created = _service(tmp_path, cp).create_app("My App")

    assert created.project.name == "sage-my-app"
    assert cp.tried_credentials == ["dead", "live"]  # in list order, and it did not stop at the first


def test_unusable_credentials_are_never_tried(tmp_path):
    """An SSH credential for the right host and an HTTPS one for another host are both out."""
    cp = FakeControlPlane(credentials=[
        CredentialRef(id="ssh", label="my key (github.com) [SSH]", domain="github.com",
                      protocol="ssh", usable=False),
        CredentialRef(id="gl", label="work GitLab (gitlab.com)", domain="gitlab.com",
                      protocol="https", usable=False),
        CredentialRef(id="ok", label="PAT (github.com)", domain="github.com",
                      protocol="https", usable=True),
    ])
    _service(tmp_path, cp).create_app("My App")
    assert cp.tried_credentials == ["ok"]


def test_no_usable_credential_lists_what_the_account_holds(tmp_path):
    """#157: a user with credentials for other hosts used to read "add one" as the only advice."""
    cp = FakeControlPlane(credentials=[
        CredentialRef(id="gl", label="work GitLab (gitlab.com)", domain="gitlab.com",
                      protocol="https", usable=False),
        CredentialRef(id="ssh", label="my key (github.com) [SSH]", domain="github.com",
                      protocol="ssh", usable=False),
    ])
    repo = FakeRepoProvider()
    with pytest.raises(RuntimeError) as e:
        _service(tmp_path, cp, repo).create_app("My App")

    msg = str(e.value)
    assert "work GitLab (gitlab.com)" in msg
    assert "my key (github.com) [SSH]" in msg
    assert "Add an HTTPS credential for github.com" in msg
    assert cp.tried_credentials == []
    assert repo.created == []  # and the orphaned repo still gets rolled back


def test_every_credential_failing_groups_them_by_what_domino_said(tmp_path):
    """ADR-0033 Q7: one line per distinct message, not per credential. Domino's refusal runs to
    three sentences and stamps a fresh requestId on each, so the grouping has to survive both."""
    class AllDeadCP(FakeControlPlane):
        def create_project(self, name, *, git_url, git_credential_id="cred-1", branch="main",
                           description=""):
            self.tried_credentials.append(git_credential_id)
            if git_credential_id == "odd":
                raise RuntimeError('POST /api/projects/beta/projects -> 400: '
                                   '{"requestId":"r-3","errors":["Repository not found."]}')
            raise RuntimeError(
                f'POST /api/projects/beta/projects -> 500: {{"requestId":"r-{git_credential_id}",'
                '"errors":["Cannot access Git repository with URI: x. This may be due to invalid '
                'Git credentials."]}')

    cp = AllDeadCP(credentials=[
        CredentialRef(id="a", label="old PAT (github.com)", domain="github.com",
                      protocol="https", usable=True),
        CredentialRef(id="b", label="new PAT (github.com)", domain="github.com",
                      protocol="https", usable=True),
        CredentialRef(id="odd", label="CI token (github.com)", domain="github.com",
                      protocol="https", usable=True),
    ])
    with pytest.raises(RuntimeError) as e:
        _service(tmp_path, cp).create_app("My App")

    msg = str(e.value)
    assert cp.tried_credentials == ["a", "b", "odd"]  # uncapped: all of them
    # The two that failed the same way share one line; the odd one out keeps its own.
    assert "old PAT (github.com), new PAT (github.com) — Cannot access Git repository" in msg
    assert "CI token (github.com) — Repository not found." in msg
    # The per-call requestId is gone, or the identical failures would never have grouped.
    assert "requestId" not in msg
    assert msg.count("Cannot access Git repository") == 1


def test_the_diag_says_which_credentials_the_loop_would_try(tmp_path):
    """#157: the container side had `credential_probe`; the API-list side had nothing, so a refused
    create could not be told from a credential Sage never considered."""
    cp = FakeControlPlane(credentials=[
        CredentialRef(id="a", label="PAT (github.com)", domain="github.com",
                      protocol="https", usable=True),
        CredentialRef(id="ssh", label="my key (github.com) [SSH]", domain="github.com",
                      protocol="ssh", usable=False),
    ])
    assert _service(tmp_path, cp).git_credential_diag() == {
        "host": "github.com",
        "will_try": ["PAT (github.com)"],
        "skipped": ["my key (github.com) [SSH]"],
    }


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


def test_the_new_repos_description_names_the_packs_assistant(tmp_path, monkeypatch, no_network_seed):
    """The repo description is prose Sage writes into a repo the user owns and publishes, so an
    OEM pack renames it (#109). The app name inside it is what the person typed and is not."""
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "none.json")
    pack = tmp_path / "brand.json"
    pack.write_text(json.dumps({"productName": "Acme", "assistantName": "Ada"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(pack))
    seen = {}

    class _Recording(FakeRepoProvider):
        def create_repo(self, name, *, description="", private=True):
            seen["description"] = description
            return super().create_repo(name, description=description, private=private)

    _service(tmp_path, repo=_Recording(), seed_calls=no_network_seed).create_app("Domino Sales")

    assert seen["description"] == "Ada app: Domino Sales"
