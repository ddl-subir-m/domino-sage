"""#607: `analyze_text` took an `alias` and judged the text on the turn's model anyway.

Every batch went through `shim.handle`, and the shim overwrites `request["model"]` with the
router's decision unconditionally. So "use opus to analyze" ran on whatever answered the turn, and
nothing said so. The alias now goes through the same consent and sensitivity gate a Delegated
model call does (ADR-0057), and a refusal comes back before a single row is sent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sage import timing
from sage.liveread import run
from sage.resources.provider import ApprovedModels, ResourceUnavailable

from .test_a_chat_turn_can_call_a_model_the_person_bound import SONNET_LABEL, Aliases, _chip, _orch
from .test_chat_turn import ScriptedGateway
from .test_csv_text_analysis_data_used import (
    COMPLAINTS,
    analysis_args,
    labels_for,
    setup_turn,
    sse,
)

SONNET, OPUS = "sonnet", "opus"


@pytest.fixture(autouse=True)
def _no_real_poll(monkeypatch):
    """`FakeOpenCode` has no event stream, so the Chat turn in `_mid_turn` waits one streamless
    poll. With sleep erased, conftest's scripted clock pays that second instead of the wall."""
    monkeypatch.setattr(time, "sleep", lambda *_: None)


class JudgingGateway(ScriptedGateway):
    """Answers a text-analysis batch with labels, and remembers which model it was sent to."""

    def __init__(self) -> None:
        super().__init__()
        self.batches: list[dict] = []

    def route(self, request, labels):
        messages = request.get("messages") or [{}]
        if str(messages[0].get("content", "")).startswith("Return only JSON with a records array"):
            self.batches.append(request)
            yield from sse(json.dumps(labels_for(request)))
            return
        yield from super().route(request, labels)


def _mid_turn(tmp: Path):
    """Sonnet answering the turn, `opus` chipped into the Conversation, a CSV attached."""
    gateway = JudgingGateway()
    orch, _oc = _orch(tmp, gateway=gateway, resources=Aliases())
    tid = orch.create_thread()["id"]
    _chip(orch, tid)
    project = orch._chat_project()
    project.control.pick_chat(SONNET)
    list(orch.chat_stream(tid, "classify these complaints"))
    project.control.pick_chat(SONNET)
    project.control.arm_chat(tid)
    upload = orch.upload_scratch("complaints.csv", COMPLAINTS.encode())
    orch.add_thread_context(tid, {"kind": "file", "path": upload["path"],
                                  "name": "complaints.csv"})
    return orch, gateway, tid, project, upload["path"]


def _analyze(orch, tid, path, alias):
    return run.perform("live_read_files", analysis_args(path=path, batch_size=6, alias=alias),
                       orch._live_read_turn_for(tid))


def _events(orch, project, tid):
    return project.shim.data_use.events(orch._data_use_turns.get(tid, ""))


def test_an_allowed_different_alias_judges_the_text_and_is_named_in_the_evidence(tmp_path):
    orch, gateway, tid, project, path = _mid_turn(tmp_path)

    reply = json.loads(_analyze(orch, tid, path, OPUS))

    assert reply["coverage"]["processed"] == 12
    assert [b["model"] for b in gateway.batches] == [OPUS, OPUS], gateway.batches
    requests = _events(orch, project, tid)[0]["requests"]
    assert [r["serving_model"] for r in requests] == [OPUS, OPUS], requests


def test_an_alias_the_conversation_never_named_is_refused_with_nothing_sent(tmp_path):
    orch, gateway, tid, project, path = _mid_turn(tmp_path)

    said = _analyze(orch, tid, path, "gpt-5.4")

    assert "gpt-5.4 isn't a language model in this conversation" in said, said
    assert gateway.batches == []
    assert _events(orch, project, tid) == []


def test_an_alias_outside_the_sensitivity_lock_is_refused_with_nothing_sent(tmp_path):
    orch, gateway, tid, project, path = _mid_turn(tmp_path)
    orch._sensitivity_for_turn = lambda *_a, **_k: (
        ApprovedModels(frozenset({SONNET}), (SONNET,)), "")

    said = _analyze(orch, tid, path, OPUS)

    assert "isn't approved for the data in this conversation" in said, said
    assert gateway.batches == []
    assert _events(orch, project, tid) == []


def test_a_gate_that_cannot_be_read_refuses_rather_than_judging(tmp_path):
    orch, gateway, tid, project, path = _mid_turn(tmp_path)

    def broken(*_a, **_k):
        raise ResourceUnavailable("the gateway answered 503")

    orch._sensitivity_for_turn = broken

    said = _analyze(orch, tid, path, OPUS)

    assert said.startswith(f"Sage did not analyze the text with {OPUS}. "), said
    assert "couldn't check which models the data in this conversation allows" in said, said
    assert gateway.batches == []
    assert _events(orch, project, tid) == []


def test_a_declared_lock_with_nothing_approved_refuses_naming_the_alias(tmp_path):
    orch, gateway, tid, project, path = _mid_turn(tmp_path)
    reason = "Nothing is approved for support-cases: the LLM Alias group approved is empty."
    orch._sensitivity_for_turn = lambda *_a, **_k: (None, reason)

    said = _analyze(orch, tid, path, OPUS)

    assert said == f"Sage did not analyze the text with {OPUS}. {reason}", said
    assert gateway.batches == []
    assert _events(orch, project, tid) == []


def test_a_conversation_with_no_model_in_it_refuses_naming_the_alias(tmp_path):
    gateway = JudgingGateway()
    orch, _oc = _orch(tmp_path, gateway=gateway, resources=Aliases())
    tid = orch.create_thread()["id"]

    said = _analyze(orch, tid, "complaints.csv", OPUS)

    assert said.startswith(f"Sage did not analyze the text with {OPUS}. "), said
    assert "No language model is in this conversation" in said, said
    assert gateway.batches == []


def test_no_alias_or_the_turns_own_model_runs_on_the_turn_as_before(tmp_path):
    orch, gateway, tid, project, path = _mid_turn(tmp_path)

    for alias in (None, SONNET, SONNET_LABEL, "auto", "AUTO"):
        gateway.batches.clear()
        reply = json.loads(_analyze(orch, tid, path, alias))
        assert reply["coverage"]["processed"] == 12, alias
        assert [b["model"] for b in gateway.batches] == [SONNET, SONNET], (alias, gateway.batches)
    requests = _events(orch, project, tid)[-1]["requests"]
    assert [r["serving_model"] for r in requests] == [None, None], requests


@pytest.mark.parametrize("alias, ran_on", [(OPUS, OPUS), (None, SONNET)])
def test_every_batch_is_on_the_turns_ledger_by_the_model_it_ran_on(tmp_path, alias, ran_on):
    """#606: neither route passes the /v1 handler that fills the ledger, so /api/diag/timing read
    an analyze_text turn as shorter than it was by every batch in it."""
    orch, gateway, tid, _project, path = _mid_turn(tmp_path)
    timing.start_turn("chat", "classify these complaints")
    try:
        json.loads(_analyze(orch, tid, path, alias))
    finally:
        record = timing.finish_turn(ok=True, decision="-")

    batches = [c for c in record.calls if c.phase == "text-analysis"]
    assert len(batches) == len(gateway.batches) == 2
    assert [(c.model, c.ok, c.chunks > 0) for c in batches] == [(ran_on, True, True)] * 2


def test_a_turn_with_no_gate_wired_refuses_an_alias(tmp_path):
    def provider(_request):
        raise AssertionError("model called")

    turn, _data, journal, _source = setup_turn(tmp_path, provider=provider)

    said = run.perform("live_read_files", analysis_args(alias=OPUS), turn)

    assert "could not be checked" in said, said
    assert journal == []


def test_the_shim_still_overrides_the_model_for_every_session(tmp_path):
    """The guard. The alias is honoured by routing around the shim, never by loosening it."""
    _, gateway, tid, project, _path = _mid_turn(tmp_path)
    request = {"model": OPUS, "stream": True, "messages": [
        {"role": "system", "content": "Return only JSON with a records array."},
        {"role": "user", "content": json.dumps({"records": []})}]}

    for session in ("controlled", f"{tid}:text-analysis"):
        gateway.batches.clear()
        list(project.shim.handle(request, project=project.id, session=session))
        assert [b["model"] for b in gateway.batches] == [SONNET], (session, gateway.batches)
