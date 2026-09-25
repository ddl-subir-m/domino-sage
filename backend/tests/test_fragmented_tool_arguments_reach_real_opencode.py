"""Fragmented tool arguments through the installed codec, the native endpoint and real OpenCode (#560).

Xiaomi ARM trials produced malformed tool arguments and kept no argument deltas, so nothing could say
which boundary first damaged them. This file is the controlled reproduction. One scripted local
gateway writes deterministic Responses streams; Sage's real `/v1/sage/responses` relay forwards
them; the pinned OpenCode 1.18.4 reads them through `driver/provider.mjs` (the installed
`@ai-sdk/openai` codec) and runs a custom `probe` tool that records exactly what it was handed.

The identities are kept distinct on purpose: the response id (`resp_*`), the output item id
(`fc_*`) and index, the tool call id (`call_*`), the OpenCode session, and the Sage turn. A lane is
joined on the id its protocol gives it and never on array position.

What the pinned codec does, read from `@ai-sdk/openai` 3.0.84 `dist/index.mjs`: a
`response.output_item.added` function_call opens a lane keyed by `output_index`; each
`response.function_call_arguments.delta` is forwarded as `tool-input-delta` for UI only; the
`tool-call` that OpenCode executes is built from `response.output_item.done`'s `item.arguments`,
and `response.function_call_arguments.done` is ignored. So "done wins" is the protocol's answer to
a delta/done disagreement, and a stream with no `output_item.done` executes nothing.

Every case asserts three witnesses: the probe log (what executed), the OpenCode transcript (what
the harness recorded), and the next request's `function_call_output` items (what the model was
told). Sage's own bounded evidence (`toolArgumentBoundaries`) is asserted beside them.
"""
from __future__ import annotations

import copy
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from sage import build_diagnostics as diagnostics
from sage import timing
from sage.driver.opencode import OpenCodeClient
from sage.gateway.protocol import Protocol
from sage.orchestrator import native_routes
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import join_aliases
from sage.router.models import Mode, ModelCatalog

from .opencode_server import BINARY, _opencode_server
from .test_a_build_pick_carries_its_own_effort import _template
from .test_native_model_controls import VerifiedResources
from .test_turn_keepalive import _served

REPO = Path(__file__).resolve().parents[2]
CODEC = REPO / "backend" / "sage" / "driver" / "provider.mjs"

# Escaped quotes, an escaped newline, a tab, a backslash, a two-byte and a four-byte UTF-8 sequence.
ARGS_A = {"label": "a", "text": 'line one\nsays "two" é \U0001f642'}
ARGS_B = {"label": "b", "text": "第二\tback\\slash"}

PROBE_TOOL = r'''
// A custom OpenCode tool that records exactly what it was handed. Test fixture only.
const LOG = process.env.SAGE_PROBE_LOG
export const probe = {
  description: "Record the arguments you were given. Call it when asked.",
  args: {
    label: { type: "string", description: "A short label." },
    text: { type: "string", description: "Any text." },
  },
  async execute(args, ctx) {
    const fs = await import("node:fs")
    fs.appendFileSync(LOG, JSON.stringify({args, callID: ctx?.callID ?? null,
      sessionID: ctx?.sessionID ?? null}) + "\n")
    return "recorded " + String(args?.label)
  },
}
'''


def sse(event: dict) -> bytes:
    return b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\n\n"


def envelope(request: dict, response_id: str, **extra) -> dict:
    return {"id": response_id, "model": request["model"], "store": False,
            "metadata": request["metadata"], "reasoning": request.get("reasoning", {}), **extra}


def function_call(request, *, response_id, calls, deltas, done_arguments=None, completed=True,
                  arguments_done=True, item_done=True):
    """One Responses stream announcing `calls` and fragmenting their arguments as `deltas` says.

    `calls`: list of (output_index, item_id, call_id, name).
    `deltas`: list of (output_index, item_id, delta_text) in wire order; "" is an empty delta.
    `done_arguments`: {item_id: arguments} for the `done` events; defaults to the joined deltas.
    """
    joined: dict[str, str] = {}
    for _, item_id, text in deltas:
        joined[item_id] = joined.get(item_id, "") + text
    done_arguments = {**joined, **(done_arguments or {})}
    yield sse({"type": "response.created", "response": envelope(request, response_id)})
    for index, item_id, call_id, name in calls:
        yield sse({"type": "response.output_item.added", "output_index": index,
                   "item": {"id": item_id, "type": "function_call", "call_id": call_id,
                            "name": name, "arguments": "", "status": "in_progress"}})
    for index, item_id, text in deltas:
        yield sse({"type": "response.function_call_arguments.delta", "output_index": index,
                   "item_id": item_id, "delta": text})
    for index, item_id, call_id, name in calls:
        if arguments_done:
            yield sse({"type": "response.function_call_arguments.done", "output_index": index,
                       "item_id": item_id, "arguments": done_arguments.get(item_id, "")})
        if item_done:
            yield sse({"type": "response.output_item.done", "output_index": index,
                       "item": {"id": item_id, "type": "function_call", "call_id": call_id,
                                "name": name, "arguments": done_arguments.get(item_id, ""),
                                "status": "completed"}})
    if completed:
        yield sse({"type": "response.completed", "response": envelope(
            request, response_id, usage={"input_tokens": 5, "output_tokens": 7})})


def text_reply(request, *, response_id, text):
    yield sse({"type": "response.created", "response": envelope(request, response_id)})
    yield sse({"type": "response.output_item.added", "output_index": 0,
               "item": {"id": "msg_" + response_id, "type": "message", "role": "assistant",
                        "content": [], "status": "in_progress"}})
    yield sse({"type": "response.output_text.delta", "output_index": 0, "content_index": 0,
               "item_id": "msg_" + response_id, "delta": text})
    yield sse({"type": "response.output_item.done", "output_index": 0,
               "item": {"id": "msg_" + response_id, "type": "message", "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}})
    yield sse({"type": "response.completed", "response": envelope(
        request, response_id, usage={"input_tokens": 5, "output_tokens": 7})})


def interleave(a: tuple[int, str, str], b: tuple[int, str, str], piece: int = 5):
    """Alternate `piece`-character fragments of two argument strings, with empty deltas between."""
    (ia, item_a, text_a), (ib, item_b, text_b) = a, b
    out = []
    pa = [text_a[i:i + piece] for i in range(0, len(text_a), piece)]
    pb = [text_b[i:i + piece] for i in range(0, len(text_b), piece)]
    for n in range(max(len(pa), len(pb))):
        if n < len(pa):
            out.append((ia, item_a, pa[n]))
        if n == 1:
            out.append((ia, item_a, ""))
        if n < len(pb):
            out.append((ib, item_b, pb[n]))
    return out


def split_bytes(wire: bytes, size: int) -> list[bytes]:
    """Byte fragments that ignore SSE framing and UTF-8 sequence boundaries alike."""
    return [wire[i:i + size] for i in range(0, len(wire), size)]


class ScriptedGateway:
    """A local Responses gateway: one script per inference, consumed in order."""

    def __init__(self):
        self.seen: list[dict] = []
        self.protocols: list[Protocol] = []
        self.scripts: list = []
        self.failures: list[str] = []

    def expect(self, script) -> None:
        self.scripts.append(script)

    def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
        self.seen.append(copy.deepcopy(request))
        self.protocols.append(protocol)
        if not self.scripts:
            self.failures.append(f"unexpected inference {len(self.seen)}")
            raise AssertionError("no script left for this inference")
        script = self.scripts.pop(0)
        yield from script(request)


def _catalog(model: str) -> ModelCatalog:
    return ModelCatalog(sovereign_plan=model, sovereign_implement=model, sovereign_ask=model,
                        plan=model, implement=model, ask=model)


class Rig:
    """Sage's native endpoint over a real socket, a scripted gateway, and one OpenCode runtime."""

    def __init__(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("SAGE_TIMING", "1")
        from sage.gateway.capabilities import evidence

        self.gateway = ScriptedGateway()
        rows = copy.deepcopy(evidence())
        aliases = [alias for row in rows
                   for alias in join_aliases({row["name"]}, [row], gateway_root=row["gateway"])]
        self.orch = Orchestrator(workspace_dir=tmp_path / "mnt" / "code", template=_template(tmp_path),
                                 gateway=self.gateway, catalog=_catalog("gpt-5.4"),
                                 project_id="Sage", resources=VerifiedResources(aliases))
        self.project = self.orch.project(start_preview=False)
        self.project.shim._gateway = self.gateway
        self.project.control.set_mode(Mode.IMPLEMENT)
        self.project.control.pick("gpt-5.4")
        self.app = FastAPI()
        native_routes.install(self.app, lambda: self.orch)
        self.runtime = tmp_path / "runtime"
        self.runtime.mkdir()
        self.probe_log = self.runtime / "probe.log"
        self.records: list[dict] = []
        self.client: OpenCodeClient | None = None
        self.sid: str | None = None

    def env(self, sage_url: str) -> dict:
        config = json.loads((REPO / "opencode.json").read_text())
        provider = config["provider"]["sage-gateway"]
        provider["npm"] = CODEC.as_uri()
        provider["options"]["baseURL"] = sage_url + "/v1"
        config["plugin"] = []
        config["mcp"] = {}
        config["permission"] = {"*": "allow"}
        (self.runtime / "opencode.json").write_text(json.dumps(config))
        tools = self.runtime / "config" / "opencode" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "probe.ts").write_text(PROBE_TOOL)
        env = dict(os.environ)
        env.update(OPENCODE_CONFIG=str(self.runtime / "opencode.json"),
                   OPENCODE_DISABLE_AUTOUPDATE="true",
                   XDG_CONFIG_HOME=str(self.runtime / "config"),
                   XDG_DATA_HOME=str(self.runtime / "data"),
                   XDG_CACHE_HOME=str(self.runtime / "cache"),
                   XDG_STATE_HOME=str(self.runtime / "state"),
                   OPENCODE_CONFIG_DIR=str(self.runtime / "config" / "opencode"),
                   SAGE_PROBE_LOG=str(self.probe_log))
        return env

    @contextmanager
    def opencode(self, sage_url: str):
        """Boot (or re-boot) the pinned binary against the same runtime directory."""
        with _opencode_server(self.runtime, self.env(sage_url)) as url:
            self.client = OpenCodeClient(url)
            self.orch._oc_client = self.client
            self.directory = str(self.project.record.path)
            if self.sid is None:
                self.sid = self.client.create_session(self.directory)
            else:
                self.client.note_session_dir(self.sid, self.directory)
            httpx.get(url + "/agent", params={"directory": self.directory}, timeout=120).raise_for_status()
            yield url

    @contextmanager
    def turn(self):
        """One Sage Build turn: the lock, the ticket, the timing record, the active session."""
        record = timing.start_turn("build")
        ticket, state = self.orch.prepare_stream_turn(record.turn_id, kind="build",
                                                      conversation="thread_test", app=True)
        assert state == "running"
        ticket.timing_record = record
        self.project.active_session_id = self.sid
        try:
            yield ticket
        finally:
            self.project.active_session_id = None
            self.orch.release_stream_turn(ticket)
            finished = timing.finish_turn(record=record)
            self.records.append(diagnostics.snapshot(
                finished, {"turnId": record.turn_id, "appId": "app", "conversationId": "thread_test",
                           "kind": "build"}, terminal=True))

    def prompt(self, text: str, *, inferences: int, deadline_s: float = 120) -> list[dict]:
        """Send one prompt and wait for OpenCode to go idle after at least `inferences` calls."""
        before = len(self.gateway.seen)
        self.client.send_prompt(self.sid, text, agent="build",
                                model={"providerID": "sage-gateway", "modelID": "sage-model"})
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            assert not self.gateway.failures, self.gateway.failures
            try:
                running = self.client.is_running(self.sid, directory=self.directory)
            except httpx.ReadTimeout:
                continue
            if len(self.gateway.seen) - before >= inferences and not running:
                break
            time.sleep(0.1)
        else:
            seen = len(self.gateway.seen) - before
            tail = (self.runtime / "opencode.log").read_text()[-4000:]
            pytest.fail(f"OpenCode did not finish: {seen} inferences seen, log {tail!r}")
        return self.client.messages(self.sid)

    def executed(self) -> list[dict]:
        if not self.probe_log.exists():
            return []
        return [json.loads(line) for line in self.probe_log.read_text().splitlines() if line]

    @staticmethod
    def tool_parts(messages: list[dict]) -> list[dict]:
        return [part for m in messages if m.get("type") == "assistant"
                for part in m.get("content", []) if part.get("type") == "tool"]

    @staticmethod
    def tool_outputs(request: dict) -> dict[str, str]:
        """The `function_call_output` items the next inference carried, by call id."""
        return {item["call_id"]: item.get("output") for item in request.get("input", [])
                if isinstance(item, dict) and item.get("type") == "function_call_output"}

    @staticmethod
    def tool_name(request: dict) -> str:
        names = [t.get("name") for t in request.get("tools", []) if isinstance(t, dict)]
        probe = [n for n in names if isinstance(n, str) and "probe" in n]
        assert len(probe) == 1, names
        return probe[0]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


def _two_interleaved_calls(rig: Rig, response_id: str, ids: tuple[str, str], *, byte_size: int):
    """Two `probe` calls in one response, argument fragments interleaved, then byte-split."""
    call_a, call_b = ids
    text_a = json.dumps(ARGS_A, ensure_ascii=False)
    text_b = json.dumps(ARGS_B, ensure_ascii=False)

    def script(request):
        name = Rig.tool_name(request)
        wire = b"".join(function_call(
            request, response_id=response_id,
            calls=[(0, "fc_" + call_a, call_a, name), (1, "fc_" + call_b, call_b, name)],
            deltas=interleave((0, "fc_" + call_a, text_a), (1, "fc_" + call_b, text_b))))
        # The four-byte character must be cut in half by the byte split, not merely be present.
        smile = "\U0001f642".encode()
        at = wire.index(smile)
        assert any(offset < at + 2 < offset + byte_size and offset != at + 2
                   for offset in range(0, len(wire), byte_size))
        yield from split_bytes(wire, byte_size)

    return script


def _lanes(record: dict, n: int) -> list[dict]:
    """The boundary lanes Sage recorded for model call `n` of the turn, from the diagnostics
    record itself (the download shape), not from the recorder."""
    calls = record.get("toolArgumentBoundaries", {}).get("calls", [])
    return next((row["lanes"] for row in calls if row["n"] == n), [])


@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_real_opencode_runs_two_interleaved_fragmented_calls_exactly_once_each(rig: Rig):
    """Acceptance (a): both interleaved calls execute exactly once with the exact synthetic
    arguments; SSE-frame and UTF-8 byte fragmentation does not change a payload. Then the same
    across a second user turn, and again after an OpenCode restart on the same session."""
    with _served(rig.app) as sage_url:
        with rig.opencode(sage_url):
            rig.gateway.expect(_two_interleaved_calls(rig, "resp_1", ("call_a", "call_b"), byte_size=7))
            rig.gateway.expect(lambda request: text_reply(request, response_id="resp_2", text="Both recorded."))
            with rig.turn():
                messages = rig.prompt("Call probe twice, as instructed by the tool results.", inferences=2)
            assert rig.gateway.protocols == [Protocol.RESPONSES, Protocol.RESPONSES]
            ran = rig.executed()
            assert [row["args"] for row in ran] == [ARGS_A, ARGS_B]
            assert [row["callID"] for row in ran] == ["call_a", "call_b"]
            assert all(row["sessionID"] == rig.sid for row in ran)
            parts = rig.tool_parts(messages)
            assert [(p["callID"], p["state"]["status"], p["state"]["input"]) for p in parts] == [
                ("call_a", "completed", ARGS_A), ("call_b", "completed", ARGS_B)]
            assert rig.tool_outputs(rig.gateway.seen[1]) == {"call_a": "recorded a", "call_b": "recorded b"}
            lanes = _lanes(rig.records[-1], 1)
            assert [(l["providerId"], l["lane"], l["outputIndex"], l["boundary"], l["terminal"])
                    for l in lanes] == [("call_a", "fc_call_a", 0, "match", "closed"),
                                        ("call_b", "fc_call_b", 1, "match", "closed")]
            assert [l["deltaJoin"]["bytes"] for l in lanes] == [
                len(json.dumps(ARGS_A, ensure_ascii=False).encode()),
                len(json.dumps(ARGS_B, ensure_ascii=False).encode())]
            assert [l["deltaJoin"]["empty"] for l in lanes] == [1, 0]
            # Both completions were sent and agreed; the codec executes from `item_done`.
            assert all(l["done"]["jsonValidity"] == "valid_object" and l["done"]["source"] == "both"
                       and l["done"]["agreement"] == "agree" for l in lanes)
            assert rig.records[-1]["toolArgumentBoundaries"]["calls"][0]["responseId"] == "resp_1"
            assert rig.records[-1]["toolArgumentBoundaries"]["calls"][0]["orphanDeltas"] == 0
            assert _lanes(rig.records[-1], 2) == []
            dumped = json.dumps(rig.records[-1])
            for private in ("line one", "back\\\\slash", "\\u7b2c", "第", "recorded"):
                assert private not in dumped, private

            # A second user turn: the same tool twice more, with NEW call ids and a different split.
            rig.gateway.expect(_two_interleaved_calls(rig, "resp_3", ("call_c", "call_d"), byte_size=3))
            rig.gateway.expect(lambda request: text_reply(request, response_id="resp_4", text="Again."))
            with rig.turn():
                rig.prompt("Do it again.", inferences=2)
            assert [row["callID"] for row in rig.executed()] == ["call_a", "call_b", "call_c", "call_d"]
            assert [row["args"] for row in rig.executed()][2:] == [ARGS_A, ARGS_B]
            outputs = rig.tool_outputs(rig.gateway.seen[3])
            assert {k: outputs[k] for k in ("call_c", "call_d")} == {"call_c": "recorded a", "call_d": "recorded b"}
            # The earlier pair rides the history as function_call + function_call_output, not re-run.
            history = rig.gateway.seen[2]["input"]
            assert [i["call_id"] for i in history if i.get("type") == "function_call"] == ["call_a", "call_b"]
            assert [l["boundary"] for l in _lanes(rig.records[-1], 1)] == ["match", "match"]

        # Restart OpenCode on the same runtime and session; the pair before is history, one more runs.
        with rig.opencode(sage_url):
            rig.gateway.expect(_two_interleaved_calls(rig, "resp_5", ("call_e", "call_f"), byte_size=11))
            rig.gateway.expect(lambda request: text_reply(request, response_id="resp_6", text="After restart."))
            with rig.turn():
                messages = rig.prompt("Once more after the restart.", inferences=2)
            assert [row["callID"] for row in rig.executed()] == [
                "call_a", "call_b", "call_c", "call_d", "call_e", "call_f"]
            outputs = rig.tool_outputs(rig.gateway.seen[5])
            assert {k: outputs[k] for k in ("call_e", "call_f")} == {"call_e": "recorded a", "call_f": "recorded b"}
            assert set(outputs) == {"call_a", "call_b", "call_c", "call_d", "call_e", "call_f"}
            assert [p["callID"] for p in rig.tool_parts(messages)] == [
                "call_a", "call_b", "call_c", "call_d", "call_e", "call_f"]
    assert not rig.gateway.scripts and not rig.gateway.failures


# --- Failure cases -----------------------------------------------------------------------------
#
# Expected results come from the protocol and the pinned codec, not from a Sage parser: the codec
# executes what `output_item.done` carries, so a lane that never reaches `output_item.done` runs
# nothing, and a disagreement between the deltas and the completion runs the completion. Sage
# records the disagreement as evidence and repairs nothing.

def _one_call(request, *, response_id, deltas_text, done_text, **kwargs):
    name = Rig.tool_name(request)
    pieces = [(0, "fc_x", deltas_text[i:i + 6]) for i in range(0, len(deltas_text), 6)]
    yield from function_call(request, response_id=response_id,
                             calls=[(0, "fc_x", "call_x", name)], deltas=pieces,
                             done_arguments={"fc_x": done_text}, **kwargs)


def _nothing_ran(rig: Rig) -> None:
    assert rig.executed() == []
    assert not rig.probe_log.exists() or rig.probe_log.read_text() == ""


def _fallback(rig: Rig, response_id: str) -> None:
    """OpenCode may make one more inference after a failed one (to report the error to the model).
    Answer it with text so the observation is about the failed call, not a starved harness."""
    rig.gateway.expect(lambda request: text_reply(request, response_id=response_id, text="Noted."))


@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_delta_done_disagreement_is_recorded_and_the_completion_is_what_runs(rig: Rig):
    """Acceptance (c): the deltas spell one payload and both completions spell another. The codec
    executes the completion, exactly once. Sage records `mismatch` and names no culprit."""
    delta_text = json.dumps(ARGS_A, ensure_ascii=False)
    done_text = json.dumps(ARGS_B, ensure_ascii=False)
    with _served(rig.app) as sage_url, rig.opencode(sage_url):
        rig.gateway.expect(lambda request: _one_call(request, response_id="resp_m",
                                                     deltas_text=delta_text, done_text=done_text))
        _fallback(rig, "resp_m2")
        with rig.turn():
            rig.prompt("Call probe once.", inferences=2)
        assert [row["args"] for row in rig.executed()] == [ARGS_B]
        assert rig.tool_outputs(rig.gateway.seen[1]) == {"call_x": "recorded b"}
        [lane] = _lanes(rig.records[-1], 1)
        assert lane["boundary"] == "mismatch" and lane["terminal"] == "closed"
        assert lane["deltaJoin"]["bytes"] == len(delta_text.encode())
        assert lane["done"] == {"source": "both", "bytes": len(done_text.encode()),
                                "jsonValidity": "valid_object", "agreement": "agree"}
        # Unknown origin stays unknown: the lane carries the comparison and no verdict field.
        assert set(lane) == {"lane", "providerId", "name", "outputIndex", "deltaJoin", "done",
                             "boundary", "terminal"}
        dumped = json.dumps(rig.records[-1])
        assert "line one" not in dumped and "第" not in dumped


@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
@pytest.mark.parametrize("shape,done_text,validity", [
    ("incomplete", '{"label": "a", "text": "unfin', "invalid"),
    ("malformed", '{"label": "a", "text": }', "invalid"),
])
def test_incomplete_or_malformed_arguments_never_execute(rig: Rig, shape, done_text, validity):
    """Acceptance (b): a completion that does not parse reaches OpenCode unrepaired and the probe
    never runs. No prefix is guessed into a payload. Sage records the validity category only."""
    with _served(rig.app) as sage_url, rig.opencode(sage_url):
        rig.gateway.expect(lambda request: _one_call(request, response_id="resp_" + shape,
                                                     deltas_text=done_text, done_text=done_text))
        _fallback(rig, "resp_after_" + shape)
        with rig.turn():
            messages = rig.prompt("Call probe once.", inferences=1)
        _nothing_ran(rig)
        # Measured on 1.18.4: OpenCode turns the unparseable call into a COMPLETED part of the
        # `invalid` tool whose input names the intended tool and echoes the text with the parse
        # error, and tells the model so on the next inference. The intended tool never runs.
        [part] = rig.tool_parts(messages)
        assert (part["callID"], part["tool"], part["state"]["status"]) == ("call_x", "invalid", "completed")
        assert part["state"]["input"]["tool"] == "probe_probe"
        assert part["metadata"]["openai"]["itemId"] == "fc_x"
        assert len(rig.gateway.seen) == 2
        assert "invalid" in rig.tool_outputs(rig.gateway.seen[1])["call_x"]
        [lane] = _lanes(rig.records[-1], 1)
        assert lane["boundary"] == "match" and lane["done"]["jsonValidity"] == validity
        assert rig.orch._project.last_gateway_error is None  # the provider did not fail
        assert "unfin" not in json.dumps(rig.records[-1])
    assert rig.gateway.protocols[0] is Protocol.RESPONSES


@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
@pytest.mark.parametrize("ending,message_prefix", [
    ("no_terminal", "Gateway stream ended before its terminal event"),
    ("mid_event", "Gateway stream ended inside an event"),
    ("network", "The model gateway stream stopped (ConnectionError). Retry the turn."),
])
def test_a_stream_that_ends_early_runs_nothing_and_reports_the_provider_failure(
        rig: Rig, ending, message_prefix):
    """Acceptance (b): a missing terminal event, a clean EOF inside a frame, and a network break
    after some deltas. The call has no completion, so nothing runs; Sage's error is the one it
    sends OpenCode; the turn lock is still held when the relay returns."""
    text = json.dumps(ARGS_A, ensure_ascii=False)

    def script(request):
        name = Rig.tool_name(request)
        frames = list(function_call(request, response_id="resp_" + ending,
                                    calls=[(0, "fc_x", "call_x", name)],
                                    deltas=[(0, "fc_x", text[:9]), (0, "fc_x", text[9:20])],
                                    completed=False, arguments_done=False, item_done=False))
        yield from frames
        if ending == "mid_event":
            yield b'data: {"type": "response.function_call_arguments.delta", "item_id": "fc_x", "delta": "'
        elif ending == "network":
            raise ConnectionError("synthetic break")

    with _served(rig.app) as sage_url, rig.opencode(sage_url):
        rig.gateway.expect(script)
        _fallback(rig, "resp_after_" + ending)
        with rig.turn() as ticket:
            rig.prompt("Call probe once.", inferences=1)
            assert rig.orch._turn_lock.locked() and rig.orch._turns.running() is ticket
        _nothing_ran(rig)
        assert rig.orch._project.last_gateway_error["message"].startswith(message_prefix)
        [lane] = _lanes(rig.records[-1], 1)
        assert lane["terminal"] == "open" and lane["boundary"] == "open"
        assert lane["deltaJoin"] == {"count": 2, "empty": 0, "bytes": 20}
        assert lane["done"]["source"] == "none"
        call = rig.records[-1]["timing"]["calls"][0]
        assert call["ok"] is False and call["outcome"] in {"error", "incomplete"}
        assert "line one" not in json.dumps(rig.records[-1])


@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_cancelled_call_runs_nothing_and_keeps_the_lane_open(rig: Rig, monkeypatch):
    """Acceptance (b): OpenCode aborts mid-arguments. Sage's cancel reaches the gateway, the lane
    stays open, the call is recorded cancelled, and the lock is still the turn's to release."""
    from sage.shim import keepalive as ka

    monkeypatch.setattr(ka, "KEEPALIVE_INTERVAL_S", 0.2)
    text = json.dumps(ARGS_A, ensure_ascii=False)
    stalled = []

    def script(request, cancel):
        name = Rig.tool_name(request)
        yield from function_call(request, response_id="resp_cancel",
                                 calls=[(0, "fc_x", "call_x", name)],
                                 deltas=[(0, "fc_x", text[:9])],
                                 completed=False, arguments_done=False, item_done=False)
        stalled.append(True)
        cancel.event.wait(60)
        stalled.append(cancel.event.is_set())

    class CancelAware(ScriptedGateway):
        def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
            self.seen.append(copy.deepcopy(request))
            self.protocols.append(protocol)
            yield from self.scripts.pop(0)(request, cancel)

    rig.gateway = CancelAware()
    rig.project.shim._gateway = rig.gateway
    rig.gateway.expect(script)
    with _served(rig.app) as sage_url, rig.opencode(sage_url):
        with rig.turn() as ticket:
            rig.client.send_prompt(rig.sid, "Call probe once.", agent="build",
                                   model={"providerID": "sage-gateway", "modelID": "sage-model"})
            deadline = time.monotonic() + 30
            while not stalled and time.monotonic() < deadline:
                time.sleep(0.05)
            assert stalled == [True]
            rig.client.interrupt(rig.sid)
            deadline = time.monotonic() + 30
            while len(stalled) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert stalled == [True, True], "the cancel did not reach the gateway"
            assert rig.orch._turn_lock.locked() and rig.orch._turns.running() is ticket
            rig.client.wait_for_idle(rig.sid, timeout_s=30, poll_s=0.2, directory=rig.directory)
        _nothing_ran(rig)
        [lane] = _lanes(rig.records[-1], 1)
        assert lane["terminal"] == "open" and lane["deltaJoin"]["count"] == 1
        call = rig.records[-1]["timing"]["calls"][0]
        assert call["outcome"] == "cancelled" and call["ok"] is False
        assert "line one" not in json.dumps(rig.records[-1])
