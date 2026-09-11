"""Pressing **Read again** reads now, as the viewer, and writes nothing (#256, ADR-0045).

The ticket's point, and the reason this is not a cheap re-render: if the button wrote into the
`.table.json`, one press would defeat a Project's **Kept rows** answer permanently and the next save
would commit somebody's address. So the rows exist in the response and nowhere else, and "no rows
touched disk" is a sentence a test can hold.

The grant is the agent's grant. `read_again` goes through `grant.reachable` and the same store
handle the agent's own read goes through, so a viewer whose access went away is refused on the line
the agent would have been refused on — the elevation ADR-0045 names is closed structurally rather
than by a rule written twice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

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
        "bound": {"datasource": ("DWH",), "dataset": ("gong-exports",)},
        "source_for": lambda n: seen if n == "DWH" else None,
        "sample_rows": lambda s, db, sc, t, lim: FakeRows(
            ["ID", "EMAIL"], [[i, f"person{i}@acme.com", db, sc] for i in range(lim)]),
        "binding_for": {("datasource", "DWH"): "bnd_1", ("dataset", "gong-exports"): "bnd_2"},
    }
    base.update(kw)
    return run.Turn(**base)


def _artifact(tmp_path: Path, name: str) -> dict:
    return json.loads((tmp_path / "examples" / "thr_a" / name).read_text())


def _everything_on_disk(tmp_path: Path) -> bytes:
    return b"".join(sorted(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()))


# ---- what the card records ----------------------------------------------------------------------


def test_a_table_read_writes_down_which_read_made_it(tmp_path: Path):
    run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 2},
                turn_for(tmp_path, scope_for={("DWH", ""): ("DWH", "MARTS")}))

    card = _artifact(tmp_path, "gong-calls.table.json")
    assert card["source"] == {"kind": "table", "binding": "bnd_1", "limit": 2,
                              "table": "GONG__CALLS", "database": "DWH", "schema": "MARTS"}


def test_a_level_is_recorded_as_a_level_and_never_folded_into_the_name(tmp_path: Path):
    """The one that would put a DIFFERENT table's rows on the card.

    A dotted name carries both levels or neither, and neither sends the press back down the ladder
    to the Binding's recorded position. So a model that reached into `MARTS` in a store whose
    Binding records `SALES` read `MARTS.GONG__CALLS`, and the press would have read
    `SALES.GONG__CALLS` — a different table, under this card's title, called today's rows.
    """
    turn = turn_for(tmp_path, scope_for={("DWH", ""): ("", "SALES")})
    run.perform("live_read_table",
                {"source": "DWH", "schema": "MARTS", "table": "GONG__CALLS", "limit": 1}, turn)
    source = _artifact(tmp_path, "gong-calls.table.json")["source"]

    assert source["schema"] == "MARTS"
    assert "database" not in source, "it had none, and inventing one is the same bug"
    assert run.read_again(source, turn).rows[0][3] == "MARTS"


def test_a_file_head_records_the_dataset_binding_and_the_path(tmp_path: Path):
    mount = tmp_path / "mnt"
    mount.mkdir()
    (mount / "calls.csv").write_text("id,email\n1,a@b.com\n")

    run.perform("live_read_files", {"dataset": "gong-exports", "path": "calls.csv"},
                turn_for(tmp_path, dataset_root=lambda n: mount))

    card = _artifact(tmp_path, "gong-exports-calls.table.json")
    assert card["source"] == {"kind": "file", "binding": "bnd_2", "limit": result.CAP_ROWS,
                              "path": "calls.csv"}


def test_a_read_through_a_chip_alone_records_no_source(tmp_path: Path):
    """#258: a card with no Binding gets no button. A store this Conversation is looking at is not
    something the Project holds, so there is nothing a different viewer could re-read it through."""
    run.perform("live_read_table", {"source": "DWH", "table": "T", "limit": 1},
                turn_for(tmp_path, bound={}, chips={"datasource": ("DWH",)}, binding_for={}))

    assert "source" not in _artifact(tmp_path, "t.table.json")


def test_the_kept_rows_card_records_no_source_because_it_has_its_rows(tmp_path: Path):
    run.perform("live_read_table", {"source": "DWH", "table": "T", "limit": 1},
                turn_for(tmp_path, keep_rows=True))

    assert sorted(_artifact(tmp_path, "t.table.json")) == ["columns", "rows", "title"]


# ---- pressing the button ------------------------------------------------------------------------


def test_read_again_returns_todays_rows(tmp_path: Path):
    turn = turn_for(tmp_path, scope_for={("DWH", ""): ("DWH", "MARTS")})
    run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 2}, turn)
    source = _artifact(tmp_path, "gong-calls.table.json")["source"]

    read = run.read_again(source, turn)

    assert read.refused == ""
    assert read.columns == ["ID", "EMAIL"]
    assert len(read.rows) == 2
    # The levels the card wrote down, back on the wire to the store — not "" and "".
    assert read.rows[0][2:] == ["DWH", "MARTS"]


def test_a_file_read_again_is_capped_like_the_one_that_wrote_the_card(tmp_path: Path):
    """A table read is cut by the store's own LIMIT. A file head is not — it is whatever fits in
    256KB — and only the writer trimmed it. Uncapped, the card says "4,217 rows" over a receipt
    stamped 500: two numbers about one read."""
    mount = tmp_path / "mnt"
    mount.mkdir()
    (mount / "calls.csv").write_text(
        "id,email\n" + "".join(f"{i},p{i}@acme.com\n" for i in range(result.CAP_ROWS + 40)))

    read = run.read_again({"kind": "file", "binding": "bnd_2", "path": "calls.csv"},
                          turn_for(tmp_path, dataset_root=lambda n: mount))

    assert len(read.rows) == result.CAP_ROWS
    assert read.truncated is True


def test_read_again_writes_nothing(tmp_path: Path):
    """The whole ticket. A press that wrote its rows into the Artifact would defeat the opt-out
    permanently, and the next save would commit them."""
    turn = turn_for(tmp_path)
    run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": 2}, turn)
    before = _everything_on_disk(tmp_path)

    read = run.read_again(_artifact(tmp_path, "gong-calls.table.json")["source"], turn)

    assert read.rows, "it did read something"
    assert _everything_on_disk(tmp_path) == before
    assert b"acme.com" not in before


def test_read_again_fills_the_levels_back_in_for_a_bare_table_name(tmp_path: Path):
    turn = turn_for(tmp_path, scope_for={("DWH", ""): ("DWH", "MARTS")})

    read = run.read_again({"kind": "table", "binding": "bnd_1", "table": "GONG__CALLS", "limit": 1},
                          turn)

    assert read.rows[0][2:] == ["DWH", "MARTS"]


def test_read_again_reads_the_file_a_file_card_came_from(tmp_path: Path):
    mount = tmp_path / "mnt"
    mount.mkdir()
    (mount / "calls.csv").write_text("id,email\n1,a@b.com\n")
    turn = turn_for(tmp_path, dataset_root=lambda n: mount)

    read = run.read_again({"kind": "file", "binding": "bnd_2", "path": "calls.csv"}, turn)

    assert read.columns == ["id", "email"]
    assert read.rows == [["1", "a@b.com"]]


# ---- when the viewer may not read it --------------------------------------------------------------


def test_a_viewer_who_can_no_longer_reach_the_store_is_told_so(tmp_path: Path):
    """Criterion: a viewer whose access has been revoked gets a refusal that says so. The card goes
    on showing its shape, because nothing here writes to it."""
    turn = turn_for(tmp_path, source_for=lambda n: None)

    read = run.read_again({"kind": "table", "binding": "bnd_1", "table": "T"}, turn)

    assert "could not find DWH" in read.refused
    assert read.rows == []


def test_a_binding_that_is_gone_names_what_is_missing(tmp_path: Path):
    read = run.read_again({"kind": "table", "binding": "bnd_gone", "table": "T"},
                          turn_for(tmp_path))

    assert read.refused
    assert "no longer" in read.refused
    assert read.rows == []


def test_a_store_that_is_bound_no_longer_refuses_through_the_same_grant(tmp_path: Path):
    """The grant is `grant.reachable`, not a second rule written beside it: unbinding the store
    refuses the button on the line it refuses the agent."""
    turn = turn_for(tmp_path, bound={})

    read = run.read_again({"kind": "table", "binding": "bnd_1", "table": "T"}, turn)

    assert read.refused
    assert read.rows == []


def test_a_record_no_card_could_have_written_is_a_fault_and_not_a_sentence(tmp_path: Path):
    with pytest.raises(ValueError):
        run.read_again({"table": "T"}, turn_for(tmp_path))


# ---- the route ------------------------------------------------------------------------------------


def _client(monkeypatch, answer):
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    monkeypatch.setattr(appmod.orchestrator, "live_read_again", answer)
    return TestClient(appmod.control_app)


def test_the_route_hands_the_cards_own_record_straight_back_to_the_read(monkeypatch):
    """The browser chooses nothing. A card that recorded no source shows no button, so whatever
    arrives here is what some Artifact wrote down."""
    seen = {}

    def answer(thread_id, source):
        seen.update(thread=thread_id, source=source)
        return {"columns": ["ID"], "rows": [[1]], "rowCount": 1, "truncated": False,
                "readAt": "2026-09-11T09:00:00Z"}

    out = _client(monkeypatch, answer).post("/api/threads/thr_a/live-read",
                                            json={"source": {"kind": "table", "binding": "b_1"}})

    assert out.status_code == 200
    assert out.json()["rows"] == [[1]]
    assert seen == {"thread": "thr_a", "source": {"kind": "table", "binding": "b_1"}}


def test_a_refusal_is_an_answer_and_not_a_fault(monkeypatch):
    """200 with a sentence. The person asked whether they can see today's rows; "no, and here is
    why" is the answer, and the card goes on showing the shape it already had."""
    out = _client(monkeypatch, lambda t, s: {"refused": "Sage cannot reach DWH here."}).post(
        "/api/threads/thr_a/live-read", json={"source": {"kind": "table", "binding": "b_1"}})

    assert out.status_code == 200
    assert out.json() == {"refused": "Sage cannot reach DWH here."}


def test_a_record_no_card_wrote_is_a_400(monkeypatch):
    def answer(thread_id, source):
        raise ValueError("that card records no read to run again")

    out = _client(monkeypatch, answer).post("/api/threads/thr_a/live-read", json={"source": {}})

    assert out.status_code == 400


def test_a_date_column_comes_back_as_text_rather_than_as_a_500(monkeypatch):
    """A warehouse hands back dates and decimals, and a response is serialised by the framework
    rather than by `json.dumps(default=str)`. Without the coercion this answers a press with a 500,
    and only on the tables somebody actually has."""
    import datetime
    import decimal

    from sage.orchestrator import service

    assert service._sendable([datetime.date(2026, 9, 11), decimal.Decimal("1.5"), None, 3, "x"]) == [
        "2026-09-11", "1.5", None, 3, "x"]


# ---- what a press may not reach ------------------------------------------------------------------


def test_a_path_climbing_into_the_dataset_next_door_reads_nothing(tmp_path: Path):
    """Mounts are siblings under one parent, so a string prefix is not containment: `../other/x.csv`
    out of `/mnt/data/gong` lands in `/mnt/data/gong-private` and still starts with the string.

    It mattered less while only the model could name a path. **Read again** takes it from a request
    body, so the grant this function just applied is one a browser could otherwise walk around.
    """
    mounts = tmp_path / "mnt"
    (mounts / "gong-exports").mkdir(parents=True)
    (mounts / "gong-exports-private").mkdir()
    (mounts / "gong-exports-private" / "customers.csv").write_text("email\na@b.com\n")
    turn = turn_for(tmp_path, dataset_root=lambda n: mounts / "gong-exports")

    read = run.read_again(
        {"kind": "file", "binding": "bnd_2", "path": "../gong-exports-private/customers.csv"}, turn)

    assert read.rows == []
    assert "no file at" in read.refused


def test_a_limit_no_number_can_hold_is_read_as_no_limit(tmp_path: Path):
    """`json.loads` turns `1e400` into `float("inf")`, and `int(inf)` raises `OverflowError` — which
    is neither of the two a careless tuple catches, so it left the route as a 500."""
    read = run.read_again({"kind": "table", "binding": "bnd_1", "table": "T", "limit": float("inf")},
                          turn_for(tmp_path))

    assert read.refused == ""
    assert len(read.rows) == result.CAP_ROWS


def test_a_file_card_whose_dataset_is_gone_is_told_about_a_file(tmp_path: Path):
    """The refusal is named for what the card is. A file card's viewer never asked about a table."""
    refused = run.read_again({"kind": "file", "binding": "bnd_gone", "path": "x.csv"},
                             turn_for(tmp_path)).refused

    assert "this file was read from" in refused


def test_a_limit_that_is_not_a_number_is_read_as_no_limit(tmp_path: Path):
    """A browser can send anything. A limit that will not parse says nothing about what the person
    may see, so it falls back to the cap rather than becoming a fault with a parser's words in it."""
    read = run.read_again({"kind": "table", "binding": "bnd_1", "table": "T", "limit": "all"},
                          turn_for(tmp_path))

    assert read.refused == ""
    assert len(read.rows) == result.CAP_ROWS


def test_a_read_with_no_table_records_no_source_so_no_button_appears(tmp_path: Path):
    """A record naming a store and no table cannot be run again: the press would reach the store
    with an empty name and come back with the generic failure. Worse on that card than no button."""
    run.perform("live_read_table", {"source": "DWH", "limit": 1}, turn_for(tmp_path))

    assert "source" not in _artifact(tmp_path, "dwh.table.json")
