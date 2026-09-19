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

from sage.liveread.mcp import _failed_text
from sage.resources.provider import (
    DataSource,
    DominoResourceProvider,
    ResourceUnavailable,
    failure_kind,
    readable_error,
)

FLIGHT = (
    "Flight returned unavailable error, with message: failed to connect to all addresses; "
    "last error: UNKNOWN: ipv4:10.0.3.4:8080: Failed to connect to remote host: Connection refused"
)


def _wire(status: str, message: str) -> str:
    """One error shaped the way `domino_data` actually hands it over.

    Use this for EVERY fixture in this file. pyarrow's Flight formatter writes
    `Flight returned <status> error, with message: <payload>` in front of everything that crosses
    the wire, and `_unpack_flight_error` removes only the trailing gRPC debug context, so the
    prefix always survives to the caller.

    This helper exists because inventing the string is what hid #399. Of the ten fixtures this file
    carried before that ticket, exactly one was producer-shaped; the rest were hand-written, so the
    suite proved a branch reachable that production could never enter. A predicate checked against
    strings its producer cannot emit is not checked at all.
    """
    return f"Flight returned {status} error, with message: {message}"


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
    said = _read_raising(monkeypatch, RuntimeError(
        _wire("invalid argument", "SQL compilation error: invalid identifier 'CUSTOMR_ID'")))

    assert "did not answer" in said
    assert "invalid identifier 'CUSTOMR_ID'" in said


@pytest.mark.parametrize("message", [
    FLIGHT,
    _wire("unavailable", "failed to connect to all addresses"),
    _wire("deadline exceeded", "Deadline Exceeded"),
    _wire("unavailable", "grpc: the connection is unavailable"),
    _wire("internal", "socket closed"),
])
def test_a_question_that_never_left_is_recognised_as_transport(message):
    assert failure_kind(RuntimeError(message))[0] == "never_delivered"


@pytest.mark.parametrize("message", [
    # Both captured live on 2026-09-17 from the running Sage process (#399).
    _wire("not found", "no credentials for user someone"),
    _wire("invalid argument", "Type: configObjectError, Subtype: invalidHostOrPort"),
    _wire("unauthenticated", "invalid token"),
])
def test_a_domino_configuration_fault_is_its_own_population(message):
    """Neither of the other two sentences is true here, which is why there are three.

    Waiting never repairs a missing credential, and the source cannot be quoted as having answered
    because it was never asked. Before #399 every one of these read as transport.
    """
    assert failure_kind(RuntimeError(message))[0] == "setup_fault"


@pytest.mark.parametrize("message", [
    "SQL compilation error: invalid identifier",
    "Table 'GONG__CALLS' does not exist or not authorized",
    "Warehouse 'HUMANS' cannot be resumed because",
])
def test_a_real_store_answer_survives_the_wrapper(message):
    """The regression #399 was. Each of these classified correctly while bare and flipped the
    moment it was wrapped the way the producer wraps it, so the bare form proves nothing."""
    assert failure_kind(RuntimeError(message))[0] == "answered"
    assert failure_kind(RuntimeError(_wire("invalid argument", message)))[0] == "answered"


def test_a_message_nobody_classified_is_shown_rather_than_swallowed():
    """The default branch is the one that shows the most.

    A wrong guess here should cost a clause, never the diagnosis — the old predicate swallowed
    every message, which is why #399 and #404 took two sessions to come apart.
    """
    kind, said = failure_kind(RuntimeError(_wire("internal", "something nobody predicted")))
    assert kind == "answered"
    assert said == "something nobody predicted"


def test_the_wrapper_never_reaches_the_person(monkeypatch):
    """Acceptance criterion 3. Repairing the predicate alone would have rendered
    `DominoError: Flight returned invalid argument error, with message: ...` at the creator —
    plumbing this feature exists to hide, invisible before only because the branch was dead."""
    said = _read_raising(monkeypatch, RuntimeError(
        _wire("invalid argument", "SQL compilation error: invalid identifier 'CUSTOMR_ID'")))

    assert "invalid identifier 'CUSTOMR_ID'" in said
    for plumbing in ("Flight returned", "with message:", "DominoError", "RuntimeError"):
        assert plumbing not in said, f"{plumbing!r} is plumbing"


def test_a_configuration_fault_does_not_tell_them_to_wait(monkeypatch):
    said = _read_raising(monkeypatch, RuntimeError(
        _wire("not found", "no credentials for user someone")))

    assert "no credentials for user someone" in said, "the reason they can act on"
    assert "Try again" not in said, "it will never clear on its own"
    assert "did not answer" not in said, "the source was never asked"


def test_an_address_in_an_unclassified_message_is_still_scrubbed():
    # Backstop: a store error that happens to quote a host must not carry one either.
    said = readable_error(RuntimeError("host 10.11.12.13:5432 rejected the statement"))
    assert "10.11.12.13" not in said and "[address]" in said


def test_the_secret_rule_still_applies():
    said = readable_error(RuntimeError("auth failed for " + "a" * 64))
    assert "a" * 64 not in said and "[redacted]" in said


# The Postgres family arrives inside Domino's own `Type:/Subtype:/Message:` envelope, and Snowflake
# does not. Both captured live on 2026-09-17 by `spikes/domino-probes/store_rejection_status_probe.py`
# and re-measured in process before this fix. Through `_wire` for the reason that helper's docstring
# gives: a fixture the producer cannot emit is not a fixture.
PG_MISSING_RELATION = _wire(
    "invalid argument",
    'Type: internalError, Subtype: . Message: ERROR: relation "public.gong_calls" does not exist '
    "(SQLSTATE 42P01)")
PG_MISSING_COLUMN = _wire(
    "invalid argument",
    'Type: internalError, Subtype: . Message: ERROR: column "custmr_id" does not exist '
    "(SQLSTATE 42703)")
SNOWFLAKE_OBJECTION = _wire(
    "invalid argument",
    "002003 (42S02): SQL compilation error: Object 'DWH.MARTS.NOPE' does not exist or not "
    "authorized.")


@pytest.mark.parametrize("message, own_words", [
    (PG_MISSING_RELATION, 'ERROR: relation "public.gong_calls" does not exist (SQLSTATE 42P01)'),
    (PG_MISSING_COLUMN, 'ERROR: column "custmr_id" does not exist (SQLSTATE 42703)'),
])
def test_a_postgres_objection_reaches_the_person_in_the_stores_own_words(monkeypatch, message,
                                                                        own_words):
    """Acceptance criterion 1. A mistyped table name is the person's own, and repairable by them.

    Before #406 they read `Type: internalError, Subtype: . Message: ERROR: relation ... does not
    exist` — Domino's envelope calling a typo an internal error, with an empty subtype rendered as
    a bare full stop, so the envelope was not even well-formed prose.
    """
    said = _read_raising(monkeypatch, RuntimeError(message))

    assert own_words in said
    for envelope in ("Type:", "Subtype:", "Message:", "internalError"):
        assert envelope not in said, f"{envelope!r} is Domino's envelope, not the store's words"


def test_the_model_reads_the_stores_own_words_too(monkeypatch):
    """Acceptance criterion 2, and the half #399 did not cover.

    `mcp.handle` hands the model `_failed_text(str(e))` on this exact exception, immediately before
    telling it to go and answer another way. The word `internalError` there steers both the retry
    and the sentence the model then writes to the person — the #398 shape, Sage supplying the
    vocabulary and the model faithfully repeating it. Asserted on `_failed_text`'s output rather
    than on the provider's exception, because that composition is what the model actually reads.
    """
    text = _failed_text(_read_raising(monkeypatch, RuntimeError(PG_MISSING_RELATION)))

    assert 'ERROR: relation "public.gong_calls" does not exist (SQLSTATE 42P01)' in text
    assert "Nothing was put on the person's screen." in text, "the standing warning survives"
    for envelope in ("Type:", "Subtype:", "Message:", "internalError"):
        assert envelope not in text, f"the model is still handed {envelope!r}"


def test_a_snowflake_objection_is_unchanged(monkeypatch):
    """Acceptance criterion 3. Snowflake has no envelope — its objection is already its own words.

    Character for character, because the plant this guards against is a strip widened to drop
    everything up to the first `.`, which would eat `002003 (42S02)` and leave the person an error
    with no code in it.
    """
    said = _read_raising(monkeypatch, RuntimeError(SNOWFLAKE_OBJECTION))

    assert said == ("Snowflake-Data-Warehouse did not answer: 002003 (42S02): SQL compilation "
                    "error: Object 'DWH.MARTS.NOPE' does not exist or not authorized.")


@pytest.mark.parametrize("message, kind", [
    (_wire("invalid argument", "Type: configObjectError, Subtype: invalidHostOrPort. "),
     "setup_fault"),
    (PG_MISSING_RELATION, "answered"),
])
def test_unwrapping_for_display_does_not_move_the_classification(message, kind):
    """Acceptance criterion 4. The strip runs after `failure_kind`, never before it.

    Both populations arrive in the SAME envelope and are told apart by the Type value (#399), so a
    strip applied first would hand the predicate a payload with nothing left to read. Category 2
    carries no `. Message:` part, which is also why keying on `Message:` rather than on a list of
    Type values discriminates at all.
    """
    assert failure_kind(RuntimeError(message))[0] == kind


def test_the_envelope_still_reaches_the_classifier_intact():
    """The arming half of criterion 4, and it took a plant that stayed green to find it.

    Asserting the two verdicts above does NOT catch a strip moved into `failure_kind`: on every
    payload measured so far the verdict is the same either way, because the one category-2 string
    we have carries no `. Message:` part for the strip to bite on. So the plant "strip before
    classification" ran green against the verdicts alone. What actually breaks under that move is
    the INPUT the predicate reads — `_SETUP_FAULT` matches `configobjecterror`, which lives in the
    Type value, so the day a config fault arrives with a `. Message:` part a strip placed upstream
    would delete the only word telling it from a store objection and it would fall through to
    `answered`. That string has not been measured and is not invented here (#399): the check is on
    the payload `failure_kind` hands back, which is measured and which pins the placement directly.
    """
    kind, said = failure_kind(RuntimeError(PG_MISSING_RELATION))

    assert kind == "answered"
    assert said.startswith("Type: internalError, Subtype: . Message: "), (
        "the envelope must survive to the predicate — it is unwrapped at the point of display")


def test_an_empty_envelope_keeps_the_envelope(monkeypatch):
    """A strip that empties the sentence is worse than the wrapper it removed.

    Domino emits empty fields — the captured Postgres string carries `Subtype: .` — so an empty
    `Message:` is the same shape one field over, and `failure_kind` collapses the trailing space
    away before the strip ever sees it. Stripped, the person reads `... did not answer: ` ending at
    the colon and the model is handed `The live read did not happen: ... did not answer: .`, which
    says Sage lost the reason rather than that the store gave none.
    """
    said = _read_raising(monkeypatch, RuntimeError(
        _wire("invalid argument", "Type: internalError, Subtype: . Message: ")))

    assert said.rstrip() != "Snowflake-Data-Warehouse did not answer:", "the sentence ends nowhere"
    assert "Type: internalError" in said, "with nothing to show, show the wrapper"
