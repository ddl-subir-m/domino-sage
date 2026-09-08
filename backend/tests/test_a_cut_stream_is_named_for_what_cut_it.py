"""A gateway stream that just stops is named for that, and nothing blames the size of the write.

Measured live (cloud dogfood, 2026-09-07 03:03-03:07): a `write` call arrived carrying 22 characters
of arguments — `{"path": "src/App.tsx"` and nothing after it, no comma, no `"content"`, no closing
brace. No terminal `finish_reason` ever came: a cap carries "length", a healthy answer carries "stop"
or "tool_calls", and this stream carried neither and simply ended, at 48.1s over 15 chunks, on a turn
where the gateway was visibly degraded (gpt-5.4/plan ttfb=95.3s beside it).

Two faults in one, and this file pins both.

Nothing noticed the cut. `pump` drains the generator to exhaustion and puts DONE whatever the stream
said on its way out, so the only symptom surfaced four layers away as OpenCode's "Invalid JSON input
for openai-chat tool call write" — a message about JSON, for a fault that has nothing to do with
JSON. `cut_off_finish_reason` was already parsing every chunk for that field; what was missing was
remembering whether ANY chunk carried a terminal one, and saying so when none did.

And the messages Sage wrote about it named a cause the capture disproves. Both the retry note and
the give-up asked for a smaller write, generalised from a single 2026-09-05 observation. 22
characters is not a write that was too big, and asking for a smaller piece changes nothing when the
gateway dropped the stream. That assumption already cost real time: it produced the escape-heavy
400-record repro in #205, which streamed perfectly and proved only that the hypothesis was wrong.

What is deliberately NOT here: a refusal. A cut answer is still the best answer there is, the chunks
still go to OpenCode unchanged, and the automatic retry is the right response.
test_a_broken_tool_call_ends_the_build_out_loud.py pins that the retry still happens, exactly once.
"""
from __future__ import annotations

import logging
import queue
import re
from pathlib import Path

import sage.shim.keepalive as ka

from .fake_opencode import Turn
from .test_a_broken_tool_call_ends_the_build_out_loud import _of, _orch

# One ordinary content delta, carrying the `"finish_reason": null` every one of them carries.
DELTA = b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
STOP = b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
TOOL_CALLS = b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
CAP = b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'
DONE_FRAME = b"data: [DONE]\n\n"


def _pump(chunks: list[bytes], raises: BaseException | None = None) -> list[object]:
    """Run a whole stream through `pump` and return what the response side would read off the queue.

    Called on this thread rather than the worker one the apps use: `pump` takes the generator and the
    queue and nothing else, so the thread buys the test nothing but a join.
    """
    def gen():
        yield from chunks
        if raises is not None:
            raise raises

    q: queue.Queue = queue.Queue()
    ka.pump(gen(), q)
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_a_stream_that_ends_without_saying_why_is_named_for_that(caplog):
    """The live shape: chunks arrive, then the stream stops, and no chunk ever said it was over."""
    with caplog.at_level(logging.WARNING, logger="sage.shim.stream"):
        drained = _pump([DELTA, DELTA, DELTA, DONE_FRAME])

    assert "no finish_reason" in caplog.text
    # The two numbers that make the line worth reading in /api/diag/log: a cut at 48.1s over 15
    # chunks is a degraded gateway, and one at 0.2s over 1 chunk would be something else entirely.
    assert "4 chunk" in caplog.text
    assert re.search(r"\d+\.\ds", caplog.text), caplog.text
    # Said, not acted on. Every chunk still goes through untouched, and DONE still follows: this is
    # a report, not a gate, and the retry in service.py is what actually answers the fault.
    assert drained == [DELTA, DELTA, DELTA, DONE_FRAME, ka.DONE]


def test_a_healthy_answer_gains_no_line(caplog):
    """A false alarm here would put a warning under every finished build. Both healthy reasons."""
    for ending in (STOP, TOOL_CALLS):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="sage.shim.stream"):
            _pump([DELTA, ending, DONE_FRAME])
        assert caplog.text == ""


def test_a_capped_answer_is_left_to_the_warning_that_already_names_it(caplog):
    """"length" IS a terminal reason, so this is not the fault being reported.

    The cap has its own line ("gateway ended the answer early"), which names the reason. Two
    warnings for one stream would only make the log ring harder to read.
    """
    with caplog.at_level(logging.WARNING, logger="sage.shim.stream"):
        _pump([DELTA, CAP, DONE_FRAME])

    assert "no finish_reason" not in caplog.text


def test_a_stream_that_carried_nothing_at_all_is_a_different_fault(caplog):
    """No chunks is not a cut answer, and it already has its own handling.

    The apps read the eagerly-pulled first item: DONE there means the gateway answered with an empty
    stream, which is a pre-stream failure wearing a 200. Folding it in here would file two unrelated
    faults under one message.
    """
    with caplog.at_level(logging.WARNING, logger="sage.shim.stream"):
        drained = _pump([])

    assert caplog.text == ""
    assert drained == [ka.DONE]


def test_a_stream_that_broke_outright_is_a_different_fault(caplog):
    """An exception mid-stream is put on the queue as ('error', e) and reported where it lands.

    That path already tells the person the gateway closed the stream, so the stream never reaches
    the check below it — and must not, or a broken stream would be reported twice, in two
    vocabularies, for one break.
    """
    boom = ConnectionResetError("upstream went away")
    with caplog.at_level(logging.WARNING, logger="sage.shim.stream"):
        drained = _pump([DELTA], raises=boom)

    assert caplog.text == ""
    assert drained == [DELTA, ("error", boom)]


def test_the_terminal_reasons_are_the_cut_ones_and_the_healthy_ones(caplog):
    """`cut_off_finish_reason` answers "was this cut?". This answers "did it end at all?"."""
    assert ka.terminal_finish_reason(STOP) == "stop"
    assert ka.terminal_finish_reason(TOOL_CALLS) == "tool_calls"
    # A cap ended the stream, however unhappily. Absence is the fault, not the reason's flavour.
    assert ka.terminal_finish_reason(CAP) == "length"
    # And the frames that end nothing: the null every delta carries, [DONE], a keepalive comment,
    # and prose using one of the words — the substring scan is a fast reject, not the answer.
    assert ka.terminal_finish_reason(DELTA) is None
    assert ka.terminal_finish_reason(DONE_FRAME) is None
    assert ka.terminal_finish_reason(b": keepalive\n\n") is None
    assert ka.terminal_finish_reason(
        b'data: {"choices":[{"delta":{"content":"stop the length of it"}}]}\n\n') is None
    # The narrower question is unchanged: a healthy ending is still not a cut.
    assert ka.cut_off_finish_reason(STOP) is None


def test_the_retry_note_does_not_send_the_agent_after_the_size_of_the_write(tmp_path: Path):
    """The note rides the automatic retry, so a wrong cause here is work the agent does for nothing.

    It used to say one very large write was the usual cause and point at `public/data/` — which sends
    the agent to restructure a step that was never the problem. The honest version names the gateway.
    """
    orch, oc = _orch(tmp_path, [Turn(writes={"src/MetricCard.tsx": "card\n"}, broken_write=True),
                                Turn(text="Added dashboard.", writes={"src/App.tsx": "app\n"})])

    list(orch.build_stream("build me a dashboard"))

    note = oc.prompts[1]["text"][len(oc.prompts[0]["text"]):]
    assert "gateway" in note
    for claim in ("very large", "enormous", "smaller", "public/data/"):
        assert claim not in note, f"the retry note still blames size: {claim!r}"


def test_the_give_up_does_not_ask_the_person_for_a_smaller_piece(tmp_path: Path):
    """Same wrong cause, aimed at the person instead. Splitting the request fixes nothing here."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/MetricCard.tsx": "card\n"}, broken_write=True),
                                 Turn(broken_write=True)])

    events = list(orch.build_stream("build me a dashboard"))

    message = _of(events, "error")[0]["message"]
    # The person is told to pick a different model, which is the one thing that helps. Size was
    # the first wrong cause; the gateway is named in the retry note above, not on this line.
    assert "Pick a different model" in message
    for claim in ("too big", "smaller"):
        assert claim not in message, f"the give-up still blames size: {claim!r}"
