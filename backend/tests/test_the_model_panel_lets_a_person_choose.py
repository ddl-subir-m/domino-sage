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
    assert len(drawn["rows"]) == 3


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
    assert "Wait for the turn to finish" in alert["description"]


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
    assert "administers the LLM Gateway" in alert["description"]
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
    assert not any("coder isn't approved" in d for d in drawn["details"])


def test_a_substituted_row_says_what_it_would_have_run():
    """Without it the panel simply shows a model nobody chose, and the person who set the slot reads
    the row as having lost their assignment."""
    (drawn,) = _drawn([{"sensitivity": _lock()}])
    assert "gpt-5.4 isn't approved, so this runs opus." in drawn["details"]


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
           " Change the implement model to release the session.")
# The `ask` row's own sentence: the pin does not reach Chat, so that slot still drives a model.
_SHADOW_ASK = ("The implement model (coder) runs every Turn in this session, so this model only"
               " runs in Chat. Change the implement model to release the session.")
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

    Plan is assigned an APPROVED model first, so it is the row the lock has NOT moved — the shadow
    has to survive there, or this would pass by suppressing the sentence everywhere."""
    _, drawn = _drawn([{"set": ["plan", "opus"]}, {"signing": "implement", "sensitivity": _LOCKED}])
    assert drawn["problems"] == [_SHADOW]
    assert "gpt-5.4 isn't approved, so this runs opus." in drawn["details"]


def test_a_lock_that_closes_every_model_still_takes_the_shadow_off():
    """The narrower case, and the one the reader can act on least: `_locked_slot_models` returns
    nothing at all when the approved set resolves to none, so there is no substitute model to name
    and the row draws no "so this runs X" line. The lock still outranks the pin, so the pin's
    sentence is still false — gating on the substitute rather than on the lock would have let it
    back in at exactly that moment."""
    (drawn,) = _drawn([{"signing": "implement", "sensitivity": _LOCKED_DEAD}])
    assert drawn["problems"] == []


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
