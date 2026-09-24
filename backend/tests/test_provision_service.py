import json

import httpx
import pytest

from sage.provision.domino import CredentialRef, DominoControlPlane, FakeControlPlane
from sage.provision.github import FakeRepoProvider
from sage.provision.service import ProvisionService


@pytest.fixture
def no_network_seed():
    """A recording no-op seeder so provision_project runs without touching git or the network."""
    calls = []
    yield calls


def _service(tmp_path, cp=None, repo=None, seed_calls=None):
    seed = (lambda url, tmpl, **kw: seed_calls.append((url, kw))) if seed_calls is not None else (lambda *a, **k: None)
    return ProvisionService(cp or FakeControlPlane(), repo or FakeRepoProvider(), tmp_path, seed=seed)


def test_provision_project_provisions_repo_and_project(tmp_path, no_network_seed):
    cp, repo = FakeControlPlane(), FakeRepoProvider()
    svc = _service(tmp_path, cp, repo, seed_calls=no_network_seed)

    project, repo_info = svc.provision_project("My App")

    assert repo_info.full_name == "test-owner/sage-my-app"
    assert repo_info.private is True
    # The Domino project is named after the repo, never after what was typed (#46) — Sage looks a
    # Project up by that name, and the typed name rides in as the chip overlay instead.
    assert project.name == "sage-my-app"
    assert project.git_url == repo_info.clone_url
    # seed was invoked with the new repo's clone URL
    assert no_network_seed and no_network_seed[0][0] == repo_info.clone_url


def test_the_initial_commit_is_authored_as_the_real_control_plane_identity(tmp_path, no_network_seed):
    """`_committer_identity()`: the seed step's initial commit is attributed to the real,
    authenticated person `whoami()` names, not left to git's own neutral fallback."""
    from sage.provision.domino import UserRef

    cp = FakeControlPlane(user=UserRef(id="u-1", name="etan_lightstone",
                                       full_name="Etan Lightstone", email="etan@example.com"))
    _service(tmp_path, cp, seed_calls=no_network_seed).provision_project("My App")

    assert no_network_seed[0][1]["identity"] == ("Etan Lightstone", "etan@example.com")


def test_the_initial_commit_falls_back_to_none_with_no_full_name_or_email(tmp_path, no_network_seed):
    # FakeControlPlane's default user has an id/name but no full_name/email — the shape a
    # whoami() built for id/name alone (older fixtures, or a real answer that never fetched them).
    _service(tmp_path, seed_calls=no_network_seed).provision_project("My App")

    assert no_network_seed[0][1]["identity"] is None


def test_the_initial_commit_identity_survives_a_whoami_failure(tmp_path, no_network_seed):
    class _BrokenControlPlane(FakeControlPlane):
        def whoami(self):
            raise RuntimeError("network hiccup")

    _service(tmp_path, _BrokenControlPlane(), seed_calls=no_network_seed).provision_project("My App")

    assert no_network_seed[0][1]["identity"] is None


def test_provision_project_resolves_repo_name_collision(tmp_path, no_network_seed):
    repo = FakeRepoProvider()
    repo.create_repo("sage-my-app")  # occupy the base name
    svc = _service(tmp_path, FakeControlPlane(), repo, seed_calls=no_network_seed)

    _, repo_info = svc.provision_project("My App")
    assert repo_info.full_name == "test-owner/sage-my-app-2"


def test_provision_project_requires_name(tmp_path, no_network_seed):
    with pytest.raises(ValueError):
        _service(tmp_path).provision_project("   ")


def test_rollback_deletes_repo_when_seed_fails(tmp_path):
    repo = FakeRepoProvider()

    def failing_seed(url, tmpl, **kw):
        raise RuntimeError("push failed")

    svc = ProvisionService(FakeControlPlane(), repo, tmp_path, seed=failing_seed)
    with pytest.raises(RuntimeError, match="push failed"):
        svc.provision_project("My App")
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
        svc.provision_project("My App")
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
    project, _ = _service(tmp_path, cp).provision_project("My App")

    assert project.name == "sage-my-app"
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
    _service(tmp_path, cp).provision_project("My App")
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
        _service(tmp_path, cp, repo).provision_project("My App")

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
        _service(tmp_path, cp).provision_project("My App")

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

    _service(tmp_path, repo=_Recording(), seed_calls=no_network_seed).provision_project("Domino Sales")

    assert seen["description"] == "Ada app: Domino Sales"
