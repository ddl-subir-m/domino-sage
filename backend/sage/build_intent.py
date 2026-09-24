"""One canonical, per-turn Build instruction at the provider boundary."""
from __future__ import annotations

import copy
import json
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


def _protocol(value) -> Protocol | None:
    try:
        return value if isinstance(value, Protocol) else Protocol(value)
    except (TypeError, ValueError):
        return None


def install(payload, protocol, intent: BuildIntent) -> dict:
    """Append one ordinary user-text carrier to a copied native request."""
    kind = _protocol(protocol)
    if kind is None or not isinstance(payload, dict):
        raise ValueError("Unsupported Build intent protocol payload")
    result = copy.deepcopy(payload)
    carrier = render(intent)
    if kind is Protocol.CHAT:
        messages = result.setdefault("messages", [])
        if not isinstance(messages, list):
            raise ValueError("Unsupported Chat Completions message payload")
        messages.append({"role": "user", "content": carrier})
    elif kind is Protocol.MESSAGES:
        messages = result.setdefault("messages", [])
        if not isinstance(messages, list):
            raise ValueError("Unsupported Anthropic Messages payload")
        messages.append({"role": "user", "content": [{"type": "text", "text": carrier}]})
    else:
        items = result.setdefault("input", [])
        if isinstance(items, str):
            items = [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": items}
            ]}]
            result["input"] = items
        if not isinstance(items, list):
            raise ValueError("Unsupported Responses input payload")
        items.append({"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": carrier}
        ]})
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


def inspect(payload, protocol, intent: BuildIntent) -> BuildIntentCheck:
    """Verify the one exact carrier at the end of ordinary user text."""
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
    elif texts and texts[-1] == expected:
        status = "ok"
    else:
        status = "changed"
    return BuildIntentCheck(status, carrier_count, carrier_bytes)
