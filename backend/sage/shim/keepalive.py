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
import time
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
    # Counted on every stream now, not only a debugged one: the count is half of what makes the
    # unterminated-stream warning below readable (the tool-call cut is 5 chunks, then a 60s gap).
    seen = 0
    started = time.monotonic()
    ended = None  # the first terminal finish_reason any chunk carried, if one ever did
    try:
        for chunk in gen:
            seen += 1
            if _debug_stream and seen <= DEBUG_STREAM_MAX_CHUNKS:
                log.info("stream chunk %d: %r", seen, chunk[:DEBUG_STREAM_MAX_BYTES])
            if ended is None:
                ended = terminal_finish_reason(chunk)
            q.put(chunk)
        if _debug_stream:
            log.info("stream done after %d chunk(s)", seen)
        # Measured live (2026-09-07; mechanism pinned 2026-09-08, gateway-questions.md bug 3).
        # The gateway does not stream a tool call's argument deltas: it sends the preamble (role,
        # id, function name) and then buffers the whole argument, during which the connection
        # carries no bytes. At 60.0s of silence the connection is torn down, and since `200` and
        # several chunks are already on the wire no error can be reported — the stream just stops,
        # with no terminal finish_reason anywhere in it. This is NOT the load effect the first note
        # here claimed: reproduced 13/13 at concurrency 1, 4 and 8 alike, and the tools-off control
        # streams 316 chunks over a LONGER 65.7s and finishes clean, so the timer is idle-based
        # rather than a duration cap. The generator is simply exhausted, so nothing raises, `[DONE]`
        # is not required for a client to cope, and the fault surfaces four layers away as
        # OpenCode's "Invalid JSON input for openai-chat tool call write" — a message about JSON,
        # for a fault that has nothing to do with JSON. This line is what tells the two apart in
        # /api/diag/log: a cap says finish_reason="length" (and shim/app.py names it), a healthy
        # answer says "stop" or "tool_calls", and a cut stream says nothing at all.
        #
        # Guarded on `seen` because a stream that carried NO chunks is a different fault — a
        # pre-stream failure wearing a 200 — and the eager first pull in both apps already handles
        # it. A stream that broke outright never reaches here: it leaves through the except below,
        # which is reported where it lands.
        if seen and ended is None:
            log.warning("gateway ended the stream with no finish_reason after %.1fs and %d chunk(s)"
                        " — the answer stops wherever it stopped, and a tool call caught by that "
                        "arrives with arguments that do not parse", time.monotonic() - started, seen)
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


def guardrail_frame(chunk: bytes) -> bool:
    """Whether an error frame inside a 200 stream is a guardrail refusal. A PROBE, not a guard.

    Nothing branches on this — `orchestrator.app.relay` logs it and carries on. It exists to answer
    one open question from real traffic: can a guardrail refuse on this path at all? Every guardrail
    refusal captured so far has been a RAISED `GatewayUpstreamError`, which is what
    `shim.enforcement._capture_refusal` hangs off. If one can arrive as a frame instead, then on that
    path the payload is never captured, `project.last_refused` stays None, and the withhold search
    returns at its first line saying nothing (`service.py:7618`). If the log line never fires, the
    gap is theoretical and nothing needs building.

    Reads the marker off the RAW chunk, deliberately. `upstream_error` above returns `err["message"]`
    alone, so a sibling `guardrail_blocked` — the gateway's machine field, and the one
    `_capture_refusal` keys on — never reaches a caller. Asking the message would answer False for a
    frame that IS a guardrail, and a probe that under-reports is worse than no probe: it would close
    the question with the wrong answer.

    Loose on the marker and strict on the frame. `guardrail` anywhere, because the shape this path
    would carry has never been captured and matching a guessed one exactly would miss the real one;
    but only inside something `upstream_error` already parsed as an error frame, so a model writing
    the word in its own prose is not mistaken for a refusal.
    """
    return upstream_error(chunk) is not None and b"guardrail" in chunk.lower()


# Finish reasons that mean the answer was cut off rather than finished. The healthy ones — "stop"
# and "tool_calls" — are deliberately absent: this only ever reports a cut.
CUT_OFF_FINISH_REASONS = ("length", "max_tokens", "content_filter")
# Every reason that means the stream reached an end, cut or not. The set whose ABSENCE is the fault
# `pump` reports: a stream carrying none of these did not finish, it stopped.
TERMINAL_FINISH_REASONS = CUT_OFF_FINISH_REASONS + ("stop", "tool_calls")


def _finish_reason(chunk: bytes, wanted: tuple[str, ...]) -> str | None:
    """The first finish_reason on a chunk that is one of `wanted`. None otherwise."""
    # Fast reject before any parsing, for the same reason upstream_error has one: this runs against
    # every chunk of every stream, and an OpenAI-style content delta carries `"finish_reason": null`
    # on each one, so keying off the field name would json.loads the whole hot path. Keying off the
    # values costs a substring scan and rejects almost every healthy chunk. A false hit — the word
    # "length", or "stop", inside prose the model is writing — falls through to the parse below and
    # is rejected there, because that parse reads `choices[].finish_reason` rather than the text.
    # "stop" is common enough English that `terminal_finish_reason`'s callers pay this on a handful
    # of chunks per stream; measured against the alternative, which is parsing every chunk, it is
    # still the cheap side.
    if not any(v.encode() in chunk for v in wanted):
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
        if not isinstance(obj, dict):
            continue
        for choice in obj.get("choices") or []:
            reason = choice.get("finish_reason") if isinstance(choice, dict) else None
            if reason in wanted:
                return str(reason)
    return None


def terminal_finish_reason(chunk: bytes) -> str | None:
    """The finish_reason on a chunk when it ends the answer — the cut reasons and the healthy ones.

    `cut_off_finish_reason` below answers "was this answer cut short?". This answers the flatter
    question "did this stream say it was over at all?", which is what `pump` needs: a gateway that
    stops sending mid-tool-call emits no terminal reason of any kind, and telling that apart from a
    cap needs the healthy reasons counted too (see the warning in `pump`).
    """
    return _finish_reason(chunk, TERMINAL_FINISH_REASONS)


def tool_names(chunk: bytes) -> list[str]:
    """The tool names a chunk announces, or [] when it announces none.

    The ledger could already say that a step COST 6.4 seconds and could never say what it spent them
    on, so which steps to cut had to be guessed at from chunk counts. That guess is the expensive
    kind: it looks well-founded and is not checkable.

    A name arrives once per tool call, in the delta that opens it — the arguments stream in after,
    under the same index and with no name — so reading only the opening delta counts each call once
    without accumulating anything. Order is kept: a step that reads twice before writing is a
    different step from one that writes twice, and the sequence is the part worth seeing.

    Deliberately not a general SSE parser, for the reason `sniff` gives for using a substring: the
    byte check rejects the whole hot path first, and only a chunk that already says `tool_calls`
    pays for a parse. Silence is normal — a text-only step announces nothing.
    """
    if b'"tool_calls"' not in chunk:
        return []
    found: list[str] = []
    for line in chunk.split(b"\n"):
        payload = line.strip()
        if not payload.startswith(b"data:"):
            continue
        payload = payload[len(b"data:"):].strip()
        if not payload.startswith(b"{"):   # skips [DONE] and SSE comments
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        for choice in obj.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function")
                name = (fn or {}).get("name") if isinstance(fn, dict) else None
                if name:
                    found.append(str(name))
    return found


def usage_tokens(chunk: bytes) -> tuple[int | None, int | None] | None:
    """`(input_tokens, cached_tokens)` off a `usage` frame, or None when this chunk carries none.

    Not every provider sends one. Measured against the dogfood gateway on 2026-09-11: gpt-5.4 emits
    a final `usage` frame (on a choices-empty chunk) and sonnet emits none at all, with or without
    `stream_options.include_usage` on the request. So a caller must treat silence as normal rather
    than as a fault — Chat runs on sonnet, and reading nothing there is the expected answer, not a
    parse that went wrong.

    Two spellings, because the field name differs by provider behind the one gateway: OpenAI-shape
    `prompt_tokens` / `prompt_tokens_details.cached_tokens`, and Anthropic-shape `input_tokens` /
    `cache_read_input_tokens`. Reading only one of them is how a real number looks like an absence.

    Keyed off the field name rather than a value, unlike `_finish_reason` above: `usage` is a rare
    word in a model's prose while "stop" and "length" are common, and the frame that carries it is
    one chunk out of hundreds, so the substring rejects the whole hot path just as cheaply.
    """
    if b'"usage"' not in chunk:
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
        if not isinstance(obj, dict):
            continue
        # `"usage": null` rides along on every chunk of a gpt-5.4 stream until the last one, so the
        # key being present is not the same as a number being there.
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            continue
        read = usage.get("prompt_tokens")
        if read is None:
            read = usage.get("input_tokens")
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if cached is None:
            cached = usage.get("cache_read_input_tokens")
        if read is None and cached is None:
            continue
        return (read, cached)
    return None


def cut_off_finish_reason(chunk: bytes) -> str | None:
    """The finish_reason on a chunk, when it says the answer was cut off rather than completed.

    Nothing in the shim read finish_reason before this, so an answer truncated at the output cap
    left no trace at all: the stream ends cleanly, `[DONE]` arrives, and the only symptom shows up
    a layer away — OpenCode fails the session with "Invalid JSON input for ... tool call write"
    because the arguments string stopped mid-token. `_unparsed_tool_input` in orchestrator/service.py
    is what catches that end of it, and the tool name is all it can say.

    One line here is the difference between the two causes that look identical from there: an
    output cap (raise it, or ask for a smaller write) and a bad escape inside arguments that were
    sent whole (a prompt problem, no cap involved). The declared caps in opencode.json cannot
    answer it either — they describe the alias OpenCode assumed, not the model the shim routed to.

    Returns None for an ordinary chunk, including one carrying `"finish_reason": null` or a
    healthy "stop".
    """
    return _finish_reason(chunk, CUT_OFF_FINISH_REASONS)


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
