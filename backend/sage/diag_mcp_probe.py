"""A throwaway MCP server that exists only to answer one question: does ANY MCP reach the model?

On 2026-09-09 Live read's tools stopped arriving in Chat turns while every surface said the server
was connected — including OpenCode itself, asked about the exact instance the turn ran in, two
seconds before the model call (see ADR-0041 and `/api/diag/mcp`). Sage has ONE MCP server, so there
was nothing to compare it against and no way to tell "OpenCode drops all MCP tools" from "OpenCode
drops OURS".

This is the comparison. Deliberately unlike Live read in every way that could matter:

  - a different TRANSPORT: stdio, spawned by OpenCode, not a remote URL it dials
  - a different PROCESS: not the orchestrator, so nothing about our routing or ports applies
  - a different SHAPE: one argumentless tool, no token, no schema worth failing on

Read the answer off the shim's own line, which lists every tool a turn was handed:

    chat tools: live read NOT OFFERED — all 10: apply_patch, bash, edit, ...

`sage-mcp-probe_probe_ping` present and Live read absent means the fault is ours after all. Both
absent means no MCP tool reaches a Chat turn at all, whatever OpenCode reports about its servers.

Deliberately dependency-free — no imports from `sage`, nothing but the standard library. A probe
that could fail for its own reasons answers a different question than the one asked.

DELETE THIS, and its `sage-mcp-probe` entry in opencode.json, once the question is settled.
"""

from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "probe_ping",
        "description": (
            "A diagnostic that does nothing. It exists so somebody can see whether MCP tools reach "
            "this turn at all. Do not call it to answer a question about data — it has no access "
            "to any."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def handle(message: dict) -> dict | None:
    """Answer one JSON-RPC message. `None` for a notification, which takes no reply."""
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    mid: Any = message.get("id")
    if mid is None:
        return None
    method = message.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "sage-mcp-probe", "version": "1"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    # Answered rather than refused: a client that asks for prompts or resources and gets a method
    # error has been seen giving up on the whole server, which would make this probe report the
    # failure it is here to measure.
    if method in ("prompts/list", "resources/list"):
        return {"jsonrpc": "2.0", "id": mid, "result": {method.split("/")[0]: []}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/call":
        name = (message.get("params") or {}).get("name")
        if name != "probe_ping":
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"No tool named {name}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text":
                         "probe_ping answered. MCP tools DO reach this turn."}]}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main() -> None:
    """Newline-delimited JSON on stdin and stdout, which is what MCP's stdio transport speaks.

    Nothing is ever written to stdout except a reply: stdout IS the protocol here, and a stray
    print would corrupt the stream rather than show up anywhere useful.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception as e:
            # stderr, never stdout: a stray byte on stdout corrupts the protocol stream. OpenCode
            # keeps a spawned server's stderr, so this is readable if it ever matters.
            print(f"sage-mcp-probe: unreadable line ({e})", file=sys.stderr)
            continue
        reply = handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
