"""The Delegated model call tool, in MCP framing OpenCode can call (ADR-0057).

Framing only. What a call actually does is `run`, injected by the caller, so the protocol can be
tested without a gateway, a workspace or a network.

The `token` argument is how a call says which turn it belongs to, and it is the same mechanism
`liveread/mcp.py` already uses for the same reason: one OpenCode server hosts many Conversations, so
neither the URL nor a header can carry it, and asking the assistant to name its own Conversation
would put scope in the hands of the thing scope is protecting against (ADR-0038). The orchestrator
mints a token per turn and puts it in that turn's prompt; an assistant can relay the one it was
given and has no way to name another Conversation's. That is what makes "only Aliases bound to THIS
Conversation" a rule rather than a request.

REACHABILITY IS NOT A PROPERTY OF THIS FILE. v1 `POST /session/{id}/prompt_async` sends the model
custom tools AND MCP tools; v2's prompt path sends neither, measured and recorded at
`driver/opencode.py:264-271`. Sage's turns are on v1, so this arrives. A tool built against v2 would
load, list healthy on `opencode mcp list`, `GET /mcp` and `/experimental/tool`, and never reach the
model — which is how Live read failed twice (ADR-0041).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .call import DEFAULT_MAX_TOKENS, MAX_TOKENS_CEILING, TOOL_NAME

log = logging.getLogger("sage.delegated")

# The same revision `liveread/mcp.py` negotiates, and for the same measured reason: OpenCode 1.18.4
# opens with `protocolVersion: "2025-11-25"`, accepts this older one in reply, and reports the server
# connected. Claiming a revision whose requirements are not implemented here would buy nothing.
PROTOCOL_VERSION = "2025-06-18"

_TOKEN = {
    "type": "string",
    "description": "The turn token from this turn's prompt. Pass it back exactly as given.",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": TOOL_NAME,
        "description": (
            "Ask a language model the person added to this conversation. Use this when the work "
            "needs a model to read text — classifying, summarising or extracting over rows you "
            "have already gathered — rather than doing it by hand or telling the person you "
            "cannot reach a model. Name the Alias exactly as this conversation names it: a model "
            "that is not in this conversation is refused, never swapped for another one. You get "
            "back the model's answer as text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": _TOKEN,
                "alias": {
                    "type": "string",
                    "description": "The language model, named as this conversation names it.",
                },
                "prompt": {"type": "string", "description": "What to ask the model."},
                "system": {
                    "type": "string",
                    "description": "Optional instruction sent ahead of the prompt.",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": (
                        f"Answer budget. Default {DEFAULT_MAX_TOKENS}, capped at "
                        f"{MAX_TOKENS_CEILING}."
                    ),
                },
            },
            "required": ["token", "alias", "prompt"],
        },
    },
]

_TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


def _failed_text(why: str) -> str:
    """What the assistant reads when a Delegated model call broke.

    Says what did NOT happen before it says what went wrong, for the reason
    `liveread.mcp._failed_text` does: handed only a message about a gateway, an assistant goes off
    and answers another way — which is right — and then describes the result as though the model
    had answered. Nothing was asked and nothing came back, and that has to be the first sentence.
    """
    return (f"The model was not called: {why}. Nothing was asked and no answer came back. "
            "Do the work another way, and do not report an answer no model gave you.")


def _result(mid: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def _error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(message: dict, *, run: Callable[[str, dict], str]) -> dict | None:
    """Answer one JSON-RPC message. `None` for a notification, which takes no reply.

    `run(name, arguments)` returns the text the assistant sees. It raises to report a failure the
    assistant should read as one — a refusal is NOT a failure, and comes back as ordinary text, so
    that the sentence the person is owed survives instead of becoming a protocol error nobody words.
    """
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    method = message.get("method")
    mid = message.get("id")

    if mid is None:
        return None  # A notification: `notifications/initialized` and friends.

    if method == "initialize":
        return _result(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "sage-delegated-model", "version": "1"},
        })
    if method in ("tools/list", "prompts/list", "resources/list"):
        if method != "tools/list":
            return _result(mid, {method.split("/")[0]: []})
        return _result(mid, {"tools": TOOLS})
    if method == "ping":
        return _result(mid, {})
    if method != "tools/call":
        return _error(mid, -32601, f"Method not found: {method}")

    params = message.get("params") or {}
    name = params.get("name")
    if name not in _TOOL_NAMES:
        return _error(mid, -32602, f"No tool named {name}")
    # Checked before the default, not after: `or {}` turns an empty list into a valid empty call,
    # so a malformed request would have read as a well-formed one with nothing in it.
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _error(mid, -32602, "arguments must be an object")

    try:
        text = run(str(name), args)
    # Broad on purpose: the assistant reads this, so nothing may escape as a 500.
    except Exception as e:
        # WARNING, so it outlives one turn in `/api/diag`'s warn ring. Never the prompt and never
        # the answer — the tool, the Conversation and what broke are the whole record, which is the
        # rule `liveread`'s own handler keeps about rows.
        log.warning("delegated model call: %s failed — %s: %s — the turn answers another way",
                    name, type(e).__name__, e)
        return _result(mid, {
            "content": [{"type": "text", "text": _failed_text(str(e))}],
            "isError": True,
        })
    return _result(mid, {"content": [{"type": "text", "text": text}]})
