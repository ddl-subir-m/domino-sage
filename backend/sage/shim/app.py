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
    sovereign=os.environ.get("SAGE_MODEL_SOVEREIGN", "qwen-2-5"),        # on-Domino, sovereign
    plan=os.environ.get("SAGE_MODEL_PLAN", "gpt-5.4"),                   # strong, plan phase
    implement=os.environ.get("SAGE_MODEL_IMPLEMENT", "bedrock-qwen3-coder"),  # cheap coder
    default=os.environ.get("SAGE_MODEL_DEFAULT", "sonnet"),
)
_control = ModelControl(mode=Mode.MANUAL, phase=Phase.PLAN)
# Spike helper (Step 1.3): SAGE_FORCE_SENSITIVITY_LOCK=1 starts locked so you can verify the
# sovereign override live — any request, whatever model OpenCode asks for, routes to sovereign.
if os.environ.get("SAGE_FORCE_SENSITIVITY_LOCK") in {"1", "true", "yes"}:
    _control.on_assets_changed([True])
_shim = EnforcementShim(_control, _catalog, _gateway)

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
):
    body = await request.json()
    requested = body.get("model")
    gen = _shim.handle(body, project=x_sage_project)

    # Pull the first chunk eagerly so token-fetch / connect / upstream-status errors
    # surface as a clean JSON error instead of a mid-stream connection reset.
    try:
        first = next(gen)
    except StopIteration:
        first = b""
    except GatewayUpstreamError as e:
        log.error("gateway %s for requested model %r: %s", e.status, requested, e.body)
        return JSONResponse(status_code=502, content={"error": {"message": str(e), "upstream_status": e.status}})
    except Exception as e:  # token fetch, connection refused, timeout, etc.
        log.exception("shim upstream failure (requested model %r)", requested)
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})

    log.info("routed request (requested=%s project=%s) -> streaming", requested, x_sage_project)

    def stream():
        yield first
        yield from gen

    return StreamingResponse(stream(), media_type="text/event-stream")
