"""A secret's value never reaches the model; its name does (#641).

Every request is scrubbed before it leaves: any Project secret value (8 characters or more —
shorter ones match too much ordinary text) becomes `{env:NAME}`, wherever it sits — a person's
message, a tool result (`echo $KEY` in a shell), or the arguments of a tool call.

When the person's last message names a secret as `{env:NAME}`, the model is told what that means.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_MIN_LEN = 8
_MENTION = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def _map_text(message: dict[str, Any], fn: Callable[[str], str]) -> dict[str, Any]:
    """`message` with `fn` applied to each piece of text in it; the same object if nothing moved."""
    out = message
    content = message.get("content")
    if isinstance(content, str):
        new = fn(content)
        if new != content:
            out = {**out, "content": new}
    elif isinstance(content, list):
        parts = [{**p, "text": fn(p["text"])} if isinstance(p, dict) and isinstance(p.get("text"), str)
                 else p for p in content]
        if any(a is not b and a["text"] != b["text"] for a, b in zip(parts, content, strict=True)):
            out = {**out, "content": parts}
    calls = message.get("tool_calls")
    if isinstance(calls, list):
        new_calls = []
        for call in calls:
            fun = call.get("function") if isinstance(call, dict) else None
            args = fun.get("arguments") if isinstance(fun, dict) else None
            new = fn(args) if isinstance(args, str) else args
            if new != args:
                call = {**call, "function": {**fun, "arguments": new}}
            new_calls.append(call)
        if any(a is not b for a, b in zip(new_calls, calls, strict=True)):
            out = {**out, "tool_calls": new_calls}
    return out


def hide_secret_values(messages: list, values: dict[str, str]) -> list:
    """`messages` with every secret value replaced by `{env:NAME}`; the same list if none occurs."""
    pairs = sorted(((v, n) for n, v in values.items() if len(v) >= _MIN_LEN), key=lambda p: -len(p[0]))
    if not pairs:
        return messages

    def scrub(text: str) -> str:
        for value, name in pairs:
            if value in text:
                text = text.replace(value, "{env:" + name + "}")
        return text

    out = [_map_text(m, scrub) if isinstance(m, dict) else m for m in messages]
    return out if any(a is not b for a, b in zip(out, messages, strict=True)) else messages


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def note_secret_mentions(messages: list) -> list:
    """Append a note to the last user message when it names a secret; the same list otherwise."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, dict) and m.get("role") == "user":
            break
    else:
        return messages
    names = list(dict.fromkeys(_MENTION.findall(_text_of(m.get("content")))))
    if not names:
        return messages
    first = names[0]
    note = (
        f"{', '.join('{env:' + n + '}' for n in names)} "
        f"{'names a secret' if len(names) == 1 else 'name secrets'} stored for this Project; you never "
        f"see the value. In Built App code read it with `secret(\"{first}\")` from `sage_secrets`. "
        "Never print, log or write the value."
    )
    content = m.get("content")
    if isinstance(content, list):
        new = {**m, "content": [*content, {"type": "text", "text": note}]}
    else:
        new = {**m, "content": f"{_text_of(content)}\n\n{note}"}
    return [*messages[:i], new, *messages[i + 1:]]
