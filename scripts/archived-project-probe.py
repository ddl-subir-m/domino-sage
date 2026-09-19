#!/usr/bin/env python3
"""Archived-project probe — does an archived Project still come back from the projects listing?

Run it in a workspace on the Sage environment (VS Code is fine; it needs only stdlib).

**The question.** `DominoControlPlane.list_apps` reads `GET /api/projects/beta/projects` with
nothing but `offset`/`limit`, and keeps every project whose `mainRepository.uri` ends in a `sage-*`
repo name. Nothing excludes an archived one. `Door._find_default` then matches on the Domino NAME
alone, so if archived projects are still listed, a viewer whose Default repo was deleted is told to
archive the Project (that is the only remedy Sage has) and lands on the very same broken Project on
the next open — with no way out and no sign that the advice failed.

`archive_project` has no caller anywhere in Sage, so this has never been exercised even once.

**The second question**, from `archive_project`'s own comment: Domino is expected to REFUSE to
archive a project that still contains a workspace ("cannot be archived. It contains N
workspace(s)"). A Default whose repo is gone always holds at least the builder whose launch just
failed, so the refusal — if it is real — sits directly in front of the remedy.

    archived-project-probe.py list
        Read-only. Every `sage-*` Project this token can see, with every top-level field the
        envelope carries, so an `archived`/`status`/`stage` field shows itself if one exists.

    archived-project-probe.py archive <project-id>
        DESTRUCTIVE (soft delete; a Domino admin can restore it). Lists the project's workspaces,
        archives it, then re-reads the listing and says whether it came back. This is the whole
        experiment, and on a Project whose repo is gone it is also the fix.

MEASURED on sage.gcp.cs.domino.tech, 2026-09-13, against a Default whose GitHub repo had been
deleted by hand:

    archived a Project through the Domino UI       -> it LEFT /api/projects/beta/projects
    re-opened the Sage door                        -> a fresh Default, repo and project both new
    the new Default's name                         -> UNSUFFIXED (`sage-<user>-<id>`, no `-N`)

**An archived Project is excluded from the listing**, so `list_apps` needs no archived filter and
the remedy the door prints ("archive the Project, then open Sage again") works. The unsuffixed name
is the load-bearing part of that: the collision check found the base name free on BOTH sides, which
it could not have done had the archived Project still been listed.

Two things the same listing showed, neither of which this probe set out to ask:

- **No archived/status field exists on the envelope at all** — only `description`, `visibility`,
  `ownerId`, `ownerUsername`, `collaborators`, `internalTags`, `isRestricted`. So had the answer
  gone the other way, `list_apps` could not have filtered archived projects out of this payload;
  it would have needed a different endpoint or parameter.
- **The listing returns OTHER USERS' Projects**, Private ones included (three of four rows belonged
  to another user). `list_apps`'s docstring says "The caller's Sage apps" and that is not what the
  API answers. Harmless for `Door._find_default`, because `sage-<user-slug>-<id>` encodes the user
  and a colleague's Default can never match — but `list_apps` has other callers. Not chased here.

The refusal half of the second question is still UNMEASURED: this archive succeeded because the
Project held no workspace (the launch that would have made one is the thing that failed). A Default
that worked before losing its repo still holds stopped builders, so `archive_project`'s comment
about Domino refusing a project that contains workspaces remains untested.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PROJECTS_PATH = "/api/projects/beta/projects"
SIDECAR = "http://localhost:8899/access-token"


def _host() -> str:
    host = (os.environ.get("DOMINO_API_HOST") or "").strip().rstrip("/")
    if not host:
        sys.exit("DOMINO_API_HOST is not set — run this inside a Domino workspace.")
    return host


def _headers() -> dict[str, str]:
    """Re-acquired per call: the sidecar token expires quickly."""
    try:
        with urllib.request.urlopen(SIDECAR, timeout=5) as r:
            token = r.read().decode().strip()
        if token:
            return {"Authorization": token if token.startswith("Bearer ") else f"Bearer {token}"}
    except Exception:  # noqa: BLE001, S110
        pass
    key = (os.environ.get("DOMINO_USER_API_KEY") or "").strip()
    if not key:
        sys.exit("no sidecar token and no DOMINO_USER_API_KEY — nothing to authenticate with.")
    # Bearer, not X-Domino-Api-Key: the beta projects API takes a PAT only as a Bearer token.
    return {"Authorization": f"Bearer {key}"}


def _call(method: str, path: str) -> tuple[int, object]:
    req = urllib.request.Request(_host() + path, method=method, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        body, status = e.read().decode(), e.code
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body[:800]


def _sage_projects() -> list[dict]:
    status, data = _call("GET", f"{PROJECTS_PATH}?offset=0&limit=200")
    if status >= 400:
        sys.exit(f"GET {PROJECTS_PATH} -> {status}: {data}")
    envelopes = data.get("projects") if isinstance(data, dict) else data
    out = []
    for env in envelopes or []:
        p = env.get("project") if isinstance(env, dict) and "project" in env else env
        if not isinstance(p, dict):
            continue
        uri = (p.get("mainRepository") or {}).get("uri") if isinstance(p.get("mainRepository"), dict) else None
        repo = uri.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") if uri else ""
        if repo.startswith("sage-"):
            out.append(p)
    return out


def cmd_list() -> None:
    projects = _sage_projects()
    print(f"{len(projects)} sage-* Project(s) in the listing\n")
    for p in projects:
        print(f"  {p.get('id')}  {p.get('name')}")
        print(f"    repo: {(p.get('mainRepository') or {}).get('uri')}")
        # Every other top-level key, verbatim: if Domino marks archived state anywhere, it is here.
        other = {k: v for k, v in p.items() if k not in ("id", "name", "mainRepository")}
        for k, v in sorted(other.items()):
            rendered = json.dumps(v) if not isinstance(v, str) else v
            print(f"    {k}: {rendered[:160]}")
        print()
    print("Look for a status/stage/archived field above. If none exists, the listing cannot")
    print("distinguish an archived Project — run `archive <id>` to find out what it does.")


def cmd_archive(project_id: str) -> None:
    before = {p.get("id") for p in _sage_projects()}
    if project_id not in before:
        sys.exit(f"{project_id} is not in the sage-* listing — nothing to do.")

    status, workspaces = _call("GET", f"/v4/workspace/project/{project_id}/workspace?offset=0&limit=20")
    items = workspaces.get("workspaces") or workspaces.get("data") or [] if isinstance(workspaces, dict) else workspaces
    print(f"workspaces in the project: {len(items) if isinstance(items, list) else '?'} (GET -> {status})")
    if isinstance(items, list):
        for w in items:
            if isinstance(w, dict):
                print(f"  {w.get('id')}  state={w.get('state') or w.get('status')}  deleted={w.get('deleted')}")

    print(f"\nDELETE {PROJECTS_PATH}/{project_id}")
    status, body = _call("DELETE", f"{PROJECTS_PATH}/{project_id}")
    print(f"  -> {status}: {json.dumps(body)[:400] if not isinstance(body, str) else body[:400]}")
    if status >= 400:
        print("\nANSWER (refusal): Domino refused the archive. If the body names the workspaces,")
        print("then `archive the Project` is not a remedy on its own, and the error text Sage")
        print("shows has to tell the person to remove the workspaces first.")
        return

    after = {p.get("id") for p in _sage_projects()}
    still_there = project_id in after
    print(f"\nre-read the listing: {len(after)} sage-* Project(s)")
    print(f"\nANSWER: an archived Project is {'STILL RETURNED' if still_there else 'GONE'} from "
          f"{PROJECTS_PATH}.")
    if still_there:
        print("  -> list_apps needs an archived filter. Until it has one, the door re-matches the")
        print("     archived Project and the advice Sage prints cannot work.")
    else:
        print("  -> list_apps needs no change, and the door will create a fresh Default next open.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["list"]:
        cmd_list()
    elif args[:1] == ["archive"] and len(args) == 2:
        cmd_archive(args[1])
    else:
        sys.exit(__doc__.split("MEASURED")[0].strip())
