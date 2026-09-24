"""New project creates a sage-* Project and lands in Chat (#46).

Service seam against the Fake Control Plane and a fake git provider, plus source pins for what the
Workbench does with the answer. The seeder is driven for real against a temp git repo, because the
one thing that carries the typed name into a container that does not exist yet is the commit it
makes.
"""
import json
import subprocess
from pathlib import Path

import pytest

from sage.provision.domino import FakeControlPlane
from sage.provision.github import FakeRepoProvider, RepoInfo, RepoNameConflict
from sage.provision.seed import seed_and_push
from sage.provision.service import ProvisionService
from sage.workspace.manager import ProjectRecord


def _service(tmp_path, cp=None, repo=None, **kw) -> ProvisionService:
    return ProvisionService(cp or FakeControlPlane(), repo or FakeRepoProvider(), tmp_path,
                            seed=lambda *a, **k: None, **kw)


def test_the_control_plane_name_is_the_sage_repo_name_not_what_was_typed(tmp_path):
    # Sage finds a Project by its Domino name — the registry's own entry names it that way (its
    # slug on disk, `local_slugs()`'s scan) — and a typed name is neither unique nor stable.
    cp = FakeControlPlane()
    repo = FakeRepoProvider()

    project, repo_info = _service(tmp_path, cp, repo).provision_project("Quarterly Revenue!")

    assert [r.full_name for r in repo.created] == ["test-owner/sage-quarterly-revenue"]
    assert repo_info.full_name == "test-owner/sage-quarterly-revenue"
    assert project.name == "sage-quarterly-revenue"
    assert cp.projects[0].name == "sage-quarterly-revenue"


def test_the_typed_name_is_the_projects_description(tmp_path):
    # So the Project is still findable in Domino's own UI, where the name is a slug.
    seen = {}

    class Recorder(FakeControlPlane):
        def create_project(self, name, *, git_url, git_credential_id="cred-1", branch="main",
                           description=""):
            seen["description"] = description
            return super().create_project(name, git_url=git_url, git_credential_id=git_credential_id,
                                          branch=branch, description=description)

    _service(tmp_path, Recorder()).provision_project("Quarterly Revenue")

    assert seen["description"] == "Quarterly Revenue"


def test_the_typed_name_is_seeded_as_the_chip_of_the_project_that_does_not_exist_yet(tmp_path):
    # The new project has nothing but the repo, so the overlay has to ride in the initial commit.
    seen = {}
    service = ProvisionService(
        FakeControlPlane(), FakeRepoProvider(), tmp_path,
        seed=lambda *a, **k: seen.update(k),
    )

    service.provision_project("Quarterly Revenue")

    assert seen["settings"] == {"displayName": "Quarterly Revenue"}


def test_a_taken_repo_name_suffixes_both_the_repo_and_the_project(tmp_path):
    class Taken(FakeRepoProvider):
        def create_repo(self, name, *, description="", private=True):
            if name == "sage-sales":
                raise RepoNameConflict(name)
            return super().create_repo(name, description=description, private=private)

    cp = FakeControlPlane()
    project, repo_info = _service(tmp_path, cp, Taken()).provision_project("Sales")

    assert repo_info.full_name == "test-owner/sage-sales-2"
    assert project.name == "sage-sales-2"  # the same suffix, or a clone can't find it by name


def test_an_empty_name_is_refused_and_creates_nothing(tmp_path):
    cp = FakeControlPlane()
    repo = FakeRepoProvider()
    service = _service(tmp_path, cp, repo)

    for blank in ("", "   ", "\n\t"):
        with pytest.raises(ValueError):
            service.provision_project(blank)

    assert repo.created == []
    assert cp.projects == []


def test_provision_project_seeds_directly_into_the_given_destination(tmp_path):
    """`registry.create` (Phase 3 step 1) needs the seeded template to land in the caller's own
    project directory, not a throwaway tempdir — `dest_for` is how it says where."""
    template = tmp_path / "template"
    template.mkdir()
    (template / "index.html").write_text("<!doctype html>")

    class BareRepoProvider(FakeRepoProvider):
        """A real bare repo on disk, so `seed_and_push`'s real push has something to push to."""

        def create_repo(self, name, *, description="", private=True):
            info = super().create_repo(name, description=description, private=private)
            bare = tmp_path / "remotes" / f"{name}.git"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
            return RepoInfo(full_name=info.full_name, clone_url=str(bare), private=info.private)

    seen: dict = {}

    def dest_for(repo_name: str) -> Path:
        seen["repo_name"] = repo_name
        return tmp_path / "projects" / repo_name

    service = ProvisionService(FakeControlPlane(), BareRepoProvider(), template, seed=seed_and_push)
    project, repo = service.provision_project("Quarterly Revenue", dest_for=dest_for)

    dest = tmp_path / "projects" / seen["repo_name"]
    assert (dest / "index.html").exists()  # the seeded dir IS the working directory, not a tempdir
    assert (dest / ".git").is_dir()
    assert project.name == seen["repo_name"]
    assert repo.clone_url == str(tmp_path / "remotes" / f"{seen['repo_name']}.git")


def test_provision_project_with_no_dest_for_still_uses_a_throwaway_tempdir(tmp_path):
    """A caller passing no `dest_for` (none does today, since `registry.create()` always does) gets
    the original behavior: the seed lands in a tempdir that is gone by the time this returns."""
    seen: dict = {}
    service = ProvisionService(
        FakeControlPlane(), FakeRepoProvider(), tmp_path,
        seed=lambda *a, dest=None, **k: seen.update(dest=dest),
    )
    service.provision_project("Sales")
    assert seen["dest"] is None


def test_a_failure_before_the_project_exists_rolls_the_repo_back(tmp_path):
    # Otherwise the next attempt at the same name collides with an orphan nobody owns.
    repo = FakeRepoProvider()

    def boom(*a, **k):
        raise RuntimeError("push rejected")

    service = ProvisionService(FakeControlPlane(), repo, tmp_path, seed=boom)
    with pytest.raises(RuntimeError):
        service.provision_project("Sales")

    assert repo.created == []  # the orphan is gone, so the name is free to retry


# --- the seeder actually writes it -----------------------------------------------------------


def test_the_seeder_commits_the_settings_file_the_builder_will_read(tmp_path):
    template = tmp_path / "template"
    template.mkdir()
    (template / "index.html").write_text("<!doctype html>")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)

    seed_and_push(str(bare), template, settings={"displayName": "Quarterly Revenue"})

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(bare), str(checkout)], check=True)
    settings = json.loads((checkout / ".sage" / "settings.json").read_text())
    assert settings == {"displayName": "Quarterly Revenue"}
    # And the manager reads exactly that as the chip.
    assert ProjectRecord(project_id="new", path=checkout).display_name() == "Quarterly Revenue"


def test_the_seeder_leaves_the_repo_alone_when_there_is_nothing_to_plant(tmp_path):
    template = tmp_path / "template"
    template.mkdir()
    (template / "index.html").write_text("<!doctype html>")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

    seed_and_push(str(bare), template)

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(checkout.parent / "origin.git"), str(checkout)], check=True)
    assert not (checkout / ".sage" / "settings.json").exists()


# --- the route -------------------------------------------------------------------------------


def test_a_container_that_cannot_provision_refuses_to_create(monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    # Forced, not assumed: this sandbox is itself a real Domino workspace, so the module-level
    # `_provision` this route reads is a genuine, live ProvisionService by the time this test runs —
    # leaving it alone would have this test actually create a real GitHub repo + Domino project on
    # every run (found live, 2026-09-24: 23+ orphaned `sage-sales-N` repos on a real account).
    monkeypatch.setattr(appmod, "_provision", None)
    client = TestClient(appmod.control_app)
    r = client.post("/api/projects", json={"name": "Sales"})
    assert r.status_code == 503
    assert "can't create a Project" in r.json()["error"]


# --- what the Workbench does with it ----------------------------------------------------------

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"


def test_the_workbench_no_longer_hands_the_browser_over_to_create():
    """ONE-APP-PLAN.md §4 Phase 2 step 5: creating and switching used to end the same way — a URL
    in another container and a wait for its session (`handOver`). Switching is now a same-origin
    navigation (see `test_project_dispatch.py` and the scope-picker source), and creating is
    disabled until Phase 3's registry-backed `create()` exists — so neither reaches for that
    mechanism any more, and it is gone rather than left dangling with nothing to call it.
    """
    store = (WB / "store.js").read_text()

    assert "async function handOver(" not in store
    assert "async attachProject(" not in store
    assert "async createProject(" not in store
    assert "window.location.replace(url)" not in store


def test_new_project_is_a_real_link_now_that_create_works(tmp_path):
    """ONE-APP-PLAN.md Phase 3 step 3: `registry.create()` is real now, so "New project" is a plain
    link to the Projects home (its own form lives there, not duplicated into this popover)."""
    picker = (WB / "components" / "scope-picker.js").read_text()

    assert "disabled: true" not in picker
    assert "arrives in a later phase" not in picker
    assert "window.location.assign('../')" in picker


def test_new_conversation_still_does_not_provision():
    # Starting a chat must never create a Domino project — it opens a Thread in the Project you
    # are already in.
    store = (WB / "store.js").read_text()
    new_thread = store[store.index("async newThread("):store.index("async openThread(")]

    assert "createThread" in new_thread
    assert "createProject" not in new_thread
    assert "openProject" not in new_thread
