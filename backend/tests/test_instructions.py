"""User project-instructions block in the workspace AGENTS.md.

Mirrors test_attach_upload.py fixtures. Verifies the managed instructions block is spliced in
without clobbering the template body or the attached-data block, and round-trips cleanly."""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Building apps in this workspace\n\nTemplate body here.\n")
    return t


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
                        gateway=object(), catalog=_catalog(), project_id="Sage")


def _agents(ws: Path) -> str:
    return (ws / "AGENTS.md").read_text()


def test_write_inserts_managed_block_after_template_body(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)

    orch.write_instructions(proj, "Use a dark theme.")

    text = _agents(proj.workspace.path)
    assert "Template body here." in text                       # template body untouched
    assert orch._INSTR_HEAD in text and orch._INSTR_FRAME in text   # managed heading + frame present
    assert "Use a dark theme." in text
    assert text.index("Template body here.") < text.index(orch._INSTR_BEGIN)  # body before block


def test_block_sits_between_template_body_and_attached_data(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.upload_file("d.csv", b"a,b\n1,2\n", sensitive=False)   # writes attached-data block
    orch.write_instructions(proj, "Prefer bar charts.")

    text = _agents(proj.workspace.path)
    body = text.index("Template body here.")
    instr = text.index(orch._INSTR_BEGIN)
    data = text.index(orch._AGENTS_BEGIN)
    assert body < instr < data                                 # body -> instructions -> attached-data


def test_round_trip_returns_only_the_raw_body(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    raw = "Line one.\n\nLine two with a heading:\n## Not the managed head"

    orch.write_instructions(proj, raw)

    # File carries the managed heading + frame, but read gives back exactly the raw body.
    assert orch._INSTR_HEAD in _agents(proj.workspace.path)
    assert orch.read_instructions(proj) == raw


def test_writing_instructions_does_not_disturb_attached_data_block(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.upload_file("d.csv", b"a,b\n1,2\n", sensitive=False)
    before = _agents(proj.workspace.path)
    data_region = before[before.index(orch._AGENTS_BEGIN):before.index(orch._AGENTS_END)]

    orch.write_instructions(proj, "Some guidance.")

    after = _agents(proj.workspace.path)
    assert data_region in after                                # attached-data region byte-identical
    assert orch.read_instructions(proj) == "Some guidance."


def test_writing_attached_data_does_not_disturb_instructions_block(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.write_instructions(proj, "Keep it minimal.")

    orch.upload_file("d.csv", b"a,b\n1,2\n", sensitive=False)   # calls _write_agents_data_block

    assert orch.read_instructions(proj) == "Keep it minimal."  # instructions survive intact
    assert orch._AGENTS_BEGIN in _agents(proj.workspace.path)


def test_empty_content_removes_the_block_cleanly(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.write_instructions(proj, "Temporary.")
    assert orch._INSTR_BEGIN in _agents(proj.workspace.path)

    orch.write_instructions(proj, "   ")                        # whitespace-only -> remove

    text = _agents(proj.workspace.path)
    assert orch._INSTR_BEGIN not in text and orch._INSTR_END not in text
    assert orch._INSTR_HEAD not in text
    assert "Template body here." in text                        # body preserved
    assert orch.read_instructions(proj) == ""
