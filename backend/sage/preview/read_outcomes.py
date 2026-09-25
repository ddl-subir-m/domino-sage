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
                   "/v4/datasetrw/snapshots/", "/v4/datasetrw/snapshot/")


def read_request(path: str, query: str = "", *, kind: str = "platform") -> dict:
    """Keep a safe route and at most twenty Dataset IDs, never other query values."""
    path, _, embedded_query = path.partition("?")
    query = query or embedded_query
    path = "/" + path.lstrip("/")
    kind = "query" if kind == "query" else "platform"
    allowed = (path.startswith("/api/queries/") if kind == "query" else any(
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
            elif identifier not in result["resourceIds"] and len(result["resourceIds"]) < 20:
                result["resourceIds"].append(identifier)
    return result


def _empty(request: dict, body: bytes | None) -> bool:
    # Bodies too large to inspect still have their HTTP outcome. This helper retains no values.
    if body is None or len(body) > 1024 * 1024:
        return False
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return False
    if value == []:
        return True
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
    else:
        result["outcome"] = "empty" if status == 200 and _empty(safe, body) else "passed"
        return result
    result["reason"] = reason
    return result
