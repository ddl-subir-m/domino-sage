"""Orchestrator tests (Phase 2) — single bound project, no multi-project surface."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True).stdout


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


def test_shutdown_saves_work_before_stopping_resources(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)  # attach so there's a project to save

    saved = []
    orch._save_to_git = lambda project, prompt: saved.append(prompt) or None

    orch.shutdown()
    assert saved == ["save before stop"]  # committed + pushed before teardown


def test_shutdown_without_a_project_is_a_noop(tmp_path: Path):
    orch = _orch(tmp_path)  # never attached
    saved = []
    orch._save_to_git = lambda project, prompt: saved.append(prompt)
    orch.shutdown()
    assert saved == []


def test_sync_pulls_teammate_changes_and_pushes(tmp_path: Path):
    # A bare remote with a workspace checkout at the bound volume, plus a teammate's clone.
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    ws = tmp_path / "mnt" / "code"
    ws.parent.mkdir(parents=True)
    _git(tmp_path, "clone", "-q", str(bare), str(ws))
    _git(ws, "config", "user.email", "sage@example.com")
    _git(ws, "config", "user.name", "Sage")
    (ws / "seed.txt").write_text("seed")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    _git(ws, "push", "-q", "-u", "origin", "HEAD")

    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    _git(other, "config", "user.email", "mate@example.com")
    _git(other, "config", "user.name", "Mate")
    (other / "mate.txt").write_text("from teammate")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "mate")
    _git(other, "push", "-q")

    orch = Orchestrator(
        workspace_dir=ws, template=_template(tmp_path), gateway=object(),
        catalog=_catalog(), project_id="Sage",
    )
    orch.project(start_preview=False)  # attach without Vite (sync reuses the memoized project)

    result = orch.sync()
    assert result["status"] == "merged"
    assert result["pushed"] is True
    assert (ws / "mate.txt").read_text() == "from teammate"  # pulled into the workspace
    # The template files the attach seeded were committed + pushed alongside the merge.
    assert "package.json" in _git(bare, "ls-tree", "--name-only", "HEAD")


def test_no_multi_project_surface():
    for gone in ("create_project", "open_project", "get", "active", "list_ids", "list_all_ids", "delete_project"):
        assert not hasattr(Orchestrator, gone), f"{gone} should be retired in Phase 2"
