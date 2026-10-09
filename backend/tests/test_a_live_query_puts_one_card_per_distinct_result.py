"""A composed statement puts one card on screen per distinct result, and none for no rows (#726).

Measured on haiku, demo prompts 1 and 4: one turn showed three tables, one of them empty, and
another turn showed the same table twice. `_statement` wrote a card for every statement it ran — a
filter that matched nothing put a "0 rows" card under a correct-looking title, and the same result
asked for again under a second title put a second card beside the first.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from sage.liveread import run
from sage.resources.provider import StatementRows

SOURCE = "Snowflake-Data-Warehouse"


@dataclass
class _Source:
    connector_type: str = "SnowflakeConfig"
    name: str = SOURCE


def _turn(tmp_path: Path, answers: list[StatementRows], *, keep_rows: bool = True,
          seen: dict | None = None, events: list | None = None) -> run.Turn:
    queue = list(answers)
    seen = {} if seen is None else seen

    def same_result(digest: str, path: str) -> str:
        return seen.setdefault(digest, path)

    return run.Turn(
        thread_id="thr_a",
        examples_dir=tmp_path / "examples" / "thr_a",
        keep_rows=keep_rows,
        bound={"datasource": (SOURCE,)},
        source_for=lambda n: _Source() if n == SOURCE else None,
        run_statement=lambda source, sql, *, limit: queue.pop(0),
        same_result=same_result,
        record_data_use=(lambda event, reply: events.append(event)) if events is not None else None,
    )


def _ask(turn: run.Turn, title: str, sql: str = "SELECT STAGE, COUNT(*) AS N FROM T GROUP BY 1"):
    return run.perform("live_read_query", {"source": SOURCE, "sql": sql, "title": title}, turn)


def _cards(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "examples" / "thr_a").glob("*.table.json"))


@pytest.mark.parametrize("keep_rows", [True, False])
def test_a_statement_that_returns_no_rows_puts_no_card_on_screen(tmp_path, keep_rows):
    events: list = []
    turn = _turn(tmp_path, [StatementRows(["STAGE", "N"], [], False)],
                 keep_rows=keep_rows, events=events)

    said = _ask(turn, "Pipeline FY2028 Q1")

    assert _cards(tmp_path) == []
    assert "no rows" in said
    assert "nothing was put on screen" in said
    assert "on screen as a" not in said
    # The read still happened, and the record of what was used says so — with no file to point at.
    assert [e["result_rows"] for e in events] == [0]
    assert [e["artifact"] for e in events] == [""]


@pytest.mark.parametrize("keep_rows", [True, False])
def test_the_same_result_under_a_second_title_is_not_a_second_card(tmp_path, keep_rows):
    rows = [["Discovery", 60], ["Proposal", 48]]
    events: list = []
    turn = _turn(tmp_path, [StatementRows(["STAGE", "N"], rows, False),
                            StatementRows(["STAGE", "N"], rows, False)],
                 keep_rows=keep_rows, events=events)

    _ask(turn, "Open pipeline by stage")
    said = _ask(turn, "Pipeline this quarter")

    assert _cards(tmp_path) == ["open-pipeline-by-stage.table.json"]
    assert "same result as a card already on screen" in said
    # The second read is recorded against the card that shows it.
    assert [e["artifact"] for e in events] == ["examples/thr_a/open-pipeline-by-stage.table.json"] * 2


@pytest.mark.parametrize("second", [
    StatementRows(["STAGE", "N"], [["Discovery", 61], ["Proposal", 48]], False),
    StatementRows(["TEAM", "N"], [["Discovery", 60], ["Proposal", 48]], False),
])
def test_a_different_result_is_a_second_card(tmp_path, second):
    """The sibling: one changed value, or the same numbers under different columns, is a different
    result — two counts that happen to agree are two answers, not one."""
    turn = _turn(tmp_path, [StatementRows(["STAGE", "N"], [["Discovery", 60], ["Proposal", 48]],
                                          False), second])

    _ask(turn, "Open pipeline by stage")
    said = _ask(turn, "Next quarter")

    assert _cards(tmp_path) == ["next-quarter.table.json", "open-pipeline-by-stage.table.json"]
    assert "same result" not in said


def test_the_orchestrator_remembers_results_for_one_turn_only(tmp_path):
    """The wiring: the Turn the orchestrator builds carries the memory, and a new turn starts it
    over — the same numbers asked for on the next question are that question's card."""
    from .fake_opencode import Turn
    from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call, _orch, _token

    class Store(Warehouse):
        def run_statement(self, source, sql, *, limit, timeout_s=30.0):
            return StatementRows(["STAGE", "N"], [["Discovery", 60]], False)

    orch, oc = _orch(tmp_path, Store(), turns=[Turn(text="ok"), Turn(text="ok")])
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1", "name": SOURCE})
    examples = orch.project(start_preview=False).record.path / "examples" / tid

    def ask(title: str) -> str:
        return _call(orch, "live_read_query", {
            "token": _token(oc), "source": SOURCE, "title": title,
            "sql": "SELECT STAGE, COUNT(*) AS N FROM DWH.MARTS.OPPS GROUP BY 1"})

    list(orch.chat_stream(tid, "open pipeline by stage"))
    ask("Pipeline")
    assert "same result" in ask("Pipeline again")
    assert sorted(p.name for p in examples.glob("*.table.json")) == ["pipeline.table.json"]

    list(orch.chat_stream(tid, "and for next quarter?"))
    assert "same result" not in ask("Next quarter")
    assert sorted(p.name for p in examples.glob("*.table.json")) == [
        "next-quarter.table.json", "pipeline.table.json"]


def test_the_same_title_asked_again_rewrites_its_own_card(tmp_path):
    rows = [["Discovery", 60]]
    turn = _turn(tmp_path, [StatementRows(["STAGE", "N"], rows, False),
                            StatementRows(["STAGE", "N"], rows, False)])

    _ask(turn, "Open pipeline")
    said = _ask(turn, "Open pipeline")

    assert _cards(tmp_path) == ["open-pipeline.table.json"]
    assert "same result" not in said
    body = json.loads((tmp_path / "examples" / "thr_a" / "open-pipeline.table.json").read_text())
    assert body["rows"] == rows
