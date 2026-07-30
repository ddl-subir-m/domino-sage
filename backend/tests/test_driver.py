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


_ATT = {"path": "public/data/sales-2026/uploads/q3.csv", "name": "q3.csv",
        "summary": "CSV — 12 columns, 48,231 rows", "detail": "columns: region, amount"}


def test_send_prompt_embeds_the_workspace_relative_path_not_the_mount_path(monkeypatch):
    # Descriptors ride the prompt TEXT (prompt.files is reserved for images, which need real media
    # parts). The path must be the in-root symlink path: the read tool hangs on /mnt/data mounts.
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "use this", attachments=[_ATT])
    assert len(calls) == 1
    text = calls[0]["prompt"]["text"]
    assert text.startswith("use this")
    assert "public/data/sales-2026/uploads/q3.csv" in text
    assert "/mnt/" not in text                       # never the absolute mount path
    assert "files" not in calls[0]["prompt"]         # no images here -> no media parts


def test_send_prompt_renders_the_summary_and_detail_of_each_attachment(monkeypatch):
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "use this", attachments=[_ATT])
    text = calls[0]["prompt"]["text"]
    assert "q3.csv" in text
    assert "CSV — 12 columns, 48,231 rows" in text
    assert "columns: region, amount" in text


def test_send_prompt_reads_no_files_and_renders_from_the_dicts_alone(monkeypatch):
    # The descriptor is computed upstream and cached; send_prompt must never touch the filesystem
    # (a PDF/PNG read here would inline mojibake, and a huge file would stall the turn).
    def _boom(*a, **k):
        raise AssertionError("send_prompt must not open files")
    monkeypatch.setattr("builtins.open", _boom)
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    ghost = {"path": "public/data/x/uploads/nope.csv", "name": "nope.csv",
             "summary": "CSV — 3 columns, 9 rows", "detail": ""}
    OpenCodeClient("http://x").send_prompt("s1", "use this", attachments=[ghost])
    text = calls[0]["prompt"]["text"]
    assert "public/data/x/uploads/nope.csv" in text   # path that does not exist on disk
    assert "CSV — 3 columns, 9 rows" in text


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


def test_an_image_attachment_rides_prompt_files_as_a_data_uri(monkeypatch):
    """`PromptInput.files` exists on 1.18.4 and takes {uri, name}. The uri must be a data: URI —
    every file-path form makes OpenCode emit malformed media ("must contain valid base64")."""
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "match this design", attachments=[
        {"path": "public/data/ds/uploads/shot.png", "name": "shot.png",
         "summary": "PNG image — 800x600", "detail": "PNG image, 800x600.",
         "image_uri": "data:image/png;base64,AAAA"}])

    assert calls[0]["prompt"]["files"] == [{"uri": "data:image/png;base64,AAAA", "name": "shot.png"}]
    assert "shot.png" in calls[0]["prompt"]["text"]   # descriptor still rendered alongside


def test_a_mixed_attachment_set_sends_media_parts_only_for_the_images(monkeypatch):
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "build it", attachments=[
        _ATT,
        {"path": "public/data/ds/uploads/shot.png", "name": "shot.png", "summary": "PNG image",
         "detail": "", "image_uri": "data:image/png;base64,BBBB"}])

    assert [f["name"] for f in calls[0]["prompt"]["files"]] == ["shot.png"]
    assert "q3.csv" in calls[0]["prompt"]["text"]


def test_an_image_that_could_not_be_inlined_tells_the_agent_it_cannot_see_it(monkeypatch):
    """Silent degradation is the trap: a descriptor with no pixels reads exactly like a normal one,
    so the agent assumes it can see the image and guesses instead of saying it can't."""
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "what colour is it?", attachments=[
        {"path": "public/data/ds/uploads/big.png", "name": "big.png",
         "summary": "PNG image — 900x900", "detail": "PNG image, 900x900.", "image_uri": None}])

    text = calls[0]["prompt"]["text"]
    assert "NOT shown to you" in text and "too large to inline" in text
    assert "files" not in calls[0]["prompt"]      # nothing to send as media


def test_a_normal_data_attachment_gets_no_image_note(monkeypatch):
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt("s1", "chart it", attachments=[_ATT])

    assert "NOT shown to you" not in calls[0]["prompt"]["text"]
