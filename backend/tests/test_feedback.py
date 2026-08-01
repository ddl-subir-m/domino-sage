"""Feedback runner parsing + circuit breaker logic (Step 5)."""
from sage.feedback.circuit_breaker import CircuitBreaker
from sage.feedback.runner import FeedbackError, FeedbackReport, parse_tsc


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
