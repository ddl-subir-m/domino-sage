"""Clicking a conversation says so, and a row that lost its open can be clicked back (#455).

Two faults that compound, and fixing either alone leaves the click broken.

`openThread` makes three round trips before it writes anything, and that single write is correct —
see the comment above it, and `test_a_newer_open_wins.py` for what spreading it out cost. But
nothing marked the interval, so for its whole length the PREVIOUS conversation stayed on screen
with no spinner and the click read as lost. `state.openingThreadId` is what marks it now: an id
rather than a flag, because the Rail draws it on a row and has to know which.

Then people click again, and the second click could not work either. `SW.router.go` re-emits an
identical hash rather than returning early, but nothing on the route object SAID a navigation had
happened, so a mode keying its open on `[threadId]` saw the same string and did not re-run. Any of
the five non-route `openThread` callers could take the generation out from under a route-driven
open, and from there the row was inert. The router counts navigations now, and the mode keys on
that too.

The harness mounts the mode and the Rail and runs hooks for real, because both faults are about a
dependency array that did not change and a marker that is set but not drawn. Asserting on the
source text would pin the spelling of the fix rather than any of this.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "open_pending_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

# Long enough that an act can be reported while the open is still in flight, short enough not to
# pad the suite. Everything else answers immediately.
_SLOW = {"conv_a": 80}


def _run(acts: list[dict]) -> list[dict]:
    """The store and the screen after each act."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(acts), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _row(step: dict, thread_id: str) -> dict:
    return next(r for r in step["rows"] if r["id"] == thread_id)


@needs_node
def test_the_store_marks_the_conversation_it_is_opening():
    """The marker names the conversation being opened, and it is not the one on screen. Both
    halves matter: a bare boolean could not tell the Rail which row to draw it on."""
    opened, opening = _run([{"act": "click", "thread": "conv_b"},
                            {"act": "settle"},
                            {"act": "click", "thread": "conv_a", "latency": _SLOW}])[-3:][1:]
    assert opened["thread"] == "conv_b" and opened["openingThreadId"] is None
    assert opening["openingThreadId"] == "conv_a"
    assert opening["thread"] == "conv_b"


@needs_node
def test_the_clicked_row_says_it_is_opening():
    """On the row that was clicked, in its own words. The class and the text are read off the
    rendered row rather than off the prop handed to it — a prop nobody draws fixes nothing."""
    opening = _run([{"act": "click", "thread": "conv_a", "latency": _SLOW}])[0]
    row = _row(opening, "conv_a")
    assert row["opening"] is True
    assert "Opening" in row["says"]
    assert row["busy"] == "true"


@needs_node
def test_the_rail_names_the_conversation_being_opened_and_not_the_one_being_left():
    """The Rail answers one question — which conversation are you looking at — so exactly one row
    is active, and from the click onwards it is the row that was clicked. Leaving the highlight on
    the conversation being left while its neighbour spins is the Rail naming two."""
    opening = _run([{"act": "click", "thread": "conv_b"},
                    {"act": "settle"},
                    {"act": "click", "thread": "conv_a", "latency": _SLOW}])[-1]
    assert [r["id"] for r in opening["rows"] if r["active"]] == ["conv_a"]
    assert _row(opening, "conv_b")["opening"] is False


@needs_node
def test_the_previous_conversation_is_not_left_standing_in_for_the_one_arriving():
    """The fault as it was reported: the click looks lost because the answer to the last one is
    still on screen. Asserted on the turns DRAWN, not on the store — the store was always right
    about which conversation it held."""
    steps = _run([{"act": "click", "thread": "conv_b"},
                  {"act": "settle"},
                  {"act": "click", "thread": "conv_a", "latency": _SLOW}])
    settled, opening = steps[1], steps[2]
    assert settled["pane"] == "turns" and settled["drawnTurns"] == ["b turn"]
    assert opening["pane"] == "skeleton"
    assert opening["drawnTurns"] == []
    # Counted, because the wrapper's class is not the thing anyone reads. Keyed on the class
    # alone, deleting both skeletons left this test green over a blank pane.
    assert opening["skeletons"] == 2
    assert settled["skeletons"] == 0


@needs_node
def test_the_marker_clears_in_the_same_write_as_the_view():
    """In the write, not a line after it. `openThread` keeps going once the view is painted — it
    reads attachments — so a clear that happens at the end of the function instead leaves a window
    where the turns are on screen under a row still saying it is opening.

    The window is what this measures: the conversation answers at once and its attachments do not,
    and the snapshot is taken inside the gap. Settling past it would pass on either."""
    settled = _run([{"act": "click", "thread": "conv_b"},
                    {"act": "settle"},
                    {"act": "click", "thread": "conv_a",
                     "latency": {"conv_a": 0, "conv_a:context": 120}},
                    {"act": "settle", "ms": 25}])[-1]
    assert settled["thread"] == "conv_a"
    assert settled["openingThreadId"] is None
    assert settled["pane"] == "turns" and settled["drawnTurns"] == ["a turn"]
    assert _row(settled, "conv_a")["opening"] is False


@needs_node
def test_a_failed_open_clears_the_marker_on_its_way_out():
    """`conv_gone` is in the Rail and not on the server — a conversation deleted in another tab.
    The mode catches the 404 and routes to `#/chat`; nothing is left running there that could
    clear a marker, so a marker still set is a spinner with nothing behind it.

    Clicked from the landing page, with nothing open. That is the case `openThread` has to clear
    for itself: arriving at `#/chat` WITH a conversation on screen clears it through
    `clearConversation` on the way past, so a failure tested from there would be green on a
    function that never cleared its own marker at all."""
    failed = _run([{"act": "click", "thread": "conv_gone"},
                   {"act": "settle"}])[-1]
    assert failed["openingThreadId"] is None
    assert failed["hash"] == "#/chat"
    assert failed["pane"] != "skeleton"


@needs_node
def test_a_stranded_row_loads_when_it_is_clicked_again():
    """The second fault. A non-route caller takes the generation, so the route names `conv_a` and
    the view holds `conv_x`. Clicking `conv_a` re-emits the hash it is already at, which is the
    case that used to change nothing."""
    steps = _run([{"act": "click", "thread": "conv_a", "latency": _SLOW},
                  {"act": "supersede", "thread": "conv_x"},
                  {"act": "settle"},
                  {"act": "click", "thread": "conv_a", "latency": {}},
                  {"act": "settle"}])
    stranded, recovered = steps[2], steps[4]
    assert stranded["hash"] == "#/chat/conv_a" and stranded["thread"] == "conv_x"
    assert recovered["thread"] == "conv_a"
    assert recovered["drawnTurns"] == ["a turn"]


@needs_node
def test_a_cross_project_open_keeps_its_marker_through_the_scope_switch():
    """The slowest open there is, and the one a clear in the scope switch takes the marker off.

    `openThread` awaits `adoptThreadScope`, which calls `setScope` whenever the conversation
    belongs to another Project, and `setScope` nulls `state.thread` and reloads the Project before
    the rest of the open runs. A marker cleared there leaves `thread` null and nothing marked, so
    the pane falls to the blank landing — the symptom this ticket was opened on, reintroduced by
    the fix for it, on the open that lasts longest.

    The snapshot is taken INSIDE `setScope`'s own reload, which is what `threads:list` holds
    open. Settling past it passes either way."""
    inside = _run([{"act": "click", "thread": "conv_b"},
                   {"act": "settle"},
                   {"act": "click", "thread": "conv_far", "latency": {"threads:list": 150}},
                   {"act": "settle", "ms": 40}])[-1]
    # The scope switch has run: it is what nulls the open conversation.
    assert inside["thread"] is None
    assert inside["openingThreadId"] == "conv_far"
    assert inside["pane"] == "skeleton" and inside["skeletons"] == 2
    assert [r["id"] for r in inside["rows"] if r["opening"]] == ["conv_far"]


@needs_node
def test_the_newer_open_still_wins():
    """The guard stays. Click A then B with A slow: B is what the person is looking at, so B is
    what the store settles on and B's row is the one that was marked."""
    steps = _run([{"act": "click", "thread": "conv_a", "latency": _SLOW},
                  {"act": "click", "thread": "conv_b"},
                  {"act": "settle"}])
    second_click, settled = steps[1], steps[2]
    assert second_click["openingThreadId"] == "conv_b"
    assert [r["id"] for r in second_click["rows"] if r["opening"]] == ["conv_b"]
    assert settled["thread"] == "conv_b" and settled["drawnTurns"] == ["b turn"]


@needs_node
def test_re_reading_the_open_conversation_does_not_mark_its_own_row():
    """`openThread` is also how the store RE-READS the conversation already on screen — after a
    turn ends somewhere else, after an investigation closes, when a handoff is drafted. There the
    marker names the conversation you are looking at, and the row must stay as it is: its turns
    are fully drawn beside it, so a spinner and a lost timestamp describe nothing that is missing.

    The centre pane got this guard when it was written; the row did not."""
    reread = _run([{"act": "click", "thread": "conv_a"},
                   {"act": "settle"},
                   {"act": "supersede", "thread": "conv_a", "latency": _SLOW}])[-1]
    assert reread["openingThreadId"] == "conv_a"
    assert reread["thread"] == "conv_a"
    assert _row(reread, "conv_a")["opening"] is False
    assert "Opening" not in _row(reread, "conv_a")["says"]
    assert reread["pane"] == "turns" and reread["drawnTurns"] == ["a turn"]


@needs_node
def test_the_composer_dock_stays_on_screen_while_a_conversation_opens():
    """`chatRunning` is project-wide, so a turn can be running while you open a different
    conversation — and Stop lives in the turn bar inside this dock. Drawing the wait in place of
    the whole column took the one way out of a wedged turn off the screen for its duration, and
    took the half-typed message with it, since the draft is the composer's own state.

    Typing survives; SENDING does not. `send` posts through `state.thread`, which is still the
    conversation being left. The plan bar goes for the same reason it is a fault elsewhere: the
    plan and the app it names belong to the conversation being left."""
    steps = _run([{"act": "click", "thread": "conv_b"},
                  {"act": "settle"},
                  {"act": "click", "thread": "conv_a", "latency": _SLOW}])
    settled, opening = steps[1], steps[2]
    assert settled["dock"] is True and opening["dock"] is True
    assert settled["composerDisabled"] is False
    assert opening["composerDisabled"] is True
    # `conv_b` carries a plan, so the bar is drawn when it is open. That is what makes its
    # absence during the open a fact rather than a bar nothing ever draws.
    assert settled["planbar"] is True
    assert opening["planbar"] is False


@needs_node
def test_the_rail_keeps_a_row_lit_when_the_marker_names_one_it_is_not_drawing():
    """The marker can name a conversation this rail has no row for — an app filter it has not
    touched, a search needle it does not match, or an open started somewhere that is not the list
    (the Resource Browser, a plan link). Asked per row, that lights nothing: the row that was lit
    goes dark and none replaces it, which is a worse answer than the one it replaced."""
    filtered = _run([{"act": "click", "thread": "conv_b"},
                     {"act": "settle"},
                     {"act": "filter", "app": "app-x"},
                     {"act": "supersede", "thread": "conv_a", "latency": _SLOW}])[-1]
    assert filtered["openingThreadId"] == "conv_a"
    assert [r["id"] for r in filtered["rows"]] == ["conv_b"]
    assert [r["id"] for r in filtered["rows"] if r["active"]] == ["conv_b"]


@needs_node
def test_clicking_a_row_that_is_already_opening_costs_nothing():
    """Clicking again is what people do, and the row stays clickable while it loads. The second
    click must not restart the read — counted on the wire, because nothing on screen tells a
    re-fetch from a no-op."""
    settled = _run([{"act": "click", "thread": "conv_a", "latency": _SLOW},
                    {"act": "click", "thread": "conv_a"},
                    {"act": "settle"}])[-1]
    assert settled["threadReads"] == ["conv_a"]
    assert settled["thread"] == "conv_a"
