"""OpenCode driver: event mapping + feedback-loop control flow (Step 5 wiring / Seam 3)."""
from sage.driver.opencode import map_event, run_feedback_loop
from sage.driver.server import parse_server_url
from sage.feedback.circuit_breaker import CircuitBreaker
from sage.feedback.runner import FeedbackError, FeedbackReport


def test_parse_server_url():
    assert parse_server_url("opencode server listening on http://127.0.0.1:4096") == "http://127.0.0.1:4096"
    assert parse_server_url("nothing here") is None


def test_map_event_kinds():
    assert map_event({"type": "message.part.updated", "properties": {}}).kind == "message"
    assert map_event({"type": "session.idle", "properties": {}}).kind == "phase"
    assert map_event({"type": "session.error", "properties": {"m": 1}}).kind == "error"
    ev = map_event({"type": "server.connected", "properties": {"x": 2}})
    assert ev.payload == {"type": "server.connected", "x": 2}


def _err_report():
    return FeedbackReport(ok=False, errors=[FeedbackError("a.tsx", 1, 1, "TS2304", "x")])


def test_loop_stops_when_typecheck_clean():
    sent = []
    reports = iter([_err_report(), FeedbackReport(ok=True)])
    report, decision = run_feedback_loop(
        "build a todo app",
        send_and_wait=lambda t: sent.append(t),
        check=lambda: next(reports),
        breaker=CircuitBreaker(),
    )
    assert report.ok and decision.reason == "typecheck clean"
    assert sent[0] == "build a todo app"
    assert "Fix these" in sent[1]  # errors fed back once, then clean


def test_loop_stops_on_no_progress():
    sent = []
    report, decision = run_feedback_loop(
        "build it",
        send_and_wait=lambda t: sent.append(t),
        check=lambda: _err_report(),  # same errors forever
        breaker=CircuitBreaker(no_progress_limit=3),
    )
    assert not report.ok and "no progress" in decision.reason
