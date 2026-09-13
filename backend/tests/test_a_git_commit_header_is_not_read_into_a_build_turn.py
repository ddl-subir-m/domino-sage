"""The build agent is told which git commands print a commit header (#328).

Traced live on 2026-09-13. An approve turn ran `git log --all --oneline -- src/App.tsx;
git show HEAD --stat`, and the step after it was refused with `guardrail:Block PII`. `git show`
prints the commit header, the author line in it carries the committer's email address, and an email
address is one of the four shapes Block PII matches. Nothing in the Project was PII: no Chat rows,
no PII file, and the three attached CSVs scanned clean against all four shapes. The refused content
was the git header. The person was told only that a tool read "something".

So the fix is a rule in the instructions the build agent reads, and the rule has to be about the
HEADER rather than about `git show`: an agent told "never `git show`" still needs a commit's file
list and reaches for something else that prints one. Redacting author lines in the shim is the
thing this is NOT — ADR-0022's one hard promise is that Sage never redacts to get past policy.

`template/react-vite/AGENTS.md` is the only surface that carries this. The build agent reads it out
of the workspace (`sage-implement`'s own prompt says "Follow AGENTS.md in the workspace"), and the
two ways it gets there are a new Project and Reset app — an existing workspace never re-seeds (#40),
so both paths are asserted here rather than the file alone. The other AGENTS.md, `template/chat`,
is hand-copied into `opencode.json` and would need the same edit twice; it is exempt for a reason
the last test pins rather than leaves to memory.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.workspace.manager import WorkspaceManager

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "template" / "react-vite" / "AGENTS.md"


def agents() -> str:
    return AGENTS.read_text(encoding="utf-8")


# ---- the rule itself ----------------------------------------------------------------------------


def test_the_agent_is_given_the_git_forms_that_print_no_header():
    # Named forms rather than "be careful": the agent reaches for git to answer a real question
    # (which commit touched this file, what changed), and a rule that only forbids leaves it to
    # guess a replacement.
    body = agents()
    assert "git log --oneline" in body
    # Quoted, because `git log --format=%h %s` unquoted is a fatal error ("ambiguous argument
    # '%s'") — an instruction that hands the agent a command that does not run costs the turn it
    # was meant to save.
    assert 'git log --format="%h %s"' in body
    # The one that replaces `git show HEAD --stat`, which is what the traced turn actually wanted.
    assert "git show --stat --format=" in body


def test_the_agent_is_told_which_commands_print_a_header_and_why():
    body = agents()
    for forbidden in ("Plain `git log`", "`git show`", "`git blame -e`"):
        assert forbidden in body, forbidden
    # The reason, because it is what lets the agent generalise to a command nobody listed.
    assert "author line carries an email address" in body


def test_the_rule_is_about_the_header_rather_than_about_one_command():
    """The half that regresses into a blocklist.

    An agent told only "never `git show`" still has to answer "which files did this commit touch",
    and `git log -1 --stat`, `git show -s`, `git whatchanged` all print the same header. The rule
    has to say the header is the thing, or it closes one command and leaves the fault class open.
    """
    body = agents()
    assert "It is the header that does this and not one command" in body


def test_the_rule_names_the_git_commands_that_stay_available():
    # Without this the safe reading of the rule is "stop using git", and the agent loses `git
    # status` and `git diff`, which carry no header and are how it sees its own working tree.
    body = agents()
    assert "`git status --short`, `git diff`, and `git blame` without `-e`" in body


def test_the_rule_does_not_ask_anyone_to_redact():
    """ADR-0022: Sage never redacts to get past policy. The rule avoids printing the header; it
    never proposes stripping the author line out of one that was printed."""
    body = agents()
    lowered = body.lower()
    assert "redact" not in lowered
    assert "sed -i" not in lowered


# ---- the two paths that carry it to the model ----------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_brand(monkeypatch, tmp_path):
    """The default pack, as `test_the_agents_file_reaches_the_model_in_the_packs_words` does: a
    baked partner pack would rewrite the nouns under the file these tests read."""
    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "no-baked-brand.json")
    monkeypatch.delenv("SAGE_BRAND_FILE", raising=False)


def _template_carrying_the_real_agents_file(tmp: Path) -> Path:
    """The smallest template the seed path will take, carrying the REAL instructions.

    The real body rather than a stub sentence, because the seed voices this file: the rule has to
    survive `brand.apply_voice` on its way to the model, not merely exist in the repo.
    """
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text(agents(), encoding="utf-8")
    (t / "node_modules" / ".bin").mkdir(parents=True)
    (t / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh")
    return t


PROBE = "Read git history with `--oneline`"


def test_a_newly_seeded_app_is_told_the_rule(tmp_path: Path):
    tmpl = _template_carrying_the_real_agents_file(tmp_path)

    ws = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl).ensure("proj1")

    assert PROBE in (ws.path / "AGENTS.md").read_text(encoding="utf-8")


def test_reset_app_is_how_an_app_that_already_exists_gets_it(tmp_path: Path):
    """`ensure` never replaces a file that is already there (#40), so every Project that existed
    before this rule landed keeps an AGENTS.md without it until it is reset. Verifying this fix in
    a workspace that already exists would prove nothing."""
    tmpl = _template_carrying_the_real_agents_file(tmp_path)
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=tmpl)
    ws = mgr.ensure("proj1")
    (ws.path / "AGENTS.md").write_text("an older Sage wrote this\n")  # seeded before the rule

    mgr.reset()

    assert PROBE in (ws.path / "AGENTS.md").read_text(encoding="utf-8")


# ---- the mirror ----------------------------------------------------------------------------------


def test_the_build_instructions_are_not_a_second_copy_inside_opencode_json():
    """The check the other AGENTS.md needs and this one does not.

    `template/chat/AGENTS.md` IS `opencode.json`'s `sage-chat` prompt, byte for byte, and editing
    one alone means the model never sees the change. The build agent's file is not inlined anywhere
    — it is read out of the workspace — so this template is the single carrier. Asserted rather
    than remembered: an agent prompt that grew a copy of these instructions would be a second place
    this rule has to be written, and nothing else would say so.
    """
    cfg = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
    carriers = [name for name, a in cfg["agent"].items()
                if "Building apps in this workspace" in a.get("prompt", "")]
    assert carriers == []


def test_the_chat_agent_is_exempt_because_it_is_never_sent_to_the_projects_history():
    """Why the rule is in one template and not both.

    sage-chat holds `bash: allow`, so it COULD run git — the reason it is exempt is not permission
    but instruction: it answers data questions in `.sage/chat-work` and is told outright not to go
    looking around the project. If that sentence ever leaves the Chat prompt, Chat becomes a
    second surface that needs this rule, and this test is what says so.
    """
    cfg = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
    chat = cfg["agent"]["sage-chat"]["prompt"]
    assert chat == (ROOT / "template" / "chat" / "AGENTS.md").read_text(encoding="utf-8")
    assert "Do not list files, do not search, do not `cd`" in chat
