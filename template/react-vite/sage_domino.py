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
import time
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is relayed as the 3xx it is, never followed.

    The default handler follows it and re-sends every request header — the token included — to
    wherever `Location` points, which the fence never sees and which need not be the platform at
    all. A deployment that answers a signed-out-looking call with a redirect to its login host would
    have this app hand its token to that host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)

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
    reads has one, and normalising is how a fence is walked around. Decoded ONCE, and anything
    still encoded after that is refused too — `%252e%252e` is `..` to a receiver that decodes
    twice — as is a backslash, which some receivers read as a slash. No family carries either in a
    path segment: ids are hex, and a file name rides in the query string.
    """
    decoded = urllib.parse.unquote(path)
    if decoded == DATASETS_PATH:
        return True
    if "%" in decoded or "\\" in decoded:
        return False
    segments = decoded.strip("/").split("/")
    if any(s in ("", ".", "..") for s in segments):
        return False
    # The bare family too, or it would fall to the loop below and match as an exact path.
    if decoded == _ONE_SEGMENT.rstrip("/") or decoded.startswith(_ONE_SEGMENT):
        return len(segments) == _ONE_SEGMENT_DEPTH
    return any(decoded == family.rstrip("/") or decoded.startswith(family.rstrip("/") + "/")
               for family in PLATFORM_READS)


def _headers(ctype: str) -> dict[str, str]:
    """Every relayed answer's headers: its type, and two that keep a relayed body from ever becoming
    a page on this app's origin.

    `nosniff` stops the browser guessing a type the platform did not send. `attachment` on anything
    that is not JSON means a viewer who NAVIGATES to a relayed file — an HTML file somebody wrote
    into a Dataset, say — gets a download, never a page running on this app's origin with this
    app's cookies. `fetch` ignores that header, so a page reading the file sees no difference.
    `Content-Length` is the server's to add: it is counted where the body is written.
    """
    headers = {"Content-Type": ctype, "X-Content-Type-Options": "nosniff"}
    if ctype.split(";")[0].strip().lower() != "application/json":
        headers["Content-Disposition"] = "attachment"
    return headers


def _problem(status: int, message: str) -> tuple[int, dict[str, str], bytes]:
    return status, _headers(_JSON), json.dumps({"error": message}).encode("utf-8")


# Same pause as a Data Source blip in `sage_queries.py`. A 4xx is the platform's answer and is
# returned as it came. A timeout already waited `TIMEOUT_S` and is not tried again.
_BLIP_ATTEMPTS = 4
_BLIP_BACKOFF_S = (0.4, 1.0, 2.0)
_BLIP_STATUS = (502, 503, 504)


def _platform_blip(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _BLIP_STATUS
    if isinstance(exc, urllib.error.URLError):
        return not isinstance(exc.reason, TimeoutError)
    return False


def get(path: str, query: str = "") -> tuple[int, dict[str, str], bytes]:
    """One GET of `path` on the platform as this app: `(status, headers, body)`.

    The platform's own answer comes back as it was, status included — a 404 for a Dataset that is
    not there is the platform's sentence, and a truer one than anything this file could say instead.
    A redirect is relayed as its status and never followed (`_NoRedirect`). What this file answers
    for itself is the three things the platform cannot: no host to call (503), no token or no route
    to it (502), and an answer past `MAX_BYTES` (502). None of those sentences carries the token or
    the host; a viewer's browser is not the place for either.
    """
    host = platform_host()
    if not host:
        return _problem(503, _NO_HOST)
    status = ctype = None
    body = b""
    for attempt in range(_BLIP_ATTEMPTS):
        try:
            bearer = token()
        except Exception as e:  # noqa: BLE001
            if _platform_blip(e) and attempt + 1 < _BLIP_ATTEMPTS:
                print(f"[sage] platform api: token blip ({type(e).__name__}); "
                      f"retrying ({attempt + 1}/{_BLIP_ATTEMPTS - 1})", flush=True)
                time.sleep(_BLIP_BACKOFF_S[attempt])
                continue
            print(f"[sage] platform api: no token from the sidecar ({type(e).__name__})", flush=True)
            return _problem(502, _NO_TOKEN)
        url = f"{host}/{path.lstrip('/')}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {bearer}",
            # JSON first, but never only: a Dataset file comes back as whatever it is.
            "Accept": "application/json, */*;q=0.5",
        })
        try:
            with _opener.open(req, timeout=TIMEOUT_S) as resp:
                status, ctype, body = (
                    resp.status, resp.headers.get("Content-Type"), resp.read(MAX_BYTES + 1))
            break
        except urllib.error.HTTPError as e:
            try:
                code = e.code
                ctype_now = e.headers.get("Content-Type")
                try:
                    raw = e.read(MAX_BYTES + 1)
                except Exception:  # noqa: BLE001
                    raw = b""
            finally:
                e.close()
            if code in _BLIP_STATUS and attempt + 1 < _BLIP_ATTEMPTS:
                print(f"[sage] platform api: GET {path!r} answered {code}; "
                      f"retrying ({attempt + 1}/{_BLIP_ATTEMPTS - 1})", flush=True)
                time.sleep(_BLIP_BACKOFF_S[attempt])
                continue
            status, ctype, body = code, ctype_now, raw
            break
        except Exception as e:  # noqa: BLE001
            if _platform_blip(e) and attempt + 1 < _BLIP_ATTEMPTS:
                print(f"[sage] platform api: GET {path!r} blip ({type(e).__name__}); "
                      f"retrying ({attempt + 1}/{_BLIP_ATTEMPTS - 1})", flush=True)
                time.sleep(_BLIP_BACKOFF_S[attempt])
                continue
            # `!r`: the path is the page's, and a log line is one line.
            print(f"[sage] platform api: GET {path!r} failed ({type(e).__name__})", flush=True)
            return _problem(502, _UNREACHABLE)
    if status is None:
        return _problem(502, _UNREACHABLE)
    if len(body) > MAX_BYTES:
        return _problem(502, _TOO_LARGE)
    return status, _headers(ctype or _JSON), body


#: The one relay path that is not a platform path: every Dataset with its taxonomy tags, read and
#: joined here, so a page asks `GET /api/domino/sage/datasets` instead of writing the pager itself.
DATASETS_PATH = "/sage/datasets"
_LISTING = "/api/datasetrw/v2/datasets"
_TAGGED = "/v4/datasetrw/datasets-v2"
_PAGE = 200
_MAX_PAGES = 100
_TAG_BATCH = 40


def _read_json(path: str, query: str):
    """`(status, body)` for one platform read; the body is parsed JSON, or an error sentence."""
    status, _, raw = get(path, query)
    try:
        body = json.loads(raw)
    except ValueError:
        return 502, f"The platform answered GET {path} with something other than JSON."
    if status != 200:
        own = body.get("error") if isinstance(body, dict) else None
        return status, own or f"The platform answered {status} to GET {path}."
    return status, body


def list_datasets() -> tuple[int, list[dict] | str]:
    """Every Dataset this app can see: `(200, [{id, name, project, tags}])`, or `(status, error)`.

    `tags` is `[{namespace, label}]` — a study tag reads `{"namespace": "study", "label": "abc123"}`.
    Both reads under it are easy to get wrong by hand. The listing pages, and a page can come back
    shorter than the `limit` asked for while more follow, so only an empty page ends it. The tags
    come from `datasets-v2`, where `taxonomyTags` sits BESIDE `datasetRwDto` on each row, never
    inside it. A read that fails is the answer: a shorter list would read as "there are no more".
    """
    found: list[dict] = []
    offset = 0
    for _ in range(_MAX_PAGES):
        status, body = _read_json(_LISTING, f"includeProjectInfo=true&offset={offset}&limit={_PAGE}")
        if status != 200:
            return status, body
        rows = body.get("datasets") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return 502, f"The platform's answer to GET {_LISTING} carried no datasets."
        if not rows:
            break
        for row in rows:
            ds = (row.get("dataset") or row.get("datasetRwDto") or row) if isinstance(row, dict) else {}
            if not isinstance(ds, dict) or not ds.get("id"):
                continue
            project = (row.get("projectInfo") or {}).get("projectName")
            found.append({"id": str(ds["id"]), "name": str(ds.get("name") or ""),
                          "project": str(project) if project else None, "tags": []})
        offset += len(rows)
    else:
        return 502, f"GET {_LISTING} did not end after {_MAX_PAGES} pages."

    by_id = {d["id"]: d for d in found}
    ids = list(by_id)
    for start in range(0, len(ids), _TAG_BATCH):
        batch = ",".join(ids[start:start + _TAG_BATCH])
        status, body = _read_json(_TAGGED, f"datasetIds={batch}&includeTaxonomyTags=true")
        if status != 200:
            return status, body
        rows = body if isinstance(body, list) else (body.get("data") if isinstance(body, dict) else None)
        if not isinstance(rows, list):
            return 502, f"The platform's answer to GET {_TAGGED} carried no datasets."
        for row in rows:
            if not isinstance(row, dict):
                continue
            ds = row.get("datasetRwDto") or row
            entry = by_id.get(str(ds.get("id"))) if isinstance(ds, dict) else None
            if entry is None:
                continue
            tags = row.get("taxonomyTags") or ds.get("taxonomyTags") or []
            entry["tags"] = [{"namespace": str(t.get("namespaceLabel") or ""),
                              "label": str(t.get("label") or "")}
                             for t in tags if isinstance(t, dict)]
    return 200, found


def relay(path: str, query: str = "") -> tuple[int, dict[str, str], bytes]:
    """`get`, behind the fence: what a page may reach through `GET /api/domino/<path>`.

    `path` is what followed the prefix. The fence comes before the host check on purpose, so that a
    refusal reads the same on a laptop as on the platform, and a test can prove it with no host.
    """
    path = "/" + path.lstrip("/")
    if not allowed(path):
        return _problem(403, _NOT_ALLOWED)
    if path == DATASETS_PATH:
        status, result = list_datasets()
        if status != 200:
            return _problem(status, result)
        body = json.dumps({"datasets": result}).encode("utf-8")
        if len(body) > MAX_BYTES:
            return _problem(502, _TOO_LARGE)
        return 200, _headers(_JSON), body
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
