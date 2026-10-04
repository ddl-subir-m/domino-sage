"""A minimal remote MCP server for tests: `Handler` speaks Streamable HTTP.

It offers `echo`, `write_note` and `ping`. `STUB_PAGES=1` splits `tools/list` over two pages.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler

PROTOCOL_VERSION = "2025-06-18"
TOOLS = [
    {"name": "echo", "description": "Echo the text.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "write_note", "description": "Write a note.", "inputSchema": {"type": "object"}},
    {"name": "ping", "description": "Ping.", "inputSchema": {"type": "object"}},
]


def answer(message: dict) -> dict | None:
    """The reply to one JSON-RPC message, or None for a notification."""
    if "id" not in message:
        return None
    method, params = message.get("method"), message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "stub", "version": "1"}}
    elif method == "tools/list":
        if os.environ.get("STUB_PAGES") == "1":
            result = ({"tools": TOOLS[2:]} if params.get("cursor") == "page-2"
                      else {"tools": TOOLS[:2], "nextCursor": "page-2"})
        else:
            result = {"tools": TOOLS}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": json.dumps(params.get("arguments") or {})}]}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": message["id"],
                "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


class Handler(BaseHTTPRequestHandler):
    """Needs `Authorization: Bearer <server.token>` when the server has a token, hands out a session
    id on `initialize` and requires it after, and answers `tools/list` as an event stream, the other
    reply shape the transport allows. A server with a `seen` list records each request's headers."""

    def do_POST(self):
        seen = getattr(self.server, "seen", None)
        if seen is not None:
            seen.append({k.lower(): v for k, v in self.headers.items()})
        token = getattr(self.server, "token", "")
        if token and self.headers.get("authorization") != f"Bearer {token}":
            self.send_response(401)
            self.end_headers()
            return
        message = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if message.get("method") != "initialize" and self.headers.get("mcp-session-id") != "s-1":
            self.send_response(400)
            self.end_headers()
            return
        reply = answer(message)
        if reply is None:
            self.send_response(202)
            self.end_headers()
            return
        body = json.dumps(reply)
        self.send_response(200)
        self.send_header("mcp-session-id", "s-1")
        if message.get("method") == "tools/list":
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"event: message\ndata: {body}\n\n".encode())
            self.wfile.flush()
            # A server may keep the stream open after its reply; `server.hold` is released by the
            # test's teardown.
            hold = getattr(self.server, "hold", None)
            if hold is not None:
                hold.wait(10)
        else:
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

    def do_GET(self):
        self.send_response(405)
        self.end_headers()

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass
