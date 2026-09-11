"""ADR-0041 — performing the read. The seam these hold is the one the whole decision rests on.

"Show me 1 sample conversation" must produce a real row on the person's screen while the assistant
is told only the shape of it. If the assistant is handed the rows, they enter Recall and leave
Domino on the next request; if the assistant is told nothing, it invents a row instead. So it is
told, in words, that it has not been shown them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sage.liveread import run


@dataclass
class FakeRows:
    columns: list
    rows: list


def turn_for(tmp_path, **kw):
    seen = object()
    base = {
        "thread_id": "thr_a",
        "examples_dir": tmp_path / "examples" / "thr_a",
        "bound": {"datasource": ("DWH",)},
        "chips": {"dataset": ("gong-exports",)},
        "source_for": lambda n: seen if n == "DWH" else None,
        "sample_rows": lambda s, db, sc, t, lim: FakeRows(
            ["ID", "TITLE"], [[i, f"call {i}"] for i in range(lim)]),
        "binding_for": {("datasource", "DWH"): "bnd_1"},
    }
    base.update(kw)
    return run.Turn(**base)


def test_the_person_gets_the_row_and_the_assistant_gets_its_shape(tmp_path):
    # `keep_rows`, because the claim here is the split between the two audiences. What the
    # Artifact keeps of the row is ADR-0045's question and is held next door.
    said = run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 1},
                       turn_for(tmp_path, keep_rows=True))

    assert "Columns: ID, TITLE" in said
    assert "NOT been shown the values" in said
    assert "call 0" not in said, "the row must not reach the assistant"

    card = json.loads((tmp_path / "examples" / "thr_a" / "gong-calls.table.json").read_text())
    assert card["rows"] == [[0, "call 0"]], "and it must reach the person"


def test_the_assistant_is_given_the_rows_where_the_creator_already_shared_that_table(tmp_path):
    said = run.perform(
        "live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 1},
        turn_for(tmp_path, shared=(("bnd_1", "GONG__CALLS"),)),
    )
    assert "the creator shared this table" in said
    assert "call 0" in said


def test_a_statement_that_came_back_full_says_there_is_more_behind_it(tmp_path):
    # Unlike a walk, a LIMIT that filled knows only that it stopped (ADR-0029's rule inverted).
    said = run.perform("live_read_table", {"source": "DWH", "table": "T", "limit": 5}, turn_for(tmp_path))
    assert "there are more" in said


def test_a_data_source_this_conversation_never_named_is_refused_before_anything_runs(tmp_path):
    def explode(*a, **k):
        raise AssertionError("the store must not be touched for a source out of range")

    said = run.perform("live_read_table", {"source": "Other-Warehouse", "table": "T"},
                       turn_for(tmp_path, sample_rows=explode))

    assert "Other-Warehouse" in said and "Use it in this conversation" in said
    assert not (tmp_path / "examples").exists(), "and nothing is written"


def test_a_source_in_range_that_the_platform_cannot_open_says_so_without_naming_machinery(tmp_path):
    said = run.perform("live_read_table", {"source": "DWH", "table": "T"},
                       turn_for(tmp_path, source_for=lambda n: None))
    assert "could not find DWH" in said
    for mechanism in ("tool", "blocked", "read-only", "provider"):
        assert mechanism not in said.lower()


def test_a_dataset_listing_becomes_a_card_and_a_truncated_one_says_so(tmp_path):
    from sage.assets.provider import DatasetFile, FileListing

    listing = FileListing([DatasetFile(f"f{i}.csv", 8) for i in range(3)])
    said = run.perform("live_read_files", {"dataset": "gong-exports"},
                       turn_for(tmp_path, list_files=lambda n: listing))

    assert "Columns: File, Bytes" in said
    card = json.loads((tmp_path / "examples" / "thr_a" / "gong-exports-files.table.json").read_text())
    assert [r[0] for r in card["rows"]] == ["f0.csv", "f1.csv", "f2.csv"]


def test_a_csv_below_a_dataset_is_read_as_rows_and_columns(tmp_path):
    root = tmp_path / "mnt" / "gong"
    root.mkdir(parents=True)
    (root / "calls.csv").write_text("ID,TITLE\n1,Acme\n2,Globex\n")

    said = run.perform("live_read_files", {"dataset": "gong-exports", "path": "calls.csv"},
                       turn_for(tmp_path, keep_rows=True, dataset_root=lambda n: root))

    assert "Columns: ID, TITLE" in said
    assert "Acme" not in said, "a file's contents are data too"
    card = json.loads((tmp_path / "examples" / "thr_a" / "gong-exports-calls.table.json").read_text())
    assert card["rows"] == [["1", "Acme"], ["2", "Globex"]]


def test_a_file_that_is_not_rows_and_columns_is_not_forced_into_a_table(tmp_path):
    root = tmp_path / "mnt" / "gong"
    root.mkdir(parents=True)
    (root / "notes.txt").write_text("just one line")

    said = run.perform("live_read_files", {"dataset": "gong-exports", "path": "notes.txt"},
                       turn_for(tmp_path, dataset_root=lambda n: root))
    assert "not laid out as rows and columns" in said


def test_a_path_climbing_out_of_the_mount_reads_as_a_file_that_is_not_there(tmp_path):
    root = tmp_path / "mnt" / "gong"
    root.mkdir(parents=True)
    (tmp_path / "mnt" / "secret.csv").write_text("a,b\n1,2\n")

    said = run.perform("live_read_files", {"dataset": "gong-exports", "path": "../secret.csv"},
                       turn_for(tmp_path, dataset_root=lambda n: root))
    assert "no file at ../secret.csv" in said


def test_a_dataset_out_of_range_is_refused_before_it_is_touched(tmp_path):
    said = run.perform("live_read_files", {"dataset": "payroll"},
                       turn_for(tmp_path, list_files=lambda n: (_ for _ in ()).throw(AssertionError("touched"))))
    assert "payroll" in said and "Use it in this conversation" in said


def test_an_unmounted_dataset_still_lists_and_says_why_a_file_cannot_be_read(tmp_path):
    # A Dataset shared from another project is never mounted here, and listing it is the whole
    # answer to "what does it hold". Refusing the listing too would be refusing the common case.
    from sage.assets.provider import DatasetFile, FileListing

    turn = turn_for(tmp_path, list_files=lambda n: FileListing([DatasetFile("a.csv", 4)]),
                    dataset_root=lambda n: None)
    assert "Columns: File, Bytes" in run.perform("live_read_files", {"dataset": "gong-exports"}, turn)

    said = run.perform("live_read_files", {"dataset": "gong-exports", "path": "a.csv"}, turn)
    assert "not mounted in this workspace" in said
    assert "listing" in said


def test_the_two_tools_are_the_only_two(tmp_path):
    try:
        run.perform("live_read_anything", {}, turn_for(tmp_path))
    except ValueError as e:
        assert "No tool named" in str(e)
    else:
        raise AssertionError("an undefined tool must not quietly do nothing")


def test_a_dataset_name_does_not_authorise_a_data_source_of_the_same_name(tmp_path):
    # Names live in separate namespaces. A flat set of them would have let a Dataset chip open a
    # store nobody put in front of this conversation.
    turn = turn_for(tmp_path, bound={}, chips={"dataset": ("DWH",)},
                    sample_rows=lambda *a: (_ for _ in ()).throw(AssertionError("read")))
    said = run.perform("live_read_table", {"source": "DWH", "table": "T"}, turn)
    assert "Use it in this conversation" in said


def test_the_assistant_is_told_when_it_saw_fewer_rows_than_were_read(tmp_path):
    # Otherwise it reasons about "the data" from a slice it thinks is all of it. Counted against the
    # read rather than against the card, which holds them only where the Project does (ADR-0045).
    wide = [[f"v{i}-{c}" for c in range(31)] for i in range(400)]
    said = run.perform(
        "live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 400},
        turn_for(tmp_path, shared=(("bnd_1", "GONG__CALLS"),),
                 sample_rows=lambda s, db, sc, t, lim: FakeRows([f"C{c}" for c in range(31)], wide)),
    )
    assert "of the 400 that were read" in said
