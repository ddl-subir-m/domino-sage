"""The turn that would have answered bounded asks first, and does not run (#386, ADR-0056).

A cross-source question classifies `data_answer`. `data_answer` arms `arm_read_only("question")`,
`READ_ONLY_DENIED = WRITE_TOOLS | SHELL_TOOLS` takes the shell, and `live_read_table` accepts no
SQL — so the turn that most needs a warehouse is the one that cannot reach one. #381 exempted a
Thread that was already investigating and gated that on `findings.md` being on disk, which no
bounded turn can write: the exemption could not fire on any Thread ever.

The way in is a decision the person makes. This is the ask: a card, and the turn ends. Nothing is
granted here and nothing is answered here.

SEVEN SENTENCES, of all three kinds, because the rule is a rule and not a sentence. Three name the
act; two name no verb at all and fuse sources, which is #378's own example and the shape the
classifier sends down the bounded lane; two doubt a column rather than reading it. Replacing the
trigger with a comparison against any one of them reds the other six.

ALL THREE ARE ABOUT THE QUESTION'S SHAPE, and none is about the evidence. A turn that has not run
has measured nothing, so "is the signal weak here" is not a fact this gate has — and a trigger that
guessed it would withhold the offer from exactly the question that needed it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, _orch

ASKS = [
    "Which customers actively use Model Monitor, based on Mixpanel, Gong and Salesforce?",
    "Investigate which accounts look like adopters and score them.",
    "Can you dig into why weekly active users fell last month?",
    "Work out which deals stalled, and get to the bottom of what they have in common.",
    "Correlate support volume with the churn flag and tell me which accounts to watch.",
    "Which of these accounts actually use the product, rather than having the flag set?",
    "Do the trial accounts really log in every week?",
]


def _thread_with_a_store(orch):
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    return tid


@pytest.mark.parametrize("prompt", ASKS)
@pytest.mark.parametrize("label", ["data_answer", "data_artifact"])
def test_the_turn_yields_a_card_and_does_not_run(tmp_path: Path, prompt: str, label: str):
    orch, oc = _orch(tmp_path, [Turn(text="answered anyway")],
                     gateway=IntentGateway({"label": label, "confidence": 0.93}))
    tid = _thread_with_a_store(orch)

    events = list(orch.chat_stream(tid, prompt))

    card = next(e for e in events if e.get("type") == "investigation-offer")
    assert card["prompt"] == prompt and card["threadId"] == tid and card["message"]
    done = next(e for e in events if e.get("type") == "done")
    assert done["ok"] is False and done["decision"] == "investigation offer"
    # It ASKED instead of answering. A card drawn over a turn that also ran would be the worst of
    # both: the bounded answer already on screen under a question about whether to go and look.
    assert oc.prompts == []


def test_the_card_survives_a_reload(tmp_path: Path):
    """Both events go to the Thread before either is yielded, the way the table card's do. A card
    held only in the stream is a card that vanishes on the reload somebody does after reading it."""
    orch, _ = _orch(tmp_path, gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}))
    tid = _thread_with_a_store(orch)

    list(orch.chat_stream(tid, ASKS[0]))

    rows = orch.thread_history(tid)
    assert [r["type"] for r in rows[-2:]] == ["investigation-offer", "done"]
    assert rows[-2]["prompt"] == ASKS[0]
    # And the question is on the record above it, so the replay does not have to write it again.
    assert any(r.get("type") == "user" and r.get("text") == ASKS[0] for r in rows)


# The everyday readings of the trigger's own words, each one a question that would have been
# answered. They are here because the card ENDS the turn: a false positive is not a line somebody
# scrolls past, it is a click and a round trip charged to a question that needed neither.
QUIET = [
    # `across` and `join` are ordinary data words until they reach a list.
    "How did revenue break down across regions last quarter?",
    "How many users joined last month?",
    # `look into` is the act; `look to` is a preposition.
    "which table should I look to for churn?",
    # `really` and `actually` are intensifiers unless they reach a verb of doing.
    "can you actually show me revenue by region?",
    "that's really useful — now break it down by month",
]


@pytest.mark.parametrize("prompt", QUIET)
def test_an_everyday_question_is_answered_rather_than_offered(tmp_path: Path, prompt: str):
    orch, oc = _orch(tmp_path, [Turn(text="Answered.")],
                     gateway=IntentGateway({"label": "data_answer", "confidence": 0.93}))
    tid = _thread_with_a_store(orch)

    events = list(orch.chat_stream(tid, prompt))

    assert not any(e.get("type") == "investigation-offer" for e in events)
    assert oc.prompts, "the question ran"


@pytest.mark.parametrize("why,label,prompt,bind", [
    ("an ordinary data question", "data_answer",
     "what's our gross exposure by desk?", True),
    ("a turn that keeps its tools anyway", "other_chat",
     "Investigate which accounts look like adopters and write the script that scores them.", True),
    ("nothing bound to investigate", "data_answer",
     "Investigate which accounts look like adopters and score them.", False),
    ("a plain answer, not a data question", "plain_answer",
     "Investigate which accounts look like adopters and score them.", True),
])
def test_no_card_is_drawn(tmp_path: Path, why: str, label: str, prompt: str, bind: bool):
    """The ways this declines, each leaving the turn exactly as it was."""
    orch, _ = _orch(tmp_path, [Turn(text="Answered.")],
                    gateway=IntentGateway({"label": label, "confidence": 0.93}))
    tid = _thread_with_a_store(orch) if bind else orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, prompt))

    assert not any(e.get("type") == "investigation-offer" for e in events)


# --- #488: the report that fused three systems and was offered nothing ------------------------

# The sentence that opened the ticket, verbatim. Every "report" is `build_app` by the classifier's
# own rule (`chat_intent.py`: "even if they involve data"), and "fuse" matched no limb — so with a
# chip on the Thread and the ADR's own example shape, this drew no card, ran on the shell lane for
# 107 s, and was stopped by the repeat brake.
REPORT = ("give me a report of active DMM users. fuse data from gong mixpanel and sfdc to build "
          "this report. be sure to include customer status in sfdc and mention accounts where the "
          "signal is only from gong in separate columns. exclude churned customers and domino "
          "employees from the analysis")


@pytest.mark.parametrize("verb", ["fuse", "fuses", "fused", "fusing",
                                  "merge", "merges", "merged", "merging",
                                  "blend", "blends", "blended", "blending"])
def test_the_fusion_verbs_name_the_limb_they_belong_to(verb: str):
    """Each verb means fusion on its own, the way "reconcile" does — no trailing list needed.
    One row per form, so a pattern that kept the stem and lost an ending reds its own row."""
    from sage.orchestrator.service import _looks_investigative
    assert _looks_investigative(f"{verb} the gong data with sfdc"), verb


def test_a_report_that_fuses_sources_is_offered_the_investigation(tmp_path: Path):
    """`build_app` is admitted. The label still rides to the end of the turn for the handoff
    suggest (#453); what changes is that a fusion with a chip on the Thread is asked first."""
    orch, oc = _orch(tmp_path, [Turn(text="answered anyway")],
                     gateway=IntentGateway({"label": "build_app", "confidence": 0.75}))
    tid = _thread_with_a_store(orch)

    events = list(orch.chat_stream(tid, REPORT))

    card = next(e for e in events if e.get("type") == "investigation-offer")
    assert card["prompt"] == REPORT
    done = next(e for e in events if e.get("type") == "done")
    assert done["ok"] is False and done["decision"] == "investigation offer"
    assert oc.prompts == []


def test_a_classifier_that_did_not_answer_offers_rather_than_running_blind(tmp_path: Path):
    """The classifier's failure fallbacks used to run the turn on the eleven-tool lane with no
    grant. `IntentGateway("not json")` is what a model whose JSON mode cannot hold sends; the
    parser stamps `invalid-json`, one of the three fallbacks that mean it never answered."""
    orch, oc = _orch(tmp_path, [Turn(text="answered anyway")], gateway=IntentGateway("not json"))
    tid = _thread_with_a_store(orch)

    events = list(orch.chat_stream(tid, REPORT))

    assert any(e.get("type") == "investigation-offer" for e in events)
    assert oc.prompts == []


def test_an_explicit_build_request_that_fuses_sources_still_goes_to_build(tmp_path: Path):
    """The guard that stays. ADR-0059 sends a build-shaped sentence past this gate before it is
    asked, so admitting `build_app` does not put a Chat grant in front of a Build request."""
    orch, _ = _orch(tmp_path, [Turn(text="answered anyway")],
                    gateway=IntentGateway({"label": "build_app", "confidence": 0.93}))
    tid = _thread_with_a_store(orch)

    prompt = "build me a dashboard that fuses Gong and Salesforce into one view of adopters"
    events = list(orch.chat_stream(tid, prompt))

    assert not any(e.get("type") == "investigation-offer" for e in events)
    assert any(e.get("type") == "handoff-suggest" for e in events), [e.get("type") for e in events]
