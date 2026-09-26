"""A viewer hides what Sage read, but never a read that fell short (#448, ADR-0062).

An answer arrives with its disclosure beside it: `data_used` says what was read and how much came
back, and the investigation opened/closed line says when the grant changed. Both are load-bearing
and neither was written for a reader who did not ask — a person who is not a data scientist sees
table paths, request ids and decision stages, and reads machinery where we intended proof.

`dataAccessShown` puts both away for that viewer, and the interesting half of this file is what it
still cannot hide. A read that came back short is drawn whatever the preference says, because hidden
it is indistinguishable from a read that worked, and the viewer's own preference is what made it so
— the same argument as the crossing receipt force-opening when an Upload did not cross (ADR-0023).
And `investigation_offer` is drawn INSTEAD of an answer, so hiding it would render a turn with
nothing in it.

Everything here runs through `js/data_access_harness.mjs`, which loads the real store and the real
block dispatcher into one sandbox. None of these claims can be read off the source:

* The TABLE. `HIDDEN_BY_DATA_ACCESS` gives every `block.type` a row with no default, so that the
  population cannot go quietly short — a hide-list drifts silently when a card is added next year,
  and a keep-list drifts the other way and buries a new disclosure. The harness reads the case
  labels out of the running dispatcher's own source and holds them against the table both ways. A
  Python test grepping the literal would pin the string rather than the behaviour.

* What the store's read PRODUCES, for each of the two block types the preference governs and for
  the statuses it must leave alone. `status` carries fifteen distinct meanings, and only the one
  mint site that stamps `fromEvent` can be told apart on the block.

* The LIVE path. ADR-0062 puts the filter in the three history functions and says nothing about the
  SSE reducer, which pushes straight into `state.messages`. So the live turn is driven here too.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
_HARNESS = Path(__file__).resolve().parent / "js" / "data_access_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(payload: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _coverage(**short: int) -> dict:
    """A `coverage` record in the shape the readers build it.

    COUNTS, not states: `liveread/run.py:614`, `calculate.py:157` and `text_analysis.py:130` all
    build `{total, processed, excluded, failed, unfinished}`. Clean by default, so each test names
    only the count it is about.
    """
    record = {"total": 12, "processed": 12, "excluded": 0, "failed": 0, "unfinished": 0}
    record.update(short)
    return record


def _event(operation_id: str = "du_1", **over) -> dict:
    """One `dataUsed` event as `DataUse.record` persists it."""
    event = {
        "operation_id": operation_id,
        "turn_id": "turn_a",
        "operation": "live_read",
        "source": "sales.csv",
        "artifact": f"examples/thr_1/{operation_id}.table.json",
        "columns": ["region", "revenue"],
        "selected_fields": ["region", "total"],
        "coverage": _coverage(),
        "requests": [],
    }
    event.update(over)
    return event


def _thread(*history: dict) -> dict:
    return {"id": "thr_1", "history": [{"type": "user", "text": "totals by region"}, *history]}


def _answer(result: dict, key: str = "before") -> dict:
    """The one assistant message. Every transcript here is a single question and its answer.

    The store's marks and the answer's rendering are checked against each other on every single
    read, here, rather than in one test that could be the one nobody adds. `drawn` is counted off
    what `SW.Message` returned and `types` off what the store marked, so a component that stopped
    skipping marked blocks — or skipped one the store had not marked — reds whichever test is
    looking, instead of passing because the harness kept its own copy of the filter.
    """
    answers = [m for m in result[key] if m["role"] == "assistant"]
    assert len(answers) == 1, answers
    answer = answers[0]
    assert answer["drawn"] == len(answer["types"]), (
        f"the store marked {len(answer['types'])} blocks to draw and the answer drew "
        f"{answer['drawn']}: {answer}")
    return answer


_A_CLEAN_READ = _thread(
    {"type": "data_used", "dataUsed": [_event()]},
    {"type": "investigation-state", "state": "open"},
    {"type": "agent", "kind": "text", "text": "Revenue by region is attached."},
)


# ---- the table -----------------------------------------------------------------------------------

@needs_node
def test_every_block_type_the_dispatcher_draws_has_a_row():
    """The shape ADR-0062 chose, and the reason it chose it.

    A hide-list would drift silently — a card added next year shows, and nobody finds out. A
    keep-list drifts the other way and buries a new disclosure. Neither failure announces itself,
    so the rule is a table over the whole population with no default, and this is the check that
    the population is still whole.

    Both directions, because both are real drift: `missingRows` is a type the dispatcher gained
    without a row, and `staleRows` is a row for a card that no longer exists.
    """
    result = _run({"table": True})
    assert result["missingRows"] == []
    assert result["staleRows"] == []
    # The count is not the claim — the two emptinesses above are — but it pins the order of
    # magnitude, so a switch that collapsed to three cases could not pass by matching a table that
    # collapsed with it.
    assert len(result["rows"]) == 37
    # Every `case` keyword in the dispatcher yielded a label this scanner could read. The labels are
    # matched with a pattern, and a pattern bounds the population it can see: a narrow one hid
    # `case 'chartV2':` from the comparison entirely, so the type was neither counted against the
    # table nor reported missing from it. Counting the keyword needs no pattern, so a label shape
    # the scanner cannot parse reds here rather than passing as a clean sheet.
    assert result["keywords"] == len(result["cases"]) == 37


@needs_node
def test_the_preference_governs_exactly_two_things():
    """ADR-0062: it governs `data_used` and the investigation line, and "It governs nothing else."

    Derived from the real table, not read off it. Thirty-four rows spell "nothing else" by sharing
    one `shown` constant, which means flipping any of them to hide was a one-word edit that took a
    viewer's table receipts, charts or withhold notices away and reddened nothing — the forbidden
    outcome was not unreachable, only untested.

    An equality over a derived list rather than a check that each row is absent from some set: the
    absence check is already satisfied by the constant it is meant to be testing.
    """
    result = _run({"table": True})
    # A plain block of each type. `status` is not here because a status hides only when it carries
    # the investigation origin, which the conditional pair below is for.
    assert result["governed"] == ["data_used"]
    assert result["conditional"] == {
        # The two the preference governs.
        "investigationLine": True,
        "aWholeRead": True,
        # The two it must never touch: any other status, and a read that fell short.
        "aTurnsFailure": False,
        "aShortRead": False,
    }


@needs_node
def test_a_case_label_only_counts_when_the_dispatcher_really_handles_it():
    """Reading labels out of source can pick up a mention in a comment or a string, which would pad
    the population with a type nothing draws. So each label is driven: `null` back is the
    dispatcher's `default`, and a label that reaches it was never a case."""
    result = _run({"table": True})
    assert result["handled"] == result["cases"]


# ---- what the preference hides -------------------------------------------------------------------

@needs_node
def test_a_clean_answer_carries_no_disclosure_for_a_viewer_who_did_not_ask():
    """The fallback, which is the whole point of the ticket. `dataAccessShown` is `false` on a first
    visit, so the answer is the answer."""
    answer = _answer(_run({"thread": _A_CLEAN_READ, "shown": False}))
    assert answer["types"] == ["text"]
    assert answer["hidden"] == 2
    assert sorted(answer["withheld"]) == ["data_used", "status"]


@needs_node
def test_the_same_answer_carries_both_for_a_viewer_who_asked():
    """The other half, and it has to be checked in the same shape: a filter that hid everything
    unconditionally would pass the test above on its own."""
    answer = _answer(_run({"thread": _A_CLEAN_READ, "shown": True}))
    assert answer["types"] == ["data_used", "status", "text"]
    assert answer["statuses"] == ["investigation-state"]
    assert answer["hidden"] == 0


@needs_node
def test_the_preference_leaves_every_other_status_on_screen():
    """Fifteen distinct meanings mint into `{ type: 'status' }` and the type is discarded at every
    site but one, so a rule written on the block type alone would hide a turn's failure and its
    spend along with the investigation line. `fromEvent` is stamped at `investigation-state` only,
    and these two are what it has to leave behind: why a turn ended, and what it cost."""
    thread = _thread(
        {"type": "investigation-state", "state": "open"},
        {"type": "delegated-calls", "message": "Two model calls on qwen-2-5."},
        {"type": "error", "message": "Timed out. Try a narrower question."},
    )
    # Started ON and then put away, which is the round trip that makes the TABLE the thing under
    # test. Thirty-three mint sites push straight onto the message and only the two governed ones go
    # through the filter, so reading a transcript that began with the preference off would leave
    # these two statuses on screen whatever the `status` row said — a green that proves the mint
    # site and not the rule. `setDataAccessShown` re-partitions every block through `pushBlock`, so
    # here the row is what decides, for all three statuses.
    result = _run({"thread": thread, "shown": True, "toggle": False})
    assert _answer(result)["statuses"] == ["investigation-state", None, None]
    answer = _answer(result, "after")
    assert answer["types"] == ["status", "status"]
    # Neither survivor carries an origin, which is what says the stamp went to one mint site and
    # the other fourteen were left alone.
    assert answer["statuses"] == [None, None]
    assert answer["withheld"] == ["status"]

    # And the same rows read the same way on a transcript that was never shown, so the two paths
    # into `pushBlock` cannot disagree about a turn's failure or its spend.
    direct = _answer(_run({"thread": thread, "shown": False}))
    assert direct["types"] == answer["types"]
    assert direct["statuses"] == answer["statuses"]


# ---- what it may never hide ----------------------------------------------------------------------

@pytest.mark.parametrize("short", [
    pytest.param({"coverage": _coverage(excluded=4)}, id="excluded"),
    pytest.param({"coverage": _coverage(failed=3)}, id="failed"),
    pytest.param({"coverage": _coverage(unfinished=1)}, id="unfinished"),
    pytest.param({"requests": [{"request_id": "r1", "state": "failed", "failure": "timeout"}]},
                 id="request-failure"),
])
@needs_node
def test_a_read_that_fell_short_is_drawn_whatever_the_preference_says(short):
    """One case per condition ADR-0062 names, because each is a separate reader in the store and
    three of them are counts on one record — a rule keyed on `coverage` as a whole, or on the first
    of the three, would pass a single case and hide the other two.

    Hidden, a read that half-worked is indistinguishable from one that worked, and the viewer's own
    preference is what made it so.
    """
    answer = _answer(_run({
        "thread": _thread({"type": "data_used", "dataUsed": [_event(**short)]}),
        "shown": False,
    }))
    assert answer["types"] == ["data_used"]
    assert answer["hidden"] == 0


@needs_node
def test_one_short_operation_draws_the_whole_turns_card():
    """Since #447 one card holds a turn's operations, so the rule is read over the group. A turn
    that read one table cleanly and failed on the second must draw the card that says so — and it
    must draw the clean operation with it, because the card's job is to account for the turn."""
    answer = _answer(_run({
        "thread": _thread({"type": "data_used", "dataUsed": [_event("du_1")]},
                          {"type": "data_used",
                           "dataUsed": [_event("du_2", coverage=_coverage(failed=2))]}),
        "shown": False,
    }))
    assert answer["types"] == ["data_used"]
    assert answer["hidden"] == 0


@needs_node
def test_a_read_that_goes_short_later_surfaces_without_a_reload():
    """`DataUse.observe`'s `save()` re-persists the event as each gateway request settles, so
    `failure` routinely arrives AFTER the copy that first minted this card. The rule "never hide a
    read that fell short" therefore cannot be a decision taken once at mint time: the first copy
    here is clean and withheld, the second names a failure, and the card is owed to the viewer then
    rather than on their next reload."""
    answer = _answer(_run({
        "thread": _thread(
            {"type": "data_used", "dataUsed": [_event("du_1")]},
            {"type": "data_used", "dataUsed": [
                _event("du_1", requests=[{"request_id": "r1", "failure": "refused"}])]},
        ),
        "shown": False,
    }))
    assert answer["types"] == ["data_used"]
    assert answer["hidden"] == 0


@needs_node
def test_the_offer_to_go_and_look_is_never_hidden():
    """`message-blocks.js:1342` draws `investigation_offer` INSTEAD of an answer. Hidden, it is a
    question nobody is asked and a turn that renders nothing — so it is a shown row in the table,
    deliberately and not by omission.

    Round-tripped for the reason the statuses above are: the offer is minted by a direct push, so
    only `setDataAccessShown`'s re-partition puts it through the filter, and that is where its row
    is what answers.
    """
    thread = _thread({"type": "investigation-offer", "message": "Shall I go and look?",
                      "prompt": "totals by region", "live": True})
    result = _run({"thread": thread, "shown": True, "toggle": False})
    after = _answer(result, "after")
    assert after["types"] == ["investigation_offer"]
    assert after["hidden"] == 0
    assert after["nudge"] is None

    direct = _answer(_run({"thread": thread, "shown": False}))
    assert direct["types"] == ["investigation_offer"]
    assert direct["hidden"] == 0


# ---- the row's origin, not the pane --------------------------------------------------------------

@pytest.mark.parametrize("view", ["split", "unified"])
@needs_node
def test_the_conversation_view_does_not_change_what_is_hidden(view):
    """Under `conversationView: unified` one transcript shows both halves, so a rule written as
    "hide it in the Chat pane" would quietly change what it hides the moment somebody switched
    views. Both views are served the same rows here, which is what makes the comparison about the
    rule rather than about the reads."""
    answer = _answer(_run({"thread": _A_CLEAN_READ, "shown": False, "view": view}))
    assert answer["types"] == ["text"]
    assert answer["hidden"] == 2


# ---- the live turn, which no history read reaches ------------------------------------------------

@pytest.mark.parametrize("shown,drawn,hidden", [(False, ["text"], 1),
                                                (True, ["data_used", "text"], 0)])
@needs_node
def test_a_live_turn_hides_what_the_same_turn_hides_after_a_reload(shown, drawn, hidden):
    """The question ADR-0062 left open. It puts the filter in the three history functions, and
    `putDataUsed` is also called out of the SSE reducer, which pushes straight into `state.messages`
    without passing through any of them — so a filter that lived only in the history functions
    would draw live what the same turn hides after a reload.

    Resolved by filtering in `putDataUsed` itself, which all three of its callers share. Driven
    here as a real streamed turn, because that is the only thing that can tell the shared helper
    from three copies of the rule.
    """
    result = _run({
        "thread": {"id": "thr_1", "history": []},
        "shown": shown,
        "live": [{"type": "data_used", "dataUsed": [_event()]},
                 {"type": "delta", "text": "Revenue by region is attached.", "final": True},
                 {"type": "done"}],
    })
    answer = _answer(result)
    assert answer["types"] == drawn
    assert answer["hidden"] == hidden


# ---- what the first design got wrong ------------------------------------------------------------
#
# Withheld disclosure was parked in a side list with the index it would have had, and spliced back
# on reveal. Both defects below came from that, they were found by review rather than by the build,
# and each has its own case here because each is reachable on its own.

@needs_node
def test_a_read_that_fails_mid_turn_is_drawn_and_never_leaves_the_message():
    """The worst of them, and the one outcome ADR-0062 forbids absolutely.

    `DataUse.observe`'s `save()` re-persists an event as each gateway request settles, so a failure
    lands mid-stream and the card must be force-shown then. Restoring it USED to rebuild
    `message.blocks`, and the SSE reducer caches a position into that array and writes through it —
    so the reveal shifted the array under that index and the next flush overwrote the card with
    streamed text. The card for a failed read was deleted, and because the count was cleared with
    it, no nudge said it had ever existed.

    `onMessage` is what makes that visible: a hidden block is still on the message, a destroyed one
    is on neither list. Marking instead of removing is what fixes it — nothing moves, so no cached
    index can go stale.
    """
    frames = [
        {"type": "data_used", "dataUsed": [_event()]},
        {"type": "delta", "text": "part one ", "final": False},
        {"type": "data_used", "dataUsed": [
            _event(requests=[{"request_id": "r1", "state": "failed", "failure": "timeout"}])]},
        {"type": "delta", "text": "part one and two", "final": True},
        {"type": "agent", "kind": "text", "text": "Final answer."},
    ]
    off = _answer(_run({"thread": {"id": "thr_1", "history": []}, "shown": False, "live": frames}))
    on = _answer(_run({"thread": {"id": "thr_1", "history": []}, "shown": True, "live": frames}))
    # Drawn with the preference OFF, because the read fell short.
    assert "data_used" in off["types"]
    assert off["hidden"] == 0
    # And identical to what a viewer who asked sees: the shortfall is not a different card.
    assert off["types"] == on["types"]
    assert off["onMessage"] == on["onMessage"]


@needs_node
def test_revealing_survives_a_turn_that_replaced_its_own_blocks():
    """The other one. The parked index named a slot in `message.blocks`, and the reducer replaces
    its streamed blocks wholesale when the recorded answer arrives — so by reveal time the index
    named a different slot and the card landed below the answer it belonged above.

    Ground truth is the same frames read by a viewer who asked. A reveal that does not reproduce it
    exactly is the ordering claim broken, whatever the block count says.
    """
    frames = [
        {"type": "delta", "text": "first ", "final": True},
        {"type": "delta", "text": "second", "final": True},
        {"type": "data_used", "dataUsed": [_event()]},
        {"type": "agent", "kind": "text", "text": "Recorded answer."},
        {"type": "done", "ok": True},
    ]
    thread = {"id": "thr_1", "history": []}
    truth = _answer(_run({"thread": thread, "shown": True, "live": frames}))
    revealed = _answer(
        _run({"thread": thread, "shown": False, "live": frames, "toggle": True}), "after")
    assert revealed["types"] == truth["types"]
    assert revealed["hidden"] == 0


@pytest.mark.parametrize("state,drawn", [
    # The `finally` at `data_use.py:265` writes this at `:267` WITHOUT touching `failure`, so a
    # off mid-stream carries `{state: 'interrupted', failure: None}`. A response that visibly did
    # not finish is a read that fell short, and truthiness on `failure` alone put it away.
    pytest.param("interrupted", True, id="interrupted-is-short"),
    # The state every request is persisted with the moment it opens. Counting it would force-show
    # every card until its requests settled, so the preference would not work during a live turn.
    pytest.param("attempted", False, id="attempted-is-not-yet-short"),
    pytest.param("response_completed", False, id="completed-is-not-short"),
])
@needs_node
def test_a_request_that_did_not_settle_is_read_from_its_state_not_only_its_failure(state, drawn):
    answer = _answer(_run({
        "thread": _thread({"type": "data_used", "dataUsed": [
            _event(requests=[{"request_id": "r1", "state": state, "failure": None}])]}),
        "shown": False,
    }))
    assert ("data_used" in answer["types"]) is drawn
    assert answer["hidden"] == (0 if drawn else 1)


@needs_node
def test_a_browser_that_will_not_store_the_choice_still_honours_the_click():
    """prefs.js refuses a write when storage is blocked or full, and when the viewer's identity has
    not landed yet — which prefs.js itself calls a real window, not a theoretical one. On a refusal
    the stored value does not move, so re-reading it answers with the value from BEFORE the click:
    the re-partition was a no-op, the transcript kept hiding while the drawer's box sat ticked, and
    the warning said "it won't persist next time" when the truth was "it did not happen at all".

    For somebody in that state the nudge on the answer is the only way in, so an inert nudge means
    the disclosure can never be reached. Held for the session first, then filed — the order is the
    fix.
    """
    result = _run({"thread": _A_CLEAN_READ, "toggle": True, "storage": "blocked"})
    assert _answer(result)["nudge"] == "Show data access (2)"
    after = _answer(result, "after")
    assert after["types"] == ["data_used", "status", "text"]
    assert after["hidden"] == 0
    # Nothing was filed, and the person was told so rather than left to find out on the next load.
    assert result["stored"] is None
    assert result["warnings"] == [
        "This browser isn't saving the choice, so it won't persist next time."]


# ---- the two controls ----------------------------------------------------------------------------

@needs_node
def test_the_answer_offers_the_way_in_to_what_it_withheld():
    """A preference whose fallback is off is invisible, and nobody goes looking for a thing they
    have never seen — so the answer carries the way in. Counted, because one withheld card and two
    are different offers and a person deciding whether to look wants to know which they are
    getting."""
    assert _answer(_run({"thread": _A_CLEAN_READ, "shown": False}))["nudge"] \
        == "Show data access (2)"
    one = _thread({"type": "investigation-state", "state": "open"})
    assert _answer(_run({"thread": one, "shown": False}))["nudge"] == "Show data access"


@needs_node
def test_an_answer_with_nothing_withheld_offers_nothing():
    """The nudge is drawn from what the store stamped, and the stamp exists only when something was
    actually withheld. So this is also the check that the answer cannot read the preference by
    accident: with disclosure on there is nothing to reveal and no offer to reveal it."""
    assert _answer(_run({"thread": _A_CLEAN_READ, "shown": True}))["nudge"] is None
    plain = _thread({"type": "agent", "kind": "text", "text": "No data was read."})
    assert _answer(_run({"thread": plain, "shown": False}))["nudge"] is None


@needs_node
def test_the_first_click_both_reveals_the_disclosure_and_records_the_choice():
    """The `chipScopeHintDismissed` shape, where the viewer's own action retires the nudge. Both
    halves matter: revealing without recording means the nudge is back on the next answer, and
    recording without revealing means the click did nothing anyone can see."""
    result = _run({"thread": _A_CLEAN_READ, "shown": False, "toggle": True})
    assert _answer(result)["types"] == ["text"]
    after = _answer(result, "after")
    assert after["types"] == ["data_used", "status", "text"]
    assert after["nudge"] is None
    assert json.loads(result["stored"]) == {"u1": {"dataAccessShown": True}}


@needs_node
def test_revealed_disclosure_lands_where_the_read_put_it():
    """Not appended. "Investigation opened" restored below the answer it opened for is a different
    claim about when the grant changed, and a `data_used` card that moved would break the reading
    order the transcript was written in."""
    result = _run({"thread": _A_CLEAN_READ, "shown": False, "toggle": True})
    assert _answer(result, "after")["types"] == ["data_used", "status", "text"]
    assert _answer(_run({"thread": _A_CLEAN_READ, "shown": True}))["types"] \
        == _answer(result, "after")["types"]


@needs_node
def test_unticking_the_box_puts_the_disclosure_away_again():
    """The drawer's checkbox is one control and has to work both ways. A writer that only revealed
    would leave a viewer who changed their mind looking at a box they had unticked and a transcript
    that had not moved — which reads as a broken control, next to the thing it governs."""
    result = _run({"thread": _A_CLEAN_READ, "shown": True, "toggle": False})
    assert _answer(result)["types"] == ["data_used", "status", "text"]
    after = _answer(result, "after")
    assert after["types"] == ["text"]
    assert after["hidden"] == 2
    assert after["nudge"] == "Show data access (2)"


# ---- where the preference is declared, and who reads it ------------------------------------------

def test_the_quiet_answer_is_what_a_first_visit_gets():
    prefs = (_JS / "prefs.js").read_text()
    assert "dataAccessShown: { fallback: false, values: [true, false] }" in prefs


@needs_node
def test_the_fallback_is_off_for_a_viewer_with_no_record():
    """The line above is the declaration; this is prefs.js answering. A value with no branch behind
    it reads back as the fallback, so the declaration alone is a written claim."""
    out = subprocess.run(["node", str(_HARNESS.parent / "prefs_harness.mjs")],
                         input=json.dumps([{"viewer": "u1", "op": "get",
                                            "name": "dataAccessShown"}]),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [False]


def test_only_the_store_branches_on_the_data_access_preference():
    """The house rule `test_only_the_store_branches_on_the_preference` holds for the conversation
    view, held here. The store decides what a Conversation's blocks ARE and a component is handed
    the answer — a second reader is a second place two views can disagree, and #61's arm was
    deletable because there was one branch to delete.

    `shell.js` is a reader because the drawer's checkbox has to show the value it is going to write.
    It draws no transcript, so it cannot disagree with one.
    """
    readers = sorted(p.relative_to(_JS).as_posix() for p in _JS.rglob("*.js")
                     if "dataAccessShown" in p.read_text())
    assert readers == ["components/shell.js", "prefs.js", "store.js"]


def test_the_preference_never_reaches_what_sage_does():
    """Rejected firmly in ADR-0062: the same question would return different answers depending on a
    display setting, and every bug report would be unanswerable without knowing a value nobody
    thinks to mention. So no Python reads it, the way none reads `conversationView`."""
    backend = Path(__file__).resolve().parents[1] / "sage"
    assert [p for p in backend.rglob("*.py") if "dataAccessShown" in p.read_text()] == []


def _loads_prefs(source: str) -> bool:
    """Whether this harness actually LOADS prefs.js, rather than merely naming it.

    A plain `"prefs.js" in source` is what the first version of the check below asked, and it was
    defeated by the comment explaining the load: strip the file from the loader, leave the sentence
    above it, and the grep still passed. That is the class this repo keeps paying for — a scanner
    cannot tell a signal from a quote of it — and it bit the fix for it one layer down.

    So comments come off FIRST, and then this matches the two load idioms these harnesses actually
    use: the file in a loader list beside `store.js`, or its own `readFileSync`. Tightening the
    pattern was tried twice and failed twice — a comment naming `prefs.js` defeated the plain
    substring, and a comment quoting a whole loader list defeated the bracketed pattern that
    replaced it. No pattern can separate a signal from a quote of the signal, so the fix is to
    delete the place quotes live rather than to describe them better.

    What is left is a quote inside a string literal in live code, which this still cannot see. That
    is stated rather than asserted away, because the last three attempts to close this by pattern
    each looked closed and were not.

    Order-agnostic within the list. Most of these load `store.js` before `prefs.js`, and that is
    fine: prefs.js defines `SW.prefs` and reads storage only when a caller asks, so the store having
    been evaluated first costs nothing. A first version of this required prefs.js FIRST and flagged
    forty-nine harnesses — the check was wrong, not the harnesses, and a red that large is the
    check confessing rather than the tree.
    """
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.MULTILINE)
    if "ROOT + 'prefs.js'" in code:
        return True
    loaders = re.findall(r"\[[^\]]*'store\.js'[^\]]*\]", code, re.DOTALL)
    return any("'prefs.js'" in loader for loader in loaders)


def test_every_harness_that_loads_the_store_loads_the_preference_it_depends_on():
    """The store asked `SW.prefs` about the conversation view at two sites before this ticket, and
    neither sat on a path a harness routinely walks. `putDataUsed` does: since #448 the reducer asks
    the preference before it puts a card on an answer, so a sandbox without `SW.prefs` throws inside
    the stream — and the reducer CATCHES, which turns a missing stub into a block that quietly never
    arrives rather than into an error anybody can read. That is how four harnesses failed here, and
    one failed by measuring zero cards where the fix was a stub and not the store.

    EVERY harness that loads the store, not the ones that happen to mint disclosure today. The first
    version of this check derived its population as "loads `store.js` and mentions `dataUsed` or
    `investigation-state`", which matched exactly one file — the other three this diff had to fix
    contain neither token, because the rows that carry them come from the Python side. A guard that
    passes on a population of one reads exactly like diligence, so the rule is now the dependency
    itself: drive the store, load its preferences.

    Cheap to satisfy and cheap to keep. `prefs.js` touches storage only inside `get` and `set`, so
    loading it costs a harness that never calls either of them nothing at all.
    """
    harnesses = Path(__file__).resolve().parent / "js"
    drives = sorted(p.name for p in harnesses.glob("*.mjs") if "'store.js'" in p.read_text())
    # Not an emptiness guard — a floor. A population of one passed the version of this check that
    # shipped first, so a number that can only be met by the real population is what replaces it.
    assert len(drives) > 40, f"the derivation found only {len(drives)} — check the grep, not the green"
    short = [name for name in drives if not _loads_prefs((harnesses / name).read_text())]
    assert short == []


def test_the_settings_drawer_carries_the_group_and_says_what_it_cannot_do():
    """Named for the group and not for the card. An investigation notice is permission GRANTED, not
    data USED, so a viewer who unticked "Data used" and then stopped seeing investigation notices
    would have been surprised by their own setting.

    And the hint is load-bearing copy: without it the control reads as a way to switch disclosure
    off, and a read that came back short would look like the setting had failed.
    """
    shell = (_JS / "components" / "shell.js").read_text()
    drawer = shell[shell.index("SW.SettingsDrawer"):]
    assert "'Data access'" in drawer
    assert "A read that failed or came back incomplete is always shown." in drawer
    # Written through the store, which owns the transcript this changes. A `save()` here would
    # leave the drawer agreeing with a transcript nobody had re-partitioned.
    assert "SW.store.setDataAccessShown(value)" in drawer
