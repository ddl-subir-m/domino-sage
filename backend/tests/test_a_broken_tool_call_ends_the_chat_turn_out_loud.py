"""A Chat turn whose tool call OpenCode rewrote to its `invalid` tool is corrected once (#567).

The shape is the one #565 pinned against the binary: the AI SDK cannot parse or validate the
call, OpenCode's `experimental_repairToolCall` rewrites it to the built-in `invalid` tool with
`{tool, error}` as its input, that part lands COMPLETED, and the session goes on. The intended tool
never ran. Chat read nothing of this: its one recovery allowance was spent only on a missing answer
or an invalid artifact, so a person asking a question could watch minutes of malformed calls and
get no answer, or an answer written around a tool that was never called.

What these tests pin, one per acceptance criterion:

1. The wrapper is read off the stream and off the transcript, and the two report one fault.
2. Invalid then valid answers with one correction and `recoveries: 1`; invalid then invalid stops
   with one correction and the ADR-0069 fields on the live row and the saved row alike.
3. The correction shares Chat's ONE allowance with the answer and artifact repair, in both orders,
   and a re-delivered wrapper is one fault.
4. Stop, the turn deadline, a provider terminal error and a refused interrupt each beat the
   recovery and write no `cause`.
5. Every Chat `done` row carries `turnId`.
6. Nothing the model emitted reaches a person, the next prompt, the saved history or the log.

The correction stays in the same session, under the same Chat enforcement pin and the original
turn deadline, which is what makes it Chat's allowance and not a second Build.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

from sage.orchestrator import service

from .fake_opencode import FakeOpenCode, Turn
from .test_chat_turn import (
    IntentGateway,
    ObservedControlOpenCode,
    StreamingFake,
    _live,
    _no_waiting,
    _orch,
)

__all__ = ["_no_waiting"]  # the host's autouse fixture, so a poll never really sleeps here

# The SDK's message as the binary emits it: the model's raw arguments ride inside it, which is why
# the default in `Turn` already looks like a cut-off file and why nothing of it may be repeated.
CORRECTION = "write call arrived with arguments that did not validate"
GIVE_UP = "The model's write call arrived with arguments that did not validate"


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def _done(rows: list[dict]) -> dict:
    return next(e for e in rows if e["type"] == "done")


def _no_cause(rows: list[dict]) -> None:
    for e in rows:
        assert "cause" not in e, e


def _wrapper(intended: str = "write", *, call_id: str, part_id: str = "p-again") -> dict:
    """A hand-built completed wrapper, for a second delivery of a call the fake already scripted."""
    return {"id": part_id, "callID": call_id, "type": "tool", "tool": "invalid",
            "state": {"status": "completed",
                      "input": {"tool": intended, "error": Turn.invalid_error},
                      "output": "The arguments provided to the tool are invalid: " + Turn.invalid_error}}


def _append_parts(oc: FakeOpenCode, extra: list[dict]) -> None:
    """Add hand-built parts to the NEXT scripted message, after the fake has built it."""
    send_prompt = oc.send_prompt

    def send(session_id, *args, **kwargs):
        send_prompt(session_id, *args, **kwargs)
        oc._by_session[session_id][-1]["content"].extend(extra)
        oc.send_prompt = send_prompt

    oc.send_prompt = send


# --- Criterion 2 (and the regression): invalid then valid, invalid then invalid ----------------


def test_a_completed_invalid_wrapper_is_corrected_once_in_the_same_session(tmp_path: Path):
    """The regression. Before #567 the wrapper was not read at all: the turn spent its allowance
    on the generic "ended without an answer" text, and the row carried no `recoveries`."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]),
                                Turn(text="There are three columns.")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2
    assert len(oc.sessions) == 1, "the correction goes into the Thread's own session"
    assert oc.prompts[1]["session"] == oc.prompts[0]["session"]
    assert oc.prompts[1]["agent"] == "sage-chat"
    assert "Your last " + CORRECTION in oc.prompts[1]["text"]
    assert "did not run" in oc.prompts[1]["text"]
    for rows in (events, orch.thread_history(tid)):
        done = _done(rows)
        assert done["ok"] is True and done["decision"] == "answered"
        assert done["recoveries"] == 1
        assert done["turnId"]
        assert "cause" not in done, "a turn that recovered is not a failed one"
        assert [e["text"] for e in rows if e.get("kind") == "text"] == ["There are three columns."]
    assert not _of(events, "error")


def test_invalid_twice_stops_with_the_cause_on_the_live_and_the_saved_row(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]), Turn(invalid_calls=["write"]),
                                Turn(text="never asked for")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2 and len(oc.sessions) == 1
    done = _done(events)
    assert done["ok"] is False and done["decision"] == "broken tool call"
    assert done["cause"] == "invalid_tool_call"
    assert done["stage"] == "chat"
    assert done["recoveries"] == 1
    assert done["turnId"]
    saved = _done(orch.thread_history(tid))
    # The same dict, plus the `at` Chat stamping adds on the way to disk.
    for key in ("ok", "decision", "cause", "stage", "recoveries", "turnId"):
        assert saved[key] == done[key], key
    message = _of(events, "error")[0]["message"]
    assert "stopped twice in the same step" in message
    assert GIVE_UP in message
    assert "Pick a different model" in message
    for blame in ("gateway", "stopped responding", "too big", "smaller"):
        assert blame not in message, f"the give-up guesses a cause: {blame!r}"
    assert any(e.get("message") == message for e in orch.thread_history(tid))
    assert orch._turns.running() is None, "the turn ticket was released"


def test_the_intended_tool_landing_later_is_a_proven_recovery(tmp_path: Path):
    """The one completion that retires the fault: the model retried its own call and it ran.
    Nothing is left to correct, and a correction here would replay a call that completed."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["read"], tools_after=["read"],
                                     text="There are three columns.")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 1
    done = _done(events)
    assert done["ok"] is True and "recoveries" not in done and "cause" not in done


def test_an_unrelated_completion_does_not_retire_the_fault(tmp_path: Path):
    """A different tool landing, and prose after the wrapper, leave the fault standing."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"], tools_after=["read", "grep"],
                                     text="Let me look at that file."),
                                Turn(text="There are three columns.")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2
    assert _done(events)["recoveries"] == 1
    assert [e["text"] for e in events if e.get("kind") == "text"] == ["There are three columns."]


# --- Criterion 1: the stream path and the transcript path, one fault between them ----------------


class _LateTranscript(StreamingFake):
    """Streams the first response; the poll owns the turn from the correction on, as in
    test_chat_turn's LateTranscript."""

    def is_running(self, session_id):
        if len(self.prompts) > 1:
            return FakeOpenCode.is_running(self, session_id)
        return super().is_running(session_id)


def _stream(tmp_path: Path, turns: list[Turn], call_id: str = "c1"):
    wrapper_input = {"tool": "write", "error": Turn.invalid_error}
    events = [
        _live("tool_run", tool="invalid", input=wrapper_input, call_id=call_id, status="called"),
        _live("tool_run", tool="invalid", input=wrapper_input, call_id=call_id, status="success"),
        _live("phase", finish="stop"),
    ]
    return _orch(tmp_path, client=lambda ws: _LateTranscript(ws, turns, events))


def test_the_wrapper_is_read_off_the_stream(tmp_path: Path, caplog):
    """The transcript carries no wrapper at all: the stream is the only witness."""
    orch, oc = _stream(tmp_path, [Turn(), Turn(text="There are three columns.")])
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2
    assert "Your last " + CORRECTION in oc.prompts[1]["text"]
    assert _done(events)["recoveries"] == 1 and _done(events)["ok"] is True
    assert caplog.text.count("rewritten to OpenCode's invalid tool") == 1


def test_the_stream_and_the_transcript_report_one_fault(tmp_path: Path, caplog):
    """Both witnesses see the same call: the fake's scripted callID rides the stream too."""
    orch, oc = _stream(tmp_path, [Turn(invalid_calls=["write"]),
                                  Turn(text="There are three columns.")],
                       call_id="call-m1-i0")
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2
    assert _done(events)["recoveries"] == 1
    assert caplog.text.count("rewritten to OpenCode's invalid tool") == 1


# --- Criterion 3: one allowance, both orders, and a duplicate spends nothing --------------------


def test_invalid_then_an_empty_answer_gets_no_second_correction(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]), Turn(), Turn(text="never")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2, "the empty answer after the correction buys no second one"
    assert "Your last " + CORRECTION in oc.prompts[1]["text"]
    for rows in (events, orch.thread_history(tid)):
        done = _done(rows)
        assert done["ok"] is False and done["decision"] == "empty answer"
        assert done["recoveries"] == 1
        assert "cause" not in done, "the second failure was not an invalid call"


def test_an_empty_answer_then_invalid_gets_no_second_correction(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(), Turn(invalid_calls=["write"]), Turn(text="never")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2, "the invalid call after the answer repair buys no second one"
    assert "ended without an answer" in oc.prompts[1]["text"]
    for rows in (events, orch.thread_history(tid)):
        done = _done(rows)
        assert done["ok"] is False and done["decision"] == "broken tool call"
        assert done["cause"] == "invalid_tool_call" and done["stage"] == "chat"
        assert done["recoveries"] == 1


def test_a_bad_table_then_invalid_gets_no_second_correction(tmp_path: Path):
    """The artifact half of the shared allowance: the table repair is the one correction."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    table = f"examples/{tid}/result.table.json"
    oc.turns = [Turn(text="The table is ready.", writes={table: "{bad"}),
                Turn(invalid_calls=["write"]), Turn(text="never")]

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2
    done = _done(events)
    assert done["ok"] is False and done["decision"] == "broken tool call"
    assert done["cause"] == "invalid_tool_call" and done["recoveries"] == 1


def test_a_re_delivered_wrapper_is_one_fault(tmp_path: Path, caplog):
    """The same call under two part ids is charged once and logged once."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]),
                                Turn(text="There are three columns.")])
    _append_parts(oc, [_wrapper(call_id="call-m1-i0")])
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2
    assert _done(events)["ok"] is True and _done(events)["recoveries"] == 1
    assert caplog.text.count("rewritten to OpenCode's invalid tool") == 1


# --- The correction keeps the session, the Chat pin and the original deadline ------------------


def test_the_correction_keeps_the_chat_pin_and_the_original_deadline(tmp_path: Path, monkeypatch):
    orch, oc = _orch(tmp_path, client=lambda ws: ObservedControlOpenCode(
        ws, [Turn(invalid_calls=["write"]), Turn(text="There are three columns.")]))
    project = orch.project(start_preview=False)
    oc.control = project.control
    tid = orch.create_thread()["id"]
    clock = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(service, "_CHAT_TURN_MAX_S", 10)
    send = oc.send_prompt

    def dispatch(*args, **kwargs):
        send(*args, **kwargs)
        if len(oc.prompts) == 2:
            # The correction takes the turn over the ORIGINAL deadline: nine seconds were spent
            # before it, and a fresh one would have let this answer land.
            clock[0] = 111.0

    oc.send_prompt = dispatch
    running = oc.is_running

    def first_request_takes_nine_seconds(*args):
        result = running(*args)
        if not result and len(oc.prompts) == 1:
            clock[0] = 109.0
        return result

    oc.is_running = first_request_takes_nine_seconds

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 2 and len(oc.sessions) == 1
    assert [s.chat_thread_id for s in oc.snapshots] == [tid, tid], "both sends under the Chat pin"
    done = _done(events)
    assert done["decision"] == "timeout" and done["ok"] is False
    assert done["recoveries"] == 1 and done["turnId"]
    _no_cause(events)


# --- Criterion 4: what beats the recovery, and none of it writes a cause -----------------------


def test_a_user_stop_beats_the_recovery(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]), Turn(text="never")])
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    send = oc.send_prompt

    def send_then_stop(*args, **kwargs):
        send(*args, **kwargs)
        project.stop_requested = True

    oc.send_prompt = send_then_stop

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 1, "no correction over a Stop"
    done = _done(events)
    assert done["decision"] == "stopped" and done["turnId"]
    assert "recoveries" not in done
    _no_cause(events)
    assert orch._turns.running() is None, "the turn ticket was released"


def test_the_deadline_beats_the_recovery(tmp_path: Path, monkeypatch):
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]), Turn(text="never")])
    tid = orch.create_thread()["id"]
    clock = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(service, "_CHAT_TURN_MAX_S", 10)
    running = oc.is_running

    def the_first_request_runs_out_the_turn(*args):
        result = running(*args)
        if not result:
            clock[0] = 111.0
        return result

    oc.is_running = the_first_request_runs_out_the_turn

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 1, "no correction past the deadline"
    done = _done(events)
    assert done["decision"] == "timeout" and done["turnId"]
    assert "recoveries" not in done
    _no_cause(events)


def test_a_provider_terminal_error_beats_the_recovery(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"],
                                     error={"name": "APIError",
                                            "data": {"message": "provider unavailable"}}),
                                Turn(text="never")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 1, "no correction over a provider that ended the turn"
    done = _done(events)
    assert done["ok"] is False and done["decision"] == "step failed" and done["turnId"]
    assert "recoveries" not in done
    _no_cause(events)
    assert any("provider unavailable" in e.get("message", "") for e in _of(events, "error"))


class _WillNotStop(FakeOpenCode):
    """Runs once, reads idle once so the poll loop leaves, then reads busy for good: the reading a
    recovery cannot accept, since whether the session let go is unknown."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.readings = 0

    def is_running(self, session_id: str) -> bool:
        self.readings += 1
        return self.readings != 2

    def interrupt(self, session_id: str) -> None:
        self.interrupted += 1


def test_a_refused_interrupt_beats_the_recovery(tmp_path: Path, caplog):
    orch, oc = _orch(tmp_path, client=lambda ws: _WillNotStop(
        ws, [Turn(invalid_calls=["write"]), Turn(text="never")]))
    orch._build_policy = replace(orch._build_policy, stop_grace_seconds=0.01)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.ERROR, logger="sage.orchestrator"):
        events = list(orch.chat_stream(tid, "summarize the file"))

    assert len(oc.prompts) == 1, "no correction into a session that may still be writing"
    assert oc.interrupted >= 1
    done = _done(events)
    assert done["ok"] is False and done["turnId"]
    assert "recoveries" not in done
    _no_cause(events)
    assert "would not confirm" in caplog.text


# --- Criterion 5: every Chat `done` row names its turn -------------------------------------------


def test_every_chat_done_row_carries_the_turn_id(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        Turn(text="Fine."),                                   # answered
        Turn(), Turn(),                                       # empty answer, allowance spent
        Turn(invalid_calls=["write"]), Turn(invalid_calls=["write"]),  # broken tool call
        Turn(invalid_calls=["write"], error={"name": "APIError",
                                             "data": {"message": "provider unavailable"}}),
    ])
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)

    done_rows = [_done(list(orch.chat_stream(tid, "hi")))]
    done_rows.append(_done(list(orch.chat_stream(tid, "summarize the file"))))
    done_rows.append(_done(list(orch.chat_stream(tid, "summarize the file"))))
    done_rows.append(_done(list(orch.chat_stream(tid, "summarize the file"))))
    # A Stop, which goes through the same terminal seam from the top of the loop.
    send = oc.send_prompt

    def send_then_stop(*args, **kwargs):
        send(*args, **kwargs)
        project.stop_requested = True

    oc.send_prompt = send_then_stop
    done_rows.append(_done(list(orch.chat_stream(tid, "summarize the file"))))
    oc.send_prompt = send
    # The explicit Build offer, which ends the turn before any model runs.
    done_rows.append(_done(list(orch.chat_stream(tid, "build me a dashboard app"))))
    # And the one row that never reaches the Thread, because there is no Thread to reach.
    done_rows.append(_done(list(orch.chat_stream("no-such-thread", "hi"))))

    assert [d["decision"] for d in done_rows] == [
        "answered", "empty answer", "broken tool call", "step failed", "stopped", "handoff",
        "unknown thread"]
    ids = [d["turnId"] for d in done_rows]
    assert all(ids) and len(set(ids)) == len(ids), ids
    saved = [r["turnId"] for r in orch.thread_history(tid) if r.get("type") == "done"]
    assert saved == ids[:-1], "the saved rows carry the same ids"


def test_a_gate_row_carries_the_turn_id(tmp_path: Path):
    """A card drawn INSTEAD of a turn ends on a `done` that never passes through `finish`."""
    orch, oc = _orch(tmp_path, [Turn(text="answered anyway")],
                     gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}))
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})

    events = list(orch.chat_stream(
        tid, "Work out which deals stalled, and get to the bottom of what they have in common."))

    assert oc.prompts == []
    done = _done(events)
    assert done["decision"] == "investigation offer" and done["turnId"]
    assert _done(orch.thread_history(tid))["turnId"] == done["turnId"]


# --- Criterion 6: nothing the model emitted reaches a person, the next prompt, or the record ----


def test_nothing_the_model_emitted_reaches_a_person_or_the_record(tmp_path: Path, caplog):
    secret = "sk-live-4f9a"
    # The SDK's message carries the model's raw arguments whole: a nested error payload inside
    # them, and a string cut mid-token where the stream stopped.
    error = ('Invalid input for tool write: Type validation failed: Value: '
             '{"filePath":"examples/Secret.png","nested":{"error":"Invalid input for tool bash"},'
             '"content":"const TOKEN = \\"' + secret)
    intended = 'write"; DROP TABLE users; --'
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=[intended], invalid_error=error),
                                Turn(invalid_calls=[intended], invalid_error=error)])
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.INFO):
        events = list(orch.chat_stream(tid, "summarize the file"))

    done = _done(events)
    assert done["decision"] == "broken tool call" and done["cause"] == "invalid_tool_call"
    message = _of(events, "error")[0]["message"]
    assert "The model's tool call arrived with arguments that did not validate" in message
    assert "Your last tool call arrived with arguments that did not validate" in oc.prompts[1]["text"]
    exposed = [json.dumps(events), json.dumps(oc.prompts), caplog.text,
               json.dumps(orch.thread_history(tid))]
    exposed += [f.read_text(errors="replace") for f in project.record.path.rglob("*")
                if f.is_file() and "node_modules" not in f.parts]
    for token in (secret, "Secret.png", "DROP TABLE", "Invalid input for tool bash",
                  "const TOKEN", "Type validation failed"):
        for text in exposed:
            assert token not in text, f"{token!r} leaked"
