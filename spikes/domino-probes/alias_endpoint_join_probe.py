#!/usr/bin/env python3
"""Can preflight tell that the endpoint behind an LLM Alias is STOPPED? (#21)

Run INSIDE a Domino workspace on cloud-dogfood.

The shape of the answer is already established offline (public-api.json + the live runs in
DOMINO-PRIMITIVES.md): endpoint status lives on `GET /api/gen-ai/beta/endpoints`, one
deployment-wide call, and the alias record already carries `endpoint_url` to join on. What is
NOT established is which field that URL joins to, and whether the premise #21 rests on is even
true. Five questions, in the order that decides whether #21 is small, large, or unnecessary:

  Q1. Does a STOPPED endpoint still appear in `/v1/models`?
      #21's whole premise. Asserted in the issue, never recorded from a run. If a stopped
      endpoint DROPS OUT of `/v1/models`, then `unresolved_slots` already catches this today
      and #21 shrinks to a wording change. Checked first because it can end the ticket.

  Q2. Which endpoint field does the alias `endpoint_url` match — `id`, `url`, or `vanityUrl`?
      The spec has all three. DOMINO-PRIMITIVES.md:211 calls it "that vanity id" but the value
      sits in a `url` path. This decides the one line the join is written on.

  Q3. Do the aliases that are NOT Domino-hosted (sonnet, opus, gpt-5.4) miss the join cleanly?
      They must read as "not a hosted endpoint", never as "stopped" (#21 criterion 4). If they
      carry an endpoint_url that accidentally matches something, the join is unsafe.

  Q4. Does the unscoped listing answer for THIS caller?
      The Model API listing needs `projectId` to dodge a 403 (DOMINO-PRIMITIVES.md). This one's
      summary says "accessible by the user" and a live run returned 18 items unscoped on
      2026-08-18 — but that was one caller. If it needs scoping, the cross-project sovereign
      endpoint (which is exactly the qwen-2-5 case) may not be listable at all.

  Q5. Does `/api/providers`.health_status reflect a stopped endpoint?
      The rejected alternative. DOMINO-PRIMITIVES.md recommends it but flags it unverified in
      the stopped case. Settled here so the fork closes instead of staying open.

Read-only. No GPU: this probe never calls a model, only lists. Free to run.

Usage (in a Domino workspace):
    cd /mnt/code/spikes/domino-probes
    GATEWAY_BASE_URL=https://<host>/apps/<id>/v1 python3 alias_endpoint_join_probe.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY = os.environ.get("GATEWAY_BASE_URL", "").rstrip("/")
ROOT = GATEWAY.rsplit("/v1", 1)[0]          # /api/aliases sits at the gateway root, not under /v1
HOST = os.environ.get("DOMINO_API_HOST", "").rstrip("/")
PROXY = os.environ.get("DOMINO_API_PROXY", "http://localhost:8899").rstrip("/")
KEY = os.environ.get("GATEWAY_API_KEY")     # dgw_ token, when running off-sidecar


def sidecar_token():
    try:
        with urllib.request.urlopen(PROXY + "/access-token", timeout=10) as r:
            return r.read().decode().strip()
    except Exception:
        return None


TOK = sidecar_token()


def get(url, tok):
    """One GET. Returns (status, parsed-or-None, note).

    An unauthenticated call to a Domino-hosted endpoint answers 200 with a Keycloak LOGIN PAGE
    in HTML, not JSON (DOMINO-PRIMITIVES.md). Status alone is not enough — inspect the body.
    """
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}" if tok else "",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            if raw.lstrip()[:9].lower().startswith(b"<!doctype") or raw.lstrip()[:5].lower() == b"<html":
                return r.status, None, "200-HTML (login page, NOT authenticated)"
            return r.status, json.loads(raw), ""
    except urllib.error.HTTPError as e:
        return e.code, None, e.read()[:200].decode("utf8", "replace")
    except Exception as e:
        return "ERR", None, f"{type(e).__name__}: {e}"


def records(payload):
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        return [r for r in (payload.get("data") or payload.get("items") or []) if isinstance(r, dict)]
    return []


if not GATEWAY or not HOST:
    sys.exit("set GATEWAY_BASE_URL and DOMINO_API_HOST")
print(f"gateway={GATEWAY}\napi host={HOST}\nsidecar token: {'yes' if TOK else 'NO — will fail'}\n")

# ---- fetch the three listings -----------------------------------------------------------------
st_a, aliases_raw, note_a = get(f"{ROOT}/api/aliases", KEY or TOK)
st_m, models_raw, note_m = get(f"{GATEWAY}/models", KEY or TOK)
st_e, eps_raw, note_e = get(f"{HOST}/api/gen-ai/beta/endpoints", TOK)          # unscoped — Q4
st_p, provs_raw, note_p = get(f"{ROOT}/api/providers", KEY or TOK)

aliases, models, eps, provs = (records(x) for x in (aliases_raw, models_raw, eps_raw, provs_raw))
print(f"GET /api/aliases                 -> {st_a} {note_a} ({len(aliases)} records)")
print(f"GET {{gateway}}/v1/models          -> {st_m} {note_m} ({len(models)} ids)")
print(f"GET /api/gen-ai/beta/endpoints   -> {st_e} {note_e} ({len(eps)} endpoints)   <-- Q4")
print(f"GET /api/providers               -> {st_p} {note_p} ({len(provs)} providers)")

if st_e != 200:
    print("\n>>> Q4 FAILED. The unscoped listing does not answer for this caller.")
    print("    Retry with ?projectId=<id>. If only the scoped call works, a cross-project")
    print("    sovereign endpoint may be unlistable and #21 cannot be built this way.")

accessible = {str(r.get("id")) for r in models if r.get("id")}

# ---- the endpoint table ------------------------------------------------------------------------
print("\n" + "=" * 78)
print("HOSTED GenAI ENDPOINTS — status is on currentVersion, which is OPTIONAL")
print("=" * 78)
by_id, by_url, by_vanity = {}, {}, {}
for e in eps:
    cv = e.get("currentVersion") or {}
    row = {
        "name": e.get("name"),
        "status": cv.get("status"),          # None when there is no current version at all
        "id": e.get("id"),
        "url": (e.get("url") or "").rstrip("/"),
        "vanityUrl": (e.get("vanityUrl") or "").rstrip("/"),
    }
    for key, table in ((row["id"], by_id), (row["url"], by_url), (row["vanityUrl"], by_vanity)):
        if key:
            table[key] = row
    print(f"  {str(row['status'] or '<no currentVersion>'):<18} {str(row['name'])[:30]:<30} {row['url'][:60]}")

stopped = [r for r in by_id.values() if r["status"] in ("Stopped", "Failed", "BuildFailed")]
print(f"\ntotal={len(by_id)} running={sum(1 for r in by_id.values() if r['status'] == 'Running')} "
      f"not-serving={len(stopped)}")

# ---- Q2 + Q3: the join -------------------------------------------------------------------------
print("\n" + "=" * 78)
print("THE JOIN — alias.endpoint_url against endpoint id / url / vanityUrl")
print("=" * 78)
field_hits = {"id": 0, "url": 0, "vanityUrl": 0}
joined, unjoined = [], []
for a in aliases:
    name = a.get("name") or a.get("id")
    eu = (a.get("endpoint_url") or "").rstrip("/")
    if not eu:
        unjoined.append((name, a.get("provider_type"), "<no endpoint_url>"))
        continue
    # try the URL whole, and with the trailing /v1 removed — the alias URL ends /v1, the
    # endpoint's url may not
    cands = {eu, eu.rsplit("/v1", 1)[0].rstrip("/")}
    # and the last UUID-looking path segment, for the id form
    segs = [s for s in eu.split("/") if s]
    hit = None
    for c in cands:
        if c in by_url:
            hit, field = by_url[c], "url"; break
        if c in by_vanity:
            hit, field = by_vanity[c], "vanityUrl"; break
    if not hit:
        for s in segs:
            if s in by_id:
                hit, field = by_id[s], "id"; break
    if hit:
        field_hits[field] += 1
        joined.append((name, field, hit["status"], hit["name"], name in accessible))
        print(f"  JOIN  {str(name)[:24]:<24} via {field:<10} -> {str(hit['status']):<12} "
              f"in/v1/models={'yes' if name in accessible else 'NO ':<3} "
              f"endpoint={str(hit['name'])[:24]}")
    else:
        unjoined.append((name, a.get("provider_type"), eu[:60]))

print(f"\n  matched by field: {field_hits}   <-- Q2: the winner is the field to join on")
print(f"\n  NOT joined ({len(unjoined)}) — these must read 'not a hosted endpoint', never 'stopped':  <-- Q3")
for name, ptype, why in unjoined:
    print(f"    {str(name)[:24]:<24} provider_type={str(ptype):<18} {why}")

# ---- Q1: the premise ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print("Q1 — DOES A STOPPED ENDPOINT STILL APPEAR IN /v1/models?  (#21's premise)")
print("=" * 78)
dead = [(n, s) for n, f, s, en, acc in joined if s not in ("Running",) and acc]
alive_but_gone = [(n, s) for n, f, s, en, acc in joined if s == "Running" and not acc]
for n, s in dead:
    print(f"  {n}: endpoint {s}, but the alias IS in /v1/models -> preflight says OK today")
if not dead:
    print("  No alias in /v1/models points at a non-Running endpoint right now.")
    print("  Either nothing is stopped, or /v1/models already filters them.")
    print("  >>> To settle it: stop a hosted endpoint you own, re-run, and look here again.")
for n, s in alive_but_gone:
    print(f"  {n}: endpoint Running but NOT in /v1/models (a grant issue, not a status one)")

# ---- Q5: the rejected alternative --------------------------------------------------------------
print("\n" + "=" * 78)
print("Q5 — /api/providers health_status, the alternative source")
print("=" * 78)
for p in provs:
    print(f"  {str(p.get('name'))[:28]:<28} type={str(p.get('provider_type')):<16} "
          f"health={p.get('health_status')!r} last_check={p.get('last_health_check')!r}")
if provs and all(p.get("health_status") is None for p in provs):
    print("\n  health_status absent on every provider -> alternative is dead, use the endpoints listing.")

print("""
==================================================================================
WHAT TO DO WITH THIS OUTPUT
  Q1 dead list non-empty  -> #21 is real as written. Build it.
  Q1 dead list empty      -> stop an endpoint you own and re-run before believing it.
  Q2 winner               -> the field the join is written on.
  Q3 unjoined list        -> every one must be reported as unknown, not stopped.
  Q4 non-200              -> #21 may not be buildable deployment-wide. Say so on the ticket.
  Q5 health populated     -> reconsider; it is one call on a host Sage already talks to.
Record the result in DOMINO-PRIMITIVES.md beside the qwen-2-5 block (line ~197).
==================================================================================""")
