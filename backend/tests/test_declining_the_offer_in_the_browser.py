"""What `Not now` leaves on screen.

The server half is in `test_declining_build_still_answers_the_question.py`. This is the browser
half, and it is run rather than read for the reason the streaming harness beside it gives: the
failure it guards is a duplicated question in the transcript, not an exception.

That duplication is the live artefact. Declining did nothing, so the person typed their sentence a
second time, and the Thread ended up holding it twice with one unanswered copy above the other.
Answering the pending question by re-sending it through the ordinary send path would rebuild that
transcript exactly — same shape, now written by Sage instead of by hand.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "decline_offer_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

ASK = "lets build an dashboard app that allows users to analyze this data"

# The explicit short-circuit's shape: the question, then the offer, and nothing in between.
OFFERED_INSTEAD = [
    {"id": "u1", "role": "user", "blocks": [{"type": "text", "value": ASK}]},
    {"id": "s1", "role": "system", "blocks": [{"type": "plan_suggestion", "reason": "explicit"}]},
]

# The classifier's shape: the turn answered first, and the offer came after it.
OFFERED_AFTER = [
    {"id": "u1", "role": "user", "blocks": [{"type": "text", "value": "what is in this dataset?"}]},
    {"id": "a1", "role": "assistant", "blocks": [{"type": "text", "value": "19 users."}]},
    {"id": "s1", "role": "system", "blocks": [{"type": "plan_suggestion", "reason": "classifier"}]},
]


# The shape that broke it live. A prompt naming a Dataset draws the file picker BEFORE the handoff
# regex runs, so the offer arrives with a card between it and the question — and a card is an
# assistant message on screen while being a `dataset-files` event in the transcript. The server's
# rule blocks only on events that carry an answer, so it still owes this question one.
OFFERED_AFTER_A_CARD = [
    {"id": "u1", "role": "user", "blocks": [{"type": "text", "value": ASK}]},
    {"id": "a1", "role": "assistant",
     "blocks": [{"type": "dataset_files", "message": "Pick the file to read."}]},
    {"id": "s1", "role": "system", "blocks": [{"type": "plan_suggestion", "reason": "explicit"}]},
]


def _decline(seed: list[dict]) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(seed), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_question_is_answered_where_it_already_is():
    out = _decline(OFFERED_INSTEAD)
    assert out["answers"] == ["Here is what that data holds."]


def test_the_question_is_not_written_to_the_screen_twice():
    """The transcript the person built by hand, and the one shape this fix must not reproduce."""
    assert _decline(OFFERED_INSTEAD)["users"] == [ASK]


def test_the_callout_goes_on_the_click():
    """Not when the turn ends. Declining an offer is an answer to the offer, and it is immediate."""
    assert _decline(OFFERED_INSTEAD)["offers"] == 0


def test_declining_calls_the_route_that_suppresses_and_answers():
    routes = _decline(OFFERED_INSTEAD)["routes"]
    assert "api/threads/t1/handoff/decline" in routes
    # Not the ordinary send: that one records the question, and it is already recorded.
    assert not any("chat/stream" in r for r in routes)


def test_an_offer_that_owes_nothing_only_suppresses():
    """The turn that raised this one already answered. Running it again would answer twice."""
    out = _decline(OFFERED_AFTER)
    assert out["routes"] == ["api/threads/t1"]      # the plain suppress PATCH, and nothing else
    assert out["answers"] == ["19 users."]


def test_a_card_between_the_question_and_the_offer_is_not_an_answer():
    """The live failure. "Build a dashboard from @<Dataset>" draws the file picker first, so the
    offer no longer sits directly under the question — and the client read that card as an answer,
    suppressed the offer without calling the route, and left the button doing nothing at all.

    The server never agreed: `handoff.unanswered_ask` blocks on the event types that carry an
    answer, and a `dataset-files` card is none of them. The client no longer holds a second opinion.
    """
    out = _decline(OFFERED_AFTER_A_CARD)
    assert "api/threads/t1/handoff/decline" in out["routes"]
    assert out["answers"][-1] == "Here is what that data holds."


def test_the_thread_reads_as_suppressed_either_way():
    for seed in (OFFERED_INSTEAD, OFFERED_AFTER):
        assert _decline(seed)["handoff"]["status"] == "suppressed"
