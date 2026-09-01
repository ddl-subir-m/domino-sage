"""Where a Resource is used, in one look (#133, parent #132).

Three lists decide whether a Resource is reachable — Project membership, this Conversation's
context, and each Built App's Bindings — and until now only the first one was drawn anywhere. The
rail's `Used by N apps` subtitle was written into the UI and never had data behind it, and the
drawer said what a Resource IS without ever saying where it is used. The first anyone learned that
membership is not use was a removal refusal naming an app they had never thought about.

So the membership listing is enriched, per row, with every Built App that binds the Resource, its
name and its Scope. Deliberately the same scan `remove_project_resource` refuses on: the drawer has
to name the apps the refusal would, or the refusal is still a surprise.

Two seams, both already here:

- Service-level, against a temp project on disk, in the style of `test_project_resources.py`. That
  is where "two apps, two Scopes, one listing" is a behaviour rather than a shape.
- Source assertions over the Workbench JS, in the style of `test_workbench_composer_mention.py`.
  There is no DOM in this suite; what these buy is that the wiring cannot be unhooked by accident —
  which is exactly how the subtitle came to be drawn from a field nothing filled.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator, ResourceStillBound
from sage.resources.bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, Binding
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
    """Write the Bindings straight into the SELECTED app's manifest.

    The same shortcut `test_project_resources.py` takes: `bind_data_source` validates the id against
    the project's Domino listing, which this orchestrator has no provider for, and what is under
    test is what the listing says about a recorded Binding rather than the cascade that records it.
    """
    _selected(orch).update_bindings(lambda _: [b.to_dict() for b in bindings])


def _source(scope: tuple[str, str, str | None]) -> Binding:
    database, schema, table = scope
    return Binding(KIND_DATA_SOURCE, "ds-1", "BigQuery_Demo", "BigQuery_Demo",
                   database=database, schema=schema, table=table,
                   connector_type="SnowflakeConfig")


def _add_source(orch: Orchestrator) -> None:
    orch.add_project_resource({
        "id": "data_source:ds-1", "kind": "datasource", "name": "BigQuery_Demo",
    })


def _row(orch: Orchestrator, rid: str) -> dict:
    return next(r for r in orch.list_project_resources() if r["id"] == rid)


# --- the listing answers "where is this used" ------------------------------------------------


def test_two_apps_binding_one_source_both_reach_the_listing_with_their_scopes(tmp_path: Path):
    """The whole point of the row: a Project holds many Built Apps (ADR-0008), so "used" is a list
    and each entry needs its own Scope. Two apps reading two schemas of one warehouse is the normal
    case, and a row that named only the app in front of you would be the old bug in a new place."""
    orch = _orch(tmp_path)
    _add_source(orch)
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _record(orch, _source(("DWH", "MARTS", None)))
    _new_app(orch, "Churn model")
    _record(orch, _source(("DWH", "SANDBOX", "DIM_ACCOUNT")))

    assert _row(orch, "data_source:ds-1")["usedBy"] == [
        {"appId": orch._wm.app_ids()[0], "name": "Desk exposure", "scope": "DWH.MARTS"},
        {"appId": orch._wm.app_ids()[1], "name": "Churn model", "scope": "DWH.SANDBOX.DIM_ACCOUNT"},
    ]


def test_the_apps_come_back_oldest_first_whichever_one_is_selected(tmp_path: Path):
    """Same order as the removal refusal's, and it must not depend on where the creator is
    standing: the drawer is read from whichever app happens to be open."""
    orch = _orch(tmp_path)
    _add_source(orch)
    first = _selected(orch).app_id
    orch.rename_app(first, "Desk exposure")
    _record(orch, _source(("DWH", "MARTS", None)))
    _new_app(orch, "Churn model")
    _record(orch, _source(("DWH", "SANDBOX", None)))
    orch.select_app(first)

    assert [e["name"] for e in _row(orch, "data_source:ds-1")["usedBy"]] == [
        "Desk exposure", "Churn model",
    ]


def test_a_resource_no_app_binds_says_so_with_an_empty_list(tmp_path: Path):
    """Membership is not use, and this is the row that says so. The field is always present, so the
    drawer's empty state is a length rather than a missing key."""
    orch = _orch(tmp_path)
    _add_source(orch)
    assert _row(orch, "data_source:ds-1")["usedBy"] == []


def test_an_alias_records_no_scope_and_the_row_says_nothing_where_none_was_chosen(tmp_path: Path):
    """Only a Data Source Binding carries a Scope (#11). Every other kind leaves the slot empty
    rather than inventing a label for a choice nobody made."""
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "llm_alias:f-sonnet", "kind": "model_llm", "name": "Claude Sonnet 4.6",
    })
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _record(orch, Binding(KIND_LLM_ALIAS, "f-sonnet", "f-sonnet", "Claude Sonnet 4.6"))

    assert _row(orch, "llm_alias:f-sonnet")["usedBy"] == [
        {"appId": orch._wm.app_ids()[0], "name": "Desk exposure", "scope": ""},
    ]


def test_the_listing_names_exactly_the_apps_the_removal_refusal_names(tmp_path: Path):
    """The reason the drawer reads this scan and not a cheaper one. A creator who has seen the
    drawer must never be told something new by the refusal — that surprise is the dead end #132
    describes, and it comes back the moment the two answers are computed from different places."""
    orch = _orch(tmp_path)
    _add_source(orch)
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _record(orch, _source(("DWH", "MARTS", None)))
    _new_app(orch, "Churn model")
    _record(orch, _source(("DWH", "SANDBOX", None)))

    drawer = [e["name"] for e in _row(orch, "data_source:ds-1")["usedBy"]]
    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert drawer == refused.value.apps == ["Desk exposure", "Churn model"]


def test_an_unbind_empties_the_row_and_the_removal_then_goes_through(tmp_path: Path):
    """Computed on read, so the row tracks the manifest with nothing to invalidate."""
    orch = _orch(tmp_path)
    _add_source(orch)
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _record(orch, _source(("DWH", "MARTS", None)))
    assert len(_row(orch, "data_source:ds-1")["usedBy"]) == 1

    orch.unbind(KIND_DATA_SOURCE, "ds-1")

    assert _row(orch, "data_source:ds-1")["usedBy"] == []
    assert orch.remove_project_resource("data_source:ds-1") is True


def test_the_enrichment_is_never_written_into_the_membership_file(tmp_path: Path):
    """Membership records the pick; the apps' manifests record the use (ADR-0010). A copy here
    would be a second answer to one question, and it would go stale on a bind in an app nobody was
    looking at — which is the failure the guard exists to stop."""
    orch = _orch(tmp_path)
    _add_source(orch)
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _record(orch, _source(("DWH", "MARTS", None)))
    assert _row(orch, "data_source:ds-1")["usedBy"]

    # A write that goes through the membership file must not carry the enrichment back in.
    orch.pin_project_resource("data_source:ds-1", {"database": "DWH", "schema": "MARTS",
                                                   "table": "DIM_ACCOUNT"})
    stored = json.loads(orch.project(start_preview=False).record.project_resources_path.read_text())
    assert [k for row in stored for k in row if k == "usedBy"] == []


def test_the_manifests_are_read_once_for_the_whole_listing(tmp_path: Path):
    """The rail redraws on every app switch and asks this question per row. A scan per row would
    re-read every app's manifest once per Resource, which is how a glance becomes a cost."""
    orch = _orch(tmp_path)
    _add_source(orch)
    orch.add_project_resource({
        "id": "llm_alias:f-sonnet", "kind": "model_llm", "name": "Claude Sonnet 4.6",
    })
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _record(orch, _source(("DWH", "MARTS", None)))
    _new_app(orch, "Churn model")

    calls = {"n": 0}
    original = Orchestrator._app_bindings
    try:
        Orchestrator._app_bindings = lambda self: (calls.__setitem__("n", calls["n"] + 1)
                                                   or original(self))
        rows = orch.list_project_resources()
    finally:
        Orchestrator._app_bindings = original

    assert len(rows) == 2
    assert calls["n"] == 1


def test_an_empty_project_asks_the_apps_nothing(tmp_path: Path):
    """A rail with no rows has no question to ask, and a hard refresh paints it first."""
    orch = _orch(tmp_path)
    calls = {"n": 0}
    original = Orchestrator._app_bindings
    try:
        Orchestrator._app_bindings = lambda self: (calls.__setitem__("n", calls["n"] + 1)
                                                   or original(self))
        assert orch.list_project_resources() == []
    finally:
        Orchestrator._app_bindings = original
    assert calls["n"] == 0


# --- the two surfaces that read it, pinned at the source ---------------------------------------

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
API = (WB / "js" / "api.js").read_text()
PANEL = (WB / "js" / "components" / "resource-panel.js").read_text()
DRAWER = (WB / "js" / "components" / "resource-drawer.js").read_text()
CSS = (WB / "css" / "shell.css").read_text()


def test_the_enrichment_survives_the_trip_from_the_row_to_the_rail():
    """`rowFromMember` is the only door membership takes into the store, and a field it does not
    name is dropped on the floor. That is how `joinedProject` was lost once already."""
    assert "usedBy: item.usedBy || []," in API


def test_the_enrichment_survives_the_domino_overlay():
    """The live Domino listing is spread over the membership row and knows nothing about apps. It
    must not be able to blank the field by being merged last."""
    overlay = API.split("function overlayListing")[1].split("SW.api = {")[0]
    assert "return { ...row, ...live, pins: row.pins, membershipParent: true };" in overlay
    assert "usedBy" not in overlay


def test_the_subtitle_is_drawn_from_the_data_rather_than_from_a_kind_list():
    """The bug this closes: the subtitle was gated on a hand-kept list of kinds AND on a field that
    nothing ever filled, so it could never render. The data is now the gate — a kind cannot be left
    out of the count by being forgotten in a list nobody revisits."""
    assert "const used = resource.usedBy || [];" in PANEL
    assert "? `Used by ${used.length} ${used.length === 1 ? 'app' : 'apps'}`" in PANEL
    assert ": used.length" in PANEL
    assert "showsDependants" not in PANEL


def test_the_app_sign_still_outranks_the_project_wide_count():
    """In Build the question on screen is what THIS app uses, not how popular the Resource is
    (#127). The count keeps the slot in Chat only, so it has to stay below both app lines."""
    subtitle = PANEL.split("const secondary = required && app")[1].split(";")[0]
    assert subtitle.index("Required by ${app.name}") < subtitle.index("Not used by ${app.name}")
    assert subtitle.index("Not used by ${app.name}") < subtitle.index("Used by ${used.length}")


def test_the_count_names_the_apps_on_hover_with_their_scopes():
    """`Used by 2 apps` says how many and never which, and `.sw-res-sub` ellipsises. The tooltip is
    the only place the names fit, and a Scope is what tells two rows of one warehouse apart."""
    assert "used.map((u) => (u.scope ? `${u.name} — ${u.scope}` : u.name)).join(', ')" in PANEL


def test_the_drawer_has_a_where_this_is_used_section():
    assert "'Where this is used'" in DRAWER
    assert "className: 'sw-drawer-meta sw-where-used'" in DRAWER
    assert ".sw-drawer-meta.sw-where-used" in CSS


def test_the_drawer_section_leads_with_this_conversation_and_no_other():
    """A chip belongs to the Conversation it was added in (ADR-0015). Scanning other Conversations
    would report context this one does not have, and the section exists to teach that."""
    section = DRAWER.split("'Where this is used'")[1]
    assert "h('dt', null, 'This conversation')," in section
    assert "h('dd', null, attached ? 'In use' : 'Not in use')," in section
    # Read off the chips this Conversation holds, which the drawer already had in hand.
    assert "const attached = resource && attachments.some((a) => a.resourceId === resource.id);" in DRAWER


def test_the_drawer_section_names_every_binding_app_and_its_scope():
    section = DRAWER.split("'Where this is used'")[1]
    assert "(resource.usedBy || []).flatMap((entry) => [" in section
    assert "h('dt', { key: `app-${entry.appId}` }, entry.name)," in section
    assert "h('dd', { key: `scope-${entry.appId}` }, entry.scope || 'In use')," in section


def test_the_drawer_section_answers_when_nothing_uses_it_yet():
    """An empty state that says what, why and what to do — not a heading over nothing."""
    section = DRAWER.split("'Where this is used'")[1]
    assert "(resource.usedBy || []).length === 0 &&" in section
    assert "`No app in ${scope.name} uses it yet. Use it in one from the project list.`" in section


def test_the_drawer_asks_for_nothing_new_to_fill_the_section():
    """`SW.api.resource` answers out of the store's index, which is built from the same membership
    listing the rail draws. A per-resource endpoint would be a second answer to one question."""
    assert "SW.api\n        .resource(previewResourceId)" in DRAWER
    assert DRAWER.count("SW.api.") == 0
