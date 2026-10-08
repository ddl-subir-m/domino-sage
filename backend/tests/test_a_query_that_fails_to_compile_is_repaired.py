"""A query the store refused to compile is sent back for a bounded repair (#708).

Live, Signal Room on Haiku: `open_deals` selected `EXPECTED_ARR` from a table that has no such
column, the preview ran it, Snowflake answered `000904 ... invalid identifier 'EXPECTED_ARR'`, and
the turn ended "queries failed". The store's own sentence named the fix, and the person had to
copy it into a prompt themselves.

A compile error is the app's own SQL and fails the same way every time, so it is repairable.
A store that is down, or refuses the person access, is not, and keeps #203's notice-only ending.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sage.orchestrator.query_faults import is_compile_fault

from .fake_opencode import Turn
from .test_a_build_whose_queries_all_fail_is_not_clean import FakeQueries, _catalog, _of, _orch
from .test_changed_page_validation import Preview

INVALID = ("DominoError: Flight returned invalid argument error, with message: 000904 (42000): "
           "SQL compilation error:\nerror line 1 at position 7 invalid identifier 'EXPECTED_ARR'")
DOWN = "DominoError: Flight returned unavailable error, with message: failed to connect"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    from sage.orchestrator.service import Orchestrator
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _ready(orch, answers: list):
    """Bind one table, write a query that reads it, and hand each validation the next answer.

    `answers[i]` is what the store said on the i-th validation of the turn: a reason, or None for a
    read that succeeded. The last one repeats, so a query that keeps failing keeps failing.
    """
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_SUBSCRIPTION_REVENUE")
    _catalog(orch, [{"name": "open_deals", "binding": "ds-dwh",
                     "sql": "SELECT EXPECTED_ARR FROM FCT_SUBSCRIPTION_REVENUE"}])
    project = orch.project(start_preview=False)
    project.queries = FakeQueries({})
    project.supervisor = Preview(project.workspace.app_id)
    orch._build_policy = replace(orch._build_policy, page_ack_wait_seconds=0.5)
    orch._restart_preview_for_config_change = lambda project: None
    validate = orch._validate_page
    passes = []

    def report(project, kind):
        answer = answers[min(len(passes), len(answers) - 1)]
        passes.append(answer)
        project.queries.answer = {"open_deals": answer} if answer else {}
        for event in validate(project, kind):
            orch.record_preview_ack(event["validationId"])
            context = orch.capture_preview_read(event["validationId"], "/api/queries/open_deals",
                                                kind="query")
            orch.record_platform_read_failure(503 if answer else 200, "/api/queries/open_deals",
                                              context=context, body=b'{"rows":[]}')
            yield event
        return project.page_validation
    orch._validate_page = report
    return passes


def _turns(n: int) -> list[Turn]:
    return [Turn(writes={"src/App.tsx": f"x{i}\n"}) for i in range(n)]


def test_a_query_that_fails_to_compile_is_repaired_once(tmp_path: Path):
    orch = _orch(tmp_path, _turns(2))
    passes = _ready(orch, [INVALID, None])

    events = list(orch.build_stream("build it"))

    oc = orch._oc_client
    assert len(oc.prompts) == 2, "the compile error was not sent back for a repair"
    assert len(passes) == 2, "the repair was not validated again"
    done = _of(events, "done")[0]
    assert done["ok"] is True, done
    assert not _of(events, "data-source-failed")


def test_the_repair_names_the_query_the_store_message_and_the_real_columns(tmp_path: Path):
    orch = _orch(tmp_path, _turns(2))
    _ready(orch, [INVALID, None])

    list(orch.build_stream("build it"))

    nudge = orch._oc_client.prompts[-1]["text"]
    assert "open_deals" in nudge
    assert "invalid identifier 'EXPECTED_ARR'" in nudge
    assert "FCT_SUBSCRIPTION_REVENUE" in nudge
    assert "ARR_USD" in nudge and "ACCOUNT_ID" in nudge


def test_a_store_that_is_down_gets_no_repair_and_ends_queries_failed(tmp_path: Path):
    orch = _orch(tmp_path, _turns(2))
    _ready(orch, [DOWN])

    events = list(orch.build_stream("build it"))

    assert len(orch._oc_client.prompts) == 1
    done = _of(events, "done")[0]
    assert done["ok"] is False
    assert done["decision"] == "queries failed"
    assert "failed to connect" in _of(events, "data-source-failed")[0]["message"]


def test_a_query_that_keeps_failing_to_compile_stops_at_the_limit(tmp_path: Path):
    orch = _orch(tmp_path, _turns(5))
    _ready(orch, [INVALID])
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=2)

    events = list(orch.build_stream("build it"))

    assert len(orch._oc_client.prompts) == 3, "not bounded at the limit"
    done = _of(events, "done")[0]
    assert done["ok"] is False
    assert done["decision"] == "queries failed"


# ---- the classifier ---------------------------------------------------------------------------

BOUND = {"FCT_SUBSCRIPTION_REVENUE"}


@pytest.mark.parametrize("message", [
    INVALID,
    "001003 (42000): SQL compilation error:\nsyntax error line 1 at position 9 unexpected 'FORM'.",
    ("002003 (42S02): SQL compilation error:\nObject 'DWH.MARTS.OPEN_DEALS' does not exist or not "
     "authorized."),
])
def test_the_apps_own_sql_is_a_compile_fault(message):
    assert is_compile_fault(message, BOUND)


@pytest.mark.parametrize("message", [
    DOWN,
    "DominoError: Flight returned unauthenticated error, with message: no credentials for user",
    "DominoError: Flight returned deadline exceeded error, with message: timed out",
    "Read failed (503; http_error).",
    # A bound table the store will not show this person is a permission refusal, not a typo.
    ("002003 (42S02): SQL compilation error:\nObject 'DWH.MARTS.FCT_SUBSCRIPTION_REVENUE' does "
     "not exist or not authorized."),
])
def test_a_fault_outside_the_apps_sql_is_not(message):
    assert not is_compile_fault(message, BOUND)


def test_a_missing_object_with_no_recorded_tables_is_not_judged_a_typo():
    assert not is_compile_fault(
        "002003 (42S02): SQL compilation error:\nObject 'GONG' does not exist or not authorized.",
        set())
