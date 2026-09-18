"""The broad question is asked first, and the narrow one only if the broad one is refused (#392).

Two gates read the same sentence and hold opposite premises about it. `_chat_table_offer` asks
*which one table*, `_chat_investigation_offer` asks *shall I look across several* — and the table
gate fired first, so a question whose whole nature is that it spans several sources was first put
to the person as a single select. Measured live twice: the pick was then ignored, and the turn
queried all three sources anyway.

ADR-0059 funnels them the other way round. The table gate is not moved below the classifier, which
is the ordering constraint at `:10570` and still holds — the classifier is FORCED, lazily, behind a
free predicate, so a prompt that does not look investigative reaches its table card with no model
call in front of it, exactly as today.

WHAT THE TESTS BELOW ARE CAREFUL TO ASSERT IS THE COST AS WELL AS THE ORDER. Four of the five
conditions are free; only the fifth is a model call, and the tests that end at the table card assert
that no `chat-intent` request was made at all. An implementation that forced the classifier
unconditionally would pass every ordering assertion here and still be the change the ADR refused.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, _orch

# Names the store in prose, so the table gate's four conditions all hold, AND fuses three sources,
# so `_looks_investigative` holds. The sentence that opened the ticket, near enough verbatim.
INVESTIGATIVE = ("Investigate which customers actively use Model Monitor, across Mixpanel, Gong "
                 "and Salesforce in Snowflake-Data-Warehouse")
# Names the same store and asks one thing of it. Nothing here fuses, doubts or investigates.
ORDINARY = "chart me daily gong calls from Snowflake-Data-Warehouse"


def _thread(orch) -> str:
    """A Thread bound to the fake warehouse, whose tree the table gate can walk."""
    tid = orch.create_thread()["id"]
    # What the composer posts for a bare Data Source chip: no scope, so the table gate has an
    # unscoped store to walk and every one of its four conditions holds.
    orch.add_thread_context(tid, {"kind": "data_source", "name": "Snowflake-Data-Warehouse",
                                  "resourceId": "data_source:ds-dwh"})
    return tid


def _classifier_calls(gateway: IntentGateway) -> int:
    return sum(1 for _, labels in gateway.seen
               if getattr(labels, "component", "") == "chat-intent")


def _kinds(events: list[dict]) -> list[str]:
    return [e.get("type") for e in events]


def test_the_investigative_question_is_asked_the_broad_one_first(tmp_path: Path):
    """The investigation card, on the turn that used to draw the table card.

    Not merely both cards in the other order: the table card is not drawn on this turn at all. A
    single select in front of a question the next card describes as spanning several sources is the
    contradiction, and drawing it second would only move it.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.93})
    orch, oc = _orch(tmp_path, [Turn(text="answered anyway")], gateway=gw)
    tid = _thread(orch)

    events = list(orch.chat_stream(tid, INVESTIGATIVE))

    assert "investigation-offer" in _kinds(events)
    assert "table-candidates" not in _kinds(events)
    done = next(e for e in events if e.get("type") == "done")
    assert done["ok"] is False and done["decision"] == "investigation offer"
    assert oc.prompts == [], "it asked instead of answering"


def test_an_ordinary_question_still_gets_its_table_card_and_pays_no_classifier(tmp_path: Path):
    """The population the ordering constraint at `:10570` was written about, unchanged.

    This is the cost half, and it is the half an implementation is most likely to lose: forcing the
    classifier above the table gate for every turn would satisfy every ordering assertion in this
    file and put a model call in front of almost every table card ever drawn.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.93})
    orch, _ = _orch(tmp_path, [Turn(text="answered")], gateway=gw)
    tid = _thread(orch)

    events = list(orch.chat_stream(tid, ORDINARY))

    assert "table-candidates" in _kinds(events)
    assert "investigation-offer" not in _kinds(events)
    assert _classifier_calls(gw) == 0


def test_a_low_confidence_investigative_question_falls_back_to_the_table_card(tmp_path: Path):
    """I2 fails, so the turn is exactly what it is today — one card, the narrow one.

    This is the reported case until #401 lands: the live prompt classified 0.60 against
    `MIN_CONFIDENCE = 0.65`. The funnel must degrade to today rather than to nothing.

    And it costs this population ONE call that today's turn does not make, asserted here rather
    than left to be discovered: this leg ends at the table card, so the classifier it forced is not
    one the turn was going to make later anyway. ADR-0059 records the same and calls the decision
    inert until #401.

    THIS TEST PINS PRE-#401 BEHAVIOUR AND #401 IS EXPECTED TO FLIP IT. Admitting a low-confidence
    turn to the widening gate draws the investigation card at 0.60, which reds the second assertion
    here and then the first. That red belongs to #401 and is updated as part of it — it is not a
    foreign red to be investigated, and the four checks in CLAUDE.md cannot tell the difference:
    they would report it deterministic and not yours, which would be the wrong verdict.

    DO NOT GO LOOKING FOR A CHANGED `MIN_CONFIDENCE`. As planned, #401 leaves the threshold at 0.65
    and splits the field instead: the widening gate stops reading `intent.valid` and reads the
    label plus a named fallback set, while the narrowing gate at `bounded_intent` keeps
    `intent.valid` unchanged. `_parse` keeps the label and sets `fallback="low-confidence"` below
    the threshold (measured), so the label is there to read. Crossing 0.65 instead would flip
    `bounded_intent` True and route the question onto the read-only lane that cannot answer it,
    which is the #408 dependency — so the number this test watches moves without the threshold
    moving.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.60})
    orch, _ = _orch(tmp_path, [Turn(text="answered")], gateway=gw)
    tid = _thread(orch)

    events = list(orch.chat_stream(tid, INVESTIGATIVE))

    assert "table-candidates" in _kinds(events)
    assert "investigation-offer" not in _kinds(events)
    assert _classifier_calls(gw) == 1


def test_a_declined_thread_pays_for_no_classifier_call(tmp_path: Path):
    """I1 is free and is checked first, so the Thread that already said no never buys a call.

    And the replay lands on the table card: `answerInvestigationAndAsk` does not set
    `skipTableGate`, so the turn that skipped the table gate and then had its investigation declined
    comes back with the gate skipped and the narrow question asked at the moment it makes sense.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.93})
    orch, _ = _orch(tmp_path, [Turn(text="answered")], gateway=gw)
    tid = _thread(orch)
    orch.decide_thread_investigation(tid, "decline")

    events = list(orch.chat_stream(tid, INVESTIGATIVE))

    assert "table-candidates" in _kinds(events)
    assert "investigation-offer" not in _kinds(events)
    assert _classifier_calls(gw) == 0


def test_the_replay_of_an_answered_card_is_not_offered_the_same_card(tmp_path: Path):
    """`skip_investigation_gate` binds the early offer too, not only the gate it was written for.

    It is the flag the click sends back, and the funnel now reads the same sentence one gate
    earlier. Without it here, the card that was just answered would be the first thing the replay
    met — the state write and this flag agree today, and this is the half that does not depend on
    that.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.93})
    orch, _ = _orch(tmp_path, [Turn(text="answered")], gateway=gw)
    tid = _thread(orch)

    events = list(orch.chat_stream(tid, INVESTIGATIVE, skip_investigation_gate=True))

    assert "investigation-offer" not in _kinds(events)
    assert "table-candidates" in _kinds(events)


def test_accepting_the_investigation_is_not_then_asked_to_pick_one_table(tmp_path: Path):
    """The narrow question is asked only if the broad one was REFUSED, which is the whole decision.

    The accept replay carries `skip_investigation_gate` and NOT `skipTableGate`
    (`answerInvestigationAndAsk`, `store.js:6737`), and under the funnel the table gate has never
    run by then — so without this the person who just said "look across several" is handed a single
    select, and the contradiction #392 is about is restored in the other order.

    Nothing is lost by skipping it. An open investigation keeps its shell and reaches the warehouse
    through `DataSourceClient(...).query(sql)`, which is per Data Source; the pick is what hands a
    BOUNDED turn its `database.schema` and column names (ADR-0059), and this turn is not one.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.93})
    orch, _ = _orch(tmp_path, [Turn(text="answered")], gateway=gw)
    tid = _thread(orch)
    orch.decide_thread_investigation(tid, "open")

    events = list(orch.chat_stream(tid, INVESTIGATIVE, skip_investigation_gate=True))

    assert "table-candidates" not in _kinds(events)
    assert "investigation-offer" not in _kinds(events)


def test_a_dismissed_dataset_stays_dismissed_when_the_card_returns_first(tmp_path: Path):
    """The dismissal is a record, and the funnel's `return` must not jump over it.

    "Ask without attaching" replays with `datasetDismissed` and no `investigationAnswered`, so the
    investigation card can now be the thing that ends that turn. The answer to it replays with
    neither flag — so a dismissal lost here comes back as the Dataset card the person just
    dismissed, asked again.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.93})
    orch, _ = _orch(tmp_path, [Turn(text="answered")], gateway=gw)
    tid = _thread(orch)

    events = list(orch.chat_stream(tid, INVESTIGATIVE, skip_dataset_gate=True,
                                   dismissed_dataset="ds-42"))

    assert "investigation-offer" in _kinds(events), "the funnel returned before the dataset gate"
    assert (tid, "ds-42") in orch._dataset_dismissed


def test_a_build_request_that_fuses_sources_is_not_offered_the_investigation(tmp_path: Path):
    """The guard. `_FUSES_SOURCES` matches "cross-references", so one sentence is both shapes.

    The early offer sits above `_explicit_handoff`, so without this the build request would be
    offered a grant that only applies to Chat turns, and #389's decline is one-way. Checked against
    the pure predicate rather than by calling `_explicit_handoff`, which writes.

    What this turn does instead is draw the table card and stop — it does not reach the handoff
    short-circuit on this leg, and the assertion says only what it sees.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.93})
    orch, _ = _orch(tmp_path, [Turn(text="built")], gateway=gw)
    tid = _thread(orch)

    prompt = ("build me a dashboard that cross-references Gong and Salesforce in "
              "Snowflake-Data-Warehouse")
    events = list(orch.chat_stream(tid, prompt))

    assert "investigation-offer" not in _kinds(events)


@pytest.mark.parametrize("prompt,expected", [
    (INVESTIGATIVE, "investigation-offer"),
    (ORDINARY, "table-candidates"),
])
def test_the_card_that_is_drawn_says_what_its_click_actually_buys(tmp_path: Path, prompt: str,
                                                                 expected: str):
    """Neither card may promise a fence, and neither may promise the answer follows.

    The table pick records a position — a `database.schema` for bare names to resolve in, and the
    chosen table's column names in the prompt — and does not choose which table gets read
    (ADR-0059). "will then answer your question" is a promise #407 and #408 currently break.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.93})
    orch, _ = _orch(tmp_path, [Turn(text="answered")], gateway=gw)
    tid = _thread(orch)

    events = list(orch.chat_stream(tid, prompt))
    card = next(e for e in events if e.get("type") == expected)

    assert "will then answer your question" not in card["message"]
    if expected == "table-candidates":
        assert "to start from" in card["message"]
    else:
        # "may first ask", not "will": the second card is not certain from either call site.
        assert "may first ask where to start reading" in card["message"]
