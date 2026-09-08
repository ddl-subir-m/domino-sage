"""`Used by 1 app` outliving the app (#133's field, #161's rule, this time on delete).

Delete every Built App in a Project and keep the Resources, and the rail went on saying
`Used by 1 app` about an app nobody could open any more. Chat showed it plainly; Build hid it
behind `Not used by {app}`, which reads the selected app's own Bindings and was right — so the
two modes disagreed on screen about the same row.

The server was never wrong: `usedBy` is computed per read off the apps' own manifests, and a
deleted app's manifest goes with its directory. What was missing is the read. `removeBindingFromApp`
already re-reads the working set after an unbind for exactly this reason (#161); deleting an app
takes ALL of that app's Bindings away at once and took no such read, and binding one to an app that
already held the Resource in the rail did not take it either.

Two seams, the pair `test_where_this_is_used.py` uses:
  - Service-level, so the answer the rail is refreshed against is pinned as the true one.
  - Source assertions over the store, because the missing call is exactly what nothing catches.
"""

from __future__ import annotations

from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.router.models import ModelCatalog


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(workspace_dir=tmp / "mnt" / "code", template=template, gateway=object(),
                        catalog=_catalog(), project_id="Sage")
    orch.project(start_preview=False)
    return orch


def _selected(orch: Orchestrator):
    return orch.project(start_preview=False).workspace


def _new_app(orch: Orchestrator, name: str) -> str:
    app_id = orch._wm.create_app("Sage").app_id
    orch.select_app(app_id)
    orch.rename_app(app_id, name)
    return app_id


def _record(orch: Orchestrator, *bindings: Binding) -> None:
    """Straight into the SELECTED app's manifest, the shortcut `test_where_this_is_used.py` takes:
    `bind_data_source` validates the id against a Domino listing this orchestrator has no provider
    for, and what is under test is what the delete does to a recorded Binding."""
    _selected(orch).update_bindings(lambda _: [b.to_dict() for b in bindings])


def _source(schema: str) -> Binding:
    return Binding(KIND_DATA_SOURCE, "ds-1", "BigQuery_Demo", "BigQuery_Demo",
                   database="DWH", schema=schema, table=None,
                   connector_type="SnowflakeConfig")


def _add_source(orch: Orchestrator) -> None:
    orch.add_project_resource({
        "id": "data_source:ds-1", "kind": "datasource", "name": "BigQuery_Demo",
    })


def _row(orch: Orchestrator, rid: str) -> dict:
    return next(r for r in orch.list_project_resources() if r["id"] == rid)


def test_the_listing_forgets_an_app_the_moment_it_is_deleted(tmp_path: Path):
    """The manifest is inside the directory the delete removes, so one read later the Resource is
    used by nobody. This is the answer the rail has to be holding, and the only one it can get."""
    orch = _orch(tmp_path)
    _add_source(orch)
    first = _selected(orch).app_id
    orch.rename_app(first, "Desk exposure")
    _record(orch, _source("MARTS"))
    second = _new_app(orch, "Churn model")
    _record(orch, _source("SANDBOX"))
    assert len(_row(orch, "data_source:ds-1")["usedBy"]) == 2

    orch.delete_app(second)
    assert [e["name"] for e in _row(orch, "data_source:ds-1")["usedBy"]] == ["Desk exposure"]

    orch.delete_app(first)
    assert _row(orch, "data_source:ds-1")["usedBy"] == []


def test_the_resource_stays_in_the_project_when_the_last_app_that_used_it_goes(tmp_path: Path):
    """The creator's own case: delete the apps, keep the Resources. Membership is not a record of
    which app is alive, so the row stays — only what it says about use changes."""
    orch = _orch(tmp_path)
    _add_source(orch)
    _record(orch, _source("MARTS"))
    orch.delete_app(_selected(orch).app_id)

    row = _row(orch, "data_source:ds-1")
    assert row["name"] == "BigQuery_Demo"
    assert row["usedBy"] == []


# --- the acts that have to read it back -------------------------------------------------------

STORE = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "store.js").read_text()


def _act(name: str) -> str:
    body = STORE.split(f"async {name}(")[1]
    return body.split("\n    },")[0]


def test_deleting_an_app_re_reads_the_working_set():
    """`loadBuild` reads the app that is LEFT — the transcript, the Bindings and the preview of
    whichever app Build moved onto. `usedBy` is a question about all of them, so it needs the
    Project's own read or the rail keeps the count the deleted app was in."""
    assert "await refreshWorkingSet();" in _act("deleteApp")


def test_binding_a_resource_the_rail_already_holds_re_reads_it_too():
    """The other direction of the same disagreement. The refresh used to be skipped for a Resource
    already in the rail — true of membership, and false of `usedBy` since #133, which this act is
    exactly what changes. Skipped, a bind in Build left Chat saying `Used by 0` about a Resource an
    app had just started using."""
    act = _act("bindToApp")
    assert "await refreshWorkingSet();" in act
    assert "if (!state.resourceIndex[resource.id]) await refreshWorkingSet();" not in act


def test_answering_the_data_source_card_re_reads_it_as_well():
    """The third door onto the same list (#185). The card's click binds, and a bind joins the
    Resource to the Project — so this one had the count wrong AND the row missing: a Data Source
    named from a card stayed off the rail entirely while the build that followed used it."""
    assert "refreshWorkingSet()," in _act("chooseSourceAndSearch")


def test_setting_a_scope_re_reads_it_because_the_row_carries_one():
    """`usedBy` records the app AND the part of the Resource it reads, and the drawer prints both —
    so the door that moves a Scope has to read the Project's copy back. The Binding list the act
    writes is the app's answer and has never spoken for the Project's."""
    assert "await refreshWorkingSet();" in _act("saveScope")


def test_answering_the_table_card_re_reads_it_as_well():
    """The card writes the Scope the door above writes (#183), so it owes the same read."""
    assert "refreshWorkingSet()," in _act("chooseTableAndBuild")


def test_renaming_an_app_re_reads_the_name_the_rail_prints():
    """`usedBy` carries the app's NAME, not just its id, and the drawer and the `Remove from {app}`
    door both print it. The act already re-reads the two other places the name lands."""
    assert "refreshWorkingSet()" in _act("renameApp")
