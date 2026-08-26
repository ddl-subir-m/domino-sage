from pathlib import Path

from sage.orchestrator.service import Orchestrator
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
