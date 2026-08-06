"""Parallel tool calls, reshaped for the gateway's Bedrock adapter (see split_parallel_tool_calls).

Verified live 2026-08-06: routing an implement turn to bedrock-qwen3-coder after the agent had made
two reads in one assistant message got the whole request rejected —

    ValidationException: Expected toolResult blocks at messages.6.content for the following Ids: …

because the gateway emits one user message per tool result, and Bedrock wants them grouped into the
one message following the assistant turn. These pin the workaround and, just as importantly, that it
touches nothing else — every non-Bedrock model must keep seeing history exactly as OpenCode sent it.
"""
from __future__ import annotations

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim, split_parallel_tool_calls

CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b", sovereign_implement="sovereign-8b", sovereign_ask="sovereign-8b",
    plan="gpt-5.4", implement="bedrock-qwen3-coder", ask="sonnet",
)


def _call(cid: str, name: str) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}


def _history(*, n_calls: int, results: int | None = None) -> list[dict]:
    calls = [_call(f"call_{i}", "read") for i in range(n_calls)]
    got = n_calls if results is None else results
    return [
        {"role": "user", "content": "build it"},
        {"role": "assistant", "content": "reading first", "tool_calls": calls},
        *[{"role": "tool", "tool_call_id": f"call_{i}", "content": "file body"} for i in range(got)],
    ]


def test_parallel_calls_become_one_call_per_assistant_message():
    out = split_parallel_tool_calls(_history(n_calls=2))

    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "tool", "assistant", "tool"]
    # Each assistant turn now carries exactly one toolUse, immediately answered by its own result.
    assert [len(m["tool_calls"]) for m in out if m["role"] == "assistant"] == [1, 1]
    assert [m["tool_calls"][0]["id"] for m in out if m["role"] == "assistant"] == ["call_0", "call_1"]
    assert [m["tool_call_id"] for m in out if m["role"] == "tool"] == ["call_0", "call_1"]


def test_the_assistant_prose_is_not_repeated_across_fragments():
    # Duplicating it would show the model saying the same thing twice, and inflate every later prompt.
    out = split_parallel_tool_calls(_history(n_calls=3))
    assert [m["content"] for m in out if m["role"] == "assistant"] == ["reading first", None, None]


def test_a_single_tool_call_is_left_exactly_as_it_was():
    history = _history(n_calls=1)
    assert split_parallel_tool_calls(history) == history


def test_history_with_no_tool_calls_is_untouched():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert split_parallel_tool_calls(history) == history


def test_unanswered_calls_are_left_alone():
    """The in-flight turn: results haven't come back yet. Splitting here would emit a toolUse with no
    toolResult after it — precisely what Bedrock rejects — so this must not 'fix' anything."""
    history = _history(n_calls=2, results=1)
    assert split_parallel_tool_calls(history) == history


def test_shim_reshapes_history_only_for_bedrock_models():
    def sent_for(mode: Mode, phase: Phase) -> list[dict]:
        control = ModelControl(mode=mode, phase=phase)
        gw = FakeGatewayClient()
        shim = EnforcementShim(control, CATALOG, gw)
        list(shim.handle({"model": "gpt-5.4", "messages": _history(n_calls=2)}, project="p"))
        return gw.seen[-1][0]["messages"]

    # Implement -> catalog.implement is bedrock-qwen3-coder: reshaped.
    assert len([m for m in sent_for(Mode.IMPLEMENT, Phase.IMPLEMENT) if m["role"] == "assistant"]) == 2
    # Plan -> gpt-5.4: OpenAI handles parallel tool calls natively, so it must arrive untouched.
    assert len([m for m in sent_for(Mode.PLAN, Phase.PLAN) if m["role"] == "assistant"]) == 1
