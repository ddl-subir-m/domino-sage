"""Observed data operations and per-request evidence. No provider receipt is inferred."""

import copy
import json
import logging
import re
import threading
from collections import deque
from uuid import uuid4

from .. import timing
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

log = logging.getLogger("sage.liveread")

_PYTHON_TOOLS = frozenset({"python", "python_exec", "python_execute"})
_EXECUTION_TOOLS = SHELL_TOOLS | _PYTHON_TOOLS
_DELEGATION_TOOLS = frozenset({"task"})
_PATH_KEYS = ("path", "filePath", "file_path")
_COMMAND_KEYS = ("command", "cmd", "code")
_EXIT = "Command exited with code "
# The one string every redaction placeholder carries, so that a placeholder the model has written
# back into its own work can be recognised however it was reshaped (#510).
_MARK_BODY = "local data withheld"
_MARKER_ECHO_DEDUPE_IDS = 10_000
_MARKER_ECHO_COUNT_CAP = 10_000
_MARK_ECHO_REPLACEMENT = "[copied withheld placeholder removed after prior execution]"
_MARK_ECHO_COMMAND_REPLACEMENT = ": # copied withheld placeholder removed after prior execution"
_MARK_ECHO_PYTHON_REPLACEMENT = "# copied withheld placeholder removed after prior execution"
_MARK_ECHO_CORRECTION = (
    "This call reproduced the placeholder that stands in the place of withheld local data in this "
    "conversation. The placeholder is a marker: it is not a command, and not content to write to a "
    "file. The tool already ran and may have changed files. This model-facing result is a "
    "correction, not evidence of success. Write what you actually mean to run, or ask the person "
    "for what you need."
)

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
        # Recent call ids dedupe cumulative history rewrites without growing for the life of the
        # process. They are held only in memory and are never logged or exported. A process restart
        # deliberately resets both the window and the saturated observation count.
        self._logged_marker_echoes: set[str] = set()
        self._logged_marker_echo_order: deque[str] = deque()
        self._observed_marker_echoes = 0

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
                reply = {"data_use": oid, "coverage": event.get("coverage") or {},
                         "selected_fields": []}
                if "columns" in event:
                    reply["columns"] = event.get("columns") or []
                if "result_rows" in event:
                    reply["result_rows"] = event.get("result_rows")
                if event.get("artifact"):
                    reply["local_reference"] = event["artifact"]
                self.operations[oid] = (copy.deepcopy(event), reply, persist)
                self._remember_sources([_source_from_event(event)])

    def events(self, turn_id):
        with self.lock:
            return [copy.deepcopy(event) for event, _, _ in self.operations.values()
                    if event["turn_id"] == turn_id]

    def resolve_image_delivery(self, request: dict, model: str, *, capable: bool,
                               sent_count: int) -> None:
        """Finish pending image-reference audit rows from the actual routed request.

        The router decision exists only in the shim, after OpenCode has made the request. Reference
        preparation therefore records `pending`, and this method replaces it once with the truth
        about the request that was sent. Image bytes and data URIs are never retained here.
        """
        messages = request.get("messages")
        if not isinstance(messages, list):
            return
        with self.lock:
            used = {
                oid
                for message in messages
                if isinstance(message, dict)
                for oid, _event, _reply in self._selected_operation_args(
                    content_text(message.get("content"))
                )
            }
            pending = [entry for oid, entry in self.operations.items()
                       if oid in used
                       and entry[0].get("operation") == "image_reference"
                       and entry[0].get("delivery") == "pending"]
            for index, (event, _reply, persist) in enumerate(pending):
                sent = capable and index < sent_count
                event["delivery"] = "sent" if sent else "not_sent"
                event["failure"] = None if sent else ("capability" if not capable else "carrier")
                event["serving_model"] = model
                persist({"type": "data_used", "dataUsed": [copy.deepcopy(event)]})

    def finish_image_delivery(self, turn_id: str) -> None:
        """Close image audit rows when a turn ended before a model request used them."""
        if not turn_id:
            return
        with self.lock:
            pending = [entry for entry in self.operations.values()
                       if entry[0].get("turn_id") == turn_id
                       and entry[0].get("operation") == "image_reference"
                       and entry[0].get("delivery") == "pending"]
            for event, _reply, persist in pending:
                event["delivery"] = "not_sent"
                event["failure"] = "no_request"
                persist({"type": "data_used", "dataUsed": [copy.deepcopy(event)]})

    def apply_restrictions(self, request, withheld=None, rewrite_counts=None):
        if not isinstance(request.get("messages"), list):
            return request
        hidden = _withheld_sources(withheld)
        if not hidden:
            return request
        return {
            **request,
            "messages": [
                _rewrite_withheld_image_attachments(message, hidden, rewrite_counts)
                if isinstance(message, dict) else message
                for message in request["messages"]
            ],
        }

    def prepare(self, request, withheld=None, rewrite_counts=None):
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
        call_messages: dict[str, dict] = {}
        completed_echo_ids: list[str] = []
        newly_observed_counts: list[int] = []
        with self.lock:
            for message in request.get("messages", []):
                if not isinstance(message, dict):
                    messages.append(message)
                    continue
                message = copy.deepcopy(message)
                message = _rewrite_withheld_image_attachments(message, hidden, rewrite_counts)
                message = _rewrite_open_code_parts(
                    message, sources, hidden, local_texts, rewrite_counts)
                for call in message.get("tool_calls") or []:
                    if isinstance(call, dict):
                        cid = str(call.get("id") or "")
                        calls[call.get("id")] = call
                        if cid:
                            call_messages[cid] = message
                        source = _sources_for_call(call, sources, hidden)
                        if cid and source:
                            direct[cid] = source
                            call = _sanitize_call(call, source)
                            _bump(rewrite_counts, "redactedCalls")
                            _replace_call(message, cid, call)
                        else:
                            raw_args = _call_arguments_text(call)
                            if _contains_local_text(raw_args, local_texts):
                                source = {"sources": [], "withheld": False}
                                direct[cid] = source
                                _replace_call(message, cid, _sanitize_call(call, source))
                                _bump(rewrite_counts, "redactedCalls")
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
                            artifact = str(event.get("artifact") or "")
                            if artifact and (path == artifact or path.endswith("/" + artifact)):
                                shape = {k: v for k, v in reply.items() if k != "selected"}
                                shape["selected_fields"] = []
                                message = {**message, "content": json.dumps(shape)}
                                break
                    cid = str(message.get("tool_call_id") or "")
                    call = calls.get(message.get("tool_call_id"), {})
                    if _echoes_the_withheld_mark(call):
                        # Answered BEFORE the receipt branch, and keyed on the call rather than on
                        # this result. The call carries no local data to withhold — it carries this
                        # module's own placeholder — so a receipt here would report a successful
                        # local execution and tell the model its imitation had worked (#510). What
                        # goes back instead is the only thing that ends the loop: the shim cannot
                        # refuse an execution (the tools run inside OpenCode, and its permission
                        # block gates by tool NAME), so the model has already run this. Saying so
                        # plainly is repair-after, which is the seam this architecture has.
                        message = {**message, "content": _MARK_ECHO_CORRECTION}
                        _bump(rewrite_counts, "markerEchoCorrections")
                        if cid and cid in call_messages:
                            visible = next((item for item in call_messages[cid].get("tool_calls") or []
                                            if isinstance(item, dict)
                                            and str(item.get("id") or "") == cid), call)
                            _replace_call(call_messages[cid], cid, _repair_marker_echo_call(visible))
                        if cid:
                            completed_echo_ids.append(cid)
                    elif cid in direct:
                        raw = _tool_content_text(message.get("content"))
                        if raw:
                            local_texts.append(raw)
                        receipt = _local_receipt(call, direct[cid], raw,
                                                 status=_status_hint(raw, call))
                        message = {**message, "content": _replace_content(message.get("content"),
                                                                          receipt)}
                        _bump(rewrite_counts, "localExecutionReceipts")
                    else:
                        message = _rewrite_image_result(message, call, rewrite_counts)
                else:
                    text = _tool_content_text(message.get("content"))
                    for oid, _event, _reply in self._selected_operation_args(text):
                        used.add(oid)
                    source = _source_for_local_text(text, local_texts, direct.values())
                    if source:
                        message = {**message,
                                   "content": _redact_message_text(message.get("content"), source)}
                messages.append(message)
            # Only the bounded tail can be new in a cumulative request. Looking at the whole
            # history after an eviction would make old ids look new and recreate the warning spam.
            recent, selected = [], set()
            for cid in reversed(completed_echo_ids):
                if cid not in selected:
                    recent.append(cid)
                    selected.add(cid)
                    if len(recent) >= _MARKER_ECHO_DEDUPE_IDS:
                        break
            for cid in reversed(recent):
                if cid in self._logged_marker_echoes:
                    continue
                while len(self._logged_marker_echo_order) >= _MARKER_ECHO_DEDUPE_IDS:
                    expired = self._logged_marker_echo_order.popleft()
                    self._logged_marker_echoes.discard(expired)
                self._logged_marker_echo_order.append(cid)
                self._logged_marker_echoes.add(cid)
                self._observed_marker_echoes = min(
                    self._observed_marker_echoes + 1, _MARKER_ECHO_COUNT_CAP)
                newly_observed_counts.append(self._observed_marker_echoes)
        if newly_observed_counts:
            rec = timing.current()
            turn_id = rec.turn_id if rec is not None else "unknown"
            ordinal = rec.calls[-1].n if rec is not None and rec.calls else 0
            for observed in newly_observed_counts:
                # One event per new completed echo. The call id is used only for in-memory dedupe;
                # commands, arguments, results, paths and call ids never enter this event.
                log.warning("data use: newly completed withheld-marker echo corrected; "
                            "turn_id=%s model_call_ordinal=%d new_marker_echoes=1 "
                            "observed_marker_echoes=%d; tool execution already occurred",
                            turn_id, ordinal, observed)
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
                                 for c in body.get("choices", [])) or body.get("type") in ("message_stop", "response.completed"):
                            completed = True
                        elif body.get("type") in ("response.failed", "response.incomplete"):
                            evidence["state"] = "failed"
                            evidence["failure"] = "incomplete"
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


# The three counts on a `coverage` record that mean a read came back with less than it was asked
# for, and the request state that means the response never settled. The same two lists
# `store.js`'s `FELL_SHORT` and `DID_NOT_SETTLE` hold, for the same rule (ADR-0062 §2): a read
# that fell short is never hidden. Kept in step by
# `test_a_working_read_is_folded_out_of_the_answer`, which reads both files.
FELL_SHORT = ("excluded", "failed", "unfinished")
DID_NOT_SETTLE = ("interrupted",)


def fell_short(event: dict) -> bool:
    """Whether this operation reports less than it was asked for."""
    coverage = event.get("coverage") or {}
    if any((coverage.get(key) or 0) > 0 for key in FELL_SHORT):
        return True
    return any(r.get("failure") or r.get("state") in DID_NOT_SETTLE
               for r in (event.get("requests") or []) if isinstance(r, dict))


def artifact_roles(events) -> dict[str, str]:
    """Which role each of a turn's operations claimed, keyed by the path it wrote (ADR-0063).

    This is the publish-side half of the join. The operation site decided the role while the
    statement was in hand; `new_artifact_paths` finds the file with nothing but its path, and
    `artifact` is what puts the two back together.

    Two things are downgraded to `'answer'` here rather than at the operation site. A role this
    function does not recognise, because an unknown word must not be taken for a verdict. And an
    operation that FELL SHORT — that decision cannot be made where the role is, because a request
    can fail after the read returned, so it is made here, at the end of the turn, when the event is
    as complete as it will get.

    Last write wins on a repeated path. Two reads in one turn can slug to the same filename, and
    the second one is what is on disk, so the second one's role is the one that describes it.
    """
    roles: dict[str, str] = {}
    for event in events or []:
        path = str(event.get("artifact") or "")
        if not path:
            continue
        role = event.get("role")
        roles[path] = "working" if role == "working" and not fell_short(event) else "answer"
    return roles


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
            "rows": (None if event.get("operation") == "document_reference"
                     else event.get("coverage", {}).get("processed", event.get("result_rows")))}


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


def _withheld_mark(source, comment=False):
    """What stands in the place of a withheld value, as text of the kind it replaced.

    `comment=True` for a slot that holds a command: a `#` line is a command a shell accepts, so a
    sanitised `bash` call is still a call that would run.

    The command form says what it is. A `#` line is a valid command and a NO-OP, and the model
    reads it back as its own prior work with nothing to tell a placeholder from content — measured
    in #510, where it was reproduced as a whole command five times until the repeated-call guard
    stopped the turn, and written as the body of four heredocs. The sentence does not make that
    impossible, and nothing written in this slot could: anything runnable is imitable. It lowers
    the odds; `_echoes_the_withheld_mark` is what answers the case where it happens anyway.

    A zero count is dropped rather than printed. The caller at `_rewrite_request` that withholds a
    call quoting local text without a named source passes an empty source list, and "0 sources"
    reads as a defect to whoever meets it — including the model.

    The command form NAMES its sources where the value form counts them, so that two commands over
    two different files do not read back to the model as one command it ran twice. The model keys
    its own history on what it can see, and a placeholder that collapses distinct steps takes away
    its only record of which ones it has taken. This does not disclose anything: `_sanitize_call`
    already keeps a `read`'s path verbatim, for the reason written there. Two commands over the
    SAME file still collapse, and nothing short of showing the command can separate them — the
    command text is the thing being withheld.
    """
    paths = [str(s.get("path") or "") for s in source.get("sources") or []]
    paths = [path for path in paths if path]
    if comment:
        named = f"{_MARK_BODY}: {', '.join(paths)}" if paths else _MARK_BODY
        return f"# <{named} — a placeholder, not a command>"
    count = len(source.get("sources") or [])
    body = _MARK_BODY
    if count:
        body += f": {count} source" + ("" if count == 1 else "s")
    return f"[{body}]"


def _echoes_the_withheld_mark(call):
    """The model wrote a redaction placeholder back as its own content (#510).

    Not bounded to `bash`, although `bash` is where it was measured. The placeholder goes into
    every string argument of a sanitised call, so every slot the model can write is a slot it can
    imitate, and a guard drawn around the tool that happened to bite is short by construction.

    Keyed on `_MARK_BODY` rather than on the whole marker: the reproductions were not copies. The
    count varied, and one heredoc carried the literal `N` from no marker this code ever emitted.

    The accepted cost of keying on a bare phrase: a call that legitimately carries "local data
    withheld" in an argument — a person working on a governance CSV that uses those words — is
    answered with the correction instead of its own result. Retire this by giving the marker a
    token no prose would collide with, but only once something needs it; a rarer string is also a
    string the model is likelier to reproduce EXACTLY, and exact reproduction is the case that
    matters. Nothing measured has hit this.
    """
    _name, args = tool_call_name_and_args(call or {})
    return arguments_echo_withheld_mark(args)


def arguments_echo_withheld_mark(arguments):
    """Whether any model-authored string argument carries the withheld marker."""
    if isinstance(arguments, str):
        return _MARK_BODY in arguments
    if isinstance(arguments, list):
        return any(arguments_echo_withheld_mark(value) for value in arguments)
    if isinstance(arguments, dict):
        return any(arguments_echo_withheld_mark(value) for value in arguments.values())
    return False


def _repair_marker_echo_call(call):
    """Remove the imitable marker from the outbound copy of a completed call.

    The real call has already executed. Required keys and JSON types stay present, while no string
    argument sent back to the model can teach it the marker again. The source history is untouched.
    """
    name, args = tool_call_name_and_args(call or {})

    def clean(value, key=""):
        if isinstance(value, str) and _MARK_BODY in value:
            if name in _EXECUTION_TOOLS and key in _COMMAND_KEYS:
                if name in _PYTHON_TOOLS:
                    return _MARK_ECHO_PYTHON_REPLACEMENT
                return _MARK_ECHO_COMMAND_REPLACEMENT
            return _MARK_ECHO_REPLACEMENT
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {nested_key: clean(item, str(nested_key))
                    for nested_key, item in value.items()}
        return value

    repaired = clean(args)
    out = copy.deepcopy(call)
    if isinstance(out.get("function"), dict):
        raw = out["function"].get("arguments")
        out["function"]["arguments"] = json.dumps(repaired) if isinstance(raw, str) else repaired
    elif "input" in out:
        out["input"] = repaired
    elif isinstance(out.get("state"), dict):
        out["state"] = {**out["state"], "input": repaired}
    return out


def _redacted_like(value, source, comment=False):
    """A placeholder of the same JSON type as the value it replaces.

    Scalars other than strings are kept: a limit, an offset or a flag is the model's own parameter,
    and a number invented here can fall outside what the tool accepts.
    """
    if isinstance(value, str):
        return _withheld_mark(source, comment=comment)
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return value


def _sanitize_call(call, source):
    """Redact a call's argument VALUES, keeping every key the call carried (#507).

    Replacing the whole arguments object left a `bash` call with no `command`, so the next call the
    model wrote in that shape was refused by the tool as missing a required key. A path is kept as
    it is: the receipt already names every source, so the path discloses nothing the model is not
    being told anyway, and a redaction marker in a path slot is itself a shape worth imitating.
    """
    _name, args = tool_call_name_and_args(call)
    clean = {
        key: value if key in _PATH_KEYS else _redacted_like(value, source, key in _COMMAND_KEYS)
        for key, value in args.items()
    }
    out = copy.deepcopy(call)
    if isinstance(out.get("function"), dict):
        raw = out["function"].get("arguments")
        out["function"]["arguments"] = json.dumps(clean) if isinstance(raw, str) else clean
    elif "input" in out:
        out["input"] = clean
    elif isinstance(out.get("state"), dict):
        out["state"] = {**out["state"], "input": clean}
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


def _redact_message_text(content, source):
    """Withhold a message's text and leave a message behind (#507).

    An assistant or user message is not a tool result, so a receipt object is not a valid instance
    of it. Standing one in a message's place taught the model to answer the person in that shape,
    and the person was shown the receipt as their answer. The text goes; what replaces it is text,
    and every part the message carried is still there.

    Every part carrying text is redacted, not only the `text` parts the match was found in: the
    whole content was dropped before this, so a part that quotes the same rows under another type
    must not survive the narrower rewrite. Parts with no text — an image attachment, a tool part
    already rewritten above — are left to the guards that own them.
    """
    mark = _withheld_mark(source)
    if isinstance(content, list):
        return [{**part, "text": mark}
                if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"]
                else part
                for part in content]
    return mark


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


def _rewrite_image_result(message, call, rewrite_counts=None):
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
            _bump(rewrite_counts, "externalImageReceipts")
        else:
            parts.append(part)
    return {**message, "content": parts}


def _rewrite_withheld_image_attachments(message, hidden, rewrite_counts=None):
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
    parts = []
    for part in content:
        if _is_image_part(part):
            parts.append({"type": "text", "text": json.dumps(receipt)})
            _bump(rewrite_counts, "withheldImageReceipts")
        else:
            parts.append(part)
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


def _rewrite_open_code_parts(message, sources, hidden, local_texts, rewrite_counts=None):
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
        _bump(rewrite_counts, "redactedCalls")
        clean_state = {**state, "input": _sanitize_part_input(call, source)}
        # An empty string carries no data to withhold, and `""` is what a call that succeeded
        # leaves in `error` — a receipt written there reports a failure that did not happen (#507).
        for key in ("output", "error"):
            if isinstance(clean_state.get(key), str) and clean_state[key]:
                clean_state[key] = json.dumps(receipt)
                _bump(rewrite_counts, "localExecutionReceipts")
        meta = clean_state.get("metadata")
        if isinstance(meta, dict):
            clean_meta = dict(meta)
            for key in ("output", "error"):
                if isinstance(clean_meta.get(key), str) and clean_meta[key]:
                    clean_meta[key] = json.dumps(receipt)
                    _bump(rewrite_counts, "localExecutionReceipts")
            clean_state["metadata"] = clean_meta
        for key in ("output", "error"):
            if isinstance(clean_state.get(key), list):
                clean_state[key] = _replace_image_parts(
                    clean_state[key], call, rewrite_counts)
        parts.append({**part, "state": clean_state})
        changed = True
    return {**message, "content": parts} if changed else message


def _sanitize_part_input(call, source):
    clean = _sanitize_call(call, source)
    state = clean.get("state") if isinstance(clean, dict) else None
    return (state or {}).get("input", {})


def _replace_image_parts(parts, call, rewrite_counts=None):
    message = _rewrite_image_result(
        {"role": "tool", "content": parts}, call, rewrite_counts)
    return message.get("content", parts)


def _bump(counts, key):
    """Increment fixed request-local metadata without making diagnostics required."""
    if counts is not None:
        counts[key] = counts.get(key, 0) + 1
