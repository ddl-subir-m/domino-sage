"""Reading a Project's MCP server before OpenCode does (ADR-0071, #621).

When a server is added Sage asks it for `tools/list` itself, because each tool's `readOnlyHint`
decides whether Ask and plan turns are offered it, and OpenCode does not pass annotations on. Also
here: a git repo that declares its own server, read into OpenCode's shape.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

from .extensions import ENV_REF, ExtensionError, unset_variables

# The revision Sage's own servers negotiate (`delegated/mcp.py`).
PROTOCOL_VERSION = "2025-06-18"
_TIMEOUT_S = 30.0
# Where a repo may declare its server: OpenCode's own config, then the `mcpServers` file other
# MCP clients read.
_DECLARATIONS = (".opencode/opencode.json", "opencode.json", ".mcp.json", "mcp.json")


def read_tools(config: dict, *, timeout: float = _TIMEOUT_S) -> dict[str, bool]:
    """Each tool the server lists, by name, and whether it is annotated `readOnlyHint: true`."""
    unset = unset_variables(config)
    if unset:
        raise ExtensionError(f"{', '.join(unset)} {'is' if len(unset) == 1 else 'are'} not set "
                             "in this app's environment.")
    config = _resolve(config)
    session = _Http(config, timeout) if config.get("type") == "remote" else _Stdio(config, timeout)
    try:
        session.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                       "clientInfo": {"name": "sage", "version": "1"}})
        session.notify("notifications/initialized")
        tools: dict[str, bool] = {}
        cursor = None
        while True:
            result = session.request("tools/list", {"cursor": cursor} if cursor else {})
            for tool in result.get("tools") or []:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    hints = tool.get("annotations") if isinstance(tool.get("annotations"), dict) \
                        else {}
                    tools[tool["name"]] = hints.get("readOnlyHint") is True
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
    finally:
        session.close()


def _resolve(value: object) -> object:
    if isinstance(value, str):
        return ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


class _Session:
    _next = 0

    def request(self, method: str, params: dict) -> dict:
        self._next += 1
        reply = self._exchange({"jsonrpc": "2.0", "id": self._next, "method": method,
                                "params": params}, self._next)
        if not isinstance(reply, dict):
            raise ExtensionError(f"The server's answer to {method} was not JSON-RPC.")
        if "error" in reply:
            said = reply["error"].get("message") if isinstance(reply["error"], dict) else ""
            raise ExtensionError(f"The server refused {method}: {said or reply['error']}")
        return reply.get("result") if isinstance(reply.get("result"), dict) else {}

    def notify(self, method: str) -> None:
        self._exchange({"jsonrpc": "2.0", "method": method}, None)

    def _exchange(self, message: dict, reply_id: int | None) -> dict | None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _Stdio(_Session):
    """One JSON-RPC message per line on the process's stdin and stdout."""

    def __init__(self, config: dict, timeout: float) -> None:
        env = {**os.environ, **(config.get("environment") or {})}
        try:
            self.proc = subprocess.Popen(config["command"], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         env=env, text=True)
        except OSError as e:
            raise ExtensionError(f"Could not start {config['command'][0]}: {e}") from e
        self.deadline = time.monotonic() + timeout
        self.timeout = timeout
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.reader = threading.Thread(target=self._read, name="mcp-tools-read", daemon=True)
        self.reader.start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _exchange(self, message: dict, reply_id: int | None) -> dict | None:
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except OSError as e:
            raise ExtensionError("The server exited before it listed its tools.") from e
        while reply_id is not None:
            try:
                line = self.lines.get(timeout=max(0.0, self.deadline - time.monotonic()))
            except queue.Empty as e:
                raise ExtensionError(f"The server did not list its tools within "
                                     f"{self.timeout:.0f} seconds.") from e
            if line is None:
                raise ExtensionError("The server exited before it listed its tools.")
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if isinstance(reply, dict) and reply.get("id") == reply_id:
                return reply
        return None

    def close(self) -> None:
        self.proc.kill()
        self.proc.wait()
        self.reader.join(timeout=5)
        for pipe in (self.proc.stdin, self.proc.stdout):
            with contextlib.suppress(OSError):
                pipe.close()


class _Http(_Session):
    """Streamable HTTP: a POST per message, answered as JSON or as an event stream."""

    def __init__(self, config: dict, timeout: float) -> None:
        self.url = config["url"]
        self.client = httpx.Client(timeout=timeout, headers={
            **(config.get("headers") or {}), "Accept": "application/json, text/event-stream"})
        self.session_id = ""

    def _exchange(self, message: dict, reply_id: int | None) -> dict | None:
        headers = {"mcp-session-id": self.session_id} if self.session_id else {}
        try:
            r = self.client.post(self.url, json=message, headers=headers)
        except httpx.HTTPError as e:
            raise ExtensionError(f"{self.url} did not answer: {e}") from e
        if r.status_code >= 400:
            raise ExtensionError(f"{self.url} answered {r.status_code}"
                                 + (". Check its headers." if r.status_code in (401, 403) else "."))
        self.session_id = r.headers.get("mcp-session-id") or self.session_id
        if reply_id is None:
            return None
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            for event in r.text.split("\n\n"):
                data = "\n".join(line[5:].lstrip() for line in event.splitlines()
                                 if line.startswith("data:"))
                try:
                    reply = json.loads(data) if data else None
                except ValueError:
                    continue
                if isinstance(reply, dict) and reply.get("id") == reply_id:
                    return reply
            raise ExtensionError(f"{self.url} did not answer in its event stream.")
        try:
            return r.json()
        except ValueError as e:
            raise ExtensionError(f"{self.url} did not answer with JSON.") from e

    def close(self) -> None:
        self.client.close()


def server_from_git(url: object, server: str = "") -> tuple[dict, str]:
    """The server a repo declares, in OpenCode's shape, with the commit it was read at.

    Only the declaration is read. Its command runs as declared (an `npx` or `uvx` package, say),
    not from the clone, which is not kept.
    """
    with _cloned(url) as (base, commit):
        declared: dict[str, dict] = {}
        for rel in _DECLARATIONS:
            try:
                body = json.loads((base / rel).read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(body, dict):
                continue
            if isinstance(body.get("mcp"), dict):
                declared = {k: v for k, v in body["mcp"].items() if isinstance(v, dict)}
            elif isinstance(body.get("mcpServers"), dict):
                declared = {k: _from_mcp_servers(v) for k, v in body["mcpServers"].items()
                            if isinstance(v, dict)}
            if declared:
                break
    if not declared:
        raise ExtensionError(f"{url} declares no MCP server. Sage looks for "
                             f"{', '.join(_DECLARATIONS)}.")
    if server:
        if server not in declared:
            raise ExtensionError(f"{url} declares no server '{server}', only "
                                 f"{', '.join(sorted(declared))}.")
        return declared[server], commit
    if len(declared) > 1:
        raise ExtensionError(f"{url} declares {', '.join(sorted(declared))}. Name the one to add.")
    return next(iter(declared.values())), commit


def _from_mcp_servers(entry: dict) -> dict:
    """An `mcpServers` entry as OpenCode's config: `${VAR}` there is `{env:VAR}` here."""
    def env(value: object) -> object:
        return value.replace("${", "{env:") if isinstance(value, str) else value

    if entry.get("url"):
        config = {"type": "remote", "url": env(entry["url"])}
        if entry.get("headers"):
            config["headers"] = {k: env(v) for k, v in entry["headers"].items()}
        return config
    config = {"type": "local",
              "command": [str(entry.get("command") or "")] + [env(a) for a in entry.get("args") or []]}
    if entry.get("env"):
        config["environment"] = {k: env(v) for k, v in entry["env"].items()}
    return config


@contextlib.contextmanager
def _cloned(url: object) -> Iterator[tuple[Path, str]]:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ExtensionError("A git URL starts with https://.")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(["git", "clone", "--depth", "1", "--quiet", "--", url, tmp],
                           check=True, capture_output=True, text=True, timeout=120, env=env)
        except subprocess.TimeoutExpired as e:
            raise ExtensionError(f"Cloning {url} took longer than two minutes.") from e
        except subprocess.CalledProcessError as e:
            said = (e.stderr or "").strip().splitlines()
            raise ExtensionError(f"git could not clone {url}: {said[-1] if said else e}") from e
        commit = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], capture_output=True,
                                text=True, check=True, env=env).stdout.strip()
        yield Path(tmp), commit
