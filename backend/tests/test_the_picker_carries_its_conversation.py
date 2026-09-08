"""Picking an app in the Build header brings its Conversation with it.

The picker moved the app and left the transcript behind. You ended up with the header, the preview
and the composer naming one Built App while the messages beside them — and the row lit in the rail —
belonged to another, because `pick` routed through `SW.appRoute`, which keeps whichever Conversation
is open and rewrites only `?app=`. The reverse link had been right for a while: opening a row in the
rail already selects that Conversation's app.

The rule is one sentence, and `SW.util.threadForApp` is where it is written down: stay where you are
if this Conversation has already touched that app, otherwise open the newest one that has, and if
none has, start clean.

The assertion is the HASH, because the route is the one writer here — Build selects whatever `?app=`
names, and the store follows it a render later (see the one-writer rule in
`test_build_keeps_the_conversation_rail.py`). Nothing is mounted; see `js/build_header_harness.mjs`
for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _pick(app: str, thread: str, select: str = "app_a") -> dict:
    steps = [{"pick": app, "thread": thread, "select": select}]
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])[-1]


@needs_node
def test_a_conversation_that_already_touched_the_app_is_the_one_you_stay_in():
    """The common case, and the one that must not move: you are reading the Conversation that built
    both apps, and you flip the preview between them. Sending that pick to some other Conversation
    would throw away the reading position of the person who is plainly already in the right place.

    `thr_many` changed P&L report, so picking it keeps the thread segment it arrived with."""
    step = _pick("app_b", "thr_many")
    assert step["hash"] == "#/build/thr_many?app=app_b"


@needs_node
def test_a_conversation_that_never_touched_the_app_hands_you_to_the_one_that_did():
    """`thr_one` only ever built Desk dashboard, so it has nothing to say about P&L report. Staying
    in it is the defect: the header would name P&L report over a transcript that never mentions it.

    Two Conversations touched app_b — `thr_many` and `thr_two` — and the newest wins. The order is
    the server's: `ThreadStore.list` answers newest-activity-first and the client keeps that order,
    so the helper takes the first match instead of forming a second opinion about recency."""
    step = _pick("app_b", "thr_one")
    assert step["hash"] == "#/build/thr_many?app=app_b"


@needs_node
def test_an_app_no_conversation_has_touched_starts_a_clean_one():
    """Rate curve viewer is in the list and in nobody's history. There is no transcript to carry, so
    the pick opens a Conversation-less Build rather than parking the app you just chose under the
    last conversation you happened to be reading.

    No thread segment in the hash is the whole claim — `#/build?app=` is the grammar a Build with no
    Conversation is addressed by."""
    step = _pick("app_c", "thr_many")
    assert step["hash"] == "#/build?app=app_c"
