#!/usr/bin/env python3
"""Reasoning probe — which gateway aliases honour `reasoning_effort`, and at which levels.

An alias that accepts the field and an alias that throws it away both answer 200 to
`reasoning_effort: "low"`. They are indistinguishable from a valid value alone, which is how Sage
spent months deciding this by matching the alias NAME. The discriminator is a NONSENSE value:

    400  the alias validates the field, so it honours it — and the refusal usually names the enum
    200  the alias discarded the field in silence; the level you sent bought nothing

Four calls per alias, each bounded and tiny:

    no field      the control. A 502 here means the endpoint is stopped, not that anything failed.
    effort=low    does a legal value pass at all
    low + tools   gpt-5.4 refuses this pair ("Function tools with reasoning_effort are not
                  supported for gpt-5.4 in /v1/chat/completions") while gemini takes it, and every
                  Sage turn except a bare Chat turn carries tools — so this column decides whether
                  an effort is usable during a build at all.
    nonsense      the discriminator above.

Then each level the alias claimed is sent on its own, because the gateway's enum is not the last
word: gemini advertises `minimal` and Vertex behind it answers 400 "Thinking level unsupported:
THINKING_LEVEL_MINIMAL". Publish the advertised enum verbatim and you offer a level that cannot run.

MEASURED on sage.gcp.cs.domino.tech, 2026-09-12 — the run that produced
`router.models.REASONING_EFFORTS`:

    alias                     no field  low  low+tools  nonsense
    gpt-5.4                     200     200    400        400     honours it, but not with tools
    domino/gemini-3.7-flash     200     200    200        400     honours it, tools and all
    sonnet, Opus-4.8, haiku     200     200    200        200     ignores it
    Gemma 4 31B, 26B A4B        200     200    200        200     ignores it
    gemma-4-31b                 502     502    502         --     endpoint stopped

    per level, gemini:   low/medium/high/max 200   minimal 400 (Vertex)   none/xhigh 400
    per level, gpt-5.4:  none/low/medium/high/xhigh 200   minimal/max 400

The gpt-5.4 row is the reason this file exists rather than a one-off curl. Sage published
low/medium/high for it for months, off the alias NAME; the first run of this probe found `none` and
`xhigh` as well, and found that `none` is the one level gpt-5.4 accepts alongside function tools —
which is the gateway's own advice in the refusal, and nobody had taken it.

Usage:

    reasoning-probe.py <alias> [<alias> ...]   probe the named aliases
    reasoning-probe.py --all                   probe every alias the key can reach (/v1/models)

Reads GATEWAY_BASE_URL and GATEWAY_API_KEY out of backend/.env in-process and never prints the key.
That value ALREADY ENDS IN /v1 (see gateway/client.py) — appending another gives a bare
{"detail":"Not Found"} 404, which reads like an auth or routing fault and is not one.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 45

# Not a level anything advertises, and not a typo for one either: a value that some provider quietly
# rounds to `low` would report "honours it" on an alias that does not.
NONSENSE = "banana"
# Every spelling seen across the providers the gateway fronts. Sent one at a time, because an alias
# that takes the field still refuses levels its backing model has no room for.
LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
TOOLS = [{
    "type": "function",
    "function": {"name": "noop", "description": "does nothing",
                 "parameters": {"type": "object", "properties": {}}},
}]


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
    return env


def _call(path: str, body: dict | None = None) -> tuple[int, str]:
    """(status, body-or-explanation). Every call failure is reported as status 0 rather than
    raised, so one dead alias cannot end a sweep. A missing backend/.env is the exception: `_env`
    exits, because that is a question about this machine and no amount of probing answers it."""
    env = _env()
    base = env.get("GATEWAY_BASE_URL", "").rstrip("/")
    if not base:
        return 0, "NO-CONFIG: GATEWAY_BASE_URL is empty"
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {env.get('GATEWAY_API_KEY', '')}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace")
    except Exception as err:
        return 0, f"ERROR: {type(err).__name__}: {err}"


def ask(alias: str, effort: str | None, with_tools: bool = False) -> tuple[int, str]:
    body: dict = {
        "model": alias,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
    }
    if effort is not None:
        body["reasoning_effort"] = effort
    if with_tools:
        body["tools"] = TOOLS
    return _call("/chat/completions", body)


def _detail(raw: str) -> str:
    """The one sentence worth reading out of a refusal body."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw[:160].replace("\n", " ")
    while isinstance(parsed, dict):
        nxt = parsed.get("error") or parsed.get("detail") or parsed.get("message")
        if nxt is None:
            break
        parsed = nxt
    return str(parsed)[:160].replace("\n", " ")


def probe(alias: str) -> int:
    bare, low = ask(alias, None), ask(alias, "low")
    tooled, junk = ask(alias, "low", with_tools=True), ask(alias, NONSENSE)
    print(f"\n{alias}")
    print(f"  no field {bare[0]}   effort=low {low[0]}   low+tools {tooled[0]}   "
          f"{NONSENSE} {junk[0]}")

    # Only two answers are measurements. A 200 to the nonsense value means the alias discarded the
    # field; a 400 means it validated it. Everything else — a stopped endpoint (502), a rate limit,
    # a 500, or the 0 `_call` reports for a timeout or a reset — means the question was never
    # answered, and saying "offers nothing" about it would write a network failure into the table
    # as if it were a finding. This table is the only thing standing between the picker and a hard
    # 400, so it may not be fed a guess.
    if junk[0] == 400:
        print(f"  HONOURS reasoning_effort — {_detail(junk[1])}")
    elif junk[0] == 200:
        print("  IGNORES reasoning_effort (a nonsense value passed), so offer no effort for it")
        return 0
    elif bare[0] == 502 and junk[0] == 502:
        # A stopped endpoint is a correctly reported non-measurement, not a broken sweep: most of
        # this deployment's sovereign endpoints are stopped at any moment, and exiting non-zero for
        # each of them would make `--all`'s exit code mean nothing. The line above already says the
        # row is empty, which is the part that must not be mistaken for a finding.
        print("  endpoint stopped — nothing here was measured")
        return 0
    else:
        print(f"  NOT MEASURED — the gateway answered {junk[0]}, not a verdict: {_detail(junk[1])}")
        return 1

    # `low[0] == 200`, not `!= 400`: a rate limit or a timeout on the low call is not evidence that
    # low succeeded, and printing this line off one would name the tools pair as the culprit for a
    # refusal that never happened.
    if tooled[0] == 400 and low[0] == 200:
        print(f"  ...but NOT alongside function tools — {_detail(tooled[1])}")
    usable, unanswered = [], []
    for level in LEVELS:
        status, raw = ask(alias, level)
        print(f"    {level:<8} {status}  {'' if status in (200, 400) else 'NOT MEASURED — '}"
              f"{'' if status == 200 else _detail(raw)}")
        if status == 200:
            usable.append(level)
        elif status != 400:
            unanswered.append(level)
    if unanswered:
        # A partial row is worse than no row: it looks complete, and the levels missing from it are
        # the ones nobody can tell apart from refused.
        print(f"  INCOMPLETE — no verdict on {', '.join(unanswered)}. Re-run before recording.")
        return 1
    # Sent WITHOUT tools on purpose. The tools exclusion above is a property of the request shape —
    # the shim decides what to do about it (enforcement.apply) — so the row is the alias's own enum.
    print(f"  usable: {', '.join(usable) or 'none'}"
          "   <- this is the row for router.models.REASONING_EFFORTS")
    return 0


def aliases() -> list[str]:
    status, raw = _call("/models")
    if status != 200:
        sys.exit(f"/v1/models answered {status}: {_detail(raw)}")
    try:
        data = json.loads(raw).get("data") or []
    except ValueError:
        sys.exit(f"/v1/models was not JSON: {raw[:160]}")
    return sorted(str(row.get("id") or "") for row in data if row.get("id"))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    names = aliases() if argv[1] == "--all" else argv[1:]
    # Non-zero if any alias went unmeasured, so a sweep that half-failed cannot read as a clean run.
    return max(probe(name) for name in names) if names else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
