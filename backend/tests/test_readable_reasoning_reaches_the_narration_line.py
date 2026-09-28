"""#599: mimo's readable reasoning reaches OpenCode's reasoning part, and never the gateway again.

Measured against cloud-dogfood 2026-09-28: mimo-v2.6-pro, a native Responses route, streams its
reasoning as `response.reasoning_text.delta`, with or without `reasoning.summary`, and sends no
encrypted content. The installed `@ai-sdk/openai` builds reasoning parts only from
`response.reasoning_summary_*`, so the part stayed empty and #589's narration had nothing to read.

Every recorded mimo request carried `opaqueStateBytes: 0`: OpenCode sends back no reasoning item
for an empty part. Once the part has text it would, so the relay drops that item on the way out
and the gateway keeps seeing the request it saw before.
"""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.gateway.events import StreamEvents
from sage.gateway.protocol import Protocol
from sage.shim.native import gemini_thought_as_reasoning, readable_reasoning_as_summary

from .test_native_model_controls import active, dispatch
from .test_native_model_controls import running as native_running

running = native_running

MODEL = "mimo-v2.6-pro"
THOUGHT = ["Checking the adverse events ", "table before writing."]


def mimo_events(metadata=None, reasoning=None):
    yield {"type": "response.output_item.added", "output_index": 0,
           "item": {"id": "rs_1", "type": "reasoning", "summary": []}}
    for piece in THOUGHT:
        yield {"type": "response.reasoning_text.delta", "item_id": "rs_1", "output_index": 0,
               "content_index": 0, "delta": piece}
    yield {"type": "response.reasoning_text.done", "item_id": "rs_1", "output_index": 0,
           "content_index": 0, "text": "".join(THOUGHT)}
    yield {"type": "response.output_item.done", "output_index": 0,
           "item": {"id": "rs_1", "type": "reasoning", "summary": [],
                    "content": [{"type": "reasoning_text", "text": "".join(THOUGHT)}]}}
    yield {"type": "response.completed", "response": {
        "id": "resp_1", "model": MODEL, "store": False, "metadata": metadata or {},
        "reasoning": reasoning or {}, "incomplete_details": None,
        "usage": {"input_tokens": 3, "output_tokens": 5}}}


def wire(events):
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)


def sse(text):
    return [json.loads(line[5:]) for line in text.splitlines()
            if line.startswith("data:") and line[5:].strip() != "[DONE]"]


def codec_reasoning(tmp_path, body: bytes, protocol="responses"):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the installed native codecs")
    backend = Path(__file__).resolve().parents[1]
    path = tmp_path / "wire.sse"
    path.write_bytes(body)
    run = subprocess.run([node, str(backend / "tests/js/native_codec_reasoning_harness.mjs"),
                          str(backend / "sage/driver/provider.mjs"), str(path), protocol],
                         capture_output=True, text=True, timeout=20, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_the_installed_codec_reads_mimo_reasoning_only_once_it_is_renamed(tmp_path):
    raw = wire(mimo_events())
    assert [e for e in codec_reasoning(tmp_path, raw) if e["type"] == "reasoning-delta"] == []

    renamed = b"".join(readable_reasoning_as_summary(b"data: " + json.dumps(e).encode() + b"\n\n")
                       for e in mimo_events())
    events = [e for e in codec_reasoning(tmp_path, renamed) if e["type"] != "text-delta"]
    assert not [e for e in events if e["type"] == "error"], events
    assert [e["delta"] for e in events if e["type"] == "reasoning-delta"] == THOUGHT
    assert {e["id"] for e in events} == {"rs_1:0"}
    assert events[0]["type"] == "reasoning-start"
    assert events[-1]["type"] == "reasoning-end"


def test_only_the_readable_reasoning_delta_is_rewritten():
    for event in mimo_events():
        frame = b"data: " + json.dumps(event).encode() + b"\n\n"
        out = readable_reasoning_as_summary(frame)
        if event["type"] != "response.reasoning_text.delta":
            assert out == frame, event["type"]
            continue
        assert json.loads(out[5:]) == {"type": "response.reasoning_summary_text.delta",
                                       "item_id": "rs_1", "output_index": 0,
                                       "summary_index": 0, "delta": event["delta"]}
    assert readable_reasoning_as_summary(b": keepalive\n\n") == b": keepalive\n\n"
    assert readable_reasoning_as_summary(b"data: not json\n\n") == b"data: not json\n\n"


def scripted(gateway):
    def route(request, labels, **kwargs):
        gateway.seen.append((copy.deepcopy(request), labels))
        body = wire(mimo_events(request["metadata"], request.get("reasoning")))
        for offset in range(0, len(body), 23):
            yield body[offset:offset + 23]
    return route


def test_opencode_receives_mimo_reasoning_as_summary_deltas(running, monkeypatch):
    client, orch, gateway = running
    monkeypatch.setattr(gateway, "route", scripted(gateway))
    client.post("/api/project/model", json={"pick": MODEL, "mode": "plan"})
    with active(orch) as headers:
        response = dispatch(client, headers, Protocol.RESPONSES, MODEL)
    assert response.status_code == 200, response.text
    events = sse(response.text)
    types = [e["type"] for e in events]
    assert "response.reasoning_text.delta" not in types
    assert [e["delta"] for e in events
            if e["type"] == "response.reasoning_summary_text.delta"] == THOUGHT
    assert all(e["summary_index"] == 0 and e["item_id"] == "rs_1" for e in events
               if e["type"] == "response.reasoning_summary_text.delta")
    assert "response.reasoning_text.done" in types and types[-1] == "response.completed"


def replayed(summary_text, encrypted=None):
    item = {"type": "reasoning", "id": "rs_1", "encrypted_content": encrypted,
            "summary": [{"type": "summary_text", "text": summary_text}]}
    return [{"role": "user", "content": "answer briefly"}, item,
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Done."}]},
            {"role": "user", "content": "and now?"}]


def outbound(client, orch, gateway, messages):
    with active(orch) as headers:
        response = dispatch(client, headers, Protocol.RESPONSES, MODEL, messages)
    assert response.status_code == 200, response.text
    sent = copy.deepcopy(gateway.seen[-1][0])
    sent.pop("metadata", None)
    return sent


def test_the_gateway_never_sees_reasoning_text_that_carries_no_provider_state(running, monkeypatch):
    client, orch, gateway = running
    monkeypatch.setattr(gateway, "route", scripted(gateway))
    client.post("/api/project/model", json={"pick": MODEL, "mode": "plan"})
    before = [m for m in replayed("x") if m.get("type") != "reasoning"]
    assert outbound(client, orch, gateway, replayed("".join(THOUGHT))) == outbound(
        client, orch, gateway, before)
    assert "adverse events" not in json.dumps(gateway.seen[-2][0])


def test_reasoning_state_is_still_sent_without_the_summary_text(running, monkeypatch):
    """gpt-5.4's items carry encrypted state. Sage now asks for summaries, and the codec would
    replay them; the gateway accepts the item with `summary: []` (measured 2026-09-28), which is
    what it was sent before summaries were asked for."""
    client, orch, gateway = running
    monkeypatch.setattr(gateway, "route", scripted(gateway))
    client.post("/api/project/model", json={"pick": MODEL, "mode": "plan"})
    sent = outbound(client, orch, gateway, replayed("a summary", encrypted="opaque-state"))
    kept = [item for item in sent["input"] if item.get("type") == "reasoning"]
    assert kept == [{**replayed("a summary", encrypted="opaque-state")[1], "summary": []}]
    assert sent["reasoning"]["summary"] == "auto"


GEMINI = "domino/gemini-3.7-flash"
GEMINI_THOUGHT = "**Weighing the rows**\n\nI am checking which table explains the goal.\n\n"


def gemini_events():
    yield {"choices": [{"index": 0, "delta": {"role": "assistant", "content": GEMINI_THOUGHT,
                                              "extra_content": {"google": {"thought": True}}}}]}
    yield {"choices": [{"index": 0, "delta": {"content": "The goal is a TEAE table."}}]}
    yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}


def chat_frame(event):
    return b"data: " + json.dumps(event).encode() + b"\n\n"


def test_the_chat_codec_shows_a_gemini_thought_as_reasoning_only_once_it_is_moved(tmp_path):
    raw = b"".join(chat_frame(e) for e in gemini_events()) + b"data: [DONE]\n\n"
    as_text = codec_reasoning(tmp_path, raw, "chat")
    assert GEMINI_THOUGHT in [e["delta"] for e in as_text if e["type"] == "text-delta"]

    moved = b"".join(gemini_thought_as_reasoning(chat_frame(e)) for e in gemini_events())
    events = codec_reasoning(tmp_path, moved + b"data: [DONE]\n\n", "chat")
    assert not [e for e in events if e["type"] == "error"], events
    assert [e["delta"] for e in events if e["type"] == "reasoning-delta"] == [GEMINI_THOUGHT]
    assert [e["delta"] for e in events if e["type"] == "text-delta"] == ["The goal is a TEAE table."]


def test_a_gemini_thought_is_reasoning_to_the_watchdog_not_reply_text():
    events = StreamEvents(Protocol.CHAT)
    thought, answer, finish = (chat_frame(e) for e in gemini_events())
    events.feed(thought)
    assert events.first_action_kind is None and not events.saw_text
    assert events.reasoning_only_chunks == 1
    events.feed(answer + finish)
    assert events.first_action_kind == "text" and events.saw_text


def chat_scripted(gateway):
    def route(request, labels, **kwargs):
        gateway.seen.append((copy.deepcopy(request), labels))
        yield b"".join(chat_frame(e) for e in gemini_events()) + b"data: [DONE]\n\n"
    return route


def chat_history():
    return [{"role": "user", "content": "answer briefly"},
            {"role": "assistant", "content": "Done.", "reasoning_content": "I looked first."},
            {"role": "user", "content": "and now?"}]


def chat_outbound(client, orch, gateway, model, effort):
    client.post("/api/project/model", json={"pick": model, "pick_effort": effort, "mode": "plan"})
    with active(orch) as headers:
        response = dispatch(client, headers, Protocol.CHAT, model, chat_history())
    assert response.status_code == 200, response.text
    return response, gateway.seen[-1][0]


@pytest.mark.parametrize("effort,level", [(None, None), ("low", "low"), ("max", "high")])
def test_a_vertex_route_asks_for_thoughts_at_the_same_level(running, monkeypatch, effort, level):
    client, orch, gateway = running
    monkeypatch.setattr(gateway, "route", chat_scripted(gateway))
    _, sent = chat_outbound(client, orch, gateway, GEMINI, effort)
    assert "reasoning_effort" not in sent
    config = {"include_thoughts": True, **({"thinking_level": level} if level else {})}
    assert sent["google"] == {"thinking_config": config}
    assert not [m for m in sent["messages"] if "reasoning_content" in m]


def test_opencode_receives_a_gemini_thought_as_reasoning_content(running, monkeypatch):
    client, orch, gateway = running
    monkeypatch.setattr(gateway, "route", chat_scripted(gateway))
    response, _ = chat_outbound(client, orch, gateway, GEMINI, None)
    deltas = [c["delta"] for e in sse(response.text) for c in e.get("choices") or []]
    assert deltas[0]["reasoning_content"] == GEMINI_THOUGHT and "content" not in deltas[0]
    assert deltas[1]["content"] == "The goal is a TEAE table."


def test_a_route_that_is_not_vertex_keeps_its_reasoning_wire(running, monkeypatch):
    client, orch, gateway = running
    monkeypatch.setattr(gateway, "route", chat_scripted(gateway))
    _, sent = chat_outbound(client, orch, gateway, "GLM 5.3 OR", "low")
    assert sent["reasoning_effort"] == "low" and "google" not in sent
