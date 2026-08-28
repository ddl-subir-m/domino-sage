"""What Chat draws for a Conversation, under each conversation view (#56).

The server half of this ticket is held in `test_chat_shows_the_whole_conversation`. This is the
client half, and it is a claim about MESSAGE STATE: whether the two halves merged, in what order, a
build run folding into one row, and what that row's face is built from. All of it is settled before
React is asked to draw anything, so the harness drives the real store against a controllable server
and mounts nothing — see `js/conversation_view_harness.mjs`. The card's own three questions are
asked by calling `AppChange` as the function it is, against a stubbed `createElement` that returns
plain objects; that is still not a render, and they have nowhere else to be asked.

The line this ticket is easiest to get wrong runs through here. The `app_change` BLOCK is emitted by
the build turn, server-side and blind to the preference, and it renders under both views. The
FOLDING — the collapsed run row, the merged read behind it — is the unified arm's alone, and #61
deletes it if split wins. So the row is a VIEW over `app_change` blocks and never a second kind of
block beside them: `test_the_row_is_built_from_the_cards_not_beside_them` is what stops the card
being rebuilt inside the branch that may go away.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "conversation_view_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list[dict]:
    """What the store held after each step, plus every path that step fetched."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _unified(thread: str) -> dict:
    return _run([{"pref": "unified"}, {"open": thread}])[1]


def _split(thread: str) -> dict:
    return _run([{"pref": "split"}, {"open": thread}])[1]


def _shape(step: dict) -> list:
    """Roles and blocks, flattened enough to read as the transcript it is."""
    return [[m["role"], m["blocks"]] for m in step["messages"]]


# ---- the merged transcript --------------------------------------------------------------------

def test_the_merged_view_shows_both_halves_in_the_order_they_happened():
    """Not Chat then Build. A Conversation that went back to Chat after a build reads in that
    order, or the transcript tells a story that never happened."""
    rows = _shape(_unified("thr_both"))

    assert rows[0] == ["user", ["which desks lost money?"]]
    assert rows[1][1][0]["run"] == "add a date filter"
    assert rows[2] == ["user", ["thanks"]]


def test_a_conversation_that_only_happened_in_build_is_not_an_empty_chat():
    """The failure this ticket opens with: typing straight into Build made a Conversation whose
    Chat half was empty forever, so opening its row showed the landing screen."""
    assert _shape(_unified("thr_build_only")) != []
    assert _split("thr_build_only")["messages"] == []  # which is what it does today


def test_a_conversation_with_no_turns_at_all_still_has_nothing_to_draw():
    """The landing screen has to survive: an empty Conversation is empty under both views, and a
    merge that invented a row would take the starters away from every new chat."""
    assert _unified("thr_empty")["messages"] == []
    assert _split("thr_empty")["messages"] == []


# ---- the collapsed run ------------------------------------------------------------------------

def test_a_build_run_folds_into_one_row_that_opens_on_its_turns():
    """Chat has no preview pane, so twenty raw implementation turns would bury the questions around
    them. One row per run, and the turns are still there behind it."""
    rows = _shape(_unified("thr_both"))
    run = rows[1][1][0]

    assert run["run"] == "add a date filter"
    assert run["folded"] == ["user:text", "assistant:sandbox_run+status"]


def test_the_row_names_the_built_app_the_run_built():
    run = _shape(_unified("thr_both"))[1][1][0]

    assert run["apps"] == ["app_a:Desk dashboard"]


def test_the_row_is_built_from_the_cards_not_beside_them():
    """#83's rule, and the one this ticket is easiest to get wrong: the row is a VIEW over
    `app_change` blocks. Built on a second source of app facts, the card would be stranded inside
    the branch #61 may delete — so the run's face is the blocks, and the fold does not repeat
    them."""
    run = _shape(_unified("thr_build_only"))[0][1][0]

    assert run["apps"] == ["app_a:Desk dashboard as it was then"]
    assert not any("app_change" in turn for turn in run["folded"])


def test_the_card_names_the_app_the_way_the_run_named_it():
    """A then-fact. The rail calls this app something else today; a run from six weeks ago names it
    as it was called then, because a rename since is not something that run did."""
    run = _shape(_unified("thr_build_only"))[0][1][0]

    assert run["apps"] == ["app_a:Desk dashboard as it was then"]
    assert "app_a" in _unified("thr_build_only")["apps"]  # and the rail's own name is loaded too


def test_a_conversation_that_drove_two_apps_shows_both_runs_in_order():
    """One Thread hands off more than once (#72) and a Project holds many apps (ADR-0008), so a
    merged read that showed only the selected app would hide half of what this Conversation did."""
    rows = _shape(_unified("thr_two_apps"))

    assert [row[1][0]["apps"] for row in rows] == [["app_a:Desk dashboard"], ["app_b:P&L report"]]
    assert [row[1][0]["run"] for row in rows] == ["build the dashboard", "now the P&L report"]


def test_the_plan_that_crossed_from_chat_is_not_folded_away():
    """A confirmed handoff writes the plan card and its `done` into the Build log with no user row
    in front of them, so a fold that started on any row would swallow the handoff's own card (#60)
    into a collapsed run with no prompt on it — on the ordinary path into Build, for every
    Conversation. Only a build turn folds, because only a build turn has a prompt to summarise."""
    rows = _shape(_unified("thr_handoff"))

    assert rows[0] == ["user", ["build me a dashboard"]]
    assert rows[1] == ["assistant", ["build_plan"]]
    assert rows[2][1][0]["run"] == "add a date filter"


def test_an_app_reset_is_not_reported_as_something_a_build_prompt_did():
    """`app-reset` and `attachments-restored` are appended outside any turn. Folded into the run
    above them they read as one of that prompt's turns, which is a claim about who did what."""
    rows = _shape(_unified("thr_handoff"))

    assert len(rows) == 4
    assert rows[2][1][0]["folded"] == ["user:text", "assistant:status"]  # its prompt, its own done
    assert rows[3] == ["assistant", ["status"]]  # the reset, in place and after the run


def test_a_merged_read_that_fails_falls_back_to_the_chat_half():
    """Not to nothing. The Chat half arrived with the thread and is still good, so a merge that
    fell over costs the Build half — it does not turn a Conversation into one that never
    happened, which is the failure this whole ticket is about."""
    step = _unified("thr_broken")

    assert _shape(step) == [["user", ["which desks lost money?"]]]


# ---- the card itself ---------------------------------------------------------------------------
#
# The three questions only the card can answer. `createElement` is stubbed to a plain object in the
# harness, so this walks a tree of data — still no React, and still no antd.

def _card(app_id: str, name: str = "As it was called then", **where) -> list[str]:
    steps = [{"pref": "unified"}, {"open": "thr_both"},
             {"card": {"appId": app_id, "name": name}, **where}]
    return _run(steps)[2]["words"]


def test_a_published_app_says_when_and_an_unpublished_one_says_it_is_not():
    assert _card("app_a")[-1] == "Published · January 2, 2026"
    assert _card("app_b")[-1] == "Not published yet"


def test_an_app_published_before_the_stamp_existed_says_published_and_stops_there():
    """`published_at()` is "" for an app published before the stamp, which is every published app in
    every Project on the release that adds one. `Published · ` with nothing after the separator
    reads as a date that failed to load."""
    assert _card("app_old")[-1] == "Published"


def test_an_app_that_was_never_published_still_offers_the_way_in():
    """It has nowhere else to be looked at, so the preview is not a fallback for it — it is the only
    door (ADR-0008). Which is why the control is a preview link and not a URL."""
    assert "Open in preview" in _card("app_b")


def test_the_card_says_nothing_about_an_app_it_cannot_find_in_the_rail():
    """Silent rather than wrong. "Not published yet" is a claim, and an app that has left the
    Project — or a rail list that has not arrived — is not evidence for it."""
    words = _card("app_gone", name="Deleted app")

    assert words == ["Deleted app", "Open in preview"]


def test_the_control_goes_away_where_it_would_navigate_to_where_you_already_are():
    """A button that takes you where you are is the dead end this card replaces. It goes only when
    Build is showing THIS app — showing another one is exactly when the way through is worth most."""
    here = {"route": "#/build/thr_both?app=app_a", "activeApp": "app_a"}

    assert "in the preview" in _card("app_a", **here)
    assert "Open in preview" not in _card("app_a", **here)
    assert "Open in preview" in _card("app_b", **here)
    # And Chat is never "in the preview": it has no preview pane to be in.
    assert "in the preview" not in _card("app_a")


# ---- what it costs ----------------------------------------------------------------------------

def test_publish_state_costs_one_read_of_the_rail_not_one_per_card():
    """Publish state is read live, because whether an app is published is a now-question. Read per
    card it would make a long merged transcript one fetch per row — which is why `SW.api.app` is
    not what answers it."""
    calls = _unified("thr_two_apps")["calls"]

    assert calls.count("/apps") == 1
    assert not [c for c in calls if c.startswith("/apps/")]


# ---- the other arm ----------------------------------------------------------------------------

def test_the_way_through_to_build_is_the_rails_own_route_and_not_a_copy_of_it():
    """No new navigation (#83). `?app=` is the single lever that moves preview, code and composer
    target together, and the card, the rail row and the plan back-link are three call sites of one
    grammar. A second place that builds the string is a second place for it to drift — which a
    source read is the only way to catch, because a copy that agrees today passes every test."""
    blocks = (_JS / "components" / "message-blocks.js").read_text()

    assert "SW.appRoute({ id: block.appId })" in blocks
    assert not re.search(r"#/build", blocks)


def test_split_leaves_chat_exactly_as_it_is_today():
    """Half of this ticket is a promise that nothing changed. Under split, Chat reads the Chat half
    and nothing else — no merged read, and no rail list it has never needed."""
    step = _split("thr_both")

    assert _shape(step) == [["user", ["which desks lost money?"]], ["user", ["thanks"]]]
    assert step["calls"] == ["/threads/thr_both", "/threads/thr_both/context"]
