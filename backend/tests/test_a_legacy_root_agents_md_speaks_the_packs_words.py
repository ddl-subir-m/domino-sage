"""A pre-`apps/` seed left AGENTS.md at the volume root, and it is still instructing turns (#202).

Until `36c8167` the template landed straight on the volume, and seeding only began voicing it at
`7d75bd9`. So a Project made before that still holds a root AGENTS.md reading "Say **{dataSource}**"
— and OpenCode walks up from BOTH session directories to the project root, so it is read on every
Chat and every Build turn. Measured live 2026-09-07: Chat answered "a Snowflake connection is called
a {dataSource}".

Three things are pinned: the tokens resolve for both halves; the repair runs on a Chat-only volume
that has no app at all; and nothing is written when nothing resolves, because the file is committed
to the person's own repo.
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


def test_a_build_that_opens_the_volume_voices_the_legacy_file(acme, tmp_path):
    manager = _manager(tmp_path)
    root = _seed_legacy(manager)

    manager.ensure("p")

    assert "Say **Cube**, **Warehouse** and **Built App**" in root.read_text()
    assert "{dataSource}" not in root.read_text()


def test_chat_voices_it_too_on_a_volume_with_no_app(acme, tmp_path):
    """Chat never seeds an app, and Chat is where the token was seen. `seed_app=False` must still
    reach the root file — the leak is one directory above anything Build owns."""
    manager = _manager(tmp_path)
    root = _seed_legacy(manager)

    manager.ensure("p", seed_app=False)

    assert "{dataSource}" not in root.read_text()
    assert not (manager._dir / "apps").exists()


def test_the_default_pack_puts_the_domino_words_back(tmp_path):
    """No pack set is the Domino default. A Project on the default must read the words the file was
    always meant to carry, not keep the braces because nobody renamed anything."""
    manager = _manager(tmp_path)
    root = _seed_legacy(manager)

    manager.ensure("p")

    assert "Say **Dataset**, **Data Source** and **Built App**" in root.read_text()


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
