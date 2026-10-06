"""#660: a rate-limited model call is tried again before anyone sees it, and named when it is not.

Dogfood, 2026-10-06: Gemini 3.7 Flash (Vertex) answered `{"code": 429, "status":
"RESOURCE_EXHAUSTED"}` inside a 200 stream and Gemini 3.8 Flash (OpenRouter) a `response.failed`
carrying a rate limit. Both turns died as `upstream_error`, when the upstream would have answered
a few seconds later.
"""
import json

import httpx
import pytest

from sage import transient
from sage.gateway.client import CostLabels, GatewayUpstreamError, OpenAICompatibleClient
from sage.gateway.events import StreamEvents
from sage.gateway.protocol import Protocol

from .test_native_model_controls import active, dispatch
from .test_native_model_controls import running as native_running

running = native_running

RATE_LIMITED = "The model is rate-limited; try again in a minute or pick another model."

SPELLINGS = {
    "int_code": {"code": 429, "message": "Resource exhausted. Please try again later."},
    "string_code": {"code": "429", "message": "Too many requests."},
    "rate_limit_error": {"type": "rate_limit_error", "message": "Rate limited."},
    "rate_limit_exceeded": {"code": "rate_limit_exceeded", "message": "Rate limit reached."},
    "resource_exhausted": {"code": 8, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded."},
}


def sse(event: dict) -> bytes:
    return b"data: " + json.dumps(event).encode() + b"\n\n"


# --- classification -------------------------------------------------------------------------

@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.CHAT, Protocol.RESPONSES])
def test_every_spelling_of_a_429_error_event_is_a_rate_limit(protocol, spelling):
    events = StreamEvents(protocol)
    events.feed(sse({"type": "error", "error": SPELLINGS[spelling]}))
    assert events.error == "rate_limit_error"


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_failed_response_carrying_a_rate_limit_is_a_rate_limit(spelling):
    events = StreamEvents(Protocol.RESPONSES)
    events.feed(sse({"type": "response.failed",
                     "response": {"status": "failed", "error": SPELLINGS[spelling]}}))
    assert events.error == "rate_limit_error"


def test_a_failed_response_for_another_reason_is_not_a_rate_limit():
    events = StreamEvents(Protocol.RESPONSES)
    events.feed(sse({"type": "response.failed",
                     "response": {"status": "failed", "error": {"code": "server_error"}}}))
    assert events.error == "response.failed"


# --- an HTTP 429 before the first byte, on every route ----------------------------------------

@pytest.fixture
def waits(monkeypatch):
    slept = []

    def wait(seconds, cancel=None):
        slept.append(seconds)
        return False

    monkeypatch.setattr(transient, "wait", wait, raising=False)
    return slept


def gateway(answers):
    seen = []

    def receive(request):
        seen.append(request)
        return answers.pop(0) if len(answers) > 1 else answers[0]

    client = OpenAICompatibleClient("https://gateway.example/apps/gw/v1", lambda: "token",
                                    domino_tags=True)
    client._http = httpx.Client(transport=httpx.MockTransport(receive))
    return client, seen


def call(client):
    return b"".join(client.route({"model": "m", "stream": True}, CostLabels("plan", "auto"),
                                 protocol=Protocol.RESPONSES))


def test_a_429_is_tried_again_after_the_wait_it_names(waits):
    client, seen = gateway([httpx.Response(429, headers={"Retry-After": "3"}, text="slow down"),
                            httpx.Response(200, content=b"reply")])
    try:
        assert call(client) == b"reply"
    finally:
        client.close()
    assert len(seen) == 2
    assert waits == [3.0]


def test_a_retry_after_beyond_the_budget_is_not_waited_for(waits):
    client, seen = gateway([httpx.Response(429, headers={"Retry-After": "600"}, text="slow down")])
    try:
        with pytest.raises(GatewayUpstreamError) as raised:
            call(client)
    finally:
        client.close()
    assert raised.value.status == 429
    assert len(seen) == 1 and waits == []


def test_a_429_that_never_clears_gives_up_inside_the_budget(waits):
    client, seen = gateway([httpx.Response(429, text="slow down")])
    try:
        with pytest.raises(GatewayUpstreamError) as raised:
            call(client)
    finally:
        client.close()
    assert raised.value.status == 429
    assert waits == list(transient.RATE_LIMIT_BACKOFF_S)
    assert sum(waits) <= transient.RATE_LIMIT_BUDGET_S
    assert len(seen) == len(transient.RATE_LIMIT_BACKOFF_S) + 1


# --- a rate limit inside a 200 stream, through the native endpoint ---------------------------

LANES = [("GLM 5.3 OR", Protocol.CHAT), ("Opus-4.8", Protocol.MESSAGES),
         ("gpt-5.4", Protocol.RESPONSES)]
# A frame that marks the start of a reply and shows the person nothing.
PREAMBLE_MARK = {Protocol.CHAT: b'"role"', Protocol.MESSAGES: b'"message_start"',
                 Protocol.RESPONSES: b'"response.created"'}


def _response(request):
    return {"store": False, "metadata": request["metadata"],
            "reasoning": request.get("reasoning", {}), "usage": {"output_tokens": 1}}


def preamble(protocol, request):
    if protocol is Protocol.RESPONSES:
        return [{"type": "response.created", "response": _response(request)}]
    if protocol is Protocol.MESSAGES:
        return [{"type": "message_start", "message": {"usage": {"input_tokens": 7}}}]
    return [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}]


def text(protocol):
    if protocol is Protocol.RESPONSES:
        return [{"type": "response.output_text.delta", "delta": "hello"}]
    if protocol is Protocol.MESSAGES:
        return [{"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "hello"}}]
    return [{"choices": [{"index": 0, "delta": {"content": "hello"}}]}]


def ending(protocol, request):
    if protocol is Protocol.RESPONSES:
        return [{"type": "response.completed", "response": _response(request)}]
    if protocol is Protocol.MESSAGES:
        return [{"type": "message_stop"}]
    return [{"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]


def limited(protocol, request):
    if protocol is Protocol.RESPONSES:
        return [{"type": "response.failed", "response": {
            **_response(request), "status": "failed",
            "error": {"code": "rate_limit_exceeded", "message": "Rate limit reached."}}}]
    return [{"type": "error", "error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                        "message": "Resource exhausted."}}]


def scripted(gateway, protocol, attempts):
    """Each call takes the next attempt; the last one repeats for as long as it is asked."""
    def route(request, labels, **kwargs):
        gateway.seen.append((request, labels))
        attempt = attempts.pop(0) if len(attempts) > 1 else attempts[0]
        if isinstance(attempt, Exception):
            raise attempt
        for event in attempt(protocol, request):
            yield sse(event)
    return route


def converse(running, monkeypatch, model, protocol, attempts):
    client, orch, gateway = running
    monkeypatch.setattr(gateway, "route", scripted(gateway, protocol, attempts))
    client.post("/api/project/model", json={"pick": model, "mode": "plan"})
    with active(orch) as headers:
        response = dispatch(client, headers, protocol, model)
    return response, orch._project, gateway


@pytest.mark.parametrize("model,protocol", LANES)
def test_a_rate_limit_before_anything_visible_is_retried_invisibly(
        running, monkeypatch, waits, model, protocol):
    attempts = [lambda p, r: preamble(p, r) + limited(p, r),
                lambda p, r: preamble(p, r) + text(p) + ending(p, r)]
    response, project, gateway = converse(running, monkeypatch, model, protocol, attempts)
    assert response.status_code == 200, response.text
    assert len(gateway.seen) == 2
    assert response.content.count(b"hello") == 1
    assert response.content.count(PREAMBLE_MARK[protocol]) == 1, response.text
    assert project.last_gateway_error is None


@pytest.mark.parametrize("model,protocol", LANES)
def test_a_rate_limit_after_text_was_shown_is_not_retried(
        running, monkeypatch, waits, model, protocol):
    attempts = [lambda p, r: preamble(p, r) + text(p) + limited(p, r)]
    response, project, gateway = converse(running, monkeypatch, model, protocol, attempts)
    assert len(gateway.seen) == 1
    assert response.content.count(b"hello") == 1
    assert project.last_gateway_error["message"] == RATE_LIMITED


@pytest.mark.parametrize("model,protocol", LANES)
def test_exhausted_retries_tell_the_person_it_is_a_rate_limit(
        running, monkeypatch, waits, model, protocol):
    attempts = [lambda p, r: preamble(p, r) + limited(p, r)]
    response, project, gateway = converse(running, monkeypatch, model, protocol, attempts)
    assert len(gateway.seen) == len(transient.RATE_LIMIT_BACKOFF_S) + 1
    assert sum(waits) <= transient.RATE_LIMIT_BUDGET_S
    assert project.last_gateway_error["message"] == RATE_LIMITED
    assert b"upstream_error" not in response.content


@pytest.mark.parametrize("model,protocol", LANES)
def test_an_http_429_that_outlasts_the_retries_is_named_a_rate_limit(
        running, monkeypatch, waits, model, protocol):
    attempts = [GatewayUpstreamError(429, "https://gateway.example", "Resource exhausted.")]
    _, project, _ = converse(running, monkeypatch, model, protocol, attempts)
    assert project.last_gateway_error["message"] == RATE_LIMITED
