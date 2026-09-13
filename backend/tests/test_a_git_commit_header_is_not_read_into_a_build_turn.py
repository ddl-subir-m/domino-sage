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


PROBE = "Read git history without printing an email address"


def rule() -> str:
    """Just this bullet, from its lead-in to the next top-level one.

    Scoped rather than whole-file: an assertion over all ~200 lines of AGENTS.md answers a
    question about the whole document, so an unrelated later edit elsewhere — a `sed -i` example,
    a sentence about never redacting the user's data — reddens a test named for this rule and
    sends the reader to a bullet nobody touched.
    """
    _, marker, rest = agents().partition("- **" + PROBE)
    assert marker, "the git-history bullet is gone from AGENTS.md"
    # `\n- `, not `\n- **`: 21 bullets in this file are not bolded, so a bolded delimiter runs
    # past the next plain one and swallows bullets this rule does not govern. Continuation lines
    # are indented, so they do not end the slice.
    body, _, _ = rest.partition("\n- ")
    return marker + body


def flat() -> str:
    """The rule with its line wrapping normalised away.

    A probe that spans a hard line break pins where the paragraph happens to wrap: add a word
    anywhere earlier and the reflow reddens a test while the rule says exactly the same thing.
    """
    return " ".join(rule().split())


# ---- the rule itself ----------------------------------------------------------------------------


def test_the_agent_is_given_the_git_forms_that_print_no_header():
    # Named forms rather than "be careful": the agent reaches for git to answer a real question
    # (which commit touched this file, what changed), and a rule that only forbids leaves it to
    # guess a replacement.
    body = flat()
    assert "git log --oneline" in body
    # Quoted, because `git log --format=%h %s` unquoted is a fatal error ("ambiguous argument
    # '%s'") — an instruction that hands the agent a command that does not run costs the turn it
    # was meant to save.
    assert 'git log --format="%h %s"' in body
    # The one that replaces `git show HEAD --stat`, which is what the traced turn actually wanted.
    assert "git show --stat --format=" in body


def test_the_commit_header_route_is_named_with_its_reason():
    # The reason, not just the blocklist: it is what lets the agent judge a command nobody listed.
    body = flat()
    assert "Plain `git log` and `git show` print the commit header" in body
    assert "author line carries one" in body


def test_blame_is_named_as_a_second_route_and_not_folded_into_the_header():
    """The two leak by DIFFERENT mechanisms, and saying so is the whole point.

    Checked live: `git blame -e` prints no commit header at all — it prints
    `^badf913 (<someone@example.com> 2026-07-20 …)` on every line. An earlier draft of this rule
    listed it beside `git log` and `git show` as a thing that "prints the commit header". An agent
    applying the rule's own generalisation test to it — does this print a header? no — would run
    it and lose the step, which is the incident the rule exists to prevent.
    """
    body = flat()
    assert "`git blame` prints no header" in body
    assert "puts one on every line it emits" in body
    # The property, then the spellings. `--show-email` is `-e`'s long form and leaks identically
    # (verified live); two rounds of this rule named `-e` alone and blessed the long form by
    # omission, which is why the sentence is about what a flag ASKS FOR.
    assert "every\n  flag that asks it for the author's address" in rule()
    for flag in ("`-e`", "`--show-email`", "`--porcelain`", "`--line-porcelain`"):
        assert flag in body, flag


def test_the_rule_generalises_on_the_address_rather_than_on_the_header():
    """The half that regresses into a blocklist.

    An agent told only "never `git show`" still has to answer "which files did this commit touch",
    and `git log -1 --stat`, `git show -s` and `git whatchanged` all print the same header. But
    "the header is the thing" is too narrow in the other direction — it clears every blame form
    above. The address is the predicate that covers both routes.
    """
    body = flat()
    assert "What you are avoiding is the ADDRESS — not one command and not one flag" in body
    assert "work out what it will actually print" in body


def test_the_safe_list_is_closed_and_names_no_escape_hatch():
    """Without a safe list the safe reading is "stop using git", and the agent loses `git status`
    and `git diff`, which are how it sees its own working tree. But the list has to be a set of
    COMMANDS, never "anything except these flags".

    Three drafts of this rule ended in a permission sentence, and each one blessed a leaking
    command by omission: "`git blame` without `-e`" cleared `--porcelain` (verified live:
    `author-mail <someone@example.com>`, no `-e` anywhere), and "with none of those three flags"
    then cleared `--show-email` (verified live: an address on every line). The third failure of
    one shape is the shape's fault, so the sentence is gone rather than patched again. `git blame`
    is now described by what its flags do and blessed by nothing.
    """
    body = flat()
    assert "`git status --short`, `git diff`." in body
    for hatch in ("are fine", "is fine", "none of those", "without `-e`"):
        assert hatch not in body, hatch


def test_the_rule_does_not_ask_anyone_to_redact():
    """ADR-0022: Sage never redacts to get past policy. The rule avoids printing the header; it
    never proposes stripping the author line out of one that was printed."""
    lowered = rule().lower()
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

    These two tests assert CARRIAGE and nothing more: the rule is in the file the app ends up
    with, by each of the two routes a file gets there. They do not prove voicing, and the real
    body does not make them prove it — the rule holds no `{token}`, so both stay green under an
    `apply_voice` that mangles every one. Voicing is covered where it belongs, with a token probe:
    `test_the_agents_file_reaches_the_model_in_the_packs_words`, over these same two routes.
    """
    t = tmp / "template"
    (t / "src").mkdir(parents=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text(agents(), encoding="utf-8")
    (t / "node_modules" / ".bin").mkdir(parents=True)
    (t / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh")
    return t


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
    # Two probes: the H1, and a sentence from the rules body. The H1 alone catches only an inline
    # that copied the file from its title — and a prompt that grew a copy of the RULES without the
    # document heading is the likelier shape, including one carrying this very git bullet, which
    # would then need every edit twice with nothing saying so.
    for probe in ("Building apps in this workspace", PROBE):
        carriers = [name for name, a in cfg["agent"].items() if probe in a.get("prompt", "")]
        assert carriers == [], (probe, carriers)


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
