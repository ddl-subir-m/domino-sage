"""A Build follow-up that changes a query an existing screen reads puts it back, or says which screen
it changed (#745).

Live, 2026-10-09 (haiku, "Signal Room 24", main e10172cb): prompt 8 asked for a Usage drift tab, and
the build rewrote the shared `open_deals` query in `.sage/queries.json` for it. The page check then
caught "query open_deals has no column 'LAST_STAGE_CHANGE_IN_DAYS'" on the Pipeline overview, and
the runtime repair rewrote `PipelineOverview.js` to fit the new query: open pipeline $0 over closed
and disqualified deals, and nothing told the person an existing tab had changed.

The catalog is one file every screen reads, and the turn kept its start text only to put back a
query it removed (#721). Now the names each running script calls are kept too, so a query whose
entry the turn changed and which a screen read when the turn began is caught: once before the page
check, with the entry as it was; again in the runtime repair, which is told to put the query back
rather than rewrite the screen; and if it is still changed when the turn ends, the person is told
which screens read it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.feedback.runner import changed_shared_queries, query_readers
from sage.orchestrator.service import _PERSISTED_EVENTS
from sage.resources.app_helpers import FASTAPI
from sage.resources.app_helpers import TEMPLATE as TEMPLATE_NAMES
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.bound_schema import BoundSource, agents_block

from .fake_opencode import Turn
from .test_a_query_the_app_still_calls_stays_in_its_catalog import (
    CATALOG,
    ROOT,
    _fastapi,
    _query,
    _screen_js,
)
from .test_controlled_build_faults_on_each_stack import _run, selected_stack  # noqa: F401

OVERVIEW = "static/components/PipelineOverview.js"
DRIFT = "static/components/UsageDrift.js"
OPEN_DEALS = _query("open_deals", "SELECT TEAM, STAGE, AMOUNT FROM deals WHERE IS_OPEN")
REWRITTEN = _query("open_deals",
                   "SELECT OPPORTUNITY_OWNER_NAME, STAGE_NAME, LAST_STAGE_CHANGE_IN_DAYS FROM deals")
DRIFT_QUERIES = [_query(q) for q in ("drift_quantiles", "drift_bins", "drift_trend")]


# ---- which queries a turn changed under a screen that read them ----------------------------------


def _app(tmp_path: Path) -> Path:
    return _fastapi(tmp_path, {"PipelineOverview": ["open_deals", "pipeline_by_team"]},
                    [OPEN_DEALS, _query("pipeline_by_team")])


def test_the_live_rewrite_names_the_query_and_the_screen_that_read_it(tmp_path: Path):
    app = _app(tmp_path)
    before, readers = (app / CATALOG).read_text(), query_readers(app)
    (app / CATALOG).write_text(json.dumps([REWRITTEN, _query("pipeline_by_team")] + DRIFT_QUERIES))

    [edit] = changed_shared_queries(app, before, readers)

    assert (edit.name, edit.readers, edit.before) == ("open_deals", [OVERVIEW], OPEN_DEALS)


def test_queries_the_turn_only_added_are_not_named(tmp_path: Path):
    app = _app(tmp_path)
    before, readers = (app / CATALOG).read_text(), query_readers(app)
    (app / CATALOG).write_text(json.dumps([OPEN_DEALS, _query("pipeline_by_team")] + DRIFT_QUERIES))

    assert changed_shared_queries(app, before, readers) == []


def test_an_entry_written_again_in_another_layout_is_not_a_change(tmp_path: Path):
    app = _app(tmp_path)
    before, readers = (app / CATALOG).read_text(), query_readers(app)
    entry = dict(reversed(list(OPEN_DEALS.items())))
    (app / CATALOG).write_text(json.dumps([_query("pipeline_by_team"), entry], indent=4))

    assert changed_shared_queries(app, before, readers) == []


def test_a_query_no_screen_read_when_the_turn_began_is_the_turns_to_change(tmp_path: Path):
    """Read only by the screen this turn wrote, so no existing screen depends on it."""
    app = _fastapi(tmp_path, {"PipelineOverview": ["pipeline_by_team"]},
                   [OPEN_DEALS, _query("pipeline_by_team")])
    before, readers = (app / CATALOG).read_text(), query_readers(app)
    (app / CATALOG).write_text(json.dumps([REWRITTEN, _query("pipeline_by_team")]))
    (app / DRIFT).write_text(_screen_js("UsageDrift", ["open_deals"]))

    assert changed_shared_queries(app, before, readers) == []


@pytest.mark.parametrize("current", ["{not json", '{"open_deals": {}}', None],
                         ids=["invalid", "object", "deleted"])
def test_a_catalog_it_cannot_read_names_nothing(tmp_path: Path, current: str | None):
    app = _app(tmp_path)
    before, readers = (app / CATALOG).read_text(), query_readers(app)
    if current is None:
        (app / CATALOG).unlink()
    else:
        (app / CATALOG).write_text(current)

    assert changed_shared_queries(app, before, readers) == []


def test_every_running_screen_that_calls_a_query_is_a_reader(tmp_path: Path):
    app = _fastapi(tmp_path, {"PipelineOverview": ["open_deals"], "DealDetail": ["open_deals"],
                              "Unloaded": ["open_deals"]},
                   [OPEN_DEALS], loaded=["PipelineOverview", "DealDetail"])

    assert query_readers(app) == {"open_deals": ["static/components/DealDetail.js", OVERVIEW]}


# ---- the Build prompt says so --------------------------------------------------------------------


def _block(names) -> str:
    binding = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                      "DWH", "MARTS", None, "SnowflakeConfig")
    return " ".join(agents_block([BoundSource(binding, [], [], None)], [], 5000, names=names).split())


@pytest.mark.parametrize("names", [FASTAPI, TEMPLATE_NAMES], ids=["fastapi-antd", "react-vite"])
def test_the_data_source_section_says_a_query_another_screen_reads_is_not_rewritten(names):
    block = _block(names)
    rule = block.split("**A query is shared by every screen that calls it.**")[1]
    assert "find every file that calls it by name" in rule
    assert "add a new query under a new name" in rule


@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_the_static_prompt_says_a_follow_up_leaves_another_screens_query_alone(stack):
    text = " ".join((ROOT / "template" / stack / "AGENTS.md").read_text().split())
    rule = text.split("A follow-up changes only the files its request is about")[1][:600]
    assert "a query in `.sage/queries.json` that another screen calls" in rule


# ---- a build turn --------------------------------------------------------------------------------


def _seed(app) -> Path:
    root = app.project.workspace.path
    (root / OVERVIEW).write_text(_screen_js("PipelineOverview", ["open_deals"]))
    index = root / "static" / "index.html"
    index.write_text(index.read_text().replace(
        '  <script src="static/components/MainScreen.js"></script>\n',
        f'  <script src="{OVERVIEW}"></script>\n'))
    (root / "static" / "app.js").write_text(
        "ReactDOM.createRoot(document.getElementById('root'))"
        ".render(React.createElement(window.app.PipelineOverview));\n")
    (root / CATALOG).write_text(json.dumps([OPEN_DEALS, _query("spare")]))
    app.project.workspace.update_bindings(lambda _: [
        {"kind": "data_source", "id": "ds-dwh", "name": "warehouse",
         "connector_type": "SnowflakeConfig"}])
    return root


def _drift_turn(app, catalog: list[dict], calls: tuple[str, ...] = ("drift_bins",)) -> Turn:
    """The new tab, its script on the page, and the catalog it wrote."""
    index = (app.project.workspace.path / "static" / "index.html").read_text()
    return Turn(writes={
        DRIFT: _screen_js("UsageDrift", list(calls)), CATALOG: json.dumps(catalog),
        "static/index.html": index.replace(f'  <script src="{OVERVIEW}"></script>\n',
                                           f'  <script src="{OVERVIEW}"></script>\n'
                                           f'  <script src="{DRIFT}"></script>\n')})


def _page(app, crash: str | None = None):
    """The page reads `open_deals`; with `crash`, the first load throws that runtime error."""
    loads = []

    def report(event):
        loads.append(event["validationId"])
        app.orch.record_preview_ack(event["validationId"])
        path = "/api/queries/open_deals"
        context = app.orch.capture_preview_read(event["validationId"], path, kind="query")
        app.orch.record_platform_read_failure(200, path, context=context,
                                              body=b'{"columns": ["TEAM"], "rows": [["a"]]}')
        if crash and len(loads) == 1:
            app.orch.record_runtime_error(crash, validation_id=event["validationId"])
    return report


def _notices(app) -> list[dict]:
    return [e for e in app.project.workspace.read_history() if e["type"] == "shared-query-changed"]


@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_a_rewritten_shared_query_is_put_back_before_the_page_is_checked(selected_stack):  # noqa: F811
    app = selected_stack
    root = _seed(app)
    app.oc.turns.extend([
        _drift_turn(app, [REWRITTEN, _query("spare")] + DRIFT_QUERIES),
        Turn(text="Put it back", writes={CATALOG: json.dumps([OPEN_DEALS, _query("spare")]
                                                            + DRIFT_QUERIES)}),
    ])

    events, done = _run(app, _page(app))

    repair = app.oc.prompts[1]["text"]
    assert "`open_deals`" in repair and OVERVIEW in repair
    assert OPEN_DEALS["sql"] in repair and REWRITTEN["sql"] not in repair
    assert "new name" in repair and "Do not rewrite" in repair
    assert next(e["reason"] for e in events if e["type"] == "iterate").startswith(
        "a query another screen reads was changed")
    assert json.loads((root / CATALOG).read_text())[0] == OPEN_DEALS
    assert _notices(app) == []
    assert done["ok"] is True


@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_a_shared_query_still_changed_at_the_end_tells_the_person_which_screen(selected_stack):  # noqa: F811
    """The request may have asked for the change; then the turn keeps it, and says so."""
    app = selected_stack
    _seed(app)
    app.oc.turns.extend([
        _drift_turn(app, [REWRITTEN, _query("spare")] + DRIFT_QUERIES),
        Turn(text="The request asked for open_deals to change."),
    ])

    events, done = _run(app, _page(app))

    assert len(app.oc.prompts) == 2
    [notice] = _notices(app)
    assert notice["names"] == ["open_deals"]
    assert "`open_deals`" in notice["message"] and OVERVIEW in notice["message"]
    assert [e["message"] for e in events if e["type"] == "shared-query-changed"] == [
        notice["message"]]
    assert done["ok"] is True


@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_the_runtime_repair_is_told_to_restore_the_query_not_rewrite_the_screen(selected_stack):  # noqa: F811
    app = selected_stack
    root = _seed(app)
    app.oc.turns.extend([
        _drift_turn(app, [REWRITTEN, _query("spare")] + DRIFT_QUERIES),
        Turn(text="The request asked for it."),
        Turn(text="Restored", writes={CATALOG: json.dumps([OPEN_DEALS, _query("spare")]
                                                          + DRIFT_QUERIES)}),
    ])
    crash = ("query open_deals has no column 'TEAM'; columns are OPPORTUNITY_OWNER_NAME, "
             "STAGE_NAME, LAST_STAGE_CHANGE_IN_DAYS")

    _, done = _run(app, _page(app, crash))

    repair = app.oc.prompts[2]["text"]
    assert crash in repair
    assert "`open_deals`" in repair and OVERVIEW in repair and OPEN_DEALS["sql"] in repair
    assert "Do not rewrite" in repair
    assert json.loads((root / CATALOG).read_text())[0] == OPEN_DEALS
    assert _notices(app) == []
    assert done["ok"] is True


@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_a_query_only_the_new_screen_reads_is_changed_without_a_word(selected_stack):  # noqa: F811
    """`spare` was in the catalog, but no screen called it when the turn began."""
    app = selected_stack
    _seed(app)
    app.oc.turns.append(_drift_turn(
        app, [OPEN_DEALS, _query("spare", "SELECT 2 AS n")], calls=("spare",)))

    events, done = _run(app, _page(app))

    assert len(app.oc.prompts) == 1
    assert not any(e["type"] == "shared-query-changed" for e in events)
    assert done["ok"] is True


def test_the_notice_survives_a_reload():
    assert "shared-query-changed" in _PERSISTED_EVENTS


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_workbench_draws_the_notice():
    message = f"This turn changed `open_deals` in {CATALOG}, which {OVERVIEW} read before it."
    history = [{"type": "user", "text": "Add a Usage drift tab"},
               {"type": "shared-query-changed", "names": ["open_deals"], "message": message},
               {"type": "done", "ok": True, "decision": "build is clean"}]
    out = subprocess.run(["node", str(Path(__file__).parent / "js" / "build_events_harness.mjs")],
                         input=json.dumps({"history": history}), check=False,
                         capture_output=True, text=True, timeout=60)

    assert out.returncode == 0, out.stderr
    assert message in json.loads(out.stdout.strip().splitlines()[-1])["values"]
