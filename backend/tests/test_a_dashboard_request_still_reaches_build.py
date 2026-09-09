"""An explicit build request is offered Build every time, not only the first time.

`should_classify` stops the handoff classifier from stacking guesses: one unanswered `suggested`
row and it stays quiet until somebody resolves it. That is right for a guess. `_explicit_handoff`
borrowed the same rule, and for an explicit "build me a dashboard" it is wrong — the person asked
in words, and a card they scrolled past is not an answer to it.

The cost was not a missing card. A silenced nudge means the request runs as a sage-chat turn, and
Chat cannot build an app: the model writes the whole page in one `write`, the gateway cuts that
buffered stream at about 48 seconds, and the turn ends on "Now I have everything. Let me build the
full HTML dashboard artifact." with nothing under it. Live on cloud-dogfood 2026-09-08, in a
Thread whose earlier turn had raised a card nobody clicked.

Declining is still permanent, and still has to be: declining re-runs the same sentence, so a
nudge that fired on it again would loop.
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
# watch is the nudge alone. `looks_like_build_request` matches it.
PROMPT = "build me a dashboard of last quarter's headcount"


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """CHAT, so a nudge these tests see came from the regex and not from the classifier."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _orch(tmp: Path):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="Here it is.")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
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


def test_a_card_nobody_clicked_does_not_send_the_next_dashboard_into_chat(tmp_path: Path):
    """The live failure. An earlier turn raised the card; the person kept typing instead.

    Pinned by position and by the agent: the nudge is the first thing after the question goes on
    the record, and no sage-chat turn runs at all. "in the events somewhere" would pass on the
    classifier's own suggestion after the turn, which is the turn this exists to prevent.
    """
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    _store(orch).mark_handoff_suggested(tid)

    kinds = _types(list(orch.chat_stream(tid, PROMPT)))

    assert kinds == ["user", "handoff-suggest", "done"]
    assert oc.prompts == []


def test_the_second_ask_does_not_stack_a_second_row_on_the_record(tmp_path: Path):
    """Offering again is not suggesting again. The Thread has been offered Build once."""
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    store = _store(orch)
    store.mark_handoff_suggested(tid)
    first = store.read_handoffs(tid)

    list(orch.chat_stream(tid, PROMPT))

    assert store.read_handoffs(tid) == first


def test_declining_is_still_permanent(tmp_path: Path):
    """`Not now` is the person saying stop, and declining re-runs this same sentence — so a nudge
    that fired under a decline would offer, decline, offer, forever."""
    orch, oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    _store(orch).suppress_handoff(tid)

    kinds = _types(list(orch.chat_stream(tid, PROMPT)))

    assert "handoff-suggest" not in kinds
    assert "agent" in kinds
    assert len(oc.prompts) == 1


def test_a_plan_already_open_is_not_offered_again(tmp_path: Path):
    """`planned` is the offer taken, not the offer ignored. It is already underway."""
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    store = _store(orch)
    store.mark_handoff_suggested(tid)
    store.mark_handoff_planned(tid, "plan_1")

    assert "handoff-suggest" not in _types(list(orch.chat_stream(tid, PROMPT)))


def test_the_classifier_keeps_its_own_quieter_rule(tmp_path: Path):
    """Only the explicit path changed. A guess about a turn that never asked for an app still
    waits for the last card to be answered, because guessing every few messages is the noise
    `should_classify` was written against."""
    suggested = [{"suggestedAt": "2026-09-08T00:00:00Z", "status": "suggested"}]

    assert handoff.should_classify(suggested) is False
    assert handoff.should_offer_explicit(suggested) is True
