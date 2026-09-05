"""The build agent's standing instructions are branded before the model reads them (ADR-0014).

Measured live, 2026-09-05. A plan turn against a Snowflake Data Source wrote this, six times, in
front of the person who asked for it:

    "Today, Anthropic API data in the {dataSource} is hard to scan and compare…"

The raw gateway stream settled where it came from: the model was GENERATING the token, one piece at
a time (`"content":"data"`, then `"content":"}"`). Nothing rendered it wrong — the model had been
told to write it.

`template/react-vite/AGENTS.md` names the nouns as pack tokens, because the word on the screen
belongs to whoever deployed Sage:

    Say **{dataset}**, **{dataSource}**, **{modelApi}**, … when you name one of these to the user.

`brand.apply_voice` is what turns those into the pack's words, and it had two callers: the Chat stub
AGENTS.md, and the agent prompts inlined into `opencode.json`. The Built App's AGENTS.md was neither.
Both seed paths reached it through `shutil.copy2` — byte for byte, tokens intact — so the model read
"Say {dataSource}" as an instruction and obeyed.

The last test here is the guard against the same drift returning by a different file: it is not
about AGENTS.md, it is about anything under `template/` that carries a pack token.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sage.orchestrator import brand
from sage.workspace import manager
from sage.workspace.manager import _VOICED_SEED, WorkspaceManager

_REPO = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO / "template"
# A `{token}` as brand.text sees one. `{appBase}` in the template is JSX (`basename={appBase}`) and
# is deliberately NOT a pack token — an unknown token is left as written, which is what keeps that
# line compiling. So the assertions below are about the pack's own names, never about braces.
_TOKEN = re.compile(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}")


def _pack_tokens() -> set[str]:
    return set(brand._tokens(brand.load()))


def _template_with_agents(tmp: Path, body: str) -> Path:
    """The smallest template the seed path will take, carrying one AGENTS.md."""
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text(body)
    (t / "node_modules" / ".bin").mkdir(parents=True)
    (t / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh")
    return t


_SAYS = "Say **{dataset}** and **{dataSource}** when you name one of these to the user.\n"


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    """The default pack, whatever the image this runs in was built with — same fixture test_brand
    uses, and for the same reason: a baked partner pack would rename the nouns under these tests."""
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)


def test_a_seeded_app_is_told_the_word_not_the_token(tmp_path: Path):
    """The reported bug. With the default pack the agent is told to say "Data Source", which is
    what the rail, the panel and the buttons beside it already say."""
    tmpl = _template_with_agents(tmp_path, _SAYS)
    ws = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl).ensure("proj1")

    said = (ws.path / "AGENTS.md").read_text()

    assert "Say **Dataset** and **Data Source**" in said
    assert "{dataSource}" not in said


def test_reset_app_seeds_the_same_words(tmp_path: Path):
    """The second copy site, and the one that repairs an app seeded before this fix: `ensure` never
    replaces a file that is already there (#40), so Reset app is the way an existing app gets it."""
    tmpl = _template_with_agents(tmp_path, _SAYS)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    ws = mgr.ensure("proj1")
    # An app carrying the unvoiced file, exactly as one seeded before the fix would.
    (ws.path / "AGENTS.md").write_text(_SAYS)

    mgr.reset()

    said = (ws.path / "AGENTS.md").read_text()
    assert "Say **Dataset** and **Data Source**" in said
    assert "{dataSource}" not in said


def test_a_partners_own_word_reaches_the_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Which is the whole point of the token being there. A pack that calls it something else must
    reach the model, or the agent narrates a noun that is on nobody's screen."""
    pack = tmp_path / "brand.json"
    pack.write_text(json.dumps({"nouns": {"dataSource": {"singular": "Connection",
                                                         "plural": "Connections"}}}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(pack))

    tmpl = _template_with_agents(tmp_path, _SAYS)
    ws = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl).ensure("proj1")

    assert "Say **Dataset** and **Connection**" in (ws.path / "AGENTS.md").read_text()


def test_the_rest_of_the_template_is_still_copied_byte_for_byte(tmp_path: Path):
    """Voicing is for the files that carry tokens and nothing else. `copy2` is what keeps the +x bit
    Domino needs to run `app.sh`, and a source file rewritten through a brand pack is a source file
    somebody has to explain."""
    tmpl = _template_with_agents(tmp_path, _SAYS)
    (tmpl / "app.sh").write_text("#!/bin/bash\nexec python serve.py\n")
    (tmpl / "app.sh").chmod(0o755)

    ws = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl).ensure("proj1")

    assert (ws.path / "src" / "App.tsx").read_bytes() == (tmpl / "src" / "App.tsx").read_bytes()
    assert (ws.path / "app.sh").stat().st_mode & 0o111  # still executable


def test_nothing_in_the_template_carries_a_pack_token_into_the_model_unresolved():
    """The drift guard, and the only test here that reads the real template.

    Not "AGENTS.md is voiced" — that is the bug we just fixed, and a guard shaped like the fix
    catches nothing new. The rule is about the token: a document under `template/` that names one of
    the pack's own nouns has to be branded somewhere on its way to the model, and there are exactly
    two somewheres. A third document growing a `{dataSource}` fails here rather than in a plan.

    Markdown only, and the boundary is the point rather than a convenience. Prose is where a brand
    token means "say the pack's word"; in source, braces are syntax. `serve.py` documents a
    Domino URL as `/u/{owner}/{project}/app/` and `App.tsx` writes `basename={appBase}`, and both
    would be WRONG to resolve — which is also why `_VOICED_SEED` is a small named set and not a
    suffix rule.
    """
    tokens = _pack_tokens()
    # The Chat template is not seeded: it is inlined into `opencode.json` as the sage-chat prompt
    # and voiced by `brand.apply_agent_voice`. Named here so the exemption is a decision on the page
    # rather than a gap in the scan.
    inlined_and_voiced = {_TEMPLATE / "chat" / "AGENTS.md"}

    unvoiced = []
    for path in _TEMPLATE.rglob("*.md"):
        if any(p in {"node_modules", "dist", "__pycache__"}
               for p in path.relative_to(_TEMPLATE).parts):
            continue
        named = {m.group(1) for m in _TOKEN.finditer(path.read_text())} & tokens
        if not named:
            continue
        if path in inlined_and_voiced or path.name in _VOICED_SEED:
            continue
        unvoiced.append(f"{path.relative_to(_REPO)} names {sorted(named)}")

    assert not unvoiced, (
        "These template files carry brand pack tokens and nothing resolves them before the model "
        "reads them — the model will write the token out as a word. Add the file to "
        "`manager._VOICED_SEED`, or voice it where it is loaded:\n  " + "\n  ".join(unvoiced))


def test_the_real_template_agents_file_still_needs_voicing():
    """The other half of the guard. The one above passes trivially if the template stops using
    tokens at all — which would be a silent un-branding, not a fix."""
    body = (_TEMPLATE / "react-vite" / "AGENTS.md").read_text()
    named = {m.group(1) for m in _TOKEN.finditer(body)} & _pack_tokens()

    assert "dataSource" in named, "the build agent's instructions no longer name the nouns as pack "
    assert "AGENTS.md" in _VOICED_SEED
    assert manager._seed_file is not None
