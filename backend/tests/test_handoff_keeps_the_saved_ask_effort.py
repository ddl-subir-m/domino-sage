"""The post-answer classifier must use the effort saved for its Ask model (#417)."""
from dataclasses import replace

import pytest

from sage.orchestrator import handoff

from .test_handoff import CATALOG, StubGateway


@pytest.mark.parametrize("model, effort, locked, expected", [
    ("GLM 5.3 OR", "low", None, "low"),
    ("sage-gateway/GLM 5.3 OR", "high", None, "high"),
    ("GLM 5.3 OR", "max", "GLM 5.3 OR", "max"),
    ("GLM 5.3 OR", None, None, None),
    ("GLM 5.3 OR", "xhigh", None, None),
    ("sonnet", "low", None, None),
    # Even another model that accepts Low must not inherit this model's assignment.
    ("GLM 5.3 OR", "low", "gpt-5.4", None),
])
def test_handoff_effort_follows_the_ask_assignment_only_while_its_model_runs(
        model, effort, locked, expected, monkeypatch):
    monkeypatch.setattr(handoff, "_health", handoff._Health())
    gateway = StubGateway("CHAT")
    assert not handoff.wants_an_app(
        title="hi", user="hi", assistant="Hello", gateway=gateway,
        catalog=replace(CATALOG, ask=model, ask_effort=effort), thread="thr_greeting",
        sensitivity=lambda _: (locked, ""),
    )
    request = gateway.seen[0][0]
    assert request["model"] == (locked or model)
    if expected is None:
        assert "reasoning_effort" not in request
    else:
        assert request["reasoning_effort"] == expected
