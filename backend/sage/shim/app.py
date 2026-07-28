"""FastAPI HTTP entrypoint for the enforcement shim (SPEC.md C4, PLAN Step 1.1).

OpenCode points its OpenAI base_url at this app. Every request:
  1. resolve the model decision (router),
  2. override the model when locked,
  3. tag with project + phase,
  4. forward to the gateway and stream back.

Run:  uvicorn sage.shim.app:app --port 8080
Env:
  GATEWAY_BASE_URL / GATEWAY_API_KEY  -> use the real Domino gateway (Step 1.1 verify)
  (unset)                             -> FakeGatewayClient, so `curl` works with no creds

Project/phase come from headers so OpenCode's vanilla OpenAI body stays untouched:
  X-Sage-Project, X-Sage-Phase (default project "unknown", phase from ModelControl).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from collections.abc import Iterator

from dotenv import load_dotenv

load_dotenv()  # backend/.env (gateway creds + model aliases); no-op if absent

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..gateway.client import GatewayUpstreamError
from ..gateway.factory import build_gateway
from ..router.model_control import ModelControl
from ..router.models import ModelCatalog, Mode, Phase
from .enforcement import EnforcementShim

log = logging.getLogger("sage.shim")
logging.basicConfig(level=logging.INFO)

_gateway, GATEWAY_MODE = build_gateway()

# TODO(Step 4.2+): SessionState is per project/session. For the spike, one process-wide control.
# Defaults are gateway alias names (see MODELS.md). Sovereign tier = "Domino Platform"
# provider models (qwen-2-5, local-domino-llm). Override per deployment via env.
_catalog = ModelCatalog(
    sovereign_plan=os.environ.get("SAGE_MODEL_SOVEREIGN_PLAN", "qwen-2-5"),            # on-Domino, sovereign
    sovereign_implement=os.environ.get("SAGE_MODEL_SOVEREIGN_IMPLEMENT", "qwen-2-5"),  # on-Domino, sovereign
    sovereign_ask=os.environ.get("SAGE_MODEL_SOVEREIGN_ASK", "qwen-2-5"),      # on-Domino, sovereign
    plan=os.environ.get("SAGE_MODEL_PLAN", "gpt-5.4"),                   # strong, plan phase
    implement=os.environ.get("SAGE_MODEL_IMPLEMENT", "bedrock-qwen3-coder"),  # cheap coder
    ask=os.environ.get("SAGE_MODEL_ASK", "sonnet"),
)
_control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
# Spike helper (Step 1.3): SAGE_FORCE_SENSITIVITY_LOCK=1 starts locked so you can verify the
# sovereign override live — any request, whatever model OpenCode asks for, routes to sovereign.
if os.environ.get("SAGE_FORCE_SENSITIVITY_LOCK") in {"1", "true", "yes"}:
    _control.on_assets_changed([True])
_shim = EnforcementShim(_control, _catalog, _gateway, force_model=(GATEWAY_MODE == "openai"))

app = FastAPI(title="sage enforcement shim")


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {"ok": True, "gateway_mode": GATEWAY_MODE, "locked": _control.locked}


# Default the project tag from the Domino project context so cost doesn't land in "unknown".
_DEFAULT_PROJECT = os.environ.get("DOMINO_PROJECT_NAME", "unknown")

# Give the upstream this long to produce its first byte (or fail) before we commit to the stream.
# A fast failure (auth bounce, unknown model) within the budget still returns a clean JSON 502, as
# before. If the model is merely thinking with no byte yet, we commit anyway and keep the connection
# warm (below) rather than keep withholding the response.
_FIRST_BYTE_BUDGET_S = 8.0
# During any silent gap (before the first byte, or between chunks), emit an SSE comment this often.
# OpenCode's model provider is Node's fetch (undici), which aborts a request that sees no
# headers/body for too long -> its stream dies as "TypeError: network error". A comment line resets
# that client-side timer without perturbing the OpenAI SSE payload (parsers ignore `:` lines). Well
# under any reasonable client timeout. gpt-5.4 plan turns routinely go silent for minutes mid-think.
_KEEPALIVE_INTERVAL_S = 15.0

_DONE = object()   # producer sentinel: the gateway generator was exhausted cleanly
_EMPTY = object()  # queue.get timed out with no item (a silent gap)


def _pump(gen: Iterator[bytes], q: "queue.Queue") -> None:
    """Drain the (blocking) gateway generator into a queue on a worker thread so the response side
    can interleave keepalives during silent gaps. Puts raw chunk bytes, then _DONE, or ('error', exc)
    if the upstream stream breaks. Note: not cancelled on client disconnect — it runs until the
    gateway completes/errors (same read=None exposure the direct stream already had)."""
    try:
        for chunk in gen:
            q.put(chunk)
        q.put(_DONE)
    except BaseException as e:  # GatewayUpstreamError, httpx ReadError/RemoteProtocolError, etc.
        q.put(("error", e))


def _get(q: "queue.Queue", timeout: float):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return _EMPTY


def _is_error(item: object) -> bool:
    return isinstance(item, tuple) and len(item) == 2 and item[0] == "error"


def _error_sse(message: str) -> Iterator[bytes]:
    """End a stream we've ALREADY committed to (200 headers sent) READABLY: emit `message` as an
    assistant content delta, a stop finish, then [DONE]. OpenCode renders it as text and closes the
    turn cleanly, instead of crashing on a truncated body ('TypeError: network error')."""
    delta = {"id": "sage-error", "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {"content": message}, "finish_reason": None}]}
    stop = {"id": "sage-error", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    yield f"data: {json.dumps(delta)}\n\n".encode()
    yield f"data: {json.dumps(stop)}\n\n".encode()
    yield b"data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_sage_project: str = Header(default=_DEFAULT_PROJECT),
    x_sage_session: str | None = Header(default=None),
):
    body = await request.json()
    requested = body.get("model")
    gen = _shim.handle(body, project=x_sage_project, session=x_sage_session)

    q: "queue.Queue" = queue.Queue()
    started = time.monotonic()
    threading.Thread(target=_pump, args=(gen, q), daemon=True).start()

    # Bounded eager pull: wait a short budget for the first byte (or a fast failure). A pre-stream
    # error inside the budget -> clean JSON 502, exactly as before. If nothing arrives (the model is
    # just thinking), commit to the stream anyway; the generator keeps the connection warm with
    # keepalives so OpenCode's fetch doesn't abort during the silent gap.
    first = await asyncio.to_thread(_get, q, _FIRST_BYTE_BUDGET_S)
    if _is_error(first):
        e = first[1]
        if isinstance(e, GatewayUpstreamError):
            log.error("gateway %s for requested model %r: %s", e.status, requested, e.body)
            return JSONResponse(status_code=502, content={"error": {"message": str(e), "upstream_status": e.status}})
        log.error("shim upstream failure (requested model %r): %s", requested, e)
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})

    log.info(
        "routed request (requested=%s project=%s) -> streaming (first byte %.1fs%s)",
        requested, x_sage_project, time.monotonic() - started,
        ", pending; keepalive engaged" if first is _EMPTY else "",
    )

    def stream() -> Iterator[bytes]:
        if first is _DONE:
            return
        if first is not _EMPTY:
            yield first  # the first real chunk the eager pull already consumed
        while True:
            item = _get(q, _KEEPALIVE_INTERVAL_S)
            if item is _EMPTY:
                yield b": keepalive\n\n"  # SSE comment: ignored by the parser, resets the client's read timer
                continue
            if item is _DONE:
                return
            if _is_error(item):
                e = item[1]
                log.warning(
                    "gateway stream broke mid-response after %.1fs (%s): %s",
                    time.monotonic() - started, type(e).__name__, e,
                )
                yield from _error_sse(
                    f"\n\n⚠️ The model gateway closed the stream mid-response ({type(e).__name__}). "
                    "This is usually an upstream idle or duration limit — please retry."
                )
                return
            yield item

    return StreamingResponse(stream(), media_type="text/event-stream")
