"""Bounded evidence from a preview read; no response values or arbitrary query parameters."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_PATH = re.compile(r"/[A-Za-z0-9/_-]+\Z")
_DATASETS = "/v4/datasetrw/datasets-v2"
# The existing sage_domino relay families. This is a diagnostic fence, not request permission.
_PLATFORM_PATHS = ("/api/datasetrw/", "/api/governance/v1/", "/api/users/v1/self",
                   "/api/users/v1/users", "/api/users/v1/user/", _DATASETS,
                   "/v4/datasetrw/snapshots/", "/v4/datasetrw/snapshot/", "/sage/datasets")
# The app's own `app.py` routes. Kept to `/api/<route>`: a segment after it can be a record's name.
_ROUTE = re.compile(r"/api/(?!queries/|domino/|llm/)[A-Za-z0-9_-]+")
# RFC 2606 / 6761 names reserved for examples. No live record links to one, so a route answering
# with one is answering with content the app made up.
_URL_HOST = re.compile(rb"https?:\\?/\\?/([A-Za-z0-9.-]+)", re.IGNORECASE)
_RESERVED_DOMAINS = (b"example.com", b"example.net", b"example.org")
_RESERVED_TLDS = (b"example", b"test", b"invalid")


def read_request(path: str, query: str = "", *, kind: str = "platform") -> dict:
    """Keep a safe route and at most twenty Dataset IDs, never other query values."""
    path, _, embedded_query = path.partition("?")
    query = query or embedded_query
    path = "/" + path.lstrip("/")
    kind = kind if kind in ("query", "route") else "platform"
    if kind == "route":
        route = _ROUTE.match(path)
        path = route.group(0) if route else "/unrecognized"
    allowed = (path.startswith("/api/queries/") if kind == "query" else
               path != "/unrecognized" if kind == "route" else any(
        path.startswith(prefix) if prefix.endswith("/") else path == prefix
        for prefix in _PLATFORM_PATHS))
    if not allowed or len(path.encode()) > 200 or not _PATH.fullmatch(path):
        path = "/unrecognized"
    result = {"kind": kind, "path": path, "resourceIds": []}
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key != "datasetIds":
            continue
        for identifier in value.split(","):
            if not _ID.fullmatch(identifier):
                result["invalidResourceId"] = True
            elif identifier not in result["resourceIds"]:
                if len(result["resourceIds"]) < 20:
                    result["resourceIds"].append(identifier)
                else:
                    result["resourceIdsTruncated"] = True
    return result


def _empty(request: dict, body: bytes | None) -> bool:
    # Bodies too large to inspect still have their HTTP outcome. This helper retains no values.
    if request.get("resourceIdsTruncated") or body is None or len(body) > 1024 * 1024:
        return False
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    if value == []:
        return not (request["path"] == _DATASETS and request["resourceIds"])
    if request["kind"] == "query":
        return isinstance(value, dict) and value.get("rows") == [] and not value.get("error")
    if request["path"] != _DATASETS or not isinstance(value, list) or not value:
        return False
    returned_ids = set()
    for dataset in value:
        if not isinstance(dataset, dict) or dataset.get("taxonomyTags") != []:
            return False
        dto = dataset.get("datasetRwDto")
        identifier = dto.get("id") if isinstance(dto, dict) else None
        if not isinstance(identifier, str):
            return False
        returned_ids.add(identifier)
    # An omitted Dataset may be inaccessible, not untagged. Do not generalize another row's [] to it.
    return set(request["resourceIds"]).issubset(returned_ids)


def _reserved_host(body: bytes | None) -> bool:
    # A bounded scan that keeps nothing: only whether a reserved host appears.
    if body is None or len(body) > 1024 * 1024:
        return False
    for match in _URL_HOST.finditer(body):
        host = match.group(1).lower().rstrip(b".")
        if host.rsplit(b".", 1)[-1] in _RESERVED_TLDS or any(
                host == domain or host.endswith(b"." + domain) for domain in _RESERVED_DOMAINS):
            return True
    return False


def _error_body(body: bytes | None) -> bool:
    # A route that caught its own failure and answered it as data (#764). Keeps nothing.
    if body is None or len(body) > 1024 * 1024:
        return False
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    return isinstance(value, dict) and bool(value.get("error"))


def read_result(request: dict, status: int | None, body: bytes | None = None,
                *, bound_ids=()) -> dict:
    """Classify HTTP/transport evidence, with emptiness only from a recognized response shape.

    A passed HTTP read does not establish business correctness. Binding mismatch is independent
    evidence, while a 404 by itself cannot distinguish missing resources from access masking.
    """
    safe = read_request(request.get("path", ""), kind=request.get("kind", "platform"))
    safe["resourceIds"] = [value for value in request.get("resourceIds", ())
                           if isinstance(value, str) and _ID.fullmatch(value)][:20]
    if request.get("invalidResourceId") is True:
        safe["invalidResourceId"] = True
    if request.get("resourceIdsTruncated") is True:
        safe["resourceIdsTruncated"] = True
    result = {**safe, "status": status, "outcome": "failed"}
    bound = set(bound_ids)
    if status is None:
        reason = "transport_error"
    elif status in (401, 403):
        reason = "access_denied"
    elif safe.get("invalidResourceId"):
        reason = "invalid_resource_id"
    elif bound and set(safe["resourceIds"]) - bound:
        reason = "binding_mismatch"
    elif status == 404:
        reason = "not_found_or_hidden"
    elif status == 504:
        reason = "timeout"
    elif status >= 500:
        reason = "unavailable"
    elif not 200 <= status < 300:
        reason = "http_error"
    elif safe["kind"] == "route" and _reserved_host(body):
        reason = "placeholder_host"
    elif safe["kind"] == "route" and _error_body(body):
        reason = "error_body"
    else:
        result["outcome"] = "empty" if status == 200 and _empty(safe, body) else "passed"
        return result
    result["reason"] = reason
    return result
