"""What this process derives from the identity env Domino injects (single source of truth).

Two launch paths share one orchestrator:

- Sage Builder workspace (`SAGE_PROXY_MODE=workspace`, default): Domino's pluggable-tool proxy
  preserves the path prefix (rewrite:false), so every request arrives under
  /<owner>/<project>/notebookSession/<runId>/. We derive it ONCE from the env Domino injects
  and thread the SAME value into request routing (strip) and Vite's `base` (bake).
- Workbench App (`SAGE_PROXY_MODE=app`): nginx strips the `/apps-internal/<id>/` mount before
  the request reaches us. The prefix must be empty — baking the workspace notebookSession path
  would break `./preview/` and `./api`.

Empty when not in a Domino workspace, which collapses everything to naked-localhost behavior.

The same DOMINO_PROJECT_OWNER/DOMINO_PROJECT_NAME pair also names this deployment in the gateway's
cost dashboard (domino_project_label), so both readers of that env live here.
"""
from __future__ import annotations

import os
from pathlib import Path


def proxy_is_app() -> bool:
    """True when this process is the published Workbench App, not a Sage Builder workspace."""
    return os.environ.get("SAGE_PROXY_MODE", "").strip().lower() == "app"


def publish_available(workspace_dir: Path | None = None) -> bool:
    """Whether this container may Publish a Built App through the control plane.

    The Workbench App's DOMINO_PROJECT_ID is Sage itself; publishing from there would ship Sage,
    not a user's app. A dogfood scratch dir that is not `/mnt/code` is the same hazard.
    """
    if proxy_is_app():
        return False
    raw = workspace_dir if workspace_dir is not None else os.environ.get("SAGE_WORKSPACE_DIR")
    if not raw:
        return True
    ws = Path(raw)
    mnt = Path("/mnt/code")
    try:
        if mnt.exists() and ws.resolve() != mnt.resolve():
            return False
    except OSError:
        return True
    return True


def domino_base_prefix() -> str:
    """The path prefix (no trailing slash), e.g. "/sub_user/Sage/notebookSession/abc123", or ""."""
    if proxy_is_app():
        # App nginx already stripped the mount. SAGE_BASE_PREFIX is only for tests / odd deploys.
        return os.environ.get("SAGE_BASE_PREFIX", "").rstrip("/")
    owner = os.environ.get("DOMINO_PROJECT_OWNER")
    project = os.environ.get("DOMINO_PROJECT_NAME")
    run_id = os.environ.get("DOMINO_RUN_ID")
    if owner and project and run_id:
        return f"/{owner}/{project}/notebookSession/{run_id}"
    # Local dev / tests: honor an explicit override, else empty.
    return os.environ.get("SAGE_BASE_PREFIX", "").rstrip("/")


def domino_project_label(fallback: str = "") -> str:
    """Human-readable name for this Sage deployment, e.g. "sub_user/Sage". Sent as the
    `sage-project` cost tag so a build can be picked out of the gateway's usage dashboard.

    Never the DOMINO_PROJECT_ID hash: the value's whole job is to be recognisable in a Group By
    dropdown. The owner is part of it because the gateway's admin usage view shows EVERY user's
    traffic — two people whose project is called "sage-demo" would otherwise merge into one row and
    silently report one build's cost as two. Every step of the fallback stays readable.
    """
    owner = os.environ.get("DOMINO_PROJECT_OWNER")
    project = os.environ.get("DOMINO_PROJECT_NAME")
    if owner and project:
        return f"{owner}/{project}"
    return project or fallback
