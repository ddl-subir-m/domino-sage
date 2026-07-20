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

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Header, Request

load_dotenv()  # backend/.env (gateway creds + model aliases); no-op if absent
from fastapi.responses import StreamingResponse

from ..gateway.client import DominoGatewayClient, FakeGatewayClient, GatewayClient
from ..router.model_control import ModelControl
from ..router.models import ModelCatalog, Mode, Phase
from .enforcement import EnforcementShim


def _build_gateway() -> GatewayClient:
    base_url = os.environ.get("GATEWAY_BASE_URL")
    api_key = os.environ.get("GATEWAY_API_KEY")
    if base_url and api_key:
        return DominoGatewayClient(base_url=base_url, api_key=api_key)
    return FakeGatewayClient()  # no creds -> curl still works locally


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
_shim = EnforcementShim(_control, _catalog, _build_gateway())

app = FastAPI(title="sage enforcement shim")


@app.get("/healthz")
def healthz() -> dict[str, object]:
    gw = type(_shim._gateway).__name__  # noqa: SLF001 - spike introspection
    return {"ok": True, "gateway": gw, "locked": _control.locked}


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_sage_project: str = Header(default="unknown"),
) -> StreamingResponse:
    body = await request.json()
    stream = _shim.handle(body, project=x_sage_project)
    return StreamingResponse(stream, media_type="text/event-stream")
