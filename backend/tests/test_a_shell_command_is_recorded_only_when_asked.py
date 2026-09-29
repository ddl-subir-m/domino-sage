"""A 2026-09-29 mimo investigation spent 21 model round trips on bash and glob calls assembling its
report, and the turn record could not say what one of them ran (#606). A command line carries
paths, table names and row values, so the text is recorded only when the workspace sets
SAGE_DIAG_COMMANDS=1, and only in the workspace's own timing readout — never in Build diagnostics.
"""

from __future__ import annotations

import pytest

from sage import build_diagnostics, timing

pytestmark = pytest.mark.usefixtures("ledger")

SCRIPT = "python3 - <<'EOF'\nimport json\nprint(json.load(open('a.table.json')))\nEOF"


def _one_bash_call():
    timing.start_turn("chat")
    observer = timing.tool_observer()
    observer.event("s", {"call_id": "c1", "tool": "bash", "status": "completed",
                         "input": {"command": SCRIPT, "description": "read results"}})
    return timing.current()


def test_the_command_is_not_recorded_by_default(monkeypatch):
    monkeypatch.setenv("SAGE_TIMING", "1")
    monkeypatch.delenv("SAGE_DIAG_COMMANDS", raising=False)
    rec = _one_bash_call()
    assert "command" not in timing.as_dict(rec)["tools"][0]
    assert "a.table.json" not in timing.render(rec)


def test_the_command_is_recorded_and_shown_when_asked(monkeypatch):
    monkeypatch.setenv("SAGE_TIMING", "1")
    monkeypatch.setenv("SAGE_DIAG_COMMANDS", "1")
    rec = _one_bash_call()
    assert timing.as_dict(rec)["tools"][0]["command"] == SCRIPT
    assert "a.table.json" in timing.render(rec)


def test_a_long_command_is_capped(monkeypatch):
    monkeypatch.setenv("SAGE_TIMING", "1")
    monkeypatch.setenv("SAGE_DIAG_COMMANDS", "1")
    timing.start_turn("chat")
    timing.tool_observer().event("s", {"call_id": "c1", "tool": "bash", "status": "completed",
                                       "input": {"command": "x" * 50_000}})
    assert len(timing.as_dict(timing.current())["tools"][0]["command"]) == 2000


def test_build_diagnostics_never_carry_the_command():
    assert "command" not in build_diagnostics.TOOL_FIELDS
