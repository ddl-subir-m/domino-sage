"""A Chat turn publishes one table per distinct result, and none for an empty one (#726).

Measured on haiku, demo prompts 1 and 4: three tables in one turn, one of them empty, and the same
table twice in another. A table the model writes itself never passes through the Live read writer,
so publication is where the turn's tables are all in one place: an empty `{"columns": [...],
"rows": []}` was valid and drew a blank card, and two files holding the same rows drew two cards.
"""
from __future__ import annotations

import json

import pytest

from .fake_opencode import Turn
from .test_chat_tables_are_validated_before_publication import setup_turn

ROWS = [["novartis", -14.3], ["acme", -6.1]]


def _table(title: str, columns: list[str], rows: list[list]) -> str:
    return json.dumps({"title": title, "columns": columns, "rows": rows})


def _published(orch, tid, events) -> list[str]:
    shown = [a["path"] for e in events if e["type"] == "artifacts" for a in e["items"]]
    assert set(shown) <= {a["path"] for a in orch.get_thread(tid)["artifacts"]}
    return sorted(shown)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


@pytest.mark.parametrize("kept_rows", [True, False])
def test_the_same_table_written_twice_is_published_once(tmp_path, kept_rows):
    orch, oc, tid, root, _ = setup_turn(tmp_path, kept_rows=kept_rows)
    first, second = f"examples/{tid}/usage-drops.table.json", f"examples/{tid}/largest-drops.table.json"
    chart = f"examples/{tid}/adoption.png"
    oc.turns = [Turn(text="Novartis dropped most.", writes={
        first: _table("Usage drops", ["ACCOUNT", "CHANGE"], ROWS),
        chart: "chart",
        second: _table("Largest accounts dropping", ["account", "change"], ROWS)})]

    events = list(orch.chat_stream(tid, "which of our largest accounts have usage dropping?"))

    published = _published(orch, tid, events)
    assert chart in published
    assert len([p for p in published if p.endswith(".table.json")]) == 1
    dropped = second if first in published else first
    assert not (root / dropped).exists()
    assert next(e for e in events if e["type"] == "done")["ok"] is True


@pytest.mark.parametrize("body", [
    {"title": "Pipeline FY2028 Q2", "columns": ["STAGE", "N"], "rows": []},
    {"columns": [], "rows": []},
    [],
])
def test_an_empty_table_is_not_published(tmp_path, body):
    orch, oc, tid, root, table = setup_turn(tmp_path)
    chart = f"examples/{tid}/pipeline.png"
    oc.turns = [Turn(text="Here is the pipeline.", writes={chart: "chart", table: json.dumps(body)})]

    events = list(orch.chat_stream(tid, "chart open pipeline this quarter"))

    assert _published(orch, tid, events) == [chart]
    assert not (root / table).exists()


def test_two_tables_that_differ_are_both_published(tmp_path):
    """The sibling: the same columns with one value changed, and the same values under other
    columns, are two results."""
    orch, oc, tid, _, _ = setup_turn(tmp_path)
    a, b, c = (f"examples/{tid}/{n}.table.json" for n in ("this-quarter", "next-quarter", "teams"))
    oc.turns = [Turn(text="Two quarters.", writes={
        a: _table("This quarter", ["STAGE", "N"], [["Discovery", 60]]),
        b: _table("Next quarter", ["STAGE", "N"], [["Discovery", 61]]),
        c: _table("Teams", ["TEAM", "N"], [["Discovery", 60]])})]

    events = list(orch.chat_stream(tid, "pipeline this quarter and next"))

    assert _published(orch, tid, events) == sorted([a, b, c])


def test_a_table_an_earlier_turn_wrote_is_not_this_turns_duplicate(tmp_path):
    """Only what this turn wrote is compared. The same numbers asked for again on a later turn are
    that turn's answer, and the earlier card is left exactly as it was."""
    orch, oc, tid, root, _ = setup_turn(tmp_path)
    old, new = f"examples/{tid}/drops.table.json", f"examples/{tid}/drops-again.table.json"
    oc.turns = [Turn(text="Drops.", writes={old: _table("Drops", ["A", "C"], ROWS)}),
                Turn(text="Drops again.", writes={new: _table("Drops again", ["A", "C"], ROWS)})]

    list(orch.chat_stream(tid, "which accounts dropped?"))
    events = list(orch.chat_stream(tid, "show that again"))

    assert _published(orch, tid, events) == [new]
    assert (root / old).exists()
