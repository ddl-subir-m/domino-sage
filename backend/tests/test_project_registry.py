"""ONE-APP-PLAN.md §2.2: the per-process registry of open projects.

`ProjectRegistry` owns only the on-disk shape (`.sage/project.json`, a directory scan, no index —
ADR-0008's rule) and the get-or-build/cache/close lifecycle. It is handed a factory closure at
construction rather than knowing how to build an `Orchestrator` itself, so these tests use a bare
counter-and-fake stand-in instead of a real one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sage.projects.registry import ProjectRegistry, ProjectRow, RegistryEntry
from sage.provision.domino import ProjectRef


def _write_entry(home, slug, **overrides):
    fields = {
        "slug": slug,
        "domino_project_id": f"proj-{slug}",
        "domino_project_name": f"Project {slug}",
        "owner_name": "etan",
        "repo_url": f"https://github.com/o/sage-{slug}.git",
        "created_at": "2026-09-23T00:00:00Z",
    }
    fields.update(overrides)
    entry = RegistryEntry(**fields)
    entry.save(home / "projects" / slug / ".sage" / "project.json")
    return entry


@dataclass
class _FakeSupervisor:
    stopped: bool = False

    def stop(self):
        self.stopped = True


@dataclass
class _FakeProject:
    supervisor: _FakeSupervisor = field(default_factory=_FakeSupervisor)


@dataclass
class _FakeOrchestrator:
    entry: RegistryEntry
    workspace_dir: object
    _project: _FakeProject | None = None


@dataclass
class _FakeControlPlane:
    apps: list
    who: str = "etan"

    def list_apps(self):
        return self.apps

    def whoami(self):
        from types import SimpleNamespace
        return SimpleNamespace(name=self.who)


@dataclass
class _FakeRepoInfo:
    full_name: str
    clone_url: str
    private: bool = True


@dataclass
class _FakeProvision:
    """Stands in for `ProvisionService.provision_project` (registry.create's only dependency on
    it): given a display name, calls `dest_for` with the final repo name (mimicking a `-N` collision
    suffix `_create_repo` may have taken) and materializes a directory there, the same contract
    `seed_and_push(dest=...)` fulfils for real."""

    repo_name: str = "sage-alpha"
    project_id: str = "proj-1"
    fails: bool = False

    def provision_project(self, display_name, *, name=None, dest_for=None):
        dest = dest_for(self.repo_name) if dest_for is not None else None
        if dest is not None:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "app.py").write_text("# seeded\n")
        if self.fails:
            raise RuntimeError("domino refused the project create")
        project = ProjectRef(id=self.project_id, name=self.repo_name,
                              git_url=f"https://github.com/o/{self.repo_name}.git")
        repo = _FakeRepoInfo(full_name=f"o/{self.repo_name}",
                             clone_url=f"https://github.com/o/{self.repo_name}.git")
        return project, repo


def _counting_factory(calls):
    def build(entry, workspace_dir):
        calls.append(entry.slug)
        return _FakeOrchestrator(entry=entry, workspace_dir=workspace_dir)
    return build


def test_local_slugs_finds_only_directories_with_a_record(tmp_path):
    _write_entry(tmp_path, "alpha")
    _write_entry(tmp_path, "beta")
    (tmp_path / "projects" / "not-a-project").mkdir(parents=True)  # no .sage/project.json
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    assert reg.local_slugs() == ["alpha", "beta"]


def test_no_projects_dir_yet_is_an_empty_list_not_an_error(tmp_path):
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    assert reg.local_slugs() == []
    assert reg.list() == []


def test_entry_round_trips_through_save_and_load(tmp_path):
    written = _write_entry(tmp_path, "alpha", owner_name="etan lightstone")
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    assert reg.entry("alpha") == written
    assert reg.entry("no-such-slug") is None


def test_open_builds_once_and_caches(tmp_path):
    _write_entry(tmp_path, "alpha")
    calls = []
    reg = ProjectRegistry(tmp_path, _counting_factory(calls))
    first = reg.open("alpha")
    second = reg.open("alpha")
    assert first is second
    assert calls == ["alpha"]
    assert reg.is_open("alpha")
    assert not reg.is_open("beta")


def test_open_passes_the_entry_and_workspace_dir_to_the_factory(tmp_path):
    _write_entry(tmp_path, "alpha")
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    orch = reg.open("alpha")
    assert orch.entry.slug == "alpha"
    assert orch.workspace_dir == tmp_path / "projects" / "alpha"


def test_all_open_snapshots_every_cached_orchestrator(tmp_path):
    _write_entry(tmp_path, "alpha")
    _write_entry(tmp_path, "beta")
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    assert reg.all_open() == []
    a = reg.open("alpha")
    b = reg.open("beta")
    assert {id(x) for x in reg.all_open()} == {id(a), id(b)}


def test_all_open_reflects_a_close(tmp_path):
    _write_entry(tmp_path, "alpha")
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    reg.open("alpha")
    reg.close("alpha")
    assert reg.all_open() == []


def test_open_an_unknown_slug_raises_key_error(tmp_path):
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    with pytest.raises(KeyError):
        reg.open("ghost")


def test_close_drops_the_cache_so_the_next_open_rebuilds(tmp_path):
    _write_entry(tmp_path, "alpha")
    calls = []
    reg = ProjectRegistry(tmp_path, _counting_factory(calls))
    first = reg.open("alpha")
    reg.close("alpha")
    assert not reg.is_open("alpha")
    second = reg.open("alpha")
    assert first is not second
    assert calls == ["alpha", "alpha"]


def test_close_stops_the_bound_projects_supervisor(tmp_path):
    _write_entry(tmp_path, "alpha")
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    orch = reg.open("alpha")
    orch._project = _FakeProject()
    reg.close("alpha")
    assert orch._project.supervisor.stopped


def test_close_with_no_project_ever_bound_is_a_quiet_no_op(tmp_path):
    _write_entry(tmp_path, "alpha")
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    reg.open("alpha")
    reg.close("alpha")  # _project is None (the dataclass default) — must not raise


def test_close_an_unopened_slug_is_a_quiet_no_op(tmp_path):
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    reg.close("never-opened")  # must not raise


def test_list_with_no_control_plane_is_local_only(tmp_path):
    _write_entry(tmp_path, "alpha")
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    rows = reg.list()
    assert rows == [ProjectRow(slug="alpha", name="Project alpha", local=True,
                                domino_project_id="proj-alpha", current=False)]


def test_list_marks_the_current_slug(tmp_path):
    _write_entry(tmp_path, "alpha")
    _write_entry(tmp_path, "beta")
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    rows = reg.list(current="beta")
    assert [r.current for r in rows] == [False, True]


def test_list_merges_remote_projects_the_token_can_see(tmp_path):
    _write_entry(tmp_path, "alpha")
    from sage.provision.domino import ProjectRef

    remote = _FakeControlPlane(apps=[
        ProjectRef(id="proj-remote", name="Remote One",
                   git_url="https://github.com/o/sage-remote-one.git"),
    ])
    reg = ProjectRegistry(tmp_path, _counting_factory([]), control_plane=remote)
    rows = reg.list()
    assert rows == [
        ProjectRow(slug="alpha", name="Project alpha", local=True,
                   domino_project_id="proj-alpha", current=False),
        ProjectRow(slug="sage-remote-one", name="Remote One", local=False,
                   domino_project_id="proj-remote", current=False),
    ]


def test_list_prefers_the_local_row_when_a_remote_slug_collides(tmp_path):
    _write_entry(tmp_path, "sage-remote-one")
    from sage.provision.domino import ProjectRef

    remote = _FakeControlPlane(apps=[
        ProjectRef(id="proj-remote", name="Remote One",
                   git_url="https://github.com/o/sage-remote-one.git"),
    ])
    reg = ProjectRegistry(tmp_path, _counting_factory([]), control_plane=remote)
    rows = reg.list()
    assert rows == [ProjectRow(slug="sage-remote-one", name="Project sage-remote-one",
                                local=True, domino_project_id="proj-sage-remote-one",
                                current=False)]


def test_a_remote_project_with_no_git_url_falls_back_to_its_id(tmp_path):
    from sage.provision.domino import ProjectRef

    remote = _FakeControlPlane(apps=[ProjectRef(id="proj-remote", name="Remote One", git_url=None)])
    reg = ProjectRegistry(tmp_path, _counting_factory([]), control_plane=remote)
    rows = reg.list()
    assert rows == [ProjectRow(slug="proj-remote", name="Remote One", local=False,
                                domino_project_id="proj-remote", current=False)]


# -- create() (Phase 3 step 1) ------------------------------------------------------------------


def test_create_lands_the_seeded_directory_as_a_local_project(tmp_path):
    provision = _FakeProvision(repo_name="sage-alpha", project_id="proj-9")
    reg = ProjectRegistry(tmp_path, _counting_factory([]),
                           control_plane=_FakeControlPlane(apps=[], who="etan"), provision=provision)
    entry = reg.create("Alpha")
    assert entry.slug == "sage-alpha"
    assert entry.domino_project_id == "proj-9"
    assert entry.owner_name == "etan"
    assert (tmp_path / "projects" / "sage-alpha" / "app.py").exists()
    assert reg.local_slugs() == ["sage-alpha"]
    assert reg.entry("sage-alpha") == entry


def test_create_slug_follows_the_actual_repo_name_not_the_display_name(tmp_path):
    # `_create_repo` may take a `-N` collision suffix; the local directory (and slug) has to carry
    # the same suffix `provision_project` was actually seeded under, not a guess from the display name.
    provision = _FakeProvision(repo_name="sage-alpha-2")
    reg = ProjectRegistry(tmp_path, _counting_factory([]),
                           control_plane=_FakeControlPlane(apps=[]), provision=provision)
    entry = reg.create("Alpha")
    assert entry.slug == "sage-alpha-2"
    assert reg.local_slugs() == ["sage-alpha-2"]


def test_create_with_no_provision_service_configured_raises(tmp_path):
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    with pytest.raises(RuntimeError):
        reg.create("Alpha")


def test_create_removes_the_partial_directory_when_the_domino_project_is_refused(tmp_path):
    provision = _FakeProvision(repo_name="sage-alpha", fails=True)
    reg = ProjectRegistry(tmp_path, _counting_factory([]),
                           control_plane=_FakeControlPlane(apps=[]), provision=provision)
    with pytest.raises(RuntimeError):
        reg.create("Alpha")
    assert not (tmp_path / "projects" / "sage-alpha").exists()
    assert reg.local_slugs() == []


# -- clone() (Phase 3 step 2) --------------------------------------------------------------------


def _fake_git_clone(monkeypatch, *, raises: Exception | None = None):
    calls = []

    def fake(clone_url, dest, *, branch="main", token_provider=None):
        calls.append((clone_url, dest, token_provider() if token_provider else None))
        if raises is not None:
            # A real `git clone` that fails partway (network drop mid-transfer) can still leave a
            # partial directory behind — that's the case the cleanup has to handle, not a clean no-op.
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
            raise raises
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "app.py").write_text("# cloned\n")

    monkeypatch.setattr("sage.provision.seed.clone", fake)
    return calls


def test_clone_lands_an_existing_remote_project_locally(tmp_path, monkeypatch):
    calls = _fake_git_clone(monkeypatch)
    remote = _FakeControlPlane(apps=[
        ProjectRef(id="proj-remote", name="Remote One",
                   git_url="https://github.com/o/sage-remote-one.git"),
    ], who="etan")
    reg = ProjectRegistry(tmp_path, _counting_factory([]), control_plane=remote,
                           git_token_provider=lambda: "ghp_secret")
    entry = reg.clone("proj-remote")
    assert entry.slug == "sage-remote-one"
    assert entry.domino_project_id == "proj-remote"
    assert entry.owner_name == "etan"
    assert (tmp_path / "projects" / "sage-remote-one" / "app.py").exists()
    assert reg.local_slugs() == ["sage-remote-one"]
    assert calls[0][2] == "ghp_secret"  # the registry's own token provider reached the clone


def test_clone_of_an_unknown_project_id_raises_key_error(tmp_path, monkeypatch):
    _fake_git_clone(monkeypatch)
    reg = ProjectRegistry(tmp_path, _counting_factory([]), control_plane=_FakeControlPlane(apps=[]))
    with pytest.raises(KeyError):
        reg.clone("no-such-id")


def test_clone_refuses_when_the_slug_already_exists_locally(tmp_path, monkeypatch):
    _write_entry(tmp_path, "sage-remote-one")
    _fake_git_clone(monkeypatch)
    remote = _FakeControlPlane(apps=[
        ProjectRef(id="proj-remote", name="Remote One",
                   git_url="https://github.com/o/sage-remote-one.git"),
    ])
    reg = ProjectRegistry(tmp_path, _counting_factory([]), control_plane=remote)
    with pytest.raises(FileExistsError):
        reg.clone("proj-remote")


def test_clone_with_no_control_plane_configured_raises(tmp_path, monkeypatch):
    _fake_git_clone(monkeypatch)
    reg = ProjectRegistry(tmp_path, _counting_factory([]))
    with pytest.raises(RuntimeError):
        reg.clone("proj-remote")


def test_clone_removes_the_partial_directory_when_git_fails(tmp_path, monkeypatch):
    _fake_git_clone(monkeypatch, raises=RuntimeError("git clone failed"))
    remote = _FakeControlPlane(apps=[
        ProjectRef(id="proj-remote", name="Remote One",
                   git_url="https://github.com/o/sage-remote-one.git"),
    ])
    reg = ProjectRegistry(tmp_path, _counting_factory([]), control_plane=remote)
    with pytest.raises(RuntimeError):
        reg.clone("proj-remote")
    assert not (tmp_path / "projects" / "sage-remote-one").exists()
    assert reg.local_slugs() == []
