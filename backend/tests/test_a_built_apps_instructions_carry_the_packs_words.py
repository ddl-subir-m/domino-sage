"""The template's AGENTS.md is tokenised, and resolved when a Built App is seeded (#114).

The Built App's repo is a surface: a partner's own customer can read it (ADR-0014), and AGENTS.md
is generated per Project, so it re-brands like every other prompt. The template ships the tokens and
`_voice_agents_md` reads them at seed time.

Three things are being pinned, and only the first is about substitution:

- the agent **speaks** the mapped nouns, and **understands** the defaults — the prompt says so out
  loud, because a user will type "dataset" whatever the pack says;
- identifiers do not move. `.sage/`, `src/appQuery.ts` and `DatasetClient` are code the agent is
  about to type, and `{appBase}` is the template's own brace, not a pack key;
- the managed regions below the body are not ours to rewrite, and neither is a Conversation that
  already happened.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"
DEFAULT_NOUNS = ["Dataset", "Data Source", "Model API", "LLM Alias", "Built App", "Gallery"]


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)
    monkeypatch.setattr("sage.orchestrator.brand._WARNED", set())


@pytest.fixture
def acme(tmp_path, monkeypatch):
    path = tmp_path / "brand.json"
    path.write_text(json.dumps({
        "productName": "Acme Studio",
        "assistantName": "Ada",
        "platformName": "Acme Cloud",
        "nouns": {
            "dataset": {"singular": "Cube", "plural": "Cubes"},
            "dataSource": {"singular": "Warehouse", "plural": "Warehouses"},
        },
    }))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(path))
    return path


# ---- the shipped template -----------------------------------------------------------------------


def test_the_shipped_template_carries_tokens_rather_than_names():
    """The static file, which is the only copy every Project is guaranteed to start from. A bare
    name here is a name that reaches a partner's customer, because nothing downstream rewrites it."""
    src = (TEMPLATE / "AGENTS.md").read_text()
    assert "{assistantName}" in src
    assert "{platformName}" in src
    assert re.search(r"\bSage\b", src) is None, "a bare product name survives in the template"
    assert re.search(r"\bDomino\b", src) is None, "a bare platform name survives in the template"


def test_the_template_names_the_default_nouns_only_as_synonyms():
    """The one place the DEFAULT words are allowed to be literal: the agent has to recognise what
    the user types, and the user types "dataset" whatever the pack says."""
    src = (TEMPLATE / "AGENTS.md").read_text()
    for noun in DEFAULT_NOUNS:
        assert noun in src, f"{noun} is not offered as a synonym"
    assert "{dataset}" in src and "{dataSource}" in src and "{builtApp}" in src


# ---- what a seeded app gets ---------------------------------------------------------------------


def _template(tmp: Path) -> Path:
    """The real template body, so what is asserted is the file that actually ships."""
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text((TEMPLATE / "AGENTS.md").read_text())
    return t


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code", template=_template(tmp), gateway=object(),
        catalog=ModelCatalog("s", "s", "s", "p", "i", "a"), project_id="Sage",
    )


def _agents(project) -> str:
    return (project.workspace.path / "AGENTS.md").read_text()


def test_a_seeded_app_speaks_the_packs_words(acme, tmp_path):
    text = _agents(_orch(tmp_path).project(start_preview=False))
    assert "Ada typechecks this workspace" in text
    assert "Say **Cube**, **Warehouse**" in text
    assert "the Acme Cloud accent `#543FDE`" in text
    assert "{assistantName}" not in text and "{dataset}" not in text


def test_a_seeded_app_still_recognises_the_default_nouns(acme, tmp_path):
    """The synonyms are literal in the template, so they survive resolution — which is the point.
    A pack that renames Dataset to Cube must not leave the agent unable to understand "Dataset"."""
    text = _agents(_orch(tmp_path).project(start_preview=False))
    for noun in DEFAULT_NOUNS:
        assert noun in text, f"{noun} stopped being offered as a synonym"
    assert "answer in the words above rather than repeating theirs" in text


def test_a_seeded_app_keeps_every_identifier_it_names(acme, tmp_path):
    """Prose and code in one file. `.sage/` is a stored path, `DatasetClient` is a class the agent
    imports, and `{appBase}` is the template's own brace — the helper leaves an unknown token as
    written, which is what makes a tokenised prompt safe to run over code samples."""
    text = _agents(_orch(tmp_path).project(start_preview=False))
    assert "`.sage/` is Ada metadata" in text          # the word moved, the path did not
    assert "`.sage/queries.json`" in text
    assert "`src/appQuery.ts`" in text
    assert "DatasetClient" in text
    assert "basename={appBase}" in text


def test_the_default_pack_leaves_the_instructions_reading_as_they_did(tmp_path):
    """No pack set is the Domino default, and the default must be the words the file used to carry
    — otherwise every existing Project reads a changed prompt for no reason."""
    text = _agents(_orch(tmp_path).project(start_preview=False))
    assert "Sage typechecks this workspace" in text
    assert "`.sage/` is Sage metadata" in text
    assert "the Domino accent `#543FDE`" in text
    assert "{" in text and "{assistantName}" not in text     # tokens resolved, code braces kept


def test_reset_puts_the_packs_words_back(acme, tmp_path):
    """Reset takes AGENTS.md back from the template, so the tokens come back with it and have to be
    read again — otherwise a reset app is the one app in the Project speaking in braces."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    (project.workspace.path / "AGENTS.md").write_text("clobbered\n")

    orch.reset_app()

    text = _agents(project)
    assert "Ada typechecks this workspace" in text
    assert "{assistantName}" not in text


def test_a_second_seed_rewrites_nothing(acme, tmp_path):
    """These files are committed to the user's repo, and a rewrite with identical content shows up
    as a dirty file in the turn's tree comparison and in their git history."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    agents = project.workspace.path / "AGENTS.md"
    before = agents.stat().st_mtime_ns

    orch._voice_agents_md(project)

    assert agents.stat().st_mtime_ns == before


# ---- what is not ours to rewrite ----------------------------------------------------------------


def test_the_users_own_project_instructions_are_never_resolved(acme, tmp_path):
    """Text Sage did not write keeps its words. The instructions block is spliced in AFTER the body
    is voiced, and nothing passes over the whole file again — so guidance that happens to contain a
    brace reaches the agent as the person typed it."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)

    orch.write_instructions(project, "Always label the axis {dataset} in Dataset charts.")

    assert "Always label the axis {dataset} in Dataset charts." in _agents(project)
    assert orch.read_instructions(project) == (
        "Always label the axis {dataset} in Dataset charts."
    )


def test_an_app_seeded_before_the_pack_change_is_left_as_it_is(acme, tmp_path):
    """An existing app's AGENTS.md holds resolved words, not tokens. There is nothing to resolve in
    it and no second chance to re-brand it — the same rule ADR-0014 states for a transcript."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    agents = project.workspace.path / "AGENTS.md"
    agents.write_text("Sage owns src/appLlm.ts. Read the Dataset at public/data/.\n")

    orch._voice_agents_md(project)

    assert agents.read_text() == "Sage owns src/appLlm.ts. Read the Dataset at public/data/.\n"


def test_a_conversation_that_already_happened_is_not_re_branded(acme, tmp_path):
    """`.sage/history.md` is regenerated from the log every turn (ADR-0006), and regeneration
    reproduces speech. A pack change cannot re-brand what the agent literally said, and rewriting it
    would falsify a record committed to the user's repo."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    workspace = project.workspace
    workspace.append_history(
        {"type": "user", "text": "read my Dataset"}, project.build_conversation)
    workspace.append_history(
        {"type": "agent", "kind": "text", "text": "I read the Dataset and built the chart."},
        project.build_conversation,
    )

    orch._refresh_history_archive(project)

    rendered = workspace.history_md_path.read_text()
    assert "**User:** read my Dataset" in rendered
    assert "I read the Dataset and built the chart." in rendered
    assert "Ada" not in rendered and "Cube" not in rendered
