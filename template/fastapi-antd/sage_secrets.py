"""Keys for this app's server (#644): `secret("NAME")` and the viewer's own keys.

    from sage_secrets import secret
    token = secret("CRM_TOKEN")

`secret(name)` is the viewer's own value for `name` when they have set one on the Your keys page,
else the builder's — the Project variable Domino puts in this process's environment — else
`default`. Call it inside a route, per request: the viewer's value belongs to the request being
answered, and a value read at import time would be the builder's for everyone.

A viewer's keys live in one cookie for this app, `sage_keys_<appId>`: their `{name: value}` map,
sealed with AES-GCM under a key HKDF derives from the Project variable `SAGE_APP_KEY` with the app id
as its info. So another App on the same origin can neither read nor forge it, and the server keeps
nothing. It is HttpOnly, Secure, SameSite=Strict, and scoped to this app's path — which the server
cannot know (the proxy strips it), so the page sends it, as `base`, when it saves.

A viewer may set only the names this app's own code reads: `sage_keys.json` beside this file, which
Sage writes from a scan for `secret("NAME")` at publish and when the preview starts. Names and notes,
never values. Without `SAGE_APP_KEY` (or without `cryptography`), `secret()` reads the environment
only and the Your keys page says viewer keys are unavailable.

Refreshed from the template at publish like `sage_serve.py` (`Stack.deploy_files`). Do not edit.
"""
from __future__ import annotations

import base64
import json
import os
import re
from contextvars import ContextVar
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import cookie_parser

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:  # an Environment built before #644; secret() still reads the environment
    AESGCM = None

ROOT = Path(__file__).resolve().parent
APP_KEY_VAR = "SAGE_APP_KEY"
NAMES_FILE = ROOT / "sage_keys.json"
# The app's directory is its id (`apps/<id>/`), the same in the preview and the published App.
APP_ID = ROOT.name
COOKIE = "sage_keys_" + re.sub(r"[^A-Za-z0-9_]", "_", APP_ID)

# A browser drops a cookie over 4096 bytes without a word; refuse well before that instead.
_MAX_COOKIE = 3800
_MAX_BODY = 16 * 1024
_MAX_AGE = 365 * 24 * 3600
_AAD = b"sage-keys"
_NONCE = 12
# A path the page sends as its base: no `;`, no space, nothing that could add a cookie attribute.
_BASE = re.compile(r"/[A-Za-z0-9._~%/-]*")

_cookie: ContextVar[str | None] = ContextVar("sage_keys_cookie", default=None)


def secret(name: str, default: str | None = None) -> str | None:
    """The viewer's value for `name` if they set one, else the environment's, else `default`."""
    return viewer_keys().get(name) or os.environ.get(name, default)


def unavailable_reason() -> str | None:
    """Why viewers cannot set keys here, or None when they can."""
    if AESGCM is None:
        return ("Viewer keys are unavailable: this App's Environment has no `cryptography` package. "
                "Rebuild the Environment, then publish again.")
    if not os.environ.get(APP_KEY_VAR):
        return f"Viewer keys are unavailable: this App's Project has no {APP_KEY_VAR} variable."
    return None


def _aead(app_id: str) -> AESGCM:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=app_id.encode()).derive(os.environ[APP_KEY_VAR].encode())
    return AESGCM(key)


def seal(keys: dict[str, str], app_id: str = APP_ID) -> str:
    """`keys` as a cookie value only this app's server can open."""
    nonce = os.urandom(_NONCE)
    sealed = _aead(app_id).encrypt(nonce, json.dumps(keys).encode(), _AAD)
    return base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")


def unseal(token: str | None, app_id: str = APP_ID) -> dict[str, str]:
    """The map in a cookie value, or {} for no cookie, a tampered one, or another app's."""
    if not token or unavailable_reason():
        return {}
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        data = json.loads(_aead(app_id).decrypt(raw[:_NONCE], raw[_NONCE:], _AAD))
    except (ValueError, InvalidTag):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def viewer_keys() -> dict[str, str]:
    """The keys of the viewer whose request is being answered; {} outside a request."""
    return unseal(_cookie.get())


def allowed() -> list[dict[str, str]]:
    """`[{"name", "note"}]` a viewer may set, from `sage_keys.json`; [] without one."""
    try:
        rows = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    return [{"name": r["name"], "note": str(r.get("note") or "")} for r in rows
            if isinstance(r, dict) and isinstance(r.get("name"), str)]


class ViewerKeys:
    """ASGI middleware: puts this request's sealed cookie where `secret()` reads it."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = b"; ".join(v for k, v in scope.get("headers") or () if k == b"cookie")
        token = _cookie.set(cookie_parser(header.decode("latin-1")).get(COOKIE))
        try:
            await self.app(scope, receive, send)
        finally:
            _cookie.reset(token)


def _refuse(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers={"Cache-Control": "no-store"})


def _cross_site(request: Request) -> bool:
    return request.headers.get("sec-fetch-site") == "cross-site"


def _cookie_path(base: object) -> str | None:
    if base in (None, ""):
        return "/"
    if not isinstance(base, str) or not _BASE.fullmatch(base):
        return None
    return base.rstrip("/") or "/"


def _answer(body: object, keys: dict[str, str], path: str) -> Response:
    response = JSONResponse(body, headers={"Cache-Control": "no-store"})
    if keys:
        response.set_cookie(COOKIE, seal(keys), max_age=_MAX_AGE, path=path, secure=True,
                            httponly=True, samesite="strict")
    else:
        response.delete_cookie(COOKIE, path=path, secure=True, httponly=True, samesite="strict")
    return response


def mount(app: FastAPI) -> None:
    """The middleware `secret()` reads, and the Your keys page's three routes."""
    app.add_middleware(ViewerKeys)

    def check(request: Request, name: str | None = None) -> tuple[list, JSONResponse | None]:
        if _cross_site(request):
            return [], _refuse("This request came from another site.", 403)
        rows = allowed()
        why = unavailable_reason()
        if rows and why:
            return rows, _refuse(why, 503)
        if name is not None and name not in {r["name"] for r in rows}:
            return rows, _refuse("This app does not read a key by that name.", 404)
        return rows, None

    @app.get("/sage/keys", include_in_schema=False)
    def list_keys(request: Request) -> Response:
        rows, refused = check(request)
        if refused:
            return refused
        have = viewer_keys()
        return JSONResponse([{**r, "set": bool(have.get(r["name"]))} for r in rows],
                            headers={"Cache-Control": "no-store"})

    @app.post("/sage/keys", include_in_schema=False)
    async def set_key(request: Request) -> Response:
        if _cross_site(request):
            return _refuse("This request came from another site.", 403)
        body = await request.body()
        if len(body) > _MAX_BODY:
            return _refuse("The request body is too large.", 413)
        try:
            payload = json.loads(body) if body else {}
        except ValueError:
            return _refuse("The request body is not valid JSON.", 400)
        if not isinstance(payload, dict):
            return _refuse("The request body must be an object.", 400)
        name, value = payload.get("name"), payload.get("value")
        rows, refused = check(request, name if isinstance(name, str) else "")
        if refused:
            return refused
        if not isinstance(value, str) or not value:
            return _refuse("A key needs a value.", 400)
        path = _cookie_path(payload.get("base"))
        if path is None:
            return _refuse("The page's base path is not a path.", 400)
        keys = {**viewer_keys(), name: value}
        if len(seal(keys)) > _MAX_COOKIE:
            return _refuse("That key is too long to keep in this browser.", 413)
        note = next(r["note"] for r in rows if r["name"] == name)
        return _answer({"name": name, "note": note, "set": True}, keys, path)

    @app.delete("/sage/keys/{name}", include_in_schema=False)
    def clear_key(name: str, request: Request, base: str = "") -> Response:
        _, refused = check(request, name)
        if refused:
            return refused
        path = _cookie_path(base)
        if path is None:
            return _refuse("The page's base path is not a path.", 400)
        keys = {k: v for k, v in viewer_keys().items() if k != name}
        return _answer({"ok": True}, keys, path)
