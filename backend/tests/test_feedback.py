"""Feedback runner parsing + circuit breaker logic (Step 5)."""
import json
from pathlib import Path

from sage.feedback.circuit_breaker import CircuitBreaker
from sage.feedback.runner import FeedbackError, FeedbackReport, FeedbackRunner, parse_tsc


def test_parse_tsc_extracts_errors():
    out = (
        "src/App.tsx(12,5): error TS2304: Cannot find name 'foo'.\n"
        "src/x.ts(1,1): error TS1005: ';' expected.\n"
        "Found 2 errors.\n"
    )
    errs = parse_tsc(out)
    assert len(errs) == 2
    assert errs[0] == FeedbackError("src/App.tsx", 12, 5, "TS2304", "Cannot find name 'foo'.")


def test_report_message_and_signature():
    r = FeedbackReport(ok=False, errors=[FeedbackError("a.tsx", 3, 1, "TS2304", "x")])
    assert "Fix these" in r.as_agent_message()
    assert "a.tsx:3:TS2304" == r.signature()
    assert FeedbackReport(ok=True).as_agent_message() == "Typecheck passed. No errors."


def test_breaker_stops_on_clean():
    b = CircuitBreaker()
    assert b.record("", resolved=True).action == "stop"


def test_breaker_no_progress():
    b = CircuitBreaker(no_progress_limit=3)
    assert b.record("sig", resolved=False).action == "continue"   # turn 1
    assert b.record("sig", resolved=False).action == "continue"   # turn 2 (repeat 1)
    assert b.record("sig", resolved=False).action == "stop"       # turn 3 (repeat 2 -> limit)


def test_breaker_max_iterations():
    b = CircuitBreaker(max_iterations=2, no_progress_limit=99)
    assert b.record("a", resolved=False).action == "continue"
    assert b.record("b", resolved=False).action == "stop"


def test_breaker_time_budget():
    t = [0.0]
    b = CircuitBreaker(max_seconds=10, no_progress_limit=99, clock=lambda: t[0])
    assert b.record("a", resolved=False).action == "continue"
    t[0] = 11.0
    assert b.record("b", resolved=False).action == "stop"


def test_implement_prompt_names_the_config_the_gate_checks():
    """The agent's own typecheck has to check what Sage checks (#41).

    The template's root `tsconfig.json` is a references-only stub with no inputs of its own, so
    `tsc -p tsconfig.json` compiles zero files and exits 0 however broken the app is. The implement
    prompt tells the agent to verify its edits; left without a config name the agent reaches for
    that root file, believes a vacuous pass, and ends the turn on code the gate then rejects —
    costing the extra turn `run_feedback_loop` spends feeding the errors back.

    Pinned to `FeedbackRunner`'s own default so the two cannot drift apart again.
    """
    config = json.loads((Path(__file__).resolve().parents[2] / "opencode.json").read_text())
    prompt = config["agent"]["sage-implement"]["prompt"]
    assert FeedbackRunner()._tsconfig in prompt
