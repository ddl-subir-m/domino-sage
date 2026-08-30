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
