"""The shim half: content a Conversation has stopped sending never reaches the gateway.

Measured 2026-09-11: replacing a tool result's content is accepted by gpt-5.4, sonnet, haiku and
gemini; dropping the message while its `tool_call` stays behind is a 400 on all four. These tests
hold that shape, and hold the rule that makes Chat and Build one code path — the filter keys on the
armed set, never on which surface armed it.
"""
from __future__ import annotations

import pytest

from sage.gateway.client import FakeGatewayClient, GatewayUpstreamError
from sage.router.model_control import ModelControl
from sage.router.models import Mode, Phase
from sage.shim.chat_paths import file_key, text_key
from sage.shim.enforcement import EnforcementShim

from .test_enforcement_shim import CATALOG


def _shim(control: ModelControl, gw: FakeGatewayClient) -> EnforcementShim:
    return EnforcementShim(control, CATALOG, gw)


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "read", "arguments": '{"filePath": "/mnt/code/raw.csv"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "name,ssn\nJ Doe,222-33-4444"},
        {"role": "user", "content": "and my note: 4871715921430428"},
    ]


def _sent(control: ModelControl, messages: list[dict]) -> list[dict]:
    gw = FakeGatewayClient()
    list(_shim(control, gw).handle({"messages": messages, "tools": []}, project="p"))
    return gw.seen[-1][0]["messages"]


def test_a_withheld_file_is_replaced_not_removed():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_withheld({file_key("raw.csv")})
    sent = _sent(control, _messages())
    assert len(sent) == len(_messages()), "a message was dropped; that is a 400 on every provider"
    result = next(m for m in sent if m.get("role") == "tool")
    assert result["tool_call_id"] == "t1", "the pairing must survive"
    assert "222-33-4444" not in result["content"]
    assert "raw.csv" in result["content"]


def test_it_applies_with_no_chat_thread_armed_because_build_has_none():
    """The whole reason the filter sits outside the `chat_id` guard."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.IMPLEMENT)
    control.arm_withheld({file_key("raw.csv")})
    assert control.snapshot().chat_thread_id is None
    sent = _sent(control, _messages())
    assert "222-33-4444" not in str(sent)


def test_pasted_text_is_withheld_by_fingerprint():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    note = _messages()[4]
    control.arm_withheld({text_key(note)})
    sent = _sent(control, _messages())
    assert "4871715921430428" not in str(sent)
    assert "222-33-4444" in str(sent), "only the armed carrier is withheld"


def test_nothing_armed_changes_nothing():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    assert _sent(control, _messages()) == _messages()


def test_the_pin_does_not_outlive_its_turn():
    """Unkeyed, the set would withhold another Conversation's content — a silent wrong answer."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    token = control.arm_withheld({file_key("raw.csv")})
    assert control.snapshot().withheld
    control.disarm_withheld(token)
    assert not control.snapshot().withheld
    assert _sent(control, _messages()) == _messages()


def test_a_superseded_turn_cannot_unpin_the_one_that_replaced_it():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    stale = control.arm_withheld({file_key("old.csv")})
    control.arm_withheld({file_key("raw.csv")})
    control.disarm_withheld(stale)
    assert control.snapshot().withheld == frozenset({file_key("raw.csv")})


def test_the_fingerprint_is_stable_across_two_consecutive_requests():
    """The key is re-derived per request, so an unstable hash would silently stop withholding."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_withheld({text_key(_messages()[4])})
    first = _sent(control, _messages())
    second = _sent(control, _messages())
    assert first == second
    assert "4871715921430428" not in str(second)


class _Refuses:
    """A gateway that refuses the way the live one does: 400 with the guardrail's own 93 bytes."""
    BODY = ('{"detail":{"error":{"message":"Blocked by guardrail: Block PII",'
            '"type":"guardrail_blocked"}}}')

    def __init__(self, body: str = BODY):
        self.body = body

    def route(self, request, labels):
        raise GatewayUpstreamError(400, "https://gw/v1/chat/completions", self.body)
        yield b""          # pragma: no cover - makes route a generator, as the real one is

    def guardrail_events(self):
        raise NotImplementedError


def _drain(control: ModelControl, gw, **kw):
    seen: list = []
    try:
        list(_shim(control, gw).handle({"messages": _messages(), "tools": []}, project="p",
                                       on_refused=lambda model, msgs: seen.append((model, msgs)), **kw))
    except GatewayUpstreamError:
        pass
    return seen


def test_a_guardrail_refusal_hands_back_what_it_refused():
    """The one moment the payload and the refusal are both in hand."""
    captured = _drain(ModelControl(mode=Mode.AUTO, phase=Phase.PLAN), _Refuses())
    assert len(captured) == 1
    model, msgs = captured[0]
    assert model, "the alias matters: Block PII is on gpt-5.4 alone"
    assert any("222-33-4444" in str(m.get("content")) for m in msgs)


def test_the_captured_payload_is_the_rewritten_one():
    """It must be what the gateway actually saw, not what OpenCode sent — a search over the wrong
    list would name a carrier that is already being withheld."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_withheld({file_key("raw.csv")})
    captured = _drain(control, _Refuses())
    assert "222-33-4444" not in str(captured[0][1]), "the capture ran before the withhold was applied"


def test_any_other_failure_never_reaches_the_callback():
    """An auth failure or a bad model id is not ours to explain, and holding its payload would be
    retaining data for no reason at all."""
    body = '{"detail":{"error":{"message":"invalid api key","type":"invalid_request_error"}}}'
    assert _drain(ModelControl(mode=Mode.AUTO, phase=Phase.PLAN), _Refuses(body)) == []


def test_a_callback_that_raises_does_not_break_the_turn():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)

    def boom(_model, _messages):
        raise RuntimeError("no")

    with pytest.raises(GatewayUpstreamError):
        list(_shim(control, _Refuses()).handle({"messages": _messages(), "tools": []},
                                               project="p", on_refused=boom))
