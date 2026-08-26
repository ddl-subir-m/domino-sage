from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator, ResourceStillBound
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
