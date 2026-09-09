"""A read that never got to the store must not answer with Domino's transport.

Live, against cloud-dogfood, a failed read came back as:

    Snowflake-Data-Warehouse did not answer: DominoError: Flight returned unavailable error, with
    message: failed to connect to all addresses; last error: UNKNOWN: ipv4:10.0.3.4:8080: Failed
    to connect to remote host: Connection refused

That is a gRPC peer address in front of somebody who asked to see a row, and it is the failure mode
both AGENTS.md files already forbid in prose — named machinery, no act to take.

The line is not "hide errors". A store that ANSWERED badly has told the creator something they
need: an unverified dialect has to fail honestly instead of looking like an empty schema. A
transport that never delivered the question has told them nothing but its own plumbing. Arrow
Flight and gRPC are Domino's words between Sage and the datasource proxy, never Snowflake's, so a
message carrying one never reached the store.
"""

from __future__ import annotations

import sys
import types

import pytest

from sage.resources.provider import (
    DataSource,
    DominoResourceProvider,
    ResourceUnavailable,
    readable_error,
    store_was_reached,
)

FLIGHT = (
    "Flight returned unavailable error, with message: failed to connect to all addresses; "
    "last error: UNKNOWN: ipv4:10.0.3.4:8080: Failed to connect to remote host: Connection refused"
)


def _source() -> DataSource:
    return DataSource(id="ds1", name="Snowflake-Data-Warehouse", connector="Snowflake",
                      credential_type="Individual", connector_type="SnowflakeConfig")


def _read_raising(monkeypatch, exc: Exception) -> str:
    """Fail where the real one fails: inside `.query()`, which is the Flight hop.

    The module is faked rather than imported. `domino_data` is the `domino` extra, which no test
    installs (see backend/pyproject.toml), so importing it for real passes on a laptop that once
    synced that extra and fails in CI.
    """

    class Boom:
        def get_datasource(self, name):
            return self

        def query(self, sql):
            raise exc

    ds = types.ModuleType("domino_data.data_sources")
    ds.DataSourceClient = Boom
    monkeypatch.setitem(sys.modules, "domino_data", types.ModuleType("domino_data"))
    monkeypatch.setitem(sys.modules, "domino_data.data_sources", ds)
    p = DominoResourceProvider.__new__(DominoResourceProvider)
    with pytest.raises(ResourceUnavailable) as caught:
        DominoResourceProvider._query(p, _source(), 'SELECT * FROM "DWH"."MARTS"."GONG__CALLS"')
    return str(caught.value)


def test_a_read_that_never_reached_the_store_says_so_without_the_plumbing(monkeypatch):
    said = _read_raising(monkeypatch, RuntimeError(FLIGHT))

    assert "Snowflake-Data-Warehouse" in said, "still name the thing they asked about"
    assert "Try again" in said, "and give them the act"
    for mechanism in ("ipv4", "grpc", "Flight", "10.0.3.4", "8080", "RuntimeError", "addresses"):
        assert mechanism.lower() not in said.lower(), f"{mechanism!r} is plumbing"


def test_a_store_that_answered_badly_still_hands_over_its_own_words(monkeypatch):
    # The other half, and the reason this is a split rather than a blanket scrub: an unverified
    # dialect must fail honestly rather than read as an empty schema.
    said = _read_raising(monkeypatch, RuntimeError("SQL compilation error: invalid identifier 'CUSTOMR_ID'"))

    assert "did not answer" in said
    assert "invalid identifier 'CUSTOMR_ID'" in said


@pytest.mark.parametrize("message", [
    FLIGHT,
    "Flight returned unauthenticated error",
    "failed to connect to all addresses",
    "Deadline Exceeded",
    "grpc: the connection is unavailable",
    "socket closed",
])
def test_every_transport_failure_is_recognised_as_one(message):
    assert not store_was_reached(RuntimeError(message))


@pytest.mark.parametrize("message", [
    "SQL compilation error: invalid identifier",
    "Table 'GONG__CALLS' does not exist or not authorized",
    "Warehouse 'HUMANS' cannot be resumed because",
])
def test_a_real_store_answer_is_not_mistaken_for_transport(message):
    assert store_was_reached(RuntimeError(message))


def test_an_address_in_an_unclassified_message_is_still_scrubbed():
    # Backstop: a store error that happens to quote a host must not carry one either.
    said = readable_error(RuntimeError("host 10.11.12.13:5432 rejected the statement"))
    assert "10.11.12.13" not in said and "[address]" in said


def test_the_secret_rule_still_applies():
    said = readable_error(RuntimeError("auth failed for " + "a" * 64))
    assert "a" * 64 not in said and "[redacted]" in said
