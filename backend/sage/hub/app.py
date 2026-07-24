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
from ..provision.service import HubService

log = logging.getLogger("sage.hub")
logging.basicConfig(level=logging.INFO)

_UI = Path(__file__).resolve().parent / "ui" / "index.html"
_REPO = Path(__file__).resolve().parents[3]
_TEMPLATE = Path(os.environ.get("SAGE_TEMPLATE", _REPO / "template" / "react-vite"))
# Where to read the ambient git credential from (the hub's own repo checkout).
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


def _build_hub() -> tuple[HubService, str]:
    """Real hub on Domino (DOMINO_API_HOST set); an all-fakes hub locally for UI work.

    Returns (service, mode) where mode is "domino" or "fake"."""
    api_host = os.environ.get("DOMINO_API_HOST")
    if not api_host:
        log.info("no DOMINO_API_HOST — hub running in fake mode (local UI dev)")
        # No-op the seed: fake mode has no real remote to push to.
        return HubService(FakeControlPlane(), FakeRepoProvider(), _TEMPLATE, seed=lambda *a, **k: None), "fake"

    remote = credentials.remote_for(_GIT_CWD)
    if remote is None or remote.provider != "github":
        # Only GitHub is a verified adapter in v1; other providers fall back to BYO-repo (not yet
        # wired), so we refuse rather than provision against an unverified contract.
        raise RuntimeError(
            f"unsupported/undetected git provider at {_GIT_CWD} "
            f"({remote.provider if remote else 'no origin'}); v1 hub supports github only"
        )

    host = remote.host
    repo_provider = GitHubProvider(token_provider=lambda: _require_token(host))
    control_plane = DominoControlPlane(
        api_host,
        sidecar_token(),
        environment_id=os.environ["DOMINO_ENVIRONMENT_ID"],
        environment_revision_id=os.environ.get("DOMINO_ENVIRONMENT_REVISION_ID"),
        hardware_tier_id=os.environ["DOMINO_HARDWARE_TIER_ID"],
        builder_tool=os.environ.get("SAGE_BUILDER_TOOL", "sageBuilder"),
    )
    return HubService(control_plane, repo_provider, _TEMPLATE), "domino"


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
        return JSONResponse({"error": f"Couldn't create the app: {e}"}, status_code=502)
    return JSONResponse(
        {
            "id": created.project.id,
            "name": created.project.name,
            "repo": created.repo.full_name,
            "git_url": created.repo.clone_url,
            "open_url": created.open_url,
            # LIVE-VERIFY seam: raw workspace-create response, so we can pin down the open-URL field.
            "workspace": created.workspace,
        }
    )


@app.post("/api/apps/{project_id}/open")
async def open_app(project_id: str) -> JSONResponse:
    try:
        result = await run_in_threadpool(hub.open_app, project_id)
    except Exception as e:
        log.exception("open_app failed")
        return JSONResponse({"error": f"Couldn't open the app: {e}"}, status_code=502)
    return JSONResponse(result)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("SAGE_CONTROL_HOST", "0.0.0.0"),
        port=int(os.environ.get("SAGE_CONTROL_PORT", "8888")),
    )
