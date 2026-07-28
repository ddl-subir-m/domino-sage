"""OpenCode driver: event mapping + feedback-loop control flow (Step 5 wiring / Seam 3)."""
import httpx

from sage.driver.opencode import OpenCodeClient, map_event, run_feedback_loop
from sage.driver.server import parse_server_url
from sage.feedback.circuit_breaker import CircuitBreaker
from sage.feedback.runner import FeedbackError, FeedbackReport


class _Resp:
    def __init__(self, status: int):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


def test_send_prompt_attaches_files_as_uri_parts(monkeypatch):
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "hi", files=["/mnt/data/foo/bar.csv"])
    assert len(calls) == 1
    assert calls[0]["prompt"]["files"] == [{"uri": "file:///mnt/data/foo/bar.csv", "name": "bar.csv"}]


def test_send_prompt_retries_text_only_when_attachments_rejected(monkeypatch):
    statuses = iter([422, 200])
    bodies = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: bodies.append(json) or _Resp(next(statuses)))
    OpenCodeClient("http://x").send_prompt("s1", "hi", files=["/a/b.csv"])
    assert len(bodies) == 2                                  # rejected once, retried
    assert "files" in bodies[0]["prompt"] and "files" not in bodies[1]["prompt"]


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
