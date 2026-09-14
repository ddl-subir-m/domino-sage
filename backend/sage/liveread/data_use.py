"""Observed data operations and per-request evidence. No provider receipt is inferred."""

import copy
import json
import re
import threading
from uuid import uuid4

from ..router.phase_classifier import READ_TOOLS, SHELL_TOOLS
from ..shim.chat_paths import (
    MENTION_MARK,
    MENTION_PATH_LINE,
    content_text,
    normalize_write_path,
    read_path_from_tool_call,
    tool_call_name_and_args,
    withheld_result,
)

_EXECUTION_TOOLS = SHELL_TOOLS | frozenset({"python", "python_exec", "python_execute"})
_DELEGATION_TOOLS = frozenset({"task"})
_PATH_KEYS = ("path", "filePath", "file_path")
_COMMAND_KEYS = ("command", "cmd", "code")
_EXIT = "Command exited with code "

OPEN_CODE_DATA_CARRIERS = (
    {
        "carrier": "live_read custom tool result",
        "coverage": "covered",
        "model_view": "selected values only, with Data used evidence",
        "lineage": "source and operation known",
    },
    {
        "carrier": "live_read MCP text result",
        "coverage": "covered",
        "model_view": "selected values only, with Data used evidence",
        "lineage": "source and operation known",
    },
    {
        "carrier": "live_read MCP error",
        "coverage": "covered",
        "model_view": "error text only; no row values are restored",
        "lineage": "operation unknown when the tool failed before recording",
    },
    {
        "carrier": "user image attachment",
        "coverage": "covered",
        "model_view": "image content reaches only vision-capable models",
        "lineage": "attachment path known from the prompt descriptor",
    },
    {
        "carrier": "tool-result image part",
        "coverage": "unsupported-state handled",
        "model_view": "unknown-lineage receipt; image bytes are not replayed",
        "lineage": "unknown unless a supported data operation recorded it",
    },
)


class DataUse:
    def __init__(self):
        self.operations = {}
        self.sources = []
        self.lock = threading.RLock()

    def record(self, event, reply, persist, turn_id):
        with self.lock:
            event = {**event, "turn_id": turn_id}
            self.operations[event["operation_id"]] = (event, copy.deepcopy(reply), persist)
            self._remember_sources([_source_from_event(event)])
            persist({"type": "data_used", "dataUsed": [copy.deepcopy(event)]})

    def restore(self, history, persist):
        """Reopen metadata after a restart. Saved Artifacts default to structure, never values."""
        latest = {event["operation_id"]: event for row in history for event in row.get("dataUsed", [])
                  if event.get("operation_id")}
        with self.lock:
            for oid, event in latest.items():
                if oid in self.operations:
                    continue
                reply = {"data_use": oid, "columns": event["columns"],
                         "result_rows": event.get("result_rows"),
                         "local_reference": event["artifact"], "coverage": event["coverage"],
                         "selected_fields": []}
                self.operations[oid] = (copy.deepcopy(event), reply, persist)
                self._remember_sources([_source_from_event(event)])

    def events(self, turn_id):
        with self.lock:
            return [copy.deepcopy(event) for event, _, _ in self.operations.values()
                    if event["turn_id"] == turn_id]

    def apply_restrictions(self, request, withheld=None):
        if not isinstance(request.get("messages"), list):
            return request
        hidden = _withheld_sources(withheld)
        if not hidden:
            return request
        return {
            **request,
            "messages": [
                _rewrite_withheld_image_attachments(message, hidden)
                if isinstance(message, dict) else message
                for message in request["messages"]
            ],
        }

    def prepare(self, request, withheld=None):
        """Build the model-facing view of known data-bearing local tool results.

        The transcript still holds the local result the person saw. This rewrites only the request
        that is leaving Sage, and only for sources Sage can identify from upload descriptors or
        recorded data operations. It does not inspect values for PII.
        """
        if not isinstance(request.get("messages"), list):
            return request, set()
        sources = _sources_from_messages(request["messages"])
        with self.lock:
            self._remember_sources(sources)
            sources.extend(copy.deepcopy(source) for source in self.sources)
            sources.extend(_source_from_event(event) for event, _, _ in self.operations.values())
        sources = [s for s in sources if s.get("path")]
        hidden = _withheld_sources(withheld)
        calls = {}
        direct: dict[str, dict] = {}
        used = set()
        local_texts: list[str] = []
        messages = []
        with self.lock:
            for message in request.get("messages", []):
                if not isinstance(message, dict):
                    messages.append(message)
                    continue
                message = copy.deepcopy(message)
                message = _rewrite_withheld_image_attachments(message, hidden)
                message = _rewrite_open_code_parts(message, sources, hidden, local_texts)
                for call in message.get("tool_calls") or []:
                    if isinstance(call, dict):
                        cid = str(call.get("id") or "")
                        calls[call.get("id")] = call
                        source = _sources_for_call(call, sources, hidden)
                        if cid and source:
                            direct[cid] = source
                            call = _sanitize_call(call, source)
                            _replace_call(message, cid, call)
                        else:
                            raw_args = _call_arguments_text(call)
                            if _contains_local_text(raw_args, local_texts):
                                source = {"sources": [], "withheld": False}
                                direct[cid] = source
                                _replace_call(message, cid, _sanitize_call(call, source))
                            for oid, _event, reply in self._selected_operation_args(raw_args):
                                used.add(oid)
                if message.get("role") == "tool":
                    content = message.get("content")
                    body = _content_json(content)
                    oid = body.get("data_use") if isinstance(body, dict) else None
                    entry = self.operations.get(oid) if isinstance(oid, str) else None
                    if entry:
                        # The operation's own result; preserve withholding already applied by the shim.
                        used.add(oid)
                    path = read_path_from_tool_call(calls.get(message.get("tool_call_id"), {}))
                    if path and content not in (withheld_result(path),
                                                withheld_result("a message in this conversation")):
                        for event, reply, _ in self.operations.values():
                            if path == event["artifact"] or path.endswith("/" + event["artifact"]):
                                shape = {k: v for k, v in reply.items() if k != "selected"}
                                shape["selected_fields"] = []
                                message = {**message, "content": json.dumps(shape)}
                                break
                    cid = str(message.get("tool_call_id") or "")
                    if cid in direct:
                        raw = _tool_content_text(message.get("content"))
                        if raw:
                            local_texts.append(raw)
                        call = calls.get(message.get("tool_call_id"), {})
                        receipt = _local_receipt(call, direct[cid], raw,
                                                 status=_status_hint(raw, call))
                        message = {**message, "content": _replace_content(message.get("content"),
                                                                          receipt)}
                    else:
                        message = _rewrite_image_result(message, calls.get(message.get("tool_call_id"), {}))
                else:
                    text = _tool_content_text(message.get("content"))
                    for oid, _event, _reply in self._selected_operation_args(text):
                        used.add(oid)
                    source = _source_for_local_text(text, local_texts, direct.values())
                    if source:
                        receipt = _local_receipt({"tool": "background", "state": {"input": {}}},
                                                 source, text, status=_status_hint(text))
                        message = {**message, "content": _replace_content(message.get("content"),
                                                                          receipt)}
                messages.append(message)
        return {**request, "messages": messages}, used

    def _remember_sources(self, sources):
        by_path = {normalize_write_path(str(s.get("path") or "")): copy.deepcopy(s)
                   for s in self.sources if s.get("path")}
        for source in sources:
            path = normalize_write_path(str(source.get("path") or ""))
            if path:
                by_path[path] = {k: copy.deepcopy(v) for k, v in source.items()
                                 if v is not None and k != "withheld"}
        self.sources = list(by_path.values())

    def _selected_operation_args(self, text):
        if not text:
            return []
        out = []
        for oid, (event, reply, _persist) in self.operations.items():
            selected = reply.get("selected") if isinstance(reply, dict) else None
            if selected and _contains_selected(text, selected):
                out.append((oid, event, reply))
        return out

    def observe(self, stream, request, used):
        if not used:
            yield from stream
            return
        request_id = "req_" + uuid4().hex
        evidence = {"request_id": request_id, "requested_alias": request.get("model"),
                    "serving_model": None, "provider_receipt": "unknown", "cache": "unknown",
                    "decision_stage": "unknown", "delivery": "unknown", "fallback": "unknown",
                    "failure": None, "state": "attempted"}

        def save():
            with self.lock:
                for oid in used:
                    event, _, persist = self.operations[oid]
                    event["requests"] = [r for r in event["requests"] if r["request_id"] != request_id]
                    event["requests"].append(copy.deepcopy(evidence))
                    persist({"type": "data_used", "dataUsed": [copy.deepcopy(event)]})

        save()
        buffer = ""
        completed = False
        try:
            for chunk in stream:
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            continue
                        try:
                            body = json.loads(payload)
                        except ValueError:
                            continue
                        if body.get("error"):
                            evidence["state"] = "failed"
                            evidence["failure"] = _failure_kind(body.get("error"))
                            reason = _safe_refusal_reason(body.get("error"))
                            if reason:
                                evidence["refusal_reason"] = reason
                        elif any(c.get("finish_reason") in ("stop", "tool_calls")
                                 for c in body.get("choices", [])):
                            completed = True
                        evidence.update(_gateway_evidence(body))
                yield chunk
        except Exception as exc:
            evidence["state"] = "failed"
            evidence["failure"] = _failure_kind(exc)
            raise
        finally:
            if evidence["state"] != "failed":
                evidence["state"] = "response_completed" if completed else "interrupted"
            save()


def _gateway_evidence(body):
    """Trust only fields the gateway response actually carries; absent stays unknown."""
    if not isinstance(body, dict):
        return {}
    candidates = [body]
    for key in ("gateway", "domino_gateway", "sage_gateway", "metadata", "evidence"):
        nested = body.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    out = {}
    for candidate in candidates:
        model = candidate.get("serving_model") or candidate.get("served_model")
        if not model and candidate is body:
            model = candidate.get("model")
        if model and "serving_model" not in out:
            out["serving_model"] = str(model)
        receipt = (candidate.get("provider_receipt") or candidate.get("provider_request_id")
                   or candidate.get("provider_response_id"))
        if receipt and "provider_receipt" not in out:
            out["provider_receipt"] = str(receipt)
        if "cache" in candidate and "cache" not in out:
            out["cache"] = _word(candidate.get("cache"))
        if "cache_hit" in candidate and "cache" not in out:
            out["cache"] = "hit" if candidate.get("cache_hit") else "miss"
        stage = candidate.get("decision_stage") or candidate.get("policy_stage")
        if stage and "decision_stage" not in out:
            out["decision_stage"] = str(stage)
        delivery = (candidate.get("delivery") or candidate.get("forwarding")
                    or candidate.get("forwarded"))
        if delivery is not None and "delivery" not in out:
            out["delivery"] = _word(delivery)
        if "fallback" in candidate and "fallback" not in out:
            out["fallback"] = _word(candidate.get("fallback"))
        if "fallback_attempt" in candidate and "fallback" not in out:
            out["fallback"] = _word(candidate.get("fallback_attempt"))
    return out


def _word(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _failure_kind(error) -> str:
    status = getattr(error, "status", None)
    body = getattr(error, "body", "")
    if isinstance(error, dict):
        status = error.get("status") or error.get("status_code") or error.get("code")
        body = json.dumps(error)
    text = f"{status or ''} {body or error}".lower()
    try:
        code = int(status or 0)
    except (TypeError, ValueError):
        code = 0
    if code in (401, 403) or any(w in text for w in ("auth", "unauthorized", "forbidden")):
        return "authentication"
    if code == 429 or "rate" in text:
        return "rate_limited"
    if "guardrail_blocked" in text or "blocked by guardrail" in text or "policy" in text:
        return "refused"
    if code >= 500 or "provider" in text:
        return "provider"
    if isinstance(error, OSError) or any(w in text for w in ("connection", "timeout", "transport")):
        return "transport"
    return "gateway_error"


def _safe_refusal_reason(error) -> str | None:
    text = ""
    if isinstance(error, dict):
        text = str(error.get("message") or error.get("detail") or "")
    else:
        text = str(error or "")
    match = re.search(r"Blocked by guardrail:\s*([^\"'}\\]+)", text)
    if not match:
        return None
    return "Blocked by guardrail: " + " ".join(match.group(1).split()).strip(" .;:")


def _sources_from_messages(messages):
    sources = []
    for message in messages:
        text = content_text(message.get("content")) if isinstance(message, dict) else ""
        if MENTION_MARK not in text:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line != MENTION_PATH_LINE.lstrip("\n") and not line.startswith(MENTION_PATH_LINE.lstrip("\n")):
                continue
            path = line.split("path:", 1)[1].strip()
            if not path:
                continue
            window = "\n".join(lines[max(0, i - 2): min(len(lines), i + 3)])
            sources.append({"path": path, **_shape_from_text(window)})
    return sources


def _shape_from_text(text):
    shape = {}
    rows = re.search(r"([0-9][0-9,]*)\s+rows?\b", text, re.IGNORECASE)
    if rows:
        try:
            shape["rows"] = int(rows.group(1).replace(",", ""))
        except ValueError:
            pass
    columns = re.search(r"\bcolumns?:\s*([^\n.]+)", text, re.IGNORECASE)
    if columns:
        names = [c.strip(" `\"'") for c in columns.group(1).split(",")]
        names = [c for c in names if c]
        if names:
            shape["columns"] = names[:80]
    return shape


def _source_from_event(event):
    return {"path": str(event.get("source") or event.get("artifact") or ""),
            "columns": list(event.get("columns") or []),
            "rows": event.get("coverage", {}).get("processed", event.get("result_rows"))}


def _withheld_sources(keys):
    out = []
    for key in keys or ():
        if isinstance(key, str) and key.startswith("file:"):
            out.append({"path": key[5:], "withheld": True})
    return out


def _same_path(a, b):
    left, right = normalize_write_path(str(a or "")), normalize_write_path(str(b or ""))
    if not left or not right:
        return False
    return left == right or left.endswith("/" + right) or right.endswith("/" + left)


def _path_in_text(text, path):
    norm = normalize_write_path(str(path or ""))
    raw = str(text or "").replace("\\", "/")
    return bool(norm and (norm in raw or str(path) in raw))


def _sources_for_call(call, sources, hidden):
    name, args = tool_call_name_and_args(call)
    if name in _EXECUTION_TOOLS:
        text = " ".join(str(args.get(k) or "") for k in _COMMAND_KEYS)
        found = [s for s in sources + hidden if _path_in_text(text, s.get("path"))]
    elif name in _DELEGATION_TOOLS:
        text = json.dumps(args, sort_keys=True, default=str)
        found = [s for s in sources + hidden if _path_in_text(text, s.get("path"))]
    elif name in READ_TOOLS:
        path = next((str(args.get(k) or "") for k in _PATH_KEYS if args.get(k)), "")
        found = [s for s in sources + hidden if _same_path(path, s.get("path"))]
    else:
        found = []
    if not found:
        return {}
    return {"sources": _dedupe_sources(found), "withheld": any(s.get("withheld") for s in found)}


def _dedupe_sources(sources):
    out = []
    seen = set()
    for source in sources:
        path = normalize_write_path(str(source.get("path") or ""))
        if not path or path in seen:
            continue
        seen.add(path)
        out.append({k: v for k, v in source.items() if v is not None and k != "withheld"})
    return out


def _replace_call(message, cid, replacement):
    if not cid:
        return
    calls = []
    changed = False
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict) and str(call.get("id") or "") == cid:
            calls.append(replacement)
            changed = True
        else:
            calls.append(call)
    if changed:
        message["tool_calls"] = calls


def _sanitize_call(call, source):
    name, _args = tool_call_name_and_args(call)
    receipt = {
        "kind": "local_execution_request",
        "tool": name,
        "note": "Full local data arguments remain local.",
        "sources": source.get("sources", []),
    }
    out = copy.deepcopy(call)
    if isinstance(out.get("function"), dict):
        raw = out["function"].get("arguments")
        out["function"]["arguments"] = json.dumps(receipt) if isinstance(raw, str) else receipt
    elif "input" in out:
        out["input"] = receipt
    elif isinstance(out.get("state"), dict):
        out["state"] = {**out["state"], "input": receipt}
    return out


def _call_arguments_text(call):
    _name, args = tool_call_name_and_args(call)
    return json.dumps(args, sort_keys=True, default=str)


def _tool_content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _content_json(content):
    bodies = []
    if isinstance(content, str):
        bodies.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                bodies.append(part["text"])
    candidate = {}
    for raw in bodies:
        try:
            body = json.loads(raw)
        except ValueError:
            continue
        if isinstance(body, dict):
            if body.get("data_use"):
                return body
            if not candidate:
                candidate = body
    return candidate


def _replace_content(content, receipt):
    text = json.dumps(receipt)
    if isinstance(content, list):
        return [{"type": "text", "text": text}]
    return text


def _local_receipt(call, source, raw, status=None):
    name, _args = tool_call_name_and_args(call or {})
    if not name:
        name = "tool"
    exit_code = _exit_code(raw)
    withheld = str(raw or "").startswith("[withheld:")
    receipt_status = _receipt_status(source, withheld, status, exit_code)
    return {
        "kind": "local_execution_receipt",
        "tool": name,
        "status": receipt_status,
        "note": (
            "Full tool output remains local. Use source structure and selected operation results; "
            "do not treat this receipt as row values."
        ),
        "sources": source.get("sources", []),
        "artifacts": sorted(set(re.findall(r"examples/[^\s<>)\"`]+", raw or ""))),
        "provider_receipt": "unknown",
        "decision_stage": "unknown",
        "exit": exit_code,
    }


def _receipt_status(source, withheld, status, exit_code):
    status = str(status or "").lower()
    if source.get("withheld") or withheld or status in ("error", "failed") or exit_code not in (None, 0):
        return "error"
    if status in ("cancelled", "canceled"):
        return "cancelled"
    if status == "interrupted":
        return "interrupted"
    return "completed"


def _status_hint(raw, call=None):
    name, _args = tool_call_name_and_args(call or {})
    if name and name != "task":
        return None
    text = "\n".join(str(raw or "").lower().strip().splitlines()[:3])
    if _has_status_hint(text, "cancelled") or _has_status_hint(text, "canceled"):
        return "cancelled"
    if _has_status_hint(text, "interrupted") or _has_status_hint(text, "aborted"):
        return "interrupted"
    if _has_status_hint(text, "failed") or _has_status_hint(text, "error"):
        return "failed"
    return None


def _has_status_hint(text, status):
    return any(re.search(pattern, text) for pattern in (
        rf"^(child|task|background task)?\s*{status}\b",
        rf"^(status|state)\s*[:=]\s*{status}\b",
        rf"\b(child|task|background task)\s+(was\s+)?{status}\b",
        rf"\b(status|state)\s*[:=]\s*{status}\b",
    ))


def _rewrite_image_result(message, call):
    content = message.get("content")
    if not isinstance(content, list) or not any(_is_image_part(p) for p in content):
        return message
    name, _args = tool_call_name_and_args(call or {})
    receipt = {
        "kind": "external_image_receipt",
        "tool": name or "tool",
        "status": "unsupported",
        "note": (
            "This tool returned image content with unknown lineage. The local transcript may keep "
            "the image, but the model-bound view carries only this receipt."
        ),
        "sources": [],
        "lineage": "unknown",
    }
    parts = []
    for part in content:
        if _is_image_part(part):
            parts.append({"type": "text", "text": json.dumps(receipt)})
        else:
            parts.append(part)
    return {**message, "content": parts}


def _rewrite_withheld_image_attachments(message, hidden):
    content = message.get("content")
    if not hidden or not isinstance(content, list) or not any(_is_image_part(p) for p in content):
        return message
    text = content_text(content)
    found = [s for s in hidden if _path_in_text(text, s.get("path"))]
    if not found:
        return message
    receipt = {
        "kind": "withheld_image_receipt",
        "status": "error",
        "note": (
            "Image content is not being sent because this conversation has stopped sending the "
            "attached source. Do not infer its contents."
        ),
        "sources": _dedupe_sources(found),
    }
    parts = [
        {"type": "text", "text": json.dumps(receipt)} if _is_image_part(part) else part
        for part in content
    ]
    return {**message, "content": parts}


def _is_image_part(part):
    if not isinstance(part, dict):
        return False
    kind = str(part.get("type") or "")
    mime = str(part.get("mime") or part.get("mimeType") or "")
    if kind in ("image", "image_url"):
        return True
    if kind == "file" and mime.startswith("image/"):
        return True
    url = str(part.get("url") or "")
    return kind == "file" and url.startswith("data:image/")


def _exit_code(text):
    raw = str(text or "")
    i = raw.rfind(_EXIT)
    if i < 0:
        return None
    digits = ""
    for ch in raw[i + len(_EXIT):]:
        if not ch.isdigit():
            break
        digits += ch
    return int(digits) if digits else None


def _contains_local_text(text, local_texts):
    haystack = str(text or "")
    if not haystack:
        return False
    for raw in local_texts:
        for line in str(raw).splitlines():
            probes = [line.strip(), *re.split(r"[\s,;|]+", line)]
            if any(len(probe) >= 8 and probe in haystack for probe in probes):
                return True
    return False


def _source_for_local_text(text, local_texts, sources):
    if not _contains_local_text(text, local_texts):
        return {}
    out = _dedupe_sources(source for source in sources for source in source.get("sources", []))
    return {"sources": out, "withheld": any(source.get("withheld") for source in sources)}


def _contains_selected(text, selected):
    haystack = str(text or "")
    return any(str(value) and str(value) in haystack for value in _flatten(selected))


def _flatten(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _flatten(item)
    elif isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    else:
        yield value


def _rewrite_open_code_parts(message, sources, hidden, local_texts):
    content = message.get("content")
    if not isinstance(content, list):
        return message
    parts = []
    changed = False
    for part in content:
        if not isinstance(part, dict):
            parts.append(part)
            continue
        name = str(part.get("tool") or part.get("name") or "")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        call = {"tool": name, "state": {"input": state.get("input") or part.get("input") or {}}}
        source = _sources_for_call(call, sources, hidden)
        if not source:
            parts.append(part)
            continue
        raw = "\n".join(str(state.get(k) or "") for k in ("output", "error"))
        if raw:
            local_texts.append(raw)
        receipt = _local_receipt(call, source, raw, status=state.get("status"))
        clean_state = {**state, "input": _sanitize_part_input(call, source)}
        for key in ("output", "error"):
            if isinstance(clean_state.get(key), str):
                clean_state[key] = json.dumps(receipt)
        meta = clean_state.get("metadata")
        if isinstance(meta, dict):
            clean_meta = dict(meta)
            for key in ("output", "error"):
                if isinstance(clean_meta.get(key), str):
                    clean_meta[key] = json.dumps(receipt)
            clean_state["metadata"] = clean_meta
        for key in ("output", "error"):
            if isinstance(clean_state.get(key), list):
                clean_state[key] = _replace_image_parts(clean_state[key], call)
        parts.append({**part, "state": clean_state})
        changed = True
    return {**message, "content": parts} if changed else message


def _sanitize_part_input(call, source):
    clean = _sanitize_call(call, source)
    state = clean.get("state") if isinstance(clean, dict) else None
    return (state or {}).get("input", {})


def _replace_image_parts(parts, call):
    message = _rewrite_image_result({"role": "tool", "content": parts}, call)
    return message.get("content", parts)
