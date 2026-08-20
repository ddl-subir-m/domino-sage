"""Probe: what a published App's sharing setting is called on the way back (#12).

`publish_guard.OPEN_VISIBILITY` is a guess. Sage SETS `visibility: "GRANT_BASED"` when it creates
an App, verified live, but nothing has yet read the value back, so neither the field's name in the
detail response nor Domino's names for the open settings are known. Until they are, the guard
treats an unrecognised value as not-open, and a re-publish of an app somebody re-shared goes
through.

This probe closes that. It does not know the field's name either, which is the point: it walks the
whole detail response and prints every path holding a value that looks like a sharing setting.

Run it TWICE, inside the workspace of a project that has a published app:

    uv run python -m sage.tools.app_visibility

    # then change the app's sharing in Domino's own settings page, and run it again

Two runs give both halves — the closed value Sage set, and the open value to match on. Put the open
one into `OPEN_VISIBILITY`, drop the others, and the guard can fail closed.

Env: DOMINO_API_HOST and the token sidecar (both present in any Domino workspace). DOMINO_PROJECT_ID
scopes the search to this project. Pass an app id as the one argument to skip the search.
"""
from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import httpx

from ..gateway.client import DEFAULT_SIDECAR_URL, sidecar_token

_APPS_PATH = "/api/apps/beta/apps"
# Substrings that mark a value as a sharing setting rather than a name or a status. Deliberately
# loose — the whole question is which words Domino uses, so the probe must not filter on the answer.
_LOOKS_LIKE = ("GRANT", "PUBLIC", "PRIVATE", "ANYONE", "ANONYM", "SHARED", "ORGANIZATION")


def _walk(node: object, path: str = "") -> list[tuple[str, str]]:
    """Every (path, value) in the response whose value reads like a sharing setting."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _walk(v, f"{path}[{i}]")
    elif isinstance(node, str) and any(w in node.upper() for w in _LOOKS_LIKE):
        out.append((path, node))
    return out


def main() -> int:
    host = os.environ.get("DOMINO_API_HOST", "").rstrip("/")
    if not host:
        print("no DOMINO_API_HOST — run this inside a Domino workspace.")
        return 2
    token = sidecar_token(os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))
    headers = {"Authorization": f"Bearer {token()}", "Accept": "application/json"}

    app_id = sys.argv[1] if len(sys.argv) > 1 else ""
    with httpx.Client(timeout=30.0) as client:
        if not app_id:
            # The beta list is global (every app on the deployment), so the project match is made
            # here rather than trusted to ?projectId= — same reason `list_project_apps` does.
            project_id = os.environ.get("DOMINO_PROJECT_ID", "")
            r = client.get(f"{host}{_APPS_PATH}", params={"offset": 0, "limit": 100}, headers=headers)
            r.raise_for_status()
            items = r.json().get("items") or []
            mine = [a for a in items if (a.get("project") or {}).get("id") == project_id]
            if not mine:
                print(f"no published app found for project {project_id!r}. "
                      f"Publish one first, or pass an app id as the argument.")
                return 1
            app_id = str(mine[0].get("id") or "")
            print(f"app: {mine[0].get('name')!r}  id={app_id}")

        r = client.get(f"{host}{_APPS_PATH}/{app_id}", headers=headers)
        r.raise_for_status()
        detail = r.json()

    print("\ntop-level keys:", ", ".join(sorted(detail)) if isinstance(detail, dict) else type(detail))
    hits = _walk(detail)
    print("\nvalues that look like a sharing setting:")
    for path, value in hits:
        print(f"  {path} = {value!r}")
    if not hits:
        print("  (none — dump below, find it by eye)")
        print(json.dumps(detail, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
