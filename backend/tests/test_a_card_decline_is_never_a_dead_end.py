"""Declining a card either does something visible or is not offered at all (#588).

`Not now` on some Chat cards hid a button and nothing else, and on two of them it left the person
stuck. The button-by-button copy is pinned beside each card's own tests; this file holds the two
changes that live in the store rather than in a component:

- The calculation offer keeps one button, and the next message retires it. The server drops the
  grant on the same turn, so a stale page's click is an ordinary bounded turn.
- A declined Build offer leaves a note, and a reload draws the same note from the suppressed
  handoff rather than an empty space where the card was.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.workspace.threads import ThreadStore

from .test_chat_turn import Turn, _orch

_JS = Path(__file__).resolve().parent / "js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _node(harness: str, spec: dict) -> dict:
    out = subprocess.run(["node", str(_JS / harness)], input=json.dumps(spec), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _render(block: dict) -> dict:
    return _node("recall_offer_harness.mjs", {"block": block})


def _buttons(rendered: dict) -> list[dict]:
    return [n for n in rendered["nodes"] if n["tag"] == "Button"]


# ---- the calculation offer ---------------------------------------------------------------------

_OFFER = {"type": "other_lane_offer", "message": "This needs a calculation that can't run here.",
          "prompt": "correlate signups with seats", "threadId": "t1", "grant": "lane_grant_1"}


@needs_node
def test_the_calculation_offer_has_one_button_and_it_spends_the_grant():
    """The answer above it is one Chat could not finish, so a decline that hid the button left the
    person holding a partial answer. Not pressing it is the decline."""
    buttons = _buttons(_render({**_OFFER, "live": True}))
    assert [b["text"] for b in buttons] == ["Run the calculation"]
    assert buttons[0]["act"] == "calculate:lane_grant_1"


@needs_node
def test_a_retired_calculation_offer_is_the_sentence_alone():
    rendered = _render({**_OFFER, "live": False})
    assert _buttons(rendered) == []
    assert "can't run here" in " ".join(n["text"] for n in rendered["nodes"])


@needs_node
def test_the_next_message_retires_the_calculation_offer():
    """On the send itself, not when the turn ends: a click in between would replay the old question
    under a grant the server has already dropped. Other live cards are left alone."""
    out = _node("card_decline_harness.mjs", {"mode": "send", "messages": [
        {"id": "a1", "role": "assistant", "blocks": [
            {"type": "text", "value": "Roughly 40% by eye."},
            {**_OFFER, "live": True},
            {"type": "continue_offer", "message": "Continue?", "prompt": "p", "threadId": "t1",
             "live": True},
        ]},
    ]})
    assert out["before"] == 1
    assert out["onSend"] == 0
    assert out["after"] == 0
    assert out["otherLive"] == 1
    assert out["grants"] == [""], "the new message is not a replay of the offer"


def test_a_new_turn_drops_the_grant_it_did_not_spend(tmp_path: Path):
    """The server half. A grant outlived every later turn until it was clicked or the server
    restarted, so a button ten messages up replayed an old question and its answer landed at the
    bottom of the chat. Any turn now moves past the offers above it."""
    orch, _ = _orch(tmp_path, [Turn(text="Counted 41,234 accounts."), Turn(text="By region: …")])
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)
    orch._statements_tried[tid] = 1
    grant = list(orch._chat_other_lane_offer(store, tid, "correlate signups with seats",
                                             claimed=True))[0]["grant"]

    list(orch.chat_stream(tid, "and by region?"))

    assert orch._spend_other_lane_grant(tid, grant) is False


# ---- the declined Build offer ------------------------------------------------------------------

_SUPPRESSED = {"suppressed": True, "status": "suppressed"}
_ANSWERED = [
    {"type": "user", "text": "what is in this dataset?"},
    {"type": "agent", "kind": "text", "text": "19 users."},
    {"type": "done", "ok": True, "decision": "answered"},
]


@needs_node
def test_a_reload_draws_the_note_the_click_left():
    out = _node("card_decline_harness.mjs", {
        "mode": "reload", "handoff": _SUPPRESSED,
        "history": _ANSWERED + [{"type": "handoff-suggest", "reason": "classifier"}]})
    assert out["types"][-1] == "plan_suggestion_declined"
    assert "plan_suggestion" not in out["types"]


@needs_node
def test_a_reload_after_an_explicit_decline_draws_no_note():
    """That decline answered the question, and the answer is what stands in the card's place."""
    out = _node("card_decline_harness.mjs", {
        "mode": "reload", "handoff": _SUPPRESSED,
        "history": [{"type": "user", "text": "build me a dashboard"},
                    {"type": "handoff-suggest", "reason": "explicit"},
                    {"type": "agent", "kind": "text", "text": "Here is what that data holds."},
                    {"type": "done", "ok": True, "decision": "answered"}]})
    assert "plan_suggestion_declined" not in out["types"]


@needs_node
def test_an_offer_still_open_is_drawn_as_the_card():
    out = _node("card_decline_harness.mjs", {
        "mode": "reload", "handoff": {"suppressed": False, "status": "suggested"},
        "history": _ANSWERED + [{"type": "handoff-suggest", "reason": "classifier"}]})
    assert out["types"][-1] == "plan_suggestion"
    assert "plan_suggestion_declined" not in out["types"]
