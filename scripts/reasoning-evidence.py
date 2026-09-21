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

    capability  measured here, one alias at a time

Every request is built by `RouteCapability.settings()` — the same call the shim makes in production.
Writing the effort field by hand here would measure a DIFFERENT question than the one Sage asks:
`messages` sends `thinking` plus `output_config.effort` and never `reasoning_effort`, so a probe
that sent `reasoning_effort` to an Anthropic route would record a 200 that no production turn can
reproduce.

Two things are asked before any level is:

    the route     a status code cannot answer this. Every accessible alias on cloud-dogfood
                  answers 200 on all three wires and replies in the shape of whichever was asked,
                  so "the native address answered" is true of a model the gateway is translating
                  for. ADR-0066's Responses contract is the discriminator instead, and it is about
                  content: a native route echoes a per-request nonce, `store: false` and the
                  requested effort, and a translated one drops them. Messages has no such contract
                  and no native Messages route is claimed here — see `_route`.
    a nonsense value
                  an alias that HONOURS the field and one that THROWS IT AWAY both answer 200 to
                  `low`. Only an illegal value separates them: 400 means the field was validated,
                  200 means it was discarded and the level you sent bought nothing. Skip this and
                  every level reads as usable on a model that supports none of them.

Then each level is sent twice, alone and beside a function tool, because the pair is refused where
neither half is: gpt-5.4 takes an effort and takes tools, and on `/v1/chat/completions` refuses them
together. `efforts_with_tools` is the column that decides whether an effort is usable during a
build, where every turn carries tools.

`reason` is NOT measured. It is a sentence a person wrote about a model — haiku's says it needs a
manual thinking budget — and nothing in a status code implies it. An existing row's `reason` is
carried over by name and root; a new row gets `""` and wants a human.

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
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sage.gateway.capabilities import RouteCapability
from sage.gateway.protocol import Protocol, endpoint

EVIDENCE = ROOT / "backend/sage/gateway/reasoning-evidence.json"
TIMEOUT_S = 45
# Not a level anything advertises, and not a typo for one either: a value some provider quietly
# rounds to `low` would report "honours it" on an alias that does not.
NONSENSE = "banana"
# Every spelling seen across the providers the gateway fronts. Sent one at a time, because an alias
# that takes the field still refuses levels its backing model has no room for.
LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
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


def _detail(raw: str) -> str:
    """The one sentence worth reading out of a refusal body."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        # A deployment that bounces an unauthenticated call to its login page answers 200 with
        # HTML, which is the least obvious way a wrong key can present.
        return ("not JSON — a sign-in page, so the key was not accepted"
                if raw.lstrip()[:9].lower().startswith(("<!doctype", "<html"))
                else raw[:160].replace("\n", " "))
    while isinstance(parsed, dict):
        nxt = parsed.get("error") or parsed.get("detail") or parsed.get("message")
        if nxt is None:
            break
        parsed = nxt
    return str(parsed)[:160].replace("\n", " ")


def _json(url: str) -> list[dict]:
    status, raw = _call(url)
    if status != 200:
        sys.exit(f"{url} answered {status}: {_detail(raw)}")
    try:
        parsed = json.loads(raw)
    except ValueError:
        sys.exit(f"{url} answered 200 but not JSON: {_detail(raw)}")
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


def _body(alias: str, protocol: Protocol, effort: str | None, tools: bool) -> dict:
    """A smallest legal request for `protocol`, carrying the settings production would send."""
    if protocol is Protocol.MESSAGES:
        body: dict = {"model": alias, "max_tokens": 16,
                      "messages": [{"role": "user", "content": "hi"}]}
        if tools:
            body["tools"] = [{"name": "noop", "description": "does nothing",
                              "input_schema": {"type": "object", "properties": {}}}]
    elif protocol is Protocol.RESPONSES:
        body = {"model": alias, "input": "hi", "max_output_tokens": 16}
        if tools:
            body["tools"] = [{"type": "function", "name": "noop", "description": "does nothing",
                              "parameters": {"type": "object", "properties": {}}}]
    else:
        body = {"model": alias, "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}]}
        if tools:
            body["tools"] = [{"type": "function",
                              "function": {"name": "noop", "description": "does nothing",
                                           "parameters": {"type": "object", "properties": {}}}}]
    # Built by the production helper, so the probe and the shim cannot drift apart. The capability
    # is constructed to allow exactly the level being asked about, because `settings` refuses a
    # level it has no evidence for — which is the whole point of it everywhere else.
    allowed = () if effort is None else (effort,)
    route = RouteCapability(protocol, efforts=allowed, efforts_with_tools=allowed, reason="")
    return body | route.settings(effort, tools=tools)


def _ask(alias: str, protocol: Protocol, effort: str | None, tools: bool = False) -> tuple[int, str]:
    return _call(endpoint(_root(), protocol), _body(alias, protocol, effort, tools))


def _passthrough(alias: str) -> tuple[bool, str]:
    """Does `/v1/responses` carry this alias natively, and the sentence saying how that was told.

    A status code answers nothing here. Measured on cloud-dogfood 2026-09-21: EVERY accessible
    alias answers 200 on all three wires and replies in the shape of whichever wire was asked, so
    "the native address answered" is true of a model the gateway is translating for.

    ADR-0066:117-122 gives the discriminator, and it is content and not status: a native Responses
    route must echo a per-request metadata nonce, `store: false`, and the REQUESTED effort, and
    "the gateway's translated response does not satisfy that contract". Measured against a negative
    control the same day — `domino/gemini-3.7-flash`, which ADR-0066:43 records as a compatibility
    route — the echo is the thing that separates them:

        domino/gemini-3.7-flash   store=None   nonce missing   effort missing   translated
        bedrock-qwen3-coder       store=None   nonce missing   effort missing   translated
        qwen-2-5                  store=None   nonce echoed    effort echoed    partial
        GLM 5.3 OR                store=False  nonce echoed    effort echoed    native
        gpt-5.4                   store=False  nonce echoed    effort echoed    native

    All three are required, so the partial row is reported as what it is rather than rounded up: an
    alias that returns the effort but drops `store` has not shown the request reached the vendor
    unstored, which is the half of the contract that is about where state lives.
    """
    nonce = uuid.uuid4().hex
    body = {"model": alias, "input": "hi", "max_output_tokens": 16, "store": False,
            "metadata": {"sage_nonce": nonce}, "reasoning": {"effort": "low"}}
    status, raw = _call(endpoint(_root(), Protocol.RESPONSES), body)
    if status != 200:
        return False, f"/v1/responses answered {status}"
    try:
        reply = json.loads(raw)
    except ValueError:
        return False, "/v1/responses answered 200 but not JSON"
    echoed = {"nonce": (reply.get("metadata") or {}).get("sage_nonce") == nonce,
              "store": reply.get("store") is False,
              "effort": (reply.get("reasoning") or {}).get("effort") == "low"}
    missing = [name for name, ok in echoed.items() if not ok]
    return not missing, "the contract holds" if not missing else f"dropped {', '.join(missing)}"


def _route(alias: str, previous: dict | None) -> tuple[Protocol, bool] | None:
    """Which wire to record for this alias, and whether it is vendor-native. None = unmeasured.

    Chat is asked first as the control: it is the compatibility route, every alias has one, and a
    gateway that refuses it is refusing everything. Then the Responses contract above decides
    native, because nothing else can.

    Messages has no such contract. ADR-0066:130 says it "relies on the exact recent metadata and
    measured native route" — a measurement made elsewhere, from gateway audit rows this key cannot
    read. So a native Messages route is not something this script can tell from a translated one,
    and an existing `messages` row is LEFT ALONE rather than overwritten with a guess. Recording it
    as compatibility would silently retire the runtime contract that fails a turn when native state
    would otherwise be lost, which is a worse error than refusing the row.
    """
    control = _ask(alias, Protocol.CHAT, None)
    if control[0] != 200:
        # The gateway's own sentence, not a guess at it. A workspace that has spent its API quota
        # refuses every route with a 400 that names the date access returns, and "unusable or
        # stopped" would send someone to look at the wrong thing entirely.
        print(f"  NOT MEASURED — no route answered 200. {_detail(control[1])}")
        return None
    native, why = _passthrough(alias)
    print(f"  /v1/responses: {why}")
    if native:
        return Protocol.RESPONSES, True
    if previous and str(previous.get("protocol")) == str(Protocol.MESSAGES):
        print("  NOT MEASURED — the existing row records a native Messages route, and no contract "
              "tells that apart from a translated one. Left as it is; re-measure it where the "
              "gateway's audit rows can be read.")
        return None
    return Protocol.CHAT, False


def _levels(alias: str, protocol: Protocol, tools: bool) -> list[str] | None:
    """The levels this route accepts. None = a level went unanswered, so the row is not writable."""
    usable, unanswered = [], []
    for level in LEVELS:
        status, raw = _ask(alias, protocol, level, tools)
        if status == 200:
            usable.append(level)
        elif status != 400:
            unanswered.append(f"{level} ({status}: {_detail(raw)})")
    if unanswered:
        print(f"  INCOMPLETE{' with tools' if tools else ''} — no verdict on "
              f"{'; '.join(unanswered)}. Re-run before recording.")
        return None
    return usable


def probe(row: dict, carried: dict[tuple[str, str], dict]) -> dict | None:
    alias = str(row["name"])
    print(f"\n{alias}")
    if row["fallback_chain"]:
        # `resolve` refuses these before it reads a proof, so measuring one would record a row
        # nothing can ever match.
        print("  skipped — an unverified fallback route; reasoning is refused for it by design")
        return None
    previous = carried.get((alias, str(row["gateway"])))
    route = _route(alias, previous)
    if route is None:
        return None
    protocol, native = route
    print(f"  route {protocol} (native {native})")
    junk = _ask(alias, protocol, NONSENSE)
    if junk[0] == 200:
        # Not an empty row by omission: the alias answers happily to a level that does not exist,
        # so every level it "accepts" is a level it discarded.
        print(f"  discards the field — {NONSENSE} answered 200, so no level buys anything")
        efforts, with_tools = [], []
    elif junk[0] != 400:
        print(f"  NOT MEASURED — the gateway answered {junk[0]} to the nonsense value, not a "
              f"verdict: {_detail(junk[1])}")
        return None
    else:
        efforts = _levels(alias, protocol, tools=False)
        with_tools = _levels(alias, protocol, tools=True) if efforts is not None else None
        if efforts is None or with_tools is None:
            return None
        # The control, asked AGAIN after the sweep. A 400 means "this level is refused" only while
        # the route still works at all, and a workspace can spend its API quota partway through
        # thirty calls — after which every remaining level 400s with a message about a date. That
        # reads as a model which accepts no effort, and it writes an empty row that looks measured.
        # A control that no longer answers says the sweep stopped being a measurement; it cannot
        # say when, so nothing from it is kept.
        after = _ask(alias, protocol, None)
        if after[0] != 200:
            print(f"  NOT MEASURED — the route stopped answering during the sweep, so the levels "
                  f"above are not evidence: {_detail(after[1])}")
            return None
        print(f"  efforts            {', '.join(efforts) or 'none'}")
        print(f"  efforts_with_tools {', '.join(with_tools) or 'none'}")
    # Carried, never invented: `reason` is a sentence about the MODEL, and no status code implies
    # one. Keyed on name and root rather than on the full identity, because an Alias whose metadata
    # moved is the same model and the sentence still holds.
    return row | {"protocol": str(protocol), "native": native, "efforts": efforts,
                  "efforts_with_tools": with_tools,
                  "reason": (previous or {}).get("reason", "")}


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
    measured = [probe(row, carried) for row in rows]
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
