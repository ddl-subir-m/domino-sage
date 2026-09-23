#!/usr/bin/env python3
"""Read where Sage's turns spent their wall clock, and rank the three suspects by measured share.

Sage's latency only exists against a live gateway and a real workspace, so this is the readout end
of the only feedback loop there is for it: the builder records every turn (sage/timing.py) and this
reads it back over HTTP. Run it against a Builder right after a slow build.

    scripts/turn-timing.py                              # localhost:8080, last 3 turns
    scripts/turn-timing.py --url https://<builder-host> # a deployed Builder (see --cookie)
    scripts/turn-timing.py --watch                      # follow the turn that is running now
    scripts/turn-timing.py --raw                        # the server's own waterfall, unsummarised

The verdict partitions measured intervals into gates, model, tools, finalization, polling,
explicit active-work overlap and other (unmeasured/unplaced). Nested and parallel intervals are
unioned. Polling while a model or tool works is waiting, not extra elapsed time. Work totals and
poll durations are shown separately and may overlap. Missing tool clocks remain unknown; an open
tool observation does not prove that OpenCode was still responding. Harness poll outcomes provide
that separate evidence.

Exits 1 when the MEDIAN turn's pre-model overhead is over budget, so it can be run as a check
rather than read. The default of 2.5s follows a median turn measured at 1.9s on 2026-09-07
after the gate work. Reassess it against the current gateway and workload: a historical
round-trip measurement is not a lower bound on what a later implementation can achieve.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from itertools import pairwise


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
    """Partition elapsed intervals. Summed work and elapsed time are separate quantities.

    Polling inside model/tool activity is waiting, not extra elapsed work. Concurrent active
    categories enter `overlap`; same-category nested/parallel spans are unioned. Unplaced legacy
    polling sums and uncertain tool observation intervals never fill an invented duration.
    """
    total = max(0, rec["ms"])
    calls, spans = rec["calls"], rec["spans"]
    intervals = []
    named, beside = {}, {}
    def add(category, start, duration):
        if start is not None and duration is not None and duration >= 0:
            end = min(total, start + duration)
            start = max(0, start)
            if end > start:
                intervals.append((start, end, category))
    for call in calls:
        add("model", call["atMs"], call["ms"] if call["ms"] is not None else total - call["atMs"])
    for span in spans:
        name = span["name"]
        if name.startswith("agent-turn."):
            continue
        target = beside if span.get("beside") else named
        target.setdefault(name, []).append(span["ms"])
        if not span.get("beside"):
            category = ("gates" if name.startswith(("turn.", "setup.", "gate.")) else
                        "finalization" if name == "typecheck" or name.startswith(("after.", "chat.table")) else None)
            if category:
                add(category, span["atMs"], span["ms"])
    for tool in rec.get("tools", []):
        if tool.get("executionMs") is not None and tool.get("clockPlacement") == "wall_clock":
            add("tools", tool.get("startAtMs"), tool["executionMs"])
        elif tool.get("firstObservedMs") is not None:
            end = tool.get("completedObservedMs")
            add("unconfirmed_tools", tool["firstObservedMs"],
                (total if end is None else end) - tool["firstObservedMs"])
    for item in rec.get("intervals", []):
        if item["name"].startswith("poll."):
            add("polling", item["atMs"], item["ms"])
    edges = sorted({0, total, *(x for start, end, _ in intervals for x in (start, end))})
    buckets = dict.fromkeys(("gates", "model", "tools", "unconfirmed_tools", "polling", "finalization", "overlap", "other"), 0.0)
    overlap = {}
    for left, right in pairwise(edges):
        active = {category for start, end, category in intervals if start < right and end > left}
        if len(active) > 1:
            active.discard("polling")
        if len(active) > 1:
            bucket = "overlap"
            label = "+".join(sorted(active))
            overlap[label] = overlap.get(label, 0) + right - left
        else:
            bucket = next(iter(active), "other")
        buckets[bucket] += right - left
    named = {k: (sum(v), len(v)) for k, v in named.items()}
    beside = {k: (sum(v), len(v)) for k, v in beside.items()}
    return {"total": total, **buckets, "overlaps": overlap,
            "calls": len(calls), "agent_turns": sum(s["name"].startswith("agent-turn.") for s in spans),
            "named": named, "beside": beside,
            "pre_names": [n for n in named if n.startswith(("turn.", "setup.", "gate."))],
            "pre_inference": min((c["atMs"] for c in calls), default=None),
            "no_inference": not calls, "obs": rec["observations"], "counters": rec["counters"],
            "polling_placed": "intervals" in rec,
            "tools_detail": rec.get("tools", []), "polls_detail": rec.get("intervals", [])}


def report(rec: dict) -> dict:
    b = split(rec)
    t = max(1, b["total"])
    when = time.strftime("%H:%M:%S", time.localtime(rec["startedAt"]))
    print(f"\n{'=' * 78}")
    print(f"{when}  {rec['kind']}  {t / 1000:.1f}s  "
          f"{'RUNNING' if rec['running'] else (rec['decision'] or '-')}   {rec['prompt'][:52]!r}")
    print(f"{'=' * 78}")

    buckets = sorted(((name, b[name]) for name in
                      ("gates", "model", "tools", "unconfirmed_tools", "polling", "finalization", "overlap", "other")),
                     key=lambda kv: -kv[1])
    for name, ms in buckets:
        bar = "█" * round(40 * ms / t)
        print(f"  {name:<8} {ms / 1000:7.1f}s  {100 * ms / t:4.0f}%  {bar}")

    print("  unconfirmed_tools = observed tool activity with unknown execution time")
    print("  other = unmeasured or unplaced time; polling during active work is not added again")
    for label, ms in b["overlaps"].items():
        print(f"    overlap {label}: {ms / 1000:.1f}s")
    print(f"\n  {b['agent_turns']} agent turn(s), {b['calls']} inference(s)")
    if b["calls"]:
        by_model: dict[str, list[float]] = {}
        for c in rec["calls"]:
            by_model.setdefault(f"{c['model'] or '?'}/{c['phase'] or '?'}", []).append(c["ms"] or 0)
        for k, xs in sorted(by_model.items(), key=lambda kv: -sum(kv[1])):
            ttfbs = [c["ttfbMs"] for c in rec["calls"]
                     if f"{c['model'] or '?'}/{c['phase'] or '?'}" == k and c["ttfbMs"] is not None]
            ttfb = f"  ttfb p50 {sorted(ttfbs)[len(ttfbs) // 2] / 1000:.1f}s" if ttfbs else ""
            print(f"    {k:<28} n={len(xs):<3} {sum(xs) / 1000:6.1f}s summed work (may overlap){ttfb}")

    if b["no_inference"]:
        print("\n  NOTE: no inference reached the shim; unmeasured time remains in other.")

    print("\n  setup/gate work (nested totals may overlap):")
    pre = [(n, *b["named"][n]) for n in b["pre_names"]]
    for name, ms, k in sorted(pre, key=lambda r: -r[1]):
        print(f"    {name:<28} {ms / 1000:6.1f}s{f'  (x{k})' if k > 1 else ''}")

    if b["beside"]:
        print("\n  beside the turn (started early, not waited on here):")
        for name, (ms, k) in sorted(b["beside"].items(), key=lambda kv: -kv[1][0]):
            print(f"    {name:<28} {ms / 1000:6.1f}s{f'  (x{k})' if k > 1 else ''}")

    during = [(n, *v) for n, v in b["named"].items() if n not in b["pre_names"]]
    if during:
        print("\n  during the turn (summed work, not additional elapsed time):")
        for name, ms, k in sorted(during, key=lambda r: -r[1]):
            print(f"    {name:<28} {ms / 1000:6.1f}s{f'  (x{k})' if k > 1 else ''}")

    tools = b["tools_detail"]
    for name in sorted({tool["tool"] for tool in tools}):
        rows = [tool for tool in tools if tool["tool"] == name]
        measured = [row["executionMs"] for row in rows if row.get("executionMs") is not None]
        open_count = sum(row.get("completedObservedMs") is None for row in rows)
        print(f"  tool {name}: n={len(rows)} exact-duration={len(measured)} "
              f"summed-work={sum(measured) / 1000:.1f}s open={open_count} "
              f"unknown-execution={len(rows) - len(measured)} "
              f"observed-sum={sum(row.get('observedMs', 0) for row in rows) / 1000:.1f}s "
              "(sampled, may overlap; not exact execution)")
    lags = [row["completionLagMs"] for row in tools if row.get("completionLagMs") is not None]
    if lags:
        print(f"  completion observation lag: n={len(lags)} max={max(lags):.0f}ms")
    polls = [row for row in b["polls_detail"] if row["name"] == "poll.read"]
    if polls:
        print(f"  harness polls: n={len(polls)} failed={sum(not row['ok'] for row in polls)} "
              f"last={'answered' if polls[-1]['ok'] else 'failed'}")
    for name in ("poll.read_ms", "poll.sleep_ms"):
        sample = b["obs"].get(name)
        if sample:
            print(f"  {name}: n={sample['n']} summed={sample['sum'] / 1000:.1f}s (overlaps active work)")
    if not b["polling_placed"]:
        print("  legacy polling has no intervals; its elapsed placement is unknown")
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
                pres = sorted(value for r in recs if (value := report(r)["pre_inference"]) is not None)
                if not pres:
                    return 0
                # Gate on the MEDIAN, report the worst. The budget used to be the worst turn, and
                # that made this a coin toss rather than a check: `gate.slots` is a 60s cache whose
                # background refresh does not always win the race, so one turn in a run legitimately
                # pays a ~2.5s cold miss and the whole check went red on it. A regression in what a
                # turn pays before its first token moves the middle of the distribution; a cold
                # cache moves only the tail.
                median_pre = pres[len(pres) // 2] / 1000
                worst = pres[-1] / 1000
        except Exception as e:  # noqa: BLE001
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
