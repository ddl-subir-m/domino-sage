"""Orchestrator app — one API over the assembled builder (SPEC C1).

One ASGI app on one port (Phase 1): project lifecycle + model control + the /v1 shim OpenCode
targets, with the preview proxy (active project's Vite dev server, HTTP + HMR) mounted under
`/preview`. Everything is served under Domino's proxy path prefix (rewrite:false preserves it); a
tiny ASGI middleware strips that prefix so bare-registered routes match, and Vite bakes the same
prefix into its `base` so the preview round-trips through the one port. Prefix is empty locally.

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
from ..gateway.open_models import OPEN_WEIGHT_MODELS
from ..feedback.runner import FeedbackRunner
from ..preview.prefix import domino_base_prefix
from ..preview.proxy import make_preview_app
from ..router.models import Mode, ModelCatalog, Phase
from .service import Orchestrator

_feedback = FeedbackRunner()

log = logging.getLogger("sage.orchestrator")
logging.basicConfig(level=logging.INFO)

_REPO = Path(__file__).resolve().parents[3]


def _build_catalog() -> ModelCatalog:
    return ModelCatalog(
        sovereign_plan=os.environ.get("SAGE_MODEL_SOVEREIGN_PLAN", "qwen-2-5"),
        sovereign_implement=os.environ.get("SAGE_MODEL_SOVEREIGN_IMPLEMENT", "qwen-2-5"),
        sovereign_ask=os.environ.get("SAGE_MODEL_SOVEREIGN_ASK", "qwen-2-5"),
        plan=os.environ.get("SAGE_MODEL_PLAN", "gpt-5.4"),
        implement=os.environ.get("SAGE_MODEL_IMPLEMENT", "bedrock-qwen3-coder"),
        ask=os.environ.get("SAGE_MODEL_ASK", "sonnet"),
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

# The Domino proxy path prefix, single-sourced from env (empty locally). Baked into Vite's `base`
# AND stripped from incoming request paths so the bare-registered routes below keep matching.
BASE_PREFIX = domino_base_prefix()


class _PrefixMiddleware:
    """Strip Domino's proxy prefix from the request path and record it as `root_path`.

    Domino forwards the full prefixed path (rewrite:false). We strip it so routes registered at
    bare paths (`/`, `/api/...`, `/preview/...`) match, and set `root_path` for correct URL
    generation. No-op when the prefix is empty (local dev). Domino also sends the prefix in the
    `x-script-name` header; a one-time mismatch is logged as a cross-check against drift.
    """

    def __init__(self, app, prefix: str) -> None:
        self._app = app
        self._prefix = prefix
        self._warned = False

    async def __call__(self, scope, receive, send):
        if self._prefix and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self._prefix or path.startswith(self._prefix + "/"):
                scope = dict(scope)
                scope["path"] = path[len(self._prefix):] or "/"
                scope["root_path"] = self._prefix
            elif not self._warned:
                self._warned = True
                log.warning("prefix %r not found in request path %r", self._prefix, path)
        await self._app(scope, receive, send)


control_app.add_middleware(_PrefixMiddleware, prefix=BASE_PREFIX)


@control_app.get("/")
def ui() -> FileResponse:
    """The thin builder UI (single static page).

    no-store: the in-memory project registry resets on restart, so a cached page pointing at a
    stale project would POST builds that silently 404. Always serve the current HTML.
    """
    return FileResponse(_UI, headers={"Cache-Control": "no-store"})


@control_app.get("/healthz")
def healthz() -> dict:
    # gateway_mode is authoritative: "openai" means the mechanism is being exercised against a
    # generic provider, NOT the real Domino sovereign gateway. `projects` is in-memory-registered
    # only (kept for back-compat); `all_projects` also surfaces on-disk workspaces from a prior
    # process so the UI's picker survives an orchestrator restart.
    return {
        "ok": True,
        "projects": orchestrator.list_ids(),
        "all_projects": orchestrator.list_all_ids(),
        "gateway_mode": GATEWAY_MODE,
        "open_weight_models": [
            {"id": m.id, "provider": m.provider} for m in OPEN_WEIGHT_MODELS
        ] if GATEWAY_MODE == "openai" else [],
    }


@control_app.post("/api/projects")
async def create_project(request: Request) -> JSONResponse:
    body = await request.json()
    pid = body["id"]
    if orchestrator.get(pid):
        return JSONResponse(status_code=409, content={"error": f"project {pid} exists"})
    project = orchestrator.create_project(pid, start_preview=body.get("start_preview", True))
    return JSONResponse(status_code=201, content=project.status())


@control_app.post("/api/projects/{pid}/open")
def open_project(pid: str) -> JSONResponse:
    """Re-attach a workspace left on disk by a prior process (see list_all_ids)."""
    try:
        project = orchestrator.open_project(pid)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return JSONResponse(content=project.status())


@control_app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> JSONResponse:
    try:
        orchestrator.delete_project(pid)
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return JSONResponse(content={"deleted": pid})


@control_app.get("/api/projects/{pid}/history")
def project_history(pid: str) -> JSONResponse:
    """The chat transcript persisted per-workspace, so the UI can replay it after a reload or an
    orchestrator restart (see Workspace.append_history / Orchestrator.history)."""
    try:
        return JSONResponse(content={"history": orchestrator.history(pid)})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": "not found"})


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
        try:
            mode = Mode(body["mode"])
        except ValueError:
            return JSONResponse(status_code=400, content={"error": f"invalid mode {body['mode']!r}"})
        project.control.set_mode(mode)
    if "phase" in body:
        project.control.set_phase(Phase(body["phase"]))
    if "pick" in body:
        project.control.pick(body["pick"])
    if "catalog" in body:
        orchestrator.set_catalog(pid, **(body.get("catalog") or {}))
    if "lock" in body:  # manual toggle; independent of the sticky asset-driven lock
        project.control.set_manual_lock(bool(body["lock"]))
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


_FILE_TREE_IGNORE = {"node_modules", ".git", "dist", "dist-ssr", ".vite", "build", "__pycache__", ".turbo"}


def _build_file_tree(root: Path, current: Path) -> list[dict]:
    entries = []
    try:
        children = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return entries
    for child in children:
        if child.name in _FILE_TREE_IGNORE or child.name.startswith("."):
            continue
        rel = child.relative_to(root).as_posix()
        if child.is_dir():
            entries.append({"name": child.name, "path": rel, "type": "dir", "children": _build_file_tree(root, child)})
        else:
            entries.append({"name": child.name, "path": rel, "type": "file"})
    return entries


def _resolve_workspace_file(root: Path, rel_path: str) -> Path:
    """Resolves a UI-supplied relative path against the workspace root, rejecting anything that
    escapes it (../, absolute paths) so the file API can't read/write outside the workspace."""
    root = root.resolve()
    candidate = (root / rel_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path escapes workspace")
    return candidate


@control_app.get("/api/projects/{pid}/files")
def list_files(pid: str) -> JSONResponse:
    project = orchestrator.get(pid)
    if not project:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return JSONResponse(content={"tree": _build_file_tree(project.workspace.path, project.workspace.path)})


@control_app.get("/api/projects/{pid}/file")
def read_file(pid: str, path: str) -> JSONResponse:
    project = orchestrator.get(pid)
    if not project:
        return JSONResponse(status_code=404, content={"error": "not found"})
    try:
        target = _resolve_workspace_file(project.workspace.path, path)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid path"})
    if not target.is_file():
        return JSONResponse(status_code=404, content={"error": "file not found"})
    try:
        content = target.read_text()
    except UnicodeDecodeError:
        return JSONResponse(status_code=415, content={"error": "binary file"})
    return JSONResponse(content={"path": path, "content": content})


@control_app.put("/api/projects/{pid}/file")
async def write_file(pid: str, request: Request) -> JSONResponse:
    project = orchestrator.get(pid)
    if not project:
        return JSONResponse(status_code=404, content={"error": "not found"})
    body = await request.json()
    path = body.get("path")
    content = body.get("content")
    if not path or content is None:
        return JSONResponse(status_code=400, content={"error": "path and content required"})
    try:
        target = _resolve_workspace_file(project.workspace.path, path)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid path"})
    if not target.exists() or not target.is_file():
        return JSONResponse(status_code=404, content={"error": "file not found"})
    target.write_text(content)
    return JSONResponse(content={"path": path, "saved": True})


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


@control_app.post("/api/projects/{pid}/assets/{dataset_id}/detach")
def detach_asset(pid: str, dataset_id: str) -> JSONResponse:
    try:
        return JSONResponse(content=orchestrator.detach_asset(pid, dataset_id))
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
            try:
                orchestrator.open_project(pid)
            except FileNotFoundError:
                msg = f"Project '{pid}' not found — it may have been reset. Create a project to continue."
                yield f"data: {_json.dumps({'type': 'error', 'code': 'no_project', 'message': msg})}\n\n"
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


@control_app.post("/api/projects/{pid}/build/stop")
def stop_build(pid: str) -> JSONResponse:
    """Stop the in-flight build/build_stream turn: interrupts the agent, reverts any file
    changes it made this turn, and drops the turn from history — as if it never happened."""
    try:
        orchestrator.stop_build(pid)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return JSONResponse(content={"stopped": True})


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
            project.last_gateway_error = {"message": str(err), "upstream_status": err.status}
            return JSONResponse(status_code=502, content={"error": {"message": str(err), "upstream_status": err.status}})
        log.exception("shim upstream failure", exc_info=err)
        project.last_gateway_error = {"message": f"{type(err).__name__}: {err}"}
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(err).__name__}: {err}"}})

    def stream():
        yield first
        yield from gen

    return StreamingResponse(stream(), media_type="text/event-stream")


# Preview proxy for the active project, mounted under /preview on the one control port. Vite bakes
# base=<prefix>/preview/, so the proxy re-adds that when forwarding upstream (see make_preview_app).
def _active_upstream() -> str:
    project = orchestrator.active()
    if not project:
        raise RuntimeError("no active project")
    return project.supervisor.upstream()


control_app.mount("/preview", make_preview_app(_active_upstream, BASE_PREFIX))


def run() -> None:
    """Run the single control app (:8080, preview mounted at /preview) in one process."""
    import asyncio
    import contextlib
    import signal

    import uvicorn

    control_port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
    server = uvicorn.Server(uvicorn.Config(control_app, host="127.0.0.1", port=control_port, log_level="info"))

    # Install our own signal handler (instead of uvicorn's) so a SIGTERM reliably reaches
    # orchestrator.shutdown() to tear down Vite/OpenCode child processes.
    server.capture_signals = contextlib.nullcontext

    async def _serve() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: setattr(server, "should_exit", True))
        try:
            await server.serve()
        finally:
            orchestrator.shutdown()

    asyncio.run(_serve())


if __name__ == "__main__":
    run()
