"""What OpenCode 1.18.4 does with a tool call it cannot use, asked of the binary (#565).

`_invalid_tool_call` classifies a completed `invalid` wrapper as "the intended tool never ran", and
Sage sends the turn again on the strength of that. A replay over a call that DID run would run it
twice, so the claim is pinned here against the pinned binary rather than against a reading of its
bundle: a scripted OpenAI-compatible model, real OpenCode, and the session's transcript and
directory afterwards.

Three ways a call arrives unusable, one session each on one boot. Two of them — arguments that
are not JSON, and a tool name OpenCode does not offer — land as the wrapper: one completed
`invalid` part with `{tool, error}` as its input, no part under the intended tool, no file on
disk, and a tool result back to the model that quotes the raw arguments, which is why nothing from
that `error` may reach a person (see the sanitization plant in
test_a_broken_tool_call_ends_the_build_out_loud.py).

The third does NOT, and that is measured here rather than assumed: arguments that are JSON but
fail the tool's schema reach OpenCode's own check inside the real tool, which fails the `write`
part with `status: "error"` and a SchemaError sentence, and no file is written either. Telling
that part from a tool that ran and failed takes reading the sentence, which the classifier must
not do, so it stays silent there and Sage makes no replay. The model is told to rewrite the input
and usually does; a turn that ends on it ends the ordinary way. Recorded so the next reader does
not widen the classifier onto a `status: "error"` part.

No gateway is stood up and no model is spent; the scripted server answers `/v1/chat/completions`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sage.driver.opencode import OpenCodeClient
from sage.orchestrator.service import _invalid_tool_call

from .opencode_server import BINARY, _opencode_server

REPO = Path(__file__).resolve().parents[2]

# case -> (tool the model names, its arguments, the category the wrapper classifies as or None
# when no wrapper is emitted, the prefix the error opens with)
CASES = {
    "not-json": ("write", '{"filePath": "src/Dashboard.tsx", "conte', "invalid_arguments",
                 "Invalid input for tool write"),
    "wrong-shape": ("write", '{"filePath": 123}', None,
                    "The write tool was called with invalid arguments"),
    "no-such-tool": ("wrtie", '{"filePath": "src/Dashboard.tsx", "content": "x"}', "unknown_tool",
                     "Model tried to call unavailable tool"),
}


def _sse(delta: dict, finish: str) -> bytes:
    frame = {"id": "scripted", "object": "chat.completion.chunk", "model": "alias",
             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    first = "data: " + json.dumps(frame) + "\n\n"
    frame["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish}]
    return (first + "data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode()


@pytest.mark.opencode
@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_a_call_opencode_cannot_use_lands_as_a_completed_invalid_part_and_runs_nothing(tmp_path):
    calls: list[dict] = []
    failures: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                messages = body.get("messages", [])
                text = json.dumps(messages)
                if "title generator" in str(messages[0].get("content", "")).lower():
                    self.wfile.write(_sse({"content": "Dashboard"}, "stop"))
                    return
                calls.append(body)
                case = next((name for name in CASES if f"CASE:{name}" in text), None)
                assert case is not None, text[:300]
                if any(m.get("role") == "tool" for m in messages):
                    # The turn after the tool result: answer and stop, so the session goes idle.
                    self.wfile.write(_sse({"content": "Stopping here."}, "stop"))
                    return
                tool, arguments, _category, _prefix = CASES[case]
                self.wfile.write(_sse({"tool_calls": [{
                    "index": 0, "id": f"call-{case}", "type": "function",
                    "function": {"name": tool, "arguments": arguments}}]}, "tool_calls"))
            except Exception as error:  # reported by the test, not swallowed
                failures.append(repr(error))

        def log_message(self, *ignored):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = json.loads((REPO / "opencode.json").read_text())
    config["provider"]["sage-gateway"]["options"]["baseURL"] = f"http://127.0.0.1:{server.server_port}/v1"
    config["plugin"] = []
    config["mcp"] = {}
    config["permission"] = {"*": "allow"}
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "opencode.json").write_text(json.dumps(config))
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(runtime / "opencode.json"), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"))
    try:
        with _opencode_server(runtime, env) as url:
            client = OpenCodeClient(url)
            for case, (tool, arguments, category, prefix) in CASES.items():
                directory = tmp_path / case
                directory.mkdir()
                sid = client.create_session(str(directory))
                seen_before = len(calls)
                client.send_prompt(sid, f"CASE:{case}. Write the dashboard file.", agent="build")
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    assert not failures, failures
                    if (len(calls) >= seen_before + 2
                            and not client.is_running(sid, directory=str(directory))):
                        break
                    time.sleep(0.1)
                else:
                    pytest.fail(f"{case}: the turn did not finish; {len(calls)} calls; {failures}")

                parts = [p for m in client.messages(sid) if m.get("type") == "assistant"
                         for p in m.get("content", []) if isinstance(p, dict)
                         and p.get("type") == "tool"]
                # Kept beside the runtime, like the CSV rig's `requests.json`: a red here is
                # read from the file, since an assertion message clips the part.
                (runtime / f"{case}-parts.json").write_text(json.dumps(parts, indent=2))
                if category is None:
                    # The schema failure: the REAL tool's part, in `error`, with nothing written.
                    assert [p.get("tool") for p in parts] == [tool], (case, parts)
                    failed = parts[0]
                    assert failed["state"]["status"] == "error", (case, failed)
                    assert failed["state"]["error"].startswith(prefix), (case, failed)
                    assert _invalid_tool_call(failed) is None, (case, failed)
                    assert not (directory / "src").exists(), (case, list(directory.rglob("*")))
                    told = [m for m in calls[-1]["messages"] if m.get("role") == "tool"]
                    assert len(told) == 1 and prefix in str(told[0]["content"])
                    continue
                wrappers = [p for p in parts if p.get("tool") == "invalid"]
                assert len(wrappers) == 1, (case, runtime / f"{case}-parts.json")
                wrapper = wrappers[0]
                # The contract `_invalid_tool_call` reads: the wrapper is COMPLETED, its input is
                # `{tool, error}` naming the intended tool, and its error opens with the SDK's
                # prefix the category is keyed on.
                assert wrapper["state"]["status"] == "completed", (case, wrapper)
                assert wrapper["state"]["input"]["tool"] == tool, (case, wrapper)
                assert wrapper["state"]["input"]["error"].startswith(prefix), (case, wrapper)
                assert wrapper["state"]["output"].startswith(
                    "The arguments provided to the tool are invalid:"), (case, wrapper)
                fault = _invalid_tool_call(wrapper)
                assert fault is not None and fault.completed and fault.tool == tool
                assert fault.category == category, (case, fault)
                assert fault.call_id == str(wrapper.get("callID") or wrapper.get("id"))
                # The intended tool never ran: no part under its name, nothing on disk.
                assert not [p for p in parts if p.get("tool") == tool], (case, parts)
                assert not (directory / "src").exists(), (case, list(directory.rglob("*")))
                # And the model was told, with the raw arguments quoted back at it. That is the
                # text the sanitization plant keeps away from people.
                told = [m for m in calls[-1]["messages"] if m.get("role") == "tool"]
                assert len(told) == 1 and "The arguments provided to the tool are invalid" in str(told[0]["content"])
                if case != "no-such-tool":
                    assert "Dashboard.tsx" in str(told[0]["content"]) or "123" in str(told[0]["content"])
    finally:
        server.shutdown()
        server.server_close()
