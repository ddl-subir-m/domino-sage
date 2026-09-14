"""The Workbench door (#45): first open creates Default and lands in this viewer's Sage Builder.

Driven at the service seam against the Fake Control Plane and a fake git provider, per the spec's
testing decision — no UI, no HTTP client, no browser.
"""
import pytest

from sage.provision import naming
from sage.provision.domino import FakeControlPlane, ProjectRef, UserRef
from sage.provision.door import DefaultProjectRepoUnreachable, Door
from sage.provision.github import FakeRepoProvider
from sage.provision.service import ProvisionService

ALICE = UserRef(id="507f1f77bcf86cd799439011", name="alice")
BOB = UserRef(id="607f1f77bcf86cd799439022", name="bob")


def _door(tmp_path, cp=None, repo=None, viewer=ALICE):
    # The fake acts as the viewer, so the builders it creates are owned by them — attaching
    # is scoped to the viewer's own workspaces (#47).
    cp = cp or FakeControlPlane(user=viewer)
    service = ProvisionService(cp, repo or FakeRepoProvider(), tmp_path, seed=lambda *a, **k: None)
    return Door(service, lambda: viewer), cp


def test_first_open_creates_the_default_project_and_a_builder(tmp_path):
    repo = FakeRepoProvider()
    door, cp = _door(tmp_path, repo=repo)

    target = door.ensure_default()

    expected = naming.default_project_name("alice", ALICE.id)
    assert expected.startswith("sage-alice-")
    # The same name in git and in the Control Plane — that is what the next door call looks up.
    assert [r.full_name for r in repo.created] == [f"test-owner/{expected}"]
    assert [p.name for p in cp.projects] == [expected]
    assert cp.projects[0].git_url == repo.created[0].clone_url
    # Their Sage Builder was started, and the browser has somewhere to go.
    assert cp.workspaces[target.project.id]
    assert target.created is True
    assert target.launched is True
    assert target.open_url == f"/alice/{expected}/notebookSession/run-{target.project.id}/"


def test_second_open_reuses_the_default_and_its_running_builder(tmp_path):
    repo = FakeRepoProvider()
    door, cp = _door(tmp_path, repo=repo)

    first = door.ensure_default()
    cp.workspaces[first.project.id][0]["state"] = "running"
    second = door.ensure_default()

    assert second.project.id == first.project.id
    assert second.created is False
    assert second.launched is False  # nothing to wait for — the builder was already up
    assert len(cp.projects) == 1  # no second Default
    assert len(repo.created) == 1  # and no second repo
    assert len(cp.workspaces[first.project.id]) == 1  # and no second builder


def test_second_open_resumes_a_stopped_builder_in_place(tmp_path):
    door, cp = _door(tmp_path)

    first = door.ensure_default()
    ws = cp.workspaces[first.project.id][0]
    ws["state"] = "Stopped"

    second = door.ensure_default()

    assert second.project.id == first.project.id
    assert second.launched is True  # the viewer waits, but only because the builder was down
    assert len(cp.workspaces[first.project.id]) == 1  # resumed in place, not piled up
    assert cp.workspaces[first.project.id][0]["state"] == "running"


def test_the_door_finds_the_default_by_its_domino_name_not_the_chip(tmp_path):
    # Naming the chip writes a Sage display overlay and leaves Domino alone, so nothing the viewer
    # types can move the Default. A Project they created and named sits alongside it, unconfused.
    door, cp = _door(tmp_path)
    first = door.ensure_default()
    cp.create_project("sage-sales-app", git_url="https://github.com/me/sage-sales-app.git")

    again = door.ensure_default()

    assert again.project.id == first.project.id
    assert again.created is False


def test_a_collision_suffix_never_shadows_the_real_default(tmp_path):
    expected = naming.default_project_name("alice", ALICE.id)
    cp = FakeControlPlane()
    # The -N project is this viewer's too, but the exact name is the one they have been landing on.
    cp.projects.append(ProjectRef(id="p-2", name=f"{expected}-2", git_url=f"https://g/me/{expected}-2.git"))
    cp.projects.append(ProjectRef(id="p-1", name=expected, git_url=f"https://g/me/{expected}.git"))
    door, _ = _door(tmp_path, cp)

    assert door.ensure_default().project.id == "p-1"


def test_a_suffixed_default_is_still_this_viewers_default(tmp_path):
    # Only the -N name exists (the base repo name was taken when it was created) — reuse it rather
    # than creating a second Default.
    expected = naming.default_project_name("alice", ALICE.id)
    cp = FakeControlPlane()
    cp.projects.append(ProjectRef(id="p-2", name=f"{expected}-2", git_url=f"https://g/me/{expected}-2.git"))
    door, _ = _door(tmp_path, cp)

    target = door.ensure_default()
    assert target.project.id == "p-2"
    assert target.created is False
    assert len(cp.projects) == 1


def test_two_viewers_get_their_own_default(tmp_path):
    cp = FakeControlPlane()
    repo = FakeRepoProvider()
    alice, _ = _door(tmp_path, cp, repo, viewer=ALICE)
    bob, _ = _door(tmp_path, cp, repo, viewer=BOB)

    a, b = alice.ensure_default(), bob.ensure_default()

    assert a.project.id != b.project.id
    assert a.project.name.startswith("sage-alice-")
    assert b.project.name.startswith("sage-bob-")
    # Neither viewer's second visit picks up the other's Project.
    assert alice.ensure_default().project.id == a.project.id
    assert bob.ensure_default().project.id == b.project.id


def test_a_project_that_is_not_this_viewers_default_is_never_adopted(tmp_path):
    # Someone else's Sage Project is visible in the list; the door must create alice's own.
    cp = FakeControlPlane()
    cp.projects.append(ProjectRef(id="p-bob", name=naming.default_project_name("bob", BOB.id),
                                 git_url="https://g/me/sage-bob-1.git"))
    door, _ = _door(tmp_path, cp)

    target = door.ensure_default()
    assert target.created is True
    assert target.project.id != "p-bob"


def test_the_viewer_is_the_control_planes_own_identity(tmp_path):
    # The door names the Default after whoever the sidecar token acts as — no inbound JWT path.
    cp = FakeControlPlane(user=UserRef(id="99887766", name="Carol Danvers"))
    service = ProvisionService(cp, FakeRepoProvider(), tmp_path, seed=lambda *a, **k: None)

    target = Door(service, cp.whoami).ensure_default()

    assert target.project.name == naming.default_project_name("Carol Danvers", "99887766")
    assert target.project.name.startswith("sage-carol-danvers-")


def test_a_sage_builder_can_provision_even_though_it_is_not_the_door(monkeypatch):
    """The provision service is gated on capability, not on role.

    A Sage Builder is not a door — it must never serve the bounce page — but #46/#47 create
    Projects and attach builders from Sage Builder chrome, so it still needs a ProvisionService.
    """
    import sage.orchestrator.app as appmod

    monkeypatch.delenv("SAGE_GIT_HOST", raising=False)
    monkeypatch.setattr(appmod, "proxy_is_app", lambda: False)

    service = appmod._build_provision_service(FakeControlPlane())
    assert service is not None
    assert appmod._build_door(service, FakeControlPlane()) is None

    monkeypatch.setattr(appmod, "proxy_is_app", lambda: True)
    assert appmod._build_door(service, FakeControlPlane()) is not None


def test_nothing_provisions_without_a_domino_control_plane(monkeypatch):
    # A laptop run has nothing to provision against, in either role.
    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "proxy_is_app", lambda: True)
    assert appmod._build_provision_service(None) is None
    assert appmod._build_door(None, None) is None


class _UnaskableRepoProvider(FakeRepoProvider):
    """A provider that cannot answer: GitHub unreachable, rate-limited, or the token expired."""

    def repo_exists(self, full_name: str) -> bool | None:
        return None


def _default_whose_launch_fails(tmp_path, *, repo, git_url=None, cp_cls=FakeControlPlane):
    """A Default Project still in Domino whose workspace launch refuses, over `repo`.

    The live shape: the person deleted the GitHub repo by hand, the Domino Project outlived it
    (Sage has no path that archives one), and `_find_default` matches on the Domino name — so the
    door lands on the broken Project on every open, forever.
    """
    expected = naming.default_project_name("alice", ALICE.id)
    git_url = git_url or f"https://github.com/test-owner/{expected}.git"
    cp = cp_cls(user=ALICE)
    cp.projects.append(ProjectRef(id="p-1", name=expected, git_url=git_url))
    cp.workspace_launch_error = (
        f"POST /v4/workspace/project/p-1/workspace -> 500: "
        f'{{"message":"Cannot access Git repository with URI: {git_url}. This may be due to '
        f'invalid Git credentials.","success":false}}'
    )
    door, _ = _door(tmp_path, cp, repo)
    return door, expected


def test_a_default_whose_repo_is_gone_says_so_instead_of_blaming_credentials(tmp_path):
    # Domino's refusal names Git credentials, which are fine — it means the repo. Sage asks the
    # provider rather than reading Domino's copy (ADR-0033), so it can say which one it was.
    repo = FakeRepoProvider()
    door, expected = _default_whose_launch_fails(tmp_path, repo=repo)

    with pytest.raises(DefaultProjectRepoUnreachable) as caught:
        door.ensure_default()

    message = str(caught.value)
    assert expected in message            # WHICH Project is broken
    assert "archive" in message.lower()   # a way out, not just a diagnosis
    # It names BOTH causes a 404 can mean. Claiming the credentials are fine would be the same
    # confident wrong answer as Domino's, pointed the other way: Domino launched with the
    # credential stored on the Project, this check used the container's own token.
    assert "deleted" in message.lower() and "credential" in message.lower()
    # The fake answers False for any name it does not hold, so the name itself has to be pinned —
    # a wrong `owner/name` would pass every other assertion here.
    assert repo.existence_checks == [f"test-owner/{expected}"]


def test_a_launch_failure_over_a_live_repo_keeps_dominos_own_words(tmp_path):
    # The repo is there, so this really is the credential failure Domino describes. Saying "your
    # repo is gone" here would send the person to the wrong fix — the exact fault being closed.
    alive = FakeRepoProvider()
    alive.create_repo(naming.default_project_name("alice", ALICE.id))
    door, _ = _default_whose_launch_fails(tmp_path, repo=alive)

    with pytest.raises(Exception) as caught:
        door.ensure_default()

    assert not isinstance(caught.value, DefaultProjectRepoUnreachable)
    assert "Cannot access Git repository" in str(caught.value)


def test_a_provider_that_cannot_be_asked_leaves_dominos_words_standing(tmp_path):
    # "Couldn't check" must never read as "it's gone". A person whose credential really is dead
    # would otherwise be told to archive a Project that is fine, and the repo with it.
    door, _ = _default_whose_launch_fails(tmp_path, repo=_UnaskableRepoProvider())

    with pytest.raises(Exception) as caught:
        door.ensure_default()

    assert not isinstance(caught.value, DefaultProjectRepoUnreachable)
    assert "Cannot access Git repository" in str(caught.value)


def test_a_default_on_another_git_host_is_never_judged_by_our_provider(tmp_path):
    # A Default pointing somewhere this provider holds no token for. Asking GitHub about a GitLab
    # path gets a 404 that means "not on GitHub", not "gone" — and archiving a healthy Project on
    # that advice cannot be undone. The provider is only ever asked about its own host.
    door, _ = _default_whose_launch_fails(
        tmp_path,
        repo=FakeRepoProvider(),  # empty: it would answer "gone" for any path it is handed
        git_url="https://gitlab.com/test-owner/sage-alice-elsewhere.git",
    )

    with pytest.raises(Exception) as caught:
        door.ensure_default()

    assert not isinstance(caught.value, DefaultProjectRepoUnreachable)
    assert "Cannot access Git repository" in str(caught.value)


class _OutageControlPlane(FakeControlPlane):
    """Domino is down for the listing `open_app` does before any launch — nothing to do with git."""

    def list_workspaces(self, project_id):
        raise RuntimeError(f"GET /v4/workspace/project/{project_id}/workspace -> 503: unavailable")


def test_a_failure_that_isnt_the_launch_is_never_reinterpreted(tmp_path):
    # `open_app` also lists projects and workspaces. A transient Domino outage in either is
    # retriable and has nothing to do with the repository — rewriting it into permanent-sounding
    # advice to archive the Project would lose the Project over a blip.
    door, _ = _default_whose_launch_fails(
        tmp_path, repo=FakeRepoProvider(), cp_cls=_OutageControlPlane,
    )

    with pytest.raises(Exception) as caught:
        door.ensure_default()

    assert not isinstance(caught.value, DefaultProjectRepoUnreachable)
    assert "503" in str(caught.value)


def test_a_stored_uri_carrying_a_token_never_reaches_the_card_or_the_log(tmp_path):
    """`list_apps` selects on the repo-NAME prefix and never reads the rest of the URI, so a
    credentialed `mainRepository.uri` arrives here intact — and this message is drawn on a page
    and written to the container log."""
    door, _ = _default_whose_launch_fails(
        tmp_path,
        repo=FakeRepoProvider(),
        git_url="https://sage-bot:ghp_realsecretvalue@github.com/test-owner/sage-alice-tok.git",
    )

    with pytest.raises(DefaultProjectRepoUnreachable) as caught:
        door.ensure_default()

    message = str(caught.value)
    assert "ghp_realsecretvalue" not in message
    assert "sage-bot" not in message
    assert "https://github.com/test-owner/sage-alice-tok.git" in message  # still says WHICH repo
