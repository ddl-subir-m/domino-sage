"""Orchestrator tests (Phase 2) — single bound project, no multi-project surface."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator, _part_key
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
    # The Project is the volume; the Built App is a directory inside it, named for an id (ADR-0008).
    assert project.record.path == tmp_path / "mnt" / "code"
    assert project.workspace.path.parent == tmp_path / "mnt" / "code" / "apps"
    assert project.workspace.path.name == project.workspace.app_id
    assert (project.workspace.path / "package.json").exists()  # seeded from the template


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
    app_id = orch.project(start_preview=False).workspace.app_id
    orch.project(start_preview=False).workspace.append_history({"type": "user", "text": "hi"})
    orch2 = _orch(tmp_path)
    assert orch2.history() == [{"type": "user", "text": "hi", "app": app_id}]


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


def test_detach_removes_symlink_and_clears_the_attachment(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    ds = _dataset(orch, "customer_pii")
    orch.attach_file(ds, "customers.csv")
    link = ws / "public" / "data" / "customer_pii" / "customers.csv"
    assert link.is_symlink()

    orch.detach_file("public/data/customer_pii/customers.csv")
    assert not link.exists()
    assert orch.project().attached == []
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


def test_shutdown_never_saves_into_a_repo_the_workspace_merely_sits_inside(tmp_path: Path):
    """#20: locally the workspace lands at `backend/workspaces/app`, inside Sage's own source tree
    and gitignored, so it is not its own repo. Stop-save walked up to the enclosing one, staged the
    whole tree from a subdirectory, and pushed Sage's uncommitted source to `origin/main`.

    The stop-safe promise covers the app's repo. It does not extend to a repo the workspace happens
    to sit inside, where saving is the opposite of safe: it publishes work nobody reviewed.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "sage@example.com")
    _git(tmp_path, "config", "user.name", "sage")
    source = tmp_path / "sage_source.py"
    source.write_text("committed\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    head = _git(tmp_path, "rev-parse", "HEAD").strip()
    source.write_text("an in-progress edit nobody asked to publish\n")

    orch = _orch(tmp_path)  # workspace is tmp_path/mnt/code — inside the repo just created
    orch.project(start_preview=False)
    orch.shutdown()  # the real _save_to_git, not a stub

    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head
    # And the edit is still an edit — not swept into a commit somewhere else.
    assert "sage_source.py" in _git(tmp_path, "status", "--porcelain")


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
    assert (ws / "mate.txt").read_text() == "from teammate"  # pulled into the volume
    # The template files the attach seeded were committed + pushed alongside the merge. They live
    # in the app's directory, which is what the repo tracks them under.
    assert "apps" in _git(bare, "ls-tree", "--name-only", "HEAD")
    app = orch.project(start_preview=False).workspace
    assert "package.json" in _git(bare, "ls-tree", "--name-only", f"HEAD:apps/{app.app_id}")


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
    orch = _domino_orch(tmp_path, cp)
    # THIS Built App already deployed app-9 — which is a fact about the app, not about the Domino
    # project it shares with every other Built App beside it (#70).
    orch.project(start_preview=False).workspace.record_domino_app("app-9")
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


def test_publish_fails_fast_when_the_server_app_sh_execs_is_missing(tmp_path: Path):
    # app.sh serves the build by exec'ing serve.py (ADR-0002). Deploying without it starts an app
    # that dies on "can't open file 'serve.py'", and Domino reports only that the app failed.
    cp = FakeControlPlane()
    t = _template(tmp_path)
    (t / "app.sh").write_text("#!/bin/bash\nexec python3 serve.py\n")  # …but no serve.py beside it
    orch = _domino_orch(tmp_path, cp, template=t)

    with pytest.raises(RuntimeError, match="serve.py"):
        orch.publish()
    assert not cp.published  # never reached the control plane


def test_publish_allows_an_app_whose_entry_script_does_not_use_the_python_server(tmp_path: Path):
    # An app seeded before the swap still serves with Node. It has no serve.py and does not need one.
    cp = FakeControlPlane()
    t = _template(tmp_path)
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    orch = _domino_orch(tmp_path, cp, template=t)

    assert orch.publish()["published"] is True


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


def test_overlapping_turn_is_refused_not_run(tmp_path: Path):
    # A turn already streaming holds _turn_lock. A second build_stream must refuse (busy) instead of
    # running a concurrent turn — that overlap is what clears the read-only gate mid-flight and makes
    # a gated planner write code, then self-destruct as a "gate violation". gateway is object() and no
    # OpenCode client is wired, so if the turn actually ran it would blow up, not yield a clean refusal.
    orch = _orch(tmp_path)
    assert orch._turn_lock.acquire(blocking=False)  # simulate a turn in flight
    try:
        events = list(orch.build_stream("build me a thing"))
    finally:
        orch._turn_lock.release()
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[-1] == {"type": "done", "ok": False, "decision": "busy"}
    assert "already running" in events[0]["message"]
    # Machine-readable marker: the UI requeues a refused turn instead of showing the user an error
    # they can't act on, so it must not have to string-match the message to recognise a refusal.
    assert events[0]["busy"] is True


def test_turn_busy_tracks_the_turn_lock(tmp_path: Path):
    # The UI polls turn_busy() after its event stream drops, to tell "connection broke, turn still
    # running" from "turn finished". A stale False there is what makes the composer go idle mid-build.
    orch = _orch(tmp_path)
    assert orch.turn_busy() is False
    assert orch._turn_lock.acquire(blocking=False)
    try:
        assert orch.turn_busy() is True
    finally:
        orch._turn_lock.release()
    assert orch.turn_busy() is False


def test_history_is_readable_mid_turn(tmp_path: Path):
    # A page reloaded mid-build can't rejoin the SSE stream — it belongs to the request that started
    # it — so the UI polls the transcript and draws whatever has landed (index.html
    # resumeRunningTurn / replayHistoryTail). That only works while events are flushed one at a
    # time: batch them until the turn ends and a reattached page sits on a spinner showing nothing
    # until the build is over. Read through a SECOND orchestrator so this proves they're on disk.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    assert orch._turn_lock.acquire(blocking=False)  # a turn is now running
    try:
        ws.append_history({"type": "user", "text": "build me a dashboard"})
        ws.append_history({"type": "agent", "kind": "tool", "tool": "write", "detail": "App.tsx"})
        assert orch.turn_busy() is True
        assert [e["type"] for e in _orch(tmp_path).history()] == ["user", "agent"]
    finally:
        orch._turn_lock.release()


def test_approve_is_refused_while_a_turn_streams(tmp_path: Path):
    # Same guard on the approve path: approving mid-turn would overlap two turns on one working tree.
    orch = _orch(tmp_path)
    assert orch._turn_lock.acquire(blocking=False)
    try:
        events = list(orch.approve_stream())
    finally:
        orch._turn_lock.release()
    assert events[-1] == {"type": "done", "ok": False, "decision": "busy"}


def test_turn_lock_is_released_after_a_refusal(tmp_path: Path):
    # A refused turn must not leak the lock — the next turn can still acquire it once the holder frees it.
    orch = _orch(tmp_path)
    orch._turn_lock.acquire()
    list(orch.build_stream("first"))  # refused; must not touch the lock it didn't take
    orch._turn_lock.release()
    assert orch._turn_lock.acquire(blocking=False)  # free again
    orch._turn_lock.release()


class _FakeOC:
    def __init__(self, msgs, boom=False):
        self._msgs = msgs
        self._boom = boom

    def messages(self, sid):
        if self._boom:
            import httpx
            raise httpx.ReadTimeout("boom")
        return self._msgs


def test_seen_baseline_marks_prior_turn_parts_so_they_dont_echo(tmp_path: Path):
    # The prior turn's completed assistant parts must be pre-marked as seen, so a follow-up turn
    # doesn't re-emit them (the "ordering" echo: prior summary reappearing atop the new turn).
    orch = _orch(tmp_path)
    session = [
        {"type": "user", "id": "u1", "content": [{"type": "text", "text": "build it"}]},
        {"type": "assistant", "id": "a1", "content": [{"type": "text"}, {"type": "tool"}]},
    ]
    seen = orch._seen_baseline(_FakeOC(session), "s1")
    assert seen == {("a1", 0), ("a1", 1)}  # both parts of the prior assistant message, none of the user msg


def test_seen_baseline_keys_on_part_id_when_one_is_present(tmp_path: Path):
    # A part's index shifts when an earlier part is dropped or merged between polls, so the same text
    # comes back under a new key and gets emitted twice. Key on the part's own id where OpenCode gives
    # one, so the baseline still recognises it after a reindex.
    orch = _orch(tmp_path)
    session = [{"type": "assistant", "id": "a1",
                "content": [{"type": "tool", "id": "prt_1"}, {"type": "text", "id": "prt_2"}]}]
    assert orch._seen_baseline(_FakeOC(session), "s1") == {("a1", "prt_1"), ("a1", "prt_2")}
    # ...and the text part is still recognised once the pending tool part ahead of it disappears.
    reindexed = {"type": "assistant", "id": "a1", "content": [{"type": "text", "id": "prt_2"}]}
    assert _part_key(reindexed, 0, reindexed["content"][0]) in orch._seen_baseline(_FakeOC(session), "s1")


def test_seen_baseline_is_empty_on_poll_error(tmp_path: Path):
    # Best-effort: a messages() failure must not break the build — worst case is the pre-existing echo.
    orch = _orch(tmp_path)
    assert orch._seen_baseline(_FakeOC([], boom=True), "s1") == set()


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
from sage.orchestrator.service import (
    _approve_prompt,
    _asks_about_a_change,
    _is_answer_only,
    _looks_like_approval,
    _looks_like_change_request,
    _looks_like_question,
    _read_only_reason,
    _should_gate,
    _wants_architecture,
    _wants_plan,
)
from sage.router.models import Mode
from sage.workspace.manager import ProjectRecord, Workspace


def test_looks_like_approval_accepts_a_bare_yes():
    for prompt in ("ok build", "ok", "yes", "go ahead", "build it", "approve", "looks good", "ship it"):
        assert _looks_like_approval(prompt), prompt


def test_looks_like_approval_rejects_anything_carrying_a_request():
    # "ok build a dashboard" is a new build, not approval of the plan on screen.
    for prompt in ("ok build a dashboard", "build a dashboard", "make the table compact", "no", ""):
        assert not _looks_like_approval(prompt), prompt


def test_read_only_reason_names_the_rule_that_withheld_the_edit_tools():
    # Ask is read-only by mode with no token armed, so it must be reported even though nothing on the
    # turn looks armed — that's the case that made an Ask-mode build read as an unexplained failure.
    assert _read_only_reason(mode=Mode.ASK, answer_only=True, gate=False) == "ask"
    # An Ask turn is also answer-only; the mode is the more useful thing to tell the user.
    assert _read_only_reason(mode=Mode.ASK, answer_only=False, gate=False) == "ask"
    assert _read_only_reason(mode=Mode.AUTO, answer_only=True, gate=False) == "question"
    assert _read_only_reason(mode=Mode.PLAN, answer_only=False, gate=True) == "plan"
    # A turn that may actually write reports no reason, so the summary keeps its real diagnostics.
    assert _read_only_reason(mode=Mode.IMPLEMENT, answer_only=False, gate=False) == ""


def test_should_gate_fires_on_first_build_only():
    assert _should_gate(mode=Mode.IMPLEMENT, has_built=False, skip_planning=False) is True
    assert _should_gate(mode=Mode.IMPLEMENT, has_built=True, skip_planning=False) is False


def test_should_gate_plan_mode_gates_even_after_building():
    assert _should_gate(mode=Mode.PLAN, has_built=True, skip_planning=False) is True


def test_should_gate_never_gates_ask_mode():
    assert _should_gate(mode=Mode.ASK, has_built=False, skip_planning=False) is False


def test_should_gate_skip_planning_only_suppresses_the_automatic_gate():
    # skip_planning opts out of the *automatic* first-build gate for Auto/Implement...
    assert _should_gate(mode=Mode.IMPLEMENT, has_built=False, skip_planning=True) is False
    assert _should_gate(mode=Mode.AUTO, has_built=False, skip_planning=True) is False
    # ...but it must NOT override an explicit Plan-mode selection. Plan mode routes to a read-only
    # agent, so if skip_planning suppressed its gate the turn could neither plan nor build — dead-end.
    assert _should_gate(mode=Mode.PLAN, has_built=False, skip_planning=True) is True
    assert _should_gate(mode=Mode.PLAN, has_built=True, skip_planning=True) is True


def test_gate_keys_on_first_build_not_first_turn():
    # A question before the first build must NOT consume the gate: the project is still unbuilt, so the
    # next real build request still gates. This is the "first build, not first turn" behavior.
    assert _should_gate(mode=Mode.AUTO, has_built=False, skip_planning=False, is_question=True) is False
    assert _should_gate(mode=Mode.AUTO, has_built=False, skip_planning=False, is_question=False) is True
    # Plan mode is an explicit ask to plan — a question there still gates.
    assert _should_gate(mode=Mode.PLAN, has_built=False, skip_planning=False, is_question=True) is True
    # Once built, iteration turns don't gate.
    assert _should_gate(mode=Mode.AUTO, has_built=True, skip_planning=False, is_question=False) is False


def test_is_answer_only_covers_ask_mode_and_any_auto_question():
    # Ask mode is always a read-only answer.
    assert _is_answer_only(mode=Mode.ASK, is_question=False, is_approval=False) is True
    # A question in Auto is answered read-only whether or not the app is built (has_built isn't a factor).
    assert _is_answer_only(mode=Mode.AUTO, is_question=True, is_approval=False) is True
    # A build request in Auto builds; an approval is never an answer.
    assert _is_answer_only(mode=Mode.AUTO, is_question=False, is_approval=False) is False
    assert _is_answer_only(mode=Mode.AUTO, is_question=True, is_approval=True) is False
    # A question in Implement is answered too. It used to fall through to the build path, where the
    # implement agent is told "a turn in which you touched no files is a failed turn" — so a question
    # either got built or got reported as `Wrote nothing`. The mode says HOW to do work, not that
    # every prompt is work. Plan still falls through: a build request there is what the gate is for.
    assert _is_answer_only(mode=Mode.IMPLEMENT, is_question=True, is_approval=False) is True
    assert _is_answer_only(mode=Mode.IMPLEMENT, is_question=False, is_approval=False) is False
    assert _is_answer_only(mode=Mode.PLAN, is_question=True, is_approval=False) is False


@pytest.mark.parametrize("prompt", [
    "what colour is the bottom-right quadrant in @over-cap-3.3MB.png?",
    "why does auto mode use a read-only mode",
    "How does the upload flow work?",
    "explain the auth flow",
    "is the dataset already mounted?",
])
def test_looks_like_question_true_for_clear_questions(prompt):
    assert _looks_like_question(prompt) is True


@pytest.mark.parametrize("prompt", [
    "build a ui where a user uploads a file",
    "make me a fraud review dashboard",
    "add a login page",
    "can you build me a dashboard?",          # build verb wins over the '?'
    "give me the ZZ note in @q3-regions.csv",  # ambiguous lead -> conservatively a build (gates)
    "",
])
def test_looks_like_question_false_for_builds_and_ambiguous(prompt):
    assert _looks_like_question(prompt) is False


# An interrogative content clause has no question mark and no interrogative lead, so both rules above
# are blind to it — and the clause these prompts trail ("...then we'll build X") loses them to the
# build-verb veto outright. The first is the live prompt from #29, verbatim: it was classified as a
# build, and the answer ("there is no clickstream table attached yet") was written into the user's
# app instead of said to them.
@pytest.mark.parametrize("prompt", [
    ("explore the clickstream table and tell me what information it has we will then use it to "
     "build a new dashboard"),
    "tell me what columns are in the orders table",
    "show me what the data looks like before we add a chart",
    "describe what state the app keeps",
])
def test_an_info_clause_before_a_build_verb_is_a_question(prompt):
    assert _looks_like_question(prompt) is True
    assert _looks_like_change_request(prompt) is False  # still complementary


# The mirror image, and the reason the rule is about ORDER rather than about the clause. These carry
# the same two fragments in the opposite order, and a rule that only looked for "tell me what" would
# turn every build request that asks for a summary afterwards into a read-only turn that never builds.
@pytest.mark.parametrize("prompt", [
    "build a dashboard and tell me what you did",
    "add a severity filter, then show me what changed",
    "refactor the table and describe what you moved",
])
def test_an_info_clause_after_a_build_verb_is_still_a_build(prompt):
    assert _looks_like_question(prompt) is False
    assert _looks_like_change_request(prompt) is True


# A conversational opener pushed the real lead out of first position, and the lead rules only ever
# looked at word zero. The first prompt is the live one from 2026-08-24, verbatim: it drew a plan card,
# and the approved plan wrote the answer into the user's app as three read-only screens.
@pytest.mark.parametrize("prompt", [
    "ok what data is there in @BigQuery_Demo",
    "so what columns does the orders table have",
    "hey, how does the upload flow work",
    "alright what is in this dataset",
])
def test_a_filler_opener_does_not_hide_the_question(prompt):
    assert _looks_like_question(prompt) is True
    assert _looks_like_change_request(prompt) is False  # still complementary


# Stripping the opener must expose a lead, never invent one. "do" is a question lead but not an
# information lead, so the weak fallback still reads the raw prompt and "ok do that" stays a build —
# the conservative half of the rule is what keeps an ambiguous prompt in the gate.
@pytest.mark.parametrize("prompt", [
    "ok do that",
    "ok build me a dashboard",
    "so add a severity filter",
    "right align the header",
])
def test_a_filler_opener_does_not_invent_a_question(prompt):
    assert _looks_like_question(prompt) is False


# The same blindness sat in front of the plan and architecture cards, which anchor at ^ for the same
# reason: a leading "plan" IS the request.
def test_a_filler_opener_does_not_hide_a_plan_or_architecture_request():
    assert _wants_plan("ok plan this first") is True
    assert _wants_plan("so, show me a step-by-step plan to add auth") is True
    assert _wants_architecture("ok what's the architecture to add a real time queue") is True


def test_approve_prompt_includes_plan_and_answers():
    p = _approve_prompt("## Plan\n1. do it", "cols: id, amount")
    assert "## Approved plan" in p and "1. do it" in p
    assert "cols: id, amount" in p and "## Answers to the open questions" in p


def test_approve_prompt_omits_answers_section_when_blank():
    assert "Open questions" not in _approve_prompt("## Plan\n1. do it", "   ")


def test_approve_prompt_names_chat_handoff_as_background():
    p = _approve_prompt("## Plan\n1. do it", "", handoff_note=(
        "A Chat Thread produced the files under `examples/` and the digest in "
        "`.sage/handoff.md`. The plan is what to build. The digest is background."
    ))
    assert "The plan is what to build" in p
    assert "digest is background" in p


def test_approve_prompt_omits_handoff_when_blank():
    assert "handoff.md" not in _approve_prompt("## Plan\n1. do it", "")


def test_archive_plan_moves_plan_out_of_live_view(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.write_plan("build a queue")
    dest = ws.archive_plan()
    assert dest is not None and dest.exists()
    assert ws.read_plan() is None  # no live plan.md remains for a later turn to misread
    assert dest.read_text() == "build a queue"


def test_archive_plan_is_a_noop_without_a_live_plan(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    assert ws.archive_plan() is None


def test_archive_plan_never_clobbers_prior_archives(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.write_plan("first")
    ws.archive_plan()
    ws.write_plan("second")
    ws.archive_plan()
    archived = sorted((tmp_path / ".sage" / "plans").glob("*.md"))
    assert [p.read_text() for p in archived] == ["first", "second"]


def _turn(ws: Workspace, prompt: str, reply: str) -> None:
    ws.append_history({"type": "user", "text": prompt})
    ws.append_history({"type": "agent", "kind": "text", "text": reply})


def test_history_md_renders_the_decisions_a_later_turn_needs(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    _turn(ws, "keep the date filter", "Added the filter.")
    ws.append_history({"type": "plan-proposed", "plan": "## Plan\n1. stacked chart"})
    ws.render_history_md()
    md = ws.history_md_path.read_text()
    assert "## Turn 1" in md
    assert "keep the date filter" in md and "Added the filter." in md
    assert "1. stacked chart" in md


def test_history_md_drops_tool_trace_noise(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    _turn(ws, "build it", "Done.")
    ws.append_history({"type": "agent", "kind": "tool", "tool": "edit", "detail": "src/App.tsx"})
    ws.append_history({"type": "typecheck", "ok": True})
    ws.render_history_md()
    md = ws.history_md_path.read_text()
    assert "src/App.tsx" not in md and "typecheck" not in md


def test_history_md_self_heals_after_a_stop_truncates_history(tmp_path: Path):
    """The reason the render is a full rewrite: the stop button rewinds history.jsonl, and an
    archive that kept reverted work would feed a later turn something that never happened."""
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    _turn(ws, "first ask", "first reply")
    baseline = ws.history_len()
    _turn(ws, "second ask", "second reply")
    ws.render_history_md()
    assert "second ask" in ws.history_md_path.read_text()
    ws.truncate_history(baseline)
    ws.render_history_md()
    md = ws.history_md_path.read_text()
    assert "first ask" in md and "second ask" not in md


def test_history_md_caps_turns_and_says_that_it_dropped_some(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    for i in range(Workspace._MAX_ARCHIVED_TURNS + 3):
        _turn(ws, f"ask number {i}", f"reply number {i}")
    ws.render_history_md()
    md = ws.history_md_path.read_text()
    assert "ask number 0" not in md  # oldest dropped
    assert "ask number 42" in md  # newest kept
    assert "Turns 1–3" in md  # and the model is told the record is partial


def test_history_md_absent_when_there_is_no_history(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.render_history_md()
    assert not ws.history_md_path.exists()


def test_settings_roundtrip_and_default_empty(tmp_path: Path):
    record = ProjectRecord(project_id="p", path=tmp_path)
    assert record.read_settings() == {}
    record.write_settings({"skip_planning": True})
    assert record.read_settings()["skip_planning"] is True


from sage.orchestrator.service import _tidy_plan


def test_tidy_plan_drops_a_verbatim_repeated_paragraph():
    para = ("The app will let you pick a table, preview rows, and inspect each column with a profile "
            "showing its type, missing-value percentage, and a small distribution chart.")
    plan = f"{para}\n\n## Plan\n1. **Table picker** — one dropdown.\n\n{para}"
    tidied = _tidy_plan(plan)
    assert tidied.count(para) == 1
    assert "## Plan" in tidied and "**Table picker**" in tidied


def test_tidy_plan_keeps_short_repeats_and_order():
    plan = "## Plan\n1. **A** — one.\n\n- None\n\n## Open questions\n- Which columns?"
    assert _tidy_plan(plan) == plan


def test_tidy_plan_drops_an_open_questions_section_that_asks_nothing():
    plan = "An explorer.\n\n## Plan\n1. **A** — one.\n\n## Open questions\nNone — ready to build."
    tidied = _tidy_plan(plan)
    assert "Open questions" not in tidied and "None" not in tidied
    assert tidied.endswith("1. **A** — one.")


def test_tidy_plan_keeps_real_open_questions():
    plan = "An explorer.\n\n## Plan\n1. **A** — one.\n\n## Open questions\n- Which columns matter?"
    assert _tidy_plan(plan) == plan


def test_tidy_plan_drops_i_will_openers_from_steps():
    plan = ("## Plan\n"
            "1. **Shape the data** — I will define a realistic synthetic dataset.\n"
            "2. **Design the layout** — I'll create a clear two-panel view.\n"
            "3. **Build the table** — We are going to add a truncation-safe preview.\n"
            "- I will render compact per-column charts.\n")
    tidied = _tidy_plan(plan)
    assert "I will" not in tidied and "I'll" not in tidied and "We are going to" not in tidied
    assert "**Shape the data** — Define a realistic synthetic dataset." in tidied
    assert "**Design the layout** — Create a clear two-panel view." in tidied
    assert "**Build the table** — Add a truncation-safe preview." in tidied
    assert "- Render compact per-column charts." in tidied


def test_tidy_plan_leaves_mid_sentence_first_person_alone():
    plan = "## Plan\n1. **Ask first** — the app asks what I will explore before loading."
    assert _tidy_plan(plan) == plan


@pytest.mark.parametrize("prior", [Mode.ASK, Mode.PLAN])
def test_approve_builds_in_implement_mode_from_a_read_only_mode(tmp_path: Path, prior: Mode):
    # Approving means "build it now". Ask and Plan are both read-only — in Ask the shim strips every
    # write and shell tool from the request, so the approved build emitted edits that never landed on
    # disk ("wrote nothing") and then looped until it gave up. The approve turn must RUN as Implement
    # — pinned for that turn only (mode=), never by moving the user's own picker.
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.write_plan("## Plan\n1. Add a filter.")
    control = project.control
    control.set_mode(prior)
    seen = []
    orch._build_stream = lambda *a, **k: (seen.append(k.get("mode")), iter([]))[1]  # type: ignore[method-assign]
    list(orch.approve_stream())
    assert seen == [Mode.IMPLEMENT]
    assert control.snapshot().mode is prior  # the user's mode was never touched to get there


def test_the_approve_bubble_is_what_the_user_did_not_what_we_sent(tmp_path: Path):
    # _build_stream falls back to the prompt when no user_text is given, and the Approve button gives
    # none — so the whole approve prompt (the plan, then the Chat handoff digest) was landing in the
    # transcript as if the user had typed it. Live, that was the repeated block of handoff plumbing
    # a reader saw in their own chat bubble.
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.write_plan("## Plan\n1. Add a filter.")
    seen = []
    orch._build_stream = lambda *a, **k: (seen.append(k.get("user_text")), iter([]))[1]  # type: ignore[method-assign]
    list(orch.approve_stream())
    assert seen == ["Approved the plan."]


def test_a_typed_approval_keeps_the_words_the_user_typed(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.write_plan("## Plan\n1. Add a filter.")
    seen = []
    orch._build_stream = lambda *a, **k: (seen.append(k.get("user_text")), iter([]))[1]  # type: ignore[method-assign]
    list(orch._approve_locked(user_text="yes go ahead"))
    assert seen == ["yes go ahead"]


def test_approve_from_ask_warns_the_mode_is_still_read_only(tmp_path: Path):
    # Approving from Ask builds, then hands back a read-only composer. The user has just watched Ask
    # write an app, so the next change they type looks like it will build too — say otherwise.
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.write_plan("## Plan\n1. Add a filter.")
    project.control.set_mode(Mode.ASK)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    kinds = [e["type"] for e in orch.approve_stream()]
    assert "ask-active" in kinds
    assert any(e["type"] == "ask-active" for e in project.workspace.read_history())  # survives reload


def test_approve_from_a_building_mode_says_nothing_about_ask(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.write_plan("## Plan\n1. Add a filter.")
    project.control.set_mode(Mode.PLAN)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    assert not any(e["type"] == "ask-active" for e in orch.approve_stream())


def test_approve_with_no_live_plan_refuses_instead_of_building_an_empty_one(tmp_path: Path):
    # A plan is a one-shot handoff: approving archives it. A second click on the card that already
    # built used to send the agent an approve prompt with nothing under the "## Approved plan"
    # heading, and it answered "there isn't a real change described in that approved plan yet" —
    # a turn of inference, and a plan card in the transcript nobody can act on. Observed live
    # 2026-08-24. The chat-approval path in build_stream has always required a non-empty plan.
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.write_plan("## Plan\n1. Add a filter.")
    built = []
    orch._build_stream = lambda *a, **k: (built.append(1), iter([]))[1]  # type: ignore[method-assign]
    list(orch.approve_stream())          # first approve consumes and archives the plan
    assert built == [1]
    events = list(orch.approve_stream())  # second approve has nothing left to build
    assert built == [1]                   # no second build turn
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[-1]["decision"] == "no plan to approve"


@pytest.mark.parametrize("prompt", [
    "remove @synthetic_adverse_events.csv from the UI",
    "make the table compact",
    "add a filter for severity",
    "can you remove the dataset?",  # a build verb wins over the '?', as in _looks_like_question
])
def test_looks_like_change_request_true_for_asked_for_changes(prompt):
    assert _looks_like_change_request(prompt) is True
    assert _looks_like_question(prompt) is False  # the two never both claim a prompt


@pytest.mark.parametrize("prompt", [
    "what tech stack will you use?",
    "how does the upload flow work",
    "",
])
def test_looks_like_change_request_false_for_questions(prompt):
    assert _looks_like_change_request(prompt) is False


# A design question names the change it is about, so "a build verb anywhere wins" refused it: Ask
# mode turned away "give me an architecture to add a real time queue" — the single most natural
# thing to type into a read-only mode. After an informational lead the build verb is in a
# subordinate clause describing hypothetical work, not an instruction to do it.
@pytest.mark.parametrize("prompt", [
    "give me an architecture to add a real time queue that shows data upload progress",
    "how would you add a real time upload queue?",
    "what's the best way to add caching here",
    "how do I fix the race in the preview loop",
    "explain how to remove the dataset from the UI",
    "why would we build it that way",
    "walk me through the approach for adding a severity filter",
    "compare the options for implementing pagination",
])
def test_a_design_question_is_answered_not_refused(prompt):
    assert _looks_like_change_request(prompt) is False
    assert _looks_like_question(prompt) is True  # still complementary


@pytest.mark.parametrize("prompt", [
    "can you remove the dataset?",  # a modal is a polite imperative, not a request for information
    "would you add a filter for severity",
    "please update the header copy",
])
def test_a_politely_worded_change_is_still_a_change(prompt):
    assert _looks_like_change_request(prompt) is True
    assert _looks_like_question(prompt) is False


@pytest.mark.parametrize("prompt", [
    "give me a dashboard for adverse events",  # ambiguous lead, and the noun is a thing to build
    "show me a fraud review UI",
])
def test_an_ambiguous_lead_needs_an_information_noun(prompt):
    # "give"/"show" open both a question and a build, so _INFO_ASK only fires on what follows.
    # These two stay outside it and keep falling through to the plan gate, as they did before.
    assert _asks_about_a_change(prompt) is False


@pytest.mark.parametrize("prompt", [
    "give me an architecture to add a real time queue that shows data upload progress",
    "how would you design the data flow for uploads?",
    "what's the architecture for the export feature we want to build",
])
def test_wants_architecture_needs_a_named_artifact_and_unbuilt_work(prompt):
    assert _wants_architecture(prompt) is True


@pytest.mark.parametrize("prompt", [
    "how would you add a real time upload queue?",  # a design question, but no artifact named
    "add an architecture diagram screen to the app",  # imperative: build it, don't describe it
    "",
])
def test_wants_architecture_false_without_both_halves(prompt):
    assert _wants_architecture(prompt) is False


# A question about how something ALREADY works must not reach the heavy deliverable — offering to
# "Build this" in reply to "how does the upload flow work" is nonsense. These get an ordinary
# answer-only turn, which may draw a diagram; that judgement is the model's, not a keyword's.
@pytest.mark.parametrize("prompt", [
    "explain the architecture of the upload pipeline",
    "what's the architecture for a live upload queue",  # names no work to do -> the lighter shape
    "how does data get from the upload to the table?",
    "walk me through the request lifecycle",
])
def test_a_question_about_existing_code_gets_no_architecture_card(prompt):
    assert _wants_architecture(prompt) is False
    assert _is_answer_only(mode=Mode.ASK, is_question=True, is_approval=False) is True


@pytest.mark.parametrize("prompt", [
    "plan this first",
    "plan it out before you build anything",
    "just plan the auth flow",
    "please plan how we'd add multi tenant orgs",
    "Plan the migration",                                   # leading capital
    "show me a plan to add a training pipeline",             # informational shape
    "give me a plan to add scheduled retraining",
    "give me a step-by-step plan to add scheduled retraining",   # modifier before the noun
    "give me a rough high-level plan to add multi tenant orgs",  # two stacked modifiers
    "draft a plan to add scheduled retraining",                  # no indirect object
    "outline a step-by-step plan to add auth",
])
def test_wants_plan_accepts_the_imperative_and_the_informational_ask(prompt):
    assert _wants_plan(prompt) is True


@pytest.mark.parametrize("prompt", [
    "planning to use postgres for this",   # "plan" only as a prefix — no word boundary
    "the plan we discussed is fine",       # mid-sentence noun, not a request
    "what's the plan for the upload flow", # names no work to do -> answered, not re-planned
    "give me an approach for caching",     # _INFO_ASK noun, but not the plan artifact
    "add a roadmap page to the app",       # imperative build, not a request to plan
    "",
])
def test_wants_plan_stays_out_of_the_ambiguous_middle(prompt):
    assert _wants_plan(prompt) is False


def test_artifact_modifiers_stay_curated_so_a_noun_phrase_cant_reach_the_artifact_noun():
    # The modifier slot in _INFO_ASK is a fixed adjective list, not `[a-z]+`. A wildcard would let any
    # noun phrase land on an artifact noun, and _asks_about_a_change is what stops Ask mode refusing a
    # change request — so an over-wide match here turns a real build request into a prose answer.
    assert _asks_about_a_change("give me a high-level plan to add auth") is True
    assert _asks_about_a_change("show me the dataset upload design") is False
    assert _asks_about_a_change("show me the settings page design") is False
    # Three stacked modifiers exceed the cap and fall through, which is the safe direction.
    assert _asks_about_a_change("give me a quick rough high-level plan to add auth") is False


def test_an_informational_ask_needs_no_indirect_object():
    # "propose me an approach" is not something anyone types, so requiring "me"/"us" left half the
    # verb list unreachable. The artifact noun still carries the decision.
    assert _asks_about_a_change("propose an approach for caching") is True
    assert _asks_about_a_change("sketch the architecture") is True
    assert _asks_about_a_change("walk through the design") is True
    assert _asks_about_a_change("recommend a strategy") is True
    # Still a build: the verb alone never decides, and "dashboard" is not a deliverable made of words.
    assert _asks_about_a_change("show a dashboard of upload failures") is False
    assert _asks_about_a_change("give the app a settings page") is False


PLAN_PROMPT = "show me a plan to add a training pipeline"


@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.IMPLEMENT, Mode.AUTO, Mode.ASK])
def test_an_explicit_plan_request_gates_in_every_mode(mode: Mode):
    # Typing "show me a plan" is the same instruction as picking Plan mode, so it must reach the plan
    # card in every mode — including on a built project, where _should_gate otherwise never fires
    # again. This is the routing _build_stream applies.
    wants_plan = _wants_plan(PLAN_PROMPT)
    assert wants_plan is True
    gate = _should_gate(mode=mode, has_built=True, skip_planning=False,
                        is_question=_looks_like_question(PLAN_PROMPT), wants_plan=wants_plan)
    assert gate is True
    assert _read_only_reason(mode=mode, answer_only=False, gate=gate) == (
        "ask" if mode is Mode.ASK else "plan")


def test_an_explicit_plan_request_is_not_answered_in_prose():
    # The bug: "show me a plan to add auth" matches _INFO_ASK, so _looks_like_question calls it a
    # question and the turn answered in prose — the plan card the user asked for is exactly what the
    # gate already produces, and it never reached it.
    assert _looks_like_question(PLAN_PROMPT) is True
    assert _is_answer_only(mode=Mode.AUTO, is_question=True, is_approval=False,
                           wants_plan=True) is False


def test_an_explicit_plan_request_outranks_skip_planning():
    # skip_planning opts out of the *automatic* first-build gate. Asking for a plan outright is not
    # automatic, so it must survive the opt-out for the same reason Plan mode does.
    assert _should_gate(mode=Mode.AUTO, has_built=True, skip_planning=True, wants_plan=True) is True


def test_approval_still_wins_over_a_plan_request():
    # "ok build" while a plan is pending must run the approved plan, never re-propose one. _build_stream
    # zeroes wants_plan on an approval; the vocabularies don't overlap either, which is the backstop.
    for prompt in ("ok build", "go ahead", "approve"):
        assert _looks_like_approval(prompt) is True
        assert _wants_plan(prompt) is False


ARCH_PROMPT = "give me an architecture to add a real time queue that shows data upload progress"


@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.IMPLEMENT, Mode.AUTO, Mode.ASK])
def test_an_architecture_request_is_neither_built_nor_planned(mode: Mode):
    # The reported bug: asked for an architecture in Plan mode the user got a ten-step build plan,
    # and switching to Implement built the feature. This is the routing _build_stream applies —
    # `arch` overrides the mode, forces the gate, and suppresses answer-only. Ask is included: it's
    # where a design question is most naturally typed, and prose is the wrong shape for a document.
    arch = _wants_architecture(ARCH_PROMPT)
    assert arch is True
    gate = arch or _should_gate(mode=mode, has_built=True, skip_planning=False,
                                is_question=_looks_like_question(ARCH_PROMPT))
    assert gate is True   # never reaches the build path, in any mode
    assert _is_answer_only(mode=mode, is_question=True, is_approval=False, arch=arch) is False
    assert _read_only_reason(mode=mode, answer_only=False, gate=gate, arch=arch) == "architecture"


def test_ask_mode_still_answers_an_ordinary_question_in_prose():
    # Only a named artifact gets the card; Ask's contract is otherwise unchanged.
    assert _wants_architecture("how does the upload flow work") is False
    assert _is_answer_only(mode=Mode.ASK, is_question=True, is_approval=False) is True
    assert _should_gate(mode=Mode.ASK, has_built=True, skip_planning=False, is_question=True) is False


def test_an_approval_is_never_rerouted_to_an_architecture():
    # "yes, build it" after an architecture card must build, not re-describe the design forever.
    assert _is_answer_only(mode=Mode.PLAN, is_question=True, is_approval=True, arch=True) is False


def test_the_architecture_artifact_survives_a_build(tmp_path: Path):
    # plan.md is a one-shot handoff that archive_plan() moves aside the moment a build consumes it.
    # A design the user asked to keep reading must not disappear the same way.
    ws = _orch(tmp_path).project(start_preview=False).workspace
    ws.write_architecture("## Components\n- **Queue** — holds jobs.")
    ws.write_plan("## Plan\n1. **Do it** — now.")
    ws.archive_plan()
    assert ws.read_plan() is None
    assert "Queue" in (ws.read_architecture() or "")


def test_approve_falls_back_to_the_architecture_when_no_plan_is_live(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.write_architecture("## Components\n- **Queue** — holds jobs.")
    seen = []
    orch._build_stream = lambda p, *a, **k: (seen.append(p), iter([]))[1]  # type: ignore[method-assign]
    list(orch.approve_stream())
    assert seen and "Queue" in seen[0]  # the design reached the build, not an empty plan


def test_a_question_in_implement_mode_answers_instead_of_building(tmp_path: Path):
    # sage-implement is told "a turn in which you touched no files is a failed turn", so a question
    # asked there either built something nobody wanted or was reported as `Wrote nothing`.
    assert _is_answer_only(mode=Mode.IMPLEMENT, is_question=True, is_approval=False) is True
    assert _is_answer_only(mode=Mode.IMPLEMENT, is_question=False, is_approval=False) is False
    # An architecture request is never answer-only — it has its own artifact, and the gate needs it.
    assert _is_answer_only(mode=Mode.IMPLEMENT, is_question=True, is_approval=False, arch=True) is False


def test_ask_mode_answers_a_design_question(tmp_path: Path):
    # An architecture request in Ask must reach the turn, not the change-request refusal — the turn
    # is read-only either way, and it's the mode this question is most naturally typed into.
    orch = _orch(tmp_path)
    orch.project(start_preview=False).control.set_mode(Mode.ASK)
    ran = []
    orch._build_stream = lambda *a, **k: (ran.append(a), iter([]))[1]  # type: ignore[method-assign]
    events = list(orch.build_stream("give me an architecture to add a real time upload queue"))
    assert ran and not [e for e in events if e["type"] == "ask-blocked"]


def test_ask_mode_refuses_a_change_request_before_running_the_turn(tmp_path: Path):
    # The bug: Ask strips every write tool, so a change request typed in Ask spent minutes reading and
    # grepping for an edit it could never make, and only reported "wrote nothing" at the end. Refuse
    # up front — no session, no inference — and hand back the prompt so the UI can offer to build it.
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.ASK)
    ran = []
    orch._build_stream = lambda *a, **k: (ran.append(a), iter([]))[1]  # type: ignore[method-assign]
    events = list(orch.build_stream("remove @synthetic_adverse_events.csv from the UI"))
    assert ran == []  # the turn never started
    blocked = next(e for e in events if e["type"] == "ask-blocked")
    assert blocked["prompt"] == "remove @synthetic_adverse_events.csv from the UI"
    assert events[-1] == {"type": "done", "ok": False, "decision": "ask mode (read-only)"}
    # The transcript must replay as a real turn: the user's words, then why nothing happened.
    kinds = [e["type"] for e in project.workspace.read_history()]
    assert kinds[-3:] == ["user", "ask-blocked", "done"]


def test_ask_mode_still_answers_a_question(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False).control.set_mode(Mode.ASK)
    ran = []
    orch._build_stream = lambda *a, **k: (ran.append(a), iter([]))[1]  # type: ignore[method-assign]
    events = list(orch.build_stream("what tech stack will you use?"))
    assert ran and not [e for e in events if e["type"] == "ask-blocked"]


def test_a_change_request_outside_ask_mode_builds_normally(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False).control.set_mode(Mode.AUTO)
    ran = []
    orch._build_stream = lambda *a, **k: (ran.append(a), iter([]))[1]  # type: ignore[method-assign]
    assert not [e for e in orch.build_stream("remove the dataset from the UI") if e["type"] == "ask-blocked"]
    assert ran


def test_status_reports_the_pinned_mode_and_the_users_own_choice_separately(tmp_path: Path):
    """`mode` is what routes right now, `selected_mode` is where the picker sits. The UI needs both:
    rendering the picker from the pinned mode snaps a mid-turn pick back and reads as a dropped click."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    token = project.control.arm_turn_mode(Mode.IMPLEMENT)
    project.control.set_mode(Mode.ASK)

    m = project.status()["model"]
    assert m["mode"] == "implement" and m["selected_mode"] == "ask"

    project.control.disarm_turn_mode(token)
    m = project.status()["model"]
    assert m["mode"] == "ask" and m["selected_mode"] == "ask"


def test_chat_pick_is_a_standing_choice_on_status(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.set_chat_pick("gpt-5.4", "medium")
    m = orch.project(start_preview=False).status()["model"]
    assert m["chat_model"] == "gpt-5.4"
    assert m["reasoning_effort"] == "medium"
    orch.set_chat_pick("auto", None)
    m = orch.project(start_preview=False).status()["model"]
    assert m["chat_model"] is None
    assert m["reasoning_effort"] is None


def test_chat_pick_rejects_unknown_embeddings_and_invalid_effort(tmp_path: Path):
    orch = _orch(tmp_path)
    with pytest.raises(ValueError, match="unknown model"):
        orch.set_chat_pick("not-a-model", None)
    with pytest.raises(ValueError, match="not a chat model"):
        orch.set_chat_pick("text-embedding-3-small", None)
    with pytest.raises(ValueError, match="invalid reasoning_effort"):
        orch.set_chat_pick("gpt-5.4", "ludicrous")
    with pytest.raises(ValueError, match="invalid reasoning_effort"):
        orch.set_chat_pick("sonnet", "high")
