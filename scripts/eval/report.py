#!/usr/bin/env python3
"""One row per Build turn across every #702 pilot run, from `runs/*/record.json`.

    python3 scripts/eval/report.py            # markdown table
    python3 scripts/eval/report.py --totals   # plus per-run token and wall-clock totals

Every turn the driver started is a row, failures and retries included. `-` is "not recorded",
never zero.
"""
from __future__ import annotations

import argparse
import json
import pathlib

RUNS = pathlib.Path(__file__).resolve().parent / "runs"


def cell(value) -> str:
    return "-" if value is None else str(value)


def routes(diag: dict) -> str:
    out = []
    for key, n in (diag.get("routes(phase|model|alias|effectiveEffort|effortSource|effortStatus)")
                   or {}).items():
        phase, model, _alias, effort, source, status = key.split("|")
        out.append(f"{phase}:{model}@{effort}({source},{status})x{n}")
    return " ".join(out) or "-"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--totals", action="store_true")
    args = p.parse_args()
    records = sorted((json.loads(f.read_text()) for f in RUNS.glob("*/record.json")),
                     key=lambda r: min((t["n"] for t in r.get("turns", [])), default=10**6))
    print("| # | run | condition | step | decision | status | verification | in tok | out tok "
          "| calls | repairs | discovery (before 1st edit) | map calls | edits | wall s | routes |")
    print("|" + "---|" * 16)
    for r in records:
        for t in r.get("turns", []):
            d = t.get("diagnostics") or {}
            done = t.get("done") or {}
            ver = d.get("verification")
            if isinstance(ver, dict):
                stages = ver.get("stages") or {}
                ver = (f"{ver.get('overall')} "
                       + "/".join(f"{k}={v}" for k, v in stages.items())).strip()
            else:
                ver = cell(ver)
            disc = (f"{d['discoveryReads']} ({d['discoveryReadsBeforeFirstEdit']})"
                    if "discoveryReads" in d else "-")
            print(f"| {t['n']} | {r['label']} | {r['condition']}"
                  f"{' map=' + r['sourceMap'] if r.get('sourceMap') else ''} | {t['step']} "
                  f"| {cell(done.get('decision'))} | {cell(d.get('status'))} | {ver} "
                  f"| {cell((d.get('inTokens') or {}).get('sum'))} "
                  f"| {cell((d.get('outTokens') or {}).get('sum'))} | {cell(d.get('modelCalls'))} "
                  f"| {len(d['repairs']) if 'repairs' in d else '-'} | {disc} "
                  f"| {cell(d.get('sourceMapCalls'))} | {cell(d.get('landedEdits'))} "
                  f"| {cell(t.get('wallSeconds'))} | {routes(d)} |")
    if args.totals:
        print("\n| run | turns | in tok | out tok | wall s | leftovers | notes |")
        print("|---|---|---|---|---|---|---|")
        for r in records:
            ts = r.get("turns", [])

            def tot(k, ts=ts):
                return sum(((t.get("diagnostics") or {}).get(k) or {}).get("sum") or 0 for t in ts)
            print(f"| {r['label']} | {len(ts)} | {tot('inTokens')} | {tot('outTokens')} "
                  f"| {round(sum(t.get('wallSeconds') or 0 for t in ts), 1)} "
                  f"| {len(r.get('leftoverProcesses') or [])} | {'; '.join(r.get('notes') or []) or '-'} |")


if __name__ == "__main__":
    main()
