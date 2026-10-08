"""App code that reads a store itself, outside `.sage/queries.json`, is not a clean build (#705).

Live: an app recording no Data Source added its own `/api/...` routes in `app.py` calling
`domino_data.data_sources.DataSourceClient().query_sql(...)`, a method that does not exist. The
routes swallowed the error and answered 200 with a count of 0, and the build ended
`typecheck clean` with data `not_applicable`, because data was judged only by the app's Bindings
and its catalog, and it had neither. This sends that code back for one repair and fails the data
check if it is still there.
"""
from __future__ import annotations

from . import test_changed_page_validation as page_tests

build = page_tests.build
run = page_tests.run

DIRECT = ("from domino_data.data_sources import DataSourceClient\n\n"
          "def event_count():\n"
          "    return DataSourceClient().query_sql('SELECT COUNT(*) FROM DWH.MARTS.MIXPANEL__EVENT')\n")


def _ack(orch):
    return lambda event: orch.record_preview_ack(event["validationId"])


def test_app_code_reading_a_store_directly_is_sent_back_and_then_fails(build):
    orch, project, oc = build
    (project.workspace.path / "app.py").write_text(DIRECT, encoding="utf-8")

    events, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 2, "the turn was not sent back for a repair"
    repair = oc.prompts[-1]["text"]
    assert "app.py" in repair and ".sage/queries.json" in repair and "useQuery" in repair
    assert done["ok"] is False
    assert done["decision"] == "queries failed"
    assert done["verification"]["stages"]["data"] == "failed"
    assert "app.py" in next(e for e in events if e["type"] == "data-source-failed")["message"]


def test_sages_own_files_that_use_domino_data_are_not_flagged(build):
    orch, project, oc = build
    root = project.workspace.path
    (root / "sage_queries.py").write_text(DIRECT, encoding="utf-8")
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "rehydrate_data.py").write_text(DIRECT, encoding="utf-8")

    events, done = run(orch, report=_ack(orch))

    assert len(oc.prompts) == 1
    assert not [e for e in events if e["type"] == "data-source-failed"]
    assert done["ok"] is True
    assert done["verification"]["stages"]["data"] == "not_applicable"
