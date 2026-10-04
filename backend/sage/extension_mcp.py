"""A Project's remote MCP servers (#642), until the MCP gateway takes them over.

Each is written into the `mcp` block of OpenCode's project config, `.opencode/opencode.json`, as
`{"type": "remote", "url", "headers", "enabled"}`, so OpenCode loads it itself in Chat and Build.
The on/off switch is OpenCode's own `enabled`, so it is Project-wide. `.opencode/sage-mcp.json`
records which keys Sage wrote, and the tools each listed when it was last read.

A secret is a header value written `{env:NAME}`. Sage stores the reference, never the value, and
resolves it only to ask the server for `tools/list` itself.

A Domino-hosted server (#645) wants a current Domino token on every request, and OpenCode reads a
`{env:...}` header once. So OpenCode is pointed at Sage's control port, `/mcp/domino/<name>`, which
forwards each request with a token fetched for it, and the real URL is kept in the registry.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .extensions import RESERVED_PREFIXES, SLOT, ExtensionError, _write_json
from .gateway.client import DEFAULT_SIDECAR_URL, sidecar_token
from .router.phase_classifier import READ_TOOLS, SHELL_TOOLS, TODO_TOOLS, WEB_TOOLS, WRITE_TOOLS

MCP_CONFIG = SLOT / "opencode.json"
REGISTRY = SLOT / "sage-mcp.json"
# The revision Sage's own servers negotiate (`delegated/mcp.py`).
PROTOCOL_VERSION = "2025-06-18"
_TIMEOUT_S = 30.0
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# OpenCode 1.18.4's own tools, and the custom tools Sage installs globally.
_BUILTIN_TOOLS = frozenset({
    "bash", "read", "write", "edit", "apply_patch", "glob", "grep", "list", "task", "todowrite",
    "todoread", "webfetch", "websearch", "codesearch", "skill", "question", "lsp", "batch",
    "invalid", "live_read", "artifact_write", "delegated_model_call",
}) | READ_TOOLS | WRITE_TOOLS | SHELL_TOOLS | TODO_TOOLS | WEB_TOOLS
# How OpenCode substitutes a variable from its own environment into config text.
ENV_REF = re.compile(r"\{env:([^}]*)\}")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREDENTIAL_HEADER = re.compile(r"key|token|secret", re.IGNORECASE)
DOMINO = "domino"
_KINDS = ("remote", DOMINO)
_APPS_PATH = "/api/apps/beta/apps"
_APPS_PAGE = 100
_APPS_MAX = 1000
_APP_HOSTS_TTL_S = 60.0
# DOMINO_API_HOST -> (expires at, the hosts its Apps are served on).
_APP_HOSTS: dict[str, tuple[float, frozenset[str]]] = {}
_APP_HOSTS_LOCK = threading.Lock()
_LOCK = threading.Lock()


def variables(value: object) -> list[str]:
    """Every `{env:NAME}` name `value` references, once each, in order."""
    if isinstance(value, str):
        return list(dict.fromkeys(ENV_REF.findall(value)))
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    return list(dict.fromkeys(name for item in items for name in variables(item)))


def unset_variables(config: object, env: dict[str, str]) -> list[str]:
    """The variables `config` names that `env` does not set. OpenCode substitutes nothing for one
    and loads the server anyway."""
    return [name for name in variables(config) if name not in env]


def _read_config(root: Path) -> dict:
    try:
        config = json.loads((root / MCP_CONFIG).read_text())
    except FileNotFoundError:
        return {"$schema": "https://opencode.ai/config.json"}
    except ValueError as e:
        raise ExtensionError(f"{MCP_CONFIG} is not valid JSON, so Sage will not rewrite it.") from e
    return config if isinstance(config, dict) else {}


def _read_registry(root: Path) -> dict[str, dict]:
    try:
        body = json.loads((root / REGISTRY).read_text())
    except (OSError, ValueError):
        return {}
    servers = body.get("servers") if isinstance(body, dict) else None
    return {k: v for k, v in (servers or {}).items() if isinstance(v, dict)}


def _write_registry(root: Path, servers: dict[str, dict]) -> None:
    _write_json(root / REGISTRY, {"version": 1, "servers": servers})


def registered(root: Path) -> set[str]:
    """The `mcp` keys Sage wrote."""
    return set(_read_registry(Path(root)))


def _row(name: str, config: dict, record: dict) -> dict:
    return {"name": name, "kind": record.get("kind", "remote"),
            "url": record.get("url") or config.get("url", ""), "headers": config.get("headers") or {},
            "enabled": config.get("enabled", True) is not False,
            "tools": list(record.get("tools") or []), "warning": record.get("warning")}


def list_servers(root: Path) -> list[dict]:
    """Each Sage-registered server as stored: header values are the references, never resolved."""
    root = Path(root)
    servers = _read_config(root).get("mcp")
    servers = servers if isinstance(servers, dict) else {}
    return [_row(name, servers[name], record) for name, record in sorted(_read_registry(root).items())
            if isinstance(servers.get(name), dict)]


def server(root: Path, name: str) -> dict:
    """One server's row. Raises KeyError for a name Sage did not register."""
    row = next((r for r in list_servers(root) if r["name"] == name), None)
    if row is None:
        raise KeyError(name)
    return row


def config_of(row: dict) -> dict:
    """The row in OpenCode's shape."""
    url = proxy_url(row["name"]) if row["kind"] == DOMINO else row["url"]
    return {"type": "remote", "url": url, "headers": row["headers"], "enabled": row["enabled"]}


def proxy_url(name: str) -> str:
    """Where OpenCode reaches a Domino-hosted server: the control port's forwarding route."""
    return f"http://127.0.0.1:{os.environ.get('SAGE_CONTROL_PORT', '8080')}/mcp/domino/{name}"


def domino_token() -> str:
    """The workspace's sidecar token, the builder's. Short-lived, so fetched for each use."""
    try:
        return sidecar_token(os.environ.get("GATEWAY_TOKEN_URL", DEFAULT_SIDECAR_URL))()
    except OSError as e:
        raise ExtensionError(f"Sage could not get a Domino token: {type(e).__name__}") from e


def direct_config(row: dict) -> dict:
    """What Sage asks for `tools/list` itself: the real URL, and for a Domino-hosted server a token
    fetched now."""
    headers = dict(row["headers"])
    if row["kind"] == DOMINO:
        headers["Authorization"] = f"Bearer {domino_token()}"
    return {"type": "remote", "url": row["url"], "headers": headers}


def _check_name(name: object, taken: set[str]) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ExtensionError("A name is lowercase letters, digits, '-' and '_', starting with a "
                             "letter or digit.")
    if name.startswith(RESERVED_PREFIXES):
        raise ExtensionError(f"'{name}' starts with 'sage-' or 'sage_', which Sage keeps for its own.")
    if name in _BUILTIN_TOOLS:
        raise ExtensionError(f"'{name}' is the name of a tool Sage has built in.")
    # OpenCode offers a server's tools as `<name>_<tool>`, so `crm` and `crm_eu` would blur.
    for other in taken:
        if other == name or name.startswith(other + "_") or other.startswith(name + "_"):
            raise ExtensionError(f"'{name}' clashes with the MCP server '{other}' already here.")
    return name


def _check_url(url: object) -> str:
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
        raise ExtensionError("An MCP server's URL starts with http:// or https://.")
    return url


def _app_hosts(api: str) -> frozenset[str]:
    """The host of every App URL Domino reports, asked with the sidecar token and kept a minute.
    `DOMINO_API_HOST` is the in-cluster address, and Apps are served on a public host of their own.
    """
    with _APP_HOSTS_LOCK:
        cached = _APP_HOSTS.get(api)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    hosts: set[str] = set()
    try:
        headers = {"Authorization": f"Bearer {domino_token()}", "Accept": "application/json"}
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            offset, total, seen = 0, None, 0
            while (total is None or offset < total) and seen < _APPS_MAX:
                r = client.get(f"{api}{_APPS_PATH}", headers=headers,
                               params={"offset": offset, "limit": _APPS_PAGE})
                r.raise_for_status()
                body = r.json()
                body = body if isinstance(body, dict) else {"items": body}
                items = body.get("items") if isinstance(body.get("items"), list) else []
                meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
                count = meta.get("totalCount")
                total = count if isinstance(count, int) else offset + len(items)
                for app in items:
                    url = app.get("url") if isinstance(app, dict) else None
                    host = urlsplit(url).hostname if isinstance(url, str) else None
                    if host:
                        hosts.add(host)
                if not items:
                    break
                offset += len(items)
                seen += len(items)
    except (httpx.HTTPError, ValueError) as e:
        said = str(e) if isinstance(e, ExtensionError) else type(e).__name__
        raise ExtensionError(f"Sage could not ask Domino which hosts its Apps are on ({said}), so "
                             "it cannot check this URL.") from e
    with _APP_HOSTS_LOCK:
        _APP_HOSTS[api] = (time.monotonic() + _APP_HOSTS_TTL_S, frozenset(hosts))
    return frozenset(hosts)


def _check_domino_url(url: object) -> str:
    raw = os.environ.get("DOMINO_API_HOST", "").strip().rstrip("/")
    api_host = urlsplit(raw if "://" in raw else f"//{raw}").hostname if raw else None
    if not api_host:
        raise ExtensionError("A Domino-hosted server can be added only when Sage runs on Domino.")
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme != "https" or not parts.hostname:
        raise ExtensionError("A Domino-hosted server's URL starts with https://.")
    # A Domino token goes with every request, so the host it goes to is the whole rule: exactly
    # Domino's own, or exactly one an App of Domino's is served on.
    if "\\" in url or "@" in parts.netloc or (
            parts.hostname != api_host
            and parts.hostname not in _app_hosts(raw if "://" in raw else f"https://{raw}")):
        raise ExtensionError(f"{parts.hostname} is not Domino's host or one its Apps are served "
                             "on. Sage sends your Domino token nowhere else.")
    return url


def _check_headers(headers: object) -> dict[str, str]:
    if headers is None:
        return {}
    if not (isinstance(headers, dict) and all(
            isinstance(k, str) and k and isinstance(v, str) for k, v in headers.items())):
        raise ExtensionError("An MCP server's headers map names to text.")
    for key, value in headers.items():
        if (key.lower() == "authorization" or _CREDENTIAL_HEADER.search(key)) \
                and not ENV_REF.search(value):
            raise ExtensionError(f"The {key} header holds a credential. Add it as a secret and "
                                 "write {env:NAME} here, not the value itself.")
    bad = [name for name in variables(headers) if not _ENV_NAME.match(name)]
    if bad:
        raise ExtensionError(f"'{bad[0]}' is not a secret's name: letters, digits and '_'.")
    return dict(headers)


def add(root: Path, name: object, url: object, headers: object = None,
        kind: object = "remote") -> dict:
    """Write one server into the project config, switched on, and register it. Answers its row."""
    root = Path(root)
    if kind not in _KINDS:
        raise ExtensionError("An MCP server's kind is remote or domino.")
    # Outside the lock: checking a Domino URL may ask Domino.
    url = _check_domino_url(url) if kind == DOMINO else url
    with _LOCK:
        records = _read_registry(root)
        name = _check_name(name, set(records))
        url = url if kind == DOMINO else _check_url(url)
        headers = _check_headers(headers)
        record: dict = {"tools": [], "warning": None}
        if kind == DOMINO:
            if any(k.lower() == "authorization" for k in headers):
                raise ExtensionError("Sage signs in to a Domino-hosted server as you. Leave the "
                                     "Authorization header out.")
            record.update(kind=DOMINO, url=url)
            url = proxy_url(name)
        config = {"type": "remote", "url": url, "headers": headers, "enabled": True}
        current = _read_config(root)
        servers = current.setdefault("mcp", {})
        if not isinstance(servers, dict):
            raise ExtensionError(f"{MCP_CONFIG} has an mcp block Sage cannot read.")
        if name in servers:
            raise ExtensionError(f"{MCP_CONFIG} already declares '{name}' and it is not Sage's.")
        servers[name] = config
        _write_json(root / MCP_CONFIG, current)
        records[name] = record
        _write_registry(root, records)
        return _row(name, config, records[name])


def set_tools(root: Path, name: str, tools: list[str], warning: str | None) -> dict:
    """What a server listed when last read, or why it could not be. Raises KeyError."""
    root = Path(root)
    with _LOCK:
        records = _read_registry(root)
        if name not in records:
            raise KeyError(name)
        records[name] = {**records[name], "tools": sorted(tools), "warning": warning}
        _write_registry(root, records)
    return server(root, name)


def set_enabled(root: Path, name: str, enabled: bool) -> dict:
    """Raises KeyError for a name Sage did not register."""
    root = Path(root)
    with _LOCK:
        current = _read_config(root)
        servers = current.get("mcp")
        if name not in _read_registry(root) or not isinstance(servers, dict) \
                or not isinstance(servers.get(name), dict):
            raise KeyError(name)
        servers[name]["enabled"] = enabled
        _write_json(root / MCP_CONFIG, current)
    return server(root, name)


def remove(root: Path, name: str) -> bool:
    root = Path(root)
    with _LOCK:
        records = _read_registry(root)
        if name not in records:
            return False
        config = _read_config(root)
        if isinstance(config.get("mcp"), dict):
            config["mcp"].pop(name, None)
        if not config.get("mcp") and set(config) <= {"$schema", "mcp"}:
            (root / MCP_CONFIG).unlink(missing_ok=True)
        else:
            _write_json(root / MCP_CONFIG, config)
        del records[name]
        _write_registry(root, records)
        return True


def read_tools(config: dict, env: dict[str, str], *, timeout: float = _TIMEOUT_S) -> list[str]:
    """The name of each tool the server lists, asked with its `{env:NAME}` references resolved
    from `env`."""
    unset = unset_variables(config, env)
    if unset:
        raise ExtensionError(f"{', '.join(unset)} {'is' if len(unset) == 1 else 'are'} not set. "
                             "Add it as a secret.")
    session = _Http(_resolve(config, env), timeout)
    try:
        session.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                       "clientInfo": {"name": "sage", "version": "1"}})
        session.notify("notifications/initialized")
        tools: list[str] = []
        cursor = None
        while True:
            result = session.request("tools/list", {"cursor": cursor} if cursor else {})
            for tool in result.get("tools") or []:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    tools.append(tool["name"])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
    finally:
        session.close()


def _resolve(value: object, env: dict[str, str]) -> object:
    if isinstance(value, str):
        return ENV_REF.sub(lambda m: env.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _resolve(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, env) for v in value]
    return value


class _Http:
    """Streamable HTTP: a POST per message, answered as JSON or as an event stream."""

    def __init__(self, config: dict, timeout: float) -> None:
        self.url = config["url"]
        self.client = httpx.Client(timeout=timeout, headers={
            **(config.get("headers") or {}), "Accept": "application/json, text/event-stream"})
        self.session_id = ""
        self._next = 0

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
        headers = {"mcp-session-id": self.session_id} if self.session_id else {}
        try:
            with self.client.stream("POST", self.url, json=message, headers=headers) as r:
                return self._reply(r, reply_id)
        except httpx.HTTPError as e:
            raise ExtensionError(f"The server did not answer: {type(e).__name__}") from e

    def _reply(self, r: httpx.Response, reply_id: int | None) -> dict | None:
        if r.status_code >= 400:
            raise ExtensionError(f"The server answered {r.status_code}"
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
            raise ExtensionError("The server did not answer in its event stream.")
        try:
            return json.loads(r.read())
        except ValueError as e:
            raise ExtensionError("The server did not answer with JSON.") from e

    def close(self) -> None:
        self.client.close()
