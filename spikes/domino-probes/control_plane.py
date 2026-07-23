"""Phase-0 STEP 0.3 — probe Domino's control-plane REST API from inside a workspace.

Purpose: learn the request payloads the "New app" hub (Phase 4) needs. The v4 swagger lists the
endpoints but NOT their request bodies, so we discover them live. READ-ONLY by default (safe GETs
to learn response shapes). Set PROBE_CREATE=1 to also create a throwaway project + workspace +
session with best-guess bodies and print exactly what the API accepted/rejected — then it cleans
up after itself (deletes the throwaway project).

Run (in the Domino workspace):
  cd /mnt/code/spikes/domino-probes
  uv run --with httpx control_plane.py                 # discovery only
  PROBE_CREATE=1 uv run --with httpx control_plane.py   # + create/cleanup a throwaway project

Paste the whole output back.
"""
from __future__ import annotations

import json
import os

import httpx

API_HOST = os.environ["DOMINO_API_HOST"].rstrip("/")
API = f"{API_HOST}/v4"  # platform APIs live under /v4 (per Domino app-building docs)
SIDECAR = os.environ.get("DOMINO_API_PROXY", "http://localhost:8899").rstrip("/")
PROJECT_ID = os.environ.get("DOMINO_PROJECT_ID", "")
USER_ID = os.environ.get("DOMINO_USER_ID", "")
RUN_ID = os.environ.get("DOMINO_RUN_ID", "x")
ENV_ID = os.environ.get("DOMINO_ENVIRONMENT_ID", "")
TIER_ID = os.environ.get("DOMINO_HARDWARE_TIER_ID", "")


def token() -> str:
    """Short-lived workspace token from the sidecar (re-acquire per call in real code)."""
    r = httpx.get(f"{SIDECAR}/access-token", timeout=10)
    r.raise_for_status()
    t = r.text.strip()
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


def call(method: str, path: str, **kw) -> tuple[int, object]:
    """Never raises — prints status + body so we can read undocumented shapes."""
    url = f"{API}{path}"
    headers = {"Authorization": token(), "Accept": "application/json"}
    if "json" in kw:
        headers["Content-Type"] = "application/json"
    print(f"\n### {method} {url}")
    if kw.get("json") is not None:
        print("    req body:", json.dumps(kw["json"]))
    try:
        r = httpx.request(method, url, headers=headers, timeout=30, **kw)
    except Exception as e:  # noqa: BLE001
        print(f"    ERROR: {type(e).__name__}: {e}")
        return 0, None
    ct = r.headers.get("content-type", "")
    body: object = r.json() if ct.startswith("application/json") else r.text[:600]
    pretty = json.dumps(body, indent=2)[:2500] if isinstance(body, (dict, list)) else body
    print(f"    -> {r.status_code}\n{pretty}")
    return r.status_code, body


def main() -> None:
    print("token prefix:", token()[:16], "…  API base:", API)

    # --- read-only discovery ---
    call("GET", "/projects?limit=3")
    call("GET", "/users/self")  # want the caller's Mongo ObjectId (ownerId) for the hub
    owner_oid = None
    if PROJECT_ID:
        _, proj = call("GET", f"/projects/{PROJECT_ID}")
        if isinstance(proj, dict):
            owner_oid = proj.get("ownerId")  # caller owns this project → their ObjectId
        call("GET", f"/projects/{PROJECT_ID}/useableEnvironments")
        call("GET", f"/workspace/project/{PROJECT_ID}/workspace?offset=0&limit=10")
        call("GET", f"/projects/{PROJECT_ID}/hardwareTiers")
    print("\nresolved owner ObjectId:", owner_oid)

    if os.environ.get("PROBE_CREATE") != "1":
        print("\n(read-only. Re-run with PROBE_CREATE=1 to test project/workspace creation.)")
        return

    # --- creation flow with the learned contract (ownerId = ObjectId; collaborators/tags required) ---
    new_pid = None
    try:
        name = f"sage-probe-{RUN_ID[:8]}"
        _, body = call(
            "POST", "/projects",
            json={
                "name": name,
                "ownerId": owner_oid,
                "visibility": "Private",
                "description": "throwaway Phase-0 probe",
                "collaborators": [],
                "tags": {"tagNames": []},  # write model wants an object, not [] (learned from 400)
            },
        )
        if isinstance(body, dict):
            new_pid = body.get("id") or body.get("projectId")
        print("\n>>> created project id:", new_pid)

        if new_pid:
            # Body modeled on the observed running-workspace config (initConfig/configTemplate).
            branch = os.environ.get("SPIKE_BRANCH", "feat/domino-workspace-builder")
            _, ws = call(
                "POST", f"/workspace/project/{new_pid}/workspace",
                json={
                    "name": "sage-spike",
                    "environmentId": ENV_ID,
                    "environmentRevisionId": os.environ.get("DOMINO_ENVIRONMENT_REVISION_ID"),
                    "hardwareTierId": {"value": TIER_ID},
                    "tools": ["sageSpike"],
                    "mainGitRepoRef": {"type": "branches", "value": branch},
                    "externalVolumeMounts": [],  # required (learned from 400)
                },
            )
            ws_id = ws.get("id") if isinstance(ws, dict) else None
            print("\n>>> created workspace id:", ws_id)
    finally:
        if new_pid:
            print("\n--- cleanup: deleting throwaway project ---")
            call("DELETE", f"/projects/{new_pid}")


if __name__ == "__main__":
    main()
