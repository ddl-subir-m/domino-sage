"""Can a guardrail refuse on the 200-stream path at all? The probe that will answer it.

The refusal capture that feeds `refusal_scan` and the withhold search hangs off a RAISED
`GatewayUpstreamError` (`shim.enforcement._capture_refusal`). The gateway has a second way to
refuse: HTTP 200 and a single `data: {"error": {...}}` frame, which `keepalive.upstream_error`
recognises and `orchestrator.app.relay` handles. Nothing raises there, so on that path the payload
is never captured, `project.last_refused` stays None, and `_withhold_search` returns at its first
line without a word (`service.py:7618`).

Whether a GUARDRAIL ever arrives that way is now MEASURED, and it does not. `Block PII` scans the
request and answers 400 before a response byte exists — with `stream: true`, and at 400KB with the
match in the last line, where a gateway that committed headers early would have had to refuse in a
frame. It never commits. And the response is not scanned at all: the model will emit
`1234567890`, `7777777777777777`, `jane.doe@example.com` and `555-123-4567` for you, each a
confirmed input-side refusal, each clean coming back. The rows are in `ka.guardrail_frame`.

So the predicate stays as a tripwire against a config change, not as a fix for a live gap. The
guardrail set belongs to the Domino administrator, and an output-side one added later is the only
thing that could put a refusal on this path — where nothing Sage has would capture it.

The predicate reads the RAW chunk rather than `upstream_error`'s answer, and that is the whole
point of it existing. `upstream_error` returns `err["message"]` alone (keepalive.py:154), so the
sibling `guardrail_blocked` — the gateway's machine field, and the one `_capture_refusal` keys on —
is dropped before any caller sees it. A probe asking the message would report "no guardrail here"
for a frame that is one, which is the same false negative that turned one live refusal into an
afternoon.
"""
from __future__ import annotations

from sage.shim import keepalive as ka

# A guardrail's machine field, as `_capture_refusal` reads it: the gateway's own marker, measured
# there as byte-identical whatever matched.
MACHINE_FIELD = (b'data: {"error": {"message": "Blocked by guardrail: Block PII", '
                 b'"type": "guardrail_blocked"}}\n\n')
# The same refusal with the marker only in the human sentence. Both shapes count, because which one
# this path carries is exactly what is not known yet.
SENTENCE_ONLY = b'data: {"error": {"message": "Blocked by guardrail: Block PII"}}\n\n'


def test_a_guardrail_frame_is_recognised_by_its_machine_field():
    """The shape `upstream_error` would hide: the marker is a sibling of `message`, so a probe
    reading the message alone answers False for a frame that IS a guardrail."""
    assert ka.upstream_error(MACHINE_FIELD) == "Blocked by guardrail: Block PII"
    assert b"guardrail_blocked" not in ka.upstream_error(MACHINE_FIELD).encode()
    assert ka.guardrail_frame(MACHINE_FIELD) is True


def test_a_guardrail_frame_is_recognised_by_its_sentence_too():
    assert ka.guardrail_frame(SENTENCE_ONLY) is True


def test_an_ordinary_error_frame_is_not_a_guardrail():
    """The three failures `upstream_error` was built for. None is a guardrail, and a probe that
    called them one would report a gap that is not there."""
    for frame in (
        b'data: {"error": {"message": "ValidationException: bad input"}}\n\n',
        b'data: {"error": {"message": "\'list\' object has no attribute \'get\'"}}\n\n',
        b'data: {"error": {"message": "missing thought_signature"}}\n\n',
    ):
        assert ka.guardrail_frame(frame) is False


def test_an_ordinary_chunk_is_not_a_guardrail():
    """Runs on every chunk of every stream, so an answer here must be cheap and must not fire on a
    model that merely says the word."""
    assert ka.guardrail_frame(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n') is False
    assert ka.guardrail_frame(b"data: [DONE]\n\n") is False
    assert ka.guardrail_frame(
        b'data: {"choices":[{"delta":{"content":"a guardrail blocked it"}}]}\n\n') is False
