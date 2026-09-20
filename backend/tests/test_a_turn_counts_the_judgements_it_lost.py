"""A turn says how many judgements it asked for and did not get (#463).

WHY THIS EXISTS. Four read-only classifiers share the `ask` slot, and each falls back correctly and
silently when it cannot get an answer. `scope` fails OPEN so an unreachable classifier cannot block
builds; `table_rank` fails CLOSED because the layer heuristic always has candidates; Chat answers
either way. Every individual decision is right and the COMPOSITION is not: measured live on two
revs, two turns closed `ok: true, decision: "answered"` while three judgements had been replaced by
defaults, and the one control the person has over models could not reach the slot that broke.

Measured 2026-09-20, the failure is also INTERMITTENT — two clean classifies at 0.85 and 0.95, then
two `fallback=invalid-json`, same model, same session. That is what makes a count necessary rather
than optional: a classifier that always fails is eventually noticed, and one that fails on an
unpredictable subset of turns produces two clean lines that convince the next reader the slot is
healthy.

WHAT THESE TESTS ARE CAREFUL ABOUT. The count is asserted through `/api/diag` on the real route,
not by reading `degraded.count()` beside the classifier that moved it — the whole deliverable is
that the number is reachable without a shell, and a helper asserted in isolation cannot show that
the route carries it. And `SAGE_TIMING` is turned OFF in the test that matters, with the plant
checked (`timing.enabled()` is False), because the one thing this count must not become is a
diagnostic switched off by a performance flag.
"""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from sage import degraded, timing
from sage.orchestrator import app as app_module
from sage.orchestrator import chat_intent, handoff, scope, table_rank
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.table_search import Candidate, Ranking
from sage.router.models import ModelCatalog

ASK_MODEL = "ask-model-under-test"
CATALOG = ModelCatalog(
    sovereign_plan="sov-plan", sovereign_implement="sov-implement", sovereign_ask="sov-ask",
    plan="plan-model", implement="implement-model", ask=ASK_MODEL,
)
SOURCE = Binding(KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse",
                 "Snowflake-Data-Warehouse")
RANKING = Ranking(candidates=(Candidate("DWH", "MARTS", "GONG__CALLS"),
                              Candidate("DWH", "STAGING", "STG_GONG__CALLS")), matched=2)


class Gateway:
    """One scripted body, however it is asked."""

    def __init__(self, body: str):
        self.body = body

    def route(self, request, labels):
        chunk = json.dumps({"choices": [{"delta": {"content": self.body}}]})
        yield f"data: {chunk}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _clean_process_state():
    """Every one of these is process-wide, so an unreset one leaks into the next test.

    The three breakers are reset for the reason `test_a_degraded_classifier_names_its_model` resets
    two of them: a tripped breaker silences the classifier under test. The count is reset for the
    stronger reason that it is the value being measured.
    """
    for module in (scope, handoff, table_rank):
        module._health.reset()
    degraded.reset()
    yield
    for module in (scope, handoff, table_rank):
        module._health.reset()
    degraded.reset()


def _intent(body: str) -> None:
    chat_intent.start("chart the daily gong calls", context="table DWH.MARTS.GONG__CALLS",
                      has_bound_context=True, gateway=Gateway(body), catalog=CATALOG).result()


def _handoff(body: str) -> None:
    handoff.wants_an_app(title="Gong calls", user="chart the daily gong calls",
                         assistant="Here you go.", gateway=Gateway(body), catalog=CATALOG,
                         thread="thr_test", sensitivity=lambda _t: (None, ""))


def _table_rank(body: str) -> None:
    table_rank.rank_with_model("chart the daily gong calls", SOURCE, RANKING,
                               columns_for=lambda _c, _t: {}, gateway=Gateway(body),
                               catalog=CATALOG)


# ---- what counts, and what does not -------------------------------------------------------------


def test_a_clean_turn_leaves_the_count_at_zero():
    """Otherwise the number is noise on every healthy turn, and nobody reads a gauge that is never
    at rest."""
    _intent(json.dumps({"label": "data_answer", "confidence": 0.93}))
    assert degraded.count() == 0


def test_the_classifier_answering_and_then_declining_is_not_a_lost_judgement():
    """`low-confidence` is an ordinary outcome the turn is designed to proceed from, and #467 put it
    at INFO for that reason. The count and the warning stream draw the line in the same place, or
    the count means something the log cannot corroborate."""
    _intent(json.dumps({"label": "data_answer", "confidence": 0.2}))
    assert degraded.count() == 0


def test_an_unusable_body_is_one_lost_judgement():
    _intent("Happy to help! The label here is data_answer.")
    assert degraded.count() == 1


def test_three_classifiers_degrading_on_one_turn_come_to_three():
    """The composition is the whole complaint. One number in place of three grep strings."""
    _intent("not json at all")
    _handoff("")
    _table_rank("")
    assert degraded.count() == 3


def test_a_failed_column_read_costs_the_second_stage_its_judgement(caplog):
    """Not the model call — the warehouse read in front of stage two, which costs the same thing.

    The same shape as "no budget left for the columns stage" a few lines on, which already counted.
    Asserted on the LOG LINE as well as the number, because the count alone cannot say which of the
    two catch-alls in this module moved it, and a test that could not tell them apart would pass
    for either one.
    """
    def explodes(_candidates, _timeout):
        raise RuntimeError("the warehouse went away")

    with caplog.at_level(logging.DEBUG, logger="sage.orchestrator.table_rank"):
        table_rank.rank_with_model("chart the daily gong calls", SOURCE, RANKING,
                                   columns_for=explodes, gateway=Gateway("DWH.MARTS.GONG__CALLS"),
                                   catalog=CATALOG)
    assert any("could not read columns" in r.getMessage() for r in caplog.records)
    # One, not two: stage one answered, and only stage two lost its judgement.
    assert degraded.count() == 1


def test_a_ranking_that_failed_outright_counts_with_no_stage_behind_it(monkeypatch, caplog):
    """The catch-all around the WHOLE ranking, which is the one site with no stage report behind it.

    A raise before any stage reaches its own report loses the entire judgement with nothing having
    counted, so a count keyed only on the four reporting sites would read zero on the turn where
    the classifier lost the most. `_ranked` is replaced rather than provoked, because every way of
    provoking it from outside is caught by a stage first — which is the point: this `except` exists
    for the raises nothing inside anticipated.
    """
    def boom(*_a, **_k):
        raise RuntimeError("something nobody wrote a branch for")

    monkeypatch.setattr(table_rank, "_ranked", boom)
    with caplog.at_level(logging.DEBUG, logger="sage.orchestrator.table_rank"):
        table_rank.rank_with_model("chart the daily gong calls", SOURCE, RANKING,
                                   columns_for=lambda _c, _t: {}, gateway=Gateway(""),
                                   catalog=CATALOG)
    assert any("ranking failed outright" in r.getMessage() for r in caplog.records)
    assert degraded.count() == 1


def test_a_classifier_whose_breaker_has_tripped_still_counts_its_loss():
    """The case the count is worth most on, and the only one with no log line of its own.

    Each breaker says its piece ONCE at ERROR and is then silent for the life of the process, so
    from the second turn onward every Auto turn builds ungated with nothing whatever to say so —
    the loudest degradation on the quietest surface. Without this the turns that lost a judgement
    for good would be the turns whose count reads zero.
    """
    for _ in range(scope.MAX_UNREADABLE):
        scope._health.unreadable_answer("mumble")
    assert scope._health.broken is True
    degraded.reset()  # the turns that tripped it are over; this is the one after.

    verdict = scope.start("add a settings page", gateway=Gateway("PLAN"),
                          catalog=CATALOG).result()
    assert verdict is False  # unchanged behaviour: a broken gate builds, as it did before it existed
    assert degraded.count() == 1


def test_a_granted_turn_starts_the_count_at_zero(tmp_path, monkeypatch):
    """The reset is at the GRANT, beside the resolved-model record and for the same argument: a turn
    that has not started must not inherit the previous turn's answer."""
    from sage.gateway.client import FakeGatewayClient
    from sage.orchestrator.service import Orchestrator

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    (template / "AGENTS.md").write_text("# Template rules\n")
    orch = Orchestrator(workspace_dir=tmp_path / "mnt" / "code", template=template,
                        gateway=FakeGatewayClient(), catalog=CATALOG, project_id="Sage")
    orch.project(start_preview=False)

    _intent("not json at all")
    assert degraded.count() == 1
    orch._begin_model_record()
    assert degraded.count() == 0


# ---- the surface, and the plant that says it is not switchable ---------------------------------


@pytest.fixture
def diag(tmp_path, monkeypatch):
    """GET /api/diag with HOME pointed somewhere empty, so no real config is read."""
    home = tmp_path / "home"
    (home / ".config" / "opencode").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SAGE_CONTROL_PORT", "1")

    def _read() -> dict:
        r = TestClient(app_module.control_app).get("/api/diag")
        assert r.status_code == 200, r.text
        return r.json()
    return _read


def test_diag_reports_the_count_with_timing_switched_off(diag, monkeypatch):
    """THE PLANT, and it is two plants in one call.

    `SAGE_TIMING=0` is set first and `timing.enabled()` is asserted False, so the test cannot pass
    by having failed to switch anything off. Then the count has to survive it. The timing ledger
    no-ops under that flag — `/api/diag/timing` goes empty — and whether a turn lost its judgements
    must not go with it. Same reasoning `app._resolved` gives for keeping the Project's copy of the
    resolved model out of the ledger.
    """
    monkeypatch.setenv("SAGE_TIMING", "0")
    assert timing.enabled() is False

    assert diag()["classifier_degradations"] == 0
    _intent("not json at all")
    _handoff("")
    assert diag()["classifier_degradations"] == 2


def test_diag_reports_the_count_with_timing_on(diag, monkeypatch):
    """The other side of the plant: the flag moves the ledger and moves nothing here."""
    monkeypatch.setenv("SAGE_TIMING", "1")
    assert timing.enabled() is True
    _intent("not json at all")
    assert diag()["classifier_degradations"] == 1
