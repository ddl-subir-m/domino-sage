#!/usr/bin/env python3
"""Read where Sage's turns spent their wall clock, and rank the three suspects by measured share.

Sage's latency only exists against a live gateway and a real workspace, so this is the readout end
of the only feedback loop there is for it: the builder records every turn (sage/timing.py) and this
reads it back over HTTP. Run it against a Builder right after a slow build.

    scripts/turn-timing.py                              # localhost:8080, last 3 turns
    scripts/turn-timing.py --url https://<builder-host> # a deployed Builder (see --cookie)
    scripts/turn-timing.py --watch                      # follow the turn that is running now
    scripts/turn-timing.py --raw                        # the server's own waterfall, unsummarised

The verdict block is the point. It splits a turn into the four things it can be spending time on
and prints them largest first, so the ranking comes off the numbers rather than off a code read:

    gates    everything serial before the first inference — the pre-turn commit, and whatever is
             LEFT of the three gates that now start at the top of the turn and are joined further
             down (the `git fetch`, the Alias listing, the scope classifier's own model call). What
             those three cost end to end is listed separately, under "beside the turn".
    model    time inside inferences: the irreducible part, and the denominator for everything else
    polling  what the sampling loop costs — the second it sleeps between looks, plus the lag
             between a tool finishing inside OpenCode and Sage noticing (emit.lag)
    other    the remainder: typecheck, git, and whatever is not yet instrumented

Exits 1 when the MEDIAN turn's pre-model overhead is over budget, so it can be run as a check
rather than read. The default of 2.5s is evidenced, not chosen: a median turn measured 1.9s on
2026-09-07 after the gate work, and the floor under that is a gateway round trip the scope
classifier has to make (~1s), so a budget near 1s is one no code change can reach.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def fetch(url: str, n: int, cookie: str = "") -> list[dict]:
    req = urllib.request.Request(f"{url.rstrip('/')}/api/diag/timing?n={n}&format=json")
    if cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def fetch_text(url: str, n: int, cookie: str = "") -> str:
    req = urllib.request.Request(f"{url.rstrip('/')}/api/diag/timing?n={n}")
    if cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode()


def split(rec: dict) -> dict:
    """One turn's wall clock, divided into the four buckets the verdict ranks.

    `gates` is measured as the time before the FIRST inference rather than as the sum of the gate
    spans, deliberately: an unmeasured gate is exactly the kind of cost this is looking for, and a
    sum of named spans would hide it. Everything named is listed underneath so the unnamed remainder
    is visible as the difference.
    """
    total = rec["ms"]
    calls = rec["calls"]
    spans = rec["spans"]
    obs = rec["observations"]

    # Summed by name, not last-wins: a build typechecks once per agent turn, and the number that
    # matters is what the turn spent on tsc altogether.
    named: dict[str, list[float]] = {}
    # A span the recorder marked `beside` ran on another thread, alongside the turn rather than in
    # front of it: the gate prefetches (the `git fetch` and the Alias listing, both started at the
    # top of the turn and joined later, or not joined at all). They are real work and they are timed,
    # but adding them to the pre-inference total would charge the turn wall clock it never waited on
    # — and the number this script exists to defend is what the turn WAITED for.
    beside: dict[str, list[float]] = {}
    for sp in spans:
        if sp["name"].startswith("agent-turn."):
            continue
        (beside if sp.get("beside") else named).setdefault(sp["name"], []).append(sp["ms"])
    named = {k: (sum(v), len(v)) for k, v in named.items()}
    beside = {k: (sum(v), len(v)) for k, v in beside.items()}
    pre_names = [n for n in named if n.startswith(("turn.", "setup.", "gate."))]
    # With no inference, "before the first one" is the whole turn, which would charge the gates
    # bucket with the entire build. A turn whose model calls never reached the shim is a real and
    # separate fault (see Project.model_calls), so fall back to the named gates and say so.
    first_call_at = (min(c["atMs"] for c in calls) if calls
                     else sum(named[n][0] for n in pre_names))
    model = sum(c["ms"] or 0 for c in calls)
    polling = obs.get("poll.sleep_ms", {}).get("sum", 0) + obs.get("poll.read_ms", {}).get("sum", 0)
    # Sleep and reads overlap nothing (the poll thread does one or the other), but a model call runs
    # on another thread THROUGH them — so polling time inside a call is not additional wall clock.
    # Charge polling only with what it costs beyond the inferences it was waiting on.
    polling = max(0, min(polling, total - model - first_call_at))
    return {
        "total": total,
        "gates": first_call_at,
        "model": model,
        "polling": polling,
        "other": max(0, total - first_call_at - model - polling),
        "calls": len(calls),
        "agent_turns": sum(1 for s in spans if s["name"].startswith("agent-turn.")),
        "named": named,
        "beside": beside,
        "pre_names": pre_names,
        "no_inference": not calls,
        "obs": obs,
        "counters": rec["counters"],
    }


def report(rec: dict) -> dict:
    b = split(rec)
    t = max(1, b["total"])
    when = time.strftime("%H:%M:%S", time.localtime(rec["startedAt"]))
    print(f"\n{'=' * 78}")
    print(f"{when}  {rec['kind']}  {t / 1000:.1f}s  "
          f"{'RUNNING' if rec['running'] else (rec['decision'] or '-')}   {rec['prompt'][:52]!r}")
    print(f"{'=' * 78}")

    buckets = sorted((("gates", b["gates"]), ("model", b["model"]),
                      ("polling", b["polling"]), ("other", b["other"])),
                     key=lambda kv: -kv[1])
    for name, ms in buckets:
        bar = "█" * round(40 * ms / t)
        print(f"  {name:<8} {ms / 1000:7.1f}s  {100 * ms / t:4.0f}%  {bar}")

    print(f"\n  {b['agent_turns']} agent turn(s), {b['calls']} inference(s)")
    if b["calls"]:
        by_model: dict[str, list[float]] = {}
        for c in rec["calls"]:
            by_model.setdefault(f"{c['model'] or '?'}/{c['phase'] or '?'}", []).append(c["ms"] or 0)
        for k, xs in sorted(by_model.items(), key=lambda kv: -sum(kv[1])):
            ttfbs = [c["ttfbMs"] for c in rec["calls"]
                     if f"{c['model'] or '?'}/{c['phase'] or '?'}" == k and c["ttfbMs"] is not None]
            ttfb = f"  ttfb p50 {sorted(ttfbs)[len(ttfbs) // 2] / 1000:.1f}s" if ttfbs else ""
            print(f"    {k:<28} n={len(xs):<3} {sum(xs) / 1000:6.1f}s total{ttfb}")

    if b["no_inference"]:
        print("\n  NOTE: no inference reached the shim this turn — the gates bucket is the sum of\n"
              "        the named pre-turn spans, not everything before the first token.")

    print("\n  before the first inference:")
    pre = [(n, *b["named"][n]) for n in b["pre_names"]]
    for name, ms, k in sorted(pre, key=lambda r: -r[1]):
        print(f"    {name:<28} {ms / 1000:6.1f}s{f'  (x{k})' if k > 1 else ''}")
    unnamed = b["gates"] - sum(r[1] for r in pre)
    if abs(unnamed) > 200:
        print(f"    {'(not instrumented)':<28} {unnamed / 1000:6.1f}s")

    if b["beside"]:
        print("\n  beside the turn (started early, not waited on here):")
        for name, (ms, k) in sorted(b["beside"].items(), key=lambda kv: -kv[1][0]):
            print(f"    {name:<28} {ms / 1000:6.1f}s{f'  (x{k})' if k > 1 else ''}")

    during = [(n, *v) for n, v in b["named"].items() if n not in b["pre_names"]]
    if during:
        print("\n  during the turn:")
        for name, ms, k in sorted(during, key=lambda r: -r[1]):
            print(f"    {name:<28} {ms / 1000:6.1f}s{f'  (x{k})' if k > 1 else ''}")

    lag = b["obs"].get("emit.lag_ms")
    if lag:
        print(f"\n  sampling lag (tool finished -> Sage saw it): p50 {lag['p50']}ms  "
              f"p90 {lag['p90']}ms  max {lag['max']}ms  over {lag['n']} tool calls")
    if b["counters"]:
        print("  " + "  ".join(f"{k}={v}" for k, v in sorted(b["counters"].items())))
    return b


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("-n", type=int, default=3, help="how many turns to read (default 3)")
    ap.add_argument("--cookie", default="", help="Cookie header for a deployed Builder")
    ap.add_argument("--raw", action="store_true", help="the server's own waterfall, unsummarised")
    ap.add_argument("--watch", action="store_true", help="re-read every 5s (follow a live turn)")
    ap.add_argument("--budget-pre", type=float, default=2.5,
                    help="seconds of MEDIAN pre-inference overhead before this exits 1 (default 2.5)")
    a = ap.parse_args()

    while True:
        try:
            if a.raw:
                print(fetch_text(a.url, a.n, a.cookie))
                median_pre = worst = 0.0
            else:
                recs = fetch(a.url, a.n, a.cookie)
                if not recs:
                    print("(no turns recorded yet — run a build, then read this again)")
                    return 0
                pres = sorted(report(r)["gates"] for r in recs)
                # Gate on the MEDIAN, report the worst. The budget used to be the worst turn, and
                # that made this a coin toss rather than a check: `gate.slots` is a 60s cache whose
                # background refresh does not always win the race, so one turn in a run legitimately
                # pays a ~2.5s cold miss and the whole check went red on it. A regression in what a
                # turn pays before its first token moves the middle of the distribution; a cold
                # cache moves only the tail.
                median_pre = pres[len(pres) // 2] / 1000
                worst = pres[-1] / 1000
        except Exception as e:
            print(f"could not read {a.url}/api/diag/timing: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        if not a.watch:
            break
        time.sleep(5)

    if median_pre > a.budget_pre:
        print(f"\nRED: the median turn ran {median_pre:.1f}s before its first inference "
              f"(budget {a.budget_pre:.1f}s; worst turn {worst:.1f}s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
