"""A remote MCP server, called from this app's server (#644).

    from sage_mcp import call_tool, list_tools
    from sage_secrets import secret

    url = URL  # the URL Sage lists for this Project's server, never one of your own
    headers = {"Authorization": f"Bearer {secret('TOKEN')}"}  # its headers, as Sage lists them
    tools = list_tools(url, headers)
    result = call_tool(url, "a_tool_it_lists", {"name": "Acme"}, headers)

Streamable HTTP only: one POST per JSON-RPC message, answered as JSON or as an event stream. Each
call opens a session (`initialize`), makes its request and closes. Call it from a route; `secret()`
there is the viewer's own key when they set one.

Errors raise `McpError` with a sentence fit for the App's log. It never carries the headers.

Refreshed from the template at publish like `sage_serve.py` (`Stack.deploy_files`). Do not edit.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from typing import Self

# The revision Sage's own servers negotiate.
PROTOCOL_VERSION = "2025-06-18"
_TIMEOUT_S = 30.0


class McpError(RuntimeError):
    """The server could not be reached, refused, or did not answer in MCP's shape."""


def list_tools(url: str, headers: dict[str, str] | None = None, *,
               timeout: float = _TIMEOUT_S) -> list[dict]:
    """Every tool the server lists: `{"name", "description", "inputSchema"}` as it sent them."""
    with _Session(url, headers, timeout) as session:
        tools: list[dict] = []
        cursor = None
        while True:
            result = session.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in result.get("tools") or [] if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools


def call_tool(url: str, name: str, arguments: dict | None = None,
              headers: dict[str, str] | None = None, *, timeout: float = _TIMEOUT_S) -> dict:
    """The tool's result: `{"content": [...], "isError": bool, ...}` as the server sent it."""
    with _Session(url, headers, timeout) as session:
        return session.request("tools/call", {"name": name, "arguments": arguments or {}})


class _Session:
    def __init__(self, url: str, headers: dict[str, str] | None, timeout: float) -> None:
        self.url = url
        self.client = httpx.Client(timeout=timeout, headers={
            **(headers or {}), "Accept": "application/json, text/event-stream"})
        self.session_id = ""
        self._next = 0

    def __enter__(self) -> Self:
        try:
            self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                        "clientInfo": {"name": "sage-app", "version": "1"}})
            self._exchange({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)
        except BaseException:
            self.client.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.client.close()

    def request(self, method: str, params: dict) -> dict:
        self._next += 1
        reply = self._exchange({"jsonrpc": "2.0", "id": self._next, "method": method,
                                "params": params}, self._next)
        if not isinstance(reply, dict):
            raise McpError(f"{self.url}'s answer to {method} was not JSON-RPC.")
        if "error" in reply:
            said = reply["error"].get("message") if isinstance(reply["error"], dict) else ""
            raise McpError(f"{self.url} refused {method}: {said or reply['error']}")
        return reply.get("result") if isinstance(reply.get("result"), dict) else {}

    def _exchange(self, message: dict, reply_id: int | None) -> dict | None:
        headers = {"mcp-session-id": self.session_id} if self.session_id else {}
        try:
            with self.client.stream("POST", self.url, json=message, headers=headers) as r:
                return self._reply(r, reply_id)
        except httpx.HTTPError as e:
            raise McpError(f"{self.url} did not answer: {type(e).__name__}") from e

    def _reply(self, r: httpx.Response, reply_id: int | None) -> dict | None:
        if r.status_code >= 400:
            raise McpError(f"{self.url} answered {r.status_code}"
                           + (". Check its headers." if r.status_code in (401, 403) else "."))
        self.session_id = r.headers.get("mcp-session-id") or self.session_id
        if reply_id is None:
            return None
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            # Read only until the reply: a server may hold the stream open after it.
            data: list[str] = []
            for line in r.iter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                    continue
                if line or not data:
                    continue
                try:
                    reply = json.loads("\n".join(data))
                except ValueError:
                    reply = None
                data = []
                if isinstance(reply, dict) and reply.get("id") == reply_id:
                    return reply
            raise McpError(f"{self.url} did not answer in its event stream.")
        try:
            return json.loads(r.read())
        except ValueError as e:
            raise McpError(f"{self.url} did not answer with JSON.") from e
