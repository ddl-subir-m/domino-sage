"""A Chat turn writes its answer once it has one, and offers further checks as follow-ups (#606).

A mimo replay had resolved its answer — ten accounts — and spent its remaining reads measuring
how many active customers had any records at all, which nobody asked. It reached the 600s ceiling
and the answer came only on Continue. The prompt now says where the question ends, and a read made
late in a Chat turn says so again beside its result.
"""
from __future__ import annotations

from pathlib import Path

from .fake_opencode import Turn
from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call, _orch, _token

_READ = {"source": "Snowflake-Data-Warehouse", "database": "DWH", "schema": "MARTS",
         "table": "GONG__CALLS", "limit": 1}
_NUDGE = "If what you have measured answers the question, write the answer now"


def _turn(tmp_path: Path):
    orch, oc = _orch(tmp_path, Warehouse(), [Turn(text="ok"), Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    list(orch.chat_stream(tid, "which active customers asked for ARM support?"))
    return orch, oc, tid


def _aged(orch, tid: str, seconds: float) -> None:
    token, minted, include = orch._live_read[tid]
    orch._live_read[tid] = (token, minted - seconds, include)


def test_the_chat_prompt_says_to_answer_before_further_checks(tmp_path: Path):
    _, oc, _ = _turn(tmp_path)
    prompt = oc.prompts[-1]["text"]

    assert "write the answer and its result table before any further read" in prompt
    assert "follow-ups the person can ask for, not as more reads in this turn" in prompt
    assert "keep that count in the answer as a caveat" in prompt
    assert "and reports coverage" not in prompt


def test_a_read_early_in_the_turn_carries_no_nudge(tmp_path: Path):
    orch, oc, _tid = _turn(tmp_path)

    said = _call(orch, "live_read_table", {"token": _token(oc), **_READ})

    assert "Columns: ID, TITLE" in said
    assert _NUDGE not in said


def test_a_read_late_in_a_chat_turn_returns_its_result_and_asks_for_the_answer(tmp_path: Path):
    orch, oc, tid = _turn(tmp_path)
    _aged(orch, tid, 400)

    said = _call(orch, "live_read_table", {"token": _token(oc), **_READ})

    assert "Columns: ID, TITLE" in said, "the read still returns what it read"
    assert _NUDGE in said
    assert "has run 6 minutes" in said


def test_a_build_read_is_never_nudged(tmp_path: Path):
    orch, oc, tid = _turn(tmp_path)
    orch._chat_project().build_conversation = tid
    _aged(orch, tid, 400)

    said = _call(orch, "live_read_table", {"token": _token(oc), **_READ})

    assert "Columns: ID, TITLE" in said
    assert _NUDGE not in said


def test_the_clock_starts_again_on_the_next_turn(tmp_path: Path):
    orch, oc, tid = _turn(tmp_path)
    _aged(orch, tid, 400)
    list(orch.chat_stream(tid, "Continue"))

    said = _call(orch, "live_read_table", {"token": _token(oc), **_READ})

    assert _NUDGE not in said
