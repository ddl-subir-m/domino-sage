"""The Ask-slot classifiers read effort and wire from the VERIFIED route (#593).

mimo-v2.6-pro is responses-native and has no row in the static effort table, so the intent
classifier sent it no effort over /chat/completions and it reasoned its 160-token cap away. Model
default now sends the lowest level the verified route accepts, over that route's own protocol; an
explicit Ask pick is still honoured exactly (#417); an unverified route is left as it was.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from sage import degraded
from sage.gateway.capabilities import evidence, resolve
from sage.gateway.protocol import Protocol
from sage.orchestrator import app as app_module
from sage.orchestrator import chat_intent, handoff

from .fake_opencode import Turn
from .test_chat_turn import _catalog, _orch

INTENT = json.dumps({"label": "plain_answer", "confidence": 0.93})


def _verified(name: str):
    proof = next(p for p in evidence() if p["name"] == name)
    capability = resolve(proof["gateway"], proof, evidence())
    assert capability.verified
    return capability


MIMO = _verified("mimo-v2.6-pro")
GLM = _verified("GLM 5.3 OR")
UNVERIFIED = resolve("https://elsewhere.example/v1", {"id": "x", "name": "mimo-v2.6-pro"}, evidence())


class RouteGateway:
    """Answers each protocol in its own wire shape, and records which one it was asked on."""

    def __init__(self, intent: str = INTENT):
        self.intent = intent
        self.seen: list = []
        self.seen_protocols: list[Protocol] = []

    def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
        self.seen.append((request, labels))
        self.seen_protocols.append(Protocol(protocol))
        verdict = self.intent if labels.component == "chat-intent" else "CHAT"
        if protocol is Protocol.RESPONSES:
            response = {"store": request.get("store"), "metadata": request.get("metadata", {}),
                        "reasoning": request.get("reasoning", {})}
            events = [{"type": "response.created", "response": response},
                      {"type": "response.output_text.delta", "delta": verdict},
                      {"type": "response.completed", "response": response}]
            # The live gateway closes a responses stream with the chat sentinel too (#593).
            yield b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events) + b"data: [DONE]\n\n"
            return
        assert protocol is Protocol.CHAT
        chunk = json.dumps({"choices": [{"delta": {"content": verdict}}]})
        yield f"data: {chunk}\n\ndata: [DONE]\n\n".encode()


def _intent(capability, effort, gateway, model="mimo-v2.6-pro"):
    return chat_intent.start(
        "What is regression?", context="", has_bound_context=False, gateway=gateway,
        catalog=replace(_catalog(), ask=model, ask_effort=effort),
        capability=lambda _model: capability).result()


def _handoff(capability, effort, gateway, model="mimo-v2.6-pro", locked=None):
    return handoff.wants_an_app(
        title="t", user="Explain regression", assistant="It fits a line.", gateway=gateway,
        catalog=replace(_catalog(), ask=model, ask_effort=effort), thread="thr_a",
        sensitivity=lambda _t: (locked, ""), capability=lambda _model: capability)


@pytest.fixture(autouse=True)
def _clean_health():
    handoff._health.reset()
    yield
    handoff._health.reset()


def test_intent_model_default_sends_the_lowest_verified_level_over_responses():
    gateway = RouteGateway()
    assert _intent(MIMO, None, gateway).valid
    request = gateway.seen[0][0]
    assert gateway.seen_protocols == [Protocol.RESPONSES]
    assert request["reasoning"] == {"effort": MIMO.efforts[0]} == {"effort": "none"}
    assert "reasoning_effort" not in request


@pytest.mark.parametrize("effort", ["low", "max"])
def test_intent_explicit_pick_is_sent_as_is(effort):
    gateway = RouteGateway()
    assert _intent(MIMO, effort, gateway).valid
    assert gateway.seen_protocols == [Protocol.RESPONSES]
    assert gateway.seen[0][0]["reasoning"] == {"effort": effort}


class SlowGateway(RouteGateway):
    """Answers after `delay`, the way a reasoning level answers after it has thought."""

    def __init__(self, delay: float):
        super().__init__()
        self.delay = delay

    def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
        time.sleep(self.delay)
        yield from super().route(request, labels, protocol=protocol, cancel=cancel)


@pytest.mark.parametrize("effort, cap", [(None, 160), ("none", 160), ("medium", 1024), ("max", 1024)])
def test_an_explicit_level_above_the_lowest_gets_room_to_reason_before_it_answers(effort, cap):
    """Measured live (#606): the Ask slot at `medium` spent mimo's 160-token cap on reasoning and
    ran past 5s, so every first turn fell back. The pick is still honoured (#417); it is given the
    room it needs rather than a cap only the lowest level fits in."""
    gateway = RouteGateway()
    assert _intent(MIMO, effort, gateway).valid
    assert gateway.seen[0][0]["max_output_tokens"] == cap


def test_an_explicit_level_above_the_lowest_waits_past_the_plain_deadline(monkeypatch):
    monkeypatch.setattr(chat_intent, "REASONING_TIMEOUT_S", 2.0)
    capability = lambda _model: MIMO
    catalog = replace(_catalog(), ask="mimo-v2.6-pro")

    def classify(effort):
        return chat_intent.start(
            "What is regression?", context="", has_bound_context=False, gateway=SlowGateway(0.5),
            catalog=replace(catalog, ask_effort=effort), capability=capability,
            timeout_s=0.2).result()

    assert classify("medium").valid, "a reasoning level is waited for past the plain deadline"
    assert classify("none").fallback == "timeout", "the lowest level keeps the plain deadline"
    assert classify(None).fallback == "timeout"


@pytest.mark.parametrize("effort, sent", [(None, "low"), ("high", "high"), ("none", None)])
def test_intent_on_a_verified_chat_route_uses_its_own_levels(effort, sent):
    gateway = RouteGateway()
    assert _intent(GLM, effort, gateway, model="GLM 5.3 OR").valid
    assert gateway.seen_protocols == [Protocol.CHAT]
    assert gateway.seen[0][0].get("reasoning_effort") == sent


@pytest.mark.parametrize("effort", [None, "low"])
def test_intent_on_an_unverified_route_sends_no_effort_over_chat(effort):
    gateway = RouteGateway()
    assert _intent(UNVERIFIED, effort, gateway).valid
    request = gateway.seen[0][0]
    assert gateway.seen_protocols == [Protocol.CHAT]
    assert not {"reasoning", "reasoning_effort"} & set(request)


@pytest.mark.parametrize("effort, sent", [(None, "none"), ("low", "low")])
def test_handoff_follows_the_verified_route(effort, sent):
    gateway = RouteGateway()
    assert _handoff(MIMO, effort, gateway) is False
    assert gateway.seen_protocols == [Protocol.RESPONSES]
    assert gateway.seen[0][0]["reasoning"] == {"effort": sent}


@pytest.mark.parametrize("effort", [None, "low"])
def test_handoff_on_an_unverified_route_sends_no_effort_over_chat(effort):
    gateway = RouteGateway()
    assert _handoff(UNVERIFIED, effort, gateway) is False
    assert gateway.seen_protocols == [Protocol.CHAT]
    assert not {"reasoning", "reasoning_effort"} & set(gateway.seen[0][0])


def test_a_sensitivity_move_takes_the_moved_models_route_and_default():
    asked = []
    gateway = RouteGateway()
    assert handoff.wants_an_app(
        title="t", user="Explain regression", assistant="It fits a line.", gateway=gateway,
        catalog=replace(_catalog(), ask="GLM 5.3 OR", ask_effort="max"), thread="thr_a",
        sensitivity=lambda _t: ("mimo-v2.6-pro", ""),
        capability=lambda model: asked.append(model) or MIMO) is False
    assert asked == ["mimo-v2.6-pro"]
    assert gateway.seen_protocols == [Protocol.RESPONSES]
    assert gateway.seen[0][0]["reasoning"] == {"effort": "none"}


HAIKU = _verified("haiku")
# Haiku's live reply on the messages route, which has no JSON mode: the label, fenced, then prose.
HAIKU_REPLY = ('```json\n{"label":"plain_answer","confidence":0.95}\n```\n\n'
               "This is a general statistical/educational question asking for an explanation.")


class MessagesGateway:
    def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
        assert protocol is Protocol.MESSAGES
        events = [
            ("message_start", {"type": "message_start", "message": {
                "model": "claude-haiku-4-5", "usage": {"input_tokens": 345, "output_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": HAIKU_REPLY}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                               "usage": {"output_tokens": 40}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        yield b"".join(f"event: {name}\ndata: {json.dumps(e)}\n\n".encode() for name, e in events)


def test_intent_reads_the_verdict_haiku_fences_on_the_messages_route():
    intent = _intent(HAIKU, None, MessagesGateway(), model="haiku")
    assert (intent.label, intent.confidence, intent.fallback) == ("plain_answer", 0.95, "")


def test_a_chat_turn_hands_both_classifiers_the_orchestrators_route(tmp_path, monkeypatch):
    # Low confidence leaves the turn unbounded, which is what lets the handoff classify run too.
    gateway = RouteGateway(json.dumps({"label": "plain_answer", "confidence": 0.2}))
    orch, _ = _orch(tmp_path, [Turn(text="An explanation.")], gateway=gateway)
    asked = []
    monkeypatch.setattr(orch, "route_capability", lambda model: asked.append(model) or MIMO)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "Explain compound interest."))
    components = [labels.component for _, labels in gateway.seen]
    assert components == ["chat-intent", "handoff"]
    assert asked == [_catalog().ask] * 2
    assert gateway.seen_protocols == [Protocol.RESPONSES] * 2
    assert all(req["reasoning"] == {"effort": "none"} for req, _ in gateway.seen)


def test_diag_keeps_the_last_degraded_turns_count_after_the_next_grant(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "opencode").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SAGE_CONTROL_PORT", "1")
    monkeypatch.setattr(degraded, "_count", 0)
    monkeypatch.setattr(degraded, "_last", 0)

    def diag() -> dict:
        r = TestClient(app_module.control_app).get("/api/diag")
        assert r.status_code == 200, r.text
        return r.json()

    degraded.judgement_lost()  # the offer-card turn lost its classify, then ended
    degraded.reset()  # the replay turn is granted
    body = diag()
    assert body["classifier_degradations"] == 0
    assert body["classifier_degradations_last_degraded_turn"] == 1
    degraded.reset()  # a clean turn in between does not erase it
    assert diag()["classifier_degradations_last_degraded_turn"] == 1
