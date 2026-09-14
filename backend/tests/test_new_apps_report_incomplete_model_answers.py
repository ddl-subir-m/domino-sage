"""#358: exercise the seeded helper through real gateway and preview HTTP boundaries.

The browser test uses the same helper/consumer with a real viewer cookie. Set
SAGE_APP_BROWSER_MODULE to an installed playwright/index.mjs to run that optional boundary.
No Sage startup, shared HOME state, live provider, or real credentials are needed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sage.preview.proxy import make_preview_app
from sage.workspace.manager import WorkspaceManager
from tests.test_preview_llm import _no_vite, _served

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "template/react-vite"
HARNESS = Path(__file__).parent / "js/app_model_outcome_harness.mjs"
TEXT = '{"total":780}'
REFUSAL = {"detail": {"error": {"type": "guardrail_blocked",
                               "message": "Blocked by guardrail: Synthetic rule",
                               "input": "PRIVATE SYNTHETIC VALUE"}}}
CASES = {
    "allowed_before": (False, None), "http_refused": (False, "refused"),
    "allowed_after": (False, None), "http_auth": (False, "authentication"),
    "http_access": (False, "access"), "http_busy": (False, "rate_limit"),
    "http_provider": (False, "provider"), "whole_length": (False, "incomplete"),
    "whole_missing_finish": (False, "incomplete"), "whole_invalid": (False, "invalid_response"),
    "whole_refused": (False, "refused"), "stream_complete": (True, None), "stream_done_held": (True, None),
    "stream_crlf": (True, None), "stream_refused": (True, "refused"),
    "stream_provider": (True, "provider"), "event_error": (True, "provider"), "event_error_crlf": (True, "provider"),
    "stream_invalid_content": (True, "invalid_response"),
    "stream_eof": (True, "incomplete"), "stream_tail": (True, "incomplete"),
    "stream_malformed": (True, "invalid_response"), "stream_length": (True, "incomplete"),
    "stream_filtered": (True, "refused"), "stop_without_done": (True, "incomplete"),
    "stop_then_error": (True, "refused"), "cancelled": (True, "cancelled"),
    "done_without_stop": (True, "incomplete"), "text_after_stop": (True, "invalid_response"),
    "stream_transport": (True, "transport"),
    "fallback": (False, None), "cache": (False, None),
}


def _event(body):
    return f"data: {json.dumps(body)}\n\n".encode()


class Gateway:
    def __init__(self):
        self.seen = []
        self.release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("content-type", "text/html")
                self.end_headers()
                self.wfile.write(b'<!doctype html><title>New app model outcomes</title>')

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["content-length"])))
                name = request["messages"][0]["content"]
                outer.seen.append((self.path, dict(self.headers), request))
                status = {"http_refused": 400, "http_auth": 401, "http_access": 403,
                          "http_busy": 429, "http_provider": 502}.get(name, 200)
                self.send_response(status)
                self.send_header("content-type", "text/event-stream" if request["stream"] else "application/json")
                self.send_header("x-request-id", "synthetic-request")
                if name == "fallback":
                    self.send_header("x-llm-fallback-served-by", "synthetic-fallback")
                    self.send_header("x-llm-fallback-reason", "timeout")
                if name == "cache":
                    self.send_header("x-cache", "HIT")
                if name == "stream_transport":
                    self.send_header("content-length", "99999")
                self.end_headers()
                if not request["stream"]:
                    body = {"model": "synthetic-response-model", "choices": [
                        {"message": {"content": TEXT}, "finish_reason": "stop"}]}
                    if name == "http_refused":
                        body = REFUSAL
                    elif status != 200:
                        body = {"error": {"type": "server_error", "message": "PRIVATE SYNTHETIC VALUE"}}
                    elif name == "whole_length":
                        body["choices"][0]["finish_reason"] = "length"
                    elif name == "whole_missing_finish":
                        del body["choices"][0]["finish_reason"]
                    elif name == "whole_refused":
                        body["choices"][0]["message"]["refusal"] = "PRIVATE SYNTHETIC VALUE"
                    self.wfile.write(b"<html>not JSON</html>" if name == "whole_invalid" else json.dumps(body).encode())
                    return
                first = _event({"choices": [{"delta": {"content": TEXT}}]})
                stop = _event({"choices": [{"delta": {}, "finish_reason": "stop"}]})
                end = b"data: [DONE]\n\n"
                error = _event({"error": REFUSAL["detail"]["error"]})
                suffix = {
                    "stream_refused": error,
                    "stream_provider": _event({"error": {"type": "server_error", "message": "PRIVATE SYNTHETIC VALUE"}}),
                    "event_error": b'event: error\ndata: {"message":"PRIVATE SYNTHETIC VALUE"}\n\n',
                    "event_error_crlf": b'event: error\r\ndata: {"message":"PRIVATE SYNTHETIC VALUE"}\r\n\r\n',
                    "stream_invalid_content": _event({"choices": [{"delta": {"content": 42}}]}) + stop + end,
                    "stream_eof": b"", "stream_tail": b'data: {"choices":',
                    "stream_malformed": b'data: invalid\n\n' + end,
                    "stream_length": _event({"choices": [{"finish_reason": "length"}]}) + end,
                    "stream_filtered": _event({"choices": [{"finish_reason": "content_filter"}]}) + end,
                    "stop_without_done": stop, "stop_then_error": stop + error + end,
                    "done_without_stop": end, "text_after_stop": stop + first + end,
                    "stream_transport": b"",
                }.get(name, stop + end)
                try:
                    if name == "stream_crlf":
                        # Every byte separately: delimiters and JSON can cross network chunks.
                        for byte in (first + suffix).replace(b"\n", b"\r\n"):
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                        return
                    self.wfile.write(first)
                    self.wfile.flush()
                    if name == "cancelled":
                        outer.release.wait(2)
                    self.wfile.write(suffix)
                    self.wfile.flush()
                    if name == "stream_done_held":
                        outer.release.wait(10)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def gateway():
    gateway = Gateway()
    try:
        yield gateway
    finally:
        gateway.close()


@pytest.fixture
def generated(tmp_path):
    # A fresh app's actual seeded source. Dependency install and OpenCode startup are unnecessary.
    ws = WorkspaceManager(tmp_path / "app", TEMPLATE)
    ws.ensure("synthetic-project")
    return ws


def _run(gateway, generated, preview_base, preview, browser_module=None, page_base=None, cases=CASES):
    if not shutil.which("node"):
        pytest.skip("node is required to execute the generated TypeScript helper")
    data = {"gateway": gateway.base + "/v1", "previewBase": preview_base, "preview": preview,
            "pageBase": page_base or gateway.base, "browserModule": browser_module,
            "helper": str(generated.app_path / "src/appLlm.ts"),
            "cases": [{"name": name, "stream": stream} for name, (stream, _) in cases.items()]}
    result = subprocess.run(["node", str(HARNESS)], input=json.dumps(data), capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _assert_results(results, gateway, preview, browser=False):
    assert len(gateway.seen) == len(CASES), "One call per case: no policy retries or preflight"
    for name, (stream, failure) in CASES.items():
        row = results[name]
        assert row["visibleStatus"] == ("Incomplete" if failure else "Complete"), (name, row)
        assert len(row["outcomes"]) == 1, (name, row)
        outcome = row["outcomes"][0]
        assert outcome["status"] == (failure or "complete"), (name, row)
        evidence = outcome["evidence"]
        assert evidence["requestedAlias"] == "synthetic-model"
        assert evidence["requestId"] == "synthetic-request"
        assert evidence["servingModel"] is evidence["providerReceipt"] is evidence["decisionStage"] is None
        if failure:
            assert row["answer"] is row["structured"] is None, (name, row)
            assert row["error"]["kind"] == failure, (name, row)
            assert "PRIVATE SYNTHETIC VALUE" not in row["error"]["message"]
            if stream:
                assert row["tokens"] == row["error"]["partialText"], (name, row)
                # Chromium can reject a broken HTTP body before delivering its buffered bytes.
                assert row["tokens"] in (["", TEXT] if name == "stream_transport" else [TEXT]), (name, row)
            if failure == "refused":
                assert "try again" not in row["error"]["message"].lower()
        else:
            assert row["answer"] == TEXT and row["structured"] == {"total": 780}, (name, row)
        if name == "http_refused":
            assert "Blocked by guardrail: Synthetic rule" in row["error"]["message"]
            assert row["error"]["reason"] == "guardrail_blocked"
        if name == "fallback":
            assert evidence["responseModel"] == "synthetic-response-model"
            assert evidence["fallbackServedBy"] == "synthetic-fallback"
            assert evidence["fallbackReason"] == "timeout"
        if name == "cache":
            assert evidence["cacheStatus"] == "HIT"
        elif name != "fallback":
            assert evidence["cacheStatus"] is evidence["fallbackServedBy"] is None
    for path, headers, request in gateway.seen:
        headers = {k.lower(): v for k, v in headers.items()}
        assert path == "/v1/chat/completions"
        assert request["model"] == "synthetic-model"
        assert headers["x-llm-tag-sage-project"] == "sage-358-synthetic"
        if preview:
            assert headers["authorization"] == "Bearer synthetic-preview-token"
        else:
            assert "authorization" not in headers
            if browser:
                assert "viewer=synthetic-viewer" in headers["cookie"]


@pytest.mark.parametrize("preview", [False, True], ids=["deployed-path", "preview-path"])
def test_generated_helper_handles_changed_gateway_responses(gateway, generated, preview):
    app = make_preview_app(_no_vite, "", None, lambda: (gateway.base + "/v1", "synthetic-preview-token"))
    with _served(app) as base:
        _assert_results(_run(gateway, generated, base, preview), gateway, preview)


@pytest.mark.parametrize("preview", [False, True], ids=["deployed-browser", "preview-browser"])
def test_browser_marks_partial_answers_incomplete(gateway, generated, preview):
    module = os.environ.get("SAGE_APP_BROWSER_MODULE")
    if not module:
        pytest.skip("Set SAGE_APP_BROWSER_MODULE to playwright/index.mjs for Chromium acceptance")
    from fastapi import FastAPI
    from starlette.responses import HTMLResponse

    app = FastAPI()
    app.add_api_route("/", lambda: HTMLResponse("<!doctype html><title>New app model outcomes</title>"))
    app.mount("/", make_preview_app(_no_vite, "", None,
              lambda: (gateway.base + "/v1", "synthetic-preview-token")))
    with _served(app) as base:
        results = _run(gateway, generated, base, preview, module, base if preview else gateway.base)
        _assert_results(results, gateway, preview, browser=True)


def test_existing_helpers_keep_their_contract(generated):
    helper = generated.app_path / "src/appLlm.ts"
    old = "// old deployed helper\nexport async function askModel() { return 'legacy'; }\n"
    helper.write_text(old)
    generated.ensure_llm_helper()
    generated.refresh_owned_sources()
    assert helper.read_text() == old


def test_new_helpers_still_receive_compatible_fixes(generated):
    helper = generated.app_path / "src/appLlm.ts"
    helper.write_text(helper.read_text() + "\n// stale edit\n")
    assert generated.ensure_llm_helper()
    assert helper.read_text() == (TEMPLATE / "src/appLlm.ts").read_text()


def test_preview_connect_failure_is_transport_not_provider(gateway, generated):
    # Reserve then close a loopback listener: the failure is a real refused connection.
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_base = f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
    app = make_preview_app(_no_vite, "", None, lambda: (dead_base, "synthetic-preview-token"))
    with _served(app) as base:
        results = _run(gateway, generated, base, True, cases={"allowed_before": (False, None)})
    row = results["allowed_before"]
    assert row["error"]["kind"] == "transport"
    assert row["outcomes"][0]["status"] == "transport"
    assert row["answer"] is row["structured"] is None
    assert row["error"]["evidence"]["requestId"] is None
