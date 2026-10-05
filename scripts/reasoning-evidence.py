#!/usr/bin/env python3
"""Reasoning evidence — rebuild `backend/sage/gateway/reasoning-evidence.json` for one deployment.

`capabilities.resolve` answers what reasoning settings a model may be given, and it answers it ONLY
from this file. A row matches a live Alias when the gateway root and all six identity fields agree
(`route_identity`), so an Alias whose `updated_at` moved on the gateway stops matching its own row,
`resolve` falls through to a bare `RouteCapability`, and every reasoning setting becomes unavailable
— with no error anywhere, because a missing proof is indistinguishable from a model that offers
nothing. That is the failure this script exists to make cheap to repair. Before it, the file was
assembled by hand for #479 and there was no way to refresh it.

Evidence is DEPLOYMENT-SPECIFIC (ADR-0066). The gateway root is part of the identity, so a row
measured on one deployment says nothing about another, and this script only ever rewrites the rows
whose root matches the `GATEWAY_BASE_URL` it ran against. Rows for other deployments are copied
through untouched.

The file needs two halves, from two places:

    identity    GET {gateway}/v1/models    the alias ids THIS caller may use, permission-filtered
                GET {gateway}/api/aliases  the control-plane metadata: provider_*, updated_at
                joined on `name` OR `id`, the way `resources.provider.join_aliases` joins them

    capability  measured by `sage.gateway.measure`, one alias at a time — read that module for
                what is asked and why

An existing row's `reason` is carried over by name and root, because an Alias whose metadata moved
is the same model and the sentence about it still holds.

Reads GATEWAY_BASE_URL and GATEWAY_API_KEY out of backend/.env in-process and never prints the key.
That value ALREADY ENDS IN /v1 (see gateway/client.py); `/api/aliases` hangs off the root BELOW it,
so both forms are derived here rather than pasted.

Usage:

    reasoning-evidence.py --all              measure every alias this key can reach
    reasoning-evidence.py <alias> [...]      measure only the named aliases
    reasoning-evidence.py --all --write      and write the file, keeping other deployments' rows

Exit is non-zero if any alias went unmeasured, so a sweep that half-failed cannot read as clean,
and --write refuses to touch the file in that case: a partial row is worse than no row, because it
looks complete and the levels missing from it cannot be told apart from refused.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sage.gateway.measure import Prober, detail

EVIDENCE = ROOT / "backend/sage/gateway/reasoning-evidence.json"
TIMEOUT_S = 45
# The identity `capabilities.route_identity` reads. Recorded verbatim off the control-plane record,
# never normalised: a row that "tidies" a field no longer matches the Alias it describes.
IDENTITY = ("id", "name", "provider_id", "provider_type", "provider_model", "updated_at")


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / "backend" / ".env"
    if not path.exists():
        sys.exit(f"no {path} — copy .env.example and fill in GATEWAY_BASE_URL / GATEWAY_API_KEY")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    if not env.get("GATEWAY_BASE_URL"):
        sys.exit(f"GATEWAY_BASE_URL is empty in {path}")
    return env


def _root() -> str:
    return _env()["GATEWAY_BASE_URL"].rstrip("/").removesuffix("/v1")


def _call(url: str, body: dict | None = None) -> tuple[int, str]:
    """(status, body-or-explanation). A transport failure is reported as status 0 rather than
    raised, so one dead alias cannot end a sweep — and 0 is not a measurement, which is what keeps
    it out of the file."""
    env = _env()
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {env.get('GATEWAY_API_KEY', '')}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace")
    except Exception as err:  # noqa: BLE001
        return 0, f"ERROR: {type(err).__name__}: {err}"


def _json(url: str) -> list[dict]:
    status, raw = _call(url)
    if status != 200:
        sys.exit(f"{url} answered {status}: {detail(raw)}")
    try:
        parsed = json.loads(raw)
    except ValueError:
        sys.exit(f"{url} answered 200 but not JSON: {detail(raw)}")
    # `/v1/models` follows OpenAI and wraps its rows; `/api/aliases` returns a bare array.
    rows = parsed.get("data") if isinstance(parsed, dict) else parsed
    return [row for row in (rows or []) if isinstance(row, dict)]


def identities() -> list[dict]:
    """The accessible aliases, each carrying the six fields `route_identity` reads.

    Intersected, never taken from one side: `/api/aliases` lists registrations the caller may hold
    no grant for, and a row measured for one of those describes a model nobody here can run.
    """
    root = _root()
    accessible = {str(row["id"]) for row in _json(f"{root}/v1/models") if row.get("id")}
    out = []
    for record in _json(f"{root}/api/aliases"):
        name, rid = str(record.get("name") or ""), str(record.get("id") or "")
        # Matched on name OR id, the way `join_aliases` matches: `/v1/models` reports the name a
        # caller must use, the control plane keys on id, and which one is echoed varies.
        if name not in accessible and rid not in accessible:
            continue
        out.append({field: record.get(field) for field in IDENTITY}
                   | {"fallback_chain": record.get("fallback_chain") or [], "gateway": root})
    return sorted(out, key=lambda row: str(row["name"]))


def main(argv: list[str]) -> int:
    write = "--write" in argv
    names = [a for a in argv[1:] if not a.startswith("--")]
    if not names and "--all" not in argv:
        print(__doc__)
        return 2
    existing = json.loads(EVIDENCE.read_text()) if EVIDENCE.exists() else []
    carried = {(str(r["name"]), str(r["gateway"])): r for r in existing}
    root = _root()
    rows = [r for r in identities() if not names or str(r["name"]) in names]
    if not rows:
        sys.exit(f"none of {', '.join(names)} is an accessible alias on {root}")
    prober = Prober(root, _call, print)
    measured = []
    for row in rows:
        print(f"\n{row['name']}")
        measured.append(prober.measure(row, carried.get((str(row["name"]), str(row["gateway"])))))
    good = [row for row in measured if row is not None]
    unmeasured = len(measured) - len(good)
    print(f"\n{len(good)} measured, {unmeasured} not, on {root}")
    if not write:
        print(json.dumps(good, indent=2))
        return 1 if unmeasured else 0
    if unmeasured:
        # A half-swept deployment must not half-replace its own rows: the aliases that failed would
        # silently lose the proofs they still had.
        print("NOT WRITTEN — re-run until every alias is measured, or name the ones you want")
        return 1
    # Other deployments' rows are this file's other half and were never in question here.
    kept = [r for r in existing if str(r.get("gateway", "")) != root
            or str(r.get("name", "")) not in {str(r["name"]) for r in good}]
    EVIDENCE.write_text(json.dumps(sorted(kept + good, key=lambda r: (r["gateway"], r["name"])),
                                   indent=2) + "\n")
    print(f"wrote {EVIDENCE.relative_to(ROOT)} — {len(kept)} row(s) kept, {len(good)} written")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
