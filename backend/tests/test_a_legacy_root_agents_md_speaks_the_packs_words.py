"""A root AGENTS.md is Sage's to move only when Sage seeded it (#202, rescoped by #548).

Until `36c8167` the template landed straight on the volume, so a Project can still hold a root
AGENTS.md — and OpenCode walks up from BOTH session directories to the project root, so it is read
on every Chat and every Build turn. #548 stopped voicing that file in place and moves it off the
walk-up instead; `test_a_legacy_root_agents_md_is_moved_out_of_the_walk_up.py` covers the move.

What survives here is the RESTRAINT, which the voicing version was careful about and which the
move has to keep: the file is committed to the person's own repo, so a root AGENTS.md that Sage
did not seed is left exactly where the person put it. The three voicing tests that used to sit
here went with the voicer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.workspace.manager import WorkspaceManager

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"
LEGACY = "Say **{dataset}**, **{dataSource}** and **{builtApp}** when you name one of these.\n"


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)
    monkeypatch.setattr("sage.orchestrator.brand._WARNED", set())


@pytest.fixture
def acme(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({
        "assistantName": "Ada",
        "nouns": {
            "dataset": {"singular": "Cube", "plural": "Cubes"},
            "dataSource": {"singular": "Warehouse", "plural": "Warehouses"},
        },
    }))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    return path


def _manager(tmp: Path) -> WorkspaceManager:
    template = tmp / "template"
    template.mkdir(parents=True, exist_ok=True)
    (template / "package.json").write_text("{}")
    (template / "AGENTS.md").write_text((TEMPLATE / "AGENTS.md").read_text())
    return WorkspaceManager(workspace_dir=tmp / "mnt" / "code", template=template)


def _root(manager: WorkspaceManager) -> Path:
    return manager._dir / "AGENTS.md"


def _seed_legacy(manager: WorkspaceManager, body: str = LEGACY) -> Path:
    manager._dir.mkdir(parents=True, exist_ok=True)
    root = _root(manager)
    root.write_text(body)
    return root





def test_a_file_with_nothing_to_resolve_is_not_rewritten(acme, tmp_path):
    """This file is committed to the person's repo. A rewrite with identical content is a dirty
    file in their git history and in the turn's tree comparison, every boot, forever."""
    manager = _manager(tmp_path)
    root = _seed_legacy(manager, "Always label the axis in Warehouse charts.\n")
    manager.ensure("p")
    before = root.stat().st_mtime_ns

    manager.ensure("p")

    assert root.stat().st_mtime_ns == before
    assert root.read_text() == "Always label the axis in Warehouse charts.\n"


def test_a_volume_that_never_had_one_grows_no_file(acme, tmp_path):
    """Every Project seeded since `36c8167` has no root AGENTS.md, and the repair must not invent
    one: a file there is exactly the bug being removed."""
    manager = _manager(tmp_path)

    manager.ensure("p")

    assert not _root(manager).exists()
