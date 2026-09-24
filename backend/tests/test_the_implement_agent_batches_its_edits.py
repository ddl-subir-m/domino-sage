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


def test_one_apply_patch_carries_every_change_to_a_file():
    prompt = _implement_prompt()
    assert "put every change to one file in one call" in prompt
    for stack in ("fastapi-antd", "react-vite"):
        bullet = _bullet(stack)
        # Still forbids parallel edits to one file: that race is real and measured.
        assert "comes back rejected" in bullet
        assert "all of that file's hunks, each opening with a bare `@@`, in ONE call" in bullet
        assert "Write a new file whole the first time" in bullet


def test_the_prompt_says_what_to_do_when_apply_patch_is_the_only_edit_tool():
    """Production offers `apply_patch` and no `edit`/`write` (#534).

    Real OpenCode 1.18.4, 2026-09-24: the tool set follows the model HANDLE, not the route. Handle
    `gpt-5.4`, which Sage's config uses, gets `apply_patch` and neither `edit` nor `write`; the TFL
    Build's recorded tool schemas agree. The prompt steered at `edit` (#494, measured on Gemini when
    `edit` WAS offered) and gave the tool the model actually had one conditional sentence. Both
    halves stay: `edit` when offered, and plain rules for the only-`apply_patch` case.
    """
    prompt = _implement_prompt()
    choose = prompt.index("Use the edit tool you have.")
    assert choose < prompt.index("Editing an existing file: use `edit`")
    assert "If you have only `apply_patch`" in prompt
