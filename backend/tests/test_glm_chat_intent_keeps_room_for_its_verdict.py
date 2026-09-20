"""The live GLM classifier spent its 160-token cap on reasoning and emitted no verdict (#417)."""
import json
from dataclasses import replace

import pytest

from sage.orchestrator import chat_intent

from .test_chat_turn import IntentGateway, _catalog


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

    result = chat_intent.start("hi", context="", has_bound_context=False,
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
