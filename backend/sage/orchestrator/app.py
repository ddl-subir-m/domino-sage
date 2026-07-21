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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

_UI = Path(__file__).resolve().parents[1] / "ui" / "index.html"

from ..assets.provider import DEFAULT_SENSITIVITY_TAG, DominoAssetProvider, FakeAssetProvider
from ..gateway.client import DEFAULT_SIDECAR_URL, GatewayUpstreamError, sidecar_token, static_token
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


def _build_assets():
    """Domino datasets when DOMINO_API_HOST is set (workspace), else an in-memory fake."""
    api_host = os.environ.get("DOMINO_API_HOST")
    if not api_host:
        return FakeAssetProvider()
    key = os.environ.get("DOMINO_API_KEY")
    token = static_token(key) if key else sidecar_token(os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))
    return DominoAssetProvider(api_host, token)


_gateway, GATEWAY_MODE = build_gateway()
orchestrator = Orchestrator(
    workspaces_root=Path(os.environ.get("SAGE_WORKSPACES", _REPO / "backend" / "workspaces")),
    template=Path(os.environ.get("SAGE_TEMPLATE", _REPO / "template" / "react-vite")),
    gateway=_gateway,
    catalog=_build_catalog(),
    opencode_cwd=Path(os.environ.get("SAGE_OPENCODE_CWD", _REPO)),  # where opencode.json lives
    # Single-provider hosts (openai mode) don't serve OpenCode's other aliases -> force the model.
    force_model=(GATEWAY_MODE == "openai"),
    assets=_build_assets(),
    sensitivity_tag=os.environ.get("SAGE_SENSITIVITY_TAG", DEFAULT_SENSITIVITY_TAG),
    domino_project_id=os.environ.get("DOMINO_PROJECT_ID"),
)

control_app = FastAPI(title="sage orchestrator")


@control_app.get("/")
def ui() -> FileResponse:
    """The thin builder UI (single static page)."""
    return FileResponse(_UI)


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


@control_app.get("/api/assets")
def list_assets() -> dict:
    return {"assets": orchestrator.list_assets(), "sensitivity_tag": orchestrator._sensitivity_tag}


@control_app.post("/api/projects/{pid}/assets/{dataset_id}/attach")
def attach_asset(pid: str, dataset_id: str) -> JSONResponse:
    try:
        return JSONResponse(content=orchestrator.attach_asset(pid, dataset_id))
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "project not found"})
    except LookupError:
        return JSONResponse(status_code=404, content={"error": "dataset not found"})


@control_app.post("/api/projects/{pid}/build/stream")
def build_stream(pid: str, body: dict) -> StreamingResponse:
    """Streaming build: SSE of progress events (agent text/tool, typecheck, done). Follow-up
    prompts reuse the session (modify/add features). Sync generator -> Starlette threadpools it,
    so the loop stays free to serve the /v1 model calls the turn makes."""
    import json as _json

    prompt = (body or {}).get("prompt", "")

    def sse():
        if not orchestrator.get(pid):
            yield f"data: {_json.dumps({'type': 'error', 'message': 'project not found'})}\n\n"
            return
        if not prompt:
            yield f"data: {_json.dumps({'type': 'error', 'message': 'prompt required'})}\n\n"
            return
        try:
            for evt in orchestrator.build_stream(pid, prompt):
                yield f"data: {_json.dumps(evt)}\n\n"
        except Exception as e:  # noqa: BLE001
            log.exception("build_stream failed")
            yield f"data: {_json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


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
        # Offload the blocking build (drives OpenCode, sleeps) to a thread so the event loop
        # stays free to serve the /v1 model calls that OpenCode makes DURING the build.
        # Without this the single loop deadlocks: build waits for a turn that can't be served.
        result = await run_in_threadpool(orchestrator.build, pid, prompt)
        return JSONResponse(content=result)
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

    def _peek():  # blocking; runs in a thread so the loop stays free
        try:
            return next(gen), None
        except StopIteration:
            return b"", None
        except Exception as e:  # noqa: BLE001
            return None, e

    first, err = await run_in_threadpool(_peek)
    if err is not None:
        if isinstance(err, GatewayUpstreamError):
            log.error("gateway %s: %s", err.status, err.body)
            return JSONResponse(status_code=502, content={"error": {"message": str(err), "upstream_status": err.status}})
        log.exception("shim upstream failure", exc_info=err)
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(err).__name__}: {err}"}})

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
