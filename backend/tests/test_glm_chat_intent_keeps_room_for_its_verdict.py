"""The live GLM classifier spent its 160-token cap on reasoning and emitted no verdict (#417)."""
import json
from dataclasses import replace

import pytest

from sage.orchestrator import chat_intent

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, _catalog, _orch


@pytest.mark.parametrize("model", ["GLM 5.3 OR", "sage-gateway/GLM 5.3 OR"])
def test_glm_intent_can_return_its_verdict_within_the_existing_cap(model):
    class CappedGateway:
        def route(self, request, labels):
            # Replay the observed distinction: default spends the cap before visible content;
            # low returns the small JSON verdict. Do not recover a verdict from reasoning text.
            assert request["model"] == model
            assert request["max_tokens"] == 160
            content = ('{"label":"other_chat","confidence":0.9}'
                       if request.get("reasoning_effort") == "low" else "")
            chunk = {"choices": [{"delta": {"content": content},
                                  "finish_reason": "stop" if content else "length"}]}
            yield f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()

    result = chat_intent.start("Tell me a joke", context="", has_bound_context=False,
                              gateway=CappedGateway(),
                              catalog=replace(_catalog(), ask=model, ask_effort="low")).result()
    assert result.valid
    assert result.label == "other_chat"


@pytest.mark.parametrize("model, effort", [
    ("GLM 5.3 OR", None), ("GLM 5.3 OR", "xhigh"),
    ("sonnet", "low"), ("gpt-5.4", None), ("unknown-glm", "low"),
])
def test_default_or_unsupported_classifier_effort_sends_no_override(model, effort):
    gateway = IntentGateway({"label": "plain_answer", "confidence": 0.9})
    assert chat_intent.start("What is regression?", context="", has_bound_context=False,
                             gateway=gateway,
                             catalog=replace(_catalog(), ask=model, ask_effort=effort)).result().valid
    assert "reasoning_effort" not in gateway.seen[0][0]


@pytest.mark.parametrize("model, effort", [
    ("GLM 5.3 OR", "high"), ("GLM 5.3 OR", "max"), ("gpt-5.4", "none"),
])
def test_classifier_keeps_another_supported_explicit_ask_effort(model, effort):
    gateway = IntentGateway({"label": "plain_answer", "confidence": 0.9})
    result = chat_intent.start("What is regression?", context="", has_bound_context=False,
                              gateway=gateway,
                              catalog=replace(_catalog(), ask=model, ask_effort=effort)).result()
    assert result.valid
    assert gateway.seen[0][0]["reasoning_effort"] == effort


@pytest.mark.parametrize("prompt", ["hi", "hello", "hey", " HI! ", "Hello.", "hello?!"])
@pytest.mark.parametrize("bound", [False, True])
def test_a_whole_message_greeting_needs_no_classifier_call(prompt, bound):
    gateway = IntentGateway({"label": "other_chat", "confidence": 0.99})
    result = chat_intent.start(
        prompt, context="table MIXPANEL__EVENT" if bound else "",
        has_bound_context=bound, gateway=gateway, catalog=_catalog()).result()
    assert gateway.seen == []
    assert result.valid and result.label == "plain_answer"


@pytest.mark.parametrize("prompt", [
    "hi, count the users", "hello\nbuild a dashboard", "say hello", "@hi",
    "high usage", "thanks, continue", "yes", "hello.csv", '"hi"',
])
def test_a_greeting_word_does_not_hide_the_rest_of_the_request(prompt):
    gateway = IntentGateway({"label": "build_app", "confidence": 0.9})
    result = chat_intent.start(prompt, context="table MIXPANEL__EVENT", has_bound_context=True,
                              gateway=gateway, catalog=_catalog()).result()
    assert result.label == "build_app"
    assert len(gateway.seen) == 1
    assert f"User: {prompt}" in gateway.seen[0][0]["messages"][1]["content"]


def test_greetings_still_reach_chat_and_keep_both_turns(tmp_path):
    gateway = IntentGateway({"label": "other_chat", "confidence": 0.99})
    orch, oc = _orch(tmp_path, [Turn(text="Hi!"), Turn(text="Hello again!")], gateway=gateway)
    tid = orch.create_thread()["id"]
    replies = []
    for prompt in ("hi", "hello"):
        events = list(orch.chat_stream(tid, prompt))
        replies.extend(e["text"] for e in events if e.get("type") == "agent")
        assert next(e for e in events if e["type"] == "done")["ok"]
    assert replies == ["Hi!", "Hello again!"]
    assert len(oc.sessions) == 1
    assert len(oc.prompts) == 2
    assert all(p["agent"] == "sage-chat" for p in oc.prompts)
    assert gateway.seen == [], "neither intent nor handoff needs a model for a greeting"
    history = orch.get_thread(tid)["history"]
    assert [e["text"] for e in history if e["type"] == "user"] == ["hi", "hello"]


def test_only_the_greeting_omits_tools_and_the_next_question_keeps_them(tmp_path, monkeypatch):
    from sage.gateway.client import FakeGatewayClient
    from sage.shim.enforcement import EnforcementShim

    gateway = IntentGateway({"label": "data_answer", "confidence": 0.99})
    orch, oc = _orch(tmp_path, [Turn(text="Hi!"), Turn(text="42 users")], gateway=gateway)
    control = orch.project(start_preview=False).control
    upstream = FakeGatewayClient()
    shim = EnforcementShim(control, _catalog(), upstream)
    request = {
        "messages": [{"role": "user", "content": "Keep the conversation context"}],
        "tools": [{"type": "function", "function": {"name": name}}
                  for name in ("read", "skill", "live_read_query", "delegated_model_call")],
        "tool_choice": "auto", "max_tokens": 32000,
    }
    send_prompt = oc.send_prompt

    def send_with_shim(*args, **kwargs):
        list(shim.handle(request, project="Sage"))
        send_prompt(*args, **kwargs)

    monkeypatch.setattr(oc, "send_prompt", send_with_shim)
    tid = orch.create_thread()["id"]
    for prompt in (" HI! ", "How many distinct users are there?"):
        events = list(orch.chat_stream(tid, prompt))
        assert next(e for e in events if e["type"] == "done")["ok"]
        assert not control.snapshot().read_only_turn
    greeting, question = [req for req, _ in upstream.seen]
    assert "tools" not in greeting
    assert "tool_choice" not in greeting
    assert question["tools"] == request["tools"]
    assert question["tool_choice"] == "auto"
    assert all(req["messages"] == request["messages"] for req in (greeting, question))
    assert all(req["max_tokens"] == 32000 for req in (greeting, question))
    assert len(oc.sessions) == 1


@pytest.mark.parametrize("chat, reason, armed", [
    (False, "greeting", True), (True, "question", True),
    (True, "plan", True), (True, "greeting", False),
])
def test_tool_omission_requires_an_active_chat_greeting(chat, reason, armed):
    from sage.gateway.client import FakeGatewayClient
    from sage.router.model_control import ModelControl
    from sage.router.models import Mode, Phase
    from sage.shim.enforcement import EnforcementShim

    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    if chat:
        control.arm_chat("thr_greeting")
    token = control.arm_read_only(reason)
    if not armed:
        control.disarm_read_only(token)
    upstream = FakeGatewayClient()
    tools = [{"type": "function", "function": {"name": "read"}}]
    list(EnforcementShim(control, _catalog(), upstream).handle(
        {"messages": [], "tools": tools, "tool_choice": "auto"}, project="Sage"))
    assert upstream.seen[-1][0]["tools"] == tools
