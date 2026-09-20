"""A classifier that could not answer is reported at WARNING, and every report names its model (#467).

WHY THIS EXISTS. Three read-only classifiers share the capped `ask` slot — `chat_intent`, `handoff`
and `table_rank` — and all three degrade in silence. Measured live on GLM 5.3 OR: every turn's
intent classifier returned a body that would not parse, and the whole of it was

    chat intent: label=- confidence=0.00 context=yes fallback=invalid-json

at INFO, on a turn that then reported an unrelated failure. Two separate things are missing there.
The level says nothing happened. And `fallback=invalid-json` does not say WHICH of the pickable
models cannot produce the JSON, so the line cannot be acted on even by someone who finds it.

WHAT THESE TESTS ARE CAREFUL NOT TO ASSERT is the wording. Every assertion here reads the emitted
`LogRecord` — its level, and the model as an interpolation ARGUMENT — rather than searching the
formatted line for a literal. A test that greps `"model="` out of `record.getMessage()` passes just
as happily when the model name is hardcoded into the format string, which is the one defect that
would make the whole change worthless: it would pin the string and let the behaviour rot under it.
`ASK_MODEL` below is deliberately not a plausible model name for the same reason — a real-looking
one could be a literal somebody typed, and this one could only have arrived by being threaded.

THE SECOND PLANT IS THE POINT. Warning on a failure is easy, and a change that warns on EVERYTHING
is worse than the bug, because it buries the one line worth reading under the ordinary ones.
`low-confidence` is the classifier working and then declining, and it has to stay at INFO.
"""

from __future__ import annotations

import json
import logging

import pytest

from sage.orchestrator import chat_intent, handoff, table_rank
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.table_search import Candidate, Ranking
from sage.router.models import ModelCatalog

ASK_MODEL = "ask-model-under-test"

CATALOG = ModelCatalog(
    sovereign_plan="sov-plan", sovereign_implement="sov-implement", sovereign_ask="sov-ask",
    plan="plan-model", implement="implement-model", ask=ASK_MODEL,
)

SOURCE = Binding(KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse", "Snowflake-Data-Warehouse")
RANKING = Ranking(candidates=(Candidate("DWH", "MARTS", "GONG__CALLS"),
                              Candidate("DWH", "STAGING", "STG_GONG__CALLS")), matched=2)


class Gateway:
    """Answers with one scripted body, whatever it is asked."""

    def __init__(self, body: str):
        self.body = body

    def route(self, request, labels):
        chunk = json.dumps({"choices": [{"delta": {"content": self.body}}]})
        yield f"data: {chunk}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Both siblings carry a process-wide breaker, and an unreset one silences the call under test."""
    handoff._health.reset()
    table_rank._health.reset()
    yield
    handoff._health.reset()
    table_rank._health.reset()


def _from(caplog, logger: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == logger]


def _intent_report(caplog, body: str) -> logging.LogRecord:
    """The one record `chat_intent` emits for a completed classify call.

    Selected by being the only one rather than by matching its text, so no part of the message has
    to be written down here. A clean call, a low-confidence call and an unparseable call each log
    exactly once.
    """
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="sage.orchestrator.chat_intent"):
        chat_intent.start("chart the daily gong calls", context="table DWH.MARTS.GONG__CALLS",
                          has_bound_context=True, gateway=Gateway(body),
                          catalog=CATALOG).result()
    records = _from(caplog, "sage.orchestrator.chat_intent")
    assert len(records) == 1, [r.getMessage() for r in records]
    return records[0]


# --- Plant 1: the classifier could not answer at all. ------------------------------------------

def test_an_unparseable_classifier_body_is_a_warning_that_names_the_model(caplog):
    record = _intent_report(caplog, "Happy to help! The label here is data_answer.")
    assert record.levelno == logging.WARNING
    assert ASK_MODEL in record.args


# --- Plant 2: the classifier answered, and declined. This is the plant that catches -------------
# --- "make everything a warning", which would bury plant 1 under ordinary turns. ---------------

def test_a_low_confidence_answer_stays_at_info_and_still_names_the_model(caplog):
    record = _intent_report(caplog, json.dumps({"label": "data_answer", "confidence": 0.2}))
    assert record.levelno == logging.INFO
    assert ASK_MODEL in record.args


# --- Plant 3: the classifier worked. -----------------------------------------------------------

def test_a_clean_answer_is_info_with_no_fallback_and_still_names_the_model(caplog):
    record = _intent_report(caplog, json.dumps({"label": "data_answer", "confidence": 0.93}))
    assert record.levelno == logging.INFO
    assert ASK_MODEL in record.args
    # The fallback suffix is its own interpolation argument, so "carries no fallback" is readable
    # as data rather than as the absence of a substring in the rendered line.
    assert not any(isinstance(a, str) and a.startswith(" fallback=") for a in record.args)


# --- The level split is over a NAMED set, and the two ordinary outcomes are in it. --------------

def test_the_working_set_admits_only_the_classifier_declining():
    """`no-bound-context` is an ordinary outcome too, and `invalid-confidence` must not sneak in.

    Derived from the module rather than retyped: a list copied into the test cannot notice when the
    one in `chat_intent` grows a member that should have been a warning.
    """
    assert set(chat_intent._WORKING) == {"", "low-confidence", "no-bound-context"}
    # `Intent.valid` is False for `low-confidence`, so a level split derived from it would have put
    # the ordinary outcome in the warning stream. These two answer different questions.
    assert not chat_intent.Intent(label="data_answer", confidence=0.2,
                                  fallback="low-confidence").valid


# --- The two siblings on the same slot. --------------------------------------------------------

def test_handoff_names_its_model_when_the_classifier_returns_nothing(caplog):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="sage.orchestrator.handoff"):
        offered = handoff.wants_an_app(
            title="Gong calls", user="chart the daily gong calls", assistant="Here you go.",
            gateway=Gateway(""), catalog=CATALOG, thread="thr_test",
            sensitivity=lambda _t: (None, ""))
    assert offered is False
    records = _from(caplog, "sage.orchestrator.handoff")
    assert len(records) == 1, [r.getMessage() for r in records]
    assert records[0].levelno == logging.WARNING
    assert ASK_MODEL in records[0].args


def test_table_rank_names_its_model_when_a_stage_returns_nothing(caplog):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="sage.orchestrator.table_rank"):
        table_rank.rank_with_model(
            "chart the daily gong calls", SOURCE, RANKING,
            columns_for=lambda _c, _t: {}, gateway=Gateway(""), catalog=CATALOG)
    warnings = [r for r in _from(caplog, "sage.orchestrator.table_rank")
                if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert ASK_MODEL in warnings[0].args
