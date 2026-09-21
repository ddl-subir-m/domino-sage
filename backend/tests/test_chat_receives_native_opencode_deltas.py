"""The live 1.18.4 stream uses message.part.delta, not only session.next.text.delta (#417)."""
import json
from contextlib import contextmanager

import pytest

from sage.driver.opencode import SessionEvents


def frame(kind, **properties):
    return {"type": kind, "properties": {"sessionID": "s1", **properties}}


def message(role="assistant", mid="m1"):
    return frame("message.updated", info={"id": mid, "role": role, "sessionID": "s1"})


def part(kind="text", pid="p1", mid="m1", **kwargs):
    return frame("message.part.updated", part={"id": pid, "messageID": mid,
                 "sessionID": "s1", "type": kind, **kwargs})


def delta(text="Hello", pid="p1", mid="m1", **kwargs):
    return frame("message.part.delta", partID=pid, messageID=mid,
                 field="text", delta=text, **kwargs)


def stream(monkeypatch, rows):
    class Response:
        def raise_for_status(self):
            pass

        def iter_lines(self):
            for row in rows:
                yield "data: " + json.dumps(row)

    @contextmanager
    def request(*args, **kwargs):
        yield Response()

    monkeypatch.setattr("sage.driver.opencode.httpx.stream", request)
    return iter(SessionEvents("http://opencode.test", "s1", "/chat"))


def test_the_first_fragment_arrives_before_the_completed_part(monkeypatch):
    completed = []

    def rows():
        yield message()
        yield part(text="", time={"start": 1})
        yield delta()
        completed.append(True)
        yield part(text="Hello there.", time={"start": 1, "end": 2})

    events = stream(monkeypatch, rows())
    first = next(events)
    assert completed == [], "the caller must not wait for the full answer"
    assert first.kind == "message" and first.payload == {"delta": "Hello", "final": False}
    final = next(events)
    assert final.payload == {"text": "Hello there.", "final": True}
    assert list(events) == []


@pytest.mark.parametrize("role,kind", [("user", "text"), ("assistant", "reasoning"),
                                       ("assistant", "tool")])
def test_only_assistant_text_is_visible(monkeypatch, role, kind):
    events = stream(monkeypatch, [message(role), part(kind, text="private"), delta("private"),
                                 part(kind, text="private", time={"start": 1, "end": 2})])
    assert list(events) == []


@pytest.mark.parametrize("bad", [
    delta(sessionID="other"), delta(pid="unknown"), delta(mid="unknown"),
    frame("message.part.delta", messageID="m1", partID="p1", field="input", delta="private"),
])
def test_an_unmatched_delta_is_not_an_answer(monkeypatch, bad):
    assert list(stream(monkeypatch, [message(), part(text=""), bad])) == []


def test_a_missing_role_does_not_echo_the_wrapped_user_prompt(monkeypatch):
    assert list(stream(monkeypatch, [part(text="internal prompt", time={"end": 2}), delta()])) == []


def test_a_final_snapshot_repairs_missed_deltas_once(monkeypatch):
    final = part(text="The complete answer.", time={"start": 1, "end": 2})
    events = list(stream(monkeypatch, [message(), final, final]))
    assert len(events) == 1
    assert events[0].payload == {"text": "The complete answer.", "final": True}


@pytest.mark.parametrize("status,expected", [("completed", "success"), ("error", "failed")])
def test_native_tool_progress_still_controls_the_chat_turn(monkeypatch, status, expected):
    running = part("tool", callID="c1", tool="read", state={"status": "running",
                   "input": {"filePath": "data.csv"}})
    ended = part("tool", callID="c1", tool="read", state={"status": status,
                 "input": {"filePath": "data.csv"}})
    events = list(stream(monkeypatch, [message(), running, running, ended, ended]))
    assert [e.kind for e in events] == ["tool_run", "tool_run"]
    assert [e.payload["status"] for e in events] == ["called", expected]
    assert all(e.payload["call_id"] == "c1" and e.payload["tool"] == "read" for e in events)
    assert events[0].payload["input"] == {"filePath": "data.csv"}


def test_native_message_completion_preserves_the_finish_reason(monkeypatch):
    done = frame("message.updated", info={"id": "m1", "role": "assistant",
                                         "finish": "tool-calls"})
    events = list(stream(monkeypatch, [done]))
    assert len(events) == 1
    assert events[0].kind == "phase" and events[0].payload == {"finish": "tool-calls"}


@pytest.mark.parametrize("first_kind", ["text", "tool"])
def test_first_output_does_not_pay_the_later_polling_floor(tmp_path, monkeypatch, first_kind):
    from sage.orchestrator import service

    from .fake_opencode import Turn
    from .test_chat_turn import _live, _orch

    waits = []
    first = (_live("message", delta="Hello", final=False) if first_kind == "text" else
             _live("tool_run", tool="read", input={"filePath": "data.csv"},
                   call_id="c1", status="called"))
    frames = [first, _live("message", delta=" there", final=False),
              _live("phase", finish="stop")]

    class Tap:
        ok = True
        seen_any = False

        def __init__(self, *args, **kwargs):
            self.ready = []

        def drain(self):
            ready, self.ready = self.ready, []
            self.seen_any = self.seen_any or bool(ready)
            return ready

        def wait_any(self, timeout, floor=0.0):
            waits.append(floor)
            self.ready = [frames[len(waits) - 1]]
            return True

        def close(self):
            pass

    monkeypatch.setattr(service, "_EventTap", Tap)
    orch, client = _orch(tmp_path, [Turn(text="Hello there")])
    monkeypatch.setattr(client, "is_running", lambda _sid: len(waits) < len(frames))
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "hi"))
    assert next(e for e in events if e["type"] == "done")["ok"]
    assert waits == [0.0, service._POLL_FLOOR_S, service._POLL_FLOOR_S]
