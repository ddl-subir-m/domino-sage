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


def test_the_reported_question_is_asked_the_broad_one_first_though_nobody_was_sure(tmp_path: Path):
    """The prompt that opened #392, end to end, at the confidence it actually scored.

    This is the case ADR-0059 recorded as the one its own decision did NOT fix: the live sentence
    classified 0.60 against `MIN_CONFIDENCE = 0.65`, I2 failed, and the funnel degraded to the
    table card plus a classifier call the turn had not been making. #401 armed it by splitting the
    field — `_chat_investigation_offer` reads `intent.usable_label`, which admits `fallback` in
    `("", "low-confidence")`, while `bounded_intent` and the three arming sites go on reading
    `intent.valid`. `MIN_CONFIDENCE` is untouched at 0.65, and a reader who goes looking for a
    moved constant will not find one: crossing it would flip `bounded_intent` True and route this
    question onto the read-only lane that cannot answer it (#407, #408).

    WHAT THIS PINS THAT #401'S OWN FILE CANNOT. `test_the_offer_gate_stops_asking_for_certainty`
    binds its Thread with `{"id": "ds1"}` and no `resourceId`, so `binding_from_context` returns
    None and its table gate can never fire — it proves the gate change with nothing to be ordered
    against. This Thread carries a real unscoped store whose tree the walk reaches, so all four
    table conditions hold and the two gates genuinely compete. Only here can the reported sentence
    show that the widening question now comes first AND the single select is not also drawn.

    The call is no longer spent for nothing: this leg ends at the card the person is owed, so it is
    the call leg 2 was going to make anyway, moved forward.
    """
    gw = IntentGateway({"label": "data_answer", "confidence": 0.60})
    orch, _ = _orch(tmp_path, [Turn(text="answered")], gateway=gw)
    tid = _thread(orch)

    events = list(orch.chat_stream(tid, INVESTIGATIVE))

    assert "investigation-offer" in _kinds(events)
    assert "table-candidates" not in _kinds(events)
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
