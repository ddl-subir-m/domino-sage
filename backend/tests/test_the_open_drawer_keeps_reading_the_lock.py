"""The model drawer's rows stay true while it sits open under a running build (#294).

Its rows name what each mode RUNS, and since #286 that answer reads the in-session pick
(ADR-0043). The pick can move with no human act: a stalled build turn is escalated to the strong
plan-tier model by the orchestrator, and nothing in the browser re-read while the drawer stayed
open. So a row could name `coder` for the whole length of an escalated step.

Driven through the real store with the intervals captured rather than run, because the fix is a
cadence and the panel harness stubs `setInterval` to a no-op.

Half of this file is about a CALL GRAPH rather than a behaviour. #294 and ADR-0043 both decided the
re-read should hang off the build watch's existing 2s tick. It cannot: that tick is started in one
place, the last line of `loadBuild`, so it exists only in a tab that loaded into a build already
going. The tab that streams the turn never has one — and that is the tab where somebody presses
Build and then opens the drawer. Those tests are here so the decision is not re-derived from the
call site, where the absence is invisible.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent / "js" / "assignments_tick_harness.mjs"


def _ticked(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps), check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---- the window ------------------------------------------------------------------------------


def test_a_server_answer_that_moves_under_the_open_drawer_reaches_the_rows():
    """The turn stalls, the orchestrator picks the strong model, and the row follows it.

    Named for the SERVER'S ANSWER moving rather than for the escalation, because that is the half
    the browser owns and the only half this file can speak for. Whether a given escalation moves the
    answer is `_locked_slot_models`'s gate — it drops a pick the standing mode will not honour, so a
    session standing in Auto never had a stale row here — and that rule is tested where it lives, in
    `test_a_pick_the_standing_mode_will_not_honour_is_not_reported_on_any_row`. Teaching this
    fixture to re-decide it would let the panel agree with the fixture rather than with the product,
    which is the trap the other panel harness names in its own header.
    """
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": True}])
    assert run["runsOnOpen"] == "coder"
    assert run["runsAfterTicks"] == "gpt-5.4"


def test_the_same_holds_in_a_tab_that_loaded_into_the_build():
    """A reload mid-build, or a second Workbench on the same Project — the other window
    `openAssignments` names, closed by the same mechanism rather than by accident."""
    (run,) = _ticked([{"watch": "reload", "locked": True, "open": True}])
    assert run["runsAfterTicks"] == "gpt-5.4"


def test_the_lock_is_re_read_once_per_tick_while_the_drawer_is_open():
    """One read per 2s round, on top of the one `openAssignments` already made on opening."""
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": True, "ticks": 4}])
    assert run["readsOnOpen"] == 1
    assert run["readsWhileOpen"] == 4


# ---- and the three places it must not fire -----------------------------------------------------


def test_a_shut_drawer_costs_nothing():
    """The cadence is the drawer's, so a build running behind a closed one reads nothing. The
    half that protects every other deployment, and the more important of the two directions."""
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": False}])
    assert run["drawerWatch"] == 0
    assert run["readsWhileOpen"] == 0


def test_an_unlocked_deployment_pays_no_request_per_tick():
    """Not an optimisation. Unlocked is the common case and draws no per-slot row at all, so
    without this gate every deployment that never opted in would pay a read every 2s to be told
    again that nothing narrows. Mirrors the gate `setBuildMode` already gets this from."""
    (run,) = _ticked([{"watch": "stream", "locked": False, "open": True, "ticks": 4}])
    assert run["readsWhileOpen"] == 0


def test_closing_the_drawer_ends_the_cadence():
    """An interval a surface opens has to die with it. A leaked 2s read of the lock, forever, is a
    worse defect than the stale row it was opened to fix."""
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": True, "closeAfter": True}])
    assert run["drawerWatchAfterClose"] == 0
    assert run["readsAfterClose"] == 0


# ---- why it is not on the build watch's tick ---------------------------------------------------


def test_the_tab_that_streams_the_build_has_no_build_watch_to_gate():
    """`_watchBuild` has exactly one caller — the last line of `loadBuild` — and the stream paths
    set `buildRunning` themselves without reaching it.

    This is the whole reason the cadence hangs off the drawer. Gating a re-read on the build
    watch's tick, which is what #294 and ADR-0043 decided, would have shipped a green test and
    fixed nothing in the case the ticket describes.
    """
    stream, reload_ = _ticked([
        {"watch": "stream", "locked": True, "open": True},
        {"watch": "reload", "locked": True, "open": True},
    ])
    assert stream["buildWatch"] == 0
    assert reload_["buildWatch"] == 1


def test_the_build_watch_reads_no_lock_of_its_own_mid_turn():
    """And where the tick does exist it re-reads sensitivity only once the turn has ENDED, through
    `refreshBindings`. So the decided fix needed a new call in it either way; the tick's existence
    was the whole of its argument."""
    (run,) = _ticked([{"watch": "reload", "locked": True, "open": False, "ticks": 4}])
    assert run["buildWatch"] == 1
    assert run["readsWhileOpen"] == 0


# ---- the two ways the gate could be cheap and wrong ---------------------------------------------


def test_a_lock_read_that_never_landed_is_retried_rather_than_read_as_unlocked():
    """FOUND IN REVIEW. `isLocked` answers "no lock" for a landed `enabled: false` AND for a `null`
    that means the read has not landed, and only the first is a reason to stop asking.

    Gated on the helper, a locked deployment whose reads were failing when the drawer opened would
    sit behind it with no lock drawn, every model selectable, and nothing ever retrying — the
    stale-"unlocked" direction `store.js` calls the dangerous one, reached by the guard that exists
    to be cheap. Measured with the gate back on the helper: zero reads for the life of the drawer.
    """
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": True,
                       "ticks": 4, "failFromBoot": 3}])
    assert run["lockedOnOpen"] is False, "the premise: nothing had landed when the drawer opened"
    assert run["runsOnOpen"] is None
    assert run["readsWhileOpen"] == 4
    assert run["lockedAfterTicks"] is True
    assert run["runsAfterTicks"] == "gpt-5.4"


def test_a_tick_costs_one_request_and_not_two():
    """FOUND IN REVIEW. `refreshSensitivity` repairs the app records when a lock is holding and
    nothing has loaded them, and `!state.activeApp` is how it knows.

    A Project with no app SELECTED leaves that null for good, so under a caller that repeats the
    repair stops being one and becomes a second request every 2s forever. Measured with the repair
    left on: 4 ticks, 4 extra reads of `/apps`.
    """
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": True, "ticks": 4}])
    assert run["activeApp"] is False, "the premise: nothing is selected, so the repair never settles"
    assert run["appReads"] == 0


def test_the_repair_still_runs_when_the_open_read_never_landed():
    """FOUND IN REVIEW, TWICE — the rule, and then the fact that nothing held it.

    The tick asks for the repair off because an answer has LANDED, not because the tick repeats. The
    first version of this argued "`openAssignments` already took this read with the repair on", which
    assumes a function that swallows its own rejection succeeded. When it did not, the recovering
    tick would install a locked answer with no app records behind it — the #264 state the repair
    exists for, with the lock notice naming a Dataset and pointing nowhere.

    Then the reason went unpinned: flipping `repairAppScope: !seen` to a bare `false` left every
    other test in this file green. This is the step that reds it.
    """
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": True,
                       "ticks": 4, "failFromBoot": 3}])
    assert run["appReads"] == 1, "once, on the read that finally landed — and not once per tick"


# ---- and the row has to DRAW it, which is a second question -------------------------------------


def test_a_pick_the_person_did_not_make_reaches_the_drawn_row():
    """FOUND IN REVIEW, and the whole of #294 rather than half of it.

    The first version of this file asserted `state.sensitivity`, and a fresh answer in the store is
    not a row on screen: the Select shows the server's per-slot model only where its own `moved`
    gate opens, and that gate read `buildModel` — the browser's MIRROR of the pick, written by
    `applyModelStatus` on user acts and loads and by nothing else. #294's pick is made by the
    orchestrator with no human act, so the mirror stayed empty, `moved` stayed false, and the row
    went on drawing the pre-escalation model however fresh the store was.

    The two rows below differ in nothing but who made the pick. That is the measurement: same lock,
    same `slot_models`, same cadence, same ticks.
    """
    orchestrator, person = _ticked([
        {"watch": "stream", "locked": True, "open": True, "ticks": 3},
        {"watch": "stream", "locked": True, "open": True, "ticks": 3, "pick": "gpt-5.4"},
    ])
    # The control: a pick the person made themselves has always reached the row, and this is the
    # cadence carrying it — `__default__` when the drawer opened, the escalated model three ticks on.
    assert person["drawnOnOpen"] == "__default__"
    assert person["drawnAfterTicks"] == "gpt-5.4"
    # And the case the ticket is about, which the store alone cannot tell apart from it.
    assert orchestrator["runsAfterTicks"] == "gpt-5.4", "the premise: the store did learn"
    assert orchestrator["drawnAfterTicks"] == "gpt-5.4"


def test_the_ask_row_reads_the_chat_pick_and_the_build_rows_read_the_build_one():
    """FOUND IN REVIEW. The fork was unpinned: reading `picked` for every row left 73 tests green,
    because the fixture only ever served `chat_picked: false`.

    The `ask` row is the one with two turns behind it, and the pin reaches only the Build one — so
    `_locked_slot_models` answers it as Chat and it reads the Chat pick. Ungated the other way, a
    Build pick would open the Ask row's gate and draw "This runs X" over a pick that drives no Chat
    turn: #285's defect with the pick in the pin's place, which is the shape this surface keeps
    having. Both directions, because one of them alone is satisfied by reading either field.
    """
    build, chat = _ticked([
        {"watch": "stream", "locked": True, "open": True, "ticks": 3},
        {"watch": "stream", "locked": True, "open": True, "ticks": 3, "chatPicked": True},
    ])
    assert (build["drawnAfterTicks"], build["drawnAskAfterTicks"]) == ("gpt-5.4", "__default__")
    assert (chat["drawnAfterTicks"], chat["drawnAskAfterTicks"]) == ("__default__", "coder")


def test_the_served_pick_is_asked_first_and_the_mirror_only_answers_for_an_old_payload():
    """FOUND IN REVIEW. The comment said "asked first" and the code was an OR, which cannot be
    closed by a fresher answer.

    Three states, and an OR collapses two of them. `set_catalog` clears the Build pick server-side
    on save, so a payload saying `picked: false` beside a mirror still holding the old pick is the
    server being RIGHT and the browser being behind — the gate must shut. A payload with no such
    key is a deployment that predates the field, and only there is the mirror the better answer.
    """
    cleared, old_payload = _ticked([
        {"watch": "stream", "locked": True, "open": True, "ticks": 4,
         "pick": "gpt-5.4", "servesPick": False},
        {"watch": "stream", "locked": True, "open": True, "ticks": 4,
         "pick": "gpt-5.4", "servesPick": "absent"},
    ])
    assert cleared["drawnAfterTicks"] == "__default__", "the server said no pick, and it is fresher"
    assert old_payload["drawnAfterTicks"] == "gpt-5.4", "no such key, so the mirror is all there is"


def test_an_unchanged_answer_does_not_redraw_the_shell():
    """FOUND IN REVIEW. Every other caller of `refreshSensitivity` fires once per event, so
    replacing `state.sensitivity` and notifying unconditionally never cost anything.

    This cadence makes it the only recurring `notify()` an idle Workbench has — a whole-shell
    re-render every 2s for the life of an open drawer, over an answer with the same bytes in it.
    Four ticks, one changed answer, one redraw.
    """
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": True, "ticks": 4}])
    assert run["readsWhileOpen"] == 4, "the premise: it did keep asking"
    assert run["notifiesWhileOpen"] == 1, "and told the shell only about the answer that moved"


def test_a_read_the_server_could_not_answer_does_not_read_as_a_deployment_with_no_lock():
    """FOUND IN REVIEW, and it is the earlier gap one layer out.

    The gate learned to tell a LANDED answer from a `null` that never landed. But the route never
    500s: it answers a thrown read with a 200 carrying the unlocked shape, so an exception inside
    `sensitivity_state` landed as "this deployment has no lock" — which stopped the cadence for the
    life of the drawer and never retried. The stale-"unlocked" outcome the gate exists to prevent,
    reached through the one shape it could not read.

    `unavailable` is the server saying which of the two this is. Everything else about that payload
    is deliberately unchanged: the picker failing open on a failed read is that route's own
    documented trade, and the lock still holds in the router and at publish whatever it answers.
    """
    broke, really_unlocked = _ticked([
        {"watch": "stream", "locked": True, "open": True, "ticks": 4, "unavailableFor": 2},
        {"watch": "stream", "locked": False, "open": True, "ticks": 4},
    ])
    assert broke["readsWhileOpen"] == 4, "two failures, and it was still asking on the fourth tick"
    assert broke["lockedAfterTicks"] is True and broke["drawnAfterTicks"] == "gpt-5.4"
    # And the standing lock was never dropped on the way through. One notify, for the one answer
    # that actually moved — not two, which is the payload being installed and then corrected.
    assert broke["notifiesWhileOpen"] == 1
    # And the settled fact still stops it, or the gate would just be gone.
    assert really_unlocked["readsWhileOpen"] == 0


def test_an_unusable_answer_is_refused_where_it_would_be_installed_not_at_each_reader():
    """FOUND IN REVIEW, and it is the round that changed the shape rather than filling a case.

    `unavailable` started as a flag the cadence gate read. Three readers needed it: the gate, the
    app-records repair, and the assignment in `refreshSensitivity` itself — which was still
    installing the never-500 payload over a standing lock, so an outage unlocked the drawer every
    2s instead of once per scope load. Patching each reader was the wrong shape; the route encodes
    "I could not answer" as "there is nothing to answer", and exactly one place should un-do that.

    It is refused where answers land, so `state.sensitivity` is null or a real answer and never the
    third thing. Measured on the arrangement that reaches both readers at once — nothing landed at
    boot, and the drawer's own read is one the server could not answer:

        with the refusal:     appReads 1, readsWhileOpen 4, recovers locked
        without it:           appReads 0, readsWhileOpen 1, never recovers
    """
    (run,) = _ticked([{"watch": "stream", "locked": True, "open": True,
                       "ticks": 4, "failFromBoot": 3, "unavailableFromOpen": 2}])
    assert run["lockedOnOpen"] is False, "the premise: the drawer opened with nothing landed"
    # The cadence is still asking on the fourth tick rather than having stopped on tick one.
    assert run["readsWhileOpen"] == 4
    # And the #264 repair still runs, because an unusable answer is not one having landed.
    assert run["appReads"] == 1
    assert run["lockedAfterTicks"] is True and run["drawnAfterTicks"] == "gpt-5.4"
