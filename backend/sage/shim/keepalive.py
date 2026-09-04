"""Streaming primitives that keep a slow model turn's SSE connection alive.

OpenCode's model provider is Node's fetch (undici): a request that sees no headers/body for too long
is aborted, and the turn dies as "TypeError: network error". gpt-5.4 plan turns go silent for minutes
while thinking. So we drain the (blocking) gateway generator on a worker thread and let the response
side emit SSE keepalive comments during any silent gap, then end a broken stream readably.

Shared by the live endpoint (orchestrator control_app `/v1/chat/completions`) and the standalone shim
app so both behave identically.
"""
from __future__ import annotations

import json
import logging
import os
import queue
from collections.abc import Iterator

# Wait this long for the first upstream byte (or a fast failure) before committing to the stream. A
# pre-stream error inside the budget still returns a clean JSON 502. If nothing arrives (the model is
# just thinking), commit anyway and keep the connection warm below.
FIRST_BYTE_BUDGET_S = 8.0
# During any silent gap emit an SSE comment this often. `: ` lines are ignored by SSE parsers, so they
# don't perturb the OpenAI payload; they only reset the client's (undici's) read timer. Well under any
# reasonable client timeout.
KEEPALIVE_INTERVAL_S = 15.0

# Log the raw SSE chunks coming back from the gateway. This process is the last hop before OpenCode,
# which reports a bad event as an opaque "Invalid sage-gateway/openai-compatible-chat stream event"
# with no payload — so these bytes are the only way to see what a provider actually emitted.
#
# Runtime-togglable (POST /api/diag/debug-stream) rather than env-only: on Domino the env var is baked
# into the image at build time, so an env-only switch costs an Environment rebuild per toggle, which
# is far too slow for chasing an intermittent stream defect. SAGE_DEBUG_STREAM still sets the initial
# state. Off by default — verbose, and the chunks carry prompt and completion text.
_debug_stream = os.environ.get("SAGE_DEBUG_STREAM", "").strip().lower() in ("1", "true", "yes")
# Per-stream cap so one long turn can't push everything else out of /api/diag's 400-line ring. A
# stream that OpenCode rejects dies within a second or two, so the interesting chunks are the first.
DEBUG_STREAM_MAX_CHUNKS = 40
DEBUG_STREAM_MAX_BYTES = 400


def debug_stream_enabled() -> bool:
    return _debug_stream


def set_debug_stream(on: bool) -> bool:
    global _debug_stream
    _debug_stream = bool(on)
    return _debug_stream

KEEPALIVE = b": keepalive\n\n"
DONE = object()   # producer sentinel: the gateway generator was exhausted cleanly
EMPTY = object()  # get() timed out with no item (a silent gap)


def pump(gen: Iterator[bytes], q: queue.Queue) -> None:
    """Drain the (blocking) gateway generator into a queue on a worker thread so the response side can
    interleave keepalives during silent gaps. Puts raw chunk bytes, then DONE, or ('error', exc) if the
    upstream stream breaks. Note: not cancelled on client disconnect — runs until the gateway
    completes/errors (the same read=None exposure the direct stream already had)."""
    log = logging.getLogger("sage.shim.stream")
    seen = 0
    try:
        for chunk in gen:
            if _debug_stream and seen < DEBUG_STREAM_MAX_CHUNKS:
                seen += 1
                log.info("stream chunk %d: %r", seen, chunk[:DEBUG_STREAM_MAX_BYTES])
            q.put(chunk)
        if _debug_stream:
            log.info("stream done after %d chunk(s)", seen)
        q.put(DONE)
    except BaseException as e:  # GatewayUpstreamError, httpx ReadError/RemoteProtocolError, etc.
        q.put(("error", e))


def get(q: queue.Queue, timeout: float):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return EMPTY


def is_error(item: object) -> bool:
    return isinstance(item, tuple) and len(item) == 2 and item[0] == "error"


def upstream_error(chunk: bytes) -> str | None:
    """The provider's error message, when a chunk is an error payload rather than a completion chunk.

    The gateway can answer a request the provider rejected with HTTP 200 and a single
    `data: {"error": {...}}` frame, then close — no `[DONE]`, no exception anywhere. The stream looks
    healthy right up until the client parses it, and OpenCode reports only "Invalid ...
    openai-compatible-chat stream event" with no payload, which is unactionable. Recognising the shape
    here is what turns it into a message a person can read (see error_sse).

    Callers must run this on EVERY chunk, not only the eagerly-pulled first one. The frame arrives
    whenever the provider gets round to failing, and a model that thinks for longer than
    FIRST_BYTE_BUDGET_S has already committed the stream by then (see the callers in
    orchestrator/app.py and shim/app.py).

    Observed live: a Bedrock ValidationException, a Gemini missing-thought_signature 400, and a bare
    gateway `'list' object has no attribute 'get'`. Returns None for an ordinary chunk.
    """
    # Fast reject before any parsing: an error frame always carries the literal `"error"` key, and
    # this now runs against every chunk of every stream — without it each content delta would pay a
    # json.loads on the hot path.
    if b'"error"' not in chunk:
        return None
    for line in chunk.split(b"\n"):
        payload = line.strip()
        if not payload.startswith(b"data:"):
            continue
        payload = payload[len(b"data:"):].strip()
        if not payload.startswith(b"{"):  # skips [DONE] and SSE comments
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        err = obj.get("error") if isinstance(obj, dict) else None
        if err is None:
            continue
        return str(err.get("message") or err) if isinstance(err, dict) else str(err)
    return None


# How many assistant tool-call messages the debug listing shows before it stops.
DEBUG_REQUEST_MAX_CALLS = 20


def tool_call_signatures(messages: object) -> list[str]:
    """One entry per assistant message that holds tool calls, listing each call's signature state.

    Grouped BY MESSAGE, not flattened per call, because the grouping is the whole diagnosis.
    Verified live against the dogfood gateway on 2026-09-04 (#155): Gemini signs a parallel batch
    ONCE, on the first call, and accepts it back that way — `[sig=408, sig=NONE]` in one message is
    healthy. Split the same batch across two assistant messages and it is rejected. So a flat list
    of calls cannot tell the healthy shape from the broken one; only the grouping can.

    Deliberately NOT a raw body dump. The request carries the whole conversation, so dumping it
    would bury /api/diag's 400-line ring in prompt text and leak far more than the question needs.

    This is the only place the outgoing side is visible at all — SAGE_DEBUG_STREAM shows the
    gateway's responses, and OpenCode's own request is otherwise unobservable from here, which is
    exactly why #155 stayed ambiguous for so long.
    """
    if not isinstance(messages, list):
        return []
    out: list[str] = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        calls = [c for c in (m.get("tool_calls") or []) if isinstance(c, dict)]
        if not calls:
            continue
        parts = []
        for call in calls:
            fn = call.get("function")
            name = (fn.get("name") if isinstance(fn, dict) else None) or "?"
            sig = _signature(call)
            parts.append("{}/{} sig={}".format(
                call.get("id") or "?", name, len(sig) if sig else "NONE"))
        out.append(f"msg{i}[{', '.join(parts)}]")
    return out


def _signature(call: dict) -> str | None:
    extra = call.get("extra_content")
    google = extra.get("google") if isinstance(extra, dict) else None
    sig = google.get("thought_signature") if isinstance(google, dict) else None
    return sig if isinstance(sig, str) and sig else None


def unsigned_tool_messages(model: object, messages: object) -> int:
    """Assistant tool-call messages whose FIRST call carries no signature — the shape Gemini rejects.

    The predicate is per MESSAGE, not per call, and that distinction is the finding. Signing is a
    property of the model turn: Gemini puts one signature on the first call of a parallel batch and
    accepts the batch back unchanged, so a later call in the same message having none is normal and
    must not be reported. A tool-call message whose first call is bare is the rejected shape, and it
    has two live causes: history written by a model that does not sign at all (the common one — the
    shim re-resolves the model per request, so a phase that ran on sonnet leaves `toolu_*` calls in
    a session OpenCode believes is Gemini's), or a signed batch taken apart across messages.

    Counted only for Gemini — the one model on the gateway that signs at all. Every other alias
    sends no signature ever, so applying this anywhere else would fire on every ordinary turn. The
    asymmetry is one-directional: sonnet and gpt-5.4 both accept Gemini's `extra_content` back
    unchanged (verified live), so only the Gemini-bound direction needs watching.

    Reproduced end to end on 2026-09-04 (#155): plan on sonnet, then an implement turn on Gemini in
    the same session, which returns the user's verbatim `default_api:bash` 400.

    DELIBERATELY BROADER than the gateway's own rule. Probed live on 2026-09-04: the same unsigned
    history is accepted (HTTP 200) when the LAST message is a user turn, and rejected (400) when the
    model has to continue from a tool result. So the first request of a turn would slip through and
    every request after it in that turn would fail. Reading the last role would buy one Gemini reply
    and then break the turn anyway, mid-flight, with the model changing under the agent. Refusing
    the whole session is the conservative direction and the stable one (ADR-0032).
    """
    bare_model = str(model).rsplit("/", 1)[-1].lower()
    if "gemini" not in bare_model or not isinstance(messages, list):
        return 0
    gap = 0
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        calls = [c for c in (m.get("tool_calls") or []) if isinstance(c, dict)]
        if calls and not _signature(calls[0]):
            gap += 1
    return gap


def error_sse(message: str) -> Iterator[bytes]:
    """End an already-committed stream (200 headers sent) READABLY: emit `message` as an assistant
    content delta, a stop finish, then [DONE]. OpenCode renders it as text and closes the turn cleanly
    instead of crashing on a truncated body ('TypeError: network error')."""
    delta = {"id": "sage-error", "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {"content": message}, "finish_reason": None}]}
    stop = {"id": "sage-error", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    yield f"data: {json.dumps(delta)}\n\n".encode()
    yield f"data: {json.dumps(stop)}\n\n".encode()
    yield b"data: [DONE]\n\n"
