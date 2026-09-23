"""#506: the gateway's own sentence reaches the log ring; only the class reaches the stream.

A Build turn died with `The model stream failed: invalid_request_error` while the gateway had
said "Function tools with reasoning_effort are not supported for gpt-5.6-sol in
/v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort ...".
That sentence named the cause, the fix, and an upstream model nobody knew the alias had been
re-pointed to. None of it survived the classification at `events.py`.

The stream still carries the class alone, because the error event flows back into OpenCode's
session history and is sent to the model on the next turn. The ring is the other channel: local
to the workspace, already readable by the person whose workspace it is, read by no model.
"""
import json
import logging

import pytest

from sage.gateway.events import MAX_LOGGED_ERROR_CHARS, StreamEvents
from sage.gateway.protocol import Protocol

REASON = ("Function tools with reasoning_effort are not supported for gpt-5.6-sol in "
          "/v1/chat/completions. To use function tools, use /v1/responses or set "
          "reasoning_effort to none.")


def sse(event: dict) -> bytes:
    return b"data: " + json.dumps(event).encode() + b"\n\n"


def refusal(message: str = REASON) -> dict:
    return {"type": "error", "error": {"type": "invalid_request_error", "message": message}}


def ring(caplog) -> list[logging.LogRecord]:
    """What `api/diag/log` would show: records from a `sage.*` logger, at WARNING or above.

    Keyed on the `sage.` prefix rather than on `sage.gateway`, because the ring handler is
    attached to the `sage` parent and takes whatever propagates to it. A line logged under a
    name outside that hierarchy would never reach the panel, and must not count here either.
    """
    return [r for r in caplog.records
            if r.name.startswith("sage.") and r.levelno >= logging.WARNING]


@pytest.fixture(autouse=True)
def _capture(caplog):
    caplog.set_level(logging.WARNING, logger="sage")


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.CHAT, Protocol.RESPONSES])
def test_the_refusal_sentence_reaches_the_log(caplog, protocol):
    """(a) The body reaches the ring for a classified error, on every lane.

    The whole sentence, not a prefix of it: the fix ("use /v1/responses") sits at the end, and
    an upstream model name nobody expected sits in the middle.
    """
    events = StreamEvents(protocol)
    events.feed(sse(refusal()))
    said = "\n".join(r.getMessage() for r in ring(caplog))
    assert REASON in said, said
    assert "gpt-5.6-sol" in said and "/v1/responses" in said, said


def test_a_body_with_no_error_object_is_still_logged(caplog):
    """The RESPONSES lane can refuse with a bare top-level message and no `error` object, which
    is the shape that classifies as `upstream_error`. That is the case with the least left in
    the stream, so it is the one that most needs the ring."""
    events = StreamEvents(Protocol.RESPONSES)
    events.feed(sse({"type": "error", "message": "tenant quota exhausted for project 42"}))
    assert events.error == "upstream_error"
    said = "\n".join(r.getMessage() for r in ring(caplog))
    assert "tenant quota exhausted for project 42" in said, said


def test_an_enormous_body_cannot_evict_the_rest_of_the_turn(caplog):
    """The ring holds 400 lines and an event may be megabytes. Clipped, not dropped."""
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(sse(refusal("x" * (MAX_LOGGED_ERROR_CHARS * 4))))
    said = "\n".join(r.getMessage() for r in ring(caplog))
    assert "x" * 100 in said
    assert len(said) < MAX_LOGGED_ERROR_CHARS * 2, len(said)


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.CHAT, Protocol.RESPONSES])
def test_the_stream_still_carries_the_class_alone(caplog, protocol):
    """(b) The value that rides back into OpenCode's session history did not change.

    `self.error` is the class and nothing else, and no parser state holds the body — the next
    turn shows the model this object's error, and an upstream sentence can echo a prompt or an
    opaque signature.
    """
    events = StreamEvents(protocol)
    events.feed(sse(refusal()))
    assert events.error == "invalid_request_error"
    assert "reasoning_effort" not in repr(events)
    assert "gpt-5.6-sol" not in repr(events)
    assert REASON not in repr(events)


def test_a_refusal_split_across_chunks_is_logged_once(caplog):
    """(c) One line per error event, not per chunk.

    A refusal arriving in five TCP reads is one refusal. Counting chunks would report five
    failures for one, and a reader counting lines to tell a blip from a wedged gateway would
    be counting the network instead.
    """
    frame = sse(refusal())
    events = StreamEvents(Protocol.MESSAGES)
    for start in range(0, len(frame), 7):
        events.feed(frame[start:start + 7])
    assert [r.getMessage() for r in ring(caplog)] != []
    assert len(ring(caplog)) == 1, [r.getMessage() for r in ring(caplog)]


def test_every_error_event_gets_its_own_line(caplog):
    """The other half of (c): an overloaded gateway emits many, and each is a real refusal.

    Three events in ONE chunk — so this fails both for a logger that fires per chunk and for
    one that fires once per stream.
    """
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(b"".join(sse(refusal(f"refusal {n}")) for n in range(3)))
    said = [r.getMessage() for r in ring(caplog)]
    assert len(said) == 3, said
    assert [n for n in range(3) if f"refusal {n}" in "\n".join(said)] == [0, 1, 2], said


def test_a_healthy_stream_says_nothing(caplog):
    """The ring rolls. A parser that warned on ordinary events would push the failing line out
    of it, which is the defect this fix exists to end."""
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(sse({"type": "message_start", "message": {"usage": {"input_tokens": 5}}}))
    events.feed(sse({"type": "message_stop"}))
    events.finish()
    assert ring(caplog) == []
