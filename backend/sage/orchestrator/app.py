"""Orchestrator app — one API over the assembled builder (SPEC C1).

One ASGI app on one port (Phase 1): project lifecycle + model control + the /v1 shim OpenCode
targets, with the preview proxy (active project's Vite dev server, HTTP + HMR) mounted under
`/preview`. Everything is served under Domino's proxy path prefix (rewrite:false preserves it); a
tiny ASGI middleware strips that prefix so bare-registered routes match, and Vite bakes the same
prefix into its `base` so the preview round-trips through the one port. Prefix is empty locally.

Run:  uv run python -m sage.orchestrator.app
"""
from __future__ import annotations

import collections
import logging
import os
import queue
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
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
from ..shim import keepalive as ka
from .service import AttachTooLarge, DataReferenced, Orchestrator, UploadUnavailable

_feedback = FeedbackRunner()

log = logging.getLogger("sage.orchestrator")
logging.basicConfig(level=logging.INFO)

# In-memory tail of recent sage.* logs so /api/diag can surface what happened during a build (which
# port OpenCode dialed, "model call -> streaming (first byte Xs)", "gateway stream broke ...") without
# shell access in the deployed builder. Bounded; captures INFO+ from the whole sage.* hierarchy.
_LOG_RING: "collections.deque[str]" = collections.deque(maxlen=400)


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOG_RING.append(self.format(record))
        except Exception:  # never let logging crash a request
            pass


_ring = _RingHandler()
_ring.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
_ring.setLevel(logging.INFO)
_sage_log = logging.getLogger("sage")
_sage_log.addHandler(_ring)
_sage_log.setLevel(logging.INFO)


def _sage_rev() -> str | None:
    """Short git HEAD of the deployed Sage checkout — lets /api/diag confirm which code is running."""
    import subprocess
    home = os.environ.get("SAGE_APP_HOME", "/opt/sage")
    try:
        out = subprocess.run(["git", "-C", home, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or None
    except Exception:
        return None


_SAGE_REV = _sage_rev()

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


def _build_control_plane():
    """A Domino control plane for Publish / Stop when this builder runs on Domino (DOMINO_API_HOST +
    the Environment/hardware ids Domino injects), else None so those endpoints report a clear
    "only on Domino" error instead of crashing local/fake runs."""
    api_host = os.environ.get("DOMINO_API_HOST")
    env_id = os.environ.get("DOMINO_ENVIRONMENT_ID")
    tier_id = os.environ.get("DOMINO_HARDWARE_TIER_ID")
    if not (api_host and env_id and tier_id):
        log.info("no DOMINO_API_HOST/ENVIRONMENT_ID/HARDWARE_TIER_ID — Publish/Stop disabled (local run)")
        return None
    from ..provision.domino import DominoControlPlane

    token = sidecar_token(os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))
    return DominoControlPlane(
        api_host,
        token,
        environment_id=env_id,
        environment_revision_id=os.environ.get("DOMINO_ENVIRONMENT_REVISION_ID"),
        hardware_tier_id=tier_id,
        builder_tool=os.environ.get("SAGE_BUILDER_TOOL", "sageBuilder"),
    )


_gateway, GATEWAY_MODE = build_gateway()
# One builder is bound to one project volume. On Domino (git-based) that's the mounted repo at
# /mnt/code; locally it defaults to a scratch dir. The display id is the Domino project name.
_WORKSPACE_DIR = Path(os.environ.get("SAGE_WORKSPACE_DIR", _REPO / "backend" / "workspaces" / "app"))
orchestrator = Orchestrator(
    workspace_dir=_WORKSPACE_DIR,
    template=Path(os.environ.get("SAGE_TEMPLATE", _REPO / "template" / "react-vite")),
    gateway=_gateway,
    catalog=_build_catalog(),
    project_id=os.environ.get("DOMINO_PROJECT_NAME", _WORKSPACE_DIR.name),
    opencode_cwd=Path(os.environ.get("SAGE_OPENCODE_CWD", _REPO)),  # where opencode.json lives
    # Single-provider hosts (openai mode) don't serve OpenCode's other aliases -> force the model.
    force_model=(GATEWAY_MODE == "openai"),
    assets=_build_assets(),
    sensitivity_tag=os.environ.get("SAGE_SENSITIVITY_TAG", DEFAULT_SENSITIVITY_TAG),
    domino_project_id=os.environ.get("DOMINO_PROJECT_ID"),
    control_plane=_build_control_plane(),
    domino_project_name=os.environ.get("DOMINO_PROJECT_NAME"),
    domino_run_id=os.environ.get("DOMINO_RUN_ID"),
)

control_app = FastAPI(title="sage orchestrator")

# The Domino proxy path prefix, single-sourced from env (empty locally). Baked into Vite's `base`
# AND stripped from incoming request paths so the bare-registered routes below keep matching.
BASE_PREFIX = domino_base_prefix()


class _PrefixMiddleware:
    """Record Domino's proxy prefix as `root_path` so bare-registered routes match.

    Domino forwards the full prefixed path (rewrite:false). Starlette routes on
    `get_route_path = path - root_path` at every level (including nested Mounts, which extend
    root_path rather than rewrite the path), so we set `root_path` and leave `path` INTACT — do NOT
    strip the path, or the /preview Mount double-counts the prefix. No-op when the prefix is empty
    (local dev). Domino also sends the prefix in `x-script-name`; a one-time mismatch is logged.
    """

    # Routes served to callers INSIDE the container over localhost, which never cross Domino's proxy
    # and so correctly carry no prefix: the shim's /v1 (every OpenCode model call) and /healthz.
    # They must not trip the warning — it fires once per process, so one internal call would
    # otherwise spend it seconds after boot and leave a REAL prefix misconfiguration silent forever.
    _UNPROXIED = ("/v1/", "/healthz")

    def __init__(self, app, prefix: str) -> None:
        self._app = app
        self._prefix = prefix
        self._warned = False

    async def __call__(self, scope, receive, send):
        if self._prefix and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self._prefix or path.startswith(self._prefix + "/"):
                scope = dict(scope)
                scope["root_path"] = self._prefix
            elif not self._warned and not path.startswith(self._UNPROXIED):
                self._warned = True
                log.warning("prefix %r not found in request path %r", self._prefix, path)
        await self._app(scope, receive, send)


control_app.add_middleware(_PrefixMiddleware, prefix=BASE_PREFIX)


@control_app.get("/")
def ui() -> FileResponse:
    """The thin builder UI (single static page). no-store so the current HTML is always served."""
    return FileResponse(_UI, headers={"Cache-Control": "no-store"})


@control_app.get("/healthz")
def healthz() -> dict:
    # gateway_mode is authoritative: "openai" means the mechanism is being exercised against a
    # generic provider, NOT the real Domino sovereign gateway.
    return {
        "ok": True,
        "project": orchestrator._project_id,
        "gateway_mode": GATEWAY_MODE,
        # True when this builder can Publish/Stop through the Domino control plane (Domino runs
        # only); the UI hides those controls otherwise.
        "domino": orchestrator._control_plane is not None,
        "open_weight_models": [
            {"id": m.id, "provider": m.provider} for m in OPEN_WEIGHT_MODELS
        ] if GATEWAY_MODE == "openai" else [],
    }


@control_app.get("/api/diag")
def diag() -> JSONResponse:
    """Browser-openable build diagnostics (no shell needed in the deployed builder). Reads the CURRENT
    project without starting anything, so it's safe to hit mid-build. Key signals:
      - sage_rev: which code is actually running (confirm a rebuild took effect)
      - model_calls: how many inferences reached the shim THIS turn. 0 while a turn is live means the
        model call never got to the gateway (OpenCode stuck earlier, e.g. on a tool), not a gateway hang
      - last_gateway_error: set if a model call failed/severed
      - ports: base_port (what opencode.json tells OpenCode to dial) must equal control_port
      - agents: the agents OpenCode actually resolved. Missing sage-ask/sage-plan/sage-implement means
        every mode silently ran the default build agent, so their read-only permission and prompt blocks
        never applied (null = OpenCode not started yet, or the query failed)
      - log_tail / opencode_log_tail: recent sage.* and OpenCode server logs
    """
    from .service import _opencode_base_port

    p = orchestrator._project  # may be None if no project bound yet; do NOT create one here
    control_port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
    try:
        base_port = _opencode_base_port(orchestrator._opencode_cwd)
    except Exception:
        base_port = None
    return JSONResponse(content={
        "sage_rev": _SAGE_REV,
        "gateway_mode": GATEWAY_MODE,
        "ports": {"control_port": control_port, "base_port": base_port,
                  "match": base_port == control_port},
        "agents": orchestrator.resolved_agents(),
        "project": None if p is None else {
            "model_calls": p.model_calls,
            "tool_call_responses": p.tool_call_responses,
            "last_gateway_error": p.last_gateway_error,
            "session_id": p.session_id,
        },
        "log_tail": list(_LOG_RING)[-60:],
        "opencode_log_tail": orchestrator._opencode_log_tail(30),
    })


@control_app.get("/api/project")
def get_project() -> JSONResponse:
    """Attach the bound project (seeds the volume + starts the preview on first call) and return
    its status. The UI calls this on load to boot the single project."""
    return JSONResponse(content=orchestrator.project().status())


@control_app.get("/api/project/history")
def project_history() -> JSONResponse:
    """The chat transcript persisted in the workspace, so the UI can replay it after a reload or
    restart (see Workspace.append_history / Orchestrator.history). Reads disk without starting the
    preview."""
    return JSONResponse(content={"history": orchestrator.history()})


@control_app.post("/api/project/model")
async def set_model(request: Request) -> JSONResponse:
    project = orchestrator.project()
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
        orchestrator.set_catalog(**(body.get("catalog") or {}))
    if "lock" in body:
        lock = bool(body["lock"])
        project.control.set_manual_lock(lock)
        if not lock:
            # A single "Unlock" fully unlocks: also override the sticky asset-driven lock (the UI
            # warns the user before this — the sovereign guarantee no longer holds for the session).
            project.control.clear_asset_lock()
    return JSONResponse(content=project.status())


@control_app.post("/api/project/sync")
async def sync_project() -> JSONResponse:
    """Pull teammate changes from the repo into the workspace, resolving any merge conflicts with
    the agent, then push. Offloaded to a thread because a conflict resolution drives a model turn
    (which needs the event loop free to serve the /v1 calls that turn makes)."""
    try:
        result = await run_in_threadpool(orchestrator.sync)
    except Exception as e:  # noqa: BLE001
        log.exception("sync failed")
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})
    return JSONResponse(content=result)


@control_app.post("/api/publish")
async def publish() -> JSONResponse:
    """Publish (or republish) THIS app's project as a live Domino App. Offloaded to a thread — it
    saves work (a git push) and makes several control-plane REST calls."""
    try:
        result = await run_in_threadpool(orchestrator.publish)
    except RuntimeError as e:  # not-on-Domino / missing app.sh — human-readable, expected failures
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:  # noqa: BLE001
        log.exception("publish failed")
        return JSONResponse(status_code=502, content={"error": f"{type(e).__name__}: {e}"})
    return JSONResponse(content=result)


@control_app.get("/api/publish-status")
async def publish_status(app_id: str) -> JSONResponse:
    """Deploy status of a published app so the UI can poll after Publish: {phase, status, app_id}."""
    try:
        result = await run_in_threadpool(orchestrator.publish_status, app_id)
    except RuntimeError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:  # noqa: BLE001
        log.exception("publish-status failed")
        return JSONResponse(status_code=502, content={"error": f"{type(e).__name__}: {e}"})
    return JSONResponse(content=result)


@control_app.post("/api/stop")
async def stop() -> JSONResponse:
    """Stop THIS builder's workspace (saving in-progress work first). Offloaded to a thread — it
    drives a git push and a control-plane call."""
    try:
        result = await run_in_threadpool(orchestrator.stop)
    except Exception as e:  # noqa: BLE001
        log.exception("stop failed")
        return JSONResponse(status_code=502, content={"error": f"{type(e).__name__}: {e}"})
    return JSONResponse(content=result)


@control_app.post("/api/preview/runtime-error")
async def preview_runtime_error(request: Request) -> Response:
    """The live preview posts here when it catches an uncaught/render error (see the template's
    reportRuntimeError). build_stream reads it after a clean typecheck to autofix runtime crashes
    that tsc can't see. Fire-and-forget: always 204, never blocks the preview."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — a malformed report must not error the preview
        return Response(status_code=204)
    orchestrator.record_runtime_error(str(body.get("message") or ""), str(body.get("stack") or ""))
    return Response(status_code=204)


@control_app.post("/api/project/check")
def check_project() -> JSONResponse:
    """Typecheck the workspace (Step 5). The server-mode driver calls the same engine after each
    agent edit and injects `message` into the next turn; exposed here for the UI + manual use."""
    project = orchestrator.project()
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


@control_app.get("/api/project/files")
def list_files() -> JSONResponse:
    project = orchestrator.project()
    return JSONResponse(content={"tree": _build_file_tree(project.workspace.path, project.workspace.path)})


@control_app.get("/api/project/file")
def read_file(path: str) -> JSONResponse:
    project = orchestrator.project()
    # Attached data files live as symlinks under public/data/ pointing at the dataset mount, which is
    # OUTSIDE the workspace — _resolve_workspace_file (which .resolve()s through the symlink) would
    # reject them as an escape. They're trusted (Sage created the symlink to a dataset the user owns),
    # so allow a read-only preview when the path exactly matches a known attachment; membership in the
    # manifest is the whitelist, so no traversal is possible.
    if any(e["path"] == path for e in project.attached):
        target = project.workspace.path / path  # is_file()/read_text() follow the symlink to the mount
    else:
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


@control_app.get("/api/project/file/raw")
def read_file_raw(path: str) -> Response:
    # Serve raw file bytes with a content type so binary files (e.g. images) render in the code view.
    project = orchestrator.project()
    if any(e["path"] == path for e in project.attached):
        target = project.workspace.path / path  # follow the symlink to the dataset mount
    else:
        try:
            target = _resolve_workspace_file(project.workspace.path, path)
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "invalid path"})
    if not target.is_file():
        return JSONResponse(status_code=404, content={"error": "file not found"})
    return FileResponse(target, headers={"Cache-Control": "no-store"})


@control_app.put("/api/project/file")
async def write_file(request: Request) -> JSONResponse:
    project = orchestrator.project()
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


@control_app.get("/api/project/instructions")
def read_instructions() -> JSONResponse:
    project = orchestrator.project()
    return JSONResponse(content={"content": orchestrator.read_instructions(project)})


@control_app.put("/api/project/instructions")
async def write_instructions(request: Request) -> JSONResponse:
    project = orchestrator.project()
    body = await request.json()
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse(status_code=400, content={"error": "content must be a string"})
    orchestrator.write_instructions(project, content)
    return JSONResponse(content={"ok": True, "content": orchestrator.read_instructions(project)})


@control_app.get("/api/assets")
def list_assets() -> dict:
    return {
        "assets": orchestrator.list_assets(),
        "sensitivity_tag": orchestrator._sensitivity_tag,
        "default_dataset_id": orchestrator.default_dataset_id(),
    }


@control_app.get("/api/project/assets/{dataset_id}/files")
def list_asset_files(dataset_id: str) -> JSONResponse:
    try:
        return JSONResponse(content={"files": orchestrator.list_asset_files(dataset_id)})
    except LookupError:
        return JSONResponse(status_code=404, content={"error": "dataset not found"})


@control_app.post("/api/project/assets/{dataset_id}/files/attach")
async def attach_file(dataset_id: str, request: Request) -> JSONResponse:
    path = (await request.json()).get("path")
    if not path:
        return JSONResponse(status_code=400, content={"error": "path required"})
    try:
        return JSONResponse(content=orchestrator.attach_file(dataset_id, path))
    except LookupError:
        return JSONResponse(status_code=404, content={"error": "dataset not mounted in this project"})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": "file not found in dataset"})
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid file path"})
    except AttachTooLarge as e:
        mb = e.cap / (1024 * 1024)
        return JSONResponse(
            status_code=413,
            content={"error": f"attaching this file would exceed the {mb:.0f} MB limit for attached data"},
        )


@control_app.post("/api/project/files/detach")
async def detach_file(request: Request) -> JSONResponse:
    path = (await request.json()).get("path")
    if not path:
        return JSONResponse(status_code=400, content={"error": "path required"})
    try:
        return JSONResponse(content=orchestrator.detach_file(path))
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid path"})


@control_app.post("/api/project/upload")
async def upload_file(request: Request) -> JSONResponse:
    # Raw-body upload (avoids a python-multipart dependency): the file bytes are the request body;
    # the name, sensitivity, and optional target dataset ride in query params. One file per request.
    filename = request.query_params.get("name", "")
    sensitive = request.query_params.get("sensitive", "").lower() in ("1", "true", "yes")
    dataset_id = request.query_params.get("dataset") or None
    data = await request.body()
    if not data:
        return JSONResponse(status_code=400, content={"error": "empty upload"})
    try:
        return JSONResponse(content=orchestrator.upload_file(filename, data, sensitive, dataset_id))
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid filename"})
    except UploadUnavailable as e:
        where = "sensitive" if e.sensitive else "default"
        msg = (
            "The dataset you picked isn't mounted and writable in this workspace."
            if dataset_id
            else "No writable dataset is available to store uploads in this project."
        )
        return JSONResponse(status_code=409, content={"error": msg, "target": where})
    except AttachTooLarge as e:
        mb = e.cap / (1024 * 1024)
        return JSONResponse(
            status_code=413,
            content={"error": f"uploading this file would exceed the {mb:.0f} MB limit for attached data"},
        )


@control_app.post("/api/project/files/delete")
async def delete_file(request: Request) -> JSONResponse:
    path = (await request.json()).get("path")
    if not path:
        return JSONResponse(status_code=400, content={"error": "path required"})
    try:
        return JSONResponse(content=orchestrator.delete_file(path))
    except DataReferenced as e:
        where = sorted(set(e.copies or e.refs))
        files = ", ".join(where[:3]) + ("…" if len(where) > 3 else "")
        verb = "has a copy of" if e.copies else "uses"
        msg = (f"Can't delete — your app {verb} this file ({files}). Remove it from the app first, "
               f"or use Detach to drop it from the workspace while keeping the data.")
        return JSONResponse(status_code=409, content={"error": msg, "refs": e.refs, "copies": e.copies})
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid path"})


@control_app.post("/api/project/build/stream")
def build_stream(body: dict) -> StreamingResponse:
    """Streaming build: SSE of progress events (agent text/tool, typecheck, done). Follow-up
    prompts reuse the session (modify/add features). Sync generator -> Starlette threadpools it,
    so the loop stays free to serve the /v1 model calls the turn makes."""
    import json as _json

    prompt = (body or {}).get("prompt", "")
    mentions = (body or {}).get("mentions") or None  # workspace paths of @-referenced attached files

    def sse():
        if not prompt:
            yield f"data: {_json.dumps({'type': 'error', 'message': 'prompt required'})}\n\n"
            return
        try:
            for evt in orchestrator.build_stream(prompt, mentions):
                yield f"data: {_json.dumps(evt)}\n\n"
        except Exception as e:  # noqa: BLE001
            log.exception("build_stream failed")
            yield f"data: {_json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@control_app.post("/api/project/build/approve")
def build_approve(body: dict) -> StreamingResponse:
    """Approve a gated plan (SPEC P6) and stream the resulting build. Body: {answers?, plan_edits?}."""
    import json as _json

    answers = (body or {}).get("answers", "") or ""
    plan_edits = (body or {}).get("plan_edits")  # None = approve the plan as proposed

    def sse():
        try:
            for evt in orchestrator.approve_stream(answers, plan_edits):
                yield f"data: {_json.dumps(evt)}\n\n"
        except Exception as e:  # noqa: BLE001
            log.exception("approve_stream failed")
            yield f"data: {_json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


@control_app.get("/api/project/settings")
def get_settings() -> JSONResponse:
    """Per-project Sage settings (e.g. skip_planning — opt out of the first-build plan gate)."""
    return JSONResponse(content=orchestrator.project().workspace.read_settings())


@control_app.post("/api/project/settings")
async def set_settings(request: Request) -> JSONResponse:
    """Update per-project settings. Currently just skip_planning (SPEC P6 opt-out)."""
    body = await request.json()
    workspace = orchestrator.project().workspace
    settings = workspace.read_settings()
    if "skip_planning" in body:
        settings["skip_planning"] = bool(body["skip_planning"])
    workspace.write_settings(settings)
    return JSONResponse(content=settings)


@control_app.post("/api/project/plan/cancel")
def cancel_plan() -> JSONResponse:
    """Discard an un-approved plan. When the user dismisses the plan card without building, the
    plan.md the gate turn wrote is still on disk (only an approve archives it). Left there it reads
    like live intent — the exact stray-plan case archive_plan() exists to prevent — so archive it
    now (non-destructive; git keeps the history). Idempotent: no-op if there's no live plan."""
    archived = orchestrator.project().workspace.archive_plan()
    return JSONResponse(content={"cancelled": True, "archived": archived is not None})


@control_app.post("/api/project/build/stop")
def stop_build() -> JSONResponse:
    """Stop the in-flight build/build_stream turn: interrupts the agent, reverts any file
    changes it made this turn, and drops the turn from history — as if it never happened."""
    orchestrator.stop_build()
    return JSONResponse(content={"stopped": True})


@control_app.get("/api/project/build/state")
def build_state() -> JSONResponse:
    """Whether a turn is running right now. Cheap enough to poll: it reads the turn lock and does
    not attach the project. The UI calls this after its SSE stream drops, to tell "the connection
    broke but the build is still going" from "the turn is over"."""
    return JSONResponse(content={"running": orchestrator.turn_busy()})


@control_app.post("/api/project/build")
async def build_project(request: Request) -> JSONResponse:
    """Run one agent build with the closed feedback loop (needs gateway access)."""
    body = await request.json()
    prompt = body.get("prompt")
    if not prompt:
        return JSONResponse(status_code=400, content={"error": "prompt required"})
    try:
        # Offload the blocking build (drives OpenCode, sleeps) to a thread so the event loop
        # stays free to serve the /v1 model calls that OpenCode makes DURING the build.
        # Without this the single loop deadlocks: build waits for a turn that can't be served.
        result = await run_in_threadpool(orchestrator.build, prompt)
        return JSONResponse(content=result)
    except Exception as e:
        log.exception("build failed")
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(e).__name__}: {e}"}})


@control_app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    project = orchestrator.project()
    body = await request.json()
    # Per-turn telemetry: every inference OpenCode runs this turn passes through here. Count it, and
    # (below) flag whether the model's response carried a tool call. build_stream reads these to explain
    # a no-edit turn. See Project.model_calls.
    project.model_calls += 1
    gen = project.shim.handle(body, project=project.id, session=project.session_id)

    # Drain the (blocking) gateway generator on a worker thread so the response side can interleave SSE
    # keepalives during silent gaps. Without this, we'd have to withhold the whole HTTP response until
    # the model's first token — minutes for a gpt-5.4 plan turn — and OpenCode's fetch (undici) aborts
    # the silent request as "TypeError: network error". See sage.shim.keepalive.
    q: "queue.Queue" = queue.Queue()
    started = time.monotonic()
    threading.Thread(target=ka.pump, args=(gen, q), daemon=True).start()

    # Bounded eager pull: a fast pre-stream failure (auth, bad model) inside the budget -> clean JSON
    # 502, exactly as before. If nothing arrives, the model is just thinking: commit to the stream and
    # keep it warm with keepalives below.
    first = await run_in_threadpool(ka.get, q, ka.FIRST_BYTE_BUDGET_S)
    if ka.is_error(first):
        err = first[1]
        if isinstance(err, GatewayUpstreamError):
            log.error("gateway %s: %s", err.status, err.body)
            project.last_gateway_error = {"message": str(err), "upstream_status": err.status}
            return JSONResponse(status_code=502, content={"error": {"message": str(err), "upstream_status": err.status}})
        log.error("shim upstream failure: %s", err)
        project.last_gateway_error = {"message": f"{type(err).__name__}: {err}"}
        return JSONResponse(status_code=502, content={"error": {"message": f"{type(err).__name__}: {err}"}})

    log.info(
        "model call -> streaming (first byte %.1fs%s)",
        time.monotonic() - started, ", pending; keepalive engaged" if first is ka.EMPTY else "",
    )

    def stream():
        # Flag once if this response carries a tool call (streamed as choices[].delta.tool_calls, or
        # finish_reason "tool_calls"). Substring sniff is enough — we only need "did the model try a
        # tool this turn", and it stays harness-agnostic (no SSE parsing).
        flagged = False

        def sniff(chunk: bytes) -> None:
            nonlocal flagged
            if not flagged and b"tool_calls" in chunk:
                flagged = True
                project.tool_call_responses += 1

        if first is ka.DONE:
            return
        if first is not ka.EMPTY:
            sniff(first)
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
                project.last_gateway_error = {"message": f"{type(e).__name__}: {e}"}
                yield from ka.error_sse(
                    f"\n\n⚠️ The model gateway closed the stream mid-response ({type(e).__name__}). "
                    "This is usually an upstream idle or duration limit — please retry."
                )
                return
            sniff(item)
            yield item

    return StreamingResponse(stream(), media_type="text/event-stream")


# Preview proxy for the bound project, mounted under /preview on the one control port. Vite bakes
# base=<prefix>/preview/, so the proxy re-adds that when forwarding upstream (see make_preview_app).
# Attaching the project (first hit) seeds the volume + starts Vite; .upstream() raises until ready.
def _preview_upstream() -> str:
    return orchestrator.project().supervisor.upstream()


control_app.mount("/preview", make_preview_app(_preview_upstream, BASE_PREFIX))


def _install_opencode_config(opencode_cwd: Path, control_port: int) -> None:
    """Make OpenCode actually load Sage's provider/agents/model — the real fix.

    OpenCode's own server log proves it loads config ONLY from ~/.config/opencode (global) and from
    project config walked up from the *session* dir (the workspace); it NEVER reads SAGE_OPENCODE_CWD.
    So /opt/sage/opencode.json was never loaded, and OpenCode silently fell back to its built-in free
    tier (HTTP 429 FreeUsageLimitError). OPENCODE_CONFIG (env) didn't take effect either.

    So write our config into the global path OpenCode demonstrably reads — no env-var dependency, no
    precedence guesswork. Align the sage-gateway baseURL to the port the shim serves, then write to both
    opencode.json and opencode.jsonc so ours is the last-loaded global source and wins over any free-tier
    default. Keep the source file aligned too (in case OPENCODE_CONFIG is honored). Logs to app logs."""
    import json
    import re

    src = opencode_cwd / "opencode.json"
    try:
        cfg = json.loads(src.read_text())
    except Exception as e:  # missing/unreadable — flag, don't crash the boot
        log.error("[wiring] cannot read %s: %s — OpenCode will stay on its free tier", src, e)
        return
    opts = ((cfg.get("provider") or {}).get("sage-gateway") or {}).get("options") or {}
    base = opts.get("baseURL", "")
    if base:
        opts["baseURL"] = re.sub(r"(://[^/:]+):\d+", rf"\g<1>:{control_port}", base)
    blob = json.dumps(cfg, indent=2) + "\n"
    try:  # keep the source aligned (OPENCODE_CONFIG path, if honored)
        src.write_text(blob)
    except OSError as e:
        log.warning("[wiring] could not rewrite %s: %s", src, e)
    global_dir = Path(os.path.expanduser("~/.config/opencode"))
    try:
        global_dir.mkdir(parents=True, exist_ok=True)
        for name in ("opencode.json", "opencode.jsonc"):
            (global_dir / name).write_text(blob)
        log.warning("[wiring] installed Sage config into %s (model=%s, sage-gateway baseURL -> :%d)",
                    global_dir, cfg.get("model"), control_port)
    except OSError as e:
        log.error("[wiring] could NOT install global opencode config (%s) — OpenCode will use its free tier", e)


def run() -> None:
    """Run the single control app (:8080, preview mounted at /preview) in one process."""
    import asyncio
    import contextlib
    import signal

    import uvicorn

    control_port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
    _install_opencode_config(Path(os.environ.get("SAGE_OPENCODE_CWD", _REPO)), control_port)
    # Loopback locally; Domino's pluggable-tool proxy reaches the tool port from outside the
    # process, so set SAGE_CONTROL_HOST=0.0.0.0 there (matches the Phase-0 spike).
    control_host = os.environ.get("SAGE_CONTROL_HOST", "127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(control_app, host=control_host, port=control_port, log_level="info"))

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
