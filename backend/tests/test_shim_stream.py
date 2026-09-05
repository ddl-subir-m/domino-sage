"""Streaming behavior of BOTH chat endpoints: the live orchestrator `control_app` one (what OpenCode
actually hits at runtime) and the standalone shim `app`. They share sage.shim.keepalive, so both must
keep OpenCode's fetch alive through gpt-5.4's silent thinking gaps and end a broken stream readably.

The failure this guards: the endpoint used to pull the first byte before returning the response, so a
model that thought for minutes withheld all headers/body -> OpenCode's Node fetch aborted with
"TypeError: network error". Now the response commits early and emits SSE keepalive comments during
silent gaps; a mid-stream upstream break becomes a readable assistant message + clean [DONE].
"""
from __future__ import annotations

import time
import types

from fastapi.testclient import TestClient

import sage.orchestrator.app as orchmod
import sage.shim.app as shimmod
import sage.shim.keepalive as ka
from sage.gateway.client import GatewayUpstreamError


def _fast(monkeypatch):
    # Tiny timers so silent-gap tests run in milliseconds, not the 8s/15s production budgets.
    monkeypatch.setattr(ka, "FIRST_BYTE_BUDGET_S", 0.05)
    monkeypatch.setattr(ka, "KEEPALIVE_INTERVAL_S", 0.05)


def _post(client):
    return client.post("/v1/chat/completions", json={"model": "gpt-5.4", "messages": []})


# ---- live path: orchestrator control_app (what OpenCode hits at runtime) --------------------------

def _fake_project(gen_factory):
    shim = types.SimpleNamespace(handle=lambda body, project, session=None: gen_factory())
    return types.SimpleNamespace(
        id="p", session_id="s", active_session_id=None, shim=shim,
        model_calls=0, tool_call_responses=0, last_gateway_error=None,
    )


def _control_client(monkeypatch, gen_factory):
    proj = _fake_project(gen_factory)
    monkeypatch.setattr(orchmod, "orchestrator", types.SimpleNamespace(project=lambda: proj))
    return TestClient(orchmod.control_app), proj


def test_control_app_emits_keepalive_during_a_silent_gap(monkeypatch):
    _fast(monkeypatch)

    def gen():
        time.sleep(0.2)  # model "thinks" past the budget and a keepalive interval
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'

    client, _ = _control_client(monkeypatch, gen)
    body = _post(client).text
    assert ": keepalive" in body          # connection kept warm during the silent gap
    assert '"content":"hi"' in body       # the real chunk still arrives intact afterwards


def test_control_app_mid_stream_break_is_readable_not_a_raw_truncation(monkeypatch):
    def gen():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise RuntimeError("RemoteProtocolError: peer closed connection")

    client, proj = _control_client(monkeypatch, gen)
    resp = _post(client)
    assert resp.status_code == 200                        # already committed to the stream
    assert '"content":"partial"' in resp.text             # the bytes we did get are preserved
    assert "closed the stream mid-response" in resp.text  # readable graceful ending
    assert "data: [DONE]" in resp.text
    assert proj.last_gateway_error is not None            # telemetry recorded the break


def test_control_app_fast_upstream_error_still_returns_502(monkeypatch):
    def gen():
        raise GatewayUpstreamError(401, "http://gw/v1/chat/completions", "unauthorized")
        yield  # pragma: no cover - generator marker

    client, proj = _control_client(monkeypatch, gen)
    resp = _post(client)
    assert resp.status_code == 502
    assert resp.json()["error"]["upstream_status"] == 401
    assert proj.model_calls == 1


# ---- standalone shim app (shares the same keepalive logic) ---------------------------------------

def _shim_client(monkeypatch, gen_factory):
    fake = types.SimpleNamespace(handle=lambda body, project, session=None: gen_factory())
    monkeypatch.setattr(shimmod, "_shim", fake)
    return TestClient(shimmod.app)


def test_diag_reports_rev_ports_and_logs_without_starting_a_project():
    client = TestClient(orchmod.control_app)
    r = client.get("/api/diag")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"sage_rev", "gateway_mode", "ports", "project", "log_tail", "opencode_log_tail"}
    assert set(body["ports"]) == {"control_port", "base_port", "match"}


def test_shim_app_mid_stream_break_is_readable(monkeypatch):
    def gen():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise RuntimeError("boom")

    client = _shim_client(monkeypatch, gen)
    resp = _post(client)
    assert resp.status_code == 200
    assert "closed the stream mid-response" in resp.text
    assert "data: [DONE]" in resp.text


def test_control_app_surfaces_a_provider_error_returned_as_a_200_stream(monkeypatch):
    """The Bedrock failure, verified live 2026-08-06: a rejected request comes back as HTTP 200 with a
    single `data: {"error": …}` frame and no [DONE]. Nothing raises, so before this the frame was
    forwarded verbatim and OpenCode died on "Invalid ... stream event" with no payload — the turn just
    stopped, `last_gateway_error` stayed None, and it took raw chunk logging to find out why."""
    err = (b'data: {"error": {"message": "Expected toolResult blocks at messages.6.content", '
           b'"aws_error_type": "ValidationException"}}\n\n')

    def gen():
        yield err

    client, proj = _control_client(monkeypatch, gen)
    resp = _post(client)

    assert resp.status_code == 200                          # already committed to the stream
    assert "Expected toolResult blocks" in resp.text        # the provider's reason reaches the user
    assert "data: [DONE]" in resp.text                      # ...and the turn closes cleanly
    assert '"error"' not in resp.text                       # the unparseable frame is NOT forwarded
    assert proj.last_gateway_error is not None              # telemetry recorded it


def test_upstream_error_ignores_ordinary_chunks():
    assert ka.upstream_error(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n') is None
    assert ka.upstream_error(b"data: [DONE]\n\n") is None
    assert ka.upstream_error(b": keepalive\n\n") is None


def test_upstream_error_still_reads_a_frame_whose_content_mentions_the_word_error():
    """The fast reject added for the per-chunk check keys on the literal `"error"`, which a model's
    own prose can contain too. Rejecting cheaply must not start rejecting real frames — nor must a
    content delta that merely says "error" get mistaken for one."""
    assert ka.upstream_error(b'data: {"choices":[{"delta":{"content":"an \\"error\\" here"}}]}\n\n') is None
    assert ka.upstream_error(b'data: {"error": {"message": "real"}}\n\n') == "real"


# ---- the frame that arrives AFTER the first-byte budget ------------------------------------------

def test_an_error_frame_that_arrives_after_the_budget_is_still_readable(monkeypatch):
    """The Gemini/GCP failure, read out of /api/diag on 2026-09-04.

    `upstream_error` was applied only to the eagerly-pulled `first` chunk, so the gateway's error
    frame was recognised only when the provider failed inside FIRST_BYTE_BUDGET_S. Live, the log
    correlated perfectly: every turn that logged "first byte 8.0s, pending; keepalive engaged" died
    as "Invalid sage-gateway/openai-compatible-chat stream event", and every turn whose first byte
    beat the budget got a readable message from the same gateway defect. The frame below is verbatim
    from that capture — a gateway-side Python AttributeError relayed as a 200.

    The 0.2s sleep is what makes this the real case: it pushes the frame past the (patched) budget,
    so `first is EMPTY`, the stream commits, and the frame arrives inside the loop.
    """
    err = b'data: {"error": {"message": "\'list\' object has no attribute \'get\'", "type": "server_error"}}\n\n'

    def gen():
        time.sleep(0.2)   # the model thinks for longer than the first-byte budget
        yield err

    _fast(monkeypatch)
    client, proj = _control_client(monkeypatch, gen)
    resp = _post(client)

    assert resp.status_code == 200
    assert "'list' object has no attribute 'get'" in resp.text   # the gateway's reason reaches the user
    assert "data: [DONE]" in resp.text                           # ...and the turn closes cleanly
    assert '"type": "server_error"' not in resp.text             # the unparseable frame is NOT forwarded
    assert proj.last_gateway_error is not None                   # telemetry recorded it


def test_a_good_chunk_before_a_late_error_frame_is_kept(monkeypatch):
    # The check now runs mid-stream, so it must end the stream without eating what already streamed.
    def gen():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        yield b'data: {"error": {"message": "provider gave up"}}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"never"}}]}\n\n'

    client, _ = _control_client(monkeypatch, gen)
    resp = _post(client)

    assert '"content":"partial"' in resp.text    # what arrived before the failure is preserved
    assert "provider gave up" in resp.text       # the reason is rendered
    assert "never" not in resp.text              # and nothing after the error frame is forwarded


def test_shim_app_surfaces_a_late_error_frame_too(monkeypatch):
    # The standalone shim checked for an error frame nowhere at all, first chunk included.
    def gen():
        time.sleep(0.2)
        yield b'data: {"error": {"message": "Function call is missing a thought_signature"}}\n\n'

    _fast(monkeypatch)
    client = _shim_client(monkeypatch, gen)
    resp = _post(client)

    assert resp.status_code == 200
    assert "missing a thought_signature" in resp.text
    assert "data: [DONE]" in resp.text


# ---- what the tool calls look like on the way OUT (#155) ----------------------------------------

def _batch(*sigs):
    """One assistant message holding len(sigs) tool calls; a None entry means that call is unsigned."""
    calls = []
    for n, sig in enumerate(sigs):
        c = {"id": f"c{n}", "type": "function",
             "function": {"name": "read", "arguments": '{"path":"a"}'}}
        if sig is not None:
            c["extra_content"] = {"google": {"thought_signature": sig}}
        calls.append(c)
    return {"role": "assistant", "content": "", "tool_calls": calls}


def test_the_listing_groups_calls_by_message():
    """Grouping IS the diagnosis: verified live 2026-09-04, Gemini signs a parallel batch once and
    accepts it back that way, so a flat per-call list cannot tell the healthy shape from the broken
    one (#155)."""
    entries = ka.tool_call_signatures([{"role": "user", "content": "go"}, _batch("abcd", None)])
    assert entries == ["msg1[c0/read sig=4, c1/read sig=NONE]"]


def test_tool_call_signatures_survives_junk():
    # The request is whatever the client sent; a summariser that raises would take the turn with it.
    assert ka.tool_call_signatures(None) == []
    assert ka.tool_call_signatures("nope") == []
    assert ka.tool_call_signatures([None, 7, {"role": "assistant", "tool_calls": [None, "x"]}]) == []
    assert ka.tool_call_signatures(
        [{"role": "assistant", "tool_calls": [{"extra_content": "not-a-dict"}]}]
    ) == ["msg0[?/? sig=NONE]"]


def test_an_unsigned_later_call_in_the_same_batch_is_not_a_gap():
    """The false positive this rule was written to kill. Live, `[sig=408, sig=NONE]` in ONE message
    round-tripped fine — an earlier build of this warning fired on it and claimed the request would
    be rejected, which was wrong."""
    healthy = [_batch("abcd", None, None)]
    assert ka.unsigned_tool_messages("gemini-3.7-flash", healthy) == 0


def test_a_tool_call_message_starting_unsigned_is_a_gap():
    # The rejected shape: the same two calls split across messages, so the second starts bare.
    split = [_batch("abcd"), _batch(None)]
    assert ka.unsigned_tool_messages("gemini-3.7-flash", split) == 1
    assert ka.unsigned_tool_messages("domino/gemini-3.7-flash", split) == 1   # provider-prefixed


def test_the_gap_is_only_counted_for_a_model_that_signs_at_all():
    split = [_batch("abcd"), _batch(None)]
    for model in ("sonnet", "gpt-5.4", "bedrock-qwen3-coder"):
        assert ka.unsigned_tool_messages(model, split) == 0
    assert ka.unsigned_tool_messages("gemini-3.7-flash", None) == 0
