"""A Build turn that dies on a gateway error carries the request a Retry sends again (#663).

Chat's Retry walks back to the person's own bubble and sends it. Build cannot: the bubble over a
click is the click ("Build it.", "Use Snowflake.", "Approved the plan."), not the request. So the
server, which holds the request, writes it onto the row that reports the failure, the way
`build-stalled` does — and leaves it empty where re-sending a sentence is not the same turn.
"""
from __future__ import annotations

from pathlib import Path

from .test_retry_an_approved_plan import PLAN, Turn, _build, _done


def _error(events: list[dict]) -> dict:
    return next(e for e in events if e["type"] == "error")


def test_a_build_request_that_hits_a_gateway_error_carries_its_prompt(tmp_path: Path):
    orch, _oc = _build(tmp_path, [PLAN, Turn(writes={"src/App.tsx": "// the table\n"}),
                                  Turn(writes={"src/App.tsx": "// more\n"})], break_on={3})
    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())

    events = list(orch.build_stream("make the table sortable"))

    assert _done(events)["decision"] == "gateway error"
    assert _error(events)["prompt"] == "make the table sortable"
    # Written, not only streamed: a reload draws the Retry from the transcript.
    project = orch.project(start_preview=False)
    rows = project.app_for_turn().read_history(project.build_conversation)
    assert [r.get("prompt") for r in rows if r.get("type") == "error"] == ["make the table sortable"]


def test_an_approved_build_that_hits_a_gateway_error_carries_no_prompt(tmp_path: Path):
    """The request an approval sends is Sage's own control prompt. Re-sent as typed text it would be
    a new request, not the approval; the row already says to type "try again" for this one."""
    orch, _oc = _build(tmp_path, [PLAN, Turn(writes={"src/App.tsx": "// half a table\n"})],
                       break_on={2})
    list(orch.build_stream("build me a consumption dashboard"))

    events = list(orch.approve_stream())

    assert _done(events)["decision"] == "gateway error"
    assert _error(events)["prompt"] == ""
