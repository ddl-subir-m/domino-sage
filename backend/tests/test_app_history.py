"""The build log belongs to the Built App (ADR-0008, #68).

The stop button's baseline is a POSITION in the log. While a Project was an app that was fine —
one log, one writer. A Project holds many Built Apps now, and two viewers in one Project are two
Sage Builders, so a shared log meant one person's stop could rewind past another person's turns.

The log and its rendering moved into `apps/<appId>/.sage/` with the rest of the app's record, which
is what these tests hold in place: two apps are two logs, a stop rewinds only its own, and the
archive the agent greps is the one standing in its own directory. Each entry also names the app it
belongs to, because one Thread can hand off more than once and a conversation no longer says which
app its turn built.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.manager import Workspace

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport

        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for every routed request — the Chat/Build classifier is its only caller."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _plan(title: str, step: str) -> str:
    return (f"{title}\n\n"
            "## Plan\n"
            f"1. **{step}** — Show it.\n\n"
            "## Open questions\n"
            "None — ready to build.\n")


_DESK = _plan("A desk exposure dashboard.", "Desk table")
_PNL = _plan("A daily P&L report.", "P&L table")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# rules")
    return t


def _orch(tmp: Path, turns: list[Turn] | None = None):
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(
        workspace_dir=root,
        template=_template(tmp),
        gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        feedback=OkFeedback(),
        opencode_client=oc,
    )
    return orch, oc, root


def _app_from_chat(orch: Orchestrator, ask: str) -> tuple[str, str]:
    """One Chat conversation through to a confirmed handoff — which is where an app is born."""
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, ask))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, {"resources": False, "artifacts": False, "transcript": False})
    return orch.project(start_preview=False).workspace.app_id, tid


def _log(root: Path, app_id: str) -> list[dict]:
    path = root / "apps" / app_id / ".sage" / "history.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---- two apps, two logs -------------------------------------------------------------------------

def test_each_built_app_keeps_its_own_log_and_its_own_archive(tmp_path: Path):
    """The whole ticket in one file layout: nothing about the log sits at the volume root, where
    two Sage Builders would be writing to the same file."""
    turns = [Turn(text="A dashboard, then."), Turn(text=_DESK),
             Turn(text="A report, then."), Turn(text=_PNL)]
    orch, _oc, root = _orch(tmp_path, turns)
    first, _ = _app_from_chat(orch, "build me a desk dashboard")
    second, _ = _app_from_chat(orch, "now build me a daily P&L report")

    for app_id in (first, second):
        ws = orch._wm.app_workspace("Sage", app_id)
        assert ws.history_path == root / "apps" / app_id / ".sage" / "history.jsonl"
        assert ws.history_md_path == root / "apps" / app_id / ".sage" / "history.md"
        assert ws.history_path.is_file()          # the confirmed handoff wrote its plan card here
    assert not (root / ".sage" / "history.jsonl").exists()
    assert not (root / ".sage" / "history.md").exists()

    # Each app's plan card is in its own log and in no other.
    assert [r["type"] for r in _log(root, first)] == ["plan-proposed", "done"]
    assert _log(root, first)[0]["plan"].startswith("A desk exposure dashboard.")
    assert _log(root, second)[0]["plan"].startswith("A daily P&L report.")


def test_the_agent_greps_its_own_apps_archive_and_not_another_apps(tmp_path: Path):
    """`AGENTS.md` tells the agent to grep `.sage/history.md` for what was asked before. It stands
    in its own app's directory, so that instruction has to resolve to that app's turns."""
    ws_a = Workspace(project_id="p", path=tmp_path / "apps" / "app_a", app_id="app_a")
    ws_b = Workspace(project_id="p", path=tmp_path / "apps" / "app_b", app_id="app_b")

    ws_a.append_history({"type": "user", "text": "show notional by desk"}, "thr_a")
    ws_b.append_history({"type": "user", "text": "show daily P&L"}, "thr_a")
    ws_a.render_history_md()
    ws_b.render_history_md()

    assert "show notional by desk" in ws_a.history_md_path.read_text()
    assert "show daily P&L" not in ws_a.history_md_path.read_text()
    assert "show daily P&L" in ws_b.history_md_path.read_text()
    assert "show notional by desk" not in ws_b.history_md_path.read_text()


# ---- stop ---------------------------------------------------------------------------------------

def test_a_stop_rewinds_its_own_apps_log_and_leaves_the_other_alone(tmp_path: Path):
    """The bug this ticket closes, at the level it bit: the baseline is a line position, so a stop
    taken against one app's log must never be applied to another's."""
    ws_a = Workspace(project_id="p", path=tmp_path / "apps" / "app_a", app_id="app_a")
    ws_b = Workspace(project_id="p", path=tmp_path / "apps" / "app_b", app_id="app_b")
    for i in range(3):
        ws_a.append_history({"type": "user", "text": f"desk ask {i}"}, "thr_a")

    baseline = ws_b.history_len()               # app B has no turns yet, so this is 0
    ws_b.append_history({"type": "user", "text": "P&L ask"}, "thr_b")
    ws_b.truncate_history(baseline)

    assert ws_b.read_history() == []
    assert [r["text"] for r in ws_a.read_history()] == ["desk ask 0", "desk ask 1", "desk ask 2"]


def test_stopping_a_build_in_one_app_leaves_the_other_apps_transcript_whole(tmp_path: Path):
    """The same thing through a real turn: two confirmed handoffs, a finished build in the first
    app and a stopped one in the second."""
    turns = [Turn(text="A dashboard, then."), Turn(text=_DESK),
             Turn(text="A report, then."), Turn(text=_PNL),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"}),
             Turn(text="Building the P&L table.", writes={"src/App.tsx": "// pnl table\n"})]
    orch, _oc, root = _orch(tmp_path, turns)
    first, first_tid = _app_from_chat(orch, "build me a desk dashboard")
    second, second_tid = _app_from_chat(orch, "now build me a daily P&L report")

    orch.select_app(first)
    orch.project(start_preview=False)
    list(orch.approve_stream(conversation=first_tid))
    built = _log(root, first)
    assert "typecheck" in [r["type"] for r in built]         # the build ran and was recorded

    orch.select_app(second)
    project = orch.project(start_preview=False)
    events = []
    for ev in orch.approve_stream(conversation=second_tid):
        events.append(ev)
        if ev.get("type") == "turn":
            project.stop_requested = True

    assert [e["type"] for e in events if e["type"] == "stopped"] == ["stopped"]
    # The stopped turn is gone from the app it ran in: back to the plan card the handoff wrote.
    assert [r["type"] for r in _log(root, second)] == ["plan-proposed", "done"]
    # And the app nobody stopped still holds every line it held before.
    assert _log(root, first) == built


# ---- what an entry says ------------------------------------------------------------------------

def test_a_log_entry_names_the_app_and_the_conversation_that_produced_it(tmp_path: Path):
    """A Thread can hand off more than once, so `conversation` alone no longer says which app a
    turn built. The entry says it itself rather than leaving it to whichever file it was read from."""
    ws = Workspace(project_id="p", path=tmp_path / "apps" / "app_a", app_id="app_a")

    ws.append_history({"type": "user", "text": "add a filter"}, "thr_a")

    assert ws.read_history("thr_a") == [
        {"type": "user", "text": "add a filter", "conversation": "thr_a", "app": "app_a"}]


def test_an_entry_from_an_unscoped_caller_still_names_its_app(tmp_path: Path):
    """The CLI and the tests build without a rail and so own no conversation. The app is not
    optional in the same way — the directory the entry was written into is the app."""
    ws = Workspace(project_id="p", path=tmp_path / "apps" / "app_a", app_id="app_a")

    ws.append_history({"type": "user", "text": "add a filter"})

    assert ws.read_history() == [{"type": "user", "text": "add a filter", "app": "app_a"}]
    assert ws.has_untagged_history() is True    # no conversation, so the adoption path still sees it


def test_a_turn_written_through_the_orchestrator_carries_both_references(tmp_path: Path):
    turns = [Turn(text="A dashboard, then."), Turn(text=_DESK),
             Turn(text="Built it.", writes={"src/App.tsx": "// desk table\n"})]
    orch, _oc, root = _orch(tmp_path, turns)
    app_id, tid = _app_from_chat(orch, "build me a desk dashboard")

    orch.project(start_preview=False)
    list(orch.approve_stream(conversation=tid))

    rows = _log(root, app_id)
    assert rows
    assert {r.get("app") for r in rows} == {app_id}
    assert {r.get("conversation") for r in rows} == {tid}
