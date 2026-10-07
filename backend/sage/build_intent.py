"""One canonical, per-turn Build instruction at the provider boundary."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from .gateway.protocol import Protocol

BuildIntentKind = Literal["direct_build", "approved_plan", "phase"]
BuildIntentStatus = Literal["ok", "missing", "changed", "duplicate", "unsupported"]

_PRECEDENCE = (
    "The edited and approved plan overrides conflicts. "
    "The exact source request fills omissions."
)
_COMMAND = "Implement now. Use tools, edit files, and verify the result."
_CARRIER = re.compile(
    r"<<<SAGE_BUILD_INTENT:(?P<id>[^:\s]+):START>>>\n(?P<body>.*)\n"
    r"<<<SAGE_BUILD_INTENT:(?P=id):END>>>", re.DOTALL)


@dataclass(frozen=True)
class BuildIntent:
    intent_id: str
    kind: BuildIntentKind
    source_requests: tuple[str, ...]
    authoritative_plan: str = ""
    answers: str = ""
    handoff_note: str = ""
    phase_brief: str = ""
    phase_index: str = ""
    prior_phase_notes: tuple[str, ...] = ()

    @classmethod
    def for_direct(cls, request: str) -> BuildIntent:
        return cls(uuid4().hex, "direct_build", (request,))

    @classmethod
    def for_approved(cls, source_requests, plan: str, answers: str,
                     handoff_note: str) -> BuildIntent:
        return cls(uuid4().hex, "approved_plan", tuple(source_requests),
                   authoritative_plan=plan, answers=answers, handoff_note=handoff_note)

    @classmethod
    def for_phase(cls, source_requests, step: str, step_index: str, answers: str,
                  prior_notes) -> BuildIntent:
        return cls(uuid4().hex, "phase", tuple(source_requests), answers=answers,
                   phase_brief=step, phase_index=step_index,
                   prior_phase_notes=tuple(prior_notes))


@dataclass(frozen=True)
class BuildIntentCheck:
    status: BuildIntentStatus
    carrier_count: int
    carrier_bytes: int


def _delimiters(intent: BuildIntent) -> tuple[str, str]:
    return (f"<<<SAGE_BUILD_INTENT:{intent.intent_id}:START>>>",
            f"<<<SAGE_BUILD_INTENT:{intent.intent_id}:END>>>")


def render(intent: BuildIntent) -> str:
    """Render readable JSON whose user-controlled delimiters stay escaped."""
    body = json.dumps({
        "kind": intent.kind,
        "source_requests": list(intent.source_requests),
        "authoritative_plan": intent.authoritative_plan,
        "answers": intent.answers,
        "handoff_note": intent.handoff_note,
        "phase_brief": intent.phase_brief,
        "phase_index": intent.phase_index,
        "prior_phase_notes": list(intent.prior_phase_notes),
        "precedence": _PRECEDENCE,
        "command": _COMMAND,
    }, ensure_ascii=False, separators=(",", ":"))
    # JSON quoting protects control characters and code fences. Escaping angle brackets keeps a
    # delimiter-like value inside a field from becoming a second structural delimiter.
    body = body.replace("<", "\\u003c").replace(">", "\\u003e")
    start, end = _delimiters(intent)
    return f"{start}\n{body}\n{end}"


def carries(text: str) -> bool:
    return _CARRIER.search(text) is not None


def without_source_requests(text: str) -> str:
    """The text with each carrier's `source_requests` emptied, for the local-data check (#590).

    The source requests are the person's own words, and what they chose to send is not local data
    to withhold from the request. The plan, answers and notes are model-written and stay checked.
    The carrier shares its message with the person's own text (#672), so it is found within it.
    """
    def emptied(match: re.Match) -> str:
        try:
            body = json.loads(match.group("body"))
            body["source_requests"] = []
        except (ValueError, TypeError):
            return match.group(0)
        return json.dumps(body, ensure_ascii=False)

    return _CARRIER.sub(emptied, text)


def _protocol(value) -> Protocol | None:
    try:
        return value if isinstance(value, Protocol) else Protocol(value)
    except (TypeError, ValueError):
        return None


def _is_person_message(row, protocol: Protocol) -> bool:
    """A user message the person authored: not a tool result, which Messages carries as `user`."""
    if not isinstance(row, dict) or row.get("role") != "user":
        return False
    if protocol is Protocol.RESPONSES:
        return row.get("type", "message") == "message"
    if protocol is Protocol.MESSAGES and isinstance(row.get("content"), list):
        return not any(isinstance(part, dict) and part.get("type") == "tool_result"
                       for part in row["content"])
    return True


def _person_index(rows: list, protocol: Protocol) -> int | None:
    return next((i for i in range(len(rows) - 1, -1, -1)
                 if _is_person_message(rows[i], protocol)), None)


def install(payload, protocol, intent: BuildIntent) -> dict:
    """Attach one carrier text part to the last message the person authored, in a copy.

    Never after the tool exchange (#672): there it read as a fresh command on every call, and the
    model answered "already done" after each tool result. With no such message it is appended.
    """
    kind = _protocol(protocol)
    if kind is None or not isinstance(payload, dict):
        raise ValueError("Unsupported Build intent protocol payload")
    result = copy.deepcopy(payload)
    key = "input" if kind is Protocol.RESPONSES else "messages"
    rows = result.setdefault(key, [])
    if kind is Protocol.RESPONSES and isinstance(rows, str):
        rows = [{"type": "message", "role": "user", "content": rows}]
        result[key] = rows
    if not isinstance(rows, list):
        raise ValueError("Unsupported Build intent protocol payload")
    part_type = "input_text" if kind is Protocol.RESPONSES else "text"
    part = {"type": part_type, "text": render(intent)}
    index = _person_index(rows, kind)
    if index is None:
        rows.append({"role": "user", "content": [part]})
        if kind is Protocol.RESPONSES:
            rows[-1]["type"] = "message"
        return result
    content = rows[index].get("content", "")
    if isinstance(content, str):
        content = [{"type": part_type, "text": content}] if content else []
    if not isinstance(content, list):
        raise ValueError("Unsupported Build intent protocol payload")
    rows[index] = {**rows[index], "content": [*content, part]}
    return result


def _content_texts(content, *, part_types: tuple[str, ...]) -> list[str] | None:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return None
    texts = []
    for part in content:
        if not isinstance(part, dict):
            return None
        if part.get("type") in part_types and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return texts


def _ordinary_user_text(payload: dict, protocol: Protocol) -> list[str] | None:
    rows = payload.get("messages" if protocol is not Protocol.RESPONSES else "input", [])
    if protocol is Protocol.RESPONSES and isinstance(rows, str):
        return [rows]
    if not isinstance(rows, list):
        return None
    texts: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        if protocol is Protocol.RESPONSES:
            if row.get("type", "message") != "message" or row.get("role") != "user":
                continue
            found = _content_texts(row.get("content", ""), part_types=("input_text",))
        else:
            if row.get("role") != "user":
                continue
            found = _content_texts(
                row.get("content", ""),
                part_types=("text", "input_text") if protocol is Protocol.CHAT else ("text",),
            )
        if found is None:
            return None
        texts.extend(found)
    return texts


def _person_texts(payload: dict, protocol: Protocol) -> list[str]:
    rows = payload.get("input" if protocol is Protocol.RESPONSES else "messages", [])
    index = _person_index(rows, protocol) if isinstance(rows, list) else None
    if index is None:
        return []
    return _content_texts(
        rows[index].get("content", ""),
        part_types=("text", "input_text") if protocol is Protocol.CHAT else (
            ("input_text",) if protocol is Protocol.RESPONSES else ("text",)),
    ) or []


def inspect(payload, protocol, intent: BuildIntent) -> BuildIntentCheck:
    """Verify the one exact carrier is a text of the last message the person authored."""
    kind = _protocol(protocol)
    if kind is None or not isinstance(payload, dict):
        return BuildIntentCheck("unsupported", 0, 0)
    texts = _ordinary_user_text(payload, kind)
    if texts is None:
        return BuildIntentCheck("unsupported", 0, 0)
    expected = render(intent)
    start, _ = _delimiters(intent)
    carrier_count = sum(text.count(start) for text in texts)
    carrier_bytes = sum(len(text.encode("utf-8")) for text in texts if start in text)
    if carrier_count == 0:
        status: BuildIntentStatus = "missing"
    elif carrier_count > 1:
        status = "duplicate"
    elif expected in _person_texts(payload, kind):
        status = "ok"
    else:
        status = "changed"
    return BuildIntentCheck(status, carrier_count, carrier_bytes)
