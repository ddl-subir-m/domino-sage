"""A `write` label grows while the file streams, instead of freezing (#497).

The symptom: `Writing app.py` appears and then sits unchanged for tens of seconds. The path is the
first and smallest key in the tool argument, so it is complete almost at once; the file content is
what takes the time.

#497's own measurement killed the fix its ticket proposed, and this file is built on that result:
**OpenCode never exposes a partly-filled tool input.** Over a 7.7 s streamed argument, both the
transcript poll and the `/event` stream went from `pending` with `input={}` straight to a `running`
snapshot whose content was already whole, 30 ms before `completed`. So anything derived from
`state["input"]` is blank for the whole window it needs to describe.

The fragments are real and they do reach Sage — they die inside OpenCode. Sage's own shim sees them
first, and `StreamEvents` already parses every frame for its ledger, so an `input_json_delta` is a
frame passing through a loop it does not currently branch on. Counting there is counting the bytes
where they actually are.

What this file pins, layer by layer, because the path crosses three of them:

  * `StreamEvents` counts lines off the fragments on all three protocol lanes, and keeps no part of
    the argument — the module's contract is the first line of its own source;
  * the count survives an arbitrary chunk boundary, including one falling inside an escape;
  * `_writing_progress` decorates a label only once there is something worth saying.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.gateway.events import StreamEvents
from sage.gateway.protocol import Protocol
from sage.orchestrator.service import Orchestrator, _tool_detail, _writing_progress
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

# A file with nine newlines in it, as the model would spell it inside a JSON string argument.
FILE = "\\n".join(f"line {i}" for i in range(10))
LINES = 9


def sse(*events) -> bytes:
    return b"".join(b"data: " + json.dumps(event).encode() + b"\r\n\r\n" for event in events)


def _fragments(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _messages_wire(size: int) -> bytes:
    return sse(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "write"}},
        *({"type": "content_block_delta", "index": 0,
           "delta": {"type": "input_json_delta", "partial_json": piece}}
          for piece in _fragments('{"filePath":"src/App.tsx","content":"' + FILE + '"}', size)),
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    )


def _chat_wire(size: int) -> bytes:
    return sse(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "write"}}]}}]},
        *({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": piece}}]}}]}
          for piece in _fragments('{"filePath":"src/App.tsx","content":"' + FILE + '"}', size)),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    )


def _responses_wire(size: int) -> bytes:
    return sse(
        {"type": "response.output_item.added", "item_id": "item_1",
         "item": {"type": "function_call", "call_id": "call_1", "name": "write"}},
        *({"type": "response.function_call_arguments.delta", "item_id": "item_1", "delta": piece}
          for piece in _fragments('{"filePath":"src/App.tsx","content":"' + FILE + '"}', size)),
        {"type": "response.function_call_arguments.done", "item_id": "item_1"},
        {"type": "response.completed", "response": {"usage": {}}},
    )


WIRES = {Protocol.MESSAGES: _messages_wire, Protocol.CHAT: _chat_wire,
         Protocol.RESPONSES: _responses_wire}


@pytest.mark.parametrize("protocol", list(WIRES))
def test_every_lane_counts_the_lines_of_a_streaming_write(protocol: Protocol):
    """All three, not only the one #497 measured.

    The measurement was taken on the Anthropic lane, because that is where `eager_input_streaming`
    is a thing you can ask for. But Sage routes all three, and a progress label that works on one
    vendor and silently does nothing on the others is worse than none: the person cannot tell a
    quiet build from an unsupported one.
    """
    events = StreamEvents(protocol)
    events.feed(WIRES[protocol](40))

    assert events.tool_input_lines == {"write": LINES}


@pytest.mark.parametrize("protocol", list(WIRES))
@pytest.mark.parametrize("size", [1, 2, 3, 7, 40, 4096])
def test_the_count_does_not_depend_on_where_the_fragments_break(protocol: Protocol, size: int):
    """A fragment can end mid-escape: `"\\` in one frame, `n..."` in the next.

    Counted apart those halves are two non-matches, and the line goes missing. `size=1` puts a
    boundary between EVERY pair of characters, so it fails on any implementation that does not carry
    the trailing backslash — the whole count collapses to zero rather than slipping by one, which is
    why this is parametrised down to a single character rather than stopping at a plausible size.
    """
    events = StreamEvents(protocol)
    events.feed(WIRES[protocol](size))

    assert events.tool_input_lines == {"write": LINES}


@pytest.mark.parametrize("protocol", list(WIRES))
@pytest.mark.parametrize("split", [1, 11, 97, 300])
def test_the_count_does_not_depend_on_where_the_network_chunks_break(protocol: Protocol, split: int):
    """The same property one layer down: a TCP chunk can cut an SSE frame anywhere, including
    inside the JSON of a fragment. `feed()` already buffers partial frames; this pins that the new
    counting rides that buffering rather than working around it."""
    wire = WIRES[protocol](40)
    events = StreamEvents(protocol)
    events.feed(wire[:split])
    events.feed(wire[split:])

    assert events.tool_input_lines == {"write": LINES}


@pytest.mark.parametrize("protocol", list(WIRES))
def test_not_one_character_of_the_argument_is_kept(protocol: Protocol):
    """`events.py` opens with "Retain metadata, never reasoning or tool arguments." Counting lines
    off an argument is the closest this module has come to breaking that, so the contract is
    asserted rather than argued.

    Checked over the whole object's state, not over the new field alone: the failure worth catching
    is a carry or a buffer that accumulates instead of being spent, and that would not be in the
    field anybody thought to look at.
    """
    events = StreamEvents(protocol)
    events.feed(WIRES[protocol](40))

    state = repr(events.__dict__)
    assert "src/App.tsx" not in state
    assert "line 7" not in state
    # The carry may legitimately hold a single backslash mid-stream; it must hold nothing more, and
    # at the end of a completed call it must hold nothing at all.
    assert events._carry == {}
    assert events._streaming == {}


def test_a_tool_that_is_still_streaming_is_told_apart_from_one_that_is_not():
    """Zero and absent are different answers and the reader depends on it: zero is a call under way
    that has produced no line yet, absent is no call at all. A reader that could not tell them apart
    would draw a label for a tool that is not running."""
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(sse({"type": "content_block_start", "index": 0,
                     "content_block": {"type": "tool_use", "id": "t1", "name": "write"}}))

    assert events.tool_input_lines == {"write": 0}
    assert "read" not in events.tool_input_lines


def test_two_tools_in_one_response_are_counted_apart():
    """Two different tools get their own counts, so a `bash` streaming beside a `write` cannot
    caption the write. Two calls of the SAME tool share one count and that is a stated limit, not
    an oversight — see the field's comment: an OpenCode transcript part carries no provider
    tool-call id, so there is nothing on the reader's side to join a finer key to."""
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(sse(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "t1", "name": "write"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": "a\\nb\\nc"}},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "t2", "name": "bash"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": "x\\ny"}},
    ))

    assert events.tool_input_lines == {"write": 2, "bash": 1}


def test_text_and_thinking_deltas_are_not_counted():
    """The lane carries prose in the same frame type. Only `input_json_delta` is a tool argument;
    counting a text delta would make the label climb while the model was talking, not writing."""
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(sse(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "t1", "name": "write"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "a\\nb\\nc\\nd"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "e\\nf"}},
    ))

    assert events.tool_input_lines == {"write": 0}


def test_a_malformed_frame_cannot_take_the_count_down():
    """Same class the classifier hardening in #498 dealt with: this reads a body Sage did not write.
    A fragment that is not a string, a tool entry that is not a dict, an index that is missing —
    none of them is worth failing a turn over."""
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(sse(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "t1", "name": "write"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": None}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": 12}},
        {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "a\\nb"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": "a\\nb"}},
    ))

    assert events.tool_input_lines == {"write": 1}

    chat = StreamEvents(Protocol.CHAT)
    chat.feed(sse({"choices": [{"delta": {"tool_calls": [None, "not-a-dict"]}}]}))
    assert chat.tool_input_lines == {}


def test_the_existing_ledger_readings_are_unchanged():
    """`tool_names`, `tool_ids` and the usage totals are what the cost ledger and #469's tool-use
    row are built on. The new branches sit in the same `_event`, so they get to prove they took
    nothing away."""
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(_messages_wire(40))

    assert events.tool_names == {"write"}
    assert events.tool_ids == {"toolu_1"}
    assert events.terminal == "message_stop"
    assert not events.error


@pytest.mark.parametrize("lines,expected", [
    (None, "src/App.tsx"),
    (0, "src/App.tsx"),
    (1, "src/App.tsx"),
    (2, "src/App.tsx · 2 lines"),
    (240, "src/App.tsx · 240 lines"),
])
def test_a_label_only_grows_once_there_is_something_to_say(lines, expected):
    """`None` is the ordinary case — no call streaming, or a tool this does not apply to — and must
    leave the label exactly as it was. Below two lines the label would flicker a number and then
    grow, which on a short write is noise standing in for the very steadiness it is meant to fix."""
    assert _writing_progress("src/App.tsx", lines) == expected


def test_an_empty_label_is_not_decorated():
    """No path means `_tool_detail` found nothing to name, and " · 12 lines" alone names no file.
    The count is a modifier on a subject, never the subject."""
    assert _writing_progress("", 200) == ""
    assert _writing_progress("", None) == ""


# --- the label on the wire, through the real turn path -------------------------------------------
#
# Everything above is the shim half. This is the orchestrator half: that the count reaches the
# `active` event a person's screen is drawn from, on the tool it was meant for and no other.

class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class CountingOpenCode(FakeOpenCode):
    """A fake that publishes a line count while the model call runs, as the shim really does.

    Not a monkeypatch of convenience: the fake dispatches synchronously, so `send_prompt` IS the
    window in which the model streams its tool argument — the same window `native_routes.pump`
    writes `project.tool_input_lines` in. Setting it anywhere else would test a state the real
    system never passes through.
    """

    def __init__(self, workspace, turns, counts):
        super().__init__(workspace, turns)
        self.project = None      # set after the Orchestrator exists; see _orch_with_counts
        self._counts = counts if isinstance(counts, list) else [counts]

    def send_prompt(self, *args, **kwargs):
        if self.project is not None and self._counts:
            # One entry per prompt, so a test can give the first turn a count and the next none —
            # which is how the real thing behaves, the count being emptied when its call ends.
            nth = min(len(self.prompts), len(self._counts) - 1)
            counts = self._counts[nth]
            # `None` means "this call streamed no tool argument, so the shim wrote nothing" —
            # NOT "the shim wrote an empty dict". The difference is the whole test below: assigning
            # `{}` here would clear the field from the double, and the production reset it is
            # supposed to be testing could be deleted with the test still green. A plant caught
            # exactly that.
            if counts is not None:
                self.project.tool_input_lines = dict(counts)
        return super().send_prompt(*args, **kwargs)


def _orch_with_counts(tmp: Path, turns: list[Turn], counts: dict):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text('{"name": "template"}')

    ws = tmp / "mnt" / "code"
    oc = CountingOpenCode(ws, turns, counts)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    # The fake needs the Project and the Orchestrator needs the fake, so the loop is closed here
    # rather than in a constructor. Nothing reads it until the first `send_prompt`.
    oc.project = project
    return orch, oc


def _details(events: list[dict], tool: str) -> list[str]:
    return [e["detail"] for e in events if e.get("type") == "active" and e.get("tool") == tool]


def test_the_count_reaches_the_active_event_a_screen_is_drawn_from(tmp_path: Path, monkeypatch):
    """End to end on the half that can be tested here: a `write` in flight is captioned with the
    lines the shim has counted, and `store.js` renders `ev.detail` verbatim."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, _oc = _orch_with_counts(
        tmp_path,
        [Turn(writes={"src/App.tsx": "app\n"}, streaming={"write": "src/Dashboard.tsx"})],
        {"write": 240})

    events = list(orch.build_stream("build me a dashboard"))

    assert "src/Dashboard.tsx · 240 lines" in _details(events, "write")


def test_a_tool_whose_argument_was_never_the_slow_part_is_left_alone(tmp_path: Path, monkeypatch):
    """`read`, `bash` and `grep` take a path or a command — short arguments that arrive whole, so
    their labels never froze. A count on them would be noise, and on `bash` it would be a count of
    lines in a command that is usually one line long."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, _oc = _orch_with_counts(
        tmp_path,
        [Turn(writes={"src/App.tsx": "app\n"},
              streaming={"write": "src/Dashboard.tsx", "read": "src/App.tsx",
                         "bash": "npm run build"})],
        {"write": 240, "read": 99, "bash": 99})

    events = list(orch.build_stream("build me a dashboard"))

    # Assert the labels EXIST before asserting what they do not contain. Without this the test
    # passes on an empty list, which is what it did until a plant caught it: the fake emitted no
    # in-flight read or bash at all, so "no read label carries a count" was true of nothing.
    assert set(_details(events, "read")) == {"src/App.tsx"}
    assert set(_details(events, "bash")) == {"npm run build"}
    assert set(_details(events, "write")) == {"src/Dashboard.tsx \u00b7 240 lines"}


def test_a_finished_call_is_not_captioned_with_the_next_one_s_progress(tmp_path: Path, monkeypatch):
    """The `tool done` log line goes through `_tool_detail` too, and it must stay a pure function of
    the part. A count added inside `_tool_detail` rather than at the in-flight call site would
    caption every completed write with whatever is streaming now — a finished file growing a line
    count, which is the defect this fixes wearing the other face."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, _oc = _orch_with_counts(
        tmp_path,
        [Turn(writes={"src/App.tsx": "app\n"}, streaming={"write": "src/Dashboard.tsx"})],
        {"write": 240})

    part = {"state": {"status": "completed", "input": {"filePath": "src/App.tsx"}}}

    list(orch.build_stream("build me a dashboard"))

    assert _tool_detail("write", part) == "src/App.tsx"


def test_a_count_does_not_outlive_the_call_that_produced_it(tmp_path: Path, monkeypatch):
    """A stale count is this defect wearing the other face.

    Left standing, the number would caption the NEXT write the moment its part appeared: a label
    that looks live and is describing a file finished a minute ago. Worse than a frozen label,
    because a frozen one is at least honest about knowing nothing.

    What this reaches is the reset at the top of a turn. The other half of the clearing — the
    `finally` in `native_routes.pump`, which empties it when a single model call's stream ends, and
    so covers a second `write` LATER IN THE SAME TURN — has no test here; driving that closure
    needs a live gateway stream through the shim app and there is no harness for one. Stated rather
    than implied.
    """
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    orch, _oc = _orch_with_counts(
        tmp_path,
        [Turn(writes={"src/App.tsx": "app\n"}, streaming={"write": "src/First.tsx"}),
         Turn(writes={"src/Two.tsx": "two\n"}, streaming={"write": "src/Second.tsx"})],
        [{"write": 240}, None])

    first = list(orch.build_stream("build me a dashboard"))
    second = list(orch.build_stream("now add a filter"))

    assert _details(first, "write") == ["src/First.tsx \u00b7 240 lines"]
    assert _details(second, "write") == ["src/Second.tsx"]
