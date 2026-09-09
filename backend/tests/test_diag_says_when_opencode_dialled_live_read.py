"""When OpenCode dialled the Live read server, and what ran before it did.

Measured live on c4f9854: OpenCode connected FIFTEEN MINUTES after boot, with no turn running, and
every Chat turn before that went out with no Live read tools in its list. The model was told to call
`sage-live-read_` tools it had not been given, so it said it could not see the person's data — and
every surface that could have contradicted it agreed with the wrong half. `opencode mcp list` said
connected. /api/diag reached the server and listed both tools. All true, and all about the SERVER;
nothing was about the moment. This is the moment.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi.testclient import TestClient

from sage.orchestrator import app as app_module

from .test_chat_turn import Turn, _orch


def test_a_server_opencode_has_never_dialled_says_never(tmp_path: Path):
    """`never` is the finding, and the reason this is not a timestamp field that is simply absent:
    an absent field reads as "the page is old", and reads that way whether or not it is."""
    orch, _ = _orch(tmp_path, [Turn(text="ok")])

    assert orch.live_read_reach() == {"opencode_connected": "never", "turns_without_tools": 0}


def test_a_chat_turn_that_ran_before_the_handshake_is_counted(tmp_path: Path):
    orch, _ = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))

    assert orch.live_read_reach()["turns_without_tools"] == 1


def test_the_handshake_is_reported_in_seconds_after_boot(tmp_path: Path):
    """Seconds after boot, not a clock time: the two facts only mean anything against each other.
    "03:20:52" says nothing on its own, and a person reading this page should not have to go and
    find the boot line to subtract it."""
    orch, _ = _orch(tmp_path, [Turn(text="ok")])
    orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    reach = orch.live_read_reach()
    assert isinstance(reach["opencode_connected"], float)
    assert reach["opencode_connected"] >= 0


def test_a_diag_probe_is_not_counted_as_opencode_arriving(tmp_path: Path):
    """Loading the diagnostics page WRITES to the log it exists to read. Without this the field
    would fill itself in the moment anyone opened /api/diag, and always report a healthy handshake
    — the exact failure this page was built to stop."""
    orch, _ = _orch(tmp_path, [Turn(text="ok")])
    orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, probe=True)

    assert orch.live_read_reach()["opencode_connected"] == "never"


def test_the_turn_count_stops_once_the_tools_are_there(tmp_path: Path):
    """It counts turns that went out WITHOUT Live read, not turns. A number that kept climbing
    after the handshake would say nothing about the failure it is here to measure."""
    orch, _ = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "before"))
    orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    list(orch.chat_stream(tid, "after"))

    assert orch.live_read_reach()["turns_without_tools"] == 1


def test_the_two_fields_reach_the_page(tmp_path: Path, monkeypatch):
    orch, _ = _orch(tmp_path, [Turn(text="ok")])
    monkeypatch.setattr(app_module, "orchestrator", orch, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".config" / "opencode"
    cfg.mkdir(parents=True)
    (cfg / "opencode.json").write_text('{"mcp": {}}')

    out = TestClient(app_module.control_app).get("/api/diag").json()["mcp"]

    assert out["opencode_connected"] == "never"
    assert out["turns_without_tools"] == 0


# ---------------------------------------------------------------------------
# The page was connecting the thing it measured, then reporting the connection as the finding.
#
# Live, 2026-09-09: every "OpenCode connected" line sat two seconds after a diag probe,
# `opencode_connected` read 10.0s (the first page load, not a turn), `opencode_says` read connected
# because asking had just connected it — and three chat turns went out with no tools in between
# while the page called the wiring healthy. Both halves of /api/diag/mcp reach the server
# themselves: `GET /mcp` connects OpenCode lazily, and `opencode mcp list` is a second OpenCode
# process that dials on startup. Neither can carry the probe header.


def test_a_dial_inside_a_diagnostic_is_not_recorded_as_opencodes(tmp_path: Path):
    orch, _ = _orch(tmp_path, [Turn(text="ok")])

    with orch.diagnostic_window():
        orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert orch.live_read_reach()["opencode_connected"] == "never"


def test_a_real_dial_outside_one_still_counts(tmp_path: Path):
    """The guard must not swallow the signal it exists to protect."""
    orch, _ = _orch(tmp_path, [Turn(text="ok")])

    orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert isinstance(orch.live_read_reach()["opencode_connected"], float)


def test_overlapping_diagnostics_do_not_unmark_each_other(tmp_path: Path):
    """Depth, not a boolean: the inner one finishing must not clear the outer one's window."""
    orch, _ = _orch(tmp_path, [Turn(text="ok")])

    with orch.diagnostic_window():
        with orch.diagnostic_window():
            pass
        orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert orch.live_read_reach()["opencode_connected"] == "never"


def test_the_window_closes_even_when_the_body_raises(tmp_path: Path):
    orch, _ = _orch(tmp_path, [Turn(text="ok")])

    with contextlib.suppress(RuntimeError), orch.diagnostic_window():
        raise RuntimeError("boom")
    orch.live_read_call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert isinstance(orch.live_read_reach()["opencode_connected"], float)
