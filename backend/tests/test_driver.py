"""OpenCode driver: event mapping + feedback-loop control flow (Step 5 wiring / Seam 3)."""
import httpx

from sage.driver.opencode import (
    OpenCodeClient,
    map_event,
    map_session_event,
    run_feedback_loop,
)
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


def test_chat_send_prompt_does_not_talk_about_the_built_app(monkeypatch):
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append(json) or _Resp(200))
    OpenCodeClient("http://x").send_prompt(
        "s1", "what data is there in @desk.csv",
        attachments=[{"path": ".sage/scratch/desk.csv", "name": "desk.csv",
                      "summary": "CSV — 2 columns", "detail": ""}],
        chat=True,
    )
    text = calls[0]["prompt"]["text"]
    assert "what data is there in @desk.csv" in text
    assert ".sage/scratch/desk.csv" in text
    assert "built app MUST" not in text
    assert "public/data/" not in text


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


def test_summarize_posts_provider_and_model(monkeypatch):
    calls = []
    monkeypatch.setattr("sage.driver.opencode.httpx.post",
                        lambda url, json, timeout: calls.append((url, json)) or _Resp(200))
    OpenCodeClient("http://x").summarize("s1", "sage-gateway", "sonnet", auto=False)
    url, body = calls[0]
    assert url.endswith("/api/session/s1/summarize")
    assert body == {"providerID": "sage-gateway", "modelID": "sonnet", "auto": False}


class _JsonResp(_Resp):
    def __init__(self, payload):
        super().__init__(200)
        self._payload = payload

    def json(self):
        return self._payload


def test_messages_asks_for_oldest_first_because_the_server_defaults_to_newest(monkeypatch):
    """The server's default order is `desc` — verified live against the pinned 1.18.4, which
    answers a two-message session as [assistant, user].

    Every caller reads this list as a transcript and lets the last assignment win when it keeps
    "the latest text part". On a desc list that keeps the EARLIEST text of the turn, which is how
    an intermediate "let me try to access that file..." was shown as the finished answer. The test
    double appends chronologically, so nothing but this assertion can see the difference.
    """
    seen = {}
    monkeypatch.setattr("sage.driver.opencode.httpx.get",
                        lambda url, params, timeout: seen.update(params) or _JsonResp({"data": []}))
    OpenCodeClient("http://x").messages("s1")
    assert seen == {"order": "asc"}


def test_a_bounded_poll_asks_for_the_newest_few_and_hands_them_back_oldest_first(monkeypatch):
    # The whole transcript came back on every poll, once a second for the length of a turn, so the
    # cost grew with the Thread rather than the question. `limit` has to page from the NEW end,
    # which is `desc` — so the rows come back reversed to keep every caller's transcript order.
    seen = {}
    newest_first = [{"id": "m3"}, {"id": "m2"}, {"id": "m1"}]
    monkeypatch.setattr(
        "sage.driver.opencode.httpx.get",
        lambda url, params, timeout: seen.update(params) or _JsonResp({"data": newest_first}))
    out = OpenCodeClient("http://x").messages("s1", limit=3)
    assert seen == {"order": "desc", "limit": 3}
    assert [m["id"] for m in out] == ["m1", "m2", "m3"]


# Frames below are the shapes captured from opencode-ai@1.18.4's global /event stream on
# 2026-08-26, driving a real turn — not invented. `properties`, not the durable stream's `data`.
_SID = "ses_fbfd639d0ffexISOOYnX610gNA"


def _frame(type_: str, **props):
    return {"id": "evt_x", "type": type_, "properties": {"sessionID": _SID, **props}}


def test_a_text_delta_is_a_fragment_and_text_ended_is_the_whole_answer():
    # The delta stream is what makes a turn visible while it runs; text.ended is what makes the
    # answer correct even if deltas were missed. /event has no ?after=, so a reconnect cannot
    # replay — the end event has to be authoritative or a dropped frame silently truncates a reply.
    d = map_session_event(_frame("session.next.text.delta", delta="Blue"), _SID)
    assert d.kind == "message" and d.payload == {"delta": "Blue", "final": False}

    e = map_session_event(_frame("session.next.text.ended", text="Blue is calm."), _SID)
    assert e.kind == "message" and e.payload == {"text": "Blue is calm.", "final": True}


def test_the_tool_name_arrives_with_the_call_not_before_it():
    # tool.input.started fires first and names nothing useful; the tool name lands on tool.called.
    # Reading the earlier event would label every action in the Thread with an empty string.
    assert map_session_event(_frame("session.next.tool.input.started",
                                    callID="call_1", name="write"), _SID) is None
    ev = map_session_event(_frame("session.next.tool.called", tool="write",
                                  input={"path": "a.txt"}, callID="call_1"), _SID)
    assert ev.kind == "tool_run"
    assert ev.payload == {"tool": "write", "input": {"path": "a.txt"},
                          "call_id": "call_1", "status": "called"}
    # And the completion carries NO tool name at all — only the callID. Measured live: a `write`
    # that succeeded arrived as tool="". A consumer has to remember the name from tool.called and
    # correlate on call_id, or every finished action in the Thread is labelled with an empty string.
    done = map_session_event(_frame("session.next.tool.success", callID="call_1"), _SID)
    assert done.payload == {"tool": "", "input": None, "call_id": "call_1", "status": "success"}


def test_a_step_that_ends_on_tool_calls_has_not_ended_the_turn():
    # finish="tool-calls" means another step follows. Treating the first step.ended as the end of
    # the turn would cut a reply off at its first tool call.
    assert map_session_event(_frame("session.next.step.ended", finish="tool-calls"),
                             _SID).payload == {"finish": "tool-calls"}
    assert map_session_event(_frame("session.next.step.ended", finish="stop"),
                             _SID).payload == {"finish": "stop"}


def test_the_global_stream_carries_other_sessions_and_housekeeping_and_neither_is_this_turn():
    # /event is process-wide. Without the sessionID filter a Thread would show another Thread's
    # turn; without the type filter it would show plugin and catalog chatter as agent activity.
    other = {"id": "evt_y", "type": "session.next.text.delta",
             "properties": {"sessionID": "ses_someone_else", "delta": "not yours"}}
    assert map_session_event(other, _SID) is None
    assert map_session_event({"id": "evt_z", "type": "server.connected", "properties": {}}, _SID) is None
    assert map_session_event({"id": "e", "type": "plugin.added", "properties": {}}, _SID) is None
    assert map_session_event({"id": "e", "type": "catalog.updated", "properties": {}}, _SID) is None


class _Stream:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return iter(self._lines)


def test_session_events_reads_data_frames_and_never_times_out_a_quiet_turn(monkeypatch):
    """A turn is silent whenever the model is thinking rather than emitting. A read timeout here
    would sever a working stream mid-turn, so connect is bounded and read is not."""
    import json as _json

    captured = {}

    def fake_stream(method, url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _Stream([
            "data: " + _json.dumps(_frame("session.next.text.delta", delta="hi")),
            "",
            "data: not json at all",
            ": keepalive comment",
            "data: " + _json.dumps(_frame("session.next.text.ended", text="hi there")),
        ])

    monkeypatch.setattr("sage.driver.opencode.httpx.stream", fake_stream)
    out = list(OpenCodeClient("http://x").session_events(_SID, directory="/mnt/code"))

    assert captured["url"] == "http://x/event"        # global stream, not the durable one
    # Without this the stream answers for the SERVER's directory, not the session's: measured
    # against the pinned binary, 0 session frames omitted vs every frame of the turn present.
    assert captured["params"] == {"directory": "/mnt/code"}
    assert captured["timeout"].read is None           # a thinking model is not a broken stream
    assert captured["timeout"].connect == 10.0        # but an absent stream fails fast
    assert [e.payload for e in out] == [
        {"delta": "hi", "final": False},
        {"text": "hi there", "final": True},
    ]


def test_closing_the_stream_stops_the_reader_because_event_never_ends(monkeypatch):
    """/event has no end and no `?after=`. A watcher that merely stopped iterating would leave a
    reader parked on a socket that goes on buffering — this session's NEXT turn, and the one after
    that, into a queue nobody drains. A Chat session is reused across turns, so that is a leak that
    grows with the conversation."""
    import json as _json

    frame = "data: " + _json.dumps(_frame("session.next.text.delta", delta="x"))
    closed: list = []

    class _Endless(_Stream):
        def __init__(self):
            super().__init__([])

        def iter_lines(self):
            while True:
                yield frame

        def close(self):
            closed.append(True)

    monkeypatch.setattr("sage.driver.opencode.httpx.stream", lambda *a, **k: _Endless())
    stream = OpenCodeClient("http://x").session_events(_SID)
    seen = 0
    for _ in stream:
        seen += 1
        if seen == 3:
            stream.close()
    assert seen == 3          # the frame after close() is not delivered
    assert closed == [True]   # and the connection went with it


def _spawn_capturing_env(monkeypatch, cwd):
    """Start an OpenCodeServer against a fake `opencode serve` and hand back the env it spawned with."""
    import io

    from sage.driver import server as drv

    seen = {}

    class _Proc:
        pid = 1
        stdout = io.StringIO("opencode server listening on http://127.0.0.1:4096\n")

        def poll(self):
            return 0

    def _popen(cmd, **kw):
        seen["env"] = kw["env"]
        return _Proc()

    monkeypatch.setattr(drv.subprocess, "Popen", _popen)
    drv.OpenCodeServer(cwd=cwd).start(ready_timeout_s=5.0)
    return seen["env"]


def test_opencode_is_pointed_at_the_voiced_config_not_the_tokenised_source(monkeypatch, tmp_path):
    """The checked-in config says `{assistantName}`; only the installed copy says the pack's word.

    Handing OpenCode the source makes the chat agent introduce itself with a pair of braces, in
    every install, packed or not — so the voiced copy has to win when it is there.
    """
    from sage.driver import server as drv

    cwd = tmp_path / "repo"
    cwd.mkdir()
    (cwd / "opencode.json").write_text('{"agent": {"sage-chat": {"prompt": "{assistantName}"}}}')
    voiced = tmp_path / "global" / "opencode.json"
    voiced.parent.mkdir()
    voiced.write_text('{"agent": {"sage-chat": {"prompt": "Ada"}}}')
    monkeypatch.setattr(drv, "_VOICED_CONFIG", voiced)

    env = _spawn_capturing_env(monkeypatch, cwd)

    assert env["OPENCODE_CONFIG"] == str(voiced)
    assert "{assistantName}" not in voiced.read_text()


def test_the_source_config_is_the_fallback_when_nothing_was_installed(monkeypatch, tmp_path):
    """A boot where `_install_opencode_config` could not write must still leave the gateway wired.

    Without any OPENCODE_CONFIG, OpenCode never sees the sage-gateway provider and drops to its
    free tier — a 429 in the user's face is worse than an unresolved token in a prompt.
    """
    from sage.driver import server as drv

    cwd = tmp_path / "repo"
    cwd.mkdir()
    (cwd / "opencode.json").write_text("{}")
    monkeypatch.setattr(drv, "_VOICED_CONFIG", tmp_path / "nowhere" / "opencode.json")

    env = _spawn_capturing_env(monkeypatch, cwd)

    assert env["OPENCODE_CONFIG"] == str(cwd / "opencode.json")
