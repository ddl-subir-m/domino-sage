"""#431: the prompt line that names `live_read_query` never reached any Project.

`_data_use_note` returned `""` unless the Project carried `dataUseVersion`, and that key was
written in exactly one place — the `if fresh:` arm of `WorkspaceManager.ensure`, whose test was
"the volume root holds nothing but `.git`". A Domino Project's volume arrives holding its cloned
repo, so the arm never ran and no Project ever carried the key. Measured 2026-09-18 from a
Workspace shell on a Project created that day: `/mnt/code` held `AGENTS.md`, `package.json`,
`src/`, `serve.py` and fifteen more at `Initial commit`, and `.sage/settings.json` held
`displayName` and `membershipBackfilled` and no version key at all.

So the flag is gone rather than fixed, and this file pins the half of that which a refusal test
cannot reach. THE REFUSALS WERE NEVER THE WHOLE BUG. A model that is never told an operation
exists does not reach a gate to be refused by it — it improvises, which is how #428's Project
reached a person as "go write the SQL yourself". Deleting the three refusals without delivering
this line would leave the tool unreachable and every test green.

WHY THIS ASSERTS LITERAL STRINGS. The two real-OpenCode tests that exercise the note build their
prompt with `orch._data_use_note()` themselves, so they would compose, send and pass on `""` — and
they skip wherever `node_modules` is absent, which is every worktree. They cannot fail on the
thing this ticket is about. This one asks the composed prompt for the tool's name instead.
"""

from __future__ import annotations

import json
from pathlib import Path

from .test_chat_turn import _orch

TID = "thr_du"

# What Domino's clone leaves at the volume root before Sage's first `ensure`, trimmed to the
# entries that matter: `.git` (which the old freshness test forgave), one tracked file beside it
# (which it did not), and a settings file already holding keys that are not ours.
CLONED = {
    "AGENTS.md": "# Project\n",
    "package.json": "{}\n",
    "serve.py": "# placeholder\n",
}


def _cloned_project(tmp_path: Path):
    """A Project whose volume root is populated BEFORE Sage ever looks at it.

    This is the fixture the ticket was filed about. The one it replaces built a directory holding
    only `.git` and asked for `seed_app=False` — a state production cannot produce, and the reason
    a green suite sat on top of a capability that was off everywhere.
    """
    root = tmp_path / "mnt" / "code"
    (root / ".git").mkdir(parents=True)
    for name, body in CLONED.items():
        (root / name).write_text(body)
    (root / ".sage").mkdir()
    (root / ".sage" / "settings.json").write_text(
        json.dumps({"displayName": "Default", "membershipBackfilled": True}))
    return _orch(tmp_path)


def test_a_project_that_arrived_as_a_clone_is_told_it_can_work_a_number_out(tmp_path: Path):
    """The whole ticket, at the only place it can be seen: the text the model receives."""
    orch, _ = _cloned_project(tmp_path)

    prompt = orch._chat_prompt(TID, "how many calls did we take last month?", {"items": []})

    # Note-only strings, every one of them. A plant put the gate back and `live_read_query` was
    # STILL in the prompt — the token paragraph names all three tools whatever the note does, so
    # asserting the tool's bare name here would have passed on the bug this test exists for.
    assert "one SELECT statement as sql" in prompt, "the model was never told how to reach it"
    assert "For CSV totals use live_read_files with operation=sum" in prompt
    assert "operation=analyze_text" in prompt


def test_the_note_does_not_depend_on_anything_in_the_projects_settings(tmp_path: Path):
    """No successor flag. The line this ticket restored must not become gated again on a key that
    a clone-shaped Project has no way to acquire, which is the failure being undone."""
    orch, _ = _cloned_project(tmp_path)
    record = orch.project(start_preview=False).record

    settings = record.read_settings()
    assert settings.get("displayName") == "Default", "ensure must leave what was already there"
    assert not [key for key in settings if "ataUse" in key or "dataVersion" in key], settings
    assert "one SELECT statement as sql" in orch._chat_prompt(TID, "count them", {"items": []})
