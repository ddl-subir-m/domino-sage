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
# The permission filter on its own. /v1/models is permission AND (maybe) status; this is
# permission alone, so the two together separate "no grant" from "not serving" (Q1).
st_acc, acc_raw, note_acc = get(f"{ROOT}/api/aliases/accessible", KEY or TOK)

aliases, models, eps, provs = (records(x) for x in (aliases_raw, models_raw, eps_raw, provs_raw))
print(f"GET /api/aliases                 -> {st_a} {note_a} ({len(aliases)} records)")
print(f"GET {{gateway}}/v1/models          -> {st_m} {note_m} ({len(models)} ids)")
print(f"GET /api/gen-ai/beta/endpoints   -> {st_e} {note_e} ({len(eps)} endpoints)   <-- Q4")
print(f"GET /api/providers               -> {st_p} {note_p} ({len(provs)} providers)")
granted = set(map(str, (acc_raw or {}).get("accessible_ids") or [])) if isinstance(acc_raw, dict) else set()
print(f"GET /api/aliases/accessible      -> {st_acc} {note_acc} ({len(granted)} granted ids)")

if st_e != 200:
    print("\n>>> Q4 FAILED. The unscoped listing does not answer for this caller.")
    print("    Retry with ?projectId=<id>. If only the scoped call works, a cross-project")
    print("    sovereign endpoint may be unlistable and #21 cannot be built this way.")

accessible = {str(r.get("id")) for r in models if r.get("id")}

# Two LLM Gateway deployments exist on cloud-dogfood and only one is Sage's
# (DOMINO-PRIMITIVES.md:139). The sparse one answers with ~4 OpenRouter aliases, none
# carrying an endpoint_url, so the join finds nothing and the sovereign tier looks
# de-registered. That trap cost two runs before this guard existed. Sage's own defaults
# (shim/app.py:51-56) are the tell: all four resolve on the right gateway.
SAGE_DEFAULTS = ("qwen-2-5", "gpt-5.4", "bedrock-qwen3-coder", "sonnet")
absent = [d for d in SAGE_DEFAULTS if d not in accessible]
if absent:
    print(f"\n  !! {len(absent)} of Sage's {len(SAGE_DEFAULTS)} default aliases are missing here: "
          f"{', '.join(absent)}")
    print("     You are probably pointed at the WRONG GATEWAY. Sage's is /apps/llm_gateway/v1.")
    print("     See DOMINO-PRIMITIVES.md:139. Re-run with GATEWAY_BASE_URL set to that one")
    print("     before believing anything below.")

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
not_running = [(n, st, acc) for n, f, st, en, acc in joined if st != "Running"]
by_name_or_id = {}
for a in aliases:
    for k in (a.get("name"), a.get("id")):
        if k:
            by_name_or_id[str(k)] = a

if not joined:
    print("  INCONCLUSIVE — no alias on this gateway carries an endpoint_url at all, so there was")
    print("  nothing to join. This gateway registers no hosted endpoint. Check GATEWAY_BASE_URL")
    print("  points at the LLM Gateway app, not some other app.")
elif not not_running:
    print("  INCONCLUSIVE — every joined alias points at a Running endpoint, so the premise was")
    print("  never exercised. Stop one of them and re-run.")
for n, st, acc in not_running:
    rec = by_name_or_id.get(str(n), {})
    rid = str(rec.get("id") or "")
    is_granted = (n in granted) or (rid and rid in granted)
    print(f"  {n}: endpoint is {st}, in /v1/models = {'yes' if acc else 'NO'}, "
          f"in accessible_ids = {'yes' if is_granted else 'NO' if granted else '?'}")
    if acc:
        print("    -> PREMISE TRUE. A non-Running endpoint's alias is still offered.")
        print("       preflight passes today and the build fails later. #21 is real. Build it.")
    elif is_granted:
        print("    -> PREMISE FALSE. The caller HAS the grant, yet /v1/models withholds it, so")
        print("       /v1/models already filters on status. unresolved_slots catches this today.")
        print("       #21 shrinks to wording: say 'stopped' where it now says 'not offered'.")
    elif granted:
        print("    -> CONFOUNDED. No grant for this alias, so its absence says nothing about")
        print("       status. Stop a Running endpoint you DO have a grant for, then re-run.")
    else:
        print("    -> UNKNOWN. /api/aliases/accessible did not answer, so permission and status")
        print("       cannot be told apart. Fix that call first.")

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
  Q1  -> ANSWERED 2026-08-21 on cloud-dogfood: PREMISE TRUE. Once local-domino-llm was
         granted, /v1/models offered it while its endpoint stayed Stopped. /v1/models
         filters on permission alone, so unresolved_slots cannot catch a stopped
         endpoint. #21 needs a new check, not a reworded message. The verdict above
         re-derives this on any gateway; CONFOUNDED just means no granted alias there
         points at a non-Running endpoint.
  Q2  -> ANSWERED 2026-08-21 on cloud-dogfood: the join is `url`, after stripping the
         trailing /v1. Not id, not vanityUrl.
  Q3  -> every unjoined alias must be reported as unknown, never as stopped. On
         cloud-dogfood that is 12 of 14, across bedrock/vertex/anthropic/openai.
  Q4  -> ANSWERED: 200 unscoped for a normal caller, one deployment-wide call.
  Q5  -> REJECTED 2026-08-21, and not because the field is empty. The qwen-2-5-14b
         provider reports health='error' while its endpoint is Running, off a check a
         month stale. It would call a working model broken. It is also per-provider,
         not per-alias, so it is the wrong granularity anyway.
Record the result in DOMINO-PRIMITIVES.md beside the qwen-2-5 block (line ~197).
==================================================================================""")
