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
    assert all(r["disabled"] for r in drawn["rows"])
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
    assert all(r["disabled"] for r in drawn["rows"])
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
    assert all(r["disabled"] for r in drawn["rows"])


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
    assert all(r["disabled"] for r in drawn["rows"])


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
