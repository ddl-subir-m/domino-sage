"""A query naming a Data Source the app does not record is a failed check, not a clean build (#704).

Live: an app recording no Data Source shipped three queries naming `mixpanel_events`, a source the
model made up. The app refuses every one of them with a 422, and the build still ended
`typecheck clean` with data `not_applicable`, because data was judged against the app's empty
Bindings and its catalog was never asked. The app's own catalog already says why, in the sentence
the viewer would be shown; this sends that back for one repair and fails the data check after it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sage.resources import builtapp

from . import test_changed_page_validation as page_tests

build = page_tests.build
run = page_tests.run

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"
INVENTED = ("The query mixpanel_events_page reads the Data Source mixpanel_events, which this app "
            "is not recorded as using.")


@pytest.fixture(autouse=True)
def _fresh_modules(build, monkeypatch):
    # After `build`, for the reason test_a_query_the_catalog_refuses_says_why gives.
    monkeypatch.setattr(builtapp, "_loaded", {})


def _app(tmp_path: Path, project, binding: str, bound: list[dict]) -> None:
    shutil.copy(TEMPLATE / "sage_queries.py", tmp_path / "template" / "sage_queries.py")
    project.workspace.update_bindings(lambda _: bound)
    sage = project.workspace.path / ".sage"
    sage.mkdir(parents=True, exist_ok=True)
    (sage / "queries.json").write_text(json.dumps([
        {"name": "mixpanel_events_page", "binding": binding,
         "sql": "SELECT * FROM DWH.MARTS.MIXPANEL__EVENT"}]), encoding="utf-8")


def _ack(orch):
    return lambda event: orch.record_preview_ack(event["validationId"])


def test_a_query_naming_an_unrecorded_source_is_sent_back_and_then_fails(build, tmp_path):
    orch, project, oc = build
    _app(tmp_path, project, "mixpanel_events", [])

    events, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 2, "the turn was not sent back for a repair"
    assert INVENTED in oc.prompts[-1]["text"]
    assert done["ok"] is False
    assert done["decision"] == "queries failed"
    assert done["verification"]["stages"]["data"] == "failed"
    assert INVENTED in next(e for e in events if e["type"] == "data-source-failed")["message"]


def test_a_query_naming_the_recorded_source_is_not_sent_back(build, tmp_path):
    orch, project, oc = build
    _app(tmp_path, project, "ds-dwh", [{"kind": "data_source", "id": "ds-dwh",
                                        "name": "warehouse", "connector_type": "SnowflakeConfig"}])

    events, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 1
    assert not [e for e in events if e["type"] == "data-source-failed"]
    assert done["verification"]["stages"]["data"] == "unverified"
