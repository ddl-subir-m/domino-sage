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


# --- file attach (Domino datasets) -----------------------------------------------------------

def _dataset(orch: Orchestrator, name: str) -> str:
    return next(a["id"] for a in orch.list_assets() if a["name"] == name)


def test_attach_file_symlinks_live_bytes_into_public_data(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    ds = _dataset(orch, "sales_2026")

    files = orch.list_asset_files(ds)
    assert {f["path"] for f in files} == {"train.csv", "README.md"}
    assert all(not f["attached"] for f in files)

    res = orch.attach_file(ds, "train.csv")
    link = ws / "public" / "data" / "sales_2026" / "train.csv"
    assert res["path"] == "public/data/sales_2026/train.csv"
    assert link.is_symlink() and link.is_file()          # points at the live mount, not a copy
    assert "month,revenue" in link.read_text()
    assert [e["file"] for e in orch.project().attached] == ["train.csv"]
    assert orch.list_asset_files(ds)[0]["attached"] or orch.list_asset_files(ds)[1]["attached"]

    # gitignored + advertised to the agent in AGENTS.md
    assert "public/data/" in (ws / ".gitignore").read_text().split()
    assert "public/data/sales_2026/train.csv" in (ws / "AGENTS.md").read_text()


def test_attaching_sensitive_dataset_file_locks_sovereign(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)  # memoize without Vite
    res = orch.attach_file(_dataset(orch, "customer_pii"), "customers.csv")
    assert res["sensitive"] is True
    assert orch.project().control.locked


def test_detach_removes_symlink_but_keeps_sticky_lock(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    ds = _dataset(orch, "customer_pii")
    orch.attach_file(ds, "customers.csv")
    link = ws / "public" / "data" / "customer_pii" / "customers.csv"
    assert link.is_symlink()

    orch.detach_file("public/data/customer_pii/customers.csv")
    assert not link.exists()
    assert orch.project().attached == []
    assert orch.project().control.locked           # sticky: detach does not unlock
    assert "customer_pii" not in (ws / "AGENTS.md").read_text()


def test_attach_respects_configurable_size_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGE_ATTACH_MAX_BYTES", "10")  # smaller than any sample file
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    from sage.orchestrator.service import AttachTooLarge

    with pytest.raises(AttachTooLarge):
        orch.attach_file(_dataset(orch, "sales_2026"), "train.csv")
    assert orch.project().attached == []           # nothing attached, no symlink left behind
    assert not (orch.project().workspace.path / "public" / "data").exists()


def test_attached_files_rehydrate_from_symlinks_after_restart(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)  # memoize without Vite
    ds = _dataset(orch, "sales_2026")
    orch.attach_file(ds, "train.csv")

    fresh = _orch(tmp_path)                          # new process over the same workspace volume
    attached = fresh.project(start_preview=False).attached
    assert [e["path"] for e in attached] == ["public/data/sales_2026/train.csv"]


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


# --- P6: first-build plan gate (grill + sign-off) --------------------------------------------
from sage.orchestrator.service import _approve_prompt, _should_gate  # noqa: E402
from sage.router.models import Mode  # noqa: E402
from sage.workspace.manager import Workspace  # noqa: E402


def test_should_gate_fires_on_first_build_only():
    assert _should_gate(mode=Mode.IMPLEMENT, history_baseline=0, skip_planning=False) is True
    assert _should_gate(mode=Mode.IMPLEMENT, history_baseline=3, skip_planning=False) is False


def test_should_gate_plan_mode_gates_on_later_turns():
    assert _should_gate(mode=Mode.PLAN, history_baseline=5, skip_planning=False) is True


def test_should_gate_never_gates_ask_mode():
    assert _should_gate(mode=Mode.ASK, history_baseline=0, skip_planning=False) is False


def test_should_gate_opt_out_wins_over_everything():
    assert _should_gate(mode=Mode.PLAN, history_baseline=0, skip_planning=True) is False


def test_approve_prompt_includes_plan_and_answers():
    p = _approve_prompt("## Plan\n1. do it", "cols: id, amount")
    assert "## Approved plan" in p and "1. do it" in p
    assert "cols: id, amount" in p and "## Answers to the open questions" in p


def test_approve_prompt_omits_answers_section_when_blank():
    assert "Open questions" not in _approve_prompt("## Plan\n1. do it", "   ")


def test_archive_plan_moves_plan_out_of_live_view(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path)
    ws.write_plan("build a queue")
    dest = ws.archive_plan()
    assert dest is not None and dest.exists()
    assert ws.read_plan() is None  # no live plan.md remains for a later turn to misread
    assert dest.read_text() == "build a queue"


def test_archive_plan_is_a_noop_without_a_live_plan(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path)
    assert ws.archive_plan() is None


def test_archive_plan_never_clobbers_prior_archives(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path)
    ws.write_plan("first")
    ws.archive_plan()
    ws.write_plan("second")
    ws.archive_plan()
    archived = sorted((tmp_path / ".sage" / "plans").glob("*.md"))
    assert [p.read_text() for p in archived] == ["first", "second"]


def test_settings_roundtrip_and_default_empty(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path)
    assert ws.read_settings() == {}
    ws.write_settings({"skip_planning": True})
    assert ws.read_settings()["skip_planning"] is True
