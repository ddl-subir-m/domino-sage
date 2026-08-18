#!/usr/bin/env python3
"""Can anything actually CALL a Domino-hosted GenAI endpoint?

Run INSIDE a Domino workspace on cloud-dogfood. This is the composables analogue of
snowflake_query_probe.py: discovery was proven, USE was not.

Open questions this settles:
  1. Which endpoints are Running? (a live run found 1 of 18 -- the rest Stopped/Failed)
  2. Are they OpenAI-compatible? vLLM serves /v1/models and /v1/chat/completions, but
     Domino's app proxy sits in front, so the mount path is unverified.
  3. What auth do they take -- the sidecar bearer, an API key, or nothing?
  4. Can we reach an endpoint in a project we do NOT own? The only Running one is
     generalAccess=Consumer in someone else's project, so access itself is unproven.

COST NOTE: a chat completion consumes GPU on someone else's endpoint. The prompt is
tiny and max_tokens is 8. /v1/models is tried first because it is a free GET.

Usage:
  python3 spikes/domino-probes/genai_endpoint_probe.py [endpoint-name-substring]
"""
import json, os, sys, time, urllib.error, urllib.request

WANT = sys.argv[1].lower() if len(sys.argv) > 1 else None
HOST = os.environ.get("DOMINO_API_HOST", "")
PROXY = os.environ.get("DOMINO_API_PROXY", "http://localhost:8899")


def token():
    try:
        with urllib.request.urlopen(PROXY + "/access-token", timeout=10) as r:
            return r.read().decode().strip()
    except Exception:
        return None


TOK = token()
print(f"sidecar token: {'<' + str(len(TOK)) + ' chars>' if TOK else '<UNAVAILABLE>'}")


def call(url, auth_label, tok, payload=None, timeout=60):
    """One HTTP call. Returns (label, status, elapsed, short body)."""
    headers = {"Accept": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()[:600].decode("utf8", "replace")
        return (auth_label, r.status, round(time.time() - t0, 2), body)
    except urllib.error.HTTPError as e:
        return (auth_label, e.code, round(time.time() - t0, 2),
                e.read()[:300].decode("utf8", "replace"))
    except Exception as e:
        return (auth_label, "ERR", round(time.time() - t0, 2), f"{type(e).__name__}: {e}")


# ---- 1. discover, and unwrap {items:[...]} ----------------------------------------
print("\n" + "=" * 74)
print("1. GenAI endpoints -- which are Running?")
print("=" * 74)
_, st, _, body = call(HOST + "/api/gen-ai/beta/endpoints", "list", TOK)
if st != 200:
    print(f"FATAL: list returned {st}: {body[:200]}")
    sys.exit(1)
# refetch in full (the 600-char cap above is for error bodies)
req = urllib.request.Request(HOST + "/api/gen-ai/beta/endpoints",
                             headers={"Authorization": "Bearer " + (TOK or ""),
                                      "Accept": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    items = json.loads(r.read()).get("items", [])

rows = []
for e in items:
    cv = e.get("currentVersion") or {}
    rows.append({
        "name": e.get("name"), "status": cv.get("status"),
        "access": e.get("generalAccess"), "url": e.get("url"),
        "project": (e.get("project") or {}).get("name"),
        "owner": ((e.get("project") or {}).get("owner") or {}).get("name"),
        "model": ((cv.get("modelSource") or {}).get("registeredModel") or {}).get("modelName"),
        "type": cv.get("modelType"),
    })

for r_ in rows:
    mark = "RUNNING >>" if r_["status"] == "Running" else "          "
    print(f"{mark} {r_['name'][:34]:<34} {str(r_['status']):<12} "
          f"access={str(r_['access']):<9} proj={r_['project']} ({r_['owner']})")

running = [r_ for r_ in rows if r_["status"] == "Running"]
print(f"\ntotal={len(rows)}  running={len(running)}")
if WANT:
    running = [r_ for r_ in running if WANT in (r_["name"] or "").lower()]
if not running:
    print("\nNo Running endpoint to call. Start one in the Domino UI, or ask the owner.")
    print("Without a Running endpoint the rest of this probe cannot answer anything.")
    sys.exit(0)

# ---- 2/3. is it OpenAI-compatible, and what auth? --------------------------------
target = running[0]
base = (target["url"] or "").rstrip("/")
print("\n" + "=" * 74)
print(f"2. Target: {target['name']}  model={target['model']}  access={target['access']}")
print(f"   project: {target['project']} (owner {target['owner']})  -- not ours, so this")
print( "   also tests cross-project access")
print(f"   base url: {base}")
print("=" * 74)

print("\n-- /v1/models (free GET; proves OpenAI compatibility and the mount path)")
for label, tok in (("sidecar bearer", TOK), ("no auth", None)):
    lbl, st, dt, body = call(f"{base}/v1/models", label, tok, timeout=30)
    print(f"   {lbl:<16} HTTP {st:<5} {dt:>6.2f}s  {body[:220]}")

print("\n-- /v1/chat/completions (COSTS GPU: 1 tiny prompt, max_tokens=8)")
payload = {"model": target["model"], "max_tokens": 8,
           "messages": [{"role": "user", "content": "Reply with the single word: ok"}]}
for label, tok in (("sidecar bearer", TOK), ("no auth", None)):
    lbl, st, dt, body = call(f"{base}/v1/chat/completions", label, tok, payload, timeout=120)
    print(f"   {lbl:<16} HTTP {st:<5} {dt:>6.2f}s  {body[:300]}")

print("""
======================================================================
HOW TO READ THIS

  /v1/models 200                -> OpenAI-compatible and the mount path is right. A built
                                   app can call it with any OpenAI client.
  chat/completions 200          -> the composable is REAL. This is the green light for
                                   surfacing GenAI endpoints in the picker.
  200 with "no auth" too        -> the endpoint is open to anyone who has the URL. That is
                                   a finding, not a convenience -- it changes what Sage may
                                   safely bake into a published app.
  401/403 on both               -> needs a credential we have not identified. Check whether
                                   generalAccess=Consumer grants call rights or only view.
  404 on /v1/*                  -> not mounted at that path. Try the bare base url, and
                                   check the endpoint's own docs page in the Domino UI.
  ERR / timeout                 -> the apps host may be unreachable from inside a workspace.
                                   Retry from a browser to separate network from auth.
======================================================================""")
