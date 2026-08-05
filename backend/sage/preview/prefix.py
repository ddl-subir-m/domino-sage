"""What this process derives from the identity env Domino injects (single source of truth).

Domino's pluggable-tool proxy preserves the path prefix (rewrite:false), so every request arrives
under /<owner>/<project>/notebookSession/<runId>/. We derive it ONCE from the env Domino injects
and thread the SAME value into request routing (strip) and Vite's `base` (bake) — so the two can't
drift. Empty when not in a Domino workspace, which collapses everything to naked-localhost behavior.

The same DOMINO_PROJECT_OWNER/DOMINO_PROJECT_NAME pair also names this deployment in the gateway's
cost dashboard (domino_project_label), so both readers of that env live here.
"""
from __future__ import annotations

import os


def domino_base_prefix() -> str:
    """The path prefix (no trailing slash), e.g. "/sub_user/Sage/notebookSession/abc123", or ""."""
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
