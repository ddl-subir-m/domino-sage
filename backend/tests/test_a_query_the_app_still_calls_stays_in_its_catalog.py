"""A query the app still calls cannot leave `.sage/queries.json`, and a missing name is a catalog
fault, not a Data Source access problem (#721, instance #719).

Live, 2026-10-08 (haiku, "Signal Room 17"): prompt 8 wrote `.sage/queries.json` whole with its three
new queries, and the seven that Pipeline, Deal Detail and Product Insights call were gone. The turn
ended "2 queries failed while building: `open_deals` (Read failed (404; not_found_or_hidden).) ...
check access to the Data Sources" — two of the seven, the two a rendered screen hit, blamed on the
store. The store was never asked; the names were not in the catalog.

Three halves. `SAGE007` is the static check: every literal name the app's running scripts pass to
`runQuery` / `useQuery` is in the catalog. `restore_removed_queries` puts back, before every
end-of-turn check, each query the turn removed that the app still calls — it only ever adds a name
the catalog no longer has, so it cannot override an edit to that query. And the failed-query notice
tells a name the catalog lacks from a read the store refused.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sage.feedback.runner import restore_removed_queries
from sage.orchestrator.service import _PERSISTED_EVENTS, Orchestrator

from .fake_opencode import Turn
from .test_a_view_state_field_without_a_type_fails_the_check import ROOT, _check
from .test_controlled_build_faults_on_each_stack import _ack, _run, selected_stack  # noqa: F401

# The run's own names: the seven prompt 7 wrote, and the three prompt 8 wrote whole over them.
KEPT = ["drift_quantiles", "drift_bins", "drift_trend"]
LOST = ["pipeline_by_stage_and_team", "open_deals", "deal_by_id", "feature_adoption_by_period",
        "customer_topics_by_period", "competitor_mentions_by_period", "accounts_with_usage_drops"]
SCREENS = {
    "Pipeline": ["pipeline_by_stage_and_team", "open_deals"],
    "DealDetail": ["deal_by_id"],
    "ProductInsights": ["feature_adoption_by_period", "customer_topics_by_period",
                        "competitor_mentions_by_period", "accounts_with_usage_drops"],
    "UsageDrift": KEPT,
}
CATALOG = ".sage/queries.json"


def _query(name: str, sql: str = "") -> dict:
    return {"name": name, "sql": sql or f"SELECT * FROM {name}"}


def _screen_js(name: str, calls: list[str]) -> str:
    reads = ", ".join(f"sage.useQuery('{q}')" for q in calls)
    return ("(function () {\n"
            "  window.app = window.app || {};\n"
            f"  window.app.{name} = function {name}() {{\n"
            f"    const reads = [{reads}];\n"
            "    return React.createElement('main', null, reads.map((r) => r.status).join(','));\n"
            "  };\n"
            "})();\n")


def _index(scripts: list[str]) -> str:
    tags = "".join(f'  <script src="{s}"></script>\n' for s in scripts)
    return ("<!DOCTYPE html>\n<html><body>\n  <div id=\"root\"></div>\n"
            '  <script src="static/sage/appQuery.js"></script>\n'
            f"{tags}"
            '  <script src="static/app.js"></script>\n</body></html>\n')


def _fastapi(tmp_path: Path, screens: dict[str, list[str]], catalog: list[dict] | None,
             *, loaded: list[str] | None = None) -> Path:
    """A fastapi-antd app whose page loads one script per screen (all of them unless `loaded`)."""
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "settings.json").write_text('{"stack": "fastapi-antd"}')
    (tmp_path / "static" / "components").mkdir(parents=True)
    (tmp_path / "app.py").write_text("x = 1\n")
    for name, calls in screens.items():
        (tmp_path / "static" / "components" / f"{name}.js").write_text(_screen_js(name, calls))
    scripts = [f"static/components/{n}.js" for n in (loaded if loaded is not None else screens)]
    (tmp_path / "static" / "index.html").write_text(_index(scripts))
    (tmp_path / "static" / "app.js").write_text(
        "ReactDOM.createRoot(document.getElementById('root'))"
        ".render(React.createElement(window.app.Pipeline));\n")
    if catalog is not None:
        (tmp_path / CATALOG).write_text(json.dumps(catalog))
    return tmp_path


def _missing(report) -> list[tuple[str, int, str]]:
    return [(e.file, e.line, e.code) for e in report.errors if e.code == "SAGE007"]


# ---- SAGE007: every name the app calls is in its catalog -----------------------------------------


def test_the_live_catalog_fails_naming_every_called_name_it_lost(tmp_path: Path, monkeypatch):
    """The instance: the catalog holds only the three, and the check names all seven — the four on
    Product Insights too, which no rendered screen hit and the live notice never named."""
    app = _fastapi(tmp_path, SCREENS, [_query(q) for q in KEPT])

    report = _check(app, monkeypatch)

    assert not report.ok
    named = [e.message.split("'")[1] for e in report.errors if e.code == "SAGE007"]
    assert sorted(named) == sorted(LOST)
    assert ("static/components/ProductInsights.js", 4, "SAGE007") in _missing(report)
    for e in report.errors:
        assert CATALOG in e.message and "This app has no query called" in e.message


def test_a_name_never_in_the_catalog_claims_no_removal_and_gives_no_access_advice(
        tmp_path: Path, monkeypatch):
    app = _fastapi(tmp_path, {"Pipeline": ["open_deals", "pipeline_by_stage"]},
                   [_query("pipeline_by_stage")])

    report = _check(app, monkeypatch)

    assert _missing(report) == [("static/components/Pipeline.js", 4, "SAGE007")]
    message = report.errors[0].message.lower()
    assert "'open_deals'" in message
    assert "removed" not in message
    assert "access" not in message and "data source" not in message


@pytest.mark.parametrize("call", [
    "sage.useQuery('drift_' + period)",
    "sage.useQuery(name)",
    "sage.useQuery(`drift_${period}`)",
    "sage.runQuery(names[0], {})",
], ids=["concatenated", "variable", "template-literal", "indexed"])
def test_a_name_built_at_run_time_is_not_guessed_at(tmp_path: Path, monkeypatch, call: str):
    app = _fastapi(tmp_path, {"Pipeline": []}, [_query("drift_bins")])
    screen = app / "static" / "components" / "Pipeline.js"
    screen.write_text(screen.read_text().replace("const reads = [];", f"const reads = [{call}];"))

    assert _missing(_check(app, monkeypatch)) == []


@pytest.mark.parametrize("call", [
    "sage.runQuery('drift_bins', { since })",
    'sage.useQuery("drift_bins")',
    "sage.useQuery(`drift_bins`, {}, { enabled })",
    "sage.useQuery(\n      'drift_bins'\n    )",
], ids=["runQuery", "double-quote", "plain-template-literal", "multiline"])
def test_every_literal_call_shape_is_read(tmp_path: Path, monkeypatch, call: str):
    app = _fastapi(tmp_path, {"Pipeline": []}, [_query("other")])
    screen = app / "static" / "components" / "Pipeline.js"
    screen.write_text(screen.read_text().replace("const reads = [];", f"const reads = [{call}];"))

    report = _check(app, monkeypatch)

    assert [e.message.split("'")[1] for e in report.errors if e.code == "SAGE007"] == ["drift_bins"]


@pytest.mark.parametrize("text", [
    "    // sage.useQuery('old_name') was the first version\n",
    "    /* sage.runQuery('old_name') */\n",
    "    const hint = \"call sage.useQuery('old_name')\";\n",
], ids=["line-comment", "block-comment", "string"])
def test_a_name_only_mentioned_is_not_a_call(tmp_path: Path, monkeypatch, text: str):
    app = _fastapi(tmp_path, {"Pipeline": []}, [])
    screen = app / "static" / "components" / "Pipeline.js"
    screen.write_text(screen.read_text().replace("    const reads", text + "    const reads"))

    assert _missing(_check(app, monkeypatch)) == []


def test_a_script_the_page_does_not_load_asks_for_nothing(tmp_path: Path, monkeypatch):
    """What never runs cannot be refused: the template's own example screen ships in every app and
    calls `example_*` names no app declares."""
    app = _fastapi(tmp_path, {"Pipeline": ["open_deals"], "Old": ["retired_query"]},
                   [_query("open_deals")], loaded=["Pipeline"])

    assert _missing(_check(app, monkeypatch)) == []


def test_a_catalog_that_is_not_a_list_says_so_in_each_error(tmp_path: Path, monkeypatch):
    app = _fastapi(tmp_path, {"Pipeline": ["open_deals"]}, None)
    (app / CATALOG).write_text(json.dumps({"open_deals": {"sql": "SELECT 1"}}))

    report = _check(app, monkeypatch)

    assert _missing(report) == [("static/components/Pipeline.js", 4, "SAGE007")]
    assert "not a JSON list" in report.errors[0].message


def _react(tmp_path: Path, screens: dict[str, list[str]], catalog: list[dict]) -> Path:
    app = tmp_path / "app"
    (app / ".sage").mkdir(parents=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": "react-vite"}))
    (app / "src" / "screens").mkdir(parents=True)
    shutil.copy(ROOT / "template/react-vite/src/appQuery.ts", app / "src" / "appQuery.ts")
    imports = "".join(f'import {n} from "./screens/{n}";\n' for n in screens)
    (app / "src" / "App.tsx").write_text(
        imports + "export default function App() {\n  return <main>"
        + "".join(f"<{n} />" for n in screens) + "</main>;\n}\n")
    for name, calls in screens.items():
        reads = ", ".join(f'useQuery<Row>("{q}")' for q in calls)
        (app / "src" / "screens" / f"{name}.tsx").write_text(
            'import { useQuery } from "../appQuery";\ntype Row = { n: number };\n'
            f"export default function {name}() {{\n  const reads = [{reads}];\n"
            "  return <div>{reads.length}</div>;\n}\n")
    (app / CATALOG).write_text(json.dumps(catalog))
    return app


def test_react_vite_reads_the_screens_the_app_imports(tmp_path: Path, monkeypatch):
    app = _react(tmp_path, {"Pipeline": ["open_deals", "drift_bins"]}, [_query("drift_bins")])
    # Imported by nothing, so it never runs: the template's own examples are this shape.
    (app / "src" / "examples").mkdir()
    (app / "src" / "examples" / "useOrders.ts").write_text(
        'import { useQuery } from "../appQuery";\nexport const o = () => useQuery("example_orders");\n')

    report = _check(app, monkeypatch)

    assert _missing(report) == [("src/screens/Pipeline.tsx", 4, "SAGE007")]
    assert "'open_deals'" in report.errors[0].message


@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_a_new_app_from_either_template_calls_no_missing_query(tmp_path: Path, monkeypatch, stack):
    app = tmp_path / "app"
    shutil.copytree(ROOT / "template" / stack, app, ignore=shutil.ignore_patterns("node_modules"))
    (app / ".sage").mkdir(exist_ok=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": stack}))

    assert _missing(_check(app, monkeypatch)) == []


# ---- the turn's removal is put back --------------------------------------------------------------


def test_the_live_rewrite_gets_the_seven_back_and_keeps_the_three(tmp_path: Path):
    app = _fastapi(tmp_path, SCREENS, None)
    before = json.dumps([_query(q) for q in LOST + KEPT])
    written = [_query(q, f"SELECT new FROM {q}") for q in KEPT]
    (app / CATALOG).write_text(json.dumps(written))

    restored = restore_removed_queries(app, before)

    assert restored == LOST
    after = json.loads((app / CATALOG).read_text())
    assert after[:3] == written
    assert after[3:] == [_query(q) for q in LOST]


def test_a_query_the_turn_removed_with_its_callers_stays_removed(tmp_path: Path):
    app = _fastapi(tmp_path, {"Pipeline": ["open_deals"]}, [_query("open_deals")])
    before = json.dumps([_query("open_deals"), _query("retired")])

    assert restore_removed_queries(app, before) == []
    assert json.loads((app / CATALOG).read_text()) == [_query("open_deals")]


def test_a_query_the_turn_edited_keeps_the_edit(tmp_path: Path):
    edited = _query("open_deals", "SELECT id FROM deals WHERE open")
    app = _fastapi(tmp_path, {"Pipeline": ["open_deals"]}, [edited])

    assert restore_removed_queries(app, json.dumps([_query("open_deals")])) == []
    assert json.loads((app / CATALOG).read_text()) == [edited]


def test_a_deleted_catalog_gets_the_called_queries_back(tmp_path: Path):
    app = _fastapi(tmp_path, {"Pipeline": ["open_deals"]}, None)

    assert restore_removed_queries(app, json.dumps([_query("open_deals"), _query("x")])) == ["open_deals"]
    assert json.loads((app / CATALOG).read_text()) == [_query("open_deals")]


@pytest.mark.parametrize("current", ["{not json", '{"open_deals": {}}'], ids=["invalid", "object"])
def test_a_catalog_it_cannot_read_is_never_written_over(tmp_path: Path, current: str):
    app = _fastapi(tmp_path, {"Pipeline": ["open_deals"]}, None)
    (app / CATALOG).write_text(current)

    assert restore_removed_queries(app, json.dumps([_query("open_deals")])) == []
    assert (app / CATALOG).read_text() == current


# ---- the person is told the right cause ----------------------------------------------------------


def test_a_name_the_catalog_lacks_is_a_catalog_fault_without_access_advice():
    failed = {"open_deals": "Read failed (404; not_found_or_hidden).",
              "pipeline_by_stage_and_team": "Read failed (404; not_found_or_hidden)."}

    message = Orchestrator._failed_notice(failed, {"drift_bins"})

    assert "no query called" in message
    assert "`open_deals`" in message and "`pipeline_by_stage_and_team`" in message
    assert "not_found_or_hidden" not in message
    assert "access" not in message.lower()


def test_a_read_the_store_refused_keeps_its_reason_beside_a_missing_name():
    """The discriminator: a name the catalog holds is still the store's answer, said as before."""
    failed = {"open_deals": "Read failed (404; not_found_or_hidden).",
              "drift_bins": "Object 'GONG' does not exist or not authorized"}

    message = Orchestrator._failed_notice(failed, {"drift_bins"})

    assert "`open_deals`" in message.split("no query called")[1]
    assert "GONG" in message and "access" in message.lower()


# ---- a build turn: restored in-turn, and checked again after every repair ------------------------


def _seed(app, catalog: list[dict]) -> Path:
    root = app.project.workspace.path
    for name, calls in SCREENS.items():
        (root / "static" / "components" / f"{name}.js").write_text(_screen_js(name, calls))
    index = root / "static" / "index.html"
    tags = "".join(f'  <script src="static/components/{n}.js"></script>\n' for n in SCREENS)
    index.write_text(index.read_text().replace(
        '  <script src="static/components/MainScreen.js"></script>\n', tags))
    (root / "static" / "app.js").write_text(
        "ReactDOM.createRoot(document.getElementById('root'))"
        ".render(React.createElement(window.app.Pipeline));\n")
    (root / CATALOG).write_text(json.dumps(catalog))
    return root


@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_a_build_that_wrote_the_catalog_whole_ends_with_every_called_query(selected_stack):  # noqa: F811
    app = selected_stack
    root = _seed(app, [_query(q) for q in LOST + KEPT])
    app.oc.turns.append(Turn(writes={CATALOG: json.dumps([_query(q) for q in KEPT])}))

    events, done = _run(app, _ack(app))

    assert {q["name"] for q in json.loads((root / CATALOG).read_text())} == set(LOST + KEPT)
    [restored] = [e for e in events if e["type"] == "queries-restored"]
    assert restored["names"] == LOST
    assert "removed" in restored["message"] and all(f"`{q}`" in restored["message"] for q in LOST)
    assert [e["names"] for e in app.project.workspace.read_history()
            if e["type"] == "queries-restored"] == [LOST]
    assert done["ok"] is True


@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_a_repair_turn_is_checked_for_the_catalog_too(selected_stack):  # noqa: F811
    """The repair of a syntax error is the turn that drops the queries and calls a new one — the
    shape #720 names, a repair checked only for what it was told to fix."""
    app = selected_stack
    _seed(app, [_query(q) for q in LOST + KEPT])
    pipeline = "static/components/Pipeline.js"
    app.oc.turns.extend([
        Turn(writes={"app.py": "def broken(:\n    pass\n"}),
        Turn(writes={"app.py": "x = 1\n", CATALOG: json.dumps([_query(q) for q in KEPT]),
                     pipeline: _screen_js("Pipeline", SCREENS["Pipeline"] + ["stage_history"])}),
        Turn(text="Added it", writes={CATALOG: json.dumps(
            [_query(q) for q in KEPT + LOST] + [_query("stage_history")])}),
    ])

    events, done = _run(app, _ack(app))

    assert [e["ok"] for e in events if e["type"] == "typecheck"] == [False, False, True]
    repair = app.oc.prompts[2]["text"]
    assert "SAGE007" in repair and "'stage_history'" in repair
    assert [e["names"] for e in events if e["type"] == "queries-restored"] == [LOST]
    assert done["ok"] is True


def test_the_restore_notice_survives_a_reload():
    assert "queries-restored" in _PERSISTED_EVENTS
