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
from sage.provision.github import FakeRepoProvider, RepoNameConflict
from sage.provision.seed import seed_and_push
from sage.provision.service import ProvisionService
from sage.workspace.manager import ProjectRecord


def _service(tmp_path, cp=None, repo=None, **kw) -> ProvisionService:
    return ProvisionService(cp or FakeControlPlane(), repo or FakeRepoProvider(), tmp_path,
                            seed=lambda *a, **k: None, **kw)


def test_the_control_plane_name_is_the_sage_repo_name_not_what_was_typed(tmp_path):
    # Sage finds a Project by its Domino name — the door looks the Default up that way — and a
    # typed name is neither unique nor stable.
    cp = FakeControlPlane()
    repo = FakeRepoProvider()

    created = _service(tmp_path, cp, repo).create_app("Quarterly Revenue!")

    assert [r.full_name for r in repo.created] == ["test-owner/sage-quarterly-revenue"]
    assert created.project.name == "sage-quarterly-revenue"
    assert cp.projects[0].name == "sage-quarterly-revenue"


def test_the_typed_name_is_the_projects_description(tmp_path):
    # So the Project is still findable in Domino's own UI, where the name is a slug.
    seen = {}

    class Recorder(FakeControlPlane):
        def create_project(self, name, *, git_url, branch="main", description=""):
            seen["description"] = description
            return super().create_project(name, git_url=git_url, branch=branch, description=description)

    _service(tmp_path, Recorder()).create_app("Quarterly Revenue")

    assert seen["description"] == "Quarterly Revenue"


def test_the_typed_name_is_seeded_as_the_chip_of_the_builder_that_does_not_exist_yet(tmp_path):
    # The new builder has nothing but the repo, so the overlay has to ride in the initial commit.
    seen = {}
    service = ProvisionService(
        FakeControlPlane(), FakeRepoProvider(), tmp_path,
        seed=lambda *a, **k: seen.update(k),
    )

    service.create_app("Quarterly Revenue")

    assert seen["settings"] == {"displayName": "Quarterly Revenue"}


def test_a_taken_repo_name_suffixes_both_the_repo_and_the_project(tmp_path):
    class Taken(FakeRepoProvider):
        def create_repo(self, name, *, description="", private=True):
            if name == "sage-sales":
                raise RepoNameConflict(name)
            return super().create_repo(name, description=description, private=private)

    cp = FakeControlPlane()
    created = _service(tmp_path, cp, Taken()).create_app("Sales")

    assert created.repo.full_name == "test-owner/sage-sales-2"
    assert created.project.name == "sage-sales-2"  # the same suffix, or the door can't find it


def test_an_empty_name_is_refused_and_creates_nothing(tmp_path):
    cp = FakeControlPlane()
    repo = FakeRepoProvider()
    service = _service(tmp_path, cp, repo)

    for blank in ("", "   ", "\n\t"):
        with pytest.raises(ValueError):
            service.create_app(blank)

    assert repo.created == []
    assert cp.projects == []


def test_the_new_project_opens_this_creators_builder(tmp_path):
    cp = FakeControlPlane()

    created = _service(tmp_path, cp).create_app("Sales")

    assert cp.workspaces[created.project.id]
    assert created.open_url == f"/tester/sage-sales/notebookSession/run-{created.project.id}/"


def test_a_failure_before_the_project_exists_rolls_the_repo_back(tmp_path):
    # Otherwise the next attempt at the same name collides with an orphan nobody owns.
    repo = FakeRepoProvider()

    def boom(*a, **k):
        raise RuntimeError("push rejected")

    service = ProvisionService(FakeControlPlane(), repo, tmp_path, seed=boom)
    with pytest.raises(RuntimeError):
        service.create_app("Sales")

    assert repo.created == []  # the orphan is gone, so the name is free to retry


# --- the seeder actually writes it -----------------------------------------------------------


def test_the_seeder_commits_the_settings_file_the_builder_will_read(tmp_path):
    template = tmp_path / "template"
    template.mkdir()
    (template / "index.html").write_text("<!doctype html>")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

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


def test_a_container_that_cannot_provision_refuses_to_create():
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    client = TestClient(appmod.control_app)
    r = client.post("/api/projects", json={"name": "Sales"})
    assert r.status_code == 503
    assert "can't create a Project" in r.json()["error"]


# --- what the Workbench does with it ----------------------------------------------------------

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"


def test_creating_a_project_hands_the_browser_over_to_the_new_builder():
    store = (WB / "store.js").read_text()
    api = (WB / "api.js").read_text()

    assert "request('/projects', { method: 'POST', body: { name } })" in api
    assert "SW.api.createProject(trimmed)" in store
    assert "This project is the current scope" not in store   # the old refusal is gone
    # Create and switch end the same way, so they share the wait and the hand-over.
    assert "async function handOver(" in store
    assert "window.location.replace(url)" in store


def test_new_project_is_explained_rather_than_offered_when_it_cannot_work():
    picker = (WB / "components" / "scope-picker.js").read_text()
    store = (WB / "store.js").read_text()

    assert "disabled: !canProvision" in picker
    # Says why, not just greyed out. Written as a token since ADR-0026 gave `Project` a noun
    # key, so the sentence a partner reads is theirs; what is pinned here is that the reason
    # is in the picker at all.
    assert "can’t create a {project}" in picker
    assert "canProvision: false," in store


def test_new_conversation_still_does_not_provision():
    # Starting a chat must never create a Domino project — it opens a Thread in the Project you
    # are already in.
    store = (WB / "store.js").read_text()
    new_thread = store[store.index("async newThread("):store.index("async openThread(")]

    assert "createThread" in new_thread
    assert "createProject" not in new_thread
    assert "openProject" not in new_thread
