"""An unsure classifier still gets the card, and the other three conditions still refuse (#401).

The reported case: "Which customers actively use Model Monitor, based on Mixpanel, Gong and
Salesforce?" classifies `data_answer` at **0.60** against `MIN_CONFIDENCE = 0.65`, so `intent.valid`
is False and the offer's second gate refuses. The person is never asked, and the turn runs on
whatever `_plain_chat_answer_only` decides. The question most in need of an investigation is the one
the classifier is least sure about, so certainty was the wrong thing for THIS gate to ask.

WHAT SPLIT, AND WHAT DID NOT. `MIN_CONFIDENCE` is unchanged, and so is `intent.valid`. The widening
gate at `_chat_investigation_offer` now reads `intent.usable_label` — did the classifier return a
label it meant — while the narrowing gates below it (`bounded_intent`, `artifact_token`,
`answer_only`) go on reading `intent.valid`. One field was answering two questions in opposite
directions; a reader looking for a changed constant will not find one.

The admitted set is named, never inferred from the label being non-empty: `_parse` keeps `label` on
THREE fallbacks, and the one that earns the named set is `invalid-confidence` — it keeps
`label="data_answer"` on a reply that answered `1.7`, which a membership check cannot see. A bare
`intent.label in {...}` check leaves every end-to-end turn below green and reds only the unit rows,
which is why the property tests at the bottom reach past `chat_stream`.

`no-bound-context` is the obvious-looking answer here and it is the wrong one. `_call` stamps it
only on an already-valid intent and only when nothing is bound, so the gate's bound-item condition —
a proper subset of the kinds `has_bound_context` counts — turns that intent away whatever the
property says. It is excluded for symmetry, not for effect.

Four conditions, four plants: each test below turns exactly one input of `_chat_investigation_offer`
off and asserts the card is gone. Delete any one condition from the gate and exactly one of them
reds. The first test is the condition all four share — the gate being reached at all.

What this does NOT do is put a warehouse back in reach of someone who DECLINES; that turn is still
armed read-only, which the decline test measures rather than assumes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator import chat_intent

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, _orch

# The prompt from the ticket, and the score it actually came back with on cloud-dogfood
# (`sage_rev=6c933ab`). Both live in the test rather than in a constant so that a later move of
# `MIN_CONFIDENCE` moves this number too, and says out loud which way it moved.
ASK = "Which customers actively use Model Monitor, based on Mixpanel, Gong and Salesforce?"
UNSURE = 0.60


def _orch_at(tmp_path: Path, confidence: float = UNSURE, label: str = "data_answer"):
    return _orch(tmp_path, [Turn(text="answered anyway")],
                 gateway=IntentGateway({"label": label, "confidence": confidence}))


def _thread_with_a_store(orch) -> str:
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    return tid


def _kinds(events) -> list[str]:
    return [str(e.get("type") or "") for e in events]


@pytest.mark.parametrize("label", ["data_answer", "data_artifact"])
def test_a_question_the_classifier_was_unsure_about_is_still_offered_the_card(
        tmp_path: Path, label: str):
    """The reported turn. 0.60 is below the threshold and the card is drawn anyway."""
    orch, oc = _orch_at(tmp_path, label=label)
    tid = _thread_with_a_store(orch)
    events = list(orch.chat_stream(tid, ASK))

    card = next(e for e in events if e.get("type") == "investigation-offer")
    assert card["prompt"] == ASK and card["threadId"] == tid
    done = next(e for e in events if e.get("type") == "done")
    assert done["ok"] is False and done["decision"] == "investigation offer"
    # It asked instead of answering, exactly as a confident turn does.
    assert oc.prompts == []


def test_declining_leaves_the_reported_question_on_the_read_only_lane(tmp_path: Path):
    """Declining runs the turn that WOULD have run, and for this question that turn is bounded.

    Worth pinning because it is easy to assume the opposite. `intent.valid` is still False at 0.60,
    so `answer_only` falls through to `_plain_chat_answer_only(prompt)` — and that returns True for
    the reported question. Not because of the `?`: it leads with "Which", which `_asks_about_a_change`
    reads as an info-lead and returns True on at `service.py:2216`, before the build-verb veto or the
    `endswith("?")` test are reached at all. The other half is that the sentence carries none of
    `_CHAT_ARTIFACT_OR_DATA_ASK`'s nouns ("Model Monitor", "Mixpanel", "Gong" and "Salesforce" are
    not `data`/`table`/`rows`/`chart`). So `arm_read_only("question")` fires and the turn loses the
    shell, which is the lane #408 says cannot answer a counting question across three sources yet.

    The distinction is not pedantry: reasoning from the `?` predicts False for "Which customers
    should we delete from the warehouse" — no `?`, and a build verb present — where the real answer
    is True, by the same early return.

    That is ADR-0056's design and not a regression — `Just answer this` promises exactly the turn
    that would have run — but it means #401 alone does not put a warehouse in reach of someone who
    declines. Only `Investigate` does, by setting `investigating` and suppressing both lanes.

    It also disagrees with the 0.60 run recorded on the ticket, which reported 11 tools with bash.
    The predicate has not changed since that `sage_rev`, so the live prompt cannot have been the
    string transcribed here. Asserting the snapshot rather than restating the ticket.
    """
    from .test_chat_turn import ObservedControlOpenCode

    orch, oc = _orch(tmp_path, [Turn(text="Answered.")],
                     gateway=IntentGateway({"label": "data_answer", "confidence": UNSURE}),
                     client=lambda ws: ObservedControlOpenCode(ws, [Turn(text="Answered.")]))
    oc.control = orch.project(start_preview=False).control
    tid = _thread_with_a_store(orch)
    orch.decide_thread_investigation(tid, "decline")

    events = list(orch.chat_stream(tid, ASK))
    assert "investigation-offer" not in _kinds(events)
    assert oc.prompts, "the question ran"
    assert oc.snapshots and oc.snapshots[0].read_only_turn


@pytest.mark.parametrize("decision", ["open", "decline"])
def test_a_decision_already_recorded_is_not_asked_for_again(tmp_path: Path, decision: str):
    """Plant one: the record. Open means there is nothing to ask for, declined means the
    conversation already said no and a card would be the same question a second time."""
    orch, _ = _orch_at(tmp_path)
    tid = _thread_with_a_store(orch)
    orch.decide_thread_investigation(tid, decision)

    assert "investigation-offer" not in _kinds(list(orch.chat_stream(tid, ASK)))


@pytest.mark.parametrize("label", ["plain_answer", "other_chat"])
def test_a_label_outside_the_offered_ones_draws_no_card(tmp_path: Path, label: str):
    """Plant two: the label. Unsure does not mean unread — a turn the classifier put somewhere
    else at 0.60 is still somewhere else, and the widening only reaches the offered labels.
    `build_app` left this list with #488: a fusion "report" is an investigation before it is a
    page, and the funnel's explicit-build guard is what keeps a Build request out, not the label."""
    orch, _ = _orch_at(tmp_path, label=label)
    tid = _thread_with_a_store(orch)

    assert "investigation-offer" not in _kinds(list(orch.chat_stream(tid, ASK)))


@pytest.mark.parametrize("label", ["data_answer", "data_artifact"])
def test_a_thread_with_nothing_bound_draws_no_card(tmp_path: Path, label: str):
    """Plant three: the data. An investigation is an act on a warehouse, so a conversation with
    no data on it is being offered a capability with nowhere to point.

    `data_artifact` is here rather than assumed. An unsure `data_artifact` with nothing bound never
    gets the `no-bound-context` stamp — `_call` only stamps an already-valid intent — so it reaches
    this gate `usable_label`, and THIS condition is what turns it away."""
    orch, _ = _orch_at(tmp_path, label=label)
    tid = orch.create_thread()["id"]

    assert "investigation-offer" not in _kinds(list(orch.chat_stream(tid, ASK)))


def test_an_everyday_question_draws_no_card_however_unsure_the_classifier_was(tmp_path: Path):
    """Plant four: the sentence. Low confidence is not itself a reason to offer — the question
    still has to look like one an investigation would answer."""
    orch, oc = _orch_at(tmp_path)
    tid = _thread_with_a_store(orch)

    events = list(orch.chat_stream(tid, "what were sales last quarter?"))
    assert "investigation-offer" not in _kinds(events)
    assert oc.prompts, "the question ran"


@pytest.mark.parametrize("raw,fallback,admitted", [
    ('{"label":"data_answer","confidence":0.93}', "", True),      # confident, unchanged
    ('{"label":"data_answer","confidence":0.60}', "low-confidence", True),   # the one #401 admits
    ('{"label":"nonsense","confidence":0.93}', "unknown-label", False),
    ('{"label":"data_answer","confidence":1.7}', "invalid-confidence", False),
    ('not json at all', "invalid-json", False),
])
def test_what_the_parser_actually_produces_decides_what_is_usable(
        raw: str, fallback: str, admitted: bool):
    """The admitted set, driven through `_parse` rather than hand-built.

    Asserting the fallback as well as the verdict is the point: an `Intent(confidence=0.93,
    fallback="low-confidence")` is a shape `_parse` can never return, and a row built that way
    proves nothing about the producer. Each row here is a string the classifier could send.
    """
    intent = chat_intent._parse(raw)
    assert intent.fallback == fallback
    assert intent.usable_label is admitted


@pytest.mark.parametrize("fallback", ["timeout", "error", "empty", "prompt-too-long"])
def test_a_classifier_that_returned_no_label_at_all_is_not_usable(fallback: str):
    """`start`/`Pending.result` produce these four without ever reaching `_parse`, so they carry
    no label to refuse on. `valid` leans on `bool(self.label)` for that and so does its sibling."""
    assert chat_intent.Intent(fallback=fallback).usable_label is False
    assert chat_intent.Intent().usable_label is False


def test_the_no_bound_context_stamp_is_refused_but_an_unsure_turn_never_carries_it():
    """The trap, and the honest limit of the line that guards it.

    `_call` builds this stamp from an already-valid intent, which is the ONLY way it occurs, so the
    population this ticket admits can never wear it: an unsure `data_artifact` with nothing bound
    keeps `low-confidence` and IS usable. Nor does refusing it change the confident population,
    because the stamp needs nothing bound and the gate's bound-item condition then refuses the turn
    one line later — `test_a_thread_with_nothing_bound_draws_no_card[data_artifact]` witnesses that
    end to end. So this assertion pins a property, not a behaviour any caller depends on. The line
    that a caller does depend on is `invalid-confidence`, one test above.
    """
    confident = chat_intent._parse('{"label":"data_artifact","confidence":0.93}')
    assert confident.valid
    stamped = chat_intent.Intent(label=confident.label, confidence=confident.confidence,
                                 raw=confident.raw, fallback="no-bound-context")
    assert stamped.usable_label is False

    unsure = chat_intent._parse('{"label":"data_artifact","confidence":0.60}')
    assert unsure.fallback == "low-confidence" and unsure.usable_label is True


def test_certainty_is_still_what_the_narrowing_gates_ask(tmp_path: Path):
    """The half that did not move. 0.60 still fails `valid`, so the bounded lanes stay off it."""
    unsure = chat_intent._parse('{"label":"data_answer","confidence":0.60}')
    assert unsure.usable_label is True and unsure.valid is False
    assert chat_intent.MIN_CONFIDENCE == 0.65
