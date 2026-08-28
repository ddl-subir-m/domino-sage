"""What Build draws for a Conversation, under each conversation view (#57).

The twin of `test_the_conversation_view`, which holds the same claim for Chat. Both are claims about
MESSAGE STATE, so both drive the real store through `js/conversation_view_harness.mjs` and mount
nothing — see that file's header for why.

THE ASYMMETRY IS THE POINT. Chat has no preview, so it shows every Built App the Conversation drove
(#56). Build has a preview bound to ONE app, so it shows that one. The Chat turns above the plan
card are therefore the WHOLE Conversation, and the build turns below it are the SELECTED app's.
`test_build_shows_only_the_selected_apps_turns` is what stops the merged read leaking the other
app's work into a pane that cannot preview it.

THE TRAP. An empty Build pane is not always the defect this ticket was filed about. The greeting
fires on `buildMessages.length === 0`, which is this Conversation's turns IN THE SELECTED APP, and
since #74 someone can start a brand-new Built App inside a Conversation full of talk. That app has
no build turns, so the greeting fires — and there it is RIGHT. The defect is the Conversation going
invisible, not the app having nothing in it yet. Hence two separate numbers in the harness report:
`transcript` is what Build draws, `appTurns` is what the greeting asks about.
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

pytestmark = needs_node


def _run(steps: list[dict]) -> list[dict]:
    """What the store held after each step, plus every path that step fetched."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _build(thread: str, *, pref: str = "unified", select: str = "") -> dict:
    """Arrive in Build on `thread`, with `select` as the app on screen."""
    steps: list[dict] = [{"pref": pref}]
    if select:
        steps.append({"select": select})
    steps.append({"build": thread})
    return _run(steps)[-1]


def _texts(step: dict) -> list:
    """The transcript flattened enough to read as the transcript it is."""
    return [[m["role"], m["blocks"]] for m in step["transcript"]]


def _block_types(step: dict) -> list[str]:
    out = []
    for message in step["transcript"]:
        for block in message["blocks"]:
            out.append(block if isinstance(block, str) else json.dumps(block, sort_keys=True))
    return out


# ---- the Conversation stops being invisible ---------------------------------------------------

def test_build_after_a_handoff_shows_the_chat_turns_above_the_plan_card():
    """The failure this ticket opens with. A handoff keeps the same Conversation, but Build's half
    began at the plan card, so a Conversation full of analysis opened on `Build the app from a
    plan` — a blank screen with a greeting on it."""
    step = _build("thr_handoff")
    rows = _texts(step)

    assert rows[0] == ["user", ["build me a dashboard"]]
    # The plan card the handoff wrote is still there, and still BELOW the talk that earned it —
    # which is the whole claim, so assert the position rather than the presence.
    plan_at = next(i for i, m in enumerate(step["transcript"]) if "build_plan" in m["blocks"])
    assert rows.index(["user", ["build me a dashboard"]]) < plan_at


def test_a_conversation_that_only_happened_in_build_still_reads_as_itself():
    """Typing straight into Build makes a Conversation with no Chat half at all. Build showed those
    turns before this ticket and still does — the merge must not cost that."""
    rows = _texts(_build("thr_build_only"))

    assert ["user", ["build me a dashboard"]] in rows


def test_the_chat_half_is_the_whole_conversation_not_only_what_came_before():
    """A Conversation that went back to Chat after a build reads in that order. Chat turns are not
    a prologue that stops at the first build turn."""
    rows = _texts(_build("thr_both"))

    assert rows[0] == ["user", ["which desks lost money?"]]
    assert rows[-1] == ["user", ["thanks"]]
    assert ["user", ["add a date filter"]] in rows
    assert rows.index(["user", ["add a date filter"]]) < rows.index(["user", ["thanks"]])


# ---- one app at a time ------------------------------------------------------------------------

def test_build_shows_only_the_selected_apps_turns():
    """The asymmetry with Chat. `thr_two_apps` drove two Built Apps; Chat shows both runs (#56) and
    Build shows the one it can preview."""
    rows = _texts(_build("thr_two_apps", select="app_a"))

    assert ["user", ["build the dashboard"]] in rows
    assert ["user", ["now the P&L report"]] not in rows


def test_switching_app_switches_which_half_of_the_conversation_is_below():
    """Same Conversation, other app. The Chat turns are the Conversation's either way; the build
    turns are the app's."""
    rows = _texts(_build("thr_two_apps", select="app_b"))

    assert ["user", ["now the P&L report"]] in rows
    assert ["user", ["build the dashboard"]] not in rows


# ---- the orientation, which is not the defect --------------------------------------------------

def test_a_new_app_in_a_talkative_conversation_still_gets_its_orientation():
    """Since #74 a brand-new Built App can be started inside a Conversation full of talk. It has no
    build turns, so the greeting fires — and that is correct, the person needs telling what to do in
    the app they just made."""
    step = _build("thr_both", select="app_b")

    assert step["appTurns"] == 0


def test_that_orientation_sits_under_the_conversation_never_instead_of_it():
    """The half of the same screen this ticket is about: the greeting is allowed, the blank
    Conversation behind it is not."""
    step = _build("thr_both", select="app_b")

    assert step["appTurns"] == 0
    assert _texts(step) == [["user", ["which desks lost money?"]], ["user", ["thanks"]]]


def test_a_conversation_with_no_turns_at_all_still_has_nothing_to_draw():
    """The greeting has to survive as the whole screen. A merge that invented a row would take the
    orientation away from every new Conversation in Build."""
    step = _build("thr_empty")

    assert step["transcript"] == []
    assert step["appTurns"] == 0


# ---- a write must not strand the transcript -----------------------------------------------------

def test_what_build_draws_is_never_behind_what_it_holds():
    """`buildMessages` is what the greeting asks about and `buildTranscript` is what Build draws, so
    a writer that set only the first strands the pane one row behind — the echo bubble missing until
    the turn's first event happens to recompute it. Counted per frame, because a later event hides
    it from any snapshot taken at the end."""
    step = _run([{"pref": "unified"}, {"build": "thr_both"}, {"echo": "make it bigger"}])[-1]

    assert step["behindFrames"] == 0
    assert ["user", ["make it bigger"]] in _texts(step)


def test_an_echoed_prompt_lands_below_the_chat_turns_not_above_them():
    """A row arriving now has no place in the merged order yet. Falling back to its index in this
    app's log would file it against a different scale entirely, and put a turn happening NOW above
    Chat turns from an hour ago."""
    step = _run([{"pref": "unified"}, {"build": "thr_both"}, {"echo": "make it bigger"}])[-1]
    rows = _texts(step)

    assert rows.index(["user", ["make it bigger"]]) > rows.index(["user", ["thanks"]])


def test_the_same_holds_under_split():
    """The same writers, the arm that is meant to be untouched. This is the half of the defect that
    would have stranded the pane for everyone, not only for the unified view."""
    step = _run([{"pref": "split"}, {"build": "thr_both"}, {"echo": "make it bigger"}])[-1]

    assert step["behindFrames"] == 0
    assert ["user", ["make it bigger"]] in _texts(step)


# ---- the split arm is untouched ----------------------------------------------------------------

def test_under_split_build_is_exactly_what_it_is_today():
    """Build's half alone, read the way it has always been read."""
    step = _build("thr_both", pref="split")

    rows = _texts(step)

    assert ["user", ["add a date filter"]] in rows
    # Not one Chat turn, and not the other app — which is the screen Build has always drawn.
    assert ["user", ["which desks lost money?"]] not in rows
    assert ["user", ["thanks"]] not in rows
    assert not any("/conversation" in call for call in step["calls"])


def test_under_split_a_conversation_that_only_happened_in_build_is_unchanged():
    step = _build("thr_build_only", pref="split")

    assert ["user", ["build me a dashboard"]] in _texts(step)


def test_switching_preference_changes_only_what_is_rendered():
    """Never what is stored. The same Conversation, read twice under the two views: the app's own
    turns are the same rows both times, and nothing is written to the server on the way through."""
    report = _run([{"pref": "unified"}, {"build": "thr_both"},
                   {"pref": "split"}, {"build": "thr_both"}])
    unified, split = report[1], report[3]

    assert unified["appTurns"] == split["appTurns"]
    assert unified["app"] == split["app"]
    for step in (unified, split):
        assert not any("/select" in call for call in step["calls"])


# ---- a link that names no app -------------------------------------------------------------------

def test_a_bound_handoff_names_the_app_even_when_a_later_turn_built_another():
    """ADR-0009: the link resolves from the Conversation's newest BOUND handoff entry. `thr_bound`
    handed off to app_a and later built app_b from Build, so the two rules disagree and the record
    says the handoff wins."""
    assert _run([{"pref": "unified"}, {"resolve": "thr_bound"}])[-1]["app"] == "app_a"


def test_an_app_started_in_build_is_named_by_its_turns():
    """A Built App started inside Build (#74) was never handed off, so no entry can name it and the
    ADR's rule has nothing to answer with. Its turns are the only record that it was driven."""
    assert _run([{"pref": "unified"}, {"resolve": "thr_two_apps"}])[-1]["app"] == "app_b"


def test_a_link_with_no_app_resolves_the_app_the_conversation_last_bound():
    """`#/build/<id>` with no `?app=` is not a request for whichever app happens to be selected —
    that is a different app for every viewer and every visit, so the same link would show two people
    two different transcripts."""
    step = _run([{"pref": "unified"}, {"resolve": "thr_two_apps"}])[-1]

    # app_a is what the server has selected; app_b is what this Conversation bound last.
    assert step["app"] == "app_b"


def test_a_link_with_no_app_resolves_the_same_way_under_either_view():
    """Which app is selected is STORED, and the preference decides what is RENDERED, so the app a
    link lands on cannot depend on it."""
    unified = _run([{"pref": "unified"}, {"resolve": "thr_two_apps"}])[-1]
    split = _run([{"pref": "split"}, {"resolve": "thr_two_apps"}])[-1]

    assert unified["app"] == split["app"] == "app_b"


def test_a_conversation_that_never_bound_an_app_leaves_the_selection_alone():
    """A Conversation with no build turns names no app, so there is nothing to resolve and the
    selected app stays — which is what Build has always done."""
    assert _run([{"pref": "unified"}, {"resolve": "thr_empty"}])[-1]["app"] is None


def test_a_merged_read_that_fell_over_leaves_the_selection_alone():
    """It cannot say which app the Conversation bound, so it does not guess. Landing on the selected
    app is what the link did before this ticket."""
    assert _run([{"pref": "unified"}, {"resolve": "thr_broken"}])[-1]["app"] is None


# ---- what the merged view must never lose --------------------------------------------------------

def test_the_handoff_offer_does_not_follow_the_conversation_into_build():
    """It offers a way over to Build, and this is Build. Both shapes go: the live callout the thread
    carries, and the suggestion persisted in the Chat log."""
    step = _build("thr_offer")

    assert ["user", ["chart this"]] in _texts(step)
    assert "plan_suggestion" not in _block_types(step)


def test_an_adopted_row_with_no_app_is_not_dropped():
    """Build history predates per-app logs, so an upgraded Project has rows tagged with the
    Conversation and no app. Filtering them out would make the merged view strictly emptier than the
    split view it replaces — the failure the server adopts legacy history to avoid."""
    unified = _build("thr_legacy")
    split = _build("thr_legacy", pref="split")

    assert ["user", ["the turn nobody stamped"]] in _texts(unified)
    assert _texts(unified) == _texts(split)


# ---- the merged read falling over ---------------------------------------------------------------

def test_a_broken_merged_read_still_shows_this_apps_turns():
    """Build short of its Chat turns is half the story; Build short of its own turns is a blank
    screen. The fallback is the split read, the same one the split view makes."""
    step = _build("thr_broken")

    assert ["user", ["add a date filter"]] in _texts(step)
    assert step["appTurns"] > 0
