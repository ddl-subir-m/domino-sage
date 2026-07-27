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


# ---------------------------------------------------------------------------
# Publish / Stop (Domino control-plane wiring). Uses FakeControlPlane — no network.
# ---------------------------------------------------------------------------
from sage.provision.domino import FakeControlPlane, PublishedApp


def _template_with_app_sh(tmp: Path) -> Path:
    t = _template(tmp)
    (t / "app.sh").write_text("#!/bin/bash\n")  # seeded into the workspace -> publish pre-check passes
    return t


def _domino_orch(tmp: Path, cp: FakeControlPlane, *, run_id: str | None = None,
                 workspace_id: str | None = None, template: Path | None = None) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=template or _template_with_app_sh(tmp),
        gateway=object(),
        catalog=_catalog(),
        project_id="Sage",
        control_plane=cp,
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
        domino_run_id=run_id,
        workspace_id=workspace_id,
    )
    orch.project(start_preview=False)  # attach + seed the workspace without starting Vite
    return orch


def test_publish_creates_a_new_app(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _domino_orch(tmp_path, cp)
    out = orch.publish()
    assert out["published"] is True
    assert out["republished"] is False
    assert out["app_id"] and out["url"]
    assert out["manage_url"] == f"/u/owner/Sales dashboard/apps/{out['app_id']}/v-{out['app_id']}/details/overview"
    assert cp.app_projects[out["app_id"]] == "proj-1"  # the app is tied to this project


def test_publish_republishes_an_existing_app(tmp_path: Path):
    cp = FakeControlPlane()
    cp.published["app-9"] = PublishedApp(id="app-9", url="https://fake.domino/app/app-9")
    cp.app_projects["app-9"] = "proj-1"  # already published for this project
    orch = _domino_orch(tmp_path, cp)
    out = orch.publish()
    assert out["republished"] is True
    assert out["app_id"] == "app-9"  # targets the existing app, stable URL
    assert out["url"] == "https://fake.domino/app/app-9"


def test_publish_fails_fast_when_app_sh_missing(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _domino_orch(tmp_path, cp, template=_template(tmp_path))  # template has no app.sh
    with pytest.raises(RuntimeError, match="app.sh"):
        orch.publish()
    assert not cp.published  # never reached the control plane


def test_publish_requires_domino(tmp_path: Path):
    orch = _orch(tmp_path)  # no control plane
    with pytest.raises(RuntimeError, match="only available when this builder runs on Domino"):
        orch.publish()


@pytest.mark.parametrize("raw,phase", [
    ("Running", "running"), ("Failed", "failed"), ("Error", "failed"),
    ("Preparing", "pending"), ("", "pending"),
])
def test_publish_status_maps_phase(tmp_path: Path, raw: str, phase: str):
    cp = FakeControlPlane()
    cp.app_statuses["app-1"] = raw
    orch = _domino_orch(tmp_path, cp)
    out = orch.publish_status("app-1")
    assert out == {"app_id": "app-1", "status": raw, "phase": phase}


def test_stop_resolves_workspace_id_from_run_id_and_stops(tmp_path: Path):
    cp = FakeControlPlane()
    ws = cp.create_workspace("proj-1")  # executionId == "run-proj-1"
    orch = _domino_orch(tmp_path, cp, run_id="run-proj-1")
    out = orch.stop()
    assert out["stopped"] is True
    assert out["workspace_id"] == ws["id"]
    assert cp.workspaces["proj-1"][0]["state"] == "Stopped"  # the workspace was stopped


def test_stop_saves_but_reports_when_workspace_id_unknown(tmp_path: Path):
    cp = FakeControlPlane()  # no workspace matching the run id -> id undiscoverable
    orch = _domino_orch(tmp_path, cp, run_id="run-unknown")
    out = orch.stop()
    assert out["stopped"] is False
    assert out["saved"] is True  # work is still saved (best-effort)
    assert "couldn't stop" in out["detail"]


def test_stop_uses_explicit_workspace_id_override(tmp_path: Path):
    cp = FakeControlPlane()
    cp.workspaces["proj-1"] = [{"id": "ws-42"}]
    orch = _domino_orch(tmp_path, cp, workspace_id="ws-42")
    out = orch.stop()
    assert out["stopped"] is True
    assert out["workspace_id"] == "ws-42"
    assert cp.workspaces["proj-1"][0]["state"] == "Stopped"


def test_record_runtime_error_stores_stamped_error(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.record_runtime_error("boom", "at App (App.tsx:3)")
    orch.project(start_preview=False)
    orch.record_runtime_error("d.getFullYear is not a function", "synthetic.ts:239")
    rt = orch._project.runtime_error
    assert rt["message"] == "d.getFullYear is not a function"
    assert rt["stack"] == "synthetic.ts:239"
    assert isinstance(rt["ts"], float)


def test_record_runtime_error_before_attach_is_dropped(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.record_runtime_error("boom", "")  # no active project
    assert orch._project is None  # dropped, did not attach


def test_await_runtime_error_only_returns_errors_after_since(tmp_path: Path):
    import time

    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)

    # A stale error (from a prior turn) is ignored: its ts predates `since`.
    orch.record_runtime_error("stale", "")
    since = time.monotonic()
    assert orch._await_runtime_error(project, since=since, timeout=0.2) is None

    # A fresh error (ts >= since) is returned promptly.
    orch.record_runtime_error("fresh crash", "stack")
    rt = orch._await_runtime_error(project, since=since, timeout=1.0)
    assert rt is not None and rt["message"] == "fresh crash"
