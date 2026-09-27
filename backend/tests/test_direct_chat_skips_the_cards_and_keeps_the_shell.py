"""Direct Chat skips the cards that only ask, and a data question keeps bash (ADR-0070).

Guided is the missing field. Every turn here passes `how_sage_works="direct"`. A greeting still
withholds every tool, a known source request still asks for the store with none, and "build me a
dashboard" still leaves Chat. What changes is the ask that Guided would have stopped on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import NEEDS_MORE_THAN_SQL_MARKER
from sage.workspace.threads import ThreadStore

from .fake_opencode import Turn
from .test_a_bound_dataset_with_no_files_asks_which_ones import (
    _calls_dataset,
    _thread_with_dataset,
)
from .test_a_bound_dataset_with_no_files_asks_which_ones import (
    _orch as _dataset_orch,
)
from .test_a_chat_build_request_is_asked_which_table import (
    _gong_warehouse,
    _thread_with_source,
)
from .test_a_chat_build_request_is_asked_which_table import _orch as _table_orch
from .test_a_known_source_request_is_a_short_request import ASK, CapturingOpenCode
from .test_a_table_named_in_the_sentence_is_not_asked_for_again import BARE, NAMED
from .test_chat_turn import IntentGateway, _orch


def _direct(tmp_path: Path, turns: list[Turn], label: str = "data_answer"):
    verdict = {"label": label, "confidence": 0.93}
    orch, oc = _orch(tmp_path, turns, gateway=IntentGateway(verdict),
                     client=lambda ws: CapturingOpenCode(ws, list(turns)))
    project = orch.project(start_preview=False)
    oc.control = project.control
    oc.shim = project.shim
    tid = orch.create_thread()["id"]
    return orch, oc, tid


def test_a_direct_greeting_still_has_no_tools(tmp_path: Path):
    orch, oc, tid = _direct(tmp_path, [Turn(text="Hi!")], label="plain_answer")
    list(orch.chat_stream(tid, "hi", how_sage_works="direct"))
    assert oc.profiles[0]["tools"] == []
    assert oc.snapshots[0].read_only_reason == "greeting"


@pytest.mark.parametrize("label", ["data_answer", "data_artifact"])
def test_a_direct_data_question_is_offered_bash(tmp_path: Path, label: str):
    """`READ_ONLY_DENIED` is what takes the shell, and Direct does not arm it for a data question.

    `data_answer` is that denylist. `data_artifact` is the other way bash disappears — an
    allowlist — so the same question on that label is not that lane either.
    """
    orch, oc, tid = _direct(tmp_path, [Turn(text="42 users.")], label=label)
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    list(orch.chat_stream(tid, "How many users joined last month?", how_sage_works="direct"))
    assert "bash" in oc.profiles[-1]["tools"]
    assert oc.snapshots[-1].read_only_turn is False
    assert oc.snapshots[-1].chat_artifact_turn is False
    assert oc.prompts[-1]["agent"] == "sage-chat-direct"


def test_a_direct_source_request_still_asks_with_no_tools(tmp_path: Path):
    orch, oc, tid = _direct(tmp_path, [Turn(text="Please attach the warehouse.")])
    list(orch.chat_stream(tid, ASK, how_sage_works="direct"))
    assert oc.snapshots[-1].read_only_reason == "source"
    assert oc.profiles[-1]["tools"] == []


def test_a_named_table_is_recorded_and_no_card_is_yielded(tmp_path: Path):
    orch, oc = _table_orch(tmp_path, [Turn(text="Here are the rows.")])
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    events = [e for e in orch.chat_stream(tid, NAMED, how_sage_works="direct") if isinstance(e, dict)]
    assert not [e for e in events if e.get("type") == "table-candidates"]
    items = ThreadStore(orch._chat_project().record.path).read_context(tid).get("items") or []
    tables = [(i.get("scope") or {}).get("table") for i in items]
    assert "GONG__CALLS" in tables
    assert oc.prompts, "recording the table let the turn run"


def test_an_unnamed_store_does_not_yield_a_card(tmp_path: Path):
    """The card path of the table offer. Guided draws it; Direct returns before the history write."""
    orch, oc = _table_orch(tmp_path, [Turn(text="Here are the rows.")])
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    events = [e for e in orch.chat_stream(tid, BARE, how_sage_works="direct") if isinstance(e, dict)]
    assert not [e for e in events if e.get("type") == "table-candidates"]
    items = ThreadStore(orch._chat_project().record.path).read_context(tid).get("items") or []
    assert not any((i.get("scope") or {}).get("table") for i in items)
    assert oc.prompts, "withholding the card did not end the turn"


def test_a_dataset_with_no_file_does_not_yield_a_card(tmp_path: Path):
    orch, oc = _dataset_orch(tmp_path, _calls_dataset(tmp_path), [Turn(text="Seven calls.")])
    tid = _thread_with_dataset(orch, "ds_revenue_2026", "revenue_2026")
    events = list(orch.chat_stream(tid, "how many calls were there?", how_sage_works="direct"))
    assert not [e for e in events if e.get("type") == "dataset-files"]
    assert oc.prompts, "the agent is who says the Dataset has no file"


def test_a_build_sentence_still_hands_off(tmp_path: Path):
    orch, oc, tid = _direct(tmp_path, [Turn(text="never said")])
    events = list(orch.chat_stream(tid, "build me a dashboard", how_sage_works="direct"))
    assert next(e for e in events if e.get("type") == "done")["decision"] == "handoff"
    assert oc.prompts == []


def test_an_investigative_question_does_not_open_an_investigation(tmp_path: Path):
    orch, oc, tid = _direct(tmp_path, [Turn(text="Looked.")])
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    prompt = "Investigate which accounts look like adopters and score them."
    events = list(orch.chat_stream(tid, prompt, how_sage_works="direct"))
    assert not [e for e in events if e.get("type") == "investigation-offer"]
    state = ThreadStore(orch._chat_project().record.path).read_investigation(tid).get("state")
    assert state != "open"
    assert oc.prompts


def test_the_other_lane_marker_is_stripped_and_no_card_is_drawn(tmp_path: Path):
    reply = f"The median needs a window.\n{NEEDS_MORE_THAN_SQL_MARKER}"
    orch, oc, tid = _direct(tmp_path, [Turn(text=reply)])
    inner = oc.send_prompt

    def counted(*args, **kwargs):
        orch._statements_tried[tid] = 1
        return inner(*args, **kwargs)

    oc.send_prompt = counted
    events = list(orch.chat_stream(tid, "what is the median?", how_sage_works="direct"))
    assert not [e for e in events if e.get("type") == "other-lane-offer"]
    assert NEEDS_MORE_THAN_SQL_MARKER not in "".join(
        e.get("text") or "" for e in events if isinstance(e, dict))
