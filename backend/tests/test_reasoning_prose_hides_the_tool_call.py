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
    ReasoningNarration,
    _AnswerMarkup,
    narration_sentence,
    strip_written_tool_call,
    visible_reasoning,
)

from .fake_opencode import FakeOpenCode, Turn, execution_plan
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
    fold = ReasoningNarration()
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
    fold = ReasoningNarration()
    assert fold.push("p", f"{PROSE} ") == PROSE
    assert fold.push("p", '{"name":"read","arguments":') is None
    assert "{" not in fold.prose()
    assert fold.replace("p", f"{PROSE}\n\n{CALL}", closed=True) is None
    assert fold.prose() == PROSE
    assert "filePath" not in fold.prose()


@pytest.mark.parametrize("prose, shown", [
    # The latest plain sentence wins.
    ("I'll read the file. Then I'll total the weekly sales.", "Then I'll total the weekly sales."),
    # A sentence naming a path, an identifier or code is passed over for the one before it.
    ("I'll total the weekly sales. Reading examples/thr_1/sales.csv now.",
     "I'll total the weekly sales."),
    ("I'll total the weekly sales. The column is weekly_total.", "I'll total the weekly sales."),
    ("I'll total the weekly sales.\n```python\ndf.sum()\n```", "I'll total the weekly sales."),
    ("I'll total the weekly sales. Run `df.sum()` first.", "I'll total the weekly sales."),
    # A heading or list marker is not part of the sentence.
    ("**Totaling weekly sales**\n\nFirst the file.", "First the file."),
    ("- Grouping revenue by desk", "Grouping revenue by desk"),
    # An unfinished sentence shows once it is long enough to read, and not before.
    ("I'll total the weekly sales. Then group them by desk and", "Then group them by desk and"),
    ("I'll total the weekly sales. Then", "I'll total the weekly sales."),
    # Nothing plain at all is nothing, not the least technical fragment.
    ("df.groupby(desk).sum().reset_index()", ""),
    ("", ""),
])
def test_the_narration_is_one_plain_sentence(prose, shown):
    assert narration_sentence(prose) == shown


def test_a_long_sentence_is_cut_on_a_word():
    words = "I'll compare every desk's weekly revenue against the same week last year " * 3
    shown = narration_sentence(words.strip() + ".")
    assert len(shown) <= 100
    assert shown.endswith("…")
    assert words.startswith(shown[:-1])
    assert shown[-2] != " "


def test_a_sentence_that_turns_technical_leaves_the_last_good_one_up():
    fold = ReasoningNarration()
    assert fold.push("p", f"{PROSE} ") == PROSE
    assert fold.push("p", "Reading examples/thr_1/sales") is None
    assert fold.push("p", ".csv now.") is None


def _narration(rows):
    return [e["text"] for e in rows if e.get("type") == "narration"]


def _reasoning_rows(rows):
    return [e for e in rows if e.get("type") == "reasoning"]


def _answer_text(rows):
    return [e.get("text") for e in rows if e.get("type") == "agent" and e.get("kind") == "text"]


def test_chat_narrates_the_prose_and_keeps_none_of_it(tmp_path: Path):
    orch, _oc = _orch(tmp_path, [Turn(reasoning=f"{PROSE}\n\n{CALL}", text=ANSWER)])
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "total the weekly sales"))
    history = orch.thread_history(tid)
    assert _narration(events) == [PROSE]
    assert _narration(history) == []
    assert _reasoning_rows(events) == _reasoning_rows(history) == []
    for rows in (events, history):
        assert _answer_text(rows) == [ANSWER]
        blob = json.dumps(rows)
        assert "filePath" not in blob
        assert SECRET not in blob


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
        assert _narration(rows) == []
        assert _reasoning_rows(rows) == []
        assert _answer_text(rows) == [ANSWER]
        assert "filePath" not in json.dumps(rows)
        assert SECRET not in json.dumps(rows)


def test_build_narrates_the_prose_and_does_not_write_the_call_into_the_plan(tmp_path: Path):
    plan = execution_plan()
    orch, _oc = _orch(tmp_path, [Turn(reasoning=f"{PROSE}\n\n{CALL}", text=plan)],
                      gateway=ScriptedGateway("BUILD"))
    events = list(orch.build_stream("build a small dashboard", conversation="conv_reason"))
    history = orch.history("conv_reason")
    assert _narration(events) == [PROSE]
    assert PROSE not in json.dumps(history)
    for rows in (events, history):
        assert _reasoning_rows(rows) == []
        assert not any(e.get("kind") == "text" and "filePath" in (e.get("text") or "")
                       for e in rows)
        blob = json.dumps(rows)
        assert "filePath" not in blob
        assert SECRET not in blob
        assert PROSE not in json.dumps(
            [e for e in rows if e.get("type") != "narration"])


_FIRST = "I'll lay out the dashboard first."
_THEN = "Then the revenue chart goes under it."


class _ThinkingAloud(FakeOpenCode):
    """A Build turn whose reasoning part is read three times: open, grown, then closed.

    No `session_events`, so the turn opens no stream reader and the poll is the only witness —
    which is the path Build's narration rides on.
    """

    STAGES = ((_FIRST, False), (f"{_FIRST} {_THEN}", False), (f"{_FIRST} {_THEN}", True))

    def __init__(self, workspace, turns):
        super().__init__(workspace, turns)
        self.polls = 0

    def is_running(self, session_id):
        if not self.prompts:
            return super().is_running(session_id)
        self.polls += 1
        return self.polls < len(self.STAGES)

    def messages(self, session_id, *, limit=None):
        text, closed = self.STAGES[min(max(self.polls, 1), len(self.STAGES)) - 1]
        timing = {"start": 1, "end": 2} if closed else {"start": 1}
        return [{**m, "content": [
            {**p, "text": text, "time": timing}
            if isinstance(p, dict) and p.get("type") == "reasoning" else p
            for p in m.get("content", [])]}
            for m in super().messages(session_id, limit=limit)]


def test_build_narrates_a_reasoning_part_while_it_is_still_open(tmp_path: Path):
    """Build used to wait for the part to close and then save the whole thought at once, so a long
    think showed nothing until it was over. Each poll now reads the open part, and the line moves
    when its latest sentence does — once per sentence, not once per poll."""
    orch, _oc = _orch(tmp_path, client=lambda ws: _ThinkingAloud(
        ws, [Turn(reasoning="placeholder", text=execution_plan())]),
        gateway=ScriptedGateway("BUILD"))
    events = list(orch.build_stream("build a small dashboard", conversation="conv_open"))
    assert _narration(events) == [_FIRST, _THEN]
    history = json.dumps(orch.history("conv_open"))
    assert _FIRST not in history and _THEN not in history
    assert SECRET not in json.dumps(events)
