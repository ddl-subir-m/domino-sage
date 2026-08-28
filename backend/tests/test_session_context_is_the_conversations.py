"""Session context belongs to the Conversation, in both modes.

`CONTEXT.md` defines Session context as what is in scope for a Thread right now, and the store
files it under one `context.json` per Conversation. The Workbench honours that by mounting one
Composer in both modes and giving it one list to read. That already holds; nothing tested it, and
the obvious-looking next step once Build grows a header app selector (#82) is to make the chips
follow the selected Built App. They must not.

The four tests below run the real Composer — both mounts, one store, one server — because the
property is about what two renders show, and reading the source proves it only for the source as
written today. The fifth is the cheap structural half: neither mode may hand the Composer a
context of its own to render.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
_HARNESS = Path(__file__).resolve().parent / "js" / "composer_context_harness.mjs"

# The last test is pure Python and reads the two mode files, so it runs everywhere.
needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_MARGINS = {"id": "file:margins.csv", "name": "margins.csv", "kind": "file",
            "path": "margins.csv"}


def _run(steps: list[dict]) -> list[dict]:
    """Each entry is what both composers drew, and what the server held, after one step."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_both_composers_show_the_conversations_chips():
    """One conversation, one context: opening it in either mode draws the same row, and the row is
    the server's — not a list either composer built for itself."""
    opened = _run([])[0]
    assert opened["chat"] == ["sales.csv"]
    assert opened["build"] == opened["chat"] == opened["server"]


@needs_node
def test_a_chip_added_in_build_reaches_the_chat_composer():
    """Dropped on the Build composer, so the add is Build's. Chat is showing the same conversation
    and has no separate list to miss it."""
    after = _run([{"drop": {"on": "build", "resource": _MARGINS}}])[-1]
    assert after["build"] == ["sales.csv", "margins.csv"]
    assert after["chat"] == after["build"] == after["server"]


@needs_node
def test_a_chip_closed_in_chat_is_gone_from_the_build_composer():
    """Closing a chip takes the resource out of the conversation, which is the only place it was.
    A Build composer holding its own copy would go on offering it to the next turn."""
    steps = [{"drop": {"on": "build", "resource": _MARGINS}},
             {"closeChip": {"on": "chat", "name": "margins.csv"}}]
    after = _run(steps)[-1]
    assert after["chat"] == ["sales.csv"]
    assert after["build"] == after["chat"] == after["server"]


@needs_node
def test_switching_built_app_does_not_change_the_session_context_row():
    """A Project holds many Built Apps and switching between them reloads the whole of Build — the
    transcript, the Bindings, the plan pin. The conversation is not one of those things, so its
    context row is untouched. This is the guard #82 is about."""
    steps = [{"drop": {"on": "build", "resource": _MARGINS}}, {"selectApp": "app_beta"}]
    before, after = _run(steps)[-2:]
    assert after["activeApp"] == "app_beta"          # the switch really happened
    assert before["activeApp"] == "app_alpha"
    assert after["build"] == before["build"] == ["sales.csv", "margins.csv"]
    assert after["chat"] == after["build"] == after["server"]


def _composer_props(source: str) -> list[set[str]]:
    """The prop names each `SW.Composer` mount passes, one set per mount."""
    text = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`]*`", "''", source)   # values, not names
    mounts = []
    for match in re.finditer(r"SW\.Composer,\s*\{", text):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "[": 1, "(": 1}.get(text[i], 0)
            depth -= {"}": 1, "]": 1, ")": 1}.get(text[i], 0)
            i += 1
        parts, depth, start, body = [], 0, 0, text[match.end():i - 1]
        for pos, ch in enumerate(body):
            depth += {"{": 1, "[": 1, "(": 1}.get(ch, 0)
            depth -= {"}": 1, "]": 1, ")": 1}.get(ch, 0)
            if ch == "," and depth == 0:
                parts.append(body[start:pos])
                start = pos + 1
        parts.append(body[start:])
        names = {p.split(":")[0].strip() for p in parts}
        mounts.append({n for n in names if re.fullmatch(r"\w+", n)})
    return mounts


@pytest.mark.parametrize("mode", ["chat.js", "builder.js"])
def test_neither_mode_hands_the_composer_a_context_of_its_own(mode):
    """The composer reads the conversation's context out of the store. A mode that passed one in
    would be a mode with an opinion about what is in scope, and the two modes would drift."""
    mounts = _composer_props((_JS / "modes" / mode).read_text())
    assert mounts, f"{mode} no longer mounts SW.Composer"
    for names in mounts:
        assert not [n for n in names if re.search(r"context|attach|chip|resource|app", n, re.IGNORECASE)], (
            f"{mode} passes the Composer a context of its own: {sorted(names)}"
        )
