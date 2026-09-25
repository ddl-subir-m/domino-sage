"""A Binding recorded before Build seeds the app reaches the app's source once it is born (#503).

Chat can bind a Resource into a Project whose selected app is still an empty directory — a bindings
manifest and a display name are all that is there. That directory used to read as an app with a
"mixed" layout and never seeded; now it reads as unborn, the bind keeps its manifest and writes no
source, and each door that puts a born app in front of the Project derives the pin from the record.
"""
from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import ModelCatalog
from sage.workspace.stack import resolve_stack

from .fake_opencode import FakeOpenCode, Turn

ALIASES = [LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0})]


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    return Orchestrator(workspace_dir=ws, template=template, gateway=None,
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", opencode_client=FakeOpenCode(ws, [Turn(text="ok")]),
                        resources=FakeResourceProvider(list(ALIASES)))


def test_a_bind_into_an_unborn_app_keeps_its_record_and_writes_no_source(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False, seed_app=False)   # Chat: the volume, no app

    orch.bind_llm_alias("id-sonnet")

    app = project.workspace.path
    assert resolve_stack(app).state == "empty", "a manifest alone does not make the directory an app"
    assert (app / ".sage" / "bindings.json").exists()
    assert not (app / "src").exists()


def test_the_pin_is_derived_when_the_app_is_seeded(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False, seed_app=False)
    orch.bind_llm_alias("id-sonnet")

    orch._ensure_seeded()

    names = project.workspace.helpers
    assert (project.workspace.path / names.llm_config_path).exists()
    assert "Claude Sonnet 4.6" in (project.workspace.path / "AGENTS.md").read_text()


def test_the_pin_is_derived_for_an_app_minted_after_the_bind(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False, seed_app=False)
    orch.bind_llm_alias("id-sonnet")

    born = orch.create_app()

    path = orch.project(start_preview=False).workspace.path
    assert path.name == born["id"]
    assert (path / orch.project(start_preview=False).workspace.helpers.llm_config_path).exists()
