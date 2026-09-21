"""A read-only road from a published app to the Domino platform API (#489).

A published app is served from `apps.<domino-host>`, and the platform API lives on the main host —
a different origin, so a `fetch` from the page is blocked before it is sent. The server this file
sits beside is the one place a call can be made from: it runs inside the App container, where
Domino injects `DOMINO_API_HOST` and a token sidecar that mints a short-lived token for whoever
published the app. Two callers share this file, and that is the reason it is one file:

  - `serve.py` (and every other stack's server) mounts `relay` at `GET /api/domino/<path>`, the
    route the app's OWN page calls;
  - Sage's preview calls `relay` directly while the app is still being built, so a page that reads
    the platform works before it is published and not only after.

`get` and `relay` are the two trust levels. `get` makes one authenticated GET and fences nothing:
it is for code the app's author writes on the server, who can already reach the sidecar from any
line of their own. `relay` is what the BROWSER drives, so it is GET only and allow-listed to a few
read-only families — a page that could name any platform path would make every published app a
platform console for everyone it is shared with, on the publisher's grants.

Those grants are the point to say out loud: the sidecar token is the PUBLISHER's, not the viewer's,
so every viewer reads exactly what the publisher may read. An app that claims to show "what you
can access" on this route is wrong for every viewer but one. Per-viewer identity reaches an app only
where Domino forwards the viewer's own session, which is same-origin browser calls and nothing
here (see `spikes/domino-probes/viewer_identity_app/`).

Stdlib for everything, for the reason `serve.py` and `sage_queries.py` are: this ships in the
creator's app repo and imports under any python3 the image carries.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# `sage_queries.py` sits beside this file in the app's repo and owns the sidecar's address. Running
# the server puts that directory first on the path already; a by-path load does not unless we do.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import sage_queries as sq

#: Where a page reaches the platform through this app: `GET <app>/api/domino/<platform path>`.
RELAY_PREFIX = "/api/domino/"
#: The most this app will relay in one answer. A Dataset file comes back whole, and a page that wants
#: more than this is reading something it should not be reading through a browser.
MAX_BYTES = 16 * 1024 * 1024
#: A platform read is milliseconds, and the sidecar is on loopback. Generous, but bounded: a call
#: that never returns would otherwise hold the viewer's request open for as long as they waited.
TIMEOUT_S = 30.0

# The families a page may read through `relay`. Every one answers on the in-cluster host with a
# sidecar token (verified live, 2026-09-21), and none of their GETs changes anything. Kept out on
# purpose: `/v4/datasetrw/datasets/`, whose `snapshot/file/{start,end,cancel,test}` GETs drive an
# upload session; `/v4/datasetUi/`, same; `/api/users/v1/user/<id>/tokenCredentials`, which is why
# the `user/<id>` family is held to exactly one segment below.
PLATFORM_READS = (
    "/api/datasetrw/",              # Datasets, their snapshots and grants (public REST)
    "/api/governance/v1/",          # bundles, policies, findings, approvals
    "/api/users/v1/self",           # whose grants this app reads with — so a page can say so
    "/api/users/v1/users",          # names, paged
    "/api/users/v1/user/",          # one user by id — the author of a snapshot, say
    "/v4/datasetrw/datasets-v2",    # the only listing that carries taxonomy tags
    "/v4/datasetrw/snapshots/",     # every snapshot of one Dataset (`/api/…/snapshots/{id}` 404s here)
    "/v4/datasetrw/snapshot/",      # one snapshot: `files/recursive`, `file/raw?path=`
)
_ONE_SEGMENT = "/api/users/v1/user/"
_ONE_SEGMENT_DEPTH = len(_ONE_SEGMENT.strip("/").split("/")) + 1   # the family, plus the id
_SELF = "/api/users/v1/self"
_JSON = "application/json; charset=utf-8"

_NO_HOST = "This app is not running on the platform, so its API is out of reach."
_NO_TOKEN = "This app could not get a token for the platform API."
_UNREACHABLE = "This app could not reach the platform API."
_TOO_LARGE = f"The platform's answer is larger than this app will relay ({MAX_BYTES // 2**20} MB)."
_NOT_ALLOWED = ("This app reads the platform API with GET only, and only under: "
                + ", ".join(PLATFORM_READS) + ".")


def platform_host() -> str:
    """`DOMINO_API_HOST`, the in-cluster address Domino injects into every workspace and App."""
    return os.environ.get("DOMINO_API_HOST", "").rstrip("/")


def token() -> str:
    """A fresh token from the sidecar. Per call, never cached: it is minted short-lived on purpose.

    The sidecar answers `Bearer <jwt>` on some deployments and the bare JWT on others; the header
    built from it must not read `Bearer Bearer`.
    """
    with urllib.request.urlopen(sq.sidecar_url(), timeout=5) as resp:
        tok = resp.read().decode("utf-8").strip()
    return tok.removeprefix("Bearer ")


def allowed(path: str) -> bool:
    """Whether `relay` may read `path`: inside one family, and unable to leave it.

    Checked on the DECODED path, because `%2e%2e` is `..` to the server that receives it. An empty
    segment, `.` or `..` anywhere is refused rather than normalised: nothing a page legitimately
    reads has one, and normalising is how a fence is walked around.
    """
    decoded = urllib.parse.unquote(path)
    segments = decoded.strip("/").split("/")
    if any(s in ("", ".", "..") for s in segments):
        return False
    # The bare family too, or it would fall to the loop below and match as an exact path.
    if decoded == _ONE_SEGMENT.rstrip("/") or decoded.startswith(_ONE_SEGMENT):
        return len(segments) == _ONE_SEGMENT_DEPTH
    return any(decoded == family.rstrip("/") or decoded.startswith(family.rstrip("/") + "/")
               for family in PLATFORM_READS)


def _problem(status: int, message: str) -> tuple[int, str, bytes]:
    return status, _JSON, json.dumps({"error": message}).encode("utf-8")


def get(path: str, query: str = "") -> tuple[int, str, bytes]:
    """One GET of `path` on the platform as this app: `(status, content type, body)`.

    The platform's own answer comes back as it was, status included — a 404 for a Dataset that is
    not there is the platform's sentence, and a truer one than anything this file could say instead.
    What this file answers for itself is the three things the platform cannot: no host to call (503),
    no token or no route to it (502), and an answer past `MAX_BYTES` (502). None of those sentences
    carries the token or the host; a viewer's browser is not the place for either.
    """
    host = platform_host()
    if not host:
        return _problem(503, _NO_HOST)
    try:
        bearer = token()
    except Exception as e:  # noqa: BLE001
        print(f"[sage] platform api: no token from the sidecar ({type(e).__name__})", flush=True)
        return _problem(502, _NO_TOKEN)
    url = f"{host}/{path.lstrip('/')}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {bearer}",
        # JSON first, but never only: a Dataset file comes back as whatever it is.
        "Accept": "application/json, */*;q=0.5",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status, ctype, body = resp.status, resp.headers.get("Content-Type"), resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        status, ctype = e.code, e.headers.get("Content-Type")
        try:
            body = e.read(MAX_BYTES + 1)
        except Exception:  # noqa: BLE001
            body = b""
    except Exception as e:  # noqa: BLE001
        print(f"[sage] platform api: GET {path} failed ({type(e).__name__})", flush=True)
        return _problem(502, _UNREACHABLE)
    if len(body) > MAX_BYTES:
        return _problem(502, _TOO_LARGE)
    return status, ctype or _JSON, body


def relay(path: str, query: str = "") -> tuple[int, str, bytes]:
    """`get`, behind the fence: what a page may reach through `GET /api/domino/<path>`.

    `path` is what followed the prefix. The fence comes before the host check on purpose, so that a
    refusal reads the same on a laptop as on the platform, and a test can prove it with no host.
    """
    path = "/" + path.lstrip("/")
    if not allowed(path):
        return _problem(403, _NOT_ALLOWED)
    return get(path, query)


def platform_status() -> str:
    """One line for the App's log saying whether the platform API answers this app.

    `DOMINO_API_HOST` is asserted for workspaces and nowhere else, so this is the only thing that
    proves the App container has it. Host and status only — the log is readable by anyone who can
    see the deployment, and the token is not for them.
    """
    host = platform_host()
    if not host:
        return "DOMINO_API_HOST is unset, so the platform API is out of reach"
    status, _, _ = get(_SELF)
    return f"{host} answered {status} to GET {_SELF}"


def log_platform_status() -> None:
    print(f"[sage] platform api: {platform_status()}", flush=True)
