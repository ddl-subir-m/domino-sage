"""Existing control saves meet the scoped HTTP endpoint and a recording gateway."""
import copy
import json
from contextlib import contextmanager

import pytest

from sage import timing
from sage.gateway.capabilities import RouteCapability, evidence, resolve
from sage.gateway.client import FakeGatewayClient, GatewayUpstreamError
from sage.gateway.protocol import Protocol
from sage.resources.provider import FakeResourceProvider, join_aliases

from .test_a_build_pick_carries_its_own_effort import _client


class VerifiedResources(FakeResourceProvider):
    def reasoning_capability(self, model):
        return next((a.route_capability for a in self.aliases if a.name == model), RouteCapability())


class RecordingGateway(FakeGatewayClient):
    failure = None

    def route(self, request, labels, *, protocol=Protocol.CHAT, cancel=None):
        self.seen.append((copy.deepcopy(request), labels))
        self.seen_protocols.append(protocol)
        if self.failure == "before":
            raise GatewayUpstreamError(403, "https://gateway.example", 'guardrail_blocked private signature')
        if protocol is Protocol.MESSAGES:
            events = [{"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
                      {"type": "message_stop"}]
        elif protocol is Protocol.RESPONSES:
            response = {"store": False, "metadata": request["metadata"], "reasoning": request.get("reasoning", {}),
                        "usage": {"input_tokens": 7, "output_tokens": 1}}
            events = [{"type": "response.created", "response": response},
                      {"type": "response.completed", "response": response}]
        else:
            events = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        if self.failure == "during":
            events = events[:1] + [{"type": "error", "error": {"message": "private signature"}}]
        if self.failure == "translation":
            events = [{"type": "response.created", "response": {"id": "translated"}}]
        if self.failure == "invalid":
            events = [{"type": "error", "error": {"type": "invalid_request_error", "message": "private signature"}}]
        wire = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
        # Split inside fields and across SSE boundaries.
        for offset in range(0, len(wire), 11):
            yield wire[offset:offset + 11]


@pytest.fixture
def running(tmp_path, monkeypatch):
    client, orch = _client(tmp_path, monkeypatch)
    rows = copy.deepcopy(evidence())
    for row in rows:
        row["capabilities"] = ["chat"]
    aliases = [alias for row in rows for alias in join_aliases({row["name"]}, [row], gateway_root=row["gateway"])]
    resources = VerifiedResources(aliases)
    orch._resources = resources
    gateway = RecordingGateway()
    orch._project.shim._gateway = gateway
    return client, orch, gateway


@contextmanager
def active(orch, chat=False):
    project = orch._project
    token = project.control.arm_chat("thread_test") if chat else None
    record = timing.current()
    ticket, state = orch.prepare_stream_turn(
        record.turn_id if record is not None else "turn_native",
        kind="chat" if chat else "build",
        conversation="thread_test", app=not chat)
    assert state == "running"
    ticket.timing_record = record
    project.active_session_id = "ses_native"
    try:
        yield {"X-Session-Id": "ses_native"}
    finally:
        project.active_session_id = None
        orch.release_stream_turn(ticket)
        if token is not None:
            project.control.disarm_chat(token)


def request_body(protocol, model, messages=None):
    messages = messages or [{"role": "user", "content": "answer briefly"}]
    common = {"model": model, "stream": True}
    if protocol is Protocol.MESSAGES:
        return {**common, "max_tokens": 1024, "messages": messages,
                "tools": [{"name": name, "input_schema": {"type": "object"}} for name in ("read", "write", "webfetch")]}
    if protocol is Protocol.RESPONSES:
        return {**common, "input": messages, "store": False,
                "tools": [{"type": "function", "name": name, "parameters": {"type": "object"}} for name in ("read", "write", "webfetch")]}
    return {**common, "messages": messages,
            "tools": [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}} for name in ("read", "write", "webfetch")]}


def dispatch(client, headers, protocol, model, messages=None):
    path = {Protocol.MESSAGES: "anthropic/messages", Protocol.RESPONSES: "responses", Protocol.CHAT: "chat/completions"}[protocol]
    return client.post("/v1/sage/" + path, headers=headers, json=request_body(protocol, model, messages))


@pytest.mark.parametrize("model,protocol,effort", [("Opus-4.8", Protocol.MESSAGES, "high"),
    ("gpt-5.4", Protocol.RESPONSES, "low"), ("GLM 5.3 OR", Protocol.CHAT, "max")])
@pytest.mark.parametrize("surface", ["chat", "build", "assignment"])
@pytest.mark.parametrize("default", [False, True])
def test_saved_choices_reach_the_gateway_through_each_existing_control(running, model, protocol, effort, surface, default):
    client, orch, gateway = running
    effort = None if default else effort
    saved = ({"chat_model": model, "reasoning_effort": effort} if surface == "chat" else
             {"catalog": {"plan": {"model": model, "effort": effort}}} if surface == "assignment" else
             {"pick": model, "pick_effort": effort, "mode": "plan"})
    response = client.post("/api/project/model", json=saved)
    assert response.status_code == 200, response.text
    with active(orch, chat=surface == "chat") as headers:
        chosen = client.post("/v1/sage/resolve", headers=headers,
                             json={"prompt": [{"role": "user", "content": "answer briefly"}], "tools": []})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json() == {"model": model, "protocol": protocol.value, "effort": effort,
                                 "native": protocol is not Protocol.CHAT}
        assert orch._project.model_calls == 0
        answer = dispatch(client, headers, protocol, model)
        assert answer.status_code == 200, answer.text
        assert orch._project.model_calls == 1
    outbound, labels = gateway.seen[-1]
    assert gateway.seen_protocols == [protocol]
    proof = next(row for row in evidence() if row["name"] == model)
    settings = resolve(proof["gateway"], proof, evidence()).settings(effort, tools=True)
    assert {k: outbound[k] for k in ("reasoning", "thinking", "output_config", "reasoning_effort") if k in outbound} == settings
    assert labels.session == "ses_native"
    assert orch._project.resolved_model.model == model
    recorded = orch._project.resolved_row()["resolved"]
    assert (recorded["protocol"], recorded["effort"], recorded["native"]) == (protocol.value, effort, protocol is not Protocol.CHAT)


@pytest.mark.parametrize("model,protocol", [("Opus-4.8", Protocol.MESSAGES), ("gpt-5.4", Protocol.RESPONSES),
                                            ("GLM 5.3 OR", Protocol.CHAT)])
def test_read_only_approval_and_web_policy_apply_before_all_upstream_routes(running, model, protocol):
    client, orch, gateway = running
    assert client.post("/api/project/model", json={"mode": "plan", "pick": model}).status_code == 200
    token = orch._project.control.arm_read_only("plan")
    try:
        with active(orch) as headers:
            answer = dispatch(client, headers, protocol, model)
            assert answer.status_code == 200, answer.text
        outbound = gateway.seen[-1][0]
        names = [t.get("name") or t["function"]["name"] for t in outbound["tools"]]
        assert names == ["read"]
    finally:
        orch._project.control.disarm_read_only(token)


def test_scope_stale_route_and_invalid_save_do_not_reach_the_gateway(running):
    client, orch, gateway = running
    assert dispatch(client, {}, Protocol.MESSAGES, "Opus-4.8").status_code == 400
    assert client.post("/api/project/model", json={"pick": "Opus-4.8", "pick_effort": "invalid"}).status_code == 400
    client.post("/api/project/model", json={"mode": "plan", "pick": "gpt-5.4", "pick_effort": "high"})
    with active(orch) as headers:
        assert dispatch(client, headers, Protocol.MESSAGES, "Opus-4.8").status_code == 400
        assert dispatch(client, {"X-Session-Id": "another"}, Protocol.RESPONSES, "gpt-5.4").status_code == 400
    assert gateway.seen == []


@pytest.mark.parametrize("failure", ["before", "during", "translation"])
def test_refusals_and_translated_responses_do_not_report_success_or_private_state(running, failure):
    client, orch, gateway = running
    client.post("/api/project/model", json={"mode": "plan", "pick": "gpt-5.4", "pick_effort": "high"})
    gateway.failure = failure
    with active(orch) as headers:
        response = dispatch(client, headers, Protocol.RESPONSES, "gpt-5.4")
    assert response.status_code == 502 or '"type": "error"' in response.text
    assert "private signature" not in response.text
    assert orch._project.last_gateway_error


@pytest.mark.parametrize("model,protocol", [("Opus-4.8", Protocol.MESSAGES), ("gpt-5.4", Protocol.RESPONSES),
                                            ("GLM 5.3 OR", Protocol.CHAT)])
def test_invalid_request_in_http_200_stream_does_not_trigger_a_retry_loop(running, model, protocol):
    client, orch, gateway = running
    assert client.post("/api/project/model", json={"mode": "plan", "pick": model}).status_code == 200
    gateway.failure = "invalid"
    with active(orch) as headers:
        response = dispatch(client, headers, protocol, model)
    assert response.status_code == 400
    assert response.json() == {"error": {"message": "The model stream failed: invalid_request_error"}}
    assert "private signature" not in response.text
    assert orch._project.last_gateway_error


def test_auto_write_transition_and_rescue_use_one_decision_before_and_after_serialization(running):
    client, orch, gateway = running
    saved = client.post('/api/project/model', json={"mode": "auto", "catalog": {
        "plan": {"model": "Opus-4.8", "effort": "high"},
        "implement": {"model": "gpt-5.4", "effort": "low"}}})
    assert saved.status_code == 200, saved.text
    prompt = [{"role": "user", "content": [{"type": "text", "text": "Build and keep the cobalt plan."}]}]
    with active(orch) as headers:
        for step, expected in enumerate(("Opus-4.8", "gpt-5.4", "Opus-4.8")):
            if step:
                tool = "write" if step == 1 else "bash"
                output = "Wrote file" if step == 1 else "SyntaxError: synthetic check"
                prompt += [{"role": "assistant", "content": [{"type": "tool-call", "toolCallId": f"c{step}",
                            "toolName": tool, "input": {"filePath": "CHECK.txt"}}]},
                           {"role": "tool", "content": [{"type": "tool-result", "toolCallId": f"c{step}",
                            "toolName": tool, "output": {"type": "text", "value": output}}]}]
            choice = client.post('/v1/sage/resolve', headers=headers, json={"prompt": prompt, "tools": []}).json()
            assert choice["model"] == expected
            # Re-create the codec's native representation of this same SDK history.
            from sage.shim.native import NativeView, sdk_view
            protocol = Protocol(choice["protocol"])
            canonical = sdk_view(prompt, [])
            canonical["model"] = expected
            wire = NativeView({"model": expected}, protocol).render(canonical)
            wire.update(stream=True, max_tokens=1024)
            path = 'anthropic/messages' if protocol is Protocol.MESSAGES else 'responses'
            result = client.post('/v1/sage/' + path, headers=headers, json=wire)
            assert result.status_code == 200, result.text
            assert "cobalt plan" in json.dumps(gateway.seen[-1][0])
            assert orch._project.resolved_model.model == expected
    assert gateway.seen_protocols == [Protocol.MESSAGES, Protocol.RESPONSES, Protocol.MESSAGES]
    assert [request.get('reasoning', request.get('output_config')) for request, _ in gateway.seen] == [
        {"effort": "high"}, {"effort": "low"}, {"effort": "high"}]


def test_downstream_disconnect_stops_a_bounded_producer_and_closes_its_upstream(running):
    import asyncio
    import threading
    from types import SimpleNamespace

    from sage.orchestrator import app as appmod

    client, orch, _ = running
    client.post('/api/project/model', json={"mode": "plan", "pick": "Opus-4.8"})
    closed = threading.Event()
    producer_closed = threading.Event()
    produced = []
    class EndlessGateway(FakeGatewayClient):
        def route(self, request, labels, *, protocol, cancel):
            cancel.bind(closed.set)
            try:
                for n in range(10000):
                    produced.append(n)
                    yield b': thinking\n\n'
            finally:
                producer_closed.set()
    orch._project.shim._gateway = EndlessGateway()
    raw = json.dumps(request_body(Protocol.MESSAGES, 'Opus-4.8')).encode()
    async def body():
        return raw
    endpoint = next(r.endpoint for r in appmod.control_app.routes if r.path == '/v1/sage/anthropic/messages')
    async def consume(headers):
        request = SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, body=body,
                                  url=SimpleNamespace(path='/v1/sage/anthropic/messages'))
        response = await endpoint(request)
        await anext(response.body_iterator)
        await response.body_iterator.aclose()
    with active(orch) as headers:
        asyncio.run(consume(headers))
    assert closed.wait(1) and producer_closed.wait(1)
    assert len(produced) <= 11  # one first frame, eight queued, and one in flight (plus scheduling).


def test_a_native_call_puts_its_first_byte_in_the_log_ring(running, caplog):
    """The legacy `/v1/chat/completions` handler has always logged "model call -> streaming
    (first byte Xs)"; the scoped route recorded the same moment in the ledger and said nothing in
    the ring. On 2026-09-21 a Build call went 121s with no OpenCode output and the ring could not
    say whether the gateway had answered — every call of that turn had gone through this route."""
    import logging
    client, orch, _ = running
    client.post("/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"})
    with caplog.at_level(logging.INFO, logger="sage.orchestrator"), active(orch) as headers:
        assert dispatch(client, headers, Protocol.CHAT, "GLM 5.3 OR").status_code == 200
    lines = [r.getMessage() for r in caplog.records if r.name == "sage.orchestrator"]
    assert any(line.startswith("model call -> streaming (first byte ") for line in lines), lines


def _open_then_close(orch, gateway, monkeypatch, caplog, after):
    """Open one scoped call against `gateway`, let `after()` decide when, then close the response
    the way OpenCode's aborted fetch does. Returns the warning lines the close left behind."""
    import asyncio
    import logging
    from types import SimpleNamespace

    import sage.shim.keepalive as ka
    from sage.orchestrator import app as appmod

    monkeypatch.setattr(ka, "FIRST_BYTE_BUDGET_S", 0.05)
    monkeypatch.setattr(ka, "KEEPALIVE_INTERVAL_S", 0.05)
    orch._project.shim._gateway = gateway
    raw = json.dumps(request_body(Protocol.CHAT, "GLM 5.3 OR")).encode()

    async def body():
        return raw
    endpoint = next(r.endpoint for r in appmod.control_app.routes if r.path == "/v1/sage/chat/completions")

    async def consume(headers):
        request = SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, body=body,
                                  url=SimpleNamespace(path="/v1/sage/chat/completions"))
        response = await endpoint(request)
        await anext(response.body_iterator)
        after()
        await response.body_iterator.aclose()
    with caplog.at_level(logging.INFO, logger="sage.orchestrator"), active(orch) as headers:
        asyncio.run(consume(headers))
    return [r.getMessage() for r in caplog.records
            if r.name == "sage.orchestrator" and r.levelno >= logging.WARNING]


def test_a_cancelled_call_the_gateway_never_answered_says_so(running, monkeypatch, caplog):
    import threading
    client, orch, _ = running
    client.post("/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"})
    closed = threading.Event()

    class SilentGateway(FakeGatewayClient):
        def route(self, request, labels, *, protocol, cancel):
            cancel.bind(closed.set)
            closed.wait(5)      # the model thinks, or the gateway hangs: not one byte either way
            return
            yield  # pragma: no cover - generator marker
    warned = _open_then_close(orch, SilentGateway(), monkeypatch, caplog, after=lambda: None)
    assert closed.wait(1)
    assert any(line.startswith("model call cancelled after ") and line.endswith("no bytes from the gateway")
               for line in warned), warned


def test_a_cancelled_call_the_gateway_was_answering_counts_what_arrived(running, monkeypatch, caplog):
    import threading
    client, orch, _ = running
    client.post("/api/project/model", json={"mode": "plan", "pick": "GLM 5.3 OR"})
    closed, sent = threading.Event(), threading.Event()

    class TwoThenSilent(FakeGatewayClient):
        def route(self, request, labels, *, protocol, cancel):
            cancel.bind(closed.set)
            yield b": thinking\n\n"
            yield b": thinking\n\n"
            sent.set()          # resumed past the second yield: the pump has counted both
            closed.wait(5)
    warned = _open_then_close(orch, TwoThenSilent(), monkeypatch, caplog, after=lambda: sent.wait(1))
    assert closed.wait(1)
    assert any(line.startswith("model call cancelled after ") and line.endswith("2 chunks had arrived")
               for line in warned), warned


def test_installed_native_config_keeps_one_handle_and_local_codecs(running, tmp_path, monkeypatch):
    from pathlib import Path

    from sage.orchestrator import app as appmod
    _, _, _ = running
    source = tmp_path / 'source'
    source.mkdir()
    config = json.loads((Path(__file__).parents[2] / 'opencode.json').read_text())
    (source / 'opencode.json').write_text(json.dumps(config))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    # The tmp source carries no codec module. This test is about the config that gets written, so
    # say the codec loads; the two tests below own the case where it does not.
    monkeypatch.setattr(appmod, '_native_codec_unavailable', lambda codec: '')
    appmod._install_opencode_config(source, 9876)
    for folder in ('opencode', 'sage-opencode'):
        installed = json.loads((tmp_path / 'home/.config' / folder / 'opencode.json').read_text())
        provider = installed['provider']['sage-gateway']
        assert provider['npm'] == (source / 'backend/sage/driver/provider.mjs').as_uri()
        # Two handles (#539): the neutral default OpenCode reads its edit tools from, and gpt-5.4
        # kept so a session saved under it still resolves.
        assert list(provider['models']) == ['gpt-5.4', 'sage-model']
        assert all(m['reasoning'] is True for m in provider['models'].values())
        assert installed['model'] == installed['small_model'] == 'sage-gateway/sage-model'
        assert provider['options']['baseURL'] == 'http://localhost:9876/v1'
        assert provider['options']['name'] == 'google'
    assert json.loads((source / 'opencode.json').read_text()) == config


def test_a_codec_that_will_not_load_leaves_the_config_on_the_shims_protocol(
        running, tmp_path, monkeypatch, caplog):
    """A declared provider module OpenCode cannot import kills every turn of the session (#482).

    The image installs the module's SDKs and self-update moves the code, so a tree newer than the
    image names imports that are not installed. Writing the handle anyway is the one outcome worth
    avoiding: the shim's protocol needs no npm, and a turn without reasoning settings beats a
    session where nothing runs at all."""
    import logging
    from pathlib import Path

    from sage.orchestrator import app as appmod
    _, _, _ = running
    source = tmp_path / 'source'
    source.mkdir()
    config = json.loads((Path(__file__).parents[2] / 'opencode.json').read_text())
    (source / 'opencode.json').write_text(json.dumps(config))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(appmod, '_native_codec_unavailable',
                        lambda codec: "Cannot find package '@ai-sdk/anthropic'")
    with caplog.at_level(logging.ERROR):
        appmod._install_opencode_config(source, 9876)
    source_provider = config['provider']['sage-gateway']
    for folder in ('opencode', 'sage-opencode'):
        installed = json.loads((tmp_path / 'home/.config' / folder / 'opencode.json').read_text())
        provider = installed['provider']['sage-gateway']
        # `npm` is not the discriminator — the checked-in config has one already, naming a
        # published package OpenCode resolves itself. What the native branch does is swap that
        # name for a file URI, cut ten models to one, add `reasoning`, and pin `small_model`.
        assert provider['npm'] == source_provider['npm'] == '@ai-sdk/openai-compatible'
        assert list(provider['models']) == list(source_provider['models'])
        assert 'reasoning' not in provider['models']['gpt-5.4']
        assert 'small_model' not in installed and 'small_model' not in config
        assert installed['model'] == config['model']
        # The port rewrite is not the native branch's and still has to happen.
        assert provider['options']['baseURL'] == 'http://localhost:9876/v1'
    assert "Cannot find package '@ai-sdk/anthropic'" in caplog.text
    assert 'Rebuild the Environment image' in caplog.text


def test_the_codec_probe_answers_from_the_loader_and_not_from_a_declaration(tmp_path):
    """The probe has to name the package, because the person reading the log has to act on it.

    A missing FILE and an unresolved IMPORT are different repairs — one is a bad path, the other is
    an image that predates the code — and the log line is the only place that distinction survives."""
    import shutil

    from sage.orchestrator.app import _native_codec_unavailable
    if shutil.which('node') is None:
        pytest.skip('Node is required to ask the module loader anything')
    codec = tmp_path / 'provider.mjs'
    codec.write_text("import { nothing } from '@sage-test/not-a-real-package';\nexport { nothing };\n")
    assert '@sage-test/not-a-real-package' in _native_codec_unavailable(codec)
    absent = tmp_path / 'absent.mjs'
    assert _native_codec_unavailable(absent) == f'{absent} is not there'
    ok = tmp_path / 'ok.mjs'
    ok.write_text('export const ok = true;\n')
    assert _native_codec_unavailable(ok) == ''


def test_the_shipped_codec_loads_where_its_dependencies_are_installed():
    """The production question, asked of the real module rather than of a stand-in.

    This skips exactly where the defect lives — a tree whose `node_modules` predates its
    `package.json` — so the skip is the finding and `-rs` is how you see it."""
    import shutil
    from pathlib import Path

    from sage.orchestrator.app import _native_codec_unavailable
    if shutil.which('node') is None:
        pytest.skip('Node is required to ask the module loader anything')
    repo = Path(__file__).resolve().parents[2]
    if not (repo / 'node_modules/@ai-sdk/anthropic').exists():
        pytest.skip('the root node_modules does not carry the pinned SDKs — run `npm ci` in the '
                    'repo root; this is the same state that breaks a workspace (#482)')
    assert _native_codec_unavailable(repo / 'backend/sage/driver/provider.mjs') == ''


def test_harness_verified_child_sessions_keep_the_parent_turn_policy(running):
    from types import SimpleNamespace
    client, orch, gateway = running
    client.post('/api/project/model', json={"mode": "plan", "pick": "Opus-4.8", "pick_effort": "high"})
    parents = []
    def belongs(child, parent):
        parents.append((child, parent))
        return child == "ses_child" and parent == "ses_native"
    orch._oc_client = SimpleNamespace(session_belongs_to=belongs)
    token = orch._project.control.arm_read_only("plan")
    try:
        with active(orch):
            assert dispatch(client, {"X-Session-Id": "ses_child"}, Protocol.MESSAGES, "Opus-4.8").status_code == 200
            assert dispatch(client, {"X-Session-Id": "ses_unrelated"}, Protocol.MESSAGES, "Opus-4.8").status_code == 400
    finally:
        orch._project.control.disarm_read_only(token)
    assert parents == [("ses_child", "ses_native"), ("ses_unrelated", "ses_native")]
    assert [t["name"] for t in gateway.seen[0][0]["tools"]] == ["read"]
    assert gateway.seen[0][1].session == "ses_child"


@pytest.mark.parametrize('model,protocol', [('Opus-4.8', Protocol.MESSAGES), ('gpt-5.4', Protocol.RESPONSES), ('GLM 5.3 OR', Protocol.CHAT)])
def test_policy_checkpoint_is_actionable_and_keeps_the_visible_conversation(running, model, protocol):
    from sage.orchestrator import recall
    from sage.shim.chat_paths import text_key
    from sage.workspace.threads import ThreadStore
    client, orch, _gateway = running
    client.post('/api/project/model', json={'mode': 'plan', 'pick': model})
    with active(orch) as headers:
        assert dispatch(client, headers, protocol, model).status_code == 200
        token = orch._project.control.arm_withheld(frozenset({'removed'}))
        try:
            answer = dispatch(client, headers, protocol, model)
        finally:
            orch._project.control.disarm_withheld(token)
    assert answer.status_code == 400
    raw = orch._project.last_gateway_error['message']
    reason = recall.reason_key(raw)
    assert reason == recall.POLICY_CHANGE
    store = ThreadStore(orch._project.record.path)
    thread = store.create('Checkpoint')
    tid = thread['id']
    store.append_history(tid, {'type': 'user', 'text': 'Remember cobalt'})
    private = 'synthetic text removed before the checkpoint'
    later_private = 'synthetic text removed after the checkpoint'
    store.append_history(tid, {'type': 'user', 'text': private})
    store.append_history(tid, {'type': 'user', 'text': later_private})
    store.append_history(tid, {'type': recall.WITHHELD, 'keys': [text_key({'content': private})]})
    store.append_history(tid, {'type': 'error', 'reason': reason, 'message': raw})
    offer = orch._record_recall_offer(store, tid)
    assert offer == {'type': recall.SUGGEST, 'scope': recall.SUMMARY, 'reason': reason}
    cleared = orch.clear_recall(tid, recall.SUMMARY)
    assert cleared['reason'] == reason
    store.append_history(tid, {'type': recall.WITHHELD, 'keys': [text_key({'content': later_private})]})
    store.append_history(tid, {'type': 'user', 'text': 'Continue'})
    seed = recall.reseed(store.read_history(tid))
    assert 'cobalt' in seed and private not in seed and later_private not in seed
    # Transport failures retain the existing refusal rules.
    assert recall.offer([{'type': 'error', 'reason': 'network timeout'}] * 2) is None
    with active(orch) as headers:
        headers['X-Session-Id'] = orch._project.active_session_id = 'ses_fresh'
        token = orch._project.control.arm_withheld(frozenset({'removed'}))
        try:
            assert dispatch(client, headers, protocol, model).status_code == 200
        finally:
            orch._project.control.disarm_withheld(token)


@pytest.mark.parametrize('surface', ['chat', 'build'])
def test_policy_checkpoint_card_reuses_the_clear_button_with_accurate_words(surface):
    from .test_the_way_out_of_a_refused_conversation_reads import _buttons, _render, _text
    block = {'type': 'recall_offer', 'scope': 'summary', 'reason': 'native-policy-change'}
    if surface == 'build':
        block['surface'] = 'build'
    rendered = _render(block)
    text = _text(rendered)
    assert 'access rules changed' in text
    assert 'gateway has refused' not in text
    assert _buttons(rendered)[0]['text'] == 'Clear recall'
    assert ('app, plan and transcript stay' if surface == 'build' else 'continue with a summary') in text


def test_the_body_that_reaches_the_gateway_carries_its_cache_breakpoints(running):
    client, orch, gateway = running
    # Assign the Claude alias to the plan slot, or Auto resolves the turn to gpt-5.4 and the route
    # refuses the mismatch before any body is built.
    assert client.post("/api/project/model",
                       json={"catalog": {"plan": {"model": "Opus-4.8", "effort": None}}}).status_code == 200
    with active(orch) as headers:
        assert dispatch(client, headers, Protocol.MESSAGES, "Opus-4.8").status_code == 200
    sent, _ = gateway.seen[-1]
    # The marker only earns anything if it survives the whole route, not just the helper: the
    # gateway forwards this body to `/anthropic/v1/messages` unchanged (#495).
    assert sent["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert json.dumps(sent).count('"cache_control"') == 1
    # One marker, not three, and that is the shape of this request rather than a short count: the
    # fixture body has no `system` and a single message, so two of the three placements have
    # nothing to land on. `test_native_policy` holds the three-breakpoint case.
    assert "system" not in sent and len(sent["messages"]) == 1
