"""/api/diag/log — the log ring as plain text, for a workspace with no shell."""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from sage.orchestrator import app as app_module


def _client(lines: list[str]) -> TestClient:
    app_module._LOG_RING.clear()
    app_module._LOG_RING.extend(lines)
    return TestClient(app_module.control_app)


def test_returns_the_ring_newest_last_as_plain_text():
    r = _client(["first", "second"]).get("/api/diag/log")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "first\nsecond"


def test_q_filters_case_insensitively():
    # The whole point: /api/diag/log?q=rescue in a URL bar, with no shell to pipe through.
    r = _client(["model policy: RESCUE examined=2", "turn: agent=None", "rescue examined=3"]).get(
        "/api/diag/log", params={"q": "rescue"})
    assert r.text == "model policy: RESCUE examined=2\nrescue examined=3"


def test_no_match_says_so_rather_than_returning_blank():
    # An empty page reads like "the endpoint is broken"; it isn't.
    r = _client(["turn: agent=None"]).get("/api/diag/log", params={"q": "rescue"})
    assert "no lines match" in r.text and "rescue" in r.text


def test_n_caps_to_the_newest_lines():
    r = _client([f"line {i}" for i in range(10)]).get("/api/diag/log", params={"n": 3})
    assert r.text == "line 7\nline 8\nline 9"


# ---- /api/diag: whether this interpreter can read inside a Data Source --------------------------
# The builder has no terminal, and `domino_data` sits in the image's SYSTEM python while the
# orchestrator runs from uv's isolated venv. Those were different answers with nothing in between to
# say so, and the cascade reported "the Domino data library is not installed here" on a deployment
# that had the package. This field is the answer, next to sage_rev, because both say whether a
# rebuild took effect.


def test_diag_reports_whether_the_data_library_is_importable():
    body = TestClient(app_module.control_app).get("/api/diag").json()
    assert set(body["data_library"]) == {"ok", "detail"}
    assert body["data_library"]["detail"]          # never a bare False with no reason


def test_diag_reports_the_import_error_when_the_data_library_is_missing(monkeypatch):
    # The reason, verbatim, rather than a bare "no": ImportError and (say) a broken transitive
    # dependency need different fixes, and the person reading this in a browser cannot run anything.
    monkeypatch.setattr(app_module, "data_library_ready",
                        lambda: "ImportError: No module named 'domino_data'")
    body = TestClient(app_module.control_app).get("/api/diag").json()
    assert body["data_library"] == {
        "ok": False, "detail": "ImportError: No module named 'domino_data'"}


# ---- the warnings-only ring (#205) -------------------------------------------------------------
# Twice, an investigation into a cut model stream spent a whole live reproduction and came back with
# nothing, because the 400-line ring had rolled past the failure before anyone could read it. A ring
# that rolls cannot answer "did this warn?" — absence in it means nothing. These pin the second ring
# that can.


def test_a_warning_lands_in_both_rings():
    app_module._LOG_RING.clear()
    app_module._WARN_RING.clear()
    logging.getLogger("sage.test").warning("gateway ended the stream with no finish_reason")
    assert any("no finish_reason" in ln for ln in app_module._LOG_RING)
    assert any("no finish_reason" in ln for ln in app_module._WARN_RING)


def test_an_info_line_stays_out_of_the_warning_ring():
    # The whole value of the second ring is that a loud turn cannot fill it.
    app_module._WARN_RING.clear()
    logging.getLogger("sage.test").info("routed request -> streaming (first byte 0.4s)")
    assert not list(app_module._WARN_RING)


def test_the_warning_survives_the_turn_that_buries_it_in_the_rolling_ring():
    app_module._LOG_RING.clear()
    app_module._WARN_RING.clear()
    log = logging.getLogger("sage.test")
    log.warning("gateway ended the stream with no finish_reason")
    for i in range(app_module._LOG_RING.maxlen + 10):   # one ordinary build turn's worth of noise
        log.info("tool ran %d", i)
    assert not any("no finish_reason" in ln for ln in app_module._LOG_RING)   # rolled, as before
    body = TestClient(app_module.control_app).get("/api/diag/log", params={"warn": 1}).text
    assert "no finish_reason" in body


def test_a_quiet_ring_says_nothing_warned_rather_than_no_lines_match():
    # An empty answer here is a real answer — nothing warned — so it must not read like a filter
    # that missed, which is what "(no lines match '')" looks like.
    app_module._WARN_RING.clear()
    body = TestClient(app_module.control_app).get("/api/diag/log", params={"warn": 1}).text
    assert body == "(no warnings yet)"


def test_diag_carries_the_warning_tail_without_anyone_knowing_the_parameter():
    app_module._WARN_RING.clear()
    logging.getLogger("sage.test").warning("gateway ended the stream with no finish_reason")
    body = TestClient(app_module.control_app).get("/api/diag").json()
    assert any("no finish_reason" in ln for ln in body["warn_tail"])


# ---- /api/diag/opencode: OpenCode's own log ------------------------------------------------
# Its MCP servers are invisible at the default log level — it connects, or silently does not, and
# the log reads the same either way. That cost a week of chasing a missing Live read, so the level
# is now raisable and the log is servable whole rather than 30 lines at a time.


def _oc_client(lines: list[str], monkeypatch) -> TestClient:
    monkeypatch.setattr(app_module.orchestrator, "_opencode_log_tail",
                        lambda n=30: lines, raising=False)
    return TestClient(app_module.control_app)


def test_the_opencode_log_is_served_whole_and_filtered(monkeypatch):
    r = _oc_client(["message=loading path=/x/opencode.json",
                    "message=mcp server connected name=sage-live-read",
                    "message=init"], monkeypatch).get("/api/diag/opencode", params={"q": "mcp"})
    assert r.text == "message=mcp server connected name=sage-live-read"


def test_no_opencode_log_match_says_so_rather_than_returning_blank(monkeypatch):
    """An empty page reads like a broken endpoint. Here the empty answer IS the finding — no MCP
    line at all is what "OpenCode never dialled it" looks like."""
    r = _oc_client(["message=init"], monkeypatch).get("/api/diag/opencode", params={"q": "mcp"})
    assert "no lines match" in r.text and "mcp" in r.text


def test_the_log_level_is_reported_so_an_empty_answer_can_be_read(monkeypatch):
    """"No mcp lines" means nothing without knowing whether anything would have printed one. The
    level says which of the two an empty answer is."""
    monkeypatch.delenv("SAGE_OPENCODE_LOG_LEVEL", raising=False)
    assert TestClient(app_module.control_app).get("/api/diag").json()[
        "opencode_log_level"] == "default (quiet)"
    monkeypatch.setenv("SAGE_OPENCODE_LOG_LEVEL", "DEBUG")
    assert TestClient(app_module.control_app).get("/api/diag").json()[
        "opencode_log_level"] == "DEBUG"


# ---- /api/diag/mcp: asking OpenCode itself ---------------------------------------------------
# Its MCP client has no logger — the only `mcp` names in the binary are CLI commands — so no log
# level answers "did it connect". `opencode mcp list` does.


def test_it_asks_opencode_from_the_directory_a_chat_turn_runs_in(monkeypatch, tmp_path):
    """Project config resolves off the git root of wherever OpenCode is RUN, so asking from the
    server's own cwd would answer about a directory no turn ever uses."""
    work = tmp_path / "mnt" / "code" / ".sage" / "chat-work"
    work.mkdir(parents=True)
    seen = {}

    class _Server:
        def cli(self, args, cwd=None, timeout_s=30.0):
            seen.update(args=args, cwd=cwd)
            return "sage-live-read  connected  2 tools"

    monkeypatch.setattr(app_module.orchestrator, "_oc_server", _Server(), raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_wm",
                        type("W", (), {"_dir": tmp_path / "mnt" / "code"})(), raising=False)

    r = TestClient(app_module.control_app).get("/api/diag/mcp")

    assert seen["args"] == ["mcp", "list"]
    assert seen["cwd"] == str(work)
    assert "sage-live-read  connected  2 tools" in r.text


def test_an_unstarted_opencode_says_so_instead_of_looking_like_no_servers(monkeypatch):
    """OpenCode starts on the first turn, not at boot. An empty answer here would read exactly like
    "configured nothing", which is the mistake this whole endpoint exists to stop."""
    monkeypatch.setattr(app_module.orchestrator, "_oc_server", None, raising=False)

    r = TestClient(app_module.control_app).get("/api/diag/mcp")

    assert "has not been started yet" in r.text
    assert "Send one chat message" in r.text


def test_only_the_two_commands_it_knows_are_ever_run(monkeypatch, tmp_path):
    """`cmd` reaches a subprocess argument list. It is a fixed choice of two, not a passthrough."""
    seen = {}

    class _Server:
        def cli(self, args, cwd=None, timeout_s=30.0):
            seen["args"] = args
            return "ok"

    monkeypatch.setattr(app_module.orchestrator, "_oc_server", _Server(), raising=False)
    monkeypatch.setattr(app_module.orchestrator, "_wm", None, raising=False)
    client = TestClient(app_module.control_app)

    client.get("/api/diag/mcp", params={"cmd": "debug"})
    assert seen["args"] == ["mcp", "debug"]
    client.get("/api/diag/mcp", params={"cmd": "add https://evil.example/x"})
    assert seen["args"] == ["mcp", "list"]
