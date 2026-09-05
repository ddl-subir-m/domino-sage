"""The rail draws the conversation you started, before there is one to draw.

"New conversation" clears the open Conversation and lands on a route naming none. The centre
pane has always shown that correctly — an empty transcript — but the rail listed only what was
on disk, and nothing on disk had changed, so the press looked like it had done nothing. This is
the third time that button has been made to look dead; the other two are written up at
`conversation-list.js` and `store.py`'s `loadBuild`, and both were fixed by making the press DO
more. This one is fixed by making it SHOW more, because there is nothing left for it to do:
the conversation genuinely does not exist yet, and writing one so the rail had a row would
leave an empty Conversation on disk for every stray click.

So the row is a placeholder, held in `pendingConversation` and drawn only while no real
Conversation is open. The two claims that keep it honest are the ones below: it is selected and
at the top the moment the button is pressed, and NOTHING is written for it — not when it
appears, not when it is abandoned.

Same harness as the conversation-view tickets and for the same reason: which row the rail says
you are looking at is settled in the store before React draws anything, so the harness drives
the real store and calls `SW.ConversationRail` as the function it is. See
`js/conversation_view_harness.mjs`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "conversation_view_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list[dict]:
    """What the rail drew after each step, plus everything that step wrote."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _active(step: dict) -> list[str]:
    return [row for row in step["rows"] if "is-active" in row.split("|")[0]]


@needs_node
def test_the_press_puts_a_selected_row_at_the_top_of_the_rail():
    """The answer to "which conversation am I looking at" is a row, or the press looked dead."""
    step = _run([{"newConversation": True}])[0]
    assert step["rows"], "the rail drew no rows at all"
    first = step["rows"][0]
    assert "is-active" in first.split("|")[0], f"the top row is not selected: {first}"
    assert "New conversation" in first
    # One selected row. A placeholder that lights up beside a real conversation's row would be
    # saying two different things about where you are.
    assert len(_active(step)) == 1, _active(step)


@needs_node
def test_the_row_says_what_turns_it_into_a_conversation():
    """A row with no timestamp needs to say why it has none, and what to do about it."""
    first = _run([{"newConversation": True}])[0]["rows"][0]
    assert "Send a message to start it" in first


@needs_node
def test_the_placeholder_sits_above_the_conversations_that_do_exist():
    """It belongs to no day and no group: it is not history until it has been said."""
    rows = _run([{"newConversation": True}])[0]["rows"]
    assert len(rows) > 1, "the fixture's Conversations did not reach the rail"
    assert all("is-active" not in r.split("|")[0] for r in rows[1:])
    assert "Desks" in rows[1], rows[1]


@needs_node
def test_nothing_is_written_for_a_conversation_that_does_not_exist():
    """The whole reason it is a placeholder. An empty Conversation on disk is one the rail
    has to list forever, and every stray click would leave another."""
    assert _run([{"newConversation": True}])[0]["writes"] == []


@needs_node
def test_the_press_lands_the_same_way_in_build():
    """Build keeps the app in the preview, so the press writes `#/build?app=`. The rail is the
    same rail (#82) and owes the same row."""
    step = _run([{"newConversation": True, "route": "#/build?app=app_a"}])[0]
    assert "New conversation" in step["rows"][0]
    assert "is-active" in step["rows"][0].split("|")[0]
    assert step["writes"] == []


@needs_node
def test_clearing_the_conversation_does_not_clear_the_row():
    """The tidy-up that would undo all of this, guarded directly.

    Build's route effect calls `clearConversation` on every arrival at a conversation-less
    `#/build` — the very navigation the button performs — so a `pendingConversation = false`
    added there wipes the row on the way in and the button looks dead for the third time.
    Asserted against `clearConversation` itself rather than through `modes/builder.js`, which
    the harness cannot run: `useEffect` is stubbed to a no-op and nothing is mounted.
    """
    step = _run([{"newConversation": True}, {"clearConversation": True}])[1]
    assert "New conversation" in step["rows"][0], step["rows"][:2]


@needs_node
def test_the_row_does_not_outlive_the_conversation_it_became():
    """The flag ends when a real Conversation opens, not when one closes.

    Left set past `newThread`, it survives the conversation it was standing in for and the next
    `clearConversation` draws it again — deleting that very conversation is enough, and the rail
    then shows a selected New conversation nobody pressed.
    """
    step = _run([{"newConversation": True},
                 {"firstMessage": True},
                 {"clearConversation": True}])[2]
    assert all("New conversation" not in r for r in step["rows"]), step["rows"]


@needs_node
def test_it_is_not_a_search_result():
    """Filtering asks about history. This row is not in it, and a conversation drawn above
    "No conversations have changed X yet" is the rail contradicting itself."""
    step = _run([{"newConversation": True},
                 {"railRows": "chat", "railAppFilter": "app_a"}])[1]
    assert all("New conversation" not in r for r in step["rows"]), step["rows"]


@needs_node
def test_the_row_does_not_offer_a_click_it_does_not_have():
    """Every other row in the rail goes somewhere. `is-pending` takes the pointer back off."""
    first = _run([{"newConversation": True}])[0]["rows"][0]
    assert "is-pending" in first.split("|")[0], first


@needs_node
def test_the_first_message_hands_the_row_over_to_the_real_conversation():
    """Replaced, not joined. The placeholder stood in for exactly this Thread."""
    step = _run([{"newConversation": True}, {"firstMessage": True}])[1]
    assert all("New conversation" not in r for r in step["rows"]), step["rows"]
    assert len(_active(step)) == 1, _active(step)
    assert step["writes"] == ["POST /threads"], step["writes"]


@needs_node
def test_clicking_another_conversation_discards_it():
    """"Discards" is the whole contract: there was never anything to keep."""
    step = _run([{"newConversation": True}, {"open": "thr_both"}, {"railRows": "chat"}])[2]
    assert all("New conversation" not in r for r in step["rows"]), step["rows"]
    assert _active(step) and "Desks" in _active(step)[0]


# The fourth time. The three above were fixed by making the press DO more or SHOW more; this one
# was already doing and already showing, into a panel that was shut. The Workbench loads with the
# Rail collapsed (#150), so the collapsed head's icon is the button most people press, and from
# there the pending row — the only thing on screen that says the press worked — is drawn behind the
# panel being clicked. The centre pane cannot stand in for it: the press lands you on an empty
# Conversation, and from an empty Conversation the pane was already the landing and stays it, so
# nothing at all moves. So the press opens the Rail, which is the expanded head's rule read from
# the other side rather than an exception to it.


@needs_node
def test_the_collapsed_head_shows_the_row_it_just_made():
    """The press has one answer on screen. It has to be on screen."""
    step = _run([{"pressCollapsedNew": "chat"}])[0]
    assert step["railHidden"] is False, "the press left the Rail shut over its own answer"
    assert step["rows"], "the Rail drew no rows"
    first = step["rows"][0]
    assert "New conversation" in first, first
    assert "is-active" in first.split("|")[0], first


@needs_node
def test_it_opens_the_rail_in_build_too():
    """One Rail in both modes (#82), and the same press behind both."""
    step = _run([{"pressCollapsedNew": "build"}])[0]
    assert step["railHidden"] is False
    assert "New conversation" in step["rows"][0], step["rows"][0]


@needs_node
def test_the_press_still_navigates():
    """Opening the Rail is the half that was missing, not a replacement for the half that was
    there: the route has to stop naming the Conversation the press just closed."""
    step = _run([{"pressCollapsedNew": "chat"}])[0]
    assert step["hash"] == "#/chat", step["hash"]


@needs_node
def test_the_press_is_not_a_choice_about_the_rail():
    """A Rail opened to show an answer is not somebody asking to keep the list open. Writing the
    preference here would leave it open on every load after one press."""
    step = _run([{"pressCollapsedNew": "chat"}])[0]
    assert step["prefRailHidden"] is not False, step["prefRailHidden"]


@needs_node
def test_nothing_is_written_for_the_collapsed_press_either():
    """Same contract as the expanded one. The row is a placeholder, and a placeholder that costs a
    Thread on disk is not one."""
    assert _run([{"pressCollapsedNew": "chat"}])[0]["writes"] == []
