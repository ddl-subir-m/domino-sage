"""Sage hub — the "New app" control plane (Phase 4.2/4.3).

A small single-page app (its own Domino App / pluggable tool) that lists the caller's Sage apps and
provisions new ones: name -> private repo -> seeded git-based Domino project -> running builder. It
reuses the hub workspace's own git credential (to call the provider API) and the Domino env/hardware
ids Domino injects (for the child workspaces).

Run:  uv run python -m sage.hub.app
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ..gateway.client import sidecar_token
from ..preview.prefix import domino_base_prefix
from ..provision import credentials
from ..provision.domino import DominoControlPlane, FakeControlPlane
from ..provision.github import FakeRepoProvider, GitHubProvider
from ..provision.service import HubService, workspace_is_running

log = logging.getLogger("sage.hub")
logging.basicConfig(level=logging.INFO)

_UI = Path(__file__).resolve().parent / "ui" / "index.html"
_REPO = Path(__file__).resolve().parents[3]
_TEMPLATE = Path(os.environ.get("SAGE_TEMPLATE", _REPO / "template" / "react-vite"))
# The git host whose Domino credential the hub uses to create repos + push seeds. Setting it
# explicitly (SAGE_GIT_HOST=github.com) lets the hub run as a plain baked App — no git-based project
# needed, since the token comes from Domino's global `git credential` helper, not any checkout.
_GIT_HOST = os.environ.get("SAGE_GIT_HOST")
# Legacy fallback when SAGE_GIT_HOST is unset: sniff the host off the origin remote of a git-based
# hub project's checkout. Kept so an existing git-based hub deploy keeps working unchanged.
_GIT_CWD = os.environ.get("SAGE_HUB_GIT_CWD", "/mnt/code")

BASE_PREFIX = domino_base_prefix()


class _PrefixMiddleware:
    """Record Domino's proxy prefix as root_path so bare-registered routes match (rewrite:false).
    No-op locally where the prefix is empty. Mirrors the orchestrator's middleware."""

    def __init__(self, app, prefix: str) -> None:
        self._app = app
        self._prefix = prefix

    async def __call__(self, scope, receive, send):
        if self._prefix and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self._prefix or path.startswith(self._prefix + "/"):
                scope = dict(scope)
                scope["root_path"] = self._prefix
        await self._app(scope, receive, send)


def _resolve_git_target() -> tuple[str, str]:
    """Resolve the (host, provider) to provision against. Prefer the explicit SAGE_GIT_HOST (hub as a
    plain baked App); otherwise sniff the origin remote of the checkout at SAGE_HUB_GIT_CWD (legacy
    git-based hub project). Raises when neither yields a host."""
    if _GIT_HOST:
        return _GIT_HOST, credentials.detect_provider(_GIT_HOST)
    remote = credentials.remote_for(_GIT_CWD)
    if remote is None:
        raise RuntimeError(
            f"no git host configured: set SAGE_GIT_HOST (e.g. github.com), or run the hub in a "
            f"git-based project so its origin remote at {_GIT_CWD} can be detected"
        )
    return remote.host, remote.provider


def _build_hub() -> tuple[HubService, str]:
    """Real hub on Domino (DOMINO_API_HOST set); an all-fakes hub locally for UI work.

    Returns (service, mode) where mode is "domino" or "fake"."""
    api_host = os.environ.get("DOMINO_API_HOST")
    if not api_host:
        log.info("no DOMINO_API_HOST — hub running in fake mode (local UI dev)")
        # No-op the seed: fake mode has no real remote to push to.
        return HubService(FakeControlPlane(), FakeRepoProvider(), _TEMPLATE, seed=lambda *a, **k: None), "fake"

    host, provider = _resolve_git_target()
    if provider != "github":
        # Only GitHub is a verified adapter in v1; other providers fall back to BYO-repo (not yet
        # wired), so we refuse rather than provision against an unverified contract.
        raise RuntimeError(
            f"unsupported git provider '{provider}' for host {host!r}; v1 hub supports github only"
        )

    def token_provider() -> str:  # shared by repo create + seed push
        return _require_token(host)

    repo_provider = GitHubProvider(token_provider=token_provider)
    control_plane = DominoControlPlane(
        api_host,
        sidecar_token(),
        environment_id=os.environ["DOMINO_ENVIRONMENT_ID"],
        environment_revision_id=os.environ.get("DOMINO_ENVIRONMENT_REVISION_ID"),
        hardware_tier_id=os.environ["DOMINO_HARDWARE_TIER_ID"],
        builder_tool=os.environ.get("SAGE_BUILDER_TOOL", "sageBuilder"),
        git_host=host,
    )
    return HubService(control_plane, repo_provider, _TEMPLATE, push_token_provider=token_provider), "domino"


def _require_token(host: str) -> str:
    tok = credentials.extract_token(host)
    if not tok:
        raise RuntimeError(f"no HTTPS credential for {host} (SSH-key creds can't be extracted)")
    return tok


hub, MODE = _build_hub()

app = FastAPI(title="Sage hub")
app.add_middleware(_PrefixMiddleware, prefix=BASE_PREFIX)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_UI, headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "mode": MODE, "prefix": BASE_PREFIX}


def _builder_tool_hint() -> str:
    """When a workspace fails to start, explain whether the builder tool is even resolvable for this
    environment — the most common cause (an undefined tool is rejected before any container logs)."""
    cp = getattr(hub, "_cp", None)
    if MODE != "domino" or not hasattr(cp, "available_tools"):
        return ""
    tool = os.environ.get("SAGE_BUILDER_TOOL", "sageBuilder")
    try:
        ids = [t.get("id") for t in cp.available_tools()]
    except Exception as e:  # noqa: BLE001 — best-effort hint, never mask the original error
        return f" (couldn't check available tools: {e})"
    if tool not in ids:
        return (f" — the '{tool}' tool isn't defined in this Environment (available: {ids}). "
                f"Add the sageBuilder block to the Environment's Pluggable Workspace Tools and retry.")
    return f" — '{tool}' is available ({ids}); the cause is elsewhere (env revision or hardware tier)."


@app.get("/api/diag")
async def diag() -> JSONResponse:
    """LIVE-VERIFY: config + the tools Domino resolves for this environment. If builder_tool isn't
    in available_tool_ids, that's why child workspaces fail to start (no logs) — add it to the
    Environment's Pluggable Workspace Tools."""
    info: dict = {
        "mode": MODE,
        "prefix": BASE_PREFIX,
        "config": {
            "environment_id": os.environ.get("DOMINO_ENVIRONMENT_ID"),
            "environment_revision_id": os.environ.get("DOMINO_ENVIRONMENT_REVISION_ID"),
            "hardware_tier_id": os.environ.get("DOMINO_HARDWARE_TIER_ID"),
            "builder_tool": os.environ.get("SAGE_BUILDER_TOOL", "sageBuilder"),
            "project_id": os.environ.get("DOMINO_PROJECT_ID"),
        },
    }
    cp = getattr(hub, "_cp", None)
    if MODE == "domino" and hasattr(cp, "available_tools"):
        try:
            tools = await run_in_threadpool(cp.available_tools)
            ids = [t.get("id") for t in tools]
            info["available_tool_ids"] = ids
            info["builder_tool_available"] = info["config"]["builder_tool"] in ids
        except Exception as e:  # noqa: BLE001 — diag must report the failure, not raise it
            info["available_tools_error"] = str(e)
    return JSONResponse(info)


@app.get("/api/apps")
async def list_apps() -> JSONResponse:
    apps = await run_in_threadpool(hub.list_apps)
    return JSONResponse([{"id": a.id, "name": a.name, "git_url": a.git_url} for a in apps])


@app.post("/api/apps")
async def create_app(request: Request) -> JSONResponse:
    body = await request.json()
    name = (body or {}).get("name", "")
    try:
        created = await run_in_threadpool(hub.create_app, name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:  # provisioning failure — human-readable, not a stack trace
        log.exception("create_app failed")
        msg = f"Couldn't create the app: {e}"
        if "Workspace start" in str(e) or "/workspace/" in str(e):
            msg += await run_in_threadpool(_builder_tool_hint)
        return JSONResponse({"error": msg}, status_code=502)
    return JSONResponse(
        {
            "id": created.project.id,
            "name": created.project.name,
            "repo": created.repo.full_name,
            "git_url": created.repo.clone_url,
            "open_url": created.open_url,
            "workspace_id": created.workspace.get("id") if isinstance(created.workspace, dict) else None,
            # Freshly launched — the session usually isn't running yet; the UI polls status before opening.
            "running": workspace_is_running(created.workspace),
        }
    )


@app.post("/api/apps/{project_id}/open")
async def open_app(project_id: str) -> JSONResponse:
    try:
        result = await run_in_threadpool(hub.open_app, project_id)
    except Exception as e:
        log.exception("open_app failed")
        return JSONResponse({"error": f"Couldn't open the app: {e}"}, status_code=502)
    ws = result.get("workspace")
    result["workspace_id"] = ws.get("id") if isinstance(ws, dict) else None
    return JSONResponse(result)


@app.post("/api/apps/{project_id}/stop")
async def stop_app(project_id: str, request: Request) -> JSONResponse:
    """Stop an app's running builder so it stops consuming a hardware tier."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — empty/invalid body is fine; fall back to newest running
        body = {}
    workspace_id = (body or {}).get("workspace_id")
    try:
        result = await run_in_threadpool(hub.stop_app, project_id, workspace_id)
    except Exception as e:
        log.exception("stop_app failed")
        return JSONResponse({"error": f"Couldn't stop the builder: {e}"}, status_code=502)
    return JSONResponse(result)


@app.post("/api/apps/{project_id}/publish")
async def publish_app(project_id: str) -> JSONResponse:
    """Publish (or re-publish) the app's latest committed code as a live, shareable Domino App."""
    try:
        result = await run_in_threadpool(hub.publish_app, project_id)
    except Exception as e:  # provisioning failure — human-readable, not a stack trace
        log.exception("publish_app failed")
        return JSONResponse({"error": f"Couldn't publish the app: {e}"}, status_code=502)
    return JSONResponse(result)


@app.get("/api/apps/publish-status")
async def publish_status(app_id: str) -> JSONResponse:
    """Deploy status of a published app (running/failed/pending) — the hub polls this after Publish."""
    try:
        result = await run_in_threadpool(hub.publish_status, app_id)
    except Exception as e:
        log.exception("publish_status failed")
        return JSONResponse({"error": f"Couldn't get publish status: {e}"}, status_code=502)
    return JSONResponse(result)


# Per-project delete progress for the non-blocking delete flow. Delete stops the builder, deletes any
# published Apps, deletes workspaces, then archives the project — Domino's async workspace delete
# retries for up to ~75s, so doing it inline blocks long enough that Domino's app proxy times out and
# returns an HTML error page. Instead the DELETE endpoint kicks the work off in a background thread
# and returns immediately; the UI polls delete-status. Only failures are recorded here — success is
# detected by the project vanishing from list_apps() (the async delete truly completing is the real
# signal, and a DELETE call can report failure while the delete still completes).
_delete_state: dict[str, dict] = {}
_delete_lock = threading.Lock()


def _run_delete(project_id: str) -> None:
    try:
        hub.delete_app(project_id)
        with _delete_lock:
            _delete_state.pop(project_id, None)
    except Exception as e:  # noqa: BLE001 — record so delete-status can surface the real reason
        log.exception("delete_app failed")
        with _delete_lock:
            _delete_state[project_id] = {"phase": "failed", "error": str(e)}


@app.delete("/api/apps/{project_id}")
async def delete_app(project_id: str) -> JSONResponse:
    """Kick off a non-blocking delete (stop → delete Apps → delete workspaces → archive) in the
    background and return immediately. The UI polls /delete-status. Soft delete; the repo is kept."""
    with _delete_lock:
        _delete_state[project_id] = {"phase": "deleting", "error": ""}
    threading.Thread(target=_run_delete, args=(project_id,), daemon=True).start()
    return JSONResponse({"deleting": True}, status_code=202)


@app.get("/api/apps/{project_id}/delete-status")
async def delete_status(project_id: str) -> JSONResponse:
    """Poll target for a background delete: done once the project no longer appears in list_apps()
    (the real success signal), failed if the background worker recorded an error, else deleting."""
    try:
        apps = await run_in_threadpool(hub.list_apps)
    except Exception as e:
        log.exception("delete_status failed")
        return JSONResponse({"error": f"Couldn't check delete status: {e}"}, status_code=502)
    still_present = any(a.id == project_id for a in apps)
    with _delete_lock:
        state = _delete_state.get(project_id)
    # A vanished project is success even if the DELETE call reported failure (mirrors _workspace_gone).
    if not still_present:
        return JSONResponse({"phase": "done", "error": ""})
    if state and state["phase"] == "failed":
        return JSONResponse({"phase": "failed", "error": state["error"]})
    return JSONResponse({"phase": "deleting", "error": ""})


@app.get("/api/apps/{project_id}/status")
async def app_status(project_id: str, workspace_id: str | None = None) -> JSONResponse:
    """Poll target: has the app's (just-launched) workspace session reached Running?"""
    try:
        result = await run_in_threadpool(hub.workspace_status, project_id, workspace_id)
    except Exception as e:
        log.exception("workspace_status failed")
        return JSONResponse({"error": f"Couldn't check status: {e}"}, status_code=502)
    return JSONResponse(result)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("SAGE_CONTROL_HOST", "0.0.0.0"),
        port=int(os.environ.get("SAGE_CONTROL_PORT", "8888")),
    )
