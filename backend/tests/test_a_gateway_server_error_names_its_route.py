"""A gateway that fails inside itself says so, and says on which route (#663).

Live (2026-10-06): a Chat turn on `domino-gcp/claude-sonnet-5` died 11 s in on a stream error event
`{"message": "'list' object has no attribute 'get'", "type": "server_error"}`, and the same turn on
`sonnet` worked. The class was folded into `upstream_error`, so the person read "The model stream
failed: upstream_error" — no model, no route, nothing saying the fault was the gateway's.

The body stays out of the sentence, as every other stream error's does (#506): it rides back into
OpenCode's session history, and an upstream body can echo a prompt.
"""
from __future__ import annotations

import copy
import json

import pytest

from sage.gateway.protocol import Protocol

from .test_native_model_controls import RecordingGateway, active, dispatch, running  # noqa: F401

BODY = "'list' object has no attribute 'get'"
SAID = ("The model gateway failed inside itself while serving GLM 5.3 OR over /v1/chat/completions "
        "(server_error). This is a gateway-side error, not a refusal of the request; try again, "
        "or pick another model.")


class ServerErrorGateway(RecordingGateway):
    after_content = False

    def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
        self.seen.append((copy.deepcopy(request), labels))
        events = [{"error": {"message": BODY, "type": "server_error"}}]
        if self.after_content:
            events.insert(0, {"choices": [{"delta": {"content": "Looking"}}]})
        for event in events:
            yield b"data: " + json.dumps(event).encode() + b"\n\n"


@pytest.fixture
def failing(running):  # noqa: F811
    client, orch, _ = running
    gateway = ServerErrorGateway()
    orch._project.shim._gateway = gateway
    assert client.post("/api/project/model", json={"chat_model": "GLM 5.3 OR"}).status_code == 200
    return client, orch, gateway


def test_a_server_error_before_any_answer_names_the_route_and_the_gateway(failing):
    client, orch, _ = failing
    with active(orch, chat=True) as headers:
        response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")

    assert response.status_code == 502
    assert response.json() == {"error": {"message": SAID}}
    assert orch._project.last_gateway_error["message"] == SAID


def test_a_server_error_mid_answer_names_the_route_and_the_gateway(failing):
    client, orch, gateway = failing
    gateway.after_content = True
    with active(orch, chat=True) as headers:
        response = dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR")

    assert response.status_code == 200
    assert SAID in response.text
    assert BODY not in response.text
    assert orch._project.last_gateway_error["message"] == SAID
