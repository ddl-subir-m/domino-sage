"""Orchestrator tests (Phase 2) — single bound project, no multi-project surface."""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _catalog() -> ModelCatalog:
    return ModelCatalog(
        sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
        plan="p", implement="i", ask="a",
    )


def _orch(tmp: Path) -> Orchestrator:
    ws = tmp / "mnt" / "code"
    return Orchestrator(
        workspace_dir=ws,
        template=_template(tmp),
        gateway=object(),  # never called without a build
        catalog=_catalog(),
        project_id="Sage",
    )


def test_project_binds_to_the_volume_and_seeds_it(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)  # no Vite in tests
    assert project.id == "Sage"
    assert project.workspace.path == tmp_path / "mnt" / "code"
    assert (project.workspace.path / "package.json").exists()  # seeded in place


def test_project_is_memoized(tmp_path: Path):
    orch = _orch(tmp_path)
    a = orch.project(start_preview=False)
    b = orch.project(start_preview=False)
    assert a is b  # single bound project, attached once


def test_history_reads_disk_without_attaching(tmp_path: Path):
    orch = _orch(tmp_path)
    assert orch.history() == []  # no attach, no preview
    assert orch._project is None
    # After a turn writes history, it's visible without attaching either.
    orch.project(start_preview=False).workspace.append_history({"type": "user", "text": "hi"})
    orch2 = _orch(tmp_path)
    assert orch2.history() == [{"type": "user", "text": "hi"}]


def test_no_multi_project_surface():
    for gone in ("create_project", "open_project", "get", "active", "list_ids", "list_all_ids", "delete_project"):
        assert not hasattr(Orchestrator, gone), f"{gone} should be retired in Phase 2"
