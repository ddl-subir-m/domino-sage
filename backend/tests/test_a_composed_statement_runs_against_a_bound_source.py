"""Running one statement the AGENT composed, rather than one Sage wrote (ADR-0058, #408).

`sample_rows` is handed a table and builds the SQL. `run_statement` is handed the SQL. That one
difference is the whole of what these tests are about, and it moves three things:

- the cap now applies to an ANSWER rather than to a question, so whether the store had more to say
  is a fact that has to be reported rather than assumed;
- the drain cannot go through `to_pandas()`, which loads an entire resultset before anything can
  look at it — safe for a statement carrying its own `LIMIT`, and an orchestrator-wide outage for
  `SELECT *` over a fact table;
- there is no deadline available to ask for, so the timeout is ABANDONMENT and the tests say so.

What is deliberately NOT tested here is that the statement is read-only. Nothing in the code decides
that, on purpose: the guarantee is the read-only warehouse role behind the Data Source, and a test
asserting Sage inspects the SQL would pin a mechanism ADR-0058 rejected. See `run_statement`'s
docstring for the condition that retires that argument.

No warehouse and no `domino_data` here. The library is an extra (`--extra domino`) that CI does not
install, so the real provider is exercised against a stub module and the stream-drain against a
stub reader — both duck-typed, which is all `_drain_within` asks of them.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

from sage.resources.provider import (
    DataSource,
    DominoResourceProvider,
    FakeResourceProvider,
    ResourceUnavailable,
    StatementRows,
    StatementTimeout,
    _drain_within,
)


def _source(connector_type: str = "SnowflakeConfig") -> DataSource:
    return DataSource("ds-dwh", "DWH", connector_type.removesuffix("Config"), "Shared", None, True,
                      connector_type=connector_type)


# --- the fake, which is what every caller above this layer will be written against ---------------


def test_a_mapped_statement_comes_back_as_columns_and_rows():
    provider = FakeResourceProvider(statements={
        "SELECT COUNT(*) AS N FROM EVENTS": (["N"], [[41234]]),
    })
    answer = provider.run_statement(_source(), "SELECT COUNT(*) AS N FROM EVENTS", limit=500)
    assert answer == StatementRows(["N"], [[41234]], False)


def test_a_statement_nobody_mapped_is_refused_the_way_a_store_refuses_one():
    """An unmapped statement is a store objecting, not a crash.

    It matters that this is `ResourceUnavailable` and carries the source's name: that is the shape
    #399 settled on, where the store's own words are the only thing separating "Sage sent the wrong
    SQL for this connector" from "this schema is empty".
    """
    provider = FakeResourceProvider()
    with pytest.raises(ResourceUnavailable) as caught:
        provider.run_statement(_source(), "SELECT 1", limit=500)
    assert "DWH did not answer" in str(caught.value)


def test_a_connector_with_no_dialect_can_still_be_asked_a_question():
    """No dialect is consulted, and that is the point rather than an omission.

    Everywhere else in `FakeResourceProvider` calls `dialect_for` so the fake refuses what the real
    provider refuses. Here the real one uses no dialect at all: Sage writes no SQL, so there is
    nothing to spell per connector, and the other Chat lane already runs arbitrary SQL against any
    bound source through `domino_data` with no dialect involved.

    Oracle is the case — it is a real `DataSource` connector with no entry in `SQL_DIALECTS`, so
    `dialect_for` refuses it and the cascade cannot look inside it. A statement against it is still
    a statement the store can answer.
    """
    provider = FakeResourceProvider(statements={"SELECT 1": (["N"], [[1]])})
    assert provider.run_statement(_source("OracleConfig"), "SELECT 1", limit=500).rows == [[1]]


def test_the_cap_cuts_the_answer_and_says_that_it_did():
    provider = FakeResourceProvider(statements={
        "SELECT NAME FROM ACCOUNTS": (["NAME"], [[f"acc-{i}"] for i in range(10)]),
    })
    answer = provider.run_statement(_source(), "SELECT NAME FROM ACCOUNTS", limit=4)
    assert answer.rows == [["acc-0"], ["acc-1"], ["acc-2"], ["acc-3"]]
    assert answer.truncated is True


def test_an_answer_that_ends_exactly_on_the_cap_was_not_cut():
    """The off-by-one that would put "4 of more than 4" under a table holding the whole answer.

    Pinned on the fake AND on the real drain below, because they are two implementations of one
    contract and this is the half of it a caller renders into a sentence.
    """
    provider = FakeResourceProvider(statements={
        "SELECT NAME FROM ACCOUNTS": (["NAME"], [[f"acc-{i}"] for i in range(4)]),
    })
    answer = provider.run_statement(_source(), "SELECT NAME FROM ACCOUNTS", limit=4)
    assert len(answer.rows) == 4
    assert answer.truncated is False


# --- the drain, which is the half that keeps a big answer from taking the orchestrator down ------


class _Batch:
    """One record batch, duck-typed to what `_drain_within` reads off a real one."""

    def __init__(self, names: list[str], columns: list[list]) -> None:
        self.schema = types.SimpleNamespace(names=names)
        self._columns = columns
        self.num_columns = len(columns)
        self.num_rows = len(columns[0]) if columns else 0

    def column(self, i: int):
        return types.SimpleNamespace(to_pylist=lambda: self._columns[i])


class _Reader:
    """A Flight stream reader that hands out batches and records whether it was cancelled."""

    def __init__(self, batches: list[_Batch]) -> None:
        self._batches = list(batches)
        self.cancelled = False

    def read_chunk(self):
        if not self._batches:
            raise StopIteration
        return types.SimpleNamespace(data=self._batches.pop(0))

    def cancel(self) -> None:
        self.cancelled = True


def _result(batches: list[_Batch]) -> types.SimpleNamespace:
    return types.SimpleNamespace(reader=_Reader(batches))


def test_the_drain_stops_at_the_cap_and_cancels_the_rest_of_the_stream():
    """The reason this reads chunks rather than calling `to_pandas()`.

    The second batch must never be pulled across the wire: a statement can answer with more rows
    than the process can hold, and `to_pandas()` would have loaded all of them before the cap got a
    say.
    """
    result = _result([
        _Batch(["NAME"], [["a", "b", "c"]]),
        _Batch(["NAME"], [["d", "e", "f"]]),
    ])
    answer = _drain_within(result, 2)
    assert answer.rows == [["a"], ["b"]]
    assert answer.truncated is True
    assert result.reader.cancelled is True


def test_a_stream_that_ends_on_the_cap_is_not_truncated_and_is_not_cancelled():
    result = _result([_Batch(["NAME"], [["a", "b"]])])
    answer = _drain_within(result, 2)
    assert answer.rows == [["a"], ["b"]]
    assert answer.truncated is False
    # Nothing was left unread, so there was nothing to cancel. Cancelling a finished stream would
    # be harmless and would also mean this test could not tell the two states apart.
    assert result.reader.cancelled is False


def test_two_output_columns_with_one_name_both_survive():
    """Read by POSITION, as `frame_rows` reads them and for the reason recorded there.

    `to_pydict()` is keyed by name and would answer with one of these twice. An agent-composed
    statement — `SELECT A.ID, B.ID FROM …` — reaches this far more readily than a generated
    `SELECT *` ever did.
    """
    result = _result([_Batch(["ID", "ID"], [[1, 2], [90, 91]])])
    answer = _drain_within(result, 500)
    assert answer.columns == ["ID", "ID"]
    assert answer.rows == [[1, 90], [2, 91]]


def test_an_empty_answer_is_an_answer():
    result = _result([])
    answer = _drain_within(result, 500)
    assert answer == StatementRows([], [], False)


# --- the real provider: abandonment, and the failure classification it shares with `_query` -------


def _with_stub_domino_data(monkeypatch, query):
    """Install a `domino_data.data_sources` whose client runs `query(sql)`.

    Through `monkeypatch.setitem` rather than a bare assignment, because `sys.modules` is shared by
    every test on this xdist worker: a stub left behind would redden a file this one never opened.
    """
    class _Client:
        def get_datasource(self, name):
            return types.SimpleNamespace(query=query)

    module = types.ModuleType("domino_data.data_sources")
    module.DataSourceClient = _Client
    package = types.ModuleType("domino_data")
    package.data_sources = module
    monkeypatch.setitem(sys.modules, "domino_data", package)
    monkeypatch.setitem(sys.modules, "domino_data.data_sources", module)


def test_a_statement_that_runs_too_long_is_abandoned_and_the_error_is_sages_own(monkeypatch):
    """The cap, and the sentence it produces.

    `domino_data` accepts no timeout at any level, so Sage cannot ask the store to stop — it can
    only stop waiting. The sentence therefore must not imply the store said anything, because it
    did not and is very likely still working.
    """
    running = threading.Event()
    release = threading.Event()

    def query(sql):
        running.set()
        release.wait(30)
        return _result([])

    _with_stub_domino_data(monkeypatch, query)
    provider = DominoResourceProvider("http://gw/v1", lambda: "tok")
    try:
        with pytest.raises(StatementTimeout) as caught:
            provider.run_statement(_source(), "SELECT 1", limit=500, timeout_s=0.1)
        said = str(caught.value)
        assert "stopped waiting" in said
        assert "did not answer" not in said, "must not read as the store having refused"
        assert running.is_set(), "the statement was started, not skipped"
    finally:
        # The abandoned worker is still blocked in `query`. Released here so the thread ends with
        # the test rather than lingering for the rest of the session.
        release.set()


def test_the_abandonment_is_still_a_resource_unavailable():
    """Its own type for the one caller that must tell a timeout from a refusal (#411), and a
    `ResourceUnavailable` so every handler written before it still catches it."""
    assert issubclass(StatementTimeout, ResourceUnavailable)


def test_a_store_that_never_received_the_statement_is_not_reported_as_having_refused(monkeypatch):
    """`run_statement` drains its own stream, so it cannot go through `_query` — and the #399
    three-way classification has to reach it anyway. It does, through `_store_failure`."""
    def query(sql):
        raise RuntimeError("Flight returned unavailable error, with message: connection refused")

    _with_stub_domino_data(monkeypatch, query)
    provider = DominoResourceProvider("http://gw/v1", lambda: "tok")
    with pytest.raises(ResourceUnavailable) as caught:
        provider.run_statement(_source(), "SELECT 1", limit=500)
    said = str(caught.value)
    assert "Try again in a moment" in said
    assert "did not answer" not in said


def test_a_store_that_objected_is_quoted(monkeypatch):
    """The store's own words, which are the only signal separating a wrong dialect from an empty
    schema (#399)."""
    def query(sql):
        raise RuntimeError("SQL compilation error: invalid identifier 'NOPE'")

    _with_stub_domino_data(monkeypatch, query)
    provider = DominoResourceProvider("http://gw/v1", lambda: "tok")
    with pytest.raises(ResourceUnavailable) as caught:
        provider.run_statement(_source(), "SELECT NOPE", limit=500)
    assert "DWH did not answer" in str(caught.value)
    assert "invalid identifier" in str(caught.value)


def test_a_statement_runs_and_comes_back_capped(monkeypatch):
    """End to end on the real provider: the statement reaches the client, and the cap that protects
    the process is applied on the way back rather than after everything has been loaded."""
    asked: list[str] = []

    def query(sql):
        asked.append(sql)
        return _result([_Batch(["ACCOUNT", "N"], [["a", "b", "c"], [3, 2, 1]])])

    _with_stub_domino_data(monkeypatch, query)
    provider = DominoResourceProvider("http://gw/v1", lambda: "tok")
    answer = provider.run_statement(
        _source(), "SELECT ACCOUNT, COUNT(*) AS N FROM E GROUP BY 1", limit=2)
    assert asked == ["SELECT ACCOUNT, COUNT(*) AS N FROM E GROUP BY 1"]
    assert answer.columns == ["ACCOUNT", "N"]
    assert answer.rows == [["a", 3], ["b", 2]]
    assert answer.truncated is True
