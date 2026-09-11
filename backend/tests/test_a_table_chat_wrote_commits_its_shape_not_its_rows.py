"""A Chat turn answered a question about an attached CSV with five real rows — an email, a card
number and an SSN — written to `examples/<threadId>/sample-5-rows.table.json`, committed, and
symlinked into every Built App by `_ensure_examples_link` (#251). Under ADR-0045 that file commits
its shape and keeps its rows only where the Project said so.

ADR-0045 first said the shim strips the array "before the write lands". It cannot: `shim/chat_paths`
is pure, and it acts on the tool calls of the NEXT request, by which time the file is on disk. The
seam that does fix up a turn's writes is `revert_denied_writes`, at turn end, over a before/after
snapshot — so the strip is its sibling and lives beside it.

The recovery ladder in `store.js` is the reason `columns_and_count` is not one `len(body["rows"])`:
a turn writes `.table.json` in whichever shape its pandas reached for, the card already reads them
all, and a count taken from only the documented shape would have said "0 rows" over every one of
them.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sage.workspace.table_shape import columns_and_count, shape_only
from sage.workspace.threads import snapshot_files, withhold_table_rows

ROWS = [["ada@example.com", "4111111111111111", "078-05-1120"],
        ["grace@example.com", "4012888888881881", "219-09-9999"]]
COLUMNS = ["email", "card_number", "ssn"]
RECORDS = [dict(zip(COLUMNS, row)) for row in ROWS]


def _write(root: Path, thread_id: str, name: str, body: object) -> Path:
    path = root / "examples" / thread_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2))
    return path


# Every near-contract shape the card already tolerates, and what each one is a receipt for. The
# ticket asks for these by name: a file holding its rows under `data`, or as pandas' own transpose,
# is as much a copy of the rows as the documented one.
SHAPES = {
    "the contract": ({"title": "Sample rows", "columns": COLUMNS, "rows": ROWS}, COLUMNS, 2),
    "record rows in the wrapper": ({"title": "Sample rows", "rows": RECORDS}, COLUMNS, 2),
    "no wrapper at all": (RECORDS, COLUMNS, 2),
    "rows under data": ({"title": "Sample rows", "data": RECORDS}, COLUMNS, 2),
    "rows under records": ({"title": "Sample rows", "records": RECORDS}, COLUMNS, 2),
    "orient=split": ({"columns": COLUMNS, "index": [0, 1], "data": ROWS}, COLUMNS, 2),
    "orient=table": ({"schema": {"fields": [{"name": "index", "type": "integer"},
                                            *({"name": c, "type": "string"} for c in COLUMNS)]},
                      "data": RECORDS}, COLUMNS, 2),
    "orient=columns": ({c: {str(i): row[n] for i, row in enumerate(ROWS)}
                        for n, c in enumerate(COLUMNS)}, COLUMNS, 2),
    "orient=index": ({str(i): dict(zip(COLUMNS, row)) for i, row in enumerate(ROWS)}, COLUMNS, 2),
    "a dump under the wrapper": ({"title": "Sample rows",
                                  "data": {c: {str(i): row[n] for i, row in enumerate(ROWS)}
                                           for n, c in enumerate(COLUMNS)}}, COLUMNS, 2),
    "columns that are not plain names": (
        {"columns": [{"name": c, "type": "string"} for c in COLUMNS], "rows": ROWS}, COLUMNS, 2),
}


@pytest.mark.parametrize("shape", list(SHAPES), ids=list(SHAPES))
def test_every_shape_the_card_reads_is_counted_rather_than_missed(shape: str):
    body, columns, rows = SHAPES[shape]
    assert columns_and_count(body) == (columns, rows)


def test_the_receipt_carries_the_title_the_columns_and_the_count():
    body, _, _ = SHAPES["the contract"]
    kept = shape_only(body, read_at="2026-09-10T18:04:00+00:00")
    assert kept == {"title": "Sample rows", "columns": COLUMNS, "rows": [], "rowCount": 2,
                    "readAt": "2026-09-10T18:04:00+00:00", "keptRows": False}


def test_a_second_pass_does_not_forget_what_the_first_one_counted():
    """A receipt is a `.table.json` like any other, and the file is rewritten whenever a later turn
    touches it. Counting the rows of an already-emptied table gives zero — which is this module's
    own worst sentence, reached from the other side — and re-dating it moves the read to the moment
    of the rewrite."""
    once = shape_only(SHAPES["the contract"][0], read_at="2026-09-10T18:04:00+00:00")
    twice = shape_only(once, read_at="2026-11-02T09:00:00+00:00")
    assert twice == once


def test_a_labelled_index_is_not_read_as_a_list_of_column_names(tmp_path: Path):
    """`df.set_index("email").to_dict("index")` puts a VALUE at the top level of every entry. Read
    as a columns-orient dump — which is what the card guesses, and what this did — those emails are
    written into `columns`, inside the file whose whole claim is that it holds no data.

    The card can afford that guess and this cannot. It is painting a file that still has its rows,
    so a wrong guess is an unreadable header for one render; here the guess is committed and pushed.
    Neither key set looks like an index, the two readings are transposes, and the count would be a
    number of columns reported as a number of rows — so nothing is claimed and the file is offered.
    """
    tid = "thr_01abc"
    labelled = {row[0]: {"card_number": row[1], "ssn": row[2]} for row in ROWS}
    path = _write(tmp_path, tid, "by-customer.table.json", labelled)

    assert columns_and_count(labelled) == ([], 0)
    withhold_table_rows(tmp_path, tid, before={}, kept_rows=False)

    kept = json.loads(path.read_text())
    assert kept["columns"] == []
    assert "rowCount" not in kept
    for row in ROWS:
        for value in row:
            assert value not in path.read_text()


def test_a_body_that_still_has_rows_is_counted_rather_than_believed():
    """`keptRows` is a field a model can write, and a turn copying an earlier receipt's shape
    writes one over real rows. Trusting the marker alone carried a stale count and a stale date onto
    a table this turn had just filled — "0 rows, read December 31, 2019" over three of them."""
    copied = shape_only({"title": "Sales", "columns": ["email"],
                         "rows": [["a@b.com"], ["c@d.com"], ["e@f.com"]],
                         "rowCount": 0, "readAt": "2020-01-01T00:00:00+00:00",
                         "keptRows": False}, read_at="2026-09-11T09:00:00+00:00")

    assert copied["rowCount"] == 3
    assert copied["readAt"] == "2026-09-11T09:00:00+00:00"


def test_a_count_that_is_not_a_number_is_not_carried():
    """`isinstance(True, int)` holds, and the card reads a boolean count as no count at all — which
    sends it to offer a file that no longer has the rows the offer implies."""
    assert "rowCount" not in shape_only({"rows": [], "rowCount": True, "keptRows": False},
                                        read_at="2026-09-10")
    assert shape_only({"rows": [], "readAt": 1710000000, "keptRows": False},
                      read_at="2026-09-10")["readAt"] == "2026-09-10"


def test_a_file_a_writer_already_emptied_comes_back_byte_for_byte():
    """One contract, two authors. A Live read writes this shape itself (#254) and carries three
    keys a Chat table never has — ADR-0045's table names `cap`, `truncated` and the statement among
    what a Live read Artifact commits. A pass that dropped them would shrink a correct file."""
    written = {"title": "Sample rows", "columns": COLUMNS, "rows": [], "rowCount": 500,
               "readAt": "2026-09-10T18:04:00+00:00", "keptRows": False,
               "cap": 500, "truncated": True, "statement": "examples/thr_x/sample.sql"}

    assert shape_only(written, read_at="2026-11-02T09:00:00+00:00") == written


def test_an_optional_key_is_not_a_way_back_in_for_values():
    """Each carried key is checked for the type it is owed. Otherwise `statement` is a string field
    the rule allows, which is exactly the shape a row would come back as."""
    smuggled = shape_only({"columns": COLUMNS, "rows": ROWS,
                           "statement": f"SELECT * WHERE email = '{ROWS[0][0]}'",
                           "cap": {"rows": ROWS}, "truncated": ROWS}, read_at="2026-09-10")

    assert "statement" not in smuggled
    assert "cap" not in smuggled
    assert "truncated" not in smuggled
    assert ROWS[0][0] not in json.dumps(smuggled)


def test_a_shape_nothing_was_recovered_from_claims_no_count():
    """Zero is a claim — "the frame was empty" — and a wrapper whose rows are under a key nobody
    has seen has not earned it. The card reads the absence and offers the file."""
    unknown = shape_only({"title": "Sample rows", "sheets": {}}, read_at="2026-09-10")
    assert "rowCount" not in unknown
    # A frame that really was empty still says so: it named its columns, so zero is a fact.
    empty = shape_only({"columns": COLUMNS, "rows": []}, read_at="2026-09-10")
    assert empty["rowCount"] == 0


def test_a_wrapper_with_no_title_is_not_given_one():
    """The card falls back to the manifest's filename-derived title, and has since before this.
    Inventing one here would put a second name on the same Artifact."""
    assert "title" not in shape_only({"columns": COLUMNS, "rows": ROWS}, read_at="2026-09-10")


@pytest.mark.parametrize("shape", list(SHAPES), ids=list(SHAPES))
def test_no_value_from_any_shape_survives_into_the_file(tmp_path: Path, shape: str):
    """The claim the whole ticket rests on, made against the bytes rather than against a dict: no
    email, no card number, no SSN anywhere in what reaches git."""
    body, _, _ = SHAPES[shape]
    tid = "thr_01abc"
    path = _write(tmp_path, tid, "sample-5-rows.table.json", body)

    withhold_table_rows(tmp_path, tid, before={}, kept_rows=False)

    text = path.read_text()
    for row in ROWS:
        for value in row:
            assert value not in text
    assert json.loads(text)["rowCount"] == 2


def test_the_pass_names_what_it_rewrote(tmp_path: Path):
    tid = "thr_01abc"
    _write(tmp_path, tid, "sample-5-rows.table.json", SHAPES["the contract"][0])

    assert withhold_table_rows(tmp_path, tid, before={}, kept_rows=False) == [
        f"examples/{tid}/sample-5-rows.table.json"]


def test_with_kept_rows_on_the_file_is_left_as_the_agent_composed_it(tmp_path: Path):
    tid = "thr_01abc"
    body = SHAPES["the contract"][0]
    path = _write(tmp_path, tid, "sample-5-rows.table.json", body)

    assert withhold_table_rows(tmp_path, tid, before={}, kept_rows=True) == []
    assert json.loads(path.read_text()) == body


def test_a_file_that_is_not_a_table_is_untouched(tmp_path: Path):
    """Only `.table.json` carries rows by construction. A note, a query and a chart are each their
    own decision, and two of them are other tickets."""
    tid = "thr_01abc"
    root = tmp_path / "examples" / tid
    root.mkdir(parents=True)
    (root / "notes.md").write_text("The card numbers look like test data.")
    (root / "sample.sql").write_text("SELECT * FROM transactions LIMIT 5\n")
    (root / "chart.png").write_bytes(b"\x89PNG not really")
    (root / "manifest.json").write_text(json.dumps({"rows": ROWS}))

    withhold_table_rows(tmp_path, tid, before={}, kept_rows=False)

    assert (root / "notes.md").read_text() == "The card numbers look like test data."
    assert (root / "sample.sql").read_text().startswith("SELECT")
    assert (root / "chart.png").read_bytes() == b"\x89PNG not really"
    assert json.loads((root / "manifest.json").read_text()) == {"rows": ROWS}


def test_another_threads_artifacts_are_not_rewritten(tmp_path: Path):
    """The pass runs at the end of one turn, and a Project has many Threads. Walking `examples/`
    whole would rewrite files under a conversation nobody was in."""
    body = SHAPES["the contract"][0]
    mine = _write(tmp_path, "thr_mine", "sample.table.json", body)
    theirs = _write(tmp_path, "thr_theirs", "sample.table.json", body)

    withhold_table_rows(tmp_path, "thr_mine", before={}, kept_rows=False)

    assert json.loads(mine.read_text())["rows"] == []
    assert json.loads(theirs.read_text())["rows"] == ROWS


def test_a_table_this_turn_did_not_write_is_left_alone(tmp_path: Path):
    """`before` is the same snapshot `revert_denied_writes` reads, and it means the same thing here:
    this turn's writes. A file from an earlier turn was already decided, under whatever answer the
    Project gave then, and re-deciding it now would rewrite history nobody asked about."""
    tid = "thr_01abc"
    path = _write(tmp_path, tid, "sample.table.json", SHAPES["the contract"][0])
    before = snapshot_files(tmp_path)

    assert withhold_table_rows(tmp_path, tid, before=before, kept_rows=False) == []
    assert json.loads(path.read_text())["rows"] == ROWS


def test_a_table_json_that_is_not_json_keeps_no_rows_either(tmp_path: Path):
    """Whatever this file is, Sage cannot read it and so cannot promise it holds no values. The
    safe answer is the one the ADR gives for an unknown destination: keep the shape it can name —
    none — rather than push bytes nobody has read."""
    tid = "thr_01abc"
    path = tmp_path / "examples" / tid / "sample.table.json"
    path.parent.mkdir(parents=True)
    path.write_text("ada@example.com,4111111111111111\n")

    withhold_table_rows(tmp_path, tid, before={}, kept_rows=False)

    kept = json.loads(path.read_text())
    assert kept == {"columns": [], "rows": [], "readAt": kept["readAt"], "keptRows": False}


def _turn_that_writes_a_table(tmp_path: Path, *, kept_rows: bool) -> Path:
    """One Chat turn, end to end, that answers with a table. Returns the file it left behind —
    which is the file the save commits, since the commit takes the tree as it stands."""
    from sage.orchestrator.service import Orchestrator

    from .fake_opencode import FakeOpenCode, Turn
    from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, [])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    project = orch.project(start_preview=False)
    project.record.set_kept_rows(kept_rows)
    tid = orch.create_thread()["id"]
    oc.turns = [Turn(text="Five rows from the file.",
                     writes={f"examples/{tid}/sample-5-rows.table.json":
                             json.dumps({"title": "Sample rows from card_panel_transactions_RAW.csv",
                                         "columns": COLUMNS, "rows": ROWS})})]
    list(orch.chat_stream(tid, "show me five rows"))
    return project.record.path / "examples" / tid / "sample-5-rows.table.json"


def test_a_turn_that_answers_with_a_table_leaves_no_rows_behind(tmp_path: Path):
    """#251, the whole of it: this is the file that was written, committed, and symlinked into
    every Built App by `_ensure_examples_link` — with an email, a card number and an SSN in it."""
    kept = json.loads(_turn_that_writes_a_table(tmp_path, kept_rows=False).read_text())

    assert kept["rows"] == []
    assert kept["rowCount"] == 2
    assert kept["columns"] == COLUMNS
    assert kept["title"] == "Sample rows from card_panel_transactions_RAW.csv"


def test_a_project_that_keeps_rows_gets_the_file_the_agent_composed(tmp_path: Path):
    kept = json.loads(_turn_that_writes_a_table(tmp_path, kept_rows=True).read_text())

    assert kept["rows"] == ROWS
    assert "keptRows" not in kept


def test_the_read_date_is_when_the_turn_wrote_the_file(tmp_path: Path):
    """Not when the pass ran. The two are seconds apart today and would not be if a turn ran long,
    and the card's sentence is about the data's age rather than about Sage's bookkeeping."""
    tid = "thr_01abc"
    path = _write(tmp_path, tid, "sample.table.json", SHAPES["the contract"][0])
    written = datetime(2026, 9, 10, 18, 4, tzinfo=UTC).timestamp()
    os.utime(path, (written, written))

    withhold_table_rows(tmp_path, tid, before={}, kept_rows=False)

    assert json.loads(path.read_text())["readAt"] == "2026-09-10T18:04:00+00:00"
