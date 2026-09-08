"""⏎ finishes a rename, the way Escape has always abandoned one.

Every rename in the Workbench is one field and one word. Escape closed the box because antd binds
it; ⏎ did nothing, because an Input outside a <form> ignores the key and antd's confirm never reads
it. So the last act of typing a name was reaching for the mouse.

The binding is a handler on the field plus a cursor already in it — either alone is not a binding —
and it runs the box's own save rather than a second copy that could drift from it. The case worth
pinning is the save that does not land: the helper closes the box itself now, so a refusal has to
hold it open with the typed name still in it, which is the one thing antd used to get right for
free.

The Conversation's box is driven here. The Built App's and the plan's are the same act through the
same helper, and the last test says so rather than driving two more.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "rename_on_enter_harness.mjs"
_WORKBENCH = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _rename(act: str) -> dict:
    """Open the rail's Rename box, type a name, and finish it the way `act` says."""
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": act}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@_needs_node
def test_the_box_opens_with_the_cursor_in_it_and_the_key_bound() -> None:
    box = _rename("enter")
    assert box["field"] == "Input"
    assert box["bound"], "the name field takes no ⏎"
    assert box["focused"], "⏎ is bound to a field nobody is typing in yet"


@_needs_node
def test_enter_saves_the_typed_name_and_closes_the_box() -> None:
    box = _rename("enter")
    assert box["patched"] == ["Desk exposure by region"]
    assert box["destroyed"] == ["Rename conversation"]


@_needs_node
def test_the_button_still_saves_the_typed_name() -> None:
    """antd closes the box on its own answer, so this one only has to still save."""
    box = _rename("button")
    assert box["patched"] == ["Desk exposure by region"]
    assert box["destroyed"] == []


@_needs_node
def test_a_refused_save_holds_the_box_open() -> None:
    box = _rename("enter-refused")
    assert box["patched"] == ["Desk exposure by region"]
    assert box["destroyed"] == [], "the box closed over a rename that never happened"


@pytest.mark.parametrize(
    "source",
    ["modes/builder.js", "components/resource-panel.js", "components/conversation-list.js"],
)
def test_every_rename_box_binds_the_key(source: str) -> None:
    """One helper, three boxes — a rename that answers ⏎ in one place and not another is worse
    than one that answers it nowhere, because the reader stops trusting the key."""
    text = (_WORKBENCH / source).read_text()
    assert "SW.util.confirmOnEnter(" in text
    assert "onPressEnter: submit," in text
