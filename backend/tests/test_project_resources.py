import json
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator, ResourceStillBound
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
    ws = tmp / "mnt" / "code"
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=object(),
                        catalog=_catalog(), project_id="Sage")
    orch.project(start_preview=False)
    return orch


def test_a_new_project_has_no_imported_resources(tmp_path: Path):
    assert _orch(tmp_path).list_project_resources() == []


def test_add_then_remove_a_dataset_from_the_project(tmp_path: Path):
    orch = _orch(tmp_path)
    first = orch.add_project_resource({
        "id": "dataset:ds1", "kind": "dataset", "name": "autodoc", "project": "Sage",
    })
    assert first["added"] is True
    assert first["item"]["name"] == "autodoc"
    assert [r["id"] for r in orch.list_project_resources()] == ["dataset:ds1"]

    again = orch.add_project_resource({"id": "dataset:ds1", "kind": "dataset", "name": "autodoc"})
    assert again["added"] is False
    assert len(orch.list_project_resources()) == 1

    assert orch.remove_project_resource("dataset:ds1") is True
    assert orch.list_project_resources() == []
    assert orch.remove_project_resource("dataset:ds1") is False


def test_remove_is_refused_while_a_binding_still_needs_the_resource(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "llm_alias:f-sonnet", "kind": "model_llm", "name": "Claude Sonnet 4.6",
    })
    orch.bind_llm_alias("f-sonnet")
    with pytest.raises(ResourceStillBound, match="Claude Sonnet 4.6"):
        orch.remove_project_resource("llm_alias:f-sonnet")
    orch.unbind("llm_alias", "f-sonnet")
    assert orch.remove_project_resource("llm_alias:f-sonnet") is True
    assert orch.list_project_resources() == []


def test_pin_a_dataset_file_and_a_table_then_unpin(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "dataset:ds_sales_2026", "kind": "dataset", "name": "sales_2026",
        "pin": {"path": "train.csv"},
    })
    row = orch.list_project_resources()[0]
    assert row["pins"] == [{"path": "train.csv", "name": "train.csv"}]
    orch.pin_project_resource("dataset:ds_sales_2026", {"path": "train.csv"})
    assert len(orch.list_project_resources()[0]["pins"]) == 1

    orch.add_project_resource({
        "id": "data_source:ds-dwh", "kind": "datasource", "name": "Snowflake-Data-Warehouse",
        "pin": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    tables = [r for r in orch.list_project_resources() if r["id"] == "data_source:ds-dwh"][0]
    assert tables["pins"][0]["table"] == "DIM_ACCOUNT"

    assert orch.unpin_project_resource("dataset:ds_sales_2026", {"path": "train.csv"}) is True
    assert orch.list_project_resources()[0]["pins"] == []
    assert orch.remove_project_resource("dataset:ds_sales_2026") is True
    leftover = orch.list_project_resources()
    assert leftover[0]["id"] == "data_source:ds-dwh"
    assert leftover[0]["pins"]


# ---- The guard asks every Built App (#71) ------------------------------------------------------
#
# A Project holds many Built Apps (ADR-0008) and a Binding always names exactly one of them, so a
# guard that reads the Bindings of the app in front of you allows a removal that silently breaks an
# app nobody was looking at. Membership stays the Project's: a Resource is picked once, not once
# per app.


def _new_app(orch: Orchestrator, name: str) -> str:
    """Mint a second Built App, name it, and put Build in front of it. #74 gives this a rail
    button; until then a confirmed handoff is the only caller, and this is the same steps it takes."""
    app_id = orch._wm.create_app("Sage").app_id
    orch.select_app(app_id)
    orch.rename_app(app_id, name)
    return app_id


def _bind_data_source(orch: Orchestrator, source_id: str, name: str) -> None:
    """Write the Binding record straight into the SELECTED app's manifest. `bind_data_source`
    validates the id against the project's Domino listing, which this orchestrator has no provider
    for, and what is under test here is the guard rather than the cascade that fills the record."""
    binding = Binding(KIND_DATA_SOURCE, source_id, name, name, connector_type="SnowflakeConfig")
    _selected(orch).update_bindings(lambda _: [binding.to_dict()])


def _add_source(orch: Orchestrator) -> None:
    orch.add_project_resource({
        "id": "data_source:ds-1", "kind": "datasource", "name": "BigQuery_Demo",
    })


def _selected(orch: Orchestrator):
    return orch.project(start_preview=False).workspace


def _use_the_source(app: Path) -> None:
    """Give the selected app a query against ds-1 and a screen that calls it — what makes the
    refusal carry files to fix rather than only a name."""
    (app / ".sage" / "queries.json").write_text(json.dumps(
        [{"name": "clicks_by_day", "binding": "ds-1", "sql": "SELECT 1", "params": []}]))
    (app / "src" / "Ads.tsx").write_text('const r = await runQuery("clicks_by_day");\n')


def test_removal_is_refused_by_a_built_app_nobody_was_looking_at(tmp_path: Path):
    """The bug this closes: the guard read one Bindings manifest — the selected app's — so removing
    a Resource the other app still binds was allowed, and that app broke out of sight."""
    orch = _orch(tmp_path)
    _add_source(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Churn model")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    orch.select_app(first)                      # look away from the app that binds it

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert refused.value.apps == ["Churn model"]
    assert [r["id"] for r in orch.list_project_resources()] == ["data_source:ds-1"]


def test_the_refusal_names_every_app_that_still_binds_the_resource(tmp_path: Path):
    """Naming one of two is a half-answer: the creator unbinds the app it named, tries again, and
    is refused a second time by an app the first refusal knew about and did not say."""
    orch = _orch(tmp_path)
    _add_source(orch)
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _new_app(orch, "Churn model")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert refused.value.apps == ["Desk exposure", "Churn model"]      # oldest app first
    assert "Desk exposure" in str(refused.value)
    assert "Churn model" in str(refused.value)


def test_the_refusal_names_the_files_in_the_app_that_binds_it(tmp_path: Path):
    """`refs` is the cleanup affordance, and it has to follow the app that holds the Binding rather
    than the one on screen — a list of files from the app you are looking at is worse than none."""
    orch = _orch(tmp_path)
    _add_source(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Churn model")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _use_the_source(_selected(orch).path)
    orch.select_app(first)

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert ".sage/queries.json" in refused.value.refs
    assert "src/Ads.tsx" in refused.value.refs


def test_a_resource_no_built_app_binds_can_still_be_removed(tmp_path: Path):
    """The guard refuses; it does not hold on. Once the last app has unbound it, the Resource
    leaves the project like any other."""
    orch = _orch(tmp_path)
    _add_source(orch)
    _new_app(orch, "Churn model")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    orch.unbind(KIND_DATA_SOURCE, "ds-1")

    assert orch.remove_project_resource("data_source:ds-1") is True
    assert orch.list_project_resources() == []


def test_a_resource_is_picked_once_for_the_project_not_once_per_built_app(tmp_path: Path):
    """Membership is the Project's and a Binding is one app's. A second app binds off the working
    set the first one was picked into, so nobody re-authorises the same Resource per app.

    Bound through `bind_llm_alias` rather than a hand-written manifest, because what is under test
    is the authorising step: the bind validates the id against the project's own listing, and that
    listing is the membership added once, before the second app existed.
    """
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "llm_alias:f-sonnet", "kind": "model_llm", "name": "Claude Sonnet 4.6",
    })
    orch.bind_llm_alias("f-sonnet")
    _new_app(orch, "Churn model")

    assert [b["id"] for b in orch.bind_llm_alias("f-sonnet")] == ["f-sonnet"]
    assert [r["id"] for r in orch.list_project_resources()] == ["llm_alias:f-sonnet"]


def test_the_refusal_names_each_file_with_its_app_once_more_than_one_binds(tmp_path: Path):
    """Every Built App is seeded from the same template, so the same path in two of them is two
    files. A bare `.sage/queries.json` would leave the creator opening apps to find which."""
    orch = _orch(tmp_path)
    _add_source(orch)
    orch.rename_app(_selected(orch).app_id, "Desk exposure")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _use_the_source(_selected(orch).path)
    _new_app(orch, "Churn model")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _use_the_source(_selected(orch).path)

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert refused.value.refs == [
        "Desk exposure — .sage/queries.json", "Desk exposure — src/Ads.tsx",
        "Churn model — .sage/queries.json", "Churn model — src/Ads.tsx",
    ]


def test_the_route_answers_409_naming_the_apps_that_still_bind_it(tmp_path: Path, monkeypatch):
    """The panel reads `apps` off the body to say which app refused, so the rule lives in the API
    contract rather than only in the markup that renders it."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    _add_source(orch)
    _new_app(orch, "Churn model")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _use_the_source(_selected(orch).path)
    monkeypatch.setattr(appmod, "orchestrator", orch)

    answer = TestClient(appmod.control_app).request(
        "DELETE", "/api/project/resources", params={"id": "data_source:ds-1"})

    assert answer.status_code == 409
    assert answer.json()["apps"] == ["Churn model"]
    assert answer.json()["refs"] == [".sage/queries.json", "src/Ads.tsx"]
    assert "Churn model still needs BigQuery_Demo" in answer.json()["error"]


# Mentioning a catalogue Resource joins the project ---------------------------
#
# Membership existed for a machine reason — provisioning — and stood in front of the user reason,
# which is naming the thing in a sentence. So naming it in a Thread does the join on the way in.
# The flag on the response row is how the panel learns to refresh; nothing else tells it.


def _thread(orch: Orchestrator) -> str:
    return orch.create_thread()["id"]


def test_naming_a_catalogue_parent_in_a_thread_joins_the_project(tmp_path: Path):
    orch = _orch(tmp_path)
    tid = _thread(orch)

    row = orch.add_thread_context(tid, {
        "kind": "dataset", "name": "card-transactions-q3", "resourceId": "dataset:ds1",
    })

    assert row["joinedProject"] is True
    assert [r["id"] for r in orch.list_project_resources()] == ["dataset:ds1"]
    assert orch.list_project_resources()[0]["name"] == "card-transactions-q3"


def test_every_parent_kind_joins_on_the_way_in(tmp_path: Path):
    orch = _orch(tmp_path)
    tid = _thread(orch)

    for kind, rid in (
        ("dataset", "dataset:ds1"),
        ("data_source", "data_source:ds-dwh"),
        ("llm_alias", "llm_alias:f-sonnet"),
        ("model_api", "model_api:churn"),
    ):
        row = orch.add_thread_context(tid, {"kind": kind, "name": rid, "resourceId": rid})
        assert row["joinedProject"] is True, kind

    assert [r["id"] for r in orch.list_project_resources()] == [
        "dataset:ds1", "data_source:ds-dwh", "llm_alias:f-sonnet", "model_api:churn",
    ]


def test_a_resource_already_in_the_project_joins_nothing_and_says_nothing(tmp_path: Path):
    """Idempotent, and quiet with it: a second flag would draw a second toast for a membership
    that never changed."""
    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "dataset:ds1", "kind": "dataset", "name": "autodoc"})
    tid = _thread(orch)

    row = orch.add_thread_context(tid, {
        "kind": "dataset", "name": "autodoc", "resourceId": "dataset:ds1",
    })

    assert "joinedProject" not in row
    assert len(orch.list_project_resources()) == 1


def test_a_file_and_an_artifact_join_nothing(tmp_path: Path):
    """Neither is a Domino Resource, so neither has a membership row to make."""
    orch = _orch(tmp_path)
    tid = _thread(orch)

    scratch = orch.project(start_preview=False).workspace.path / "positions.csv"
    scratch.write_text("a,b\n1,2\n")
    a_file = orch.add_thread_context(tid, {
        "kind": "file", "name": "positions.csv", "path": "positions.csv",
        "resourceId": "file:positions.csv",
    })
    an_artifact = orch.add_thread_context(tid, {
        "kind": "artifact", "name": "chart.png", "path": ".sage/artifacts/chart.png",
        "resourceId": "artifact:.sage/artifacts/chart.png",
    })

    assert "joinedProject" not in a_file
    assert "joinedProject" not in an_artifact
    assert orch.list_project_resources() == []


def test_a_leaf_joins_nothing_because_the_rail_is_the_only_way_to_reach_one(tmp_path: Path):
    """A Dataset file and a warehouse table are reached by expanding a parent in the rail, which
    already means that parent is a member. Joining off a leaf would write a membership row for the
    LEAF's own id, which is not a Resource id the rail can render."""
    orch = _orch(tmp_path)
    tid = _thread(orch)

    dsfile = orch.add_thread_context(tid, {
        "kind": "file", "name": "train.csv", "resourceId": "dsfile:ds1:train.csv",
        "parentId": "dataset:ds1", "datasetId": "ds1", "datasetRelPath": "train.csv",
    })
    table = orch.add_thread_context(tid, {
        "kind": "data_source", "name": "DIM_ACCOUNT",
        "resourceId": "table:ds-dwh:DWH.MARTS.DIM_ACCOUNT",
        "parentId": "data_source:ds-dwh",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })

    assert "joinedProject" not in dsfile
    assert "joinedProject" not in table
    assert orch.list_project_resources() == []


def test_the_join_flag_is_an_event_and_is_never_persisted(tmp_path: Path):
    """It says "membership changed just now", which is true once. Read back off disk it would say
    it again on every reload, and the panel would keep re-announcing an old join."""
    orch = _orch(tmp_path)
    tid = _thread(orch)
    orch.add_thread_context(tid, {
        "kind": "dataset", "name": "autodoc", "resourceId": "dataset:ds1",
    })

    stored = orch.thread_context(tid)["items"]

    assert [i.get("joinedProject") for i in stored] == [None]


def test_a_mention_writes_the_same_row_the_browse_button_writes(tmp_path: Path):
    """The two doors into the working set have to agree. The model picker reads `alias` and
    `reasoning_efforts` off these rows whenever the Alias listing is unavailable, so a join that
    kept only id/kind/name wrote an option it draws blank and cannot select."""
    orch = _orch(tmp_path)
    tid = _thread(orch)

    orch.add_thread_context(tid, {
        "kind": "llm_alias", "name": "Claude Sonnet 4.6", "resourceId": "llm_alias:f-sonnet",
        "description": "Anthropic", "alias": "f-sonnet",
        "capabilities": ["vision"], "reasoning_efforts": ["low", "high"],
    })

    joined = orch.list_project_resources()[0]
    assert joined["alias"] == "f-sonnet"
    assert joined["reasoning_efforts"] == ["low", "high"]
    assert joined["capabilities"] == ["vision"]
    assert joined["description"] == "Anthropic"


def test_the_chip_does_not_keep_what_only_the_membership_row_wanted(tmp_path: Path):
    """They ride in on the mention because the catalogue row is the only thing that has them. A
    chip has no use for any of them, so it does not carry them afterwards."""
    orch = _orch(tmp_path)
    tid = _thread(orch)

    row = orch.add_thread_context(tid, {
        "kind": "llm_alias", "name": "Claude Sonnet 4.6", "resourceId": "llm_alias:f-sonnet",
        "alias": "f-sonnet", "reasoning_efforts": ["low", "high"],
    })

    stored = orch.thread_context(tid)["items"][0]
    for field in ("description", "alias", "capabilities", "reasoning_efforts"):
        assert field not in row, field
        assert field not in stored, field
    assert stored["name"] == "Claude Sonnet 4.6"


def test_adding_a_resource_again_fills_in_what_the_first_write_left_out(tmp_path: Path):
    """Adding is idempotent on id, so a row that reached the working set thin lands right back on
    that early return — and could never be repaired. Only what is MISSING is filled in: a value
    already on the row is the one this Project has, and a re-add is not a reason to move it."""
    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "llm_alias:f-sonnet", "kind": "model_llm", "name": "Sonnet"})

    again = orch.add_project_resource({
        "id": "llm_alias:f-sonnet", "kind": "model_llm", "name": "Renamed by nobody",
        "alias": "f-sonnet", "reasoning_efforts": ["low", "high"],
    })

    assert again["added"] is False
    row = orch.list_project_resources()[0]
    assert row["alias"] == "f-sonnet"
    assert row["reasoning_efforts"] == ["low", "high"]
    # Present already, so left alone — filling gaps is not a rename.
    assert row["name"] == "Sonnet"


# ---- A conversation's chips hold a Resource too (#168) -----------------------------------------
#
# The guard above asks every Built App and stops there, so a Data Source a live conversation is
# holding a table chip on could be removed out from under it: the chip stays on the Thread naming a
# Resource the Project no longer holds, and every turn after that carries it to the model as though
# it were still a member. The scan is the mirror of `_release_chat_file`, which has always walked
# live Threads before releasing fetched bytes for exactly the reciprocal reason.
#
# It refuses rather than cascading. Stripping the chip would rewrite a conversation whose turns
# already happened, which is the sort of quiet history-rewrite Sage avoids everywhere else, and the
# two ways out — close the chip, delete the conversation — are both real acts the creator can take.


def _talk_about_the_source(orch: Orchestrator, thread_id: str) -> str:
    """Put a table chip from ds-1 on this Thread, and answer with the chip's id.

    A table chip rather than the Data Source itself, because that is the shape the bug was found in:
    the chip's own `resourceId` is a leaf's, and only its `parentId` names the membership row that
    the removal is about.
    """
    chip = orch.add_thread_context(thread_id, {
        "kind": "data_source", "name": "DIM_ACCOUNT",
        "resourceId": "table:ds-1:DWH.MARTS.DIM_ACCOUNT",
        "parentId": "data_source:ds-1",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    return chip["id"]


def _named_thread(orch: Orchestrator, title: str) -> str:
    tid = orch.create_thread()["id"]
    orch.patch_thread(tid, {"title": title})
    return tid


def test_a_deleted_conversation_holds_no_resource_back(tmp_path: Path):
    """`ThreadStore.delete` writes a tombstone and leaves `threads/<id>/` where it stands, so the
    deleted conversation's `context.json` is still on disk with its chips in it. A scan over the
    directories would read them back and let a conversation nobody can open refuse this removal for
    the life of the project — a fresh instance of exactly the bug the guard exists to fix."""
    orch = _orch(tmp_path)
    _add_source(orch)
    tid = _named_thread(orch, "Positions review")
    _talk_about_the_source(orch, tid)
    orch.delete_thread(tid)

    assert orch.remove_project_resource("data_source:ds-1") is True
    assert orch.list_project_resources() == []


def test_a_live_conversation_holding_a_chip_refuses_the_removal(tmp_path: Path):
    """The conversation is named, not merely counted. A Project holds many Threads and the one
    holding it is rarely the one on screen, so "a conversation still needs this" without a title is
    a refusal the creator cannot act on."""
    orch = _orch(tmp_path)
    _add_source(orch)
    _talk_about_the_source(orch, _named_thread(orch, "Positions review"))

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert refused.value.conversations == ["Positions review"]
    assert refused.value.apps == []
    assert "Positions review still needs BigQuery_Demo" in str(refused.value)
    assert [r["id"] for r in orch.list_project_resources()] == ["data_source:ds-1"]


def test_a_conversation_holding_the_parent_itself_refuses_it_too(tmp_path: Path):
    """A chip on the Data Source itself names the membership row in `resourceId` rather than in
    `parentId`. Both fields are the same claim on the same Resource."""
    orch = _orch(tmp_path)
    _add_source(orch)
    tid = _named_thread(orch, "Warehouse tour")
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "BigQuery_Demo", "resourceId": "data_source:ds-1",
    })

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert refused.value.conversations == ["Warehouse tour"]


def test_closing_the_chip_lets_the_resource_leave(tmp_path: Path):
    """The refusal is a door rather than a wall: closing the chip is one of the two acts that
    releases it, and the retry after it has to go through."""
    orch = _orch(tmp_path)
    _add_source(orch)
    tid = _named_thread(orch, "Positions review")
    chip = _talk_about_the_source(orch, tid)

    orch.remove_thread_context(tid, chip)

    assert orch.remove_project_resource("data_source:ds-1") is True
    assert orch.list_project_resources() == []


def test_the_refusal_names_every_conversation_holding_it(tmp_path: Path):
    """Naming one of two is a half-answer here for the same reason it is with apps: the creator
    closes the chip the refusal named, tries again, and is refused a second time by a conversation
    the first refusal knew about and did not say."""
    orch = _orch(tmp_path)
    _add_source(orch)
    _talk_about_the_source(orch, _named_thread(orch, "Positions review"))
    _talk_about_the_source(orch, _named_thread(orch, "Desk exposure"))

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert sorted(refused.value.conversations) == ["Desk exposure", "Positions review"]


def test_a_refused_removal_takes_no_chip_with_it(tmp_path: Path):
    """It refuses; it does not tidy. The chip is evidence about turns that already happened, and
    the person removing a Resource from the working set is not speaking for a conversation they may
    not even have open."""
    orch = _orch(tmp_path)
    _add_source(orch)
    mine = _named_thread(orch, "Positions review")
    theirs = _named_thread(orch, "Desk exposure")
    _talk_about_the_source(orch, mine)
    _talk_about_the_source(orch, theirs)

    with pytest.raises(ResourceStillBound):
        orch.remove_project_resource("data_source:ds-1")

    for tid in (mine, theirs):
        held = [i.get("parentId") for i in orch.thread_context(tid)["items"]]
        assert held == ["data_source:ds-1"]


def test_a_resource_an_app_binds_and_a_conversation_holds_refuses_once_naming_both(tmp_path: Path):
    """Two holders, one refusal. Reporting the app and going quiet about the conversation would
    send the creator to unbind the app and straight back into a second refusal."""
    orch = _orch(tmp_path)
    _add_source(orch)
    first = _selected(orch).app_id
    _new_app(orch, "Churn model")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    orch.select_app(first)
    _talk_about_the_source(orch, _named_thread(orch, "Positions review"))

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert refused.value.apps == ["Churn model"]
    assert refused.value.conversations == ["Positions review"]
    assert "Churn model" in str(refused.value)
    assert "Positions review" in str(refused.value)


def test_the_route_answers_409_naming_the_conversations_that_still_hold_it(tmp_path: Path,
                                                                          monkeypatch):
    """The panel reads `conversations` off the body the same way it reads `apps`, so a chip-only
    refusal arrives with something to act on rather than as a bare sentence."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    _add_source(orch)
    _new_app(orch, "Churn model")
    _bind_data_source(orch, "ds-1", "BigQuery_Demo")
    _use_the_source(_selected(orch).path)
    _talk_about_the_source(orch, _named_thread(orch, "Positions review"))
    monkeypatch.setattr(appmod, "orchestrator", orch)

    answer = TestClient(appmod.control_app).request(
        "DELETE", "/api/project/resources", params={"id": "data_source:ds-1"})

    assert answer.status_code == 409
    assert answer.json()["apps"] == ["Churn model"]
    assert answer.json()["conversations"] == ["Positions review"]
    assert answer.json()["refs"] == [".sage/queries.json", "src/Ads.tsx"]


def test_a_chip_that_spells_the_data_source_the_old_way_still_holds_it(tmp_path: Path):
    """An older project keys a Data Source under `datasource:` and the backfill writes the row again
    under `data_source:`, so the chip and the membership row it names can be spelled two ways. Every
    other id comparison in this file joins the two — `_apps_that_bind` and the backfill itself — and
    a scan that did not would let the removal through on the one spelling nobody typed."""
    orch = _orch(tmp_path)
    _add_source(orch)                                   # keyed `data_source:ds-1`
    tid = _named_thread(orch, "Positions review")
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "DIM_ACCOUNT",
        "resourceId": "table:ds-1:DWH.MARTS.DIM_ACCOUNT",
        "parentId": "datasource:ds-1",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })

    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("data_source:ds-1")

    assert refused.value.conversations == ["Positions review"]

    # And the other way round, which is the removal of the stale twin row the backfill left behind.
    # The Resource is named rather than keyed: a sentence reading "Positions review still needs
    # datasource:ds-1" would put an internal id in front of someone who only ever saw a name.
    with pytest.raises(ResourceStillBound) as twin:
        orch.remove_project_resource("datasource:ds-1")

    assert "still needs BigQuery_Demo" in str(twin.value)
