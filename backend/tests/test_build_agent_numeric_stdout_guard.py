"""Build gets the same stdout guard Chat needed, but through AGENTS.md.

The VLTA incident was a Chat turn, but the failure shape is not Chat-specific: any OpenCode tool
result is carried into the next gateway request. Build has bash and sometimes inspects attached
data before writing to the app, so raw pandas output can carry the same 10-11 digit run that the
gateway's PII policy reads as a phone number.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.workspace.manager import WorkspaceManager

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "template" / "fastapi-antd" / "AGENTS.md"
PROBES = (
    "Format numeric diagnostics with",
    "10-11 digit string",
    "round()",
    "the gateway's PII rule reads as a phone number",
)


def agents() -> str:
    return AGENTS.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)


def _template_carrying_the_real_agents_file(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "static").mkdir(parents=True)
    (t / "static" / "app.js").write_text("placeholder")
    (t / "app.py").write_text("# app\n")
    (t / "AGENTS.md").write_text(agents(), encoding="utf-8")
    return t


def test_the_build_template_tells_the_agent_to_format_numeric_stdout():
    text = agents()

    for probe in PROBES:
        assert probe in text, probe


def test_a_newly_seeded_app_is_told_the_numeric_stdout_rule(tmp_path: Path):
    tmpl = _template_carrying_the_real_agents_file(tmp_path)

    ws = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl).ensure("proj1")
    text = (ws.path / "AGENTS.md").read_text(encoding="utf-8")

    for probe in PROBES:
        assert probe in text, probe


def test_reset_app_is_how_an_existing_app_gets_the_numeric_stdout_rule(tmp_path: Path):
    tmpl = _template_carrying_the_real_agents_file(tmp_path)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    ws = mgr.ensure("proj1")
    (ws.path / "AGENTS.md").write_text("an older Sage wrote this\n")

    mgr.reset()
    text = (ws.path / "AGENTS.md").read_text(encoding="utf-8")

    for probe in PROBES:
        assert probe in text, probe
