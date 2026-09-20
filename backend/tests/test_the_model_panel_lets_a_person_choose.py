"""The model panel — the drawer behind the chip, and the only door Auto and Ask have (ADR-0017).

The backend half of this is `test_a_person_picks_the_model_a_mode_runs_on.py`. This is the half a
person touches: which rows they see, what each row offers, what a click writes, and the two states
where the panel is readable but closed. Driven through the real store and the real component, with a
stubbed `createElement`, so a Select's options and disabled flag are settled here rather than
described.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent / "js" / "model_assignments_harness.mjs"


def _drawn(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps), check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _row(drawn: dict, slot_label: str) -> dict:
    return next(r for r in drawn["rows"] if r["label"] == f"{slot_label} model")


def _model_rows(drawn: dict) -> list[dict]:
    """The MODEL select of each slot row. A row has carried two controls since ADR-0049 — the second
    one is the reasoning effort — so counting every Select in the tree no longer counts slots."""
    return [r for r in drawn["rows"] if r["label"].endswith(" model")]


def _effort_row(drawn: dict, slot_label: str) -> dict | None:
    """The effort select of one row, or None where no control was drawn at all. The difference is the
    rule: a model the gateway measured as discarding `reasoning_effort` gets no control, because a
    control whose setting is thrown away is worse than none (#280)."""
    return next((r for r in drawn["rows"] if r["label"] == f"{slot_label} reasoning effort"), None)


# ---- what the panel offers ------------------------------------------------------------------------


def test_the_panel_lists_three_slots_and_names_the_one_chat_also_uses():
    """Three, not four: Auto has no assignment of its own, so a row for it would change nothing.

    And `ask` is one row for two consumers — `_resolve_chat` returns `catalog.ask` as CHAT_DEFAULT —
    so the label discloses that rather than repointing Chat silently."""
    (drawn,) = _drawn([{}])
    assert drawn["labels"] == ["Plan", "Implement", "Ask and Chat"]


def test_two_slots_holding_one_model_are_still_two_rows():
    """The override menu collapses those, because it offers MODELS and two identical rows would be a
    choice with no difference. The panel offers SLOTS, where the same model in two of them is
    exactly the thing a person came here to change."""
    (drawn,) = _drawn([{}])
    assert _row(drawn, "Plan")["value"] == "__default__"
    assert _row(drawn, "Ask and Chat")["value"] == "__default__"
    assert len(_model_rows(drawn)) == 3


def test_every_row_offers_the_way_back_to_the_default_by_name():
    """"Use the default" without the name is not the way back — it is a promise a person has to take
    on faith, and after an assignment the catalog on screen no longer holds what it goes back to."""
    (drawn,) = _drawn([{}])
    first = _row(drawn, "Implement")["options"][0]
    assert first == {"value": "__default__", "label": "Use the default (coder)",
                     "disabled": False, "title": None}


def test_a_model_that_cannot_hold_a_conversation_is_not_offered():
    # Same rule the Chat picker applies, from the same place — `SW.util.chatCapable`.
    (drawn,) = _drawn([{}])
    assert "embed-3" not in [o["value"] for o in _row(drawn, "Plan")["options"]]


def test_an_alias_whose_endpoint_is_stopped_is_shown_and_refused():
    """Hiding it answers "where did that model go" with nothing; allowing it fails opaquely
    mid-build. It is listed, disabled, and carries the reason — prevention rather than a good error
    message afterwards."""
    (drawn,) = _drawn([{}])
    dead = next(o for o in _row(drawn, "Plan")["options"] if o["value"] == "local-llm")
    assert dead["disabled"] is True
    assert "not serving" in dead["label"]
    assert "Start that endpoint" in dead["title"]


# ---- what a click does ----------------------------------------------------------------------------


def test_choosing_a_model_writes_that_slot_and_only_that_slot():
    """A slot nobody mentioned must not be sent: absent means "leave it" and present-and-empty means
    "take it back", and a panel that posted all three on every change would revert the other two."""
    (drawn,) = _drawn([{"set": ["plan", "opus"]}])
    assert drawn["wrote"] == [{"catalog": {"plan": "opus"}}]
    assert drawn["after"] == "opus"


def test_the_default_row_clears_the_assignment_rather_than_setting_it():
    # `null`, not the default's model id. Writing the id back would leave the slot pinned to whatever
    # the default happens to be today, which is a different thing from following it.
    (drawn,) = _drawn([{"set": ["implement", "__default__"]}])
    assert drawn["wrote"] == [{"catalog": {"implement": None}}]
    assert drawn["after"] == "__default__"


# ---- the two states where it is readable but closed ------------------------------------------------


def test_a_running_build_closes_the_rows_and_says_why():
    """Nothing pins the catalog for the duration of a turn, so a change accepted here would move the
    rest of that build onto another model. Closed, not hidden: the assignments stay readable."""
    (drawn,) = _drawn([{"running": True}])
    assert drawn["rows"] and all(r["disabled"] for r in drawn["rows"])
    assert drawn["labels"] == ["Plan", "Implement", "Ask and Chat"]
    (alert,) = [a for a in drawn["alerts"] if a["message"] == "A build is running"]
    assert "Wait for this build to finish" in alert["description"]


def test_a_gateway_that_will_not_list_models_says_so_and_offers_a_retry():
    """An empty select reads as "you have no models" rather than "the gateway did not answer", and
    only one of those is something a person can act on. It must not fall back to offering the models
    already assigned either — a list that can only offer what is already chosen cannot express a
    change, which is the defect this whole panel exists to fix."""
    (drawn,) = _drawn([{"listing": "down"}])
    (alert,) = [a for a in drawn["alerts"] if a["type"] == "warning"]
    assert "not answering" in alert["description"]
    assert alert["hasAction"] is True
    assert drawn["rows"] and all(r["disabled"] for r in drawn["rows"])
    # The current assignments are still on screen — that is what "readable but closed" means.
    assert drawn["labels"] == ["Plan", "Implement", "Ask and Chat"]


def test_a_gateway_that_lists_no_models_at_all_still_explains_itself():
    """A listing that succeeded and came back empty closes these rows just as firmly as one that
    failed, and it has no error to report. Saying nothing there would ship a fresh copy of the
    unexplained disabled control this panel was built to replace."""
    (drawn,) = _drawn([{"listing": "empty"}])
    (alert,) = [a for a in drawn["alerts"] if a["type"] == "warning"]
    assert alert["message"] == "No models available to you"
    assert "Ask an administrator for access" in alert["description"]
    # `CONTEXT.md` puts bare "gateway" on the _Avoid_ list of both LLM Gateway and AI Gateway, and
    # `tools/brand_lint.py` only reaches `SW.brand.*` call sites — a plain literal like this one is
    # exactly where the term drifts back in.
    assert "the gateway" not in alert["description"]
    assert drawn["rows"] and all(r["disabled"] for r in drawn["rows"])


# ---- what the two review axes found untested --------------------------------------------------------


def test_a_read_that_never_lands_still_shows_what_each_mode_runs():
    """A gateway that answers "I cannot list" is not the same failure as a read that never arrives:
    the first carries the slots, the second carries nothing. Falling back to the catalog the status
    poll already keeps current is what keeps the panel readable rather than empty — the difference
    between a control that is closed and one that is blank."""
    (drawn,) = _drawn([{"listing": "throw"}])
    assert drawn["labels"] == ["Plan", "Implement", "Ask and Chat"]
    # The model each slot runs is on screen, and it is the only option — the default it would revert
    # to is exactly what a failed read does not know, so no row claims to offer it.
    plan = _row(drawn, "Plan")
    assert plan["value"] == "gpt-5.4"
    assert plan["options"] == [{"value": "gpt-5.4", "label": "gpt-5.4", "disabled": False,
                                "title": None}]
    assert drawn["rows"] and all(r["disabled"] for r in drawn["rows"])


def test_a_listing_that_arrived_leaves_the_rows_open_and_says_what_went_unchecked():
    """`_endpoint_listing` failing means only that reachability went unchecked. Reporting that as
    "can't list the models" would close a panel whose list is right there, and the rows would be
    disabled under a sentence that is not true of them."""
    (drawn,) = _drawn([{"listing": "unchecked"}])
    (alert,) = [a for a in drawn["alerts"] if a["type"] == "warning"]
    assert alert["message"] == "Couldn't check every model"
    assert not any(r["disabled"] for r in drawn["rows"])


def test_a_slot_pointed_at_a_model_that_will_not_answer_says_so_on_the_slot():
    """The save-time re-check, which is the half a greyed menu row cannot carry: that one says the
    MODEL is bad, and this says the SLOT is. Without it, assigning a stopped endpoint looks saved and
    fails on the next build."""
    (_, after_save) = _drawn([{"set": ["plan", "local-llm"]}, {}])
    assert any("Start that endpoint" in p for p in after_save["problems"])


def test_a_save_that_lands_survives_the_reload_that_does_not():
    """The narrow window: the write succeeds, the re-read fails. `applyModelStatus` has already moved
    the chip onto the new model, so leaving the panel's own rows at their pre-save values puts two
    controls on screen disagreeing — and the drawer is the one saying the save did not happen. The
    only honest reading of an old value under "couldn't check every model" is that it was refused,
    so the person saves again."""
    (drawn,) = _drawn([{"set": ["plan", "opus"], "failReload": True}])
    assert drawn["wrote"] == [{"catalog": {"plan": "opus"}}]
    assert drawn["after"] == "opus", "the panel redrew the pre-save model"


# ---- the sensitivity lock (ADR-0043) -------------------------------------------------------------
# The lock does not close this panel: approved models stay assignable in it, which is the whole
# reason the rest are drawn disabled rather than hidden. What it DOES change is what each row shows
# it runs, because the router substitutes at turn time and a row still naming the barred assignment
# is a control describing a model no turn will use.

# A Bindings lock over the harness's own Aliases: `gpt-5.4` is barred, `coder` and `opus` are not,
# and the slots resolve to two DIFFERENT approved models — which is the case a panel drawn off one
# `sensitivity.model` for all three rows gets wrong.
_LOCK = {
    "enabled": True, "locked": True, "group": "sensitive-approved",
    "approved": ["coder", "opus"], "datasets": ["sales-2026"], "refusal": None,
    "model": "opus", "chat_model": "coder",
    "slot_models": {"plan": "opus", "implement": "coder", "ask": "coder"},
    # Sent on every answer since #294, so a fixture that omits them describes a payload no
    # deployment produces — and because the row asks the SERVED flag and falls through to the
    # browser's mirror only when the key is ABSENT, omitting them here put every assertion in this
    # file on the legacy branch. False by default: a pick is the exception, and the two fixtures
    # that mean one say so.
    "picked": False, "chat_picked": False,
    "reason": "declared",
}


def _lock(**over: object) -> dict:
    return {**_LOCK, **over}


def test_a_locked_row_shows_the_model_it_will_actually_run():
    """FOUND IN LIVE QA (2026-09-10): every row went on reading its barred assignment with "not
    allowed" beside it, which is a select answering the wrong question — the row exists to say what
    this mode runs, and under the lock that is never the barred model."""
    (drawn,) = _drawn([{"sensitivity": _lock()}])
    assert _row(drawn, "Plan")["value"] == "opus"
    assert _row(drawn, "Ask and Chat")["value"] == "coder"


def test_each_slot_moves_where_its_own_mode_moves():
    """Two rows, two answers. `llm_router._lock_preferences` prefers the sovereign slot of the mode
    a turn is in, so a deployment whose sovereign slots differ and are separately approved gets a
    different model per row — and one field reused for all three would name the right model on one
    row and the wrong one on the other two."""
    (drawn,) = _drawn([{"sensitivity": _lock()}])
    assert _row(drawn, "Plan")["value"] != _row(drawn, "Ask and Chat")["value"]


def test_a_slot_already_holding_an_approved_model_is_left_alone():
    """Substituting there would replace a real assignment with a name the router never chose: an
    approved pick runs on itself, and the row has nothing to correct."""
    (drawn,) = _drawn([{"sensitivity": _lock()}])
    # `implement` runs `coder`, which is approved, so the row still offers the way back.
    assert _row(drawn, "Implement")["value"] == "__default__"
    assert not any("not coder." in d for d in drawn["details"])


def test_a_substituted_row_says_what_it_would_have_run():
    """Without it the panel simply shows a model nobody chose, and the person who set the slot reads
    the row as having lost their assignment."""
    (drawn,) = _drawn([{"sensitivity": _lock()}])
    assert "This runs opus, not gpt-5.4." in drawn["details"]


def test_a_row_whose_move_the_server_could_not_work_out_is_not_invented():
    """`nearest_approved` reads the sovereign slots and the administrator's ordering, and a second
    copy of that rule in JavaScript would be a confident label wrong exactly where it matters. With
    no answer the row keeps its own model, disabled in the menu with the reason on it."""
    (drawn,) = _drawn([{"sensitivity": _lock(slot_models={})}])
    assert _row(drawn, "Plan")["value"] == "__default__"
    assert not drawn["details"]


def test_the_lock_notice_says_the_limit_and_stops_there():
    """One paragraph. The scope sentence that used to ride under it — what happens to a Data Source
    bound alongside — is a lesson about a different object, read here by somebody who came to change
    a model and now has two paragraphs to get through first."""
    (drawn,) = _drawn([{"sensitivity": _lock()}])
    (alert,) = [a for a in drawn["alerts"] if a["message"] == "Allowed models only"]
    assert alert["paragraphs"] == [
        "Only approved models can be used with the Dataset sales-2026."
    ]


def test_a_session_lock_still_says_how_to_get_out_of_it():
    """The one paragraph that is not scope prose: under a sticky lock, removing the Dataset does
    nothing and a person told only "approved models only" is at a dead end."""
    (drawn,) = _drawn([{"sensitivity": _lock(reason="session", datasets=[])}])
    (alert,) = [a for a in drawn["alerts"] if a["message"] == "Allowed models only"]
    assert len(alert["paragraphs"]) == 2
    assert "Start a new chat" in alert["paragraphs"][1]


# ---- the pin the drawer could not see (#276) -------------------------------------------------------


_LOCKED = {
    "enabled": True, "locked": True, "group": "approved-for-sensitive", "approved": ["opus"],
    "datasets": ["claims"], "refusal": None, "model": "opus", "chat_model": "opus",
    "slot_models": {"plan": "opus", "implement": "opus", "ask": "opus"}, "reason": "declared",
}

_SHADOW = ("The implement model (coder) runs every Turn in this session, so this model won't run."
           " Change the implement model to switch.")
# The `ask` row's own sentence: the pin does not reach Chat, so that slot still drives a model.
_SHADOW_ASK = ("The implement model (coder) runs every Turn in this session, so this model only"
               " runs in Chat. Change the implement model to switch.")
# The lock that closes every model. `_locked_slot_models` returns nothing at all when the approved
# set resolves to none, so this is the lock holding with no substitute to name.
_LOCKED_DEAD = dict(_LOCKED, approved=[], slot_models={},
                    refusal="No models are approved for sensitive data yet.")


def test_a_shadowed_row_draws_the_sentence_and_the_holder_row_does_not():
    """The drawer is where the choice is made and it said nothing about the pin: a slot the pin had
    taken out of play drew exactly like a live one. Two sentences over three rows is the assertion —
    the holder is the row without one, because it runs exactly what it says, and a sentence telling
    the reader to change the model they are looking at would send them backwards."""
    (drawn,) = _drawn([{"signing": "implement"}])
    assert drawn["labels"] == ["Plan", "Implement", "Ask and Chat"]
    assert drawn["problems"] == [_SHADOW, _SHADOW_ASK]


def test_the_lock_takes_the_shadow_off_a_row_it_has_already_moved():
    """The lock outranks the pin (`llm_router._lock_sensitivity` wraps `_pin_signing`), so on a row
    the lock has moved, "this model won't run" names the wrong cause and sends the reader to a slot
    that changes nothing while the lock holds. The row already says what runs, one line down.

    Plan is assigned an APPROVED model first, so the lock moves the pinned turn back onto Plan's own
    model. That is the case this issue closes: the shadow still exists, but the pin did not decide."""
    _, drawn = _drawn([{"set": ["plan", "opus"]}, {
        "signing": "implement", "sensitivity": _LOCKED,
        "pinDecided": {"plan": False, "ask": False},
    }])
    assert drawn["problems"] == []
    assert "This runs opus, not gpt-5.4." in drawn["details"]


def test_a_lock_that_closes_every_model_still_takes_the_shadow_off():
    """The narrower case, and the one the reader can act on least: `_locked_slot_models` returns
    nothing at all when the approved set resolves to none, so there is no substitute model to name
    and the row draws no "so this runs X" line. The lock still outranks the pin, so the pin's
    sentence is still false — gating on the substitute rather than on the lock would have let it
    back in at exactly that moment."""
    (drawn,) = _drawn([{"signing": "implement", "sensitivity": _LOCKED_DEAD,
                      "pinDecided": {"plan": False, "ask": False}}])
    assert drawn["problems"] == []


def test_a_row_can_carry_a_verdict_and_a_capability_note_at_once():
    """The two are independent fields and this is the row that shows it (#463).

    Written in the separate pass that asks what every OTHER fixture happens to share: all of them
    serve, so `problem` is silent on all of them, and "the mark is not in `problem`" had only ever
    been shown where `problem` had nothing to say. `local-llm` is the harness's stopped alias and
    now also declares no `tools`, so a slot on it draws both sentences — which is what the component
    comment claims and what nothing held until now.
    """
    (drawn,) = _drawn([{"seed": {"plan": {"model": "local-llm"}}}])
    (problem,) = drawn["problems"]
    assert "Stopped" in problem
    (capability,) = drawn["capabilities"]
    assert "doesn't advertise tool support" in capability


def test_the_capability_note_survives_the_gate_that_eats_problem():
    """#463, and this test is the entire reason the mark is a field of its own.

    The gate above the sentence drops `problem` whenever a row is shadowed and the server says the
    pin did not decide it — the precedence comment in `service.model_assignments` records that cost
    in its own words ("pin + LOCK — the row shows NOTHING"). A pinned slot is the one most likely to
    be carrying a capability mark, so a rank inside `problem` would render as nothing on exactly the
    rows it was written for.

    Plan is put on `gemini-3.7-flash`, the one alias in the fixture that declares `chat` and not
    `tools` — which is what it declares live. `problems` loses Plan's shadow to the gate and keeps
    Ask's, whose pin did decide; `capabilities` keeps Plan's note. Two lists of one, from a state
    where the gate is provably closed over that row.
    """
    (drawn,) = _drawn([{"seed": {"plan": {"model": "gemini-3.7-flash"}},
                        "signing": "implement", "pinDecided": {"plan": False}}])
    assert drawn["problems"] == [_SHADOW_ASK]
    assert len(drawn["capabilities"]) == 1
    assert "doesn't advertise tool support" in drawn["capabilities"][0]


def test_the_gate_is_closed_over_that_row_and_not_merely_quiet():
    """The control for the test above. Same row, same model, the pin deciding this time — so the
    shadow renders and `problems` is two. Without it a gate that had stopped dropping anything would
    leave that assertion passing for the wrong reason."""
    (drawn,) = _drawn([{"seed": {"plan": {"model": "gemini-3.7-flash"}},
                        "signing": "implement"}])
    assert len(drawn["problems"]) == 2
    assert len(drawn["capabilities"]) == 1


def test_a_model_that_advertises_tools_gets_no_note():
    """Every slot on a tool-carrying alias, and the panel says nothing. A mark on every row is a
    mark on none."""
    (drawn,) = _drawn([{}])
    assert drawn["capabilities"] == []


def test_a_marked_alias_is_still_offered_and_still_pickable():
    """Mark it; do not hide it (#296). The capability list has been measured wrong in both
    directions, and a person who cannot see a model cannot report that the list is wrong about it.
    The note rides as the row's tooltip, where `problem` would have closed the row."""
    (drawn,) = _drawn([{}])
    marked = next(o for o in _row(drawn, "Plan")["options"] if o["value"] == "gemini-3.7-flash")
    assert marked["disabled"] is False
    assert "doesn't advertise tool support" in marked["title"]


def test_the_lock_leaves_a_verdict_about_a_model_that_will_not_answer_standing():
    """Only the pin's sentence goes. A model that will not answer will not answer whatever moved the
    turn, so dropping every `problem` on a moved row would lose a true one — which is the difference
    between gating on `shadowed && runs` and gating on `runs`."""
    # Two steps: a row's verdict is read off the tree drawn BEFORE the click, so the assignment has
    # to land in its own step for the panel to have re-read and reported it.
    _, drawn = _drawn([{"set": ["implement", "local-llm"]}, {"sensitivity": _LOCKED}])
    assert any("Start that endpoint" in p for p in drawn["problems"])


def test_a_save_re_reads_the_lock_because_the_assignment_is_one_of_its_inputs():
    """#285. The lock's per-slot answer applies the signing pin, and the pin's input is an
    assignment — so assigning a signing model moves every row's "so this runs X" at once. The
    drawer redrawing its rows from the save while its sentences came from a read taken before it is
    the stale half of the same defect.

    Counted rather than compared, deliberately: asserting the sentence CHANGED would need the
    fixture to re-implement `locked_runs_on`, and the panel would then be agreeing with this file
    instead of with the product.
    """
    (drawn,) = _drawn([{"set": ["implement", "opus"]}])
    assert drawn["wrote"] == [{"catalog": {"implement": "opus"}}]
    assert drawn["sensitivityReads"] == 1


# ---- a row the lock moved past an approved model (#287) ------------------------------------------

# The lock as it answers once the pin has routed a turn past an approved row: the server's answer for
# every slot is `coder`, whatever that slot holds. `plan` will hold `opus`, which IS approved, and it
# moves all the same — the pin takes the Build turn to a holder the lock bars, and the lock moves it
# on from there. The row's own model is not what moved it, which is what a gate reading only the
# row's own model cannot see.
_PIN_MOVED = _lock(model="coder", chat_model="coder",
                   slot_models={"plan": "coder", "implement": "coder", "ask": "coder"})

# The measured case, built in the harness's own terms: the signing slot holds `gpt-5.4`, which the
# lock bars, and `plan` holds an approved model of its own.
_PIN_PAST_APPROVED = [{"set": ["implement", "gpt-5.4"]}, {"set": ["plan", "opus"]},
                      {"signing": "implement", "sensitivity": _PIN_MOVED,
                       "pinDecided": {"plan": False, "ask": False}}]


def test_a_row_holding_an_approved_model_still_says_where_its_turn_went():
    """#287. The sentence was gated on the row's OWN model being unapproved, so this row — approved,
    and moved anyway — named one model, ran another, and said nothing at all. A wrong explanation
    beside a visible warning is recoverable, because the reader sees a claim and can doubt it; a
    silent substitution gives them nothing to doubt."""
    *_, drawn = _drawn(_PIN_PAST_APPROVED)
    assert _row(drawn, "Plan")["value"] == "coder"
    assert "This runs coder, not opus." in drawn["details"]


def test_one_sentence_covers_both_ways_a_row_gets_moved():
    """The old prose asserted a cause — "<model> isn't approved, so <other> runs" — and it is false
    on exactly the row the wider gate reaches: `opus` IS approved, and approving it again would
    change nothing. Asserted as a whole list, because the defect this replaces was a row drawing no
    sentence: a membership test passes just as well when a row is still silent."""
    *_, drawn = _drawn(_PIN_PAST_APPROVED)
    assert drawn["details"] == [
        "This runs coder, not opus.",        # approved, moved by the pin
        "This runs coder, not gpt-5.4.",     # barred, moved by the lock
        "This runs coder, not gpt-5.4.",
    ]

def test_the_moved_row_does_not_also_carry_the_pins_sentence():
    """The pin's sentence names the HOLDER's model as what runs, and on this row that is false: the
    holder is barred too, so the lock moved the pinned turn on again. Left standing beside the new
    line it gave the reader two adjacent sentences naming two different models as what runs, and
    nothing to tell them apart — a worse reading than the silence #287 started as.

    Asserted here and not only in the block above, because the fixture that produces the pair is the
    one whose `problems` nothing was looking at."""
    *_, drawn = _drawn(_PIN_PAST_APPROVED)
    assert drawn["problems"] == []


# The pin's other state: the holder's model is APPROVED, so the lock leaves the pin standing and the
# pin is genuinely what moved the row. Both sentences are true here, which is the reason to keep one.
_PIN_SURVIVES = [
    {"set": ["implement", "opus"]}, {"set": ["plan", "coder"]},
    {"signing": "implement", "sensitivity": _lock(
        model="opus", chat_model="opus",
        slot_models={"plan": "opus", "implement": "opus", "ask": "opus"})},
]


def test_a_row_the_pin_moved_keeps_the_holders_remedy():
    """The pin's own sentence carries the actionable cure: change the holder's model. It used to be
    dropped on this row because `runs` was also drawn, which left the correct model visible and the
    exit missing."""
    *_, drawn = _drawn(_PIN_SURVIVES)
    assert "This runs opus, not coder." in drawn["details"]
    assert drawn["problems"] == [
        ("The implement model (opus) runs every Turn in this session, so this model won't run. "
         "Change the implement model to switch."),
        ("The implement model (opus) runs every Turn in this session, so this model only runs in "
         "Chat. Change the implement model to switch."),
    ]


def test_a_pick_that_reselects_a_shadowed_rows_model_defeats_the_pin_sentence():
    """The measured #302 case. Plan holds an approved model, Implement signs, and the live pick names
    Plan's own model. The row correctly stays on that model, so the pin sentence would be false."""
    _, drawn = _drawn([{"set": ["plan", "opus"]}, {
        "signing": "implement",
        "pick": "opus",
        "pinDecided": {"plan": False, "ask": True},
        "sensitivity": _lock(
            picked=True, model="opus", chat_model="opus",
            slot_models={"plan": "opus", "implement": "opus", "ask": "opus"}),
    }])
    assert _row(drawn, "Plan")["value"] == "opus"
    assert not any("won't run" in p for p in drawn["problems"])


def test_an_answer_that_no_rule_on_this_row_explains_is_not_drawn():
    """The comparison reads two values from two reads that are not kept in step. `setAssignment`
    patches the row and notifies BEFORE the lock's re-read lands, and that read is fire-and-forget
    so a failed one leaves the last answer standing — so swapping one approved model for another
    would have snapped the select back to the PRE-save model under "This runs <old>, not <new>",
    the drawer telling the reader their save was refused.

    Same fixture as above with the pin taken off: `plan` holds an approved model, nothing has
    shadowed it, and the server's stale-looking answer is then a disagreement between two reads
    rather than a move."""
    _, drawn = _drawn([{"set": ["plan", "opus"]}, {"sensitivity": _PIN_MOVED}])
    assert _row(drawn, "Plan")["value"] == "opus"
    assert not any("not opus." in d for d in drawn["details"])


# The fallback rows — `assignments` null, so the panel builds rows from the status poll's catalog —
# carry no `shadowed` key, so `moved` falls back to `barredNow` there and the widened gate cannot
# invent a move. That is a true argument and it is the reason the path is safe, but an argument is
# not a guard. Adding `shadowed` to the fallback-row shape is a reasonable thing to want, since the
# pin's sentence needs no gateway and is already computed without one; the day somebody does, the
# two reads behind the comparison stop being comparable — `catalog` is kept current by the status
# poll, and `sensitivity` is refreshed on open, save and mode change and by nothing else. This is
# what fails then.
#
# One step, and the throw has to be in the FIRST one: a read that fails after a read that landed
# leaves the earlier answer standing, so the panel still holds real rows and never reaches this
# path. The approved set is the deployment defaults for the same reason the rows cannot be assigned
# here — with no read there is nothing to save against, so `barredNow` has to be made false by
# approving what the catalog already holds.
_STALE_FALLBACK = [{"listing": "throw", "sensitivity": _lock(
    approved=["gpt-5.4", "coder"], model="coder", chat_model="coder",
    slot_models={"plan": "coder", "implement": "gpt-5.4", "ask": "coder"})}]


def test_a_fallback_row_cannot_invent_a_move_it_was_never_told_about():
    """A row built from the catalog poll carries no verdict of its own, so a difference between it
    and the lock's per-slot answer is two reads disagreeing rather than a move. Saying "This runs
    coder, not gpt-5.4." there would name a substitution nothing on the server performed, on the one
    read that already has the least to say."""
    (drawn,) = _drawn(_STALE_FALLBACK)
    assert drawn["labels"] == ["Plan", "Implement", "Ask and Chat"], "the rows are drawn at all"
    assert [r["value"] for r in drawn["rows"]] == ["gpt-5.4", "coder", "gpt-5.4"]
    assert drawn["details"] == []


# ---- the row reads the pick (#286) -----------------------------------------------------------

# The lock with a live Build pick under it, as the server answers once `locked_runs_on` reads one:
# `opus` is picked and approved, so both Build rows run it, while the "Ask and Chat" row is answered
# as Chat and keeps its own approved model. The Ask row is assigned that model rather than left on
# the deployment default, because the default is `gpt-5.4` and a barred row moves for a reason that
# has nothing to do with the pick.
_PICKED = {
    "seed": {"ask": {"model": "coder"}},
    "pick": "opus",
    # `picked` beside the moved `slot_models`, because that is what the server sends: one
    # `control.snapshot()` decides both (#294). Without it this fixture describes a payload no
    # deployment produces, and since the row asks the SERVED flag before the browser's mirror it
    # would also send every assertion below down the legacy fallback instead of the live path.
    "sensitivity": {**_LOCK, "picked": True,
                    "slot_models": {"plan": "opus", "implement": "opus", "ask": "coder"}},
}


def test_a_live_pick_moves_the_rows_it_reaches_even_where_their_own_model_is_allowed():
    """An in-session act outranks the signing pin one layer below the lock, so a row that says what
    its mode RUNS has to read the pick. The Implement row is the one that carries this: it holds
    `coder`, which is approved and unshadowed, so nothing else on the row would open the gate — and
    before #286 it went on naming its own model while every Implement turn ran `opus`."""
    (drawn,) = _drawn([_PICKED])
    assert _row(drawn, "Plan")["value"] == "opus"
    assert _row(drawn, "Implement")["value"] == "opus"
    assert "This runs opus, not coder." in drawn["details"]


def test_the_build_pick_does_not_reach_the_row_that_answers_for_chat():
    """`_locked_slot_models` forces `chat_thread_id` on the `ask` row, so the router reads
    `chat_model` there and `picked_model` on the other two. The browser reads the same fork, and a
    single pick field for all three rows would substitute a Build pick into the one row no Build
    pick can move."""
    (drawn,) = _drawn([_PICKED])
    assert _row(drawn, "Ask and Chat")["value"] == "coder"
    # The whole list, in the order a person reads down the panel: two substituted rows and then the
    # Ask row's plain default line. A substring check would pass on the Implement row's own sentence.
    assert drawn["details"] == [
        "This runs opus, not gpt-5.4.", "This runs opus, not coder.", "Default is gpt-5.4.",
    ]


def test_a_chat_pick_moves_that_row_and_leaves_the_build_rows_alone():
    """The other side of the same fork, and the one that shows it is a fork rather than an exclusion:
    the Chat pick moves the row the Build pick could not, and the Implement row — approved, holding
    its own model, no pick of its own — stays on the way back to the default."""
    (drawn,) = _drawn([{
        "seed": {"ask": {"model": "coder"}},
        "chatPick": "opus",
        "sensitivity": _lock(chat_picked=True,
                             slot_models={"plan": "opus", "implement": "coder", "ask": "opus"}),
    }])
    assert _row(drawn, "Ask and Chat")["value"] == "opus"
    assert _row(drawn, "Implement")["value"] == "__default__"


def test_the_drawer_is_masked_because_nothing_re_reads_these_rows_behind_it():
    """Load-bearing since #286 and asserted rather than left to antd's default. The rows read the
    pick and `store.js` keeps the pick out of the re-read list, so they are only right for as long
    as nobody can reach the picker — which is what the mask, and only the mask, provides. See #294
    for the one mover the mask cannot block."""
    (drawn,) = _drawn([{"sensitivity": _lock()}])
    assert drawn["drawerMask"] is True


# ---- the effort beside the model (ADR-0049, #283) --------------------------------------------
#
# The row's second control. It is the drawer's and not the Build menu's because an effort is half an
# assignment: a cost-and-quality decision about the Project's work, shared and persisted, where the
# Build menu's override dies with the Builder. The menu's own submenu is #295 and waits on the
# plumbing that carries a picked effort to a turn (#282).


def test_a_row_whose_model_takes_a_level_offers_one_and_names_the_way_back():
    """Two controls on the row, and the second one's first option carries no level of its own —
    picking it CLEARS the effort rather than setting one, the same difference the model select's
    "Use the default" draws one line up."""
    (drawn,) = _drawn([{}])
    plan = _effort_row(drawn, "Plan")
    assert plan is not None
    assert plan["value"] == "__model_default__"
    assert plan["options"][0] == {"value": "__model_default__", "label": "Model default",
                                  "disabled": False, "title": None}


def test_a_model_that_throws_the_field_away_gets_no_control_at_all():
    """Five of the eight Aliases on the gateway discard `reasoning_effort` in silence, so a 200 from
    one of them means "thrown away" and not "accepted" (#280). A control that appears where the
    setting changes nothing is worse than no control, and this is the same `efforts.length > 0` rule
    the Chat picker already applies rather than a second one written here."""
    (drawn,) = _drawn([{}])
    # `implement` holds `coder`, measured as discarding the field.
    assert _effort_row(drawn, "Implement") is None
    assert _effort_row(drawn, "Plan") is not None


def test_the_levels_offered_are_the_ones_that_row_s_model_accepts():
    """Per alias, never a union. A person offered `max` because some other Alias takes it would get
    a hard 400 from the one their row is actually on — which is the whole reason the measured table
    is keyed by alias rather than being one set of legal values (ADR-0049)."""
    (_, drawn) = _drawn([{"set": ["plan", "gemini-3.7-flash"]}, {}])
    assert [o["value"] for o in _effort_row(drawn, "Plan")["options"]] == [
        "__model_default__", "low", "medium", "high", "max"]
    # The same panel, one row down, offering a different set off a different model.
    assert [o["value"] for o in _effort_row(drawn, "Ask and Chat")["options"]] == [
        "__model_default__", "none"]
    # `minimal` is advertised by the gateway and 400s at Vertex, so the narrowing takes it off
    # before the panel ever sees it — asserted because publishing the enum verbatim is the one
    # mistake this list is drawn to avoid.
    assert "minimal" not in [o["value"] for o in _effort_row(drawn, "Plan")["options"]]


def test_setting_a_level_writes_the_level_and_leaves_the_model_alone():
    """Its own call carrying its own key. `set_catalog` reads an ABSENT key as "leave it", so this
    is what stops the two controls on one row clobbering each other — the same argument that keeps
    the panel from posting all three rows whenever one changes."""
    (drawn,) = _drawn([{"setEffort": ["plan", "none"]}])
    assert drawn["wrote"] == [{"catalog": {"plan": {"effort": "none"}}}]
    assert drawn["afterEffort"] == "none"
    # The model half untouched, and still following the default it was following.
    assert drawn["after"] == "__default__"


def test_the_way_back_clears_the_level_rather_than_setting_one():
    """`null`, not the level that happens to be the alias's own default. A setting that could never
    be undone is the defect the model's own "Use the default" row exists to prevent, and an effort
    saved beside it is no different."""
    (_, drawn) = _drawn([{"setEffort": ["plan", "high"]},
                         {"setEffort": ["plan", "__model_default__"]}])
    assert drawn["wrote"] == [{"catalog": {"plan": {"effort": None}}}]
    assert drawn["afterEffort"] == "__model_default__"


def test_setting_a_level_does_not_re_read_the_sensitivity_lock():
    """The model half does, because `locked_runs_on` resolves MODELS and an assignment moves every
    row's answer at once (#285). No level is an input to any of it — not the approved set, not the
    administrator's ordering, not the signing pin — so a read here would be a gateway listing per
    level picked, to be told the same thing back."""
    (model_save,) = _drawn([{"set": ["implement", "opus"]}])
    (effort_save,) = _drawn([{"setEffort": ["plan", "high"]}])
    assert model_save["sensitivityReads"] == 1
    assert effort_save["sensitivityReads"] == 0


# ---- the level a model change took with it ---------------------------------------------------
#
# `_merge_assignment` DROPS the level rather than refusing the save, because refusing would make a
# slot carrying one impossible to retarget. The drop is visible on the re-read — the control goes
# back to its default — and until this ticket it said nothing about itself, which is a row that
# moved and gave no reason (#287) in its weaker form.

_RETARGET = [{"set": ["plan", "gemini-3.7-flash"]}, {"setEffort": ["plan", "max"]}]


def test_a_level_the_new_model_refuses_is_cleared_and_the_row_says_why():
    *_, drawn = _drawn([*_RETARGET, {"set": ["plan", "gpt-5.4"]}])
    assert drawn["afterEffort"] == "__model_default__"
    assert drawn["afterEffortNotes"] == [
        'gpt-5.4 doesn\'t accept "Max", so the reasoning effort was cleared.']


def test_the_row_says_why_even_when_the_control_itself_is_gone():
    """The case it matters in most. A model that accepts no level at all takes the control off the
    row at the same moment it takes the setting, so a sentence drawn only beside a surviving control
    would be silent exactly where the reader has the least to go on."""
    *_, drawn = _drawn([*_RETARGET, {"set": ["plan", "coder"]}])
    assert drawn["afterEffort"] is None, "the control should be gone, not defaulted"
    assert drawn["afterEffortNotes"] == [
        'coder doesn\'t accept "Max", so the reasoning effort was cleared.']


def test_saving_the_same_model_keeps_its_supported_effort_in_silence():
    """The sentence is about a drop. Drawn on a row that kept its level it
    would be a warning about something that did not happen."""
    *_, drawn = _drawn([{"set": ["plan", "gemini-3.7-flash"]},
                        {"setEffort": ["plan", "high"]},
                        {"set": ["plan", "gemini-3.7-flash"]}])
    assert drawn["afterEffort"] == "high"
    assert drawn["afterEffortNotes"] == []


def test_taking_the_assignment_back_does_not_claim_a_model_refused_anything():
    """Clearing the model clears the whole entry, both halves, and no model was asked about either
    one. The fixture is the case that proves the exclusion is needed rather than tidy: the default
    this row falls back to DOES accept `high`, so a note here would name a refusal that never
    happened and could not happen."""
    *_, drawn = _drawn([{"set": ["plan", "gemini-3.7-flash"]},
                        {"setEffort": ["plan", "high"]},
                        {"set": ["plan", "__default__"]}])
    assert drawn["afterEffort"] == "__model_default__"
    assert drawn["afterEffortNotes"] == []


def test_a_person_clearing_the_level_themselves_is_not_told_a_model_refused_it():
    """Why the two controls save on separate calls rather than one PUT of the whole row: the note is
    written from the MODEL call, so an effort call can never produce it. Folded into one call, a
    person picking "Model default" would be handed an explanation for their own act."""
    *_, drawn = _drawn([{"setEffort": ["plan", "high"]},
                        {"setEffort": ["plan", "__model_default__"]}])
    assert drawn["afterEffortNotes"] == []


def test_a_level_the_row_s_own_model_refuses_is_refused_rather_than_dropped():
    """The other half of the contract, and the reason the drop is safe to explain: when the call
    ASKED for the level, `_merge_assignment` answers 400 and nothing is written. So a level that is
    gone after a model change has been through the drop and nothing else, which is what lets the
    sentence above name a cause without a server field to read it from."""
    *_, drawn = _drawn([{"set": ["plan", "gemini-3.7-flash"]},
                        {"setEffort": ["plan", "max"]},
                        {"set": ["plan", "gpt-5.4"]},
                        {"setEffort": ["plan", "max"]}])
    # Refused: the row still holds no level, and no note claims one was dropped.
    assert drawn["afterEffort"] == "__model_default__"
    assert drawn["afterEffortNotes"] == []


def test_a_re_read_that_never_landed_claims_no_drop():
    """The patched row predicts the same clearing so a failed reload does not leave a level showing
    that is already off disk — but a SENTENCE drawn off that prediction would be this surface
    claiming a refusal it was never told about, on the one read with the least to say."""
    *_, drawn = _drawn([*_RETARGET, {"set": ["plan", "coder"], "failReload": True}])
    assert drawn["afterEffortNotes"] == []


# ---- the level beside the three sentences that were already there ----------------------------
#
# The row carries three verdicts and a written order between them (#287, ADR-0043). All three answer
# ONE question — what will this slot's turn run — which is why they can contradict each other and
# why the lock outranks the pin among them. The effort note answers a different question: what was
# just written to disk. So it joins no precedence, and these are the assertions that say so.


def test_the_effort_control_adds_no_sentence_to_the_three_about_what_runs():
    *_, drawn = _drawn(_PIN_PAST_APPROVED)
    assert drawn["details"] == [
        "This runs coder, not opus.",
        "This runs coder, not gpt-5.4.",
        "This runs coder, not gpt-5.4.",
    ]
    assert drawn["problems"] == []
    assert drawn["effortNotes"] == []


def test_a_dropped_level_and_a_model_that_will_not_answer_are_both_said():
    """Neither displaces the other, because they are about different things: one says this model
    will fail the next build, the other says the level you set is gone. Dropping either would be
    this row going quiet about something true to keep its neighbour company."""
    *_, drawn = _drawn([*_RETARGET, {"set": ["plan", "local-llm"]}])
    assert any("Start that endpoint" in p for p in drawn["afterProblems"])
    assert drawn["afterEffortNotes"] == [
        'local-llm doesn\'t accept "Max", so the reasoning effort was cleared.']


def test_a_locked_row_offers_the_levels_of_the_model_it_would_save():
    """The select above substitutes because the row exists to say what this mode RUNS. This one does
    not, and must not: `_merge_assignment` validates a level against the model the ASSIGNMENT holds,
    so offering the substitute's levels would offer levels this row cannot save. The lock moves what
    runs; it does not move what is being edited here."""
    (drawn,) = _drawn([{"sensitivity": _LOCK}])
    # `plan` holds `gpt-5.4` and the lock runs it on `opus`, which takes no level at all.
    assert _row(drawn, "Plan")["value"] == "opus"
    assert [o["value"] for o in _effort_row(drawn, "Plan")["options"]] == [
        "__model_default__", "none"]


def test_a_shadowed_row_says_what_runs_and_what_was_saved_without_contradiction():
    """The precedence question, asked of the pin's sentence rather than the lock's, because the pin
    is the one that can stand beside a drop. Both are true and neither weakens the other: the pin
    names the model every Turn in this session runs, and the note names the level this save took off
    disk. Written as an assertion rather than left to the comment, because the last time a sentence
    was added to this row it was true in isolation and wrong beside its neighbour (#287)."""
    *_, drawn = _drawn([*_RETARGET, {"set": ["plan", "gpt-5.4"], "signing": "implement"}])
    assert any("runs every Turn in this session" in p for p in drawn["afterProblems"])
    assert drawn["afterEffortNotes"] == [
        'gpt-5.4 doesn\'t accept "Max", so the reasoning effort was cleared.']
    # And no fourth claim about what runs, which is the shape the contradiction would take.
    assert not any("runs" in d for d in drawn["afterDetails"])


def test_one_row_s_save_does_not_wipe_another_row_s_note():
    """The note is keyed to the row it is about, so clearing it has to be too. Cleared wholesale, a
    person who retargets Plan and then sets Implement's level has just lost the only account of why
    Plan's level went — the silence this surface exists to close, put back by an unrelated click."""
    *_, drawn = _drawn([*_RETARGET, {"set": ["plan", "gpt-5.4"],
                                     "also": {"setEffort": ["ask", "high"]}}])
    assert drawn["afterEffortNotes"] == [
        'gpt-5.4 doesn\'t accept "Max", so the reasoning effort was cleared.']


def test_a_refused_save_takes_the_note_down_with_it():
    """Every other exit from a save writes a verdict; this one used to write none. A note left
    standing under a REFUSED save reads as that save's account — the row explaining a clearing
    beside a toast saying nothing was saved at all."""
    *_, drawn = _drawn([*_RETARGET, {"set": ["plan", "gpt-5.4"],
                                     "also": {"set": ["plan", "opus"], "failSave": True}}])
    assert drawn["afterEffortNotes"] == []


# ---- a level the row's model stopped accepting ------------------------------------------------
#
# Reachable with nobody having done anything wrong, and `service._effective_catalog` names both
# ways: the deployment default can move under a stored level long after it was saved, and the
# measured table can narrow under one when an alias is probed (#280). Nothing re-validates either.

_STRANDED = [{"seed": {"plan": {"model": "coder", "effort": "high"}}}]


def test_a_level_the_model_no_longer_accepts_can_still_be_seen_and_taken_off():
    """Otherwise the setting is invisible, still on disk, still sent — and the only way to clear it
    is to give up the row's model assignment as well. The "draw nothing where the model offers none"
    rule is about a control that would change nothing; this one clears something real."""
    (drawn,) = _drawn(_STRANDED)
    control = _effort_row(drawn, "Plan")
    assert control is not None, "the control was removed while the level was still saved"
    assert control["value"] == "high"
    # Offered so the select can label the value it is showing — without an option antd draws the raw
    # key — and closed, because it is not a thing that can be chosen again.
    stranded = next(o for o in control["options"] if o["value"] == "high")
    assert stranded["disabled"] is True
    assert stranded["label"] == "High — not accepted"
    assert "doesn't accept this level" in stranded["title"]


def test_the_way_back_is_still_open_on_a_stranded_level():
    """The point of drawing the control at all. A row whose only option was the level it cannot use
    would be a dead end with an explanation on it."""
    *_, drawn = _drawn([*_STRANDED, {"setEffort": ["plan", "__model_default__"]}])
    assert drawn["wrote"] == [{"catalog": {"plan": {"effort": None}}}]
    assert drawn["afterEffort"] is None, "the control goes once the level it was drawn for is gone"


def test_a_save_that_dropped_nothing_leaves_another_row_s_note_alone():
    """The commonest save there is, and the one the note has to survive. Every clear on this surface
    is keyed to the row it is about; unguarded, a routine model change two rows down would erase the
    only account of why the level above it went — which is the silence, put back by a click that had
    nothing to do with it."""
    *_, drawn = _drawn([*_RETARGET, {"set": ["plan", "gpt-5.4"],
                                     "also": {"set": ["implement", "opus"]}}])
    assert drawn["afterEffortNotes"] == [
        'gpt-5.4 doesn\'t accept "Max", so the reasoning effort was cleared.']


def test_a_stranded_level_beside_levels_the_model_does_offer():
    """The partial case, and the shape narrowing the offer produces rather than the rare one. When a
    list shrinks under a saved level — #282 narrows gpt-5.4 to the one level a tool-carrying turn can
    run — the row holds a level that is missing from a list that is otherwise full. Without the
    closed option the select has no label for the value it is showing and draws the raw key."""
    (drawn,) = _drawn([{"seed": {"plan": {"model": "gpt-5.4", "effort": "max"}}}])
    control = _effort_row(drawn, "Plan")
    assert control["value"] == "max"
    assert control["options"][-1]["label"] == "Max — not accepted"
    # The levels it does offer are all still there, and so is the way back.
    assert [o["value"] for o in control["options"][:-1]] == [
        "__model_default__", "none"]


# ---- a row the file could not hand over (#289) ---------------------------------------------


def test_a_dropped_row_draws_its_sentence_where_an_unassigned_row_draws_none():
    """Both states through the SAME row, rendered, because the fault is that they look identical.
    A slot whose committed row is malformed follows the deployment default, which is pixel-for-pixel
    what a slot nobody assigned does — so a pair of tests each asserting one of these would both
    pass with the sentence never drawn at all.

    Rendered rather than read off the gate at `model-assignments.js:288`: that gate drops a
    `problem` it believes is the pin's, and reading the condition says it spares this one. Drawing
    it is what proves it."""
    clean, dropped = _drawn([{}, {"unreadable": ["plan"]}])
    assert clean["problems"] == []
    assert dropped["problems"] == [
        ("The plan row in .sage/model_overrides.json couldn't be read, so this slot is following "
         "the default. Fix that row, or remove it."),
    ]
    # Still the default in the closed Select, and still offering the way back to it by name: the
    # sentence is the only thing that changes. A row that had also moved its value would be the
    # panel claiming an assignment the catalog does not hold.
    assert _row(dropped, "Plan")["value"] == "__default__"


def test_the_pin_still_takes_the_row_from_a_dropped_one():
    """The ranking, drawn. Under the shadow this row shows only the pin's sentence — the cost the
    server's precedence comment writes down — and the moment the pin is gone the row's own sentence
    is back. Two steps, because one asserting either half alone cannot tell an order from a gate
    that ate both."""
    shadowed, released = _drawn([
        {"unreadable": ["ask"], "signing": "implement"},
        {"unreadable": ["ask"]},
    ])
    assert not any("couldn't be read" in p for p in shadowed["problems"])
    assert any("couldn't be read" in p for p in released["problems"])
    # The shadow's own sentence IS drawn here, which is what the ranking traded the typo for. The
    # test below is the case where that stops being true and nothing is drawn at all.
    assert any("runs every Turn in this session" in p for p in shadowed["problems"])


def test_a_pinned_and_locked_row_keeps_the_pin_sentence_when_the_pin_decided():
    """The field added by #302 closes this corner. The row still ranks the shadow over the unreadable
    sentence, but the drawer no longer drops that shadow merely because a lock also holds. It asks
    whether the server says the pin actually decided."""
    pinned_and_locked, locked_only = _drawn([
        {"unreadable": ["ask"], "signing": "implement", "sensitivity": _lock()},
        {"unreadable": ["ask"], "sensitivity": _lock()},
    ])
    assert any("only runs in Chat" in p for p in pinned_and_locked["problems"])
    assert any("couldn't be read" in p for p in locked_only["problems"])


# ---- the line under an assigned row (#299) ---------------------------------------------------------


def test_a_row_assigned_to_the_model_that_is_also_the_default_says_which_it_is():
    """The one state the select cannot show. `plan` holds `gpt-5.4` and the deployment default IS
    `gpt-5.4`, so a closed select naming it is true of a row that was pinned and of a row that was
    never touched — and the two behave differently the day the default moves. Naming the default
    here ("Default is gpt-5.4.") answers a question nobody asked and leaves the reader unable to
    tell the two apart, so the sentence names the assignment and what it costs instead."""
    (drawn,) = _drawn([{"seed": {"plan": {"model": "gpt-5.4"}}}])
    assert _row(drawn, "Plan")["value"] == "gpt-5.4"
    assert drawn["details"] == [
        ("Assigned to gpt-5.4, which is also the current default. "
         "This slot stays on it if the default changes."),
    ]


def test_a_row_assigned_away_from_the_default_still_names_what_it_left():
    """The other side of the same fork, asserted beside it rather than left to the pick tests: where
    the two differ the select already shows the assignment, so the thing the row cannot show is what
    it went back to — and that sentence is unchanged."""
    (drawn,) = _drawn([{"seed": {"implement": {"model": "gpt-5.4"}}}])
    assert _row(drawn, "Implement")["value"] == "gpt-5.4"
    assert drawn["details"] == ["Default is coder."]


def test_a_row_whose_file_moved_under_a_stale_catalog_names_the_file_model_and_the_gap():
    """The skew the server cannot rule out, so the row has to. `assigned` is a fresh read of
    `model_overrides.json` while `model` is the shim catalog, which is rebuilt at boot and on save
    and not when the file moves underneath it — so a committed file arriving in an open Builder
    assigns `coder` while the catalog still holds `gpt-5.4`. Reading `current.model` made the select
    name `gpt-5.4`, which is the model the next turn will not run. Silently switching the select
    would leave the row unexplained, so the select shows the file model and the line names the stale
    catalog. The level control also cannot keep offering levels for `gpt-5.4` under a select that
    now reads `coder`."""
    (drawn,) = _drawn([{"seed": {"plan": {"model": "coder"}}, "stale": {"plan": "gpt-5.4"}}])
    assert _row(drawn, "Plan")["value"] == "coder"
    assert drawn["details"] == [
        "Saved as coder. This Builder still has old details for gpt-5.4."]
    assert _effort_row(drawn, "Plan") is None


def test_the_re_read_that_closes_the_gap_gets_the_sentence_back():
    """The control on the test above, and the reason it is not just a way of never drawing the line:
    the same two steps, with the catalog caught up, and the row says what it holds. Without this a
    sentence that had simply stopped being drawn would pass the test beside it."""
    _, caught_up = _drawn([
        {"seed": {"plan": {"model": "gpt-5.4"}}, "stale": {"plan": "coder"}},
        {"seed": {"plan": {"model": "gpt-5.4"}}},
    ])
    assert _row(caught_up, "Plan")["value"] == "gpt-5.4"
    assert caught_up["details"] == [
        ("Assigned to gpt-5.4, which is also the current default. "
         "This slot stays on it if the default changes."),
    ]


def test_a_slot_with_no_default_at_all_claims_nothing_rather_than_naming_nothing():
    """Holding the neighbour still. The pin sentence is drawn on three names agreeing, and the only
    reason three ABSENT names cannot agree their way into "Assigned to undefined, which is also the
    current default" is a separate conjunct one line up — the gate already requires a default. That
    is a rule holding for a reason stated somewhere else, which is exactly what stops holding when
    someone widens the other conjunct (#287). No deployment sends a slot without a default today, so
    this is the guard and not the report of a live fault."""
    (drawn,) = _drawn([{"seed": {"plan": {"model": "gpt-5.4"}}, "noDefault": ["plan"]}])
    assert drawn["details"] == []
