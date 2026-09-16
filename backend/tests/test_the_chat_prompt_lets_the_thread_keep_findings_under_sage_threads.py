"""The pinned prompt exempts `findings.md` from the `.sage/` bans, in BOTH copies.

`template/chat/AGENTS.md` forbids writing under `.sage/` and separately forbids reading `.sage/`
at all. A skill body cannot reliably beat an explicit system-prompt prohibition, so making the
file reachable on disk (`ensure_chat_workdir`) buys nothing while the prompt still says no.

Both copies, because `template/chat/AGENTS.md` is the source of truth and `opencode.json`'s
`agent.sage-chat.prompt` is what the model is actually sent. Editing one alone is the failure this
asserts against, and it is the one that bites: the two drift in one-sided commits.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _both() -> tuple[str, str]:
    md = (ROOT / "template" / "chat" / "AGENTS.md").read_text(encoding="utf-8")
    prompt = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))[
        "agent"]["sage-chat"]["prompt"]
    return md, prompt


def test_the_prompt_carves_findings_md_out_of_both_sage_bans():
    md, prompt = _both()
    for probe in (
        # The write ban names its one exception rather than reading as absolute.
        "Do not write under `src/`, `public/`, or `.sage/` — with one exception,",
        "`.sage/threads/<threadId>/findings.md`, below.",
        # The read ban is now about the REST of `.sage/`.
        "Do not READ the rest of `.sage/` either.",
        "`.sage/threads/<threadId>/findings.md` is the one place",
        "under `.sage/` you may read and write",
    ):
        assert probe in md, probe
        assert probe in prompt, probe


def test_the_rest_of_the_read_ban_is_kept_verbatim():
    """The carve-out is an exception, not a repeal. The reason the ban exists — a turn lost opening
    `project-resources.json` and a Thread's `context.json` looking for a connection — is still
    correct and still earned."""
    md, prompt = _both()
    for probe in (
        "opening `project-resources.json` and a Thread's `context.json` looking for a connection.",
        "is in the context block above: use the name it gives",
    ):
        assert probe in md, probe
        assert probe in prompt, probe


def test_the_prompt_says_what_may_go_in_findings_and_what_may_not():
    """The file is committed and pushed, and Sage never reads the remote. An always-on rule is the
    only thing between a measurement log and a row of customer identifiers in git."""
    md, prompt = _both()
    for probe in (
        "Write measurements, never conclusions.",
        "a UTC timestamp",
        "**and its denominator**",
        "Aggregates and column facts only",
        "Never a\nvalue copied out of a row: no identifiers, no names, no exemplars.",
        "This file is committed.",
        "Read it before you plan the turn, and append what you measure.",
        "load the `investigate-weak-signals` skill",
    ):
        assert probe in md, probe
        assert probe in prompt, probe


def test_the_method_itself_is_not_pinned_into_every_turn():
    """The prompt is sent on every Chat turn including a one-word greeting. The protocol earns its
    ~900 characters; the method — discovery, fill-rate measurement, fusion, a worked example — does
    not, and lives in the skill the section points at.

    A size bound rather than a list of words the method would use. The failure is this section
    GROWING into the method, and a word list asserts a negative over the whole 13 KB prompt: it
    passes today by an accident of wording and reds on some later, unrelated edit that happens to
    use the word, pointing at the wrong change.
    """
    md, _ = _both()
    start = md.index("## Keeping findings across turns")
    section = md[start:md.index("\n## ", start + 1)]
    assert len(section) < 1200, len(section)
