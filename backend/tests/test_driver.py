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


def test_send_prompt_embeds_attachment_paths_in_prompt_text(monkeypatch):
    # 1.18.4's /prompt carries no file-part field, so @mentioned attachments are named as real
    # local paths in the prompt TEXT (the agent's read tool follows the symlinks).
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "use this", files=["/mnt/data/foo/bar.csv"])
    assert len(calls) == 1
    text = calls[0]["prompt"]["text"]
    assert text.startswith("use this")
    assert "/mnt/data/foo/bar.csv" in text          # the real path the agent reads
    assert "files" not in calls[0]["prompt"]         # no phantom file-part field


def test_send_prompt_inlines_a_preview_and_tells_the_agent_not_to_read(monkeypatch, tmp_path):
    # Attachments now ride as an inlined PREVIEW (schema head) with a "do NOT open with the read tool"
    # instruction — OpenCode's read tool hangs on /mnt/data mounts outside its project root.
    f = tmp_path / "data.csv"
    f.write_text("col_a,col_b\n1,2\n3,4\n")
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "use this", files=[str(f)])
    text = calls[0]["prompt"]["text"]
    assert str(f) in text                      # path still referenced (for the runtime URL)
    assert "col_a,col_b" in text               # the preview content is inlined
    assert "Do NOT open" in text               # explicit instruction not to read it
    assert "files" not in calls[0]["prompt"]   # still no phantom file-part field


def test_send_prompt_text_only_when_no_attachments(monkeypatch):
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "just text")
    assert calls[0]["prompt"] == {"text": "just text"}   # untouched when nothing attached


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
