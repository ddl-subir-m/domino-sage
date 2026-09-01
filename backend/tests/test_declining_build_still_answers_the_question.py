"""`Not now` on a Build offer must not strand the question underneath it.

From a live run. "lets build an dashboard app that allows users to analyze this data" matched the
explicit-build regex, so Chat offered Build INSTEAD of running a turn (`chat_stream`) — right, since
sage-chat writes an Artifact and never an app, and running the turn first spends ninety seconds to
arrive where the offer already is. The person pressed Not now. Nothing happened: the question sat
there answered by nothing, and the only way on was to type it again.

Worse, the decline had switched the offer off permanently as it went, so the retyped copy took a
different path through the same code and got a chat turn. The person's own workaround silently
changed what Sage did with their sentence.

Suppression stays permanent — that is the person saying stop, and the spec says so
(docs/workbench/handoff.md §2, criterion 10). What is fixed is the question: declining an offer that
was made instead of an answer now produces the answer, once, under the question already on screen.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator import handoff

from .fake_opencode import Turn
from .test_chat_turn import _no_waiting, _orch  # noqa: F401  (_no_waiting is an autouse fixture)

BUILD_ASK = "lets build an dashboard app that allows users to analyze this data"


def _types(events: list[dict]) -> list[str]:
    return [e.get("type") for e in events]


def _texts(history: list[dict], kind: str) -> list[str]:
    return [e.get("text") or "" for e in history if e.get("type") == kind]


# ---- which offer owes an answer ------------------------------------------------------------------


def test_an_offer_made_instead_of_an_answer_names_the_question():
    history = [
        {"type": "user", "text": BUILD_ASK},
        {"type": "handoff-suggest", "reason": "explicit"},
        {"type": "done", "ok": True, "decision": "handoff"},
    ]
    assert handoff.unanswered_ask(history) == BUILD_ASK


def test_an_offer_made_after_an_answer_owes_nothing():
    """The classifier runs on a turn that already replied. Its text is in the way, and should be."""
    history = [
        {"type": "user", "text": "what is in this dataset?"},
        {"type": "agent", "kind": "text", "text": "19 users across 4 departments."},
        {"type": "handoff-suggest", "reason": "classifier"},
    ]
    assert handoff.unanswered_ask(history) == ""


def test_a_turn_that_was_stopped_owes_nothing():
    """A stopped turn said why it stopped. That is an answer, and re-running it is not a decline."""
    history = [
        {"type": "user", "text": BUILD_ASK},
        {"type": "stopped", "message": "The step Sage was running did not finish."},
        {"type": "handoff-suggest", "reason": "explicit"},
    ]
    assert handoff.unanswered_ask(history) == ""


def test_no_offer_at_all_owes_nothing():
    assert handoff.unanswered_ask([{"type": "user", "text": "hi"}]) == ""
    assert handoff.unanswered_ask([]) == ""


# ---- the live sequence, end to end ----------------------------------------------------------------


def test_an_explicit_build_request_is_still_offered_build_instead_of_a_turn(tmp_path: Path):
    """Unchanged, and the reason the rest of this file exists rather than a one-line deletion."""
    orch, oc = _orch(tmp_path, [Turn(text="a dashboard")])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, BUILD_ASK))

    assert "handoff-suggest" in _types(events)
    assert not oc.prompts, "the whole point of the short-circuit is that no turn runs"


def test_declining_answers_the_question_it_declined(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="Here is what that data holds.")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, BUILD_ASK))
    assert not oc.prompts

    events = list(orch.decline_handoff_stream(tid))

    assert oc.prompts, "declining an offer made instead of an answer has to produce the answer"
    assert any(e.get("type") == "agent" and e.get("kind") == "text"
               and e.get("text") == "Here is what that data holds." for e in events)
    # And the turn was run against the question, not against anything the browser sent.
    assert BUILD_ASK in oc.prompts[0]["text"]


def test_the_question_is_not_written_to_the_thread_twice(tmp_path: Path):
    """The transcript the person produced by hand. One question, asked once."""
    from sage.workspace.threads import ThreadStore

    orch, _oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, BUILD_ASK))
    events = list(orch.decline_handoff_stream(tid))

    store = ThreadStore(orch.project(start_preview=False).record.path)
    assert _texts(store.read_history(tid), "user") == [BUILD_ASK]
    # Nor to the screen: the bubble is already there, so the stream must not paint a second one.
    assert "user" not in _types(events)


def test_declining_an_offer_that_owes_nothing_only_suppresses(tmp_path: Path):
    """A classifier offer follows a turn that answered. Re-running it would answer twice."""
    orch, oc = _orch(tmp_path, [Turn(text="19 users."), Turn(text="should not run")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what is in this dataset?"))
    ran = len(oc.prompts)

    events = list(orch.decline_handoff_stream(tid))

    assert len(oc.prompts) == ran
    assert _types(events) == ["done"]


def test_suppression_is_still_permanent(tmp_path: Path):
    """Not now is the person saying stop (handoff.md §2, criterion 10). Declining answers the
    question in hand; it does not buy the offer another go."""
    orch, _oc = _orch(tmp_path, [Turn(text="ok"), Turn(text="ok again")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, BUILD_ASK))
    list(orch.decline_handoff_stream(tid))

    events = list(orch.chat_stream(tid, BUILD_ASK))

    assert "handoff-suggest" not in _types(events)


def test_the_declined_turn_is_an_ordinary_chat_turn(tmp_path: Path):
    """It runs sage-chat, not a build agent. Declining Build does not mean building anyway."""
    orch, oc = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, BUILD_ASK))

    list(orch.decline_handoff_stream(tid))

    assert [p["agent"] for p in oc.prompts] == ["sage-chat"]


def test_declining_on_an_unknown_thread_says_so(tmp_path: Path):
    orch, _oc = _orch(tmp_path, [])
    events = list(orch.decline_handoff_stream("th_nope"))
    assert _types(events) == ["error", "done"]
