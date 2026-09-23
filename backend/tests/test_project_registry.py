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

    def list_apps(self):
        return self.apps


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
    assert rows == [ProjectRow(slug="alpha", name="Project alpha", local=True, current=False)]


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
        ProjectRow(slug="alpha", name="Project alpha", local=True, current=False),
        ProjectRow(slug="sage-remote-one", name="Remote One", local=False, current=False),
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
                                local=True, current=False)]


def test_a_remote_project_with_no_git_url_falls_back_to_its_id(tmp_path):
    from sage.provision.domino import ProjectRef

    remote = _FakeControlPlane(apps=[ProjectRef(id="proj-remote", name="Remote One", git_url=None)])
    reg = ProjectRegistry(tmp_path, _counting_factory([]), control_plane=remote)
    rows = reg.list()
    assert rows == [ProjectRow(slug="proj-remote", name="Remote One", local=False, current=False)]
