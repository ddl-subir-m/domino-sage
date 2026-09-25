#!/usr/bin/env python3
"""Lay Build diagnostics downloads side by side, so a model comparison is read and not assembled.

WHY. Checking whether Build works on GLM, Haiku and Sonnet meant opening each `build-<turnId>.json`
download and reading it field by field (#546). Every fix in the book needs a before-and-after on
several models, and nothing laid the runs next to each other.

    scripts/build-scorecard.py ~/Downloads/build-turn_*.json

THE FIXED PAIR. Run the same two prompts on each model, each from a blank template, and download
the diagnostics for every turn:

  1. "Build me an app that <one sentence>", with one data file attached  -- a new app.
  2. "<one follow-up change to that app>"                                -- one change on it.

That is two downloads per model, six for three models. Feed all six in one go. Keep the two
sentences byte-identical between models: the scorecard can check the SHAPE of a comparison but it
never sees a prompt, so it cannot tell you that you asked two models for different apps.

WHAT IT READS. Only the files you name. `scripts/turn-timing.py` speaks HTTP to a running Builder
and reads the same records live; this one never opens a socket, because the download is what
arrives in a ticket. A download is exactly `build_diagnostics.Store.get` serialised by
`api.js downloadBuildDiagnostics`, so every field named below is one `sage/build_diagnostics.py`
admits by name. That module keeps a closed contract -- "no catch-all copy" -- so a field this
script does not recognise means the writer grew one, and the scorecard says so under NOTES instead
of dropping it quietly.

MISSING IS NOT ZERO. A fact the record does not carry prints as `-`, never as `0`. `0 edits` and
`edits not observed` are different findings and the difference is the whole point of the run.

WHEN A COMPARISON IS NOT VALID. The scorecard exits 2 and prints why, rather than printing a
number that looks like a result:

  * a turn whose capture is incomplete -- dropped events, an upstream truncation, an interrupted
    capture, or no timing record at all. Every count from it is a floor, not a count.
  * a turn that did not end in `success`. A build that stopped early did less work, so its smaller
    numbers are not a smaller appetite.
  * models that did not run the same number of turns, or ran them in a different
    kind/phase order -- that is not the fixed pair.
  * one model's turns spread over more than one app, so the pair was not one app plus a follow-up.
  * files captured by different `sourceRevision`s -- that compares two builds of Sage, not two
    models. `null` means the capture could not read its own revision.
  * the same turn handed in twice.

Exit 1 means a file could not be read at all; exit 2 means they were read and are not comparable.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

SCHEMA_VERSION = 1  # build_diagnostics.SCHEMA_VERSION

# Mirrored from `sage/tool_timing.py`, which decides these things about a tool run as the record is
# written. A landed edit here is a completed call to an edit tool. `tool_timing` additionally
# requires a `targetFingerprint` before it books an edit, because it keys edits by file to answer a
# later read; counting is a different question, and an edit whose path argument never reached the
# stream still landed. So this counts a few that `tool_timing` does not, and never the reverse.
EDIT_TOOLS = {"edit", "write"}  # tool_timing._EDITS
SHELL_TOOLS = {"bash", "shell", "sh", "run", "run_command", "execute", "exec", "terminal"}

# What `native_routes.py` gives one model call, plus `timing._CallHandle.done`'s own defaults.
# `build_diagnostics` copies `outcome` as free text, so a value outside this set really can arrive;
# it is reported rather than bucketed.
CALL_OUTCOMES = {"running", "success", "error", "incomplete", "refusal", "cancelled",
                 "no_action_timeout", "model_output_limit", "pre_edit_policy",
                 "context_rollover_required", "context_continue_required",
                 "context_measurement_unavailable"}

TOP_LEVEL = {"schemaVersion", "sourceRevision", "turn", "buildOutcome", "capture", "timing",
             "implementationSession", "planningRecovery", "preEditGuard", "contextRollover",
             "planContract", "retention"}
TIMING_KEYS = {"ms", "ok", "running", "calls", "spans", "tools", "intervals", "repeatBrake",
               "counters", "observations", "toolsTruncated", "intervalsTruncated",
               "repeatBrakeTruncated"}


def load(path: str):
    """One downloaded `build-<turnId>.json`, or the reason it is not one."""
    try:
        doc = json.loads(pathlib.Path(path).read_text())
    except OSError as exc:
        return None, f"could not be read: {exc.strerror or exc}"
    except ValueError as exc:
        return None, f"is not JSON: {exc}"
    if not isinstance(doc, dict):
        return None, "is not a diagnostics download: its top level is not an object"
    version = doc.get("schemaVersion")
    if version != SCHEMA_VERSION:
        return None, (f"has schemaVersion {version!r}; this scorecard reads {SCHEMA_VERSION} only. "
                      "Check it against sage/build_diagnostics.py before trusting any number.")
    if not isinstance(doc.get("timing"), dict):
        return None, ("carries no `timing` section, so it is not a per-turn download. The Build "
                      "history list gives summaries; download the individual turn.")
    return doc, ""


def _num(row: dict, key: str):
    value = row.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _total(rows, key):
    """The sum over the rows that carry the field, or None when not one of them does."""
    seen = [v for v in (_num(row, key) for row in rows) if v is not None]
    return sum(seen) if seen else None


def _landed_edit(row: dict) -> bool:
    return row.get("tool") in EDIT_TOOLS and row.get("status") == "completed"


def facts(doc: dict, path: str) -> dict:
    """Every number one row shows, read straight off one download."""
    timing = doc["timing"]
    calls = [r for r in timing.get("calls", []) if isinstance(r, dict)]
    spans = [r for r in timing.get("spans", []) if isinstance(r, dict)]
    tools = [r for r in timing.get("tools", []) if isinstance(r, dict)]
    turn = doc.get("turn") if isinstance(doc.get("turn"), dict) else {}
    capture = doc.get("capture") if isinstance(doc.get("capture"), dict) else {}
    session = doc.get("implementationSession") or {}
    contract = doc.get("planContract") or {}

    waits = [v for v in (_num(c, "firstActionMs") for c in calls) if v is not None]
    # `cachedTokens` is a part of `inTokens`, not an extra: gateway/events.py rebuilds the Messages
    # protocol's `input_tokens` as input + cache_read + cache_creation, and a chat-completions
    # `prompt_tokens` already counts its cached part. So this share is between 0 and 1.
    cached, in_tokens = _total(calls, "cachedTokens"), _total(calls, "inTokens")

    # The longest stretch of tool calls with no landed edit at the end of it, including the tail
    # after the last one. File order is the order `tool_timing` first observed each run.
    longest = run = 0
    for row in tools:
        if _landed_edit(row):
            longest, run = max(longest, run), 0
        else:
            run += 1
    longest = max(longest, run)

    checks = [s for s in spans if s.get("name") == "typecheck"]
    codes = sorted({c for s in checks for c in s.get("errorCodes", []) if isinstance(c, str)})
    return {
        "path": path,
        "turnId": turn.get("turnId") or "?",
        "appId": turn.get("appId") or "?",
        "conversationId": turn.get("conversationId", ""),
        "startedAt": turn.get("startedAt"),
        "kind": f"{turn.get('kind') or '?'}/{turn.get('phase') or '?'}",
        "revision": doc.get("sourceRevision"),
        "models": list(dict.fromkeys(c["model"] for c in calls if isinstance(c.get("model"), str))),
        "status": (doc.get("buildOutcome") or {}).get("status") or "?",
        "ms": _num(timing, "ms"),
        "calls": len(calls),
        "fresh": session.get("reason") if session.get("fresh") else None,
        "noAction": sum(1 for c in calls if c.get("outcome") == "no_action_timeout"),
        "recoveries": Counter(c["noActionRecoveryAction"] for c in calls
                              if "noActionRecoveryAction" in c),
        "wait": max(waits) if waits else None,
        "reasoningOnly": _total(calls, "reasoningOnlyChunks"),
        "efforts": list(dict.fromkeys(f"{c.get('effectiveEffort') or 'default'}"
                                      f"({c.get('effortSource')})"
                                      for c in calls if "effortSource" in c)),
        "protocols": list(dict.fromkeys(c["protocol"] for c in calls
                                        if isinstance(c.get("protocol"), str))),
        "verified": bool(calls) and all(c.get("routeVerified") is True for c in calls),
        "cached": cached,
        "inTokens": in_tokens,
        "edits": sum(1 for r in tools if _landed_edit(r)),
        "bash": sum(1 for r in tools if r.get("tool") in SHELL_TOOLS),
        "longestRun": longest,
        "errors": _num(checks[-1], "errors") if checks else None,
        "errorCodes": codes,
        "repairs": Counter(s["retry_reason"] for s in spans
                           if str(s.get("name", "")).startswith("agent-turn.")
                           and "retry_reason" in s),
        "planValid": contract.get("valid") if contract else None,
        "planSteps": contract.get("stepCount") if contract else None,
        "complete": bool(capture.get("complete")),
        "captureStatus": capture.get("status") or "?",
        "recordAvailable": capture.get("recordAvailable"),
        "dropped": {k: v for k, v in (capture.get("droppedEvents") or {}).items() if v},
        "truncated": sorted(k for k, v in (capture.get("upstreamTruncated") or {}).items() if v),
        "unknownTop": sorted(set(doc) - TOP_LEVEL),
        "unknownTiming": sorted(set(timing) - TIMING_KEYS),
        "unknownOutcomes": sorted({c["outcome"] for c in calls
                                   if isinstance(c.get("outcome"), str)
                                   and c["outcome"] not in CALL_OUTCOMES}),
    }


def label(row: dict) -> str:
    """The model a row is filed under. Several means the turn changed model part way through."""
    return "+".join(row["models"]) or "?"


def _order(row: dict):
    started = row["startedAt"]
    return (1, str(started)) if isinstance(started, str) else (0, "", started or 0)


def _secs(ms):
    return "-" if ms is None else f"{ms / 1000:.1f}s"


def _share(part, whole):
    return "-" if not whole or part is None else f"{100 * part / whole:.0f}%"


def _counts(counter: Counter) -> str:
    return " ".join(f"{k}x{v}" for k, v in sorted(counter.items())) or "-"


def render_turn(row: dict, out) -> None:
    flag = "" if row["complete"] else "   [INCOMPLETE CAPTURE]"
    print(f"\n{row['turnId']}  {row['kind']}  {label(row)}  {row['status']}  "
          f"{_secs(row['ms'])}  app {row['appId']}{flag}", file=out)
    print(f"    calls    {row['calls']} model calls · fresh session "
          f"{row['fresh'] or '-'} · no-action timeouts {row['noAction']} · "
          f"recoveries {_counts(row['recoveries'])}", file=out)
    print(f"    waiting  longest {_secs(row['wait'])} before a first action · "
          f"reasoning-only chunks "
          f"{'-' if row['reasoningOnly'] is None else row['reasoningOnly']}", file=out)
    route = "+".join(row["protocols"]) or "?"
    print(f"    route    effort {' '.join(row['efforts']) or '-'} · "
          f"{route}{'' if row['verified'] else '?'} · cached "
          f"{_share(row['cached'], row['inTokens'])} of "
          f"{'-' if row['inTokens'] is None else format(row['inTokens'], ',')} in-tokens", file=out)
    print(f"    work     {row['edits']} edits landed · {row['bash']} bash · longest run with no "
          f"landed edit: {row['longestRun']} tool calls", file=out)
    plan = "-" if row["planValid"] is None else f"{'valid' if row['planValid'] else 'INVALID'}"
    steps = "" if row["planSteps"] is None else f", {row['planSteps']} steps"
    print(f"    checks   typecheck "
          f"{'-' if row['errors'] is None else str(row['errors']) + ' errors'}"
          f"{' (' + ' '.join(row['errorCodes']) + ')' if row['errorCodes'] else ''} · "
          f"repairs {_counts(row['repairs'])} · plan {plan}{steps}", file=out)


HEADINGS = ("model", "turns", "ok", "secs", "calls", "fresh", "noact", "wait", "cached",
            "edits", "bash", "runrun", "errs", "repairs")


def summary_line(name: str, rows: list[dict]) -> tuple:
    ms = [r["ms"] for r in rows if r["ms"] is not None]
    waits = [r["wait"] for r in rows if r["wait"] is not None]
    cached = [r["cached"] for r in rows if r["cached"] is not None]
    in_tokens = [r["inTokens"] for r in rows if r["inTokens"] is not None]
    errs = [r["errors"] for r in rows if r["errors"] is not None]
    return (name, str(len(rows)),
            str(sum(1 for r in rows if r["status"] == "success")),
            _secs(sum(ms)) if ms else "-",
            str(sum(r["calls"] for r in rows)),
            str(sum(1 for r in rows if r["fresh"])),
            str(sum(r["noAction"] for r in rows)),
            _secs(max(waits)) if waits else "-",
            _share(sum(cached), sum(in_tokens)) if cached and in_tokens else "-",
            str(sum(r["edits"] for r in rows)),
            str(sum(r["bash"] for r in rows)),
            str(max(r["longestRun"] for r in rows)),
            str(sum(errs)) if errs else "-",
            str(sum(sum(r["repairs"].values()) for r in rows)))


def render_summary(groups: dict, out) -> None:
    lines = [HEADINGS] + [summary_line(name, rows) for name, rows in groups.items()]
    widths = [max(len(line[i]) for line in lines) for i in range(len(HEADINGS))]
    print("\nPER MODEL  (the last eleven columns are totals over that model's turns; `wait` and "
          "`runrun` are its worst single turn)", file=out)
    for index, line in enumerate(lines):
        cells = [line[0].ljust(widths[0])] + [c.rjust(w) for c, w in zip(line[1:], widths[1:])]
        print("  " + "  ".join(cells), file=out)
        if index == 0:
            print("  " + "  ".join("-" * w for w in widths), file=out)


def comparability(rows: list[dict], groups: dict) -> list[str]:
    """Every reason these files are not a like-for-like comparison."""
    notes = []
    for row in rows:
        if not row["complete"]:
            why = []
            if row["captureStatus"] != "finished":
                why.append(f"capture {row['captureStatus']}")
            if row["recordAvailable"] is False:
                why.append("no timing record")
            if row["dropped"]:
                why.append("dropped " + " ".join(f"{k}x{v}" for k, v in sorted(row["dropped"].items())))
            if row["truncated"]:
                why.append("upstream truncated " + " ".join(row["truncated"]))
            notes.append(f"{row['turnId']} has an incomplete capture ({', '.join(why) or 'capped'}); "
                         "its counts are floors, not counts.")
        if row["status"] != "success":
            notes.append(f"{row['turnId']} ended `{row['status']}`, so it did less work than a build "
                         "that finished. Its smaller numbers are not a smaller appetite.")
    seen = {}
    for row in rows:
        key = (row["turnId"], row["appId"], row["conversationId"])
        if key in seen:
            notes.append(f"{row['turnId']} was handed in twice ({seen[key]} and {row['path']}).")
        seen[key] = row["path"]
    revisions = {row["revision"] for row in rows}
    if len(revisions) > 1:
        shown = ", ".join(sorted(str(r) for r in revisions))
        notes.append(f"These turns were captured by different builds of Sage ({shown}); that "
                     "compares revisions, not models.")
    shapes = {name: [r["kind"] for r in rows] for name, rows in groups.items()}
    if len({len(s) for s in shapes.values()}) > 1:
        counts = ", ".join(f"{name} {len(shape)}" for name, shape in shapes.items())
        notes.append(f"The models did not run the same number of turns ({counts}).")
    elif len({tuple(s) for s in shapes.values()}) > 1:
        notes.append("The models ran the same number of turns in a different kind/phase order: "
                     + "; ".join(f"{name} {'->'.join(shape)}" for name, shape in shapes.items()))
    for name, group in groups.items():
        apps = {row["appId"] for row in group}
        if len(apps) > 1:
            notes.append(f"{name} spread its turns over {len(apps)} apps, so this is not one app "
                         "plus a follow-up on it.")
    return notes


def render_notes(rows: list[dict], out) -> None:
    for row in rows:
        for keys, where in ((row["unknownTop"], "top level"), (row["unknownTiming"], "`timing`")):
            if keys:
                print(f"  {row['turnId']}: {where} carries {', '.join(keys)}, which this scorecard "
                      "does not read. sage/build_diagnostics.py grew a field.", file=out)
        if row["unknownOutcomes"]:
            print(f"  {row['turnId']}: model calls ended {', '.join(row['unknownOutcomes'])}, "
                  "outside the outcomes this scorecard counts.", file=out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", metavar="FILE",
                        help="downloaded build-<turnId>.json files, one per turn")
    args = parser.parse_args()

    rows, refused = [], []
    for path in args.files:
        doc, why = load(path)
        if doc is None:
            refused.append(f"{path} {why}")
        else:
            rows.append(facts(doc, path))
    for line in refused:
        print(f"error: {line}", file=sys.stderr)
    if refused or not rows:
        return 1

    rows.sort(key=_order)
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(label(row), []).append(row)

    out = sys.stdout
    print(f"{len(rows)} turns, {len(groups)} models", file=out)
    for group in groups.values():  # by model, then in the order the turns were taken
        for row in group:
            render_turn(row, out)
    render_summary(groups, out)

    notes = comparability(rows, groups)
    unknown = any(row["unknownTop"] or row["unknownTiming"] or row["unknownOutcomes"]
                  for row in rows)
    if notes:
        print("\nNOT A VALID COMPARISON", file=out)
        for note in notes:
            print(f"  * {note}", file=out)
    if unknown:
        print("\nNOTES", file=out)
        render_notes(rows, out)
    if not notes:
        print("\nComparable: same turn count, same kind/phase order, one app per model, one Sage "
              "revision, every capture complete and successful. The prompts themselves are not in "
              "the download, so check those yourself.", file=out)
    return 2 if notes else 0


if __name__ == "__main__":
    sys.exit(main())
