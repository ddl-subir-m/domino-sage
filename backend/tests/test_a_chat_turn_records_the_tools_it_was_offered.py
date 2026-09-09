"""A Chat turn writes down whether Live read was among the tools the model was handed.

Sage has told people it could not see their data, naming the `sage-live-read_` tools out of its own
prompt as "not available in this turn" — while `opencode mcp list` reported the server connected,
and `/api/diag` reached it and listed both tools. Every one of those can be true at once: the tool
list is fixed when a turn starts, and the MCP handshake for a session has been seen landing half a
minute after that. The request the shim is holding is the only place the list the model actually got
is written down, so this is the only surface that can tell a tool that never arrived from a model
that had it and said otherwise.
"""
from __future__ import annotations

import logging

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b", sovereign_implement="sovereign-8b", sovereign_ask="sovereign-8b",
    plan="strong-vendor", implement="cheap-vendor", ask="ask-vendor",
)

_LIVE_READ = ("sage-live-read_live_read_table", "sage-live-read_live_read_files")


def _req(*names: str) -> dict:
    return {"messages": [],
            "tools": [{"type": "function", "function": {"name": n}} for n in ("read", *names)]}


def _run(caplog, tools: tuple[str, ...], thread: str = "thr_1") -> list[str]:
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    shim = EnforcementShim(control, CATALOG, FakeGatewayClient())
    control.arm_chat(thread)
    with caplog.at_level(logging.INFO, logger="sage.shim"):
        list(shim.handle(_req(*tools), {}))
        # A turn is many requests. The line is about the turn, so it must not repeat per request.
        list(shim.handle(_req(*tools), {}))
    return [r.getMessage() for r in caplog.records if "chat tools" in r.getMessage()]


def test_a_turn_that_was_offered_live_read_names_both_tools(caplog):
    said = _run(caplog, _LIVE_READ)

    assert said == [("chat tools: live read sage-live-read_live_read_files, "
                     "sage-live-read_live_read_table — all 3: read, "
                     "sage-live-read_live_read_files, sage-live-read_live_read_table")]


def test_a_turn_that_was_offered_none_says_so_rather_than_nothing(caplog):
    """The finding, and the one an absent log line cannot carry: silence here would read the same
    whether the tools were missing or the turn never ran."""
    assert _run(caplog, ()) == ["chat tools: live read NOT OFFERED — all 1: read"]


def test_a_build_turn_is_not_asked_the_question(caplog):
    """Live read is minted per Chat turn (ADR-0041). A Build turn has its own token and its own
    prompt, and answering here for it would put a line on every request Sage ever makes."""
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    shim = EnforcementShim(control, CATALOG, FakeGatewayClient())
    with caplog.at_level(logging.INFO, logger="sage.shim"):
        list(shim.handle(_req(*_LIVE_READ), {}))

    assert not [r for r in caplog.records if "chat tools" in r.getMessage()]


def test_the_whole_list_is_recorded_beside_the_verdict(caplog):
    """`NOT OFFERED` alone cannot separate two very different turns.

    Live on 2026-09-09, three turns read NOT OFFERED while the MCP server was connected, the config
    was clean and the port was right. The next question — did OpenCode send no MCP tools at all, or
    send them under names nothing here recognises — had nothing on any surface to answer it. The
    list is the answer, and it costs one line a turn.
    """
    said = _run(caplog, ("glob", "sage-live-read_live_read_table"))

    assert said == [("chat tools: live read sage-live-read_live_read_table — all 3: "
                     "glob, read, sage-live-read_live_read_table")]


def test_the_list_is_what_the_model_GOT_not_what_opencode_proposed(caplog):
    """Read after the denial filter, so a tool this shim strips is absent here too. Logging the
    request as it arrived would describe a turn that never happened, and the whole point of this
    line is that it is the one record of what the model actually held."""
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    shim = EnforcementShim(control, CATALOG, FakeGatewayClient())
    control.arm_chat("thr_web")
    with caplog.at_level(logging.INFO, logger="sage.shim"):
        # webfetch is denied on every turn the orchestrator did not arm for the web.
        list(shim.handle(_req("webfetch", "sage-live-read_live_read_table"), {}))
    said = [r.getMessage() for r in caplog.records if "chat tools" in r.getMessage()]

    assert said == [("chat tools: live read sage-live-read_live_read_table — all 2: "
                     "read, sage-live-read_live_read_table")]
    assert "webfetch" not in said[0]
