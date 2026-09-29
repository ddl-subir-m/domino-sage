"""#606: `analyze_text` judges at a reasoning level of its own, never the picker's.

Measured 2026-09-29: with the picker at Low on mimo, the judging batches went through `shim.handle`
and inherited that Low, while a chipped judging model got no level at all. The picker's level was
chosen for the picker's model; the person names a judging level in the prompt, or it is low.
"""

from __future__ import annotations

import json

import pytest

from sage import timing
from sage.gateway.capabilities import RouteCapability
from sage.gateway.client import CostLabels
from sage.gateway.protocol import Protocol
from sage.liveread import run
from sage.orchestrator.service import _timed_batch
from sage.shim.native import text_stream

from .test_analyze_text_honours_its_alias import OPUS, SONNET, _events, _mid_turn, _no_real_poll  # noqa: F401
from .test_csv_text_analysis_data_used import analysis_args, labels_for, setup_turn, sse

LEVELS = RouteCapability(efforts=("none", "low", "high"), verified=True)


def _judging(tmp_path, capability=LEVELS, picker="high"):
    orch, gateway, tid, project, path = _mid_turn(tmp_path)
    project.shim.resolve_capability = lambda _model: capability
    project.control.pick_chat(SONNET, picker)

    def analyze(**over):
        return run.perform("live_read_files", analysis_args(path=path, batch_size=6, **over),
                           orch._live_read_turn_for(tid))
    return analyze, gateway, orch, project, tid


@pytest.mark.parametrize("alias, ran_on", [(None, SONNET), (OPUS, OPUS)])
def test_an_unnamed_level_judges_at_low_whatever_the_picker_says(tmp_path, alias, ran_on):
    analyze, gateway, orch, project, tid = _judging(tmp_path)

    reply = json.loads(analyze(alias=alias))

    assert reply["coverage"]["processed"] == 12
    assert [(b["model"], b.get("reasoning_effort")) for b in gateway.batches] == [(ran_on, "low")] * 2
    requests = _events(orch, project, tid)[0]["requests"]
    assert [r["reasoning_effort"] for r in requests] == ["low", "low"]


@pytest.mark.parametrize("alias, ran_on", [(None, SONNET), (OPUS, OPUS)])
def test_a_named_level_the_model_takes_is_sent_as_named(tmp_path, alias, ran_on):
    analyze, gateway, *_ = _judging(tmp_path, picker="low")

    json.loads(analyze(alias=alias, effort=" High "))

    assert [(b["model"], b.get("reasoning_effort")) for b in gateway.batches] == [(ran_on, "high")] * 2


def test_a_named_level_the_model_does_not_take_is_refused_naming_the_ones_it_does(tmp_path):
    analyze, gateway, orch, project, tid = _judging(tmp_path)

    said = analyze(effort="xhigh")

    assert said == (f"{SONNET} can't judge at reasoning effort 'xhigh'. It takes none, low, high. "
                    "Ask the person which to use. No text was analyzed."), said
    assert gateway.batches == []
    assert _events(orch, project, tid) == []


def test_a_model_with_no_levels_judges_at_its_default_and_refuses_a_named_one(tmp_path):
    analyze, gateway, *_ = _judging(tmp_path, capability=RouteCapability(verified=True))

    json.loads(analyze())
    assert [b.get("reasoning_effort") for b in gateway.batches] == [None, None]

    gateway.batches.clear()
    said = analyze(effort="low")
    assert "It offers no reasoning level here." in said, said
    assert gateway.batches == []


def test_the_batch_is_on_the_ledger_with_the_level_it_ran_at(tmp_path):
    analyze, *_ = _judging(tmp_path)
    timing.start_turn("chat", "classify these complaints")
    try:
        json.loads(analyze())
    finally:
        record = timing.finish_turn(ok=True, decision="-")

    batches = [c for c in record.calls if c.phase == "text-analysis"]
    assert [(c.effective_effort, c.effort_source) for c in batches] == [("low", "stage_default")] * 2


@pytest.mark.parametrize("named, sent", [(None, None), ("auto", None), ("Default", None),
                                         ("LOW", "low")])
def test_the_named_level_reaches_the_gate_normalised(tmp_path, named, sent):
    asked = []

    def gate(serving, level):
        asked.append((serving, level))
        return level, ""

    seen = []

    def provider(request):
        seen.append(request)
        return sse(json.dumps(labels_for(request)))

    turn, *_ = setup_turn(tmp_path, provider=provider, text_effort_for=gate)
    json.loads(run.perform("live_read_files", analysis_args(effort=named), turn))

    assert asked == [("", sent)]
    assert {r.get("reasoning_effort") for r in seen} == {sent}


def test_a_named_level_with_no_gate_wired_is_refused(tmp_path):
    def provider(_request):
        raise AssertionError("model called")

    turn, _data, journal, _source = setup_turn(tmp_path, provider=provider)

    said = run.perform("live_read_files", analysis_args(effort="high"), turn)

    assert said == "The reasoning effort 'high' could not be checked here, so no text was analyzed."
    assert journal == []


def test_the_default_batch_is_twenty_records(tmp_path):
    rows = "ticket,complaint\n" + "".join(f"T-{i},Package {i} arrived late\n" for i in range(45))
    seen = []

    def provider(request):
        seen.append(len(json.loads(request["messages"][1]["content"])["records"]))
        return sse(json.dumps(labels_for(request)))

    turn, *_ = setup_turn(tmp_path, content=rows, provider=provider)
    json.loads(run.perform("live_read_files", analysis_args(batch_size=None), turn))

    assert sorted(seen) == [5, 20, 20]


class ResponsesGateway:
    def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
        response = {"store": request.get("store"), "metadata": request.get("metadata", {}),
                    "reasoning": request.get("reasoning", {}),
                    "usage": {"input_tokens": 900, "output_tokens": 70,
                              "output_tokens_details": {"reasoning_tokens": 50}}}
        events = [{"type": "response.created", "response": response},
                  {"type": "response.output_text.delta", "delta": "{}"},
                  {"type": "response.completed", "response": response}]
        yield b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events) + b"data: [DONE]\n\n"


def test_a_native_batch_puts_its_tokens_on_the_ledger(tmp_path):
    """Only text deltas cross `text_stream`, so without the stop frame's usage a long think read
    as a slow first byte and nothing else."""
    capability = RouteCapability(protocol=Protocol.RESPONSES, efforts=("low",), verified=True)
    request = {"model": "mimo", "stream": True, "reasoning_effort": "low",
               "messages": [{"role": "user", "content": "judge"}]}
    labels = CostLabels(phase="ask", mode="auto", component="chat-delegated")
    timing.start_turn("chat", "judge")
    try:
        call = timing.model_call("mimo", "text-analysis")
        list(_timed_batch(text_stream(ResponsesGateway(), request, labels, capability), call))
    finally:
        record = timing.finish_turn(ok=True, decision="-")

    (batch,) = record.calls
    assert (batch.input_tokens, batch.output_tokens, batch.reasoning_tokens) == (900, 70, 50)
