"""`scripts/build-scorecard.py` reads several Build diagnostics downloads at once (#546).

Every fixture here is hand-built from the writer's own contract in `sage/build_diagnostics.py` --
`CALL_FIELDS`, `SPAN_FIELDS`, `TOOL_FIELDS` and what `snapshot` puts around them. No real download
is ever committed: one carries a live session's prompts and possibly a customer's data.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build-scorecard.py"
REVISION = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


def _call(n, model, **extra):
    """One `timing.calls` row: only keys `build_diagnostics.CALL_FIELDS` admits."""
    row = {"n": n, "model": model, "phase": "implement", "protocol": "chat", "routeVerified": True,
           "atMs": n * 1000, "ms": 4000, "ttfbMs": 700, "firstActionMs": 1200,
           "reasoningOnlyChunks": 3, "outcome": "success", "inTokens": 100_000,
           "cachedTokens": 40_000, "toolInvocations": [], "tools": ["edit"]}
    row.update(extra)
    return row


def _tool(tool, status="completed", **extra):
    """One `timing.tools` row, as `tool_timing` builds it."""
    row = {"tool": tool, "status": status, "targetFingerprint": "f" * 16,
           "firstObservedMs": 10, "lastObservedMs": 20, "completedObservedMs": 20}
    row.update(extra)
    return row


def _doc(turn_id, *, model="sonnet", app="app-a", kind="build", phase="planning",
         status="success", ms=60_000, started=1790269775.0, calls=None, spans=None, tools=None,
         capture=None, **extra):
    doc = {
        "schemaVersion": 1,
        "sourceRevision": REVISION,
        "turn": {"turnId": turn_id, "appId": app, "conversationId": "conv-1", "kind": kind,
                 "phase": phase, "startedAt": started},
        "buildOutcome": {"status": status},
        "capture": {"status": "finished", "complete": True, "recorderEnabled": True,
                    "recordAvailable": True, "droppedEvents": {}, "upstreamTruncated": {}},
        "timing": {"ms": ms, "ok": True, "running": False,
                   "calls": [_call(1, model)] if calls is None else calls,
                   "spans": [] if spans is None else spans,
                   "tools": [] if tools is None else tools,
                   "intervals": [], "repeatBrake": [], "counters": {}, "observations": {},
                   "toolsTruncated": False, "intervalsTruncated": False,
                   "repeatBrakeTruncated": False},
        "retention": {"recordLimit": 20, "droppedRecords": 0},
    }
    if capture:
        doc["capture"].update(capture)
    doc.update(extra)
    return doc


def _write(tmp_path, name, doc):
    path = tmp_path / f"build-{name}.json"
    path.write_text(json.dumps(doc))
    return str(path)


def _run(*paths):
    return subprocess.run([sys.executable, str(SCRIPT), *paths], capture_output=True, text=True,
                          check=False)


def _pair(tmp_path, model, app, prefix, **kw):
    """The fixed pair: a new app, then one follow-up change on it."""
    first = _doc(f"turn_{prefix}1", model=model, app=app, kind="build", phase="planning",
                 started=1790269775.0, **kw)
    second = _doc(f"turn_{prefix}2", model=model, app=app, kind="approve", phase="implementation",
                  started=1790269999.0, **kw)
    return [_write(tmp_path, f"{prefix}1", first), _write(tmp_path, f"{prefix}2", second)]


def test_a_valid_pair_on_two_models_reports_each_turn_and_totals_per_model(tmp_path):
    work = {"tools": [_tool("edit"), _tool("bash"), _tool("read"), _tool("read"),
                      _tool("grep"), _tool("write")],
            "spans": [{"name": "typecheck", "atMs": 100, "ms": 900, "open": False, "errors": 2,
                       "errorCodes": ["TS2304"]},
                      {"name": "agent-turn.2", "depth": 0, "atMs": 0, "ms": 900, "open": False,
                       "retry_reason": "typecheck_repair"}]}
    files = _pair(tmp_path, "glm-5.3", "app-glm", "g", **work) + \
        _pair(tmp_path, "claude-haiku-4.5", "app-haiku", "h", **work)
    done = _run(*files)
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    assert "4 turns, 2 models" in out
    # A landed edit, then bash/read/read/grep, then the second landed edit: the longest run with
    # no landed edit at the end of it is those four. `read` and `grep` are tool calls too.
    assert "2 edits landed · 1 bash · longest run with no landed edit: 4 tool calls" in out
    assert "typecheck 2 errors (TS2304) · repairs typecheck_repairx1 · plan -" in out
    assert "cached 40% of 100,000 in-tokens" in out
    summary = [line.split() for line in out.splitlines() if line.strip().startswith("glm-5.3")]
    # model turns ok secs calls fresh noact wait cached edits bash runrun errs repairs
    assert summary == [["glm-5.3", "2", "2", "120.0s", "2", "0", "0", "1.2s", "40%",
                        "4", "2", "4", "4", "2"]]
    assert "Comparable:" in out


def test_an_unknown_schema_version_is_refused_by_name_instead_of_read(tmp_path):
    path = _write(tmp_path, "future", _doc("turn_x", schemaVersion=2))
    done = _run(path)
    assert done.returncode == 1
    assert "schemaVersion 2" in done.stderr and "reads 1 only" in done.stderr
    assert done.stdout == ""


def test_an_incomplete_capture_is_called_a_floor_rather_than_counted(tmp_path):
    files = _pair(tmp_path, "glm-5.3", "app-glm", "g") + _pair(
        tmp_path, "claude-haiku-4.5", "app-haiku", "h",
        capture={"complete": False, "status": "interrupted",
                 "droppedEvents": {"calls": 7}})
    done = _run(*files)
    assert done.returncode == 2
    assert "[INCOMPLETE CAPTURE]" in done.stdout
    assert "NOT A VALID COMPARISON" in done.stdout
    assert "capture interrupted, dropped callsx7" in done.stdout
    assert "floors, not counts" in done.stdout
    assert "Comparable:" not in done.stdout


def test_two_models_that_did_not_run_the_same_turns_are_not_compared_quietly(tmp_path):
    files = _pair(tmp_path, "glm-5.3", "app-glm", "g") + [
        _write(tmp_path, "h1", _doc("turn_h1", model="claude-haiku-4.5", app="app-haiku"))]
    done = _run(*files)
    assert done.returncode == 2
    assert "did not run the same number of turns (glm-5.3 2, claude-haiku-4.5 1)" in done.stdout


def test_a_field_the_writer_grew_is_named_rather_than_dropped(tmp_path):
    path = _write(tmp_path, "grown", _doc("turn_x", sandboxPolicy={"version": 1}))
    done = _run(path)
    assert "NOTES" in done.stdout
    assert "top level carries sandboxPolicy" in done.stdout
    assert "grew a field" in done.stdout


def test_a_fact_the_record_never_carried_prints_as_unknown_not_as_zero(tmp_path):
    bare = _call(1, "sonnet")
    # A provider that reports no cache read simply omits `cachedTokens`; `inTokens` still arrives.
    for key in ("firstActionMs", "reasoningOnlyChunks", "cachedTokens"):
        del bare[key]
    path = _write(tmp_path, "bare", _doc("turn_x", calls=[bare]))
    done = _run(path)
    assert "longest - before a first action · reasoning-only chunks -" in done.stdout
    assert "cached - of 100,000 in-tokens" in done.stdout
    # The summary agrees with the row above it: a share nothing measured is not a share of nought.
    row = [line.split() for line in done.stdout.splitlines() if line.strip().startswith("sonnet")]
    assert [cell for cell in row[0] if "%" in cell or cell == "-"] == ["-", "-", "-"]
    # A tool list that really is empty is still a measured zero, and says zero.
    assert "0 edits landed · 0 bash" in done.stdout
