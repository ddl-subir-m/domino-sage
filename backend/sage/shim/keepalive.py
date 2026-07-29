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

KEEPALIVE = b": keepalive\n\n"
DONE = object()   # producer sentinel: the gateway generator was exhausted cleanly
EMPTY = object()  # get() timed out with no item (a silent gap)


def pump(gen: Iterator[bytes], q: "queue.Queue") -> None:
    """Drain the (blocking) gateway generator into a queue on a worker thread so the response side can
    interleave keepalives during silent gaps. Puts raw chunk bytes, then DONE, or ('error', exc) if the
    upstream stream breaks. Note: not cancelled on client disconnect — runs until the gateway
    completes/errors (the same read=None exposure the direct stream already had)."""
    try:
        for chunk in gen:
            q.put(chunk)
        q.put(DONE)
    except BaseException as e:  # GatewayUpstreamError, httpx ReadError/RemoteProtocolError, etc.
        q.put(("error", e))


def get(q: "queue.Queue", timeout: float):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return EMPTY


def is_error(item: object) -> bool:
    return isinstance(item, tuple) and len(item) == 2 and item[0] == "error"


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
