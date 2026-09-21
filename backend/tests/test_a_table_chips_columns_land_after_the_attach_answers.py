"""A table chip's columns are read beside the attach, not on it — and the turn still gets them.

The attach POST used to read the table's columns from the warehouse before it stored the row, so
the chip took as long as the warehouse took to describe the table. The read now starts on a
thread after the row is stored, and the TURN joins it, bounded, right before the re-read that
feeds the prompt (#440) — so the fix for the wait does not give back what #440 bought.

Three conditions, one plant each:
- (a) the attach answers while the read is still blocked. Plant: move `_columns_for_context`
  back inline → `add_thread_context` blocks on the Event and the test times out.
- (b) a turn asked straight after the attach carries the columns. Plant: drop
  `_await_chip_columns` from `chat_stream` → the prompt goes without them.
- (c) a read for scope A that lands after the row moved to scope B writes nothing. Plant: drop
  the scope check in `set_context_columns` → A's columns sit under B's name.

- (d) a pin that moves ANOTHER chip's table while this chip's read is out must not take the
  landed columns with it. Plant: hold `confirm_thread_table_candidate`'s read-modify-write open
  across its own column read again → A's columns are gone after the confirm.
- (e) two adds that both passed the `resourceId` pre-check reach `_start_chip_columns` with the
  same row: one read, joined by the turn. Plant: drop the `is_alive` guard → two reads.

- (f) a read that never returns costs ONE turn the bound, not every turn after it. Plant: drop
  the `started + timeout` term from the join → the second turn waits the bound again.

Not covered: two adds racing inside `_CONTEXT_LOCK`. A barrier inside the locked region deadlocks
the test; the lock's own docstring says what it closes.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from sage.orchestrator import service as svc
from sage.workspace.threads import ThreadStore

from .test_a_bare_data_source_chip_carries_its_columns import NAMED, _row
from .test_a_chat_build_request_is_asked_which_table import _gong_warehouse, _orch

CHIP = {
    "kind": "data_source",
    "name": "GONG__CALLS",
    "resourceId": "table:ds-dwh:DWH.MARTS.GONG__CALLS",
    "bindingKey": ["data_source", "ds-dwh"],
    "scope": {"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"},
}


def _slow_columns(orch, gate: threading.Event, monkeypatch):
    """The fake store's `list_columns`, held until `gate` is set. Answers what the fake would."""
    real = orch._resources.list_columns
    calls: list[tuple] = []

    def held(source, database, schema, table=""):
        calls.append((database, schema, table))
        gate.wait(10)
        return real(source, database, schema, table)

    monkeypatch.setattr(orch._resources, "list_columns", held)
    return calls


def _join_jobs(orch, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with orch._chip_column_lock:
            live = [th for _, th, _started in orch._chip_column_jobs.values()]
        if not live:
            return
        for th in live:
            th.join(0.05)
    raise AssertionError("column jobs did not finish")


def test_the_attach_answers_before_the_columns_are_read(tmp_path: Path, monkeypatch):
    """(a)"""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    gate = threading.Event()
    calls = _slow_columns(orch, gate, monkeypatch)
    tid = orch.create_thread()["id"]

    done = threading.Event()
    answer: dict = {}

    def add():
        answer.update(orch.add_thread_context(tid, dict(CHIP)))
        done.set()

    threading.Thread(target=add, daemon=True).start()
    assert done.wait(5), "the attach waited on the column read"
    # The read has started (positive control: without it the next assertion says nothing) and the
    # row is stored without columns yet.
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls, "the column read never started"
    assert "columns" not in _row(orch, tid), "columns were on the row before the read answered"

    gate.set()
    _join_jobs(orch)
    row = _row(orch, tid)
    assert row.get("id") == answer["id"]
    assert [c["name"] for c in row["columns"]] == ["CALL_ID", "STARTED_AT"]


def test_a_turn_asked_straight_after_the_attach_carries_the_columns(tmp_path: Path, monkeypatch):
    """(b) — the join. The gate opens from a third thread a moment after the turn starts, so the
    turn is provably waiting rather than finding the columns already there."""
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    gate = threading.Event()
    _slow_columns(orch, gate, monkeypatch)
    tid = orch.create_thread()["id"]

    orch.add_thread_context(tid, dict(CHIP))
    assert "columns" not in _row(orch, tid), "positive control: the read had already landed"

    threading.Timer(0.3, gate.set).start()
    list(orch.chat_stream(tid, NAMED))

    assert oc.prompts, "the turn reached the agent"
    said = oc.prompts[-1]["text"]
    assert "CALL_ID" in said, said


def test_a_turn_does_not_wait_past_the_bound(tmp_path: Path, monkeypatch):
    """(b), the other half: a store that never answers must not hold the turn for good. The
    bound is shrunk so the test does not sit for 20 s; the prompt goes without columns, as it
    does for a store that refuses (#440's recovery)."""
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    gate = threading.Event()  # never set
    _slow_columns(orch, gate, monkeypatch)
    monkeypatch.setattr(svc, "_CHIP_COLUMNS_WAIT_S", 0.2)
    tid = orch.create_thread()["id"]

    orch.add_thread_context(tid, dict(CHIP))
    started = time.monotonic()
    # The default is bound at definition time, so pass it: the constant is what the plan named
    # and what a live tune touches; the parameter is the seam this test reaches it through.
    real = orch._await_chip_columns
    monkeypatch.setattr(orch, "_await_chip_columns",
                        lambda thread_id: real(thread_id, timeout=svc._CHIP_COLUMNS_WAIT_S))
    list(orch.chat_stream(tid, NAMED))
    assert time.monotonic() - started < 5, "the turn waited on a read that will not answer"
    assert oc.prompts, "the turn reached the agent"
    assert "CALL_ID" not in oc.prompts[-1]["text"]
    gate.set()


def test_columns_read_for_one_table_never_land_under_another(tmp_path: Path):
    """(c)"""
    orch, _ = _orch(tmp_path)
    store = ThreadStore(orch._chat_project().record.path)
    tid = orch.create_thread()["id"]
    row = store.add_context(tid, dict(CHIP))
    moved = {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"}

    # The panel moved the table while the read for GONG__CALLS was still out.
    ctx = store.read_context(tid)
    ctx["items"][0]["scope"] = moved
    store.write_context(tid, ctx)

    wrote = store.set_context_columns(tid, row["id"], CHIP["scope"],
                                      [{"name": "CALL_ID", "type": "TEXT", "table": "GONG__CALLS"}])
    assert wrote is False
    assert "columns" not in store.read_context(tid)["items"][0]

    # And the same call for the scope the row IS on lands.
    wrote = store.set_context_columns(tid, row["id"], moved,
                                      [{"name": "ACCOUNT_ID", "type": "TEXT", "table": "DIM_ACCOUNT"}])
    assert wrote is True
    assert [c["name"] for c in store.read_context(tid)["items"][0]["columns"]] == ["ACCOUNT_ID"]


def test_a_pin_on_another_chip_keeps_the_columns_that_landed_meanwhile(tmp_path: Path, monkeypatch):
    """(d). Chip A's read is out. A pin moves chip B's table, and the store is slow to describe
    B — slow enough that A's columns land in the window. B's write must carry A's columns."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    orch._resources.columns["DIM_ACCOUNT"] = [("ACCOUNT_ID", "TEXT")]
    gate_a, gate_b = threading.Event(), threading.Event()
    real = orch._resources.list_columns

    def held(source, database, schema, table=""):
        (gate_a if table == "GONG__CALLS" else gate_b).wait(10)
        return real(source, database, schema, table)

    monkeypatch.setattr(orch._resources, "list_columns", held)
    tid = orch.create_thread()["id"]
    a = orch.add_thread_context(tid, dict(CHIP))                       # read A out, held
    b = orch.add_thread_context(tid, {"kind": "data_source", "name": "Snowflake-Data-Warehouse",
                                      "resourceId": "data_source:ds-dwh",
                                      "bindingKey": ["data_source", "ds-dwh"]})

    done = threading.Event()

    def pin():
        orch.confirm_thread_table_candidate(tid, "ds-dwh", "DWH", "MARTS", "DIM_ACCOUNT")
        done.set()

    threading.Thread(target=pin, daemon=True).start()
    # The pin is inside its own column read now; A lands while it waits.
    time.sleep(0.2)
    gate_a.set()
    _join_jobs(orch)
    rows = {i["id"]: i for i in ThreadStore(orch._chat_project().record.path)
            .read_context(tid)["items"]}
    assert [c["name"] for c in rows[a["id"]].get("columns") or []] == ["CALL_ID", "STARTED_AT"], \
        "positive control: A did not land before the pin wrote"
    gate_b.set()
    assert done.wait(5)
    rows = {i["id"]: i for i in ThreadStore(orch._chat_project().record.path)
            .read_context(tid)["items"]}
    assert [c["name"] for c in rows[a["id"]].get("columns") or []] == ["CALL_ID", "STARTED_AT"], \
        "the pin's write took A's columns with it"
    assert rows[b["id"]]["scope"]["table"] == "DIM_ACCOUNT"
    assert [c["name"] for c in rows[b["id"]]["columns"]] == ["ACCOUNT_ID"]


def test_two_adds_that_both_passed_the_pre_check_read_the_table_once(tmp_path: Path, monkeypatch):
    """(e). Reached by calling what the second add calls, with the row the first add stored,
    while the first read is still out."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    gate = threading.Event()
    calls = _slow_columns(orch, gate, monkeypatch)
    tid = orch.create_thread()["id"]
    row = orch.add_thread_context(tid, dict(CHIP))
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) == 1, "positive control: the first read did not start"

    source = orch._context_source(dict(CHIP))
    orch._start_chip_columns(tid, row, source, CHIP["scope"])   # the racing second add
    with orch._chip_column_lock:
        assert len(orch._chip_column_jobs) == 1
    gate.set()
    _join_jobs(orch)
    assert len(calls) == 1, "the second add read the table again"
    with orch._chip_column_lock:
        assert not orch._chip_column_jobs, "a finished read left its registration behind"
    assert [c["name"] for c in _row(orch, tid)["columns"]] == ["CALL_ID", "STARTED_AT"]


def test_a_read_that_never_returns_costs_one_turn_the_bound_and_not_the_next(
        tmp_path: Path, monkeypatch):
    """(f). `list_columns` has no timeout of its own, so a hung describe stays registered for the
    life of the process. The first turn after it waits the bound; the one after must not."""
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    gate = threading.Event()  # never set
    _slow_columns(orch, gate, monkeypatch)
    monkeypatch.setattr(svc, "_CHIP_COLUMNS_WAIT_S", 0.4)
    real = orch._await_chip_columns
    waits: list[float] = []   # the join's own time, not the turn's — the turn has its own delays

    def timed(thread_id):
        t0 = time.monotonic()
        real(thread_id, timeout=svc._CHIP_COLUMNS_WAIT_S)
        waits.append(time.monotonic() - t0)

    monkeypatch.setattr(orch, "_await_chip_columns", timed)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, dict(CHIP))

    list(orch.chat_stream(tid, NAMED))
    list(orch.chat_stream(tid, NAMED))
    assert len(oc.prompts) == 2 and len(waits) == 2, (len(oc.prompts), waits)
    assert waits[0] >= 0.3, f"positive control: the first turn did not wait the bound ({waits})"
    assert waits[1] < 0.1, f"the second turn waited on the hung read again ({waits})"
    gate.set()
