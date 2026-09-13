"""A Chat model save the server refuses must leave the control on the pair it already had (#306).

`setChatModel` writes the pick into the store and notifies before the POST goes out, which is what
makes the menu feel immediate. The argument for putting the pick back when that POST fails is
already written on `setBuildModel`, one screen up in the same file, and it applies here word for
word: a refused MODE change shows up in the next turn's behaviour, so a control left showing it
corrects itself, while a refused MODEL never does — the control goes on naming a model no turn will
use, for every turn after it.

The half that is not the same as `setBuildModel`'s is why this needed its own ticket. That one puts
one field back. This one writes two, and they are not independent: a reasoning effort is only
meaningful against a model that accepts it (ADR-0049). Restoring the model and leaving the level, or
reading the two at different moments, puts a pair on screen that was never on screen — and can put
up one no model would accept. So both tests below assert the PAIR. Asserting the model alone passes
on a refusal that only moved the level, which is `test_a_refused_level_is_not_left_standing_alone`.

Since #286 the stale field can be more than a stale control. The drawer's "Ask and Chat" row falls
back to `state.model` as the browser's mirror of the Chat pick, so a refused save also leaves a row
predicting what that mode will run from a pick that never happened. A fallback only: the row asks
`sensitivity.chat_picked` first and reads the mirror just where that field is absent, so the drawn
half of this retires with the last deployment whose payload predates it. The stale control does not.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_model_refusal_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_refused_save_leaves_the_pair_the_control_already_had():
    """Both halves, together. The pick moved the model AND cleared the level, so a revert that
    restored either one on its own would leave a pair the person never chose."""
    (refused,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                        "pick": {"model": "coder"}, "refuse": True}])

    assert refused["model"] == "gpt-5.4"
    assert refused["reasoningEffort"] == "high"
    assert refused["said"] == ["Domino refused that model"], "and the person is told it failed"


def test_a_refused_level_is_not_left_standing_alone():
    """The case that tells a fix from a half of one. The pick keeps the model and changes only the
    level, so `model` is right whether or not anything was put back — and the level is the whole
    assertion."""
    (refused,) = _run([{"start": {"model": "gpt-5.4"},
                        "pick": {"model": "gpt-5.4", "effort": "low"}, "refuse": True}])

    assert refused["reasoningEffort"] is None
    assert refused["model"] == "gpt-5.4"


def test_a_save_that_lands_keeps_the_pick():
    """The other direction, and the reason it is here: a revert that fired on every save would pass
    both tests above while taking the picker away from everybody."""
    (landed,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                       "pick": {"model": "coder"}, "refuse": False}])

    assert landed["posted"] == {"chat_model": "coder", "reasoning_effort": None}
    assert landed["model"] == "coder"
    assert landed["reasoningEffort"] is None
    assert landed["said"] == []


def test_a_refusal_does_not_put_its_pair_back_over_a_later_save_that_landed():
    """The window the revert itself opens. Nothing disables the picker while a save is in flight, so
    a second pick can land before the first answers — and `applyModelStatus` has a dozen other
    callers besides, every `loadBuild` among them. A revert with no guard would then put the
    pre-FIRST pair back over a pair the server has already taken, under a toast naming the save that
    failed: the control would name a model no read ever reported, and nothing on screen would say so.

    Before this ticket a refusal wrote nothing back, so the later save stood. That is what makes this
    the revert's own case rather than a pre-existing one.
    """
    (overlapped,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                           "pick": {"model": "coder"},
                           "during": {"model": "gpt-5.4", "effort": "low"}}])

    assert overlapped["posted"] == {"chat_model": "gpt-5.4", "reasoning_effort": "low"}, \
        "the second save is the one the server took"
    assert overlapped["model"] == "gpt-5.4"
    assert overlapped["reasoningEffort"] == "low"
    assert overlapped["said"] == ["Domino refused that model"], "and the refusal is still reported"


def test_a_read_that_carries_no_chat_pair_confirms_nothing():
    """What the pair to put back is recorded FROM. It has to be the answer that carried it, not the
    store as it stands when an answer arrives: a payload with no Chat pair in it writes nothing, and
    the store at that moment holds the optimistic pick. Recorded from the store, such a read makes
    the refused pick its own "confirmed" value, the restore writes it back over itself, and the
    control is left exactly where #306 says it must never be — under a toast saying the save failed.

    Reachable without anything going unusually wrong: `loadBuild` catches a failed `/project` read
    into `{}` and hands that straight to `applyModelStatus`, and `{}` is truthy, so the early exit
    does not fire.
    """
    (stale,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                      "pick": {"model": "coder"}, "readDuring": True}])

    assert stale["model"] == "gpt-5.4"
    assert stale["reasoningEffort"] == "high"


def test_a_refusal_does_not_yank_back_a_pick_whose_own_save_is_still_out():
    """The staleness guard, on the only case that can tell it is there. A refusal puts the confirmed
    pair back, and putting it back over a LATER pick that is still being saved would take that pick
    off the control while its own POST is still in flight — the person's second choice vanishing
    under a toast about their first.

    Read in that window deliberately. Once the second save answers it settles the control either
    way, so a test that looked afterwards would pass with the guard gone.
    """
    (pending,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                        "pick": {"model": "coder"},
                        "pendingSecond": {"model": "gpt-5.4", "effort": "low"}}])

    assert pending["midFlight"] == {"model": "gpt-5.4", "effort": "low"}, \
        "the pick still being saved is left alone"
    assert pending["model"] == "gpt-5.4"
    assert pending["reasoningEffort"] == "low", "and the save that landed is what stands"


def test_two_refusals_in_one_window_still_put_back_a_pair_the_server_held():
    """The pair that goes back cannot be captured per call. Picks are optimistic and nothing blocks
    a second one, so a call made while the first POST is out reads the FIRST pick's optimistic value
    as the pair it will restore — a pair the server never took. Both refusals then land, the second
    one restores it, and the control is left naming a model no turn will use, which is the exact
    thing this ticket exists to stop.

    The other order self-heals, so `test_a_refusal_does_not_put_its_pair_back_over_a_later_save_that_landed`
    does not reach this: the defect is in what each call captures, not in which answer arrives first.
    """
    (both,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                     "pick": {"model": "coder"},
                     "thenAlsoRefused": {"model": "gpt-5.4", "effort": "low"}}])

    assert both["posted"] is None, "neither save reached the server"
    assert both["model"] == "gpt-5.4"
    assert both["reasoningEffort"] == "high"
    assert both["said"] == ["Domino refused that model"] * 2, "and both refusals are reported"


def test_the_ask_and_chat_row_does_not_predict_from_a_refused_pick():
    """#286's blast radius, drawn. The `ask` row holds an approved model and the lock answers that
    its turn runs a different one; the only rule on this row that can explain the difference is a
    live Chat pick, so the row draws the substitute exactly when it believes there is one.

    Read through the payload that does NOT carry `chat_picked`, because that is the only state where
    the row consults the browser's mirror rather than the server's own flag (#294). On a current
    deployment the flag answers first and the row is right either way — the mirror is the fallback,
    and this is the fallback being asked.
    """
    refused, landed = _run([
        {"start": {"model": ""}, "pick": {"model": "coder"}, "refuse": True, "servesPick": False},
        {"start": {"model": ""}, "pick": {"model": "coder"}, "refuse": False, "servesPick": False},
    ])

    # Nobody has picked, so nothing moved this row: it shows its own assignment and says nothing.
    assert refused["askRow"] == {"value": "coder", "note": ""}
    # And the same row after a save that landed, which is what the sentence is FOR.
    assert landed["askRow"]["value"] == "gpt-5.4"
    assert landed["askRow"]["note"] == "This runs gpt-5.4, not coder."
