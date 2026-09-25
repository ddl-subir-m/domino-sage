"""The implement agent sends one call per file and reads its edits back in one message (#534).

MEASURED 2026-09-24, a TFL Build (fastapi-antd, sonnet, `sourceRevision 923cd487`): 22 implement
calls, 420 s of model time, 305 s of it before each call's first action. So the cost is CALLS:

    calls 5-10   six `apply_patch`, one per call, most to the same files     ~124 s
    calls 11-16  seven reads of files the turn had just written, 1-2 per call  ~77 s

Both followed the prompt. The workspace AGENTS.md said "Send one edit at a time to a given file",
which is right about PARALLEL edits and was read as one CHANGE per call. The implement prompt said
to read back the changed lines, and nothing said to do it in one message.

The read-back itself stays, and that is measured too. Real OpenCode 1.18.4 against a scripted model
(2026-09-24): a hunk with no `@@` line returns "Success. Updated the following files: M a.js" and
writes nothing. The model cannot tell that from a real edit without reading the file. What goes is
the ROUND TRIPS: one `apply_patch` envelope carrying two bare-`@@` hunks in one file and an
`*** Add File:` applied both hunks and the new file in one call.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _implement_prompt() -> str:
    return json.loads((REPO / "opencode.json").read_text())["agent"]["sage-implement"]["prompt"]


def _bullet(stack: str) -> str:
    text = (REPO / "template" / stack / "AGENTS.md").read_text()
    start = text.index("- **Send one edit at a time to a given file.**")
    return " ".join(text[start:text.index("\n- **", start + 1)].split())  # unwrap the markdown


def test_the_read_back_is_one_message_for_every_changed_file():
    prompt = _implement_prompt()
    assert "read back every file you changed in ONE message" in prompt
    assert "After editing, read back the changed lines needed to confirm" not in prompt
    # The guard the read-back exists for is still named, so nobody deletes it as mere latency.
    assert "Read the changed lines after editing" in prompt


def test_the_one_call_per_file_rule_left_the_text_every_model_reads():
    """The rule above is true of a patch envelope and false of `edit`, which takes one `oldString`
    per call. OpenCode offers `apply_patch` to a GPT handle alone (#539), so the rule moved to the
    turn prompt of a GPT turn (#541) — `test_a_gpt_implement_turn_is_told_the_patch_envelope` holds
    it there. What is held here is the other half: the shared text no longer tells a model to batch
    changes it has no tool to batch, and no longer names a tool it was never offered."""
    prompt = _implement_prompt()
    assert "apply_patch" not in prompt
    for stack in ("fastapi-antd", "react-vite"):
        bullet = _bullet(stack)
        # Still forbids parallel edits to one file: that race is real and measured.
        assert "comes back rejected" in bullet
        assert "apply_patch" not in bullet
        assert "Write a new file whole the first time" in bullet


def test_the_prompt_describes_only_the_tools_a_model_reading_it_was_offered():
    """The tool set follows the model HANDLE, not the route (real OpenCode 1.18.4, 2026-09-24).
    #539 made Sage name the handle per prompt, so this one static string is read by GPT turns
    offered `apply_patch` AND by GLM, Claude, Gemini and Qwen turns offered `edit`/`write`. It
    cannot describe both, and it cannot know which — so it describes the pair every non-GPT model
    has, and the patch envelope is delivered with the turn instead (#541)."""
    prompt = _implement_prompt()
    assert "apply_patch" not in prompt
    assert "Editing an existing file: use `edit`" in prompt
    assert "A new file: use `write`." in prompt
