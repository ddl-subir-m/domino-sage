"""Chat OpenCode compaction: threshold policy + post-turn wiring."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import chat_compact
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog, Phase, SessionState

from .fake_opencode import Turn
from .test_chat_turn import _orch


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    from sage.orchestrator import handoff
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _asst(n: int = 1, tokens: dict | None = None, **extra) -> dict:
    m = {"id": f"a{n}", "type": "assistant", "content": [{"type": "text", "text": "ok"}]}
    if tokens is not None:
        m["tokens"] = tokens
    m.update(extra)
    return m


def test_context_limits_match_opencode_json():
    root = Path(__file__).resolve().parents[2]
    cfg = json.loads((root / "opencode.json").read_text())
    models = cfg["provider"]["sage-gateway"]["models"]
    for name, spec in models.items():
        assert chat_compact.context_limit(name) == spec["limit"]["context"]


def test_should_compact_uses_tokens_when_present():
    limit = chat_compact.context_limit("a")  # unknown alias → default 128k
    over = int(limit * chat_compact.TOKEN_RATIO) + 1
    assert chat_compact.should_compact([_asst(tokens={"input": over, "output": 0})], "a")
    assert not chat_compact.should_compact(
        [_asst(tokens={"input": over // 2, "output": 0})], "a")


def test_should_compact_falls_back_to_turns_without_tokens():
    short = [_asst(i) for i in range(chat_compact.TURN_FALLBACK - 1)]
    assert not chat_compact.should_compact(short, "a")
    long = [_asst(i) for i in range(chat_compact.TURN_FALLBACK)]
    assert chat_compact.should_compact(long, "a")


def test_turns_reset_after_a_compaction_marker():
    msgs = [_asst(i) for i in range(chat_compact.TURN_FALLBACK)]
    msgs.append({"id": "c", "type": "user", "content": [{"type": "compaction"}]})
    msgs.append({"id": "cs", "type": "assistant", "summary": True, "content": []})
    msgs.append(_asst(99))
    assert not chat_compact.should_compact(msgs, "a")


def test_old_token_counts_before_compact_do_not_retrigger():
    over = 200_000
    msgs = [
        _asst(1, tokens={"input": over, "output": 0}),
        {"id": "c", "type": "user", "content": [{"type": "compaction"}]},
        {"id": "cs", "type": "assistant", "summary": True, "content": []},
        _asst(2, tokens={"input": 100, "output": 10}),
    ]
    assert not chat_compact.should_compact(msgs, "a")


def test_v2_message_shape_tokens_and_compaction():
    over = int(chat_compact.DEFAULT_CONTEXT * chat_compact.TOKEN_RATIO) + 1
    msgs = [{
        "info": {"role": "assistant", "tokens": {"input": over, "output": 1, "reasoning": 0,
                                                "cache": {"read": 0, "write": 0}}},
        "parts": [{"type": "text", "text": "ok"}],
    }]
    assert chat_compact.should_compact(msgs, "unknown")


def test_compact_model_is_the_chat_pick():
    catalog = ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                           plan="p", implement="i", ask="ask-default")
    state = SessionState(mode=Mode.ASK, phase=Phase.PLAN, chat_thread_id="thr_1",
                         chat_model="sonnet")
    assert chat_compact.compact_model(state, catalog) == ("sage-gateway", "sonnet")
    state = SessionState(mode=Mode.ASK, phase=Phase.PLAN, chat_thread_id="thr_1")
    assert chat_compact.compact_model(state, catalog) == ("sage-gateway", "ask-default")


def test_one_chat_turn_does_not_compact(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "hi"))
    assert oc.compacts == []
    assert next(e for e in events if e["type"] == "done")["ok"] is True


def test_long_thread_compacts_opencode_not_ui_history(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(chat_compact, "TURN_FALLBACK", 2)
    orch, oc = _orch(tmp_path, [Turn(text="one"), Turn(text="two")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "first"))
    assert oc.compacts == []
    events = list(orch.chat_stream(tid, "second"))
    assert len(oc.compacts) == 1
    assert oc.compacts[0]["modelID"] == "a"  # catalog.ask
    assert oc.compacts[0]["providerID"] == "sage-gateway"
    assert oc.compacts[0]["auto"] is False
    hist = orch.thread_history(tid)
    assert [e["text"] for e in hist if e["type"] == "user"] == ["first", "second"]
    assert next(e for e in events if e["type"] == "done")["ok"] is True
    assert not any(e.get("type") == "compact" for e in hist)


def test_token_threshold_compacts_on_the_first_over_limit_turn(tmp_path: Path):
    over = int(chat_compact.DEFAULT_CONTEXT * chat_compact.TOKEN_RATIO) + 1
    orch, oc = _orch(tmp_path, [Turn(text="ok", tokens={"input": over, "output": 8})])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))
    assert len(oc.compacts) == 1
    assert len(orch.thread_history(tid)) >= 2  # user + done at least


def test_compact_uses_the_thread_chat_model(tmp_path: Path):
    over = int(chat_compact.context_limit("sonnet") * chat_compact.TOKEN_RATIO) + 1
    orch, oc = _orch(tmp_path, [Turn(text="ok", tokens={"input": over, "output": 1})])
    orch.project(start_preview=False).control.pick_chat("sonnet")
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))
    assert oc.compacts[0]["modelID"] == "sonnet"


def test_compact_error_does_not_fail_the_turn(tmp_path: Path):
    over = int(chat_compact.DEFAULT_CONTEXT * chat_compact.TOKEN_RATIO) + 1
    orch, oc = _orch(tmp_path, [Turn(text="still answered", tokens={"input": over, "output": 1})])
    oc.compact_error = RuntimeError("summarize 500")
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "hi"))
    assert next(e for e in events if e["type"] == "done") == {
        "type": "done", "ok": True, "decision": "answered",
    }
    assert oc.compacts == []
    hist = orch.thread_history(tid)
    assert hist[0]["text"] == "hi"
    assert any(e.get("kind") == "text" and "still answered" in e.get("text", "") for e in hist)


def test_does_not_recompact_the_next_short_turn(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(chat_compact, "TURN_FALLBACK", 2)
    orch, oc = _orch(tmp_path, [Turn(text="a"), Turn(text="b"), Turn(text="c")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "one"))
    list(orch.chat_stream(tid, "two"))
    assert len(oc.compacts) == 1
    list(orch.chat_stream(tid, "three"))
    assert len(oc.compacts) == 1


def test_compaction_leaves_a_session_the_next_turn_has_taken(tmp_path: Path):
    """Compaction is aftercare: it runs after `done`, off the turn lock, so it takes the lock back
    before it rewrites anything. Summarising a session a live turn is prompting pulls that turn's
    context out from under it, and wait_for_idle would then sit out the whole turn."""
    over = int(chat_compact.DEFAULT_CONTEXT * chat_compact.TOKEN_RATIO) + 1
    orch, oc = _orch(tmp_path, [Turn(text="ok", tokens={"input": over, "output": 1})])
    tid = orch.create_thread()["id"]

    def next_turn_gets_there_first(*_a, **_k):
        orch._turn_lock.acquire()

    orch._maybe_suggest_handoff = next_turn_gets_there_first
    try:
        events = list(orch.chat_stream(tid, "hi"))
    finally:
        del orch._maybe_suggest_handoff
        orch._turn_lock.release()

    assert oc.compacts == []
    assert next(e for e in events if e["type"] == "done")["ok"] is True

    # Deferred, not dropped: the trigger is a context size, so the next turn ends over it too.
    orch._cancel_chat_idle_save()
    oc.turns.append(Turn(text="still ok", tokens={"input": over, "output": 1}))
    list(orch.chat_stream(tid, "again"))
    assert len(oc.compacts) == 1
