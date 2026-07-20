"""Orchestrator app — one API over the assembled builder (SPEC C1).

Runs two ASGI apps in one process:
  - control app  (:8080): project lifecycle + model control + the /v1 shim OpenCode targets
  - preview app  (:8090): proxies the active project's Vite dev server (HTTP + HMR)

Two ports because Vite serves assets from absolute paths (/src, /@vite), so the preview must
proxy at root, not a subpath.

Run:  uv run python -m sage.orchestrator.app
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..gateway.client import GatewayUpstreamError
from ..gateway.factory import build_gateway
from ..feedback.runner import FeedbackRunner
from ..preview.proxy import make_preview_app
from ..router.models import Mode, ModelCatalog, Phase
from .service import Orchestrator

_feedback = FeedbackRunner()

log = logging.getLogger("sage.orchestrator")
logging.basicConfig(level=logging.INFO)

_REPO = Path(__file__).resolve().parents[3]


def _build_catalog() -> ModelCatalog:
    return ModelCatalog(
        sovereign=os.environ.get("SAGE_MODEL_SOVEREIGN", "qwen-2-5"),
        plan=os.environ.get("SAGE_MODEL_PLAN", "gpt-5.4"),
        implement=os.environ.get("SAGE_MODEL_IMPLEMENT", "bedrock-qwen3-coder"),
        default=os.environ.get("SAGE_MODEL_DEFAULT", "sonnet"),
    )


_gateway, GATEWAY_MODE = build_gateway()
orchestrator = Orchestrator(
    workspaces_root=Path(os.environ.get("SAGE_WORKSPACES", _REPO / "backend" / "workspaces")),
    template=Path(os.environ.get("SAGE_TEMPLATE", _REPO / "template" / "react-vite")),
    gateway=_gateway,
    catalog=_build_catalog(),
    opencode_cwd=Path(os.environ.get("SAGE_OPENCODE_CWD", _REPO)),  # where opencode.json lives
)

control_app = FastAPI(title="sage orchestrator")


@control_app.get("/healthz")
def healthz() -> dict:
    # gateway_mode is authoritative: "openai" means the mechanism is being exercised against a
    # generic provider, NOT the real Domino sovereign gateway.
    return {"ok": True, "projects": orchestrator.list_ids(), "gateway_mode": GATEWAY_MODE}


@control_app.post("/api/projects")
async def create_project(request: Request) -> JSONResponse:
    body = await request.json()
    pid = body["id"]
    if orchestrator.get(pid):
        return JSONResponse(status_code=409, content={"error": f"project {pid} exists"})
    project = orchestrator.create_project(pid, start_preview=body.get("start_preview", True))
    return JSONResponse(status_code=201, content=project.status())


@control_app.get("/api/projects")
def list_projects() -> dict:
    return {"projects": [orchestrator.get(p).status() for p in orchestrator.list_ids()]}


@control_app.get("/api/projects/{pid}")
def get_project(pid: str) -> JSONResponse:
    project = orchestrator.get(pid)
    if not project:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return JSONResponse(content=project.status())


@control_app.post("/api/projects/{pid}/model")
async def set_model(pid: str, request: Request) -> JSONResponse:
    project = orchestrator.get(pid)
    if not project:
        return JSONResponse(status_code=404, content={"error": "not found"})
    body = await request.json()
    if "mode" in body:
        project.control.set_mode(Mode(body["mode"]))
    if "phase" in body:
        project.control.set_phase(Phase(body["phase"]))
    if "pick" in body:
        project.control.pick(body["pick"])
    if body.get("lock"):  # sticky; cannot be cleared via API
        project.control.on_assets_changed([True])
    return JSONResponse(content=project.status())


@control_app.post("/api/projects/{pid}/check")
def check_project(pid: str) -> JSONResponse:
    """Typecheck the workspace (Step 5). The server-mode driver calls the same engine after each
    agent edit and injects `message` into the next turn; exposed here for the UI + manual use."""
    project = orchestrator.get(pid)
    if not project:
        return JSONResponse(status_code=404, content={"error": "not found"})
    report = _feedback.check(project.workspace.path)
    return JSONResponse(content={
        "ok": report.ok,
        "error_count": len(report.errors),
        "message": report.as_agent_message(),
        "signature": report.signature(),
    })


@control_app.post("/api/projects/{pid}/build")
async def build_project(pid: str, request: Request) -> JSONResponse:
    """Run one agent build with the closed feedback loop (needs gateway access)."""
    if not orchestrator.get(pid):
        return JSONResponse(status_code=404, content={"error": "not found"})
    body = await request.json()
    prompt = body.get("prompt")
    if not prompt:
        return JSONResponse(status_code=400, content={"error": "prompt required"})
    try:
        return JSONResponse(content=orchestrator.build(pid, prompt))
    except Exception as e:
        log.exception("build failed")
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})


@control_app.post("/v1/chat/completions")
async def chat_completions(request: Request, x_sage_project: str = Header(default="")):
    project = orchestrator.get(x_sage_project) if x_sage_project else orchestrator.active()
    if not project:
        return JSONResponse(status_code=404, content={"error": {"message": "no such project; create one first"}})
    body = await request.json()
    requested = body.get("model")
    gen = project.shim.handle(body, project=project.id)
    try:
        first = next(gen)
    except StopIteration:
        first = b""
    except GatewayUpstreamError as e:
        log.error("gateway %s: %s", e.status, e.body)
        return JSONResponse(status_code=502, content={"error": {"message": str(e), "upstream_status": e.status}})
    except Exception as e:
        log.exception("shim upstream failure")
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})

    def stream():
        yield first
        yield from gen

    return StreamingResponse(stream(), media_type="text/event-stream")


# Preview proxy for the active project (served on its own port so Vite's absolute paths resolve).
def _active_upstream() -> str:
    project = orchestrator.active()
    if not project:
        raise RuntimeError("no active project")
    return project.supervisor.upstream()


preview_app = make_preview_app(_active_upstream)


def run() -> None:
    """Run control (:8080) and preview (:8090) together in one process."""
    import asyncio

    import uvicorn

    control_port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
    preview_port = int(os.environ.get("SAGE_PREVIEW_PORT", "8090"))
    servers = [
        uvicorn.Server(uvicorn.Config(control_app, host="127.0.0.1", port=control_port, log_level="info")),
        uvicorn.Server(uvicorn.Config(preview_app, host="127.0.0.1", port=preview_port, log_level="warning")),
    ]

    async def _serve() -> None:
        try:
            await asyncio.gather(*(s.serve() for s in servers))
        finally:
            orchestrator.shutdown()

    asyncio.run(_serve())


if __name__ == "__main__":
    run()
