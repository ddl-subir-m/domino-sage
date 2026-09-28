"""#607: `analyze_text` took an `alias` and judged the text on the turn's model anyway.

Every batch went through `shim.handle`, and the shim overwrites `request["model"]` with the
router's decision unconditionally. So "use opus to analyze" ran on whatever answered the turn, and
nothing said so. The alias now goes through the same consent and sensitivity gate a Delegated
model call does (ADR-0057), and a refusal comes back before a single row is sent.
"""

from __future__ import annotations

import json
from pathlib import Path

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

    assert "couldn't check which models the data in this conversation allows" in said, said
    assert gateway.batches == []
    assert _events(orch, project, tid) == []


def test_no_alias_or_the_turns_own_model_runs_on_the_turn_as_before(tmp_path):
    orch, gateway, tid, project, path = _mid_turn(tmp_path)

    for alias in (None, SONNET, SONNET_LABEL):
        gateway.batches.clear()
        reply = json.loads(_analyze(orch, tid, path, alias))
        assert reply["coverage"]["processed"] == 12, alias
        assert [b["model"] for b in gateway.batches] == [SONNET, SONNET], (alias, gateway.batches)
    requests = _events(orch, project, tid)[-1]["requests"]
    assert [r["serving_model"] for r in requests] == [None, None], requests


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
