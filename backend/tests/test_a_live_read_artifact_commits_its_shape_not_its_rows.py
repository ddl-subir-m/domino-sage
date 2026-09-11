"""ADR-0045 — a Live read's Artifact commits the shape, and the rows only by consent.

ADR-0041 asked nobody for anything, and said why in one sentence: nothing leaves Domino on the way
to the answer. That sentence is false whenever the Project's git remote is not Domino's — the
Artifact is committed and pushed, on the turn that read the rows, to a host nobody named. So the
rows in the file follow the Project's **Kept rows** answer, and everything else in it does not.

Three writers, two answers between them:

* a table read and a file head in a Dataset mount write the shape, and the rows only where the
  Project said so;
* a Dataset's file listing is unchanged in both states, because `[[path, size]]` is filenames and a
  filename is not a row.

What the ASSISTANT is handed is untouched either way. That receipt never carried rows to begin with,
and the `values` hatch is about the model's context (ADR-0041), which this decision never reaches.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sage.liveread import result, run


@dataclass
class FakeRows:
    columns: list
    rows: list


def turn_for(tmp_path: Path, **kw) -> run.Turn:
    seen = object()
    base = {
        "thread_id": "thr_a",
        "examples_dir": tmp_path / "examples" / "thr_a",
        "bound": {"datasource": ("DWH",)},
        "chips": {"dataset": ("gong-exports",)},
        "source_for": lambda n: seen if n == "DWH" else None,
        "sample_rows": lambda s, db, sc, t, lim: FakeRows(
            ["ID", "EMAIL"], [[i, f"person{i}@acme.com"] for i in range(lim)]),
        "binding_for": {("datasource", "DWH"): "bnd_1"},
    }
    base.update(kw)
    return run.Turn(**base)


def _artifact(tmp_path: Path, name: str) -> dict:
    return json.loads((tmp_path / "examples" / "thr_a" / name).read_text())


def _bytes(tmp_path: Path, name: str) -> bytes:
    return (tmp_path / "examples" / "thr_a" / name).read_bytes()


# ---- a table read -------------------------------------------------------------------------------


def test_a_table_read_commits_its_shape_and_none_of_its_values(tmp_path: Path):
    """Columns, a count, the cap, whether the read stopped short, and when it was read. The card
    that renders this says what the table holds; the repo carries nothing anybody could read a
    person's address out of."""
    run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 2},
                turn_for(tmp_path))

    card = _artifact(tmp_path, "gong-calls.table.json")
    assert card["keptRows"] is False
    assert card["columns"] == ["ID", "EMAIL"]
    assert card["rowCount"] == 2
    assert card["cap"] == 2
    assert card["truncated"] is True
    assert card["readAt"]
    assert card["rows"] == [], "the key stays, so one receipt shape exists rather than two"
    assert b"acme.com" not in _bytes(tmp_path, "gong-calls.table.json")


def test_a_table_read_writes_what_it_always_wrote_where_the_project_kept_rows(tmp_path: Path):
    """With the opt-in, byte-for-byte what shipped: `{title, columns, rows}` with positional rows,
    which is the one shape the Workbench reads."""
    run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 2},
                turn_for(tmp_path, keep_rows=True))

    card = _artifact(tmp_path, "gong-calls.table.json")
    assert sorted(card) == ["columns", "rows", "title"]
    assert card["rows"] == [[0, "person0@acme.com"], [1, "person1@acme.com"]]


# ---- a file head in a Dataset mount -------------------------------------------------------------


def _mount(tmp_path: Path) -> Path:
    root = tmp_path / "mnt" / "gong"
    root.mkdir(parents=True)
    (root / "calls.csv").write_text("ID,EMAIL\n1,person1@acme.com\n2,person2@acme.com\n")
    return root


def test_a_file_head_commits_its_shape_and_none_of_its_values(tmp_path: Path):
    """The same rule over a second writer. Deciding by the kind of source would need the audience
    fact ADR-0045 was shown Sage cannot obtain, so there is one rule over every writer."""
    run.perform("live_read_files", {"dataset": "gong-exports", "path": "calls.csv"},
                turn_for(tmp_path, dataset_root=lambda n: _mount(tmp_path)))

    card = _artifact(tmp_path, "gong-exports-calls.table.json")
    assert card["keptRows"] is False
    assert card["columns"] == ["ID", "EMAIL"]
    assert card["rowCount"] == 2
    assert card["rows"] == []
    assert b"acme.com" not in _bytes(tmp_path, "gong-exports-calls.table.json")


def test_a_file_head_writes_its_rows_where_the_project_kept_rows(tmp_path: Path):
    run.perform("live_read_files", {"dataset": "gong-exports", "path": "calls.csv"},
                turn_for(tmp_path, keep_rows=True, dataset_root=lambda n: _mount(tmp_path)))

    card = _artifact(tmp_path, "gong-exports-calls.table.json")
    assert card["rows"] == [["1", "person1@acme.com"], ["2", "person2@acme.com"]]


# ---- a Dataset's file listing -------------------------------------------------------------------


def test_a_dataset_listing_is_the_same_bytes_whatever_the_project_answered(tmp_path: Path):
    """A filename is not a row. Withholding the listing would take away the one read that answers
    "what does this Dataset hold" while giving up nothing, so it is not a judgement call made per
    Project — it is the same file either way."""
    from sage.assets.provider import DatasetFile, FileListing

    listing = FileListing([DatasetFile(f"f{i}.csv", 8) for i in range(3)])

    off = tmp_path / "off"
    on = tmp_path / "on"
    for where, keep in ((off, False), (on, True)):
        run.perform("live_read_files", {"dataset": "gong-exports"},
                    turn_for(where, keep_rows=keep, list_files=lambda n: listing))

    name = "examples/thr_a/gong-exports-files.table.json"
    assert (off / name).read_bytes() == (on / name).read_bytes()
    assert [r[0] for r in json.loads((off / name).read_text())["rows"]] == [
        "f0.csv", "f1.csv", "f2.csv"]


# ---- what the assistant is handed ---------------------------------------------------------------


def test_the_receipt_carries_no_values_either_way(tmp_path: Path):
    """This decision is about what is committed. What ADR-0041 shows the model — a shape, and never
    a value it was not given — is not reopened here."""
    args = {"source": "DWH", "table": "GONG__CALLS", "limit": 2}
    off = run.perform("live_read_table", args, turn_for(tmp_path / "off"))
    on = run.perform("live_read_table", args, turn_for(tmp_path / "on", keep_rows=True))

    for said in (off, on):
        assert "Read GONG__CALLS: the first 2 rows" in said
        assert "Columns: ID, EMAIL" in said
        assert "acme.com" not in said


def test_the_assistant_is_not_sent_to_describe_a_card_that_holds_no_rows(tmp_path: Path):
    """The one sentence that had to change. "The person can see them on the card" is what lets the
    assistant decline to quote without sounding broken — and it is false where the card carries the
    shape, so an assistant told it anyway would talk about rows nobody is looking at."""
    args = {"source": "DWH", "table": "GONG__CALLS", "limit": 2}
    off = run.perform("live_read_table", args, turn_for(tmp_path / "off"))
    on = run.perform("live_read_table", args, turn_for(tmp_path / "on", keep_rows=True))

    assert "the person can see them on the card" in on
    assert "NOBODY has been shown the values" in off
    assert "does not keep data rows in its files" in off, "and it is told why, so it does not guess"
    assert "on the card" not in off


def test_a_table_the_creator_shared_still_reaches_the_model_with_kept_rows_off(tmp_path: Path):
    """The two budgets answer different questions. `values` asks whether the creator put this table
    in front of the model; **Kept rows** asks who can clone the repo. A Project that keeps no rows
    in its files has said nothing about the first."""
    said = run.perform(
        "live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 1},
        turn_for(tmp_path, shared=(("bnd_1", "GONG__CALLS"),)),
    )
    assert "the creator shared this table" in said
    assert "person0@acme.com" in said
    assert _artifact(tmp_path, "gong-calls.table.json")["rows"] == []


def test_the_model_is_told_how_many_of_the_read_rows_it_got_not_how_many_are_on_a_card(tmp_path):
    """`values` is cut to the model's budget, so the receipt names both numbers. It used to say
    "of the N on the card", which counts rows a shape-only card does not have."""
    said = run.perform(
        "live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 400},
        turn_for(tmp_path, shared=(("bnd_1", "GONG__CALLS"),),
                 sample_rows=lambda s, db, sc, t, lim: FakeRows(
                     [f"C{c}" for c in range(31)], [[f"v{c}" for c in range(31)]] * lim)),
    )
    assert "of the 400 that were read" in said


def test_neither_cap_moved(tmp_path: Path):
    """ADR-0045 rejected lowering either. The card's cap governs only a Project that opted in and
    what Read again holds in memory; the model's budget is about a context blowout, which this
    decision never reaches."""
    assert result.CAP_ROWS == 500
    assert result.VALUES_BUDGET_CHARS == 8000


# ---- the statement beside it --------------------------------------------------------------------


def test_the_statement_sibling_is_committed_in_both_states(tmp_path: Path):
    """It carries no values: `sample_rows` takes a table and a limit and cannot filter. The shape
    file points at it so the card can say what was run without the `.sql` having to be found."""
    kept = result.record(tmp_path / "e" / "thr_a", "s", "t", ["N"], [[1]],
                         statement="SELECT * FROM T LIMIT 1")
    shape = result.record(tmp_path / "e" / "thr_a", "s2", "t", ["N"], [[1]],
                          statement="SELECT * FROM T LIMIT 1", keep_rows=False)

    assert (tmp_path / "e" / "thr_a" / "s.sql").read_text() == "SELECT * FROM T LIMIT 1\n"
    assert (tmp_path / "e" / "thr_a" / "s2.sql").read_text() == "SELECT * FROM T LIMIT 1\n"
    assert kept.statement == "examples/thr_a/s.sql"
    assert shape.statement == "examples/thr_a/s2.sql"
    assert json.loads((tmp_path / "e" / "thr_a" / "s2.table.json").read_text())["statement"] == (
        "examples/thr_a/s2.sql")


def test_a_writer_that_says_nothing_keeps_no_rows(tmp_path: Path):
    """The default is the safe one, structurally rather than by documentation: a writer added later
    that forgets to ask commits a shape, not somebody's address."""
    result.record(tmp_path / "e" / "thr_a", "s", "t", ["EMAIL"], [["person@acme.com"]])
    assert json.loads((tmp_path / "e" / "thr_a" / "s.table.json").read_text())["rows"] == []


def test_the_file_this_writes_is_one_the_chat_pass_leaves_alone(tmp_path: Path):
    """One contract, held from both ends. `withhold_table_rows` runs over every `.table.json` a
    Chat turn left behind, including one a Live read wrote — so if the two disagreed about what a
    receipt keeps, the pass would quietly shrink this file and drop the halves of ADR-0029's rule
    on the way past. Idempotence is what makes them one shape rather than two that look alike."""
    from sage.workspace import table_shape

    result.record(tmp_path / "e" / "thr_a", "s", "t", ["ID"], [[1], [2], [3]],
                  cap=2, statement="SELECT * FROM T LIMIT 2")
    written = json.loads((tmp_path / "e" / "thr_a" / "s.table.json").read_text())

    assert table_shape.shape_only(written, read_at="2026-01-01T00:00:00Z") == written
    assert written["truncated"] is True and written["cap"] == 2
    assert written["statement"] == "examples/thr_a/s.sql"


# ---- the Project's answer reaches the read ------------------------------------------------------


def test_the_read_is_told_what_this_project_answered(tmp_path: Path):
    """Read fresh off the record rather than captured when the token was minted, like every other
    grant on the turn: a creator can answer this while a turn runs."""
    from .test_chat_turn import _orch

    orch, _oc = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    token = orch._mint_live_read_token(thread)

    assert orch._live_read_turn(token).keep_rows is False
    orch.set_kept_rows(True)
    assert orch._live_read_turn(token).keep_rows is True
