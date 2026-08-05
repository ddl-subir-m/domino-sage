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
from starlette.concurrency import run_in_threadpool

from ..gateway.client import GatewayUpstreamError
from ..gateway.factory import build_gateway
from ..preview.prefix import domino_project_label
from ..router.model_control import ModelControl
from ..router.models import Mode, ModelCatalog, Phase
from . import keepalive as ka
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
_shim = EnforcementShim(_control, _catalog, _gateway, force_model=(GATEWAY_MODE == "openai"),
                        project_name=domino_project_label(fallback="unknown"))

app = FastAPI(title="sage enforcement shim")


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {"ok": True, "gateway_mode": GATEWAY_MODE, "locked": _control.locked}


# Default the project tag from the Domino project context so cost doesn't land in "unknown".
_DEFAULT_PROJECT = os.environ.get("DOMINO_PROJECT_NAME", "unknown")


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_sage_project: str = Header(default=_DEFAULT_PROJECT),
    x_sage_session: str | None = Header(default=None),
):
    body = await request.json()
    requested = body.get("model")
    gen = _shim.handle(body, project=x_sage_project, session=x_sage_session)

    q: queue.Queue = queue.Queue()
    started = time.monotonic()
    threading.Thread(target=ka.pump, args=(gen, q), daemon=True).start()

    # Bounded eager pull: wait a short budget for the first byte (or a fast failure). A pre-stream
    # error inside the budget -> clean JSON 502, exactly as before. If nothing arrives (the model is
    # just thinking), commit to the stream anyway; the generator keeps the connection warm with
    # keepalives so OpenCode's fetch doesn't abort during the silent gap.
    first = await run_in_threadpool(ka.get, q, ka.FIRST_BYTE_BUDGET_S)
    if ka.is_error(first):
        e = first[1]
        if isinstance(e, GatewayUpstreamError):
            log.error("gateway %s for requested model %r: %s", e.status, requested, e.body)
            return JSONResponse(status_code=502, content={"error": {"message": str(e), "upstream_status": e.status}})
        log.error("shim upstream failure (requested model %r): %s", requested, e)
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})

    log.info(
        "routed request (requested=%s project=%s) -> streaming (first byte %.1fs%s)",
        requested, x_sage_project, time.monotonic() - started,
        ", pending; keepalive engaged" if first is ka.EMPTY else "",
    )

    def stream() -> Iterator[bytes]:
        if first is ka.DONE:
            return
        if first is not ka.EMPTY:
            yield first  # the first real chunk the eager pull already consumed
        while True:
            item = ka.get(q, ka.KEEPALIVE_INTERVAL_S)
            if item is ka.EMPTY:
                yield ka.KEEPALIVE  # SSE comment: ignored by the parser, resets the client's read timer
                continue
            if item is ka.DONE:
                return
            if ka.is_error(item):
                e = item[1]
                log.warning(
                    "gateway stream broke mid-response after %.1fs (%s): %s",
                    time.monotonic() - started, type(e).__name__, e,
                )
                yield from ka.error_sse(
                    f"\n\n⚠️ The model gateway closed the stream mid-response ({type(e).__name__}). "
                    "This is usually an upstream idle or duration limit — please retry."
                )
                return
            yield item

    return StreamingResponse(stream(), media_type="text/event-stream")
