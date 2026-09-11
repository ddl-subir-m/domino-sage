"""A Rail that opened itself must close again (#150 follow-up).

The Rail gets out of the way once it has answered you — that is why clicking one of its rows
collapses it, and why the expanded head's own New conversation button does. A New app is the same
act read from Build's side: it puts a new app in the preview, and the preview is the surface 260px
of Rail is taking from. It was the one picking act with no collapse behind it.

What made it reachable is that nothing was ending an auto-expand. `expandRail` is write-free and
is not meant to last — it exists so the pending row is visible the moment Chat's collapsed head is
pressed — but only a row click ever undid it. Somebody who STARTED a conversation never makes that
click, so the Rail stayed open through the whole conversation, crossed into Build with it (one Rail
serves both modes, #82), and was still open over the new app.

There are two ends to that open, and they are not the same act. A New app is a pick, like clicking
a row, so it closes the Rail whoever opened it. The first message is not — `newThread` is where the
auto-expand's own reason runs out ("the flag has done its job"), so the close there is guarded: a
Rail somebody chose survives typing, because typing answers nothing the Rail asked.

Only running shows any of it. The rule lives in three files at once: the store owns the value,
`prefs.js` owns which door may write it, and each component decides what an open panel costs. So
the harness presses the real controls the Rail draws and reports the panel AND the record on file.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "rail_auto_expand_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _act(act: str) -> dict:
    """One way the Rail came to be open, then a New app, then the Rail as next seen."""
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


def test_a_new_app_closes_a_rail_that_opened_itself():
    """The reported path. A conversation started from Chat's collapsed head, carried into Build,
    and a New app over the top of it — the Rail was still holding the list."""
    out = _act("chat-plus-then-new-app")
    assert out["openedBefore"] is True, "the Rail never opened, so the New app proves nothing"
    assert out["railHidden"] is True
    assert out["railDrawsTheList"] is False, "the Rail is still holding 260px of Build"


def test_a_new_app_does_not_take_the_choice_somebody_made():
    """The other half of the rule. `toggleRail` is the only door that writes, so a Rail opened by
    hand must close for this New app and still be open on the next load. A collapse that wrote
    would file a choice the person never made over the one they did."""
    out = _act("rail-opened-by-hand")
    assert out["openedBefore"] is True
    assert out["railHidden"] is True
    assert out["storedRailHidden"] is False, "the hand-made choice was overwritten"


def test_the_first_message_closes_a_rail_that_opened_itself():
    """The Chat half. `expandRail` opens the Rail so the press has a visible answer, and until now
    only a row click undid it — a click the person who STARTED the conversation never makes. So the
    open ran to the end of the session. It ends where its reason does instead."""
    out = _act("chat-plus-then-type")
    assert out["openedBefore"] is True
    assert out["threadId"] == "t-new", "no conversation opened, so nothing ended the expand"
    assert out["railHidden"] is True
    assert out["railDrawsTheList"] is False


def test_the_first_message_leaves_a_rail_somebody_opened_alone():
    """The guard, and the reason the auto-expand is tracked at all rather than every close being
    unconditional. Clicking a row ANSWERS the Rail — it asks which conversation you are looking at
    — so it may close one opened by hand. Typing the first message answers nothing it asked."""
    out = _act("hand-opened-then-type")
    assert out["threadId"] == "t-new"
    assert out["railHidden"] is False, "typing shut a Rail the person opened on purpose"
    assert out["railDrawsTheList"] is True
    assert out["storedRailHidden"] is False
