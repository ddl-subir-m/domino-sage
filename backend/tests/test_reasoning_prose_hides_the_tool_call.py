"""Reasoning stays readable. The tool call inside it does not.

The split happens before the browser and before the transcript. A reasoning part is its own
event, the prose around a call is kept, and a part that is only a call produces nothing.
An answer the person asked for as JSON stays. A tool call the model wrote into the answer
as `<tool_call><function=…>` markup does not: the stream holds it and the transcript drops it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator.service import (
    Orchestrator,
    ReasoningFold,
    _AnswerMarkup,
    strip_written_tool_call,
    visible_reasoning,
)

from .fake_opencode import Turn, execution_plan
from .test_chat_turn import ScriptedGateway, StreamingFake, _live, _orch

PROSE = "I'll total the weekly sales."
CALL = '{"name":"read","arguments":{"filePath":"sales.csv"}}'
ANSWER = '{"total": 12}'
SECRET = "should-not-leak"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def test_prose_around_a_tool_call_stays_and_the_call_goes():
    assert visible_reasoning(f"{PROSE}\n\n{CALL}") == PROSE
    assert "filePath" not in visible_reasoning(f"{PROSE}\n\n{CALL}")


def test_a_chunk_that_is_only_a_tool_call_produces_nothing():
    assert visible_reasoning(CALL) == ""
    fenced = f"{PROSE}\n\n```json\n{CALL}\n```"
    assert visible_reasoning(fenced) == PROSE
    wrapped = f"{PROSE}\n\n<tool_call>\n{CALL}\n</tool_call>"
    assert visible_reasoning(wrapped) == PROSE


_WRITTEN_CALL = (
    "<tool_call><function=live_read_query>"
    "<parameter=token>SECRET</parameter>"
    "<parameter=sql>SELECT 1</parameter>"
    "</function></tool_call>"
)


def test_a_tool_call_written_as_function_parameters_is_not_reasoning():
    assert visible_reasoning(f"{PROSE}\n\n{_WRITTEN_CALL}\n\nLet me retry.") == (
        f"{PROSE}\n\nLet me retry.")
    assert visible_reasoning(_WRITTEN_CALL) == ""
    assert "SECRET" not in visible_reasoning(f"{PROSE}\n{_WRITTEN_CALL}")


def test_an_unclosed_function_call_is_not_reasoning():
    open_call = "<tool_call><function=live_read_query><parameter=token>SECRET</parameter>"
    assert visible_reasoning(f"{PROSE}\n{open_call}") == PROSE
    assert "SECRET" not in visible_reasoning(f"{PROSE}\n{open_call}")


def test_a_partial_tool_tag_is_held_until_the_call_closes():
    fold = ReasoningFold()
    assert fold.push("p", f"{PROSE} <tool_ca") == PROSE
    assert "<" not in fold.prose()
    fold.push("p", "ll>" + _WRITTEN_CALL[len("<tool_call>"):] + " After.")
    assert fold.prose() == f"{PROSE}  After."
    assert "SECRET" not in fold.prose()


def test_an_answer_keeps_json_and_drops_a_tool_call_written_into_it():
    assert strip_written_tool_call('{"total": 12}') == '{"total": 12}'
    assert strip_written_tool_call(f"Looking.\n\n{_WRITTEN_CALL}\n\nDone.") == "Looking.\n\nDone."
    gate = _AnswerMarkup()
    assert gate.push("Looking. ") == "Looking. "
    assert gate.push("<tool_ca") == ""
    assert "SECRET" not in gate.shown
    assert gate.push("ll>" + _WRITTEN_CALL[len("<tool_call>"):]) == ""
    assert gate.push(" Done.") == " Done."
    final = gate.finish(f"Looking. {_WRITTEN_CALL} Done.")
    assert final == "Looking.  Done."
    assert "SECRET" not in final
    assert "<tool_call>" not in final


def test_json_that_is_not_a_tool_call_stays():
    assert visible_reasoning(f"The total is {ANSWER}") == f"The total is {ANSWER}"
    bare = '{"filePath":"sales.csv"}'
    assert visible_reasoning(f"{PROSE} {bare}") == f"{PROSE} {bare}"


def test_an_unfinished_call_is_held_until_the_part_closes():
    fold = ReasoningFold()
    assert fold.push("p", f"{PROSE} ") == PROSE
    assert fold.push("p", '{"name":"read","arguments":') is None
    assert "{" not in fold.prose()
    assert fold.replace("p", f"{PROSE}\n\n{CALL}", closed=True) is None
    assert fold.prose() == PROSE
    assert "filePath" not in fold.prose()


def _reasoning_rows(rows):
    return [e for e in rows if e.get("type") == "reasoning"]


def _answer_text(rows):
    return [e.get("text") for e in rows if e.get("type") == "agent" and e.get("kind") == "text"]


def test_chat_shows_the_prose_and_keeps_the_call_out_of_the_thread(tmp_path: Path):
    orch, _oc = _orch(tmp_path, [Turn(reasoning=f"{PROSE}\n\n{CALL}", text=ANSWER)])
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "total the weekly sales"))
    history = orch.thread_history(tid)
    for rows in (events, history):
        shown = _reasoning_rows(rows)
        assert [e["text"] for e in shown] == [PROSE]
        assert _answer_text(rows) == [ANSWER]
        blob = json.dumps(rows)
        assert "filePath" not in blob
        assert SECRET not in blob
    kinds = [e["type"] for e in history]
    assert kinds.index("reasoning") < kinds.index("agent")


def test_chat_streams_the_prose_and_not_a_tool_call_written_into_the_answer(tmp_path: Path):
    body = f"Looking.\n\n{_WRITTEN_CALL}\n\nDone."
    events = [
        _live("message", delta="Looking.\n\n", final=False),
        _live("message", delta="<tool_ca", final=False),
        _live("message", delta="ll>" + _WRITTEN_CALL[len("<tool_call>"):] + "\n\n", final=False),
        _live("message", delta="Done.", final=False),
        _live("message", text=body, final=True),
        _live("phase", finish="stop"),
    ]

    def client(ws):
        return StreamingFake(ws, [Turn(text=body)], events)

    orch, _oc = _orch(tmp_path, client=client)
    tid = orch.create_thread()["id"]
    events_out = list(orch.chat_stream(tid, "which customers asked for ARM support"))
    history = orch.thread_history(tid)
    for rows in (events_out, history):
        blob = json.dumps(rows)
        assert "SECRET" not in blob
        assert "<tool_call>" not in blob
        assert "parameter=" not in blob
        assert "<function=" not in blob
    assert _answer_text(history) == ["Looking.\n\nDone."]
    streamed = "".join(e["text"] for e in events_out
                       if e.get("type") == "delta" and not e.get("final"))
    assert "SECRET" not in streamed
    assert streamed.strip() == "Looking.\n\nDone."


def test_chat_shows_nothing_when_the_thought_is_only_the_call(tmp_path: Path):
    orch, _oc = _orch(tmp_path, [Turn(reasoning=CALL, text=ANSWER)])
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "total the weekly sales"))
    history = orch.thread_history(tid)
    for rows in (events, history):
        assert _reasoning_rows(rows) == []
        assert _answer_text(rows) == [ANSWER]
        assert "filePath" not in json.dumps(rows)
        assert SECRET not in json.dumps(rows)


def test_build_shows_the_prose_and_does_not_write_the_call_into_the_plan(tmp_path: Path):
    plan = execution_plan()
    orch, _oc = _orch(tmp_path, [Turn(reasoning=f"{PROSE}\n\n{CALL}", text=plan)],
                      gateway=ScriptedGateway("BUILD"))
    events = list(orch.build_stream("build a small dashboard", conversation="conv_reason"))
    history = orch.history("conv_reason")
    for rows in (events, history):
        shown = _reasoning_rows(rows)
        assert [e["text"] for e in shown] == [PROSE]
        assert not any(e.get("kind") == "text" and "filePath" in (e.get("text") or "")
                       for e in rows)
        blob = json.dumps(rows)
        assert "filePath" not in blob
        assert SECRET not in blob
        assert PROSE not in json.dumps(
            [e for e in rows if e.get("type") != "reasoning"])
