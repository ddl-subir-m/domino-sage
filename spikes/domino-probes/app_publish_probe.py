"""Phase-5 STEP 5.2 — probe Domino's PUBLIC apps API to lock the "Publish" create body.

Purpose: learn exactly what `POST /api/apps/beta/apps` accepts for a git-based project's
app, and whether creating an app with a `version` also LAUNCHES it (one-call publish) or
needs a follow-up start. READ-ONLY by default. Set PROBE_CREATE=1 to create a throwaway
app with the best-guess body, print what the API accepted/rejected, then delete it.

Run (in the git-based Domino project workspace whose app you want to publish):
  cd /mnt/code/spikes/domino-probes
  uv run --with httpx app_publish_probe.py                 # discovery only
  PROBE_CREATE=1 uv run --with httpx app_publish_probe.py   # + create/cleanup a throwaway app

Paste the whole output back.
"""
from __future__ import annotations

import json
import os

import httpx

API_HOST = os.environ["DOMINO_API_HOST"].rstrip("/")  # apps beta API is NOT under /v4
SIDECAR = os.environ.get("DOMINO_API_PROXY", "http://localhost:8899").rstrip("/")
PROJECT_ID = os.environ.get("DOMINO_PROJECT_ID", "")
ENV_ID = os.environ.get("DOMINO_ENVIRONMENT_ID", "")
TIER_ID = os.environ.get("DOMINO_HARDWARE_TIER_ID", "")
RUN_ID = os.environ.get("DOMINO_RUN_ID", "x")
# gitRef the published version deploys from. "head" = latest on the project's branch;
# override to pin a branch/commit, e.g. GIT_REF_TYPE=branches GIT_REF_VALUE=main.
GIT_REF_TYPE = os.environ.get("GIT_REF_TYPE", "head")
GIT_REF_VALUE = os.environ.get("GIT_REF_VALUE", "")
ENTRY_POINT = os.environ.get("ENTRY_POINT", "app.sh")
VISIBILITY = os.environ.get("APP_VISIBILITY", "GRANT_BASED")

APPS = "/api/apps/beta/apps"


def token() -> str:
    """Short-lived workspace token from the sidecar (re-acquire per call in real code)."""
    r = httpx.get(f"{SIDECAR}/access-token", timeout=10)
    r.raise_for_status()
    t = r.text.strip()
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


def call(method: str, path: str, **kw) -> tuple[int, object]:
    """Never raises — prints status + body so we can read undocumented shapes."""
    url = f"{API_HOST}{path}"
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


def _git_ref() -> dict:
    ref = {"type": GIT_REF_TYPE}
    if GIT_REF_VALUE:
        ref["value"] = GIT_REF_VALUE
    return ref


def _create_body(name: str) -> dict:
    return {
        "name": name,
        "projectId": PROJECT_ID,
        "visibility": VISIBILITY,
        "entryPoint": ENTRY_POINT,
        "configurationType": "STANDARD",
        "version": {
            "environmentId": ENV_ID,
            "hardwareTierId": TIER_ID,
            "gitRef": _git_ref(),
        },
    }


def main() -> None:
    print("token prefix:", token()[:16], "…  API host:", API_HOST)
    print("project:", PROJECT_ID, "| env:", ENV_ID, "| tier:", TIER_ID,
          "| gitRef:", _git_ref(), "| entryPoint:", ENTRY_POINT)

    # --- read-only discovery: what apps already exist for this project? ---
    call("GET", f"{APPS}?projectId={PROJECT_ID}&offset=0&limit=10")

    name = f"sage-publish-probe-{RUN_ID[:8]}"
    if os.environ.get("PROBE_CREATE") != "1":
        print("\n--- DRY RUN. Exact body POST would send (nothing created): ---")
        print(json.dumps(_create_body(name), indent=2))
        print("\n(Re-run with PROBE_CREATE=1 to create + auto-delete a throwaway app.)")
        return

    if not (PROJECT_ID and ENV_ID and TIER_ID):
        print("\nMISSING one of DOMINO_PROJECT_ID / DOMINO_ENVIRONMENT_ID / "
              "DOMINO_HARDWARE_TIER_ID — cannot create. Aborting.")
        return

    app_id = None
    try:
        status, body = call("POST", APPS, json=_create_body(name))
        if isinstance(body, dict):
            app_id = body.get("id") or body.get("appId")
        print("\n>>> created app id:", app_id, "| status:", status)
        if isinstance(body, dict):
            # KEY QUESTION: is a version already running (create == launch)?
            print(">>> url:", body.get("url"))
            print(">>> currentVersion present?:", "currentVersion" in body,
                  "->", json.dumps(body.get("currentVersion"))[:300])
        if app_id:
            call("GET", f"{APPS}/{app_id}")  # confirm state right after create
            call("GET", f"{APPS}/{app_id}/versions?offset=0&limit=5")
    finally:
        if app_id:
            print("\n--- cleanup: deleting throwaway app ---")
            call("DELETE", f"{APPS}/{app_id}")


if __name__ == "__main__":
    main()
