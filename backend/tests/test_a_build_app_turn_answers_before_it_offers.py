"""A `build_app` turn answers first, and offers Build beside the answer (#453).

The intent classifier ran BEFORE the turn and ended it: offer, `done`, return. The card it minted
was tagged `reason: "classifier"`, and that word is what the client reads to decide that declining
owes nothing (`message-blocks.js:425`, `store.js:8274`). True of a card sitting under an answer;
false of one raised INSTEAD of it. So `Not now` threw the question away and Retry was the only way
back to it. Reported live on `sage-subir-mansukhani-66a821b1`, 2026-09-19, on a request for a
report — `chat_intent.py:34` counts a report as `build_app`, so an analysis question reaches that
gate routinely.

`handoff.md` §2 already says which way round it goes: run the classifier AFTER each turn.

The verdict is CARRIED to the post-turn site rather than simply dropped, and that is the part worth
arguing with, so `test_the_carry_is_not_decoration` below plants against it. `bounded_intent`
excludes `build_app`, so the turn reaches `_maybe_suggest_handoff` unaided — but that site asks
`wants_an_app`, a second model call whose prompt answers CHAT for "a question, a chart, a table,
exploration" and defaults to CHAT. `chat_intent`'s `build_app` counts a report or a page. Delete
the early return and nothing else and this ticket's own population gets its answer and loses its
offer, having paid a five-second call to be told so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn

# Names no Data Source, so the table gate declines off the Thread's own rows and what these tests
# watch is the handoff card alone. `looks_like_build_request` does NOT match it — asserted below,
# because every claim in this file is about the arm the regex does not reach.
PROMPT = "put together a quarterly headcount report the team can open"

ANSWER = "Headcount rose 4% over the quarter."


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class RecordingGateway:
    """`build_app` to the intent classifier, CHAT to everything else, and it keeps the receipts.

    The CHAT is deliberate. `wants_an_app` answering CHAT is what a live gateway would most likely
    say about a one-off report, so a card these tests see cannot have come from it — it came from
    the carried verdict or from nowhere. `components` is how the second call is counted.
    """

    def __init__(self, label: str = "build_app", confidence: float = 0.93):
        self.label = label
        self.confidence = confidence
        self.components: list[str] = []

    def route(self, request, labels):
        component = getattr(labels, "component", "")
        self.components.append(component)
        if component == "chat-intent":
            verdict = json.dumps({"label": self.label, "confidence": self.confidence})
        else:
            verdict = "CHAT"
        body = json.dumps({"choices": [{"delta": {"content": verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _orch(tmp: Path, gateway: RecordingGateway | None = None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    # Two, because one test asks a second question. `FakeOpenCode` answers with nothing once the
    # script runs out, and a turn that answers with nothing is indistinguishable here from a turn
    # that never ran — which is the exact distinction that test exists to make.
    oc = FakeOpenCode(ws, [Turn(text=ANSWER), Turn(text=ANSWER)])
    orch = Orchestrator(workspace_dir=ws, template=template,
                        gateway=gateway or RecordingGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s",
                                             plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def _store(orch: Orchestrator) -> ThreadStore:
    return ThreadStore(orch.project(start_preview=False).record.path)


def _types(events: list[dict]) -> list[str]:
    return [str(e.get("type") or "") for e in events]


def test_the_prompt_these_tests_use_is_not_an_explicit_build_request():
    """The premise. Every claim below is about the classifier arm, and it would read exactly the
    same if the regex had quietly started matching this sentence — the card would still be there,
    tagged `explicit` instead, and four of these tests would pass for the wrong reason."""
    assert handoff.looks_like_build_request(PROMPT) is False


def test_the_answer_arrives_and_the_offer_rides_out_beside_it(tmp_path: Path):
    """The ticket. The turn runs, the answer lands, and the card comes after it rather than
    instead of it.

    Pinned by ORDER, not by membership. "both events are in there somewhere" is satisfied by the
    old behaviour plus any later turn, and the thing that was broken was which one came first.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, PROMPT))
    kinds = _types(events)

    assert oc.prompts, "the turn must run: that is the answer the person came for"
    assert any(e.get("type") == "agent" and e.get("text") == ANSWER for e in events)
    offer = next(e for e in events if e.get("type") == "handoff-suggest")
    assert offer["reason"] == "classifier"
    assert kinds.index("agent") < kinds.index("handoff-suggest")
    assert kinds.index("done") < kinds.index("handoff-suggest")


def test_the_carry_is_not_decoration(tmp_path: Path):
    """The carry, measured. Deleting the early return alone would leave `wants_an_app` to decide,
    and its prompt calls a one-off report CHAT — the answer would arrive and the offer would not.

    So this asserts BOTH halves at once: the card is there, and the handoff classifier was never
    asked. One model call for this turn's intent, none for a second opinion on it.
    """
    gateway = RecordingGateway()
    orch, _oc = _orch(tmp_path, gateway)
    tid = orch.create_thread()["id"]

    assert "handoff-suggest" in _types(list(orch.chat_stream(tid, PROMPT)))
    assert "handoff" not in gateway.components
    assert gateway.components.count("chat-intent") == 1


def test_a_label_that_is_not_build_app_still_asks_the_handoff_classifier(tmp_path: Path):
    """The other side of the carry. `already_classified` has to be the verdict and not a constant:
    wired True for everyone it would mint a card on every unbounded turn in Sage, and every test
    above would still be green. `other_chat` is unbounded, so it reaches the same site — and there
    it must pay for the second opinion it has not already got."""
    gateway = RecordingGateway(label="other_chat")
    orch, _oc = _orch(tmp_path, gateway)
    tid = orch.create_thread()["id"]

    assert "handoff-suggest" not in _types(list(orch.chat_stream(tid, PROMPT)))
    assert "handoff" in gateway.components


def test_not_now_is_still_permanent(tmp_path: Path):
    """Constraint 2. Once no answer is lost, `Not now` keeps being the one permanent answer
    (`handoff.md` §8, criterion 10). The gate moved from `should_offer_explicit` to
    `should_classify` with the verdict, and a suppressed Thread is silent under both — but only
    one of them is on the path now, so it is this one that has to be shown silent."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    _store(orch).suppress_handoff(tid)

    kinds = _types(list(orch.chat_stream(tid, PROMPT)))

    assert "handoff-suggest" not in kinds
    assert "agent" in kinds          # and the question is still answered
    assert len(oc.prompts) == 1


def test_one_turn_draws_one_offer(tmp_path: Path):
    """Constraint 3. The verdict now reaches a site that could also detect one of its own, and a
    turn that drew two cards would be the obvious way for this change to go wrong quietly. Counted
    on the stream AND on the record, because the two are written by different lines."""
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    kinds = _types(list(orch.chat_stream(tid, PROMPT)))

    assert kinds.count("handoff-suggest") == 1
    assert _types(orch.thread_history(tid)).count("handoff-suggest") == 1
    assert len(_store(orch).read_handoffs(tid)) == 1


def test_the_offer_is_recorded_so_the_next_turn_stays_quiet_but_still_answers(tmp_path: Path):
    """The card goes on `handoff.json`, as the pre-turn block's `mark_handoff_suggested` did. Two
    build-shaped questions in a row is the noise `should_classify` exists to stop, and the carry
    would sail straight past it if the first card were never written down.

    The SECOND half is the half worth writing down. Silence on turn two is what the gate move from
    `should_offer_explicit` to `should_classify` buys, and a test that only asserts the silence
    would be equally green if turn two had gone back to ending before it ran — which is #453 in the
    one Thread state where nobody would look for it. So: no card, and the question still answered.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, PROMPT))
    first = _store(orch).read_handoffs(tid)
    assert first and first[-1]["status"] == "suggested"

    second = list(orch.chat_stream(tid, PROMPT))

    assert "handoff-suggest" not in _types(second)
    assert _store(orch).read_handoffs(tid) == first
    assert len(oc.prompts) == 2, "quiet is not the same as short-circuited"
    assert any(e.get("type") == "agent" and e.get("text") == ANSWER for e in second)


def test_an_explicit_build_request_is_still_offered_instead_of_the_turn(tmp_path: Path):
    """Constraint 1. The explicit arm is untouched: the person asked in words, so Build answers
    rather than Chat, the card is tagged `explicit`, and declining it still owes the answer through
    `/handoff/decline`. Nothing here changed, which is exactly why it is worth a line — the two
    arms now differ in one more way than they did."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "build me a dashboard of last quarter's headcount"))

    assert _types(events) == ["user", "handoff-suggest", "done"]
    assert events[1]["reason"] == "explicit"
    assert oc.prompts == []
