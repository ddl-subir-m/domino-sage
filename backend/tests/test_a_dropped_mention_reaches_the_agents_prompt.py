"""A dropped @mention is told to the agent, not only to the screen (#130).

WHAT WAS MISSING. `_unusable_mentions` has always written the honest sentence — "Couldn't use
@sonnet — this app doesn't use it yet" — and always written it to ONE reader. It was yielded as a
`mentions-unresolved` bubble and nowhere else. The prompt the agent received had the mention taken
out of it and no note that anything had been taken, which is not a gap a model can see: it reads as
an ordinary under-specified request. So the agent invents. It picks a Resource the app IS bound to,
or writes placeholder rows, and reports a clean build — and the person reads a green turn built on
nothing they named, one bubble below the sentence saying it couldn't be built.

WHAT THIS ASSERTS. The same sentence, in the prompt, for all three reasons a mention is dropped: a
Resource with no Binding, a file that was never attached, and a Chat upload that lives outside the
app (`.sage/scratch/`, the Project root). One value feeds both readers, so a fourth drop reason
added later cannot reach one and miss the other.

AND WHAT IT MUST NOT SAY. Both repairs are a person's act — attaching a file and recording a
Binding (ADR-0010) — so the note carries its own prohibitions, and they are the half worth
guarding. Told about a gap with no instruction, a model closes it: it substitutes, it mocks, or it
goes looking for the binding control. Name the gap and stop is the whole of the wanted behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """The scope classifier is the only caller on this path; BUILD keeps it out of the way."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The two waits a scripted turn can never spend usefully: the poll sleep and the runtime-error
    poll."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orch(tmp: Path):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="1. Add the table")])
    orch = Orchestrator(
        workspace_dir=ws, template=template, gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def _turn(orch, oc, mentions=None, resources=None) -> tuple[str, list[dict]]:
    """One Build turn. Returns what the agent was sent, and what the transcript was told."""
    events = list(orch.build_stream("add a table of sales", mentions, resources))
    return oc.prompts[-1]["text"], events


def _screen(events: list[dict]) -> str:
    said = [e["message"] for e in events if e["type"] == "mentions-unresolved"]
    assert len(said) == 1, f"expected one mentions-unresolved event, got {said}"
    return said[0]


# ---- the three drop reasons ----------------------------------------------------------------


def test_a_resource_this_app_never_recorded_is_named_in_the_prompt(tmp_path: Path):
    orch, oc = _orch(tmp_path)

    sent, events = _turn(orch, oc, resources=[
        {"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}])

    assert _screen(events) in sent          # the screen's own sentence, whole, not a paraphrase
    assert "@Warehouse" in sent


def test_a_file_that_was_never_attached_is_named_in_the_prompt(tmp_path: Path):
    orch, oc = _orch(tmp_path)

    sent, events = _turn(orch, oc, mentions=["public/data/gone.csv"])

    assert _screen(events) in sent
    assert "@gone.csv" in sent and "not attached to this app" in sent


def test_a_chat_file_living_outside_the_app_is_named_in_the_prompt(tmp_path: Path):
    orch, oc = _orch(tmp_path)

    sent, events = _turn(orch, oc, mentions=[".sage/scratch/events.csv"])

    assert _screen(events) in sent
    assert "@events.csv" in sent and "Chat file" in sent


# ---- what the note tells the agent to do about it -------------------------------------------


def test_the_note_forbids_the_repair_only_a_person_can_make(tmp_path: Path):
    # The failure this guards is the agent reading a report of a gap as a job. It cannot record a
    # Binding (ADR-0010) and it cannot attach a file, so a turn that goes looking for either burns
    # the turn and still builds nothing.
    orch, oc = _orch(tmp_path)

    sent, _ = _turn(orch, oc, resources=[
        {"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}])

    note = sent[sent.index("The person @mentioned something this turn cannot use"):]
    assert "You cannot repair this yourself" in note
    assert "do not attach or record anything" in note
    # And the other invention: filling the hole so the build stays green.
    assert "do not substitute a different file or Resource" in note
    assert "placeholder, mock or example data" in note


def test_a_turn_that_dropped_nothing_carries_no_note(tmp_path: Path):
    # A note on every turn would be noise the agent learns to skip, and would put the sentence in
    # front of a build that never lost anything.
    orch, oc = _orch(tmp_path)

    sent, events = _turn(orch, oc)

    assert [e for e in events if e["type"] == "mentions-unresolved"] == []
    assert "this turn cannot use" not in sent


def test_a_recorded_resource_is_neither_dropped_nor_reported(tmp_path: Path):
    # The prompt half reads the same Binding list the turn honors, so a Resource the app IS
    # recorded as using must never arrive as one the turn refused.
    orch, oc = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    proj.workspace.update_bindings(
        lambda entries: [*entries,
                         Binding(KIND_DATA_SOURCE, "ds1", "Warehouse", "Warehouse").to_dict()])

    sent, events = _turn(orch, oc, resources=[
        {"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}])

    assert [e for e in events if e["type"] == "mentions-unresolved"] == []
    assert "this turn cannot use" not in sent
