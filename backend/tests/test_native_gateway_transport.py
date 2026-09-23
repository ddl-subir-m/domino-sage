"""All wire formats use one configured gateway and preserve opaque state bytes."""
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from sage.gateway.client import CostLabels, OpenAICompatibleClient
from sage.gateway.events import MAX_EVENT_BYTES, StreamEvents
from sage.gateway.protocol import Protocol, endpoint


@pytest.mark.parametrize("protocol", ["messages", "responses", "chat"])
@pytest.mark.parametrize("kind", ["malformed", "safe", "http", "http-retry", "stream-failure", "abort"])
def test_installed_codecs_keep_private_state_out_of_errors(protocol, kind):
    from sage.orchestrator.native_routes import _error_event
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the installed native codecs")
    backend = Path(__file__).resolve().parents[1]
    safe_wire = _error_event(Protocol(protocol),
                            "The model gateway refused content under its guardrail policy (guardrail_blocked).").decode()
    run = subprocess.run([node, str(backend / "tests/js/native_codec_errors_harness.mjs"),
                          str(backend / "sage/driver/provider.mjs"), protocol, kind, safe_wire],
                         capture_output=True, text=True, timeout=20, check=False)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert len(report["requests"]) == 2
    assert report["requests"][0] == "/v1/sage/resolve"
    # The resolved protocol decides the ENDPOINT, and that is the whole of #505's fix: gpt-5.4's
    # tool-carrying turns 400 on chat/completions since the alias was repointed, so a `responses`
    # route must not be posted to the chat one. Asserted here because this harness drives the
    # production codec and is already parametrised over all three wires.
    assert report["requests"][1] == {"messages": "/v1/sage/anthropic/messages",
                                     "responses": "/v1/sage/responses",
                                     "chat": "/v1/sage/chat/completions"}[protocol], report
    assert not report["errorLeaked"] and not report["raw"], report
    assert len(report["errors"]) == 1, report
    error = report["errors"][0]
    expected = ("Session policy changed. Use Clear recall before continuing." if kind == "http" else
                "The model gateway refused content under its guardrail policy (guardrail_blocked)."
                if kind in {"safe", "http-retry"} else "Model request cancelled." if kind == "abort"
                else "The model reply could not be read. Try again.")
    assert error["message"] == expected, report
    if kind == "abort":
        assert error["name"] == "AbortError"
    if kind.startswith("http"):
        assert error["apiClass"] is True
        assert error["status"] == (400 if kind == "http" else 502)
        assert error["retryable"] is (kind == "http-retry")


def test_the_codec_asks_anthropic_to_stream_tool_arguments_eagerly():
    """A Build writes a whole file into one tool argument, and Anthropic buffers each argument
    value unless `eager_input_streaming` is set on the tool. It IS set on every call Sage makes —
    but by accident. Nothing in Sage asks for it: the pinned `@ai-sdk/anthropic` defaults
    `toolStreaming` on for a streaming request, and `provider.mjs` imports that copy.

    Measured 2026-09-22 against the pinned binary, driving the real codec at a fake endpoint: all
    16 tools arrived carrying `eager_input_streaming: true`, with no `anthropic-beta` header. The
    same probe run with OpenCode resolving the SDK itself — its compiled binary carries an older
    copy — sent `undefined` on every tool. So the behaviour rides on the `package.json` pin plus
    the `file://` codec, and a bump could take it away with nothing to notice (#497).

    This asserts the flag, not the benefit. What it buys is earlier fragments, and Sage cannot show
    them yet: OpenCode exposes a tool's input as `{}` until the call completes, so the fragments
    die before any Sage surface sees them. That gap is the open half of #497."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the installed native codecs")
    backend = Path(__file__).resolve().parents[1]
    run = subprocess.run([node, str(backend / "tests/js/native_codec_tool_streaming_harness.mjs"),
                          str(backend / "sage/driver/provider.mjs")],
                         capture_output=True, text=True, timeout=20, check=False)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    # A non-streaming request gets no eager flag at all, so the stream flag is part of the claim.
    assert report["stream"] is True, report
    assert [tool["name"] for tool in report["tools"]] == ["write", "bash"], report
    assert [tool["eager"] for tool in report["tools"]] == [True, True], report


@pytest.mark.parametrize("suffix", ["", "/", "/v1", "/v1/"])
@pytest.mark.parametrize("protocol,path", [(Protocol.CHAT, "/v1/chat/completions"),
                                          (Protocol.MESSAGES, "/anthropic/v1/messages"),
                                          (Protocol.RESPONSES, "/v1/responses")])
def test_gateway_application_path_is_preserved(suffix, protocol, path):
    assert endpoint("https://gateway.example/apps/llm_gateway" + suffix, protocol) == (
        "https://gateway.example/apps/llm_gateway" + path)


@pytest.mark.parametrize("protocol", list(Protocol))
@pytest.mark.parametrize("stream", [False, True])
def test_native_body_identity_and_tags_do_not_change(protocol, stream):
    seen = []
    def receive(request):
        seen.append(request)
        return httpx.Response(200, content=b"opaque reply")
    token = iter(["first", "second"])
    client = OpenAICompatibleClient("https://gateway.example/apps/gw/v1/", lambda: next(token),
                                    domino_tags=True)
    client._http = httpx.Client(transport=httpx.MockTransport(receive))
    body = {"model": "Exact/ALIAS", "stream": stream, "signature": "synthetic-signature",
            "encrypted_content": "synthetic-state", "call_id": "call_unchanged"}
    try:
        for session in ("one", None):
            assert b"".join(client.route(body, CostLabels("plan", "auto", session=session),
                                        protocol=protocol)) == b"opaque reply"
    finally:
        client.close()
    assert all(str(request.url) == endpoint("https://gateway.example/apps/gw", protocol)
               and json.loads(request.content) == body for request in seen)
    assert [r.headers["authorization"] for r in seen] == ["Bearer first", "Bearer second"]
    assert seen[0].headers["X-LLM-Tag-sage-session"] == "one"
    assert "X-LLM-Tag-sage-session" not in seen[1].headers
    assert ("anthropic-version" in seen[0].headers) == (protocol is Protocol.MESSAGES)


def test_legacy_direct_vendor_mode_cannot_take_native_calls():
    client = OpenAICompatibleClient("https://vendor.example/v1", lambda: "unused")
    for protocol in (Protocol.MESSAGES, Protocol.RESPONSES):
        with pytest.raises(ValueError, match="LLM Gateway"):
            list(client.route({}, CostLabels("plan", "auto"), protocol=protocol))
    assert client._http is None


def sse(*events):
    return b"".join(b"data: " + json.dumps(event).encode() + b"\r\n\r\n" for event in events)


@pytest.mark.parametrize("protocol,wire", [
    (Protocol.CHAT, sse({"choices": [{"delta": {"content": "tool_calls is only text"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "read"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]},
                      "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}})),
    (Protocol.MESSAGES, sse({"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
        {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "read"}},
        {"type": "message_delta", "usage": {"output_tokens": 3}}, {"type": "message_stop"})),
    (Protocol.RESPONSES, sse({"type": "response.output_item.added",
        "item": {"type": "function_call", "call_id": "t1", "name": "read"}},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 5, "output_tokens": 3}}})),
])
def test_all_chunk_boundaries_preserve_tools_usage_and_terminal(protocol, wire):
    for split in range(len(wire) + 1):
        events = StreamEvents(protocol)
        events.feed(wire[:split])
        events.feed(wire[split:])
        events.finish()
        assert events.tool_names == {"read"}
        assert len(events.tool_ids) == 1
        assert (events.input_tokens, events.output_tokens) == (5, 3)
        assert not events.error


def test_text_is_not_a_tool_call_and_unfinished_stream_is_rejected():
    events = StreamEvents(Protocol.CHAT)
    events.feed(sse({"choices": [{"delta": {"content": "tool_calls"}}]}))
    assert not events.tool_ids
    with pytest.raises(ValueError, match="terminal"):
        events.finish()


def test_partial_and_oversized_events_are_rejected():
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(b'data: {"type":')
    with pytest.raises(ValueError, match="inside"):
        events.finish()
    with pytest.raises(ValueError, match="size limit"):
        events.feed(b"x" * MAX_EVENT_BYTES)


def test_response_incomplete_and_error_are_failures_without_raw_content():
    events = StreamEvents(Protocol.RESPONSES)
    events.feed(sse({"type": "response.incomplete", "response": {
        "incomplete_details": {"reason": "max_output_tokens"}}}))
    events.finish()
    assert events.error == "max_output_tokens"
    events = StreamEvents(Protocol.MESSAGES)
    events.feed(sse({"type": "error", "error": {"type": "overloaded_error", "message": "secret"}}))
    events.finish()
    assert events.error == "overloaded_error"
    assert "secret" not in repr(events)


def test_cancel_closes_only_its_response_and_cancellation_before_bind_is_not_lost():
    from sage.gateway.client import StreamCancellation
    one, two = StreamCancellation(), StreamCancellation()
    closed = []
    one.bind(lambda: closed.append("one"))
    two.bind(lambda: closed.append("two"))
    one.cancel()
    assert closed == ["one"] and one.event.is_set() and not two.event.is_set()
    late = StreamCancellation()
    late.cancel()
    late.bind(lambda: closed.append("late"))
    assert closed == ["one", "late"]


def test_incomplete_error_frame_is_not_forwarded_before_it_can_be_validated():
    parser = StreamEvents(Protocol.RESPONSES)
    assert parser.feed(b'data: {"type":"error","message":"private') == []
    parser.feed(b' state"}\n\n')
    assert parser.error == "upstream_error"
    assert "private" not in repr(parser)


@pytest.mark.parametrize('protocol,event', [
    (Protocol.MESSAGES, {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}),
    (Protocol.CHAT, {"choices": [{"delta": {}, "finish_reason": "length"}]}),
])
def test_token_limit_is_incomplete_output_not_success(protocol, event):
    parser = StreamEvents(protocol)
    parser.feed(sse(event))
    assert parser.error


def test_native_cache_usage_counts_total_input_without_exposing_state():
    parser = StreamEvents(Protocol.MESSAGES)
    parser.feed(sse({"type": "message_start", "message": {"usage": {
        "input_tokens": 5, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 2}}}))
    parser.feed(sse({"type": "message_delta", "usage": {"output_tokens": 3}}))
    assert (parser.input_tokens, parser.cached_tokens, parser.output_tokens) == (17, 10, 3)
    parser = StreamEvents(Protocol.RESPONSES)
    parser.feed(sse({"type": "response.completed", "response": {"usage": {
        "input_tokens": 17, "input_tokens_details": {"cached_tokens": 10}, "output_tokens": 3}}}))
    assert (parser.input_tokens, parser.cached_tokens, parser.output_tokens) == (17, 10, 3)
