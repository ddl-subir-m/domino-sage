"""A refused Build model save must put back the pair the SERVER last confirmed (#323).

`setBuildModel` already reverts, and #306 scoped it out on exactly that — "`setBuildModel` is
correct" was true of the one thing it was compared on. It is not true of the pair it puts back. The
revert captures `state.buildModel`/`state.buildEffort` at entry and restores them unconditionally,
and both halves of that are wrong in a window the product leaves open: nothing disables the picker
while the POST is out, and `applyModelStatus` has a dozen other writers.

Three failure modes, the same three #306 fixed a screen up, one of them measured live before it was
fixed rather than reasoned about: two refusals in one window left the control on `coder`, the FIRST
pick's optimistic value, which no answer ever carried, while the server still held `gpt-5.4`/`high`.
A refused model never self-corrects — unlike a refused MODE, which shows up in the next turn's
behaviour — so the picker names a model no build will run, permanently.

The pair asserted, never the model alone: a level is only meaningful against a model that accepts it
(ADR-0049), so a restore that moved one half leaves a pairing that was never on screen.
`test_a_refused_level_is_not_left_standing_alone` is the one that fails if only the model is checked.

Named rather than left as absence, because the drawer mirrors THIS pair too. `model-assignments.js`
reads `buildModel` as the browser's mirror for the `plan` and `implement` rows exactly as it reads
the Chat pick for `ask`, falling back to it wherever `sensitivity.picked` is absent — so a stale
Build pick draws two rows predicting a model the server never took, which is the Build instance of
the #286 consequence the Chat file pins. The fix under test makes it right today; nothing below
pins it. The fixture cannot express it as it stands: the harness's `PANEL` gives the plan and
implement rows the same model the lock's `slot_models` gives them, so those rows have no substitute
to draw, and giving them one moves the note the Chat file asserts on — `plan` renders before `ask`,
and the harness takes the first match. That is a ticket of its own, not a line here.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "model_refusal_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list[dict]:
    steps = [{**s, "control": "build"} for s in steps]
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_refused_save_leaves_the_pair_the_control_already_had():
    """The behaviour that was already right, pinned before the rest moves it. The pick moved the
    model AND cleared the level, so a restore of either half alone leaves a pair nobody chose."""
    (refused,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                        "pick": {"model": "coder"}, "refuse": True}])
    assert refused["model"] == "gpt-5.4"
    assert refused["reasoningEffort"] == "high"
    assert refused["said"] == ["Domino refused that model"], "and the person is told it failed"


def test_a_refused_level_is_not_left_standing_alone():
    """What tells a fix of both halves from a fix of one. The pick keeps the model and changes only
    the level, so `model` reads right whether or not anything was put back — the level is the whole
    assertion."""
    (refused,) = _run([{"start": {"model": "gpt-5.4"},
                        "pick": {"model": "gpt-5.4", "effort": "low"}, "refuse": True}])
    assert refused["reasoningEffort"] is None
    assert refused["model"] == "gpt-5.4"


def test_a_save_that_lands_keeps_the_pick():
    """The other direction, and the reason it is here: a revert that fired on every save would pass
    every refusal test above while taking the picker away from everybody."""
    (landed,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                       "pick": {"model": "coder"}, "refuse": False}])
    assert landed["posted"] == {"pick": "coder", "pick_effort": None}
    assert landed["model"] == "coder"
    assert landed["reasoningEffort"] is None
    assert landed["said"] == []


def test_a_refusal_does_not_put_its_pair_back_over_a_later_save_that_landed():
    """A second pick made in the window LANDS, and only then does the first refusal answer. The
    per-call capture holds the pre-first pair, so the catch writes it over the pair the server has
    just taken — and the toast names the first model, so nothing on screen says the second was
    undone. The record this restores from moved when that save landed, which is why the staleness
    guard is not what covers this case."""
    (overlapped,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                           "pick": {"model": "coder"},
                           "during": {"model": "gpt-5.4", "effort": "low"}}])
    assert overlapped["posted"] == {"pick": "gpt-5.4", "pick_effort": "low"}, \
        "the second save is the one the server took"
    assert overlapped["model"] == "gpt-5.4"
    assert overlapped["reasoningEffort"] == "low", "and its level stands, not the pre-first one"
    assert overlapped["said"] == ["Domino refused that model"], "and the refusal is still reported"


def test_a_read_that_carries_no_build_pair_confirms_nothing():
    """What the pair put back is recorded FROM. It has to be the answer that carried it, not what
    the store stands at when an answer arrives: a payload with no Build pair in it writes neither
    field, so the store at that moment still holds the optimistic pick. Recorded off `state`, such a
    read makes the refused pick its own "confirmed" value and the restore writes it back over
    itself, leaving the control exactly where this ticket says it must never be — under a toast
    saying the save failed. Reachable without anything going unusually wrong: `loadBuild` catches a
    failed `/project` read into `{}` and hands it straight to `applyModelStatus`, and `{}` is truthy,
    so the early exit does not fire."""
    (stale,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                      "pick": {"model": "coder"}, "readDuring": True}])
    assert stale["model"] == "gpt-5.4"
    assert stale["reasoningEffort"] == "high"


def test_a_refusal_does_not_yank_back_a_pick_whose_own_save_is_still_out():
    """The staleness guard, on the only case that tells it is there. A second pick is made in the
    window and is STILL UNANSWERED when the first is refused, so the restore would take somebody's
    second choice off the control while its own POST is out, under a toast about their first. The
    assertion is on the mid-flight read, because afterwards the second answer settles the control
    either way and the difference is gone."""
    (pending,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                        "pick": {"model": "coder"},
                        "pendingSecond": {"model": "gpt-5.4", "effort": "low"}}])
    assert pending["midFlight"] == {"model": "gpt-5.4", "effort": "low"}, \
        "the pick whose save is still out stays on the control"


def test_two_refusals_in_one_window_still_put_back_a_pair_the_server_held():
    """The one measured live. Both saves are refused and the FIRST answers first, so the second
    call's captured "before" is the first call's OPTIMISTIC value: the control ends on `coder`, a
    pair no answer ever carried, while the server still holds `gpt-5.4`/`high`. The other order
    self-heals, which is why the `during` test above does not reach this — the defect is the pair
    each call captures, not the order the answers arrive in."""
    (both,) = _run([{"start": {"model": "gpt-5.4", "effort": "high"},
                     "pick": {"model": "coder"},
                     "thenAlsoRefused": {"model": "gpt-5.4", "effort": "low"}}])
    assert both["posted"] is None, "neither save reached the server"
    assert both["model"] == "gpt-5.4"
    assert both["reasoningEffort"] == "high"
    assert both["said"] == ["Domino refused that model"] * 2, "and both refusals are reported"
