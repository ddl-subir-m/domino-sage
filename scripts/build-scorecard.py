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
of dropping it quietly. That check covers the top level, `timing`, and the `calls`/`spans`/`tools`
ROWS, which is where every number below actually comes from.

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
    models. `null` means the capture could not read its own revision, which is NOT agreement: if
    every file is null the scorecard says the check could not run rather than passing it.
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

# A landed edit is a completed call to a tool that writes a file.
#
# This set is DELIBERATELY WIDER than `tool_timing._EDITS`, which is `{"edit", "write"}` and so
# cannot see `apply_patch` -- the only edit tool OpenCode offers a `gpt-` handle (#539). The rows
# are not missing from the download: `tool_timing.py:125-126` stores the tool name verbatim, so an
# `apply_patch` run is in `timing.tools` as an ordinary completed row. `_EDITS` only gates that
# module's own per-file bookkeeping, and inheriting it here would print `0 edits landed` for a GPT
# run that did exactly the work a Sonnet run did -- the precise comparison this script exists to
# make, reported backwards.
#
# `_EDITS` is being widened under #551. Until that lands the two sets differ ON PURPOSE, and for
# COUNTING this one is authoritative. Do not "fix" the divergence by narrowing this set.
# (`request_composition._DIAGNOSTIC_TOOL_NAMES` also lists a bare `patch`; no evidence OpenCode
# offers it, so it is left out rather than guessed in.)
EDIT_TOOLS = {"edit", "write", "apply_patch"}
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

# The ROW contracts, which is where every number above actually comes from. A new field on a call
# is the one most likely to matter and the one least likely to be noticed, so these are checked
# too: `build_diagnostics.CALL_FIELDS` alone has 40 entries and this script reads about twelve.
# Each set is `<SECTION>_FIELDS` plus the extras `snapshot()` attaches to that section by hand.
CALL_ROW = {
    "n", "model", "requestedAlias", "phase", "reason", "callId", "turnId", "protocol",
    "routeVerified", "configuredEffort", "effectiveEffort", "effortSource", "effortStatus",
    "requestedEffort", "sessionId", "rootSessionId", "firstTextMs", "firstToolArgumentMs",
    "firstActionMs", "firstActionKind", "noActionNoticeMs", "noActionTimeoutMs",
    "reasoningOnlyChunks", "noActionRecoveryAttempt", "noActionRecoveryAction", "lastChunkMs",
    "maxChunkGapMs", "outcome", "forwardedReqBytes", "toolsTruncated", "outTokens",
    "reasoningTokens", "atMs", "ttfbMs", "prepMs", "ms", "chunks", "reqBytes", "inTokens",
    "cachedTokens", "ok",
    "toolInvocations", "tools", "requestComposition", "buildIntent", "responseReportedModel"}
SPAN_ROW = {"name", "depth", "atMs", "ms", "open", "no_edit_attempt", "wrote_code",
            "retry_exhausted", "errors",
            "retryCategory", "errorCodes", "stack", "retry_reason"}
TOOL_ROW = {"sessionId", "harnessCallId", "partId", "identitySource", "tool", "firstObservedMs",
            "lastObservedMs", "completedObservedMs", "observationSource", "startUnixMs",
            "endUnixMs", "startSource", "endSource", "executionMs", "completionLagMs",
            "targetFingerprint", "queryFingerprint", "targetMetadataFinal", "editSincePreviousRead",
            "opaqueOperationSincePreviousRead", "targetState", "status", "observedMs", "startAtMs",
            "endAtMs", "clockPlacement", "range"}


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
    # Name the file you were actually given. The Build history LIST body is `{"records": [...]}`
    # with no `schemaVersion` at all, so testing the version first told that case it had drifted
    # from the contract, and kept the "download the individual turn" message for the raw store
    # file -- the one shape that is not what a person downloads.
    if "records" in doc and "timing" not in doc:
        return None, ("is a Build history listing, not one turn. That body holds bounded summaries; "
                      "open a turn and download its diagnostics.")
    version = doc.get("schemaVersion")
    if version != SCHEMA_VERSION:
        return None, (f"has schemaVersion {version!r}; this scorecard reads {SCHEMA_VERSION} only. "
                      "Check it against sage/build_diagnostics.py before trusting any number.")
    if not isinstance(doc.get("timing"), dict):
        return None, "carries no `timing` section, so it is not a per-turn download."
    for section in ("calls", "spans", "tools"):
        if not isinstance(doc["timing"].get(section, []), list):
            return None, f"has a `timing.{section}` that is not a list, so it is not a download."
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
    # `prompt_tokens` already counts its cached part. That holds PER CALL, so the two must be summed
    # over the same calls: `usage()` fills in only what a provider reported, and totalling each
    # field over whatever rows happen to carry it can put a cache read over a smaller denominator
    # and print a share above 100%.
    # The share therefore runs over the calls that reported BOTH. `usage()` records only what the
    # provider sent and `_metadata` admits an explicit null, so one real turn routinely mixes calls
    # that carried a cache figure with calls that did not. Dividing a cache read by every call's
    # input total states an UNREPORTED cache as zero -- the rule this module opens with, broken in
    # the commoner direction: nine silent calls beside one 90% call printed 9%.
    with_in = [c for c in calls if _num(c, "inTokens") is not None]
    reporting = [c for c in with_in if _num(c, "cachedTokens") is not None]
    in_total = sum(_num(c, "inTokens") for c in with_in) if with_in else None
    in_measured = sum(_num(c, "inTokens") for c in reporting) if reporting else None
    cached = sum(_num(c, "cachedTokens") for c in reporting) if reporting else None

    # The longest stretch of tool calls with no landed edit at the end of it, including the tail
    # after the last one. File order is the order `tool_timing` first observed each run.
    longest = run = 0
    for row in tools:
        if _landed_edit(row):
            longest, run = max(longest, run), 0
        else:
            run += 1
    longest = max(longest, run)

    # `service.py` opens one `typecheck` span per agent turn inside the repair loop, so a turn that
    # checked three times has three. Both the count and the codes come from the LAST one, which is
    # what the turn was left with. Taking the count from the last and the codes from all of them
    # reads as "one error (three codes)", two of which were already fixed.
    checks = [s for s in spans if s.get("name") == "typecheck"]
    final = checks[-1] if checks else {}
    codes = sorted(c for c in final.get("errorCodes", []) if isinstance(c, str))
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
        # `_implementation_session` admits the block all-or-nothing, and `fresh` is a real bool with
        # `reason` covering "reused". So a measured reuse and a turn that carries no block at all
        # are different findings and must not render the same. Keep both parts.
        "sessionReason": session.get("reason") if session else None,
        "sessionFresh": session.get("fresh") if session else None,
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
        "inMeasured": in_measured,   # denominator: only the calls that reported a cache figure
        "inTokens": in_total,        # display: every call that reported an input total
        "cacheCalls": len(reporting),
        "tokenCalls": len(with_in),
        "edits": sum(1 for r in tools if _landed_edit(r)),
        "bash": sum(1 for r in tools if r.get("tool") in SHELL_TOOLS),
        "longestRun": longest,
        "errors": _num(final, "errors") if checks else None,
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
        "unknownRows": sorted({f"{section}.{key}"
                               for section, known in (("calls", CALL_ROW), ("spans", SPAN_ROW),
                                                      ("tools", TOOL_ROW))
                               for r in {"calls": calls, "spans": spans, "tools": tools}[section]
                               for key in set(r) - known}),
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


def _cache(row: dict) -> str:
    """The share over the calls that measured it, and the shortfall said out loud."""
    if row["inMeasured"] is None:
        total = "-" if row["inTokens"] is None else format(row["inTokens"], ",")
        return f"- of {total} in-tokens (no call reported a cache figure)"
    share = f"{_share(row['cached'], row['inMeasured'])} of {row['inMeasured']:,} in-tokens"
    if row["cacheCalls"] == row["tokenCalls"]:
        return share
    return (f"{share} measured ({row['cacheCalls']} of {row['tokenCalls']} calls reported; "
            f"{row['inTokens']:,} in-tokens in total)")


def _session(row: dict) -> str:
    """`-` only when the record carried no implementation session at all."""
    reason = row["sessionReason"]
    if reason is None:
        return "-"
    return f"fresh ({reason})" if row["sessionFresh"] else f"reused ({reason})"


def render_turn(row: dict, out) -> None:
    flag = "" if row["complete"] else "   [INCOMPLETE CAPTURE]"
    revision = (row["revision"] or "?")[:12]
    print(f"\n{row['turnId']}  {row['kind']}  {label(row)}  {row['status']}  "
          f"{_secs(row['ms'])}  app {row['appId']}  sage {revision}{flag}", file=out)
    print(f"    calls    {row['calls']} model calls · session {_session(row)} · "
          f"no-action timeouts {row['noAction']} · "
          f"recoveries {_counts(row['recoveries'])}", file=out)
    print(f"    waiting  longest {_secs(row['wait'])} before a first action · "
          f"reasoning-only chunks "
          f"{'-' if row['reasoningOnly'] is None else row['reasoningOnly']}", file=out)
    route = "+".join(row["protocols"]) or "?"
    print(f"    route    effort {' '.join(row['efforts']) or '-'} · "
          f"{route}{'' if row['verified'] else '?'} · cached {_cache(row)}", file=out)
    print(f"    work     {row['edits']} edits landed · {row['bash']} bash calls (any outcome) · "
          f"longest run with no landed edit: {row['longestRun']} tool calls", file=out)
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
    # Numerator and denominator over the same calls, for the same reason as inside a turn.
    paired = [(r["cached"], r["inMeasured"]) for r in rows
              if r["cached"] is not None and r["inMeasured"]]
    partial = any(r["cacheCalls"] != r["tokenCalls"] for r in rows)
    errs = [r["errors"] for r in rows if r["errors"] is not None]
    sessions = [r for r in rows if r["sessionReason"] is not None]
    return (name, str(len(rows)),
            str(sum(1 for r in rows if r["status"] == "success")),
            _secs(sum(ms)) if ms else "-",
            str(sum(r["calls"] for r in rows)),
            str(sum(1 for r in sessions if r["sessionFresh"])) if sessions else "-",
            str(sum(r["noAction"] for r in rows)),
            _secs(max(waits)) if waits else "-",
            (_share(sum(c for c, _ in paired), sum(i for _, i in paired)) + ("~" if partial else ""))
            if paired else "-",
            str(sum(r["edits"] for r in rows)),
            str(sum(r["bash"] for r in rows)),
            str(max(r["longestRun"] for r in rows)),
            str(sum(errs)) if errs else "-",
            str(sum(sum(r["repairs"].values()) for r in rows)))


def render_summary(groups: dict, out) -> None:
    lines = [HEADINGS] + [summary_line(name, rows) for name, rows in groups.items()]
    widths = [max(len(line[i]) for line in lines) for i in range(len(HEADINGS))]
    print("\nPER MODEL  (totals over that model's turns, except: `wait` and `runrun` are its worst "
          "single turn, and `cached` is a ratio -- `~` means not every call reported one)", file=out)
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
    # `null` is "the capture could not read its own revision", not "the same revision". A deployed
    # workspace has no git checkout, so `source_revision()` returns None there -- which is exactly
    # where these downloads come from. Treating that unknown as agreement claims a check that never
    # ran, so the all-null case gets its own note rather than passing silently.
    if revisions == {None}:
        notes.append("No capture recorded a `sourceRevision`, so nothing here shows the turns ran "
                     "against the same build of Sage. A deployed workspace has no git checkout; "
                     "pair these downloads with the build you ran them on yourself.")
    elif len(revisions) > 1:
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
        for keys, where in ((row["unknownTop"], "top level"), (row["unknownTiming"], "`timing`"),
                            (row["unknownRows"], "a record row")):
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
    unknown = any(row["unknownTop"] or row["unknownTiming"] or row["unknownRows"]
                  or row["unknownOutcomes"] for row in rows)
    if notes:
        print("\nNOT A VALID COMPARISON", file=out)
        for note in notes:
            print(f"  * {note}", file=out)
    if unknown:
        print("\nNOTES", file=out)
        render_notes(rows, out)
    if not notes and len(groups) < 2:
        # Every cross-model clause is vacuous over one model, so claiming them would be a check
        # that never ran.
        print("\nOne model here, so there is nothing to compare it against. Its captures are "
              "complete and successful; hand in another model's turns to get a comparison.",
              file=out)
    elif not notes:
        print("\nComparable: same turn count, same kind/phase order, one app per model, one Sage "
              "revision, every capture complete and successful. The prompts themselves are not in "
              "the download, so check those yourself.", file=out)
    return 2 if notes else 0


if __name__ == "__main__":
    sys.exit(main())
