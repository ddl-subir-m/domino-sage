"""Probe: can a Domino Dataset actually hold Sage's shared state? (D-Q5 and D-Q7)

`DATASETS-VS-ARTIFACTS-RESEARCH.md` sources everything about Datasets from Domino's own
docs except the two facts that decide whether Sage should move its conversation log and
its generated charts out of git. Both need a real instance. This closes them.

**D-Q5 — does a project collaborator inherit a Dataset role?** Datasets carry their own
ACL; Domino dropped project inheritance in 5.4 and the collaborator table says a Results
Consumer cannot mount one at all. Sage's whole reason to move is that git is the only
store both viewers of a project share. If a collaborator does NOT see the project's own
Dataset, a Dataset is a NARROWER sharing surface than git and the migration makes the
stated problem worse. That single answer can end the discussion, so run it first.

**D-Q7 — is a Dataset mount inside the git work-tree?** `TurnSnapshot` reverts a stopped
turn with `--work-tree=<workspace root>` + `reset --hard` + `clean -fd`, so anything
outside that root is invisible to it. Domino's published tree puts `/mnt/data` beside
`/mnt/code`, not under it, and `AddMountInput` has no `path` field to override with. If
that holds here, moving `examples/<threadId>/` onto a Dataset silently drops stop-button
cleanup and a cancelled turn leaves its PNGs behind.

    uv run python -m sage.tools.dataset_probe

Run it THREE times, from a Domino workspace in the same project each time:
  1. as the project owner        -> the baseline: which Datasets exist, and the owner's role
  2. as a second user added as project **Contributor**
  3. as that same user demoted to **Results Consumer**

Runs 2 and 3 are the answer. The project's own Dataset missing from either run, or present
with only `DatasetRwReader`, means a shared read-write log on a Dataset needs Sage to call
`POST .../grants` itself -- which needs `EditSecurity` and is a new security-relevant
surface, not a storage change.

Env: DOMINO_API_HOST and the token sidecar (both present in any Domino workspace).
SAGE_WORKSPACE_DIR overrides the git work-tree D-Q7 measures against; it defaults to
Domino's own `/mnt/code`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import httpx

from ..assets.provider import DEFAULT_DATASET_MOUNT_ROOTS
from ..gateway.client import sidecar_token
from ..orchestrator import brand

# Artifacts is the other candidate the research rules out on mechanism (manual sync, no
# write-back from an App). Listed here anyway: if it is not even mounted, that is one more
# nail, and it costs nothing to look while we are already walking /mnt.
_ARTIFACT_ROOTS = ("/mnt/artifacts", "/mnt/imported/artifacts")

_DATASETS_PATH = "/api/datasetrw/v2/datasets"
_ROLE_PATH = "/v4/datasetrw/dataset/{id}/role"

# The listing pages, and its default page is TEN. One unpaged call answered 10 of 196 Datasets on
# the dogfood deployment, and 10 rows read exactly like "this user can hardly see anything" -- the
# false negative this probe exists to avoid. Same two numbers as `assets/provider.py`, which pages
# the same endpoint for the product, so the two agree on how much is enough.
_PAGE = 100
_MAX_PAGES = 100


def _get(client: httpx.Client, host: str, path: str,
         params: dict | None = None) -> tuple[int, object]:
    """Status alongside body on purpose. A 403 here is a RESULT -- it is what "the
    collaborator cannot see this Dataset" looks like -- so it must not raise."""
    r = client.get(f"{host}{path}", params=params)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:400]


def _dataset_records(body: object) -> list[dict] | None:
    """The outer records of one listing page, or None when the shape is not one we know.

    `datasetrw/v2` answers `{"datasets": [{"dataset": {...}}], "metadata": {...}}` -- a dict, and
    doubly nested. This probe used to ask `isinstance(body, list)`, so the real answer failed the
    test: it printed "A non-200 IS the finding" over a 200 and returned before reading one role.
    A tool whose whole job is one design answer cannot afford to fail in the shape of that answer.

    The unwrap is `assets/provider.py`'s, so the probe and the product agree on what a record is.
    A bare list is still accepted -- if the endpoint ever answers one, that is not a reason to
    refuse to read it.

    Presence of the key, not truthiness of its value. `datasets or data or []` is what the product
    writes, and there it is harmless because an empty list and a missing key both end the loop.
    Here they must not be one answer: "" is a legible finding, and the emptiest listing of all --
    a Results Consumer who can see NOTHING -- is the single answer D-Q5 most wants. Read with
    `or` it would come back None and be announced as a probe bug nobody should record.
    """
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("datasets", "data"):
            rows = body.get(key)
            if isinstance(rows, list):
                return rows
    return None


def _probe_permissions(host: str, headers: dict[str, str]) -> None:
    print("=" * 72)
    print(brand.text("D-Q5  {dataset} visibility and role, as whoever this token belongs to"))
    print("=" * 72)

    with httpx.Client(headers=headers, timeout=30) as client:
        records: list[dict] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            status, body = _get(client, host, _DATASETS_PATH,
                                {"offset": offset, "limit": _PAGE})
            print(f"\nGET {_DATASETS_PATH}?offset={offset}&limit={_PAGE} -> {status}")
            if status != 200:
                print(json.dumps(body, indent=2)[:1200] if not isinstance(body, str) else body)
                print("\n  A non-200 IS the finding. Record it verbatim.")
                return
            page = _dataset_records(body)
            if page is None:
                # Split from the branch above on purpose. A 200 nobody can parse is a bug in THIS
                # file; a 403 is the research answer. Reporting the first as the second is how a
                # working permission gets written down as a broken one.
                print(json.dumps(body, indent=2)[:1200])
                print("\n  200, but not a shape this probe knows. That makes it a PROBE bug, not")
                print("  a finding about permissions -- do not record it as one. Fix the unwrap")
                print("  in `_dataset_records` against the body above and run it again.")
                return
            records.extend(page)
            if len(page) < _PAGE:
                break
            offset += _PAGE
        else:
            print(f"\n  Stopped after {_MAX_PAGES} pages ({len(records)} records) -- the listing")
            print("  did not terminate. Everything below is a PARTIAL answer; say so when you")
            print("  record it.")

        project = os.environ.get("DOMINO_PROJECT_ID", "")
        print(f"\n  {len(records)} dataset(s) visible"
              + (f"; DOMINO_PROJECT_ID={project}" if project else "; DOMINO_PROJECT_ID unset"))
        print(f"  one role lookup follows per Dataset, so expect {len(records)} more calls")

        for item in records:
            # Field names are not pinned by the research, so print the whole record for the
            # first one and key fields after. The probe must not filter on the answer.
            ds = item.get("dataset") or item
            did = ds.get("id") or ds.get("datasetId") or ""
            name = ds.get("name", "?")
            owner = ds.get("projectId") or ds.get("projectName") or "?"
            mine = " <-- THIS PROJECT" if project and str(owner) == project else ""
            print(f"\n  - {name}  id={did}  project={owner}{mine}")
            if did:
                rstatus, role = _get(client, host, _ROLE_PATH.format(id=did))
                print(f"    role -> {rstatus}: {json.dumps(role)[:300]}")

        if records:
            print("\n  Full first record (field names are not pinned by the research):")
            print("  " + json.dumps(records[0], indent=2)[:900].replace("\n", "\n  "))


def _probe_mounts() -> None:
    print("\n" + "=" * 72)
    print(brand.text("D-Q7  Is a {dataset} mount inside the git work-tree TurnSnapshot reverts?"))
    print("=" * 72)

    root = Path(os.environ.get("SAGE_WORKSPACE_DIR", "/mnt/code")).resolve()
    print(f"\n  git work-tree (TurnSnapshot --work-tree): {root}")
    print(f"  exists: {root.exists()}")

    verdict_inside = []
    for candidate in (*DEFAULT_DATASET_MOUNT_ROOTS, *_ARTIFACT_ROOTS):
        p = Path(candidate)
        if not p.exists():
            print(f"\n  {candidate}  -- absent")
            continue
        entries = sorted(c.name for c in p.iterdir())[:10] if p.is_dir() else []
        resolved = p.resolve()
        # `is_relative_to` on the RESOLVED path, so a symlink from inside the work-tree
        # out to a mount is reported by where the bytes really live, not where it is linked.
        inside = resolved.is_relative_to(root)
        verdict_inside.append(inside)
        print(f"\n  {candidate}  -> {resolved}")
        print(f"    inside work-tree: {inside}"
              + ("   *** clean -fd WOULD reach this ***" if inside else "   (invisible to the stop button)"))
        print(f"    entries: {entries or '(empty)'}")

    print("\n  /mnt top level:")
    mnt = Path("/mnt")
    absent = brand.text("(no /mnt -- not a {platformName} workspace)")
    print(f"    {sorted(c.name for c in mnt.iterdir()) if mnt.exists() else absent}")

    if verdict_inside and not any(verdict_inside):
        print("\n  VERDICT: every mount found is OUTSIDE the work-tree. Moving examples/ onto")
        print(brand.text("  a {dataset} drops stop-button cleanup. That needs a design answer first."))


def main() -> None:
    host = os.environ.get("DOMINO_API_HOST", "").rstrip("/")
    if not host:
        # Mount geometry is still worth printing without a host: it needs no API at all,
        # and it is half the question.
        print(brand.text("no DOMINO_API_HOST -- skipping D-Q5. Run this inside a {platformName} workspace.\n"))
        _probe_mounts()
        sys.exit(1)

    # The sidecar is the only way to a token and it exists ONLY inside a real Domino
    # workspace, so a laptop run always fails here. D-Q7 needs no API at all -- report the
    # failure and carry on rather than losing half the probe to a traceback.
    try:
        token = sidecar_token()()
    except Exception as e:
        print(f"token sidecar unreachable ({type(e).__name__}: {e}) -- skipping D-Q5.")
        print(brand.text("D-Q5 only answers anything from inside a {platformName} workspace anyway.\n"))
    else:
        _probe_permissions(host, {"Authorization": f"Bearer {token}", "Accept": "application/json"})

    _probe_mounts()

    print("\nRecord this run under docs/live-runs/, and say which user and which project role"
          "\nit was run as -- the output is meaningless without that.")


if __name__ == "__main__":
    main()
