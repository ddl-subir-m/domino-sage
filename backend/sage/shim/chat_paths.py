"""Which paths a sage-chat turn may write. Pure: no I/O, no OpenCode.

The shim and the orchestrator both call `chat_path_allowed`. OpenCode's own permission
config does not enforce this (see phase_classifier.READ_ONLY_DENIED); a denied write must
be stripped from the request and reverted on disk if it still landed.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..router.phase_classifier import WRITE_TOOLS

# Thread ids we mint (`thr_` + hex) plus anything already on disk. A path using another id
# is not "this Thread" even if it sits under examples/.
_THREAD_ID = re.compile(r"^thr_[a-zA-Z0-9_-]+$")


def normalize_write_path(path: str) -> str:
    """Workspace-relative posix path, no leading slash. Unknown prefixes (`/mnt/code/`,
    `/workspaces/`) are stripped so an absolute tool arg still classifies."""
    rel = (path or "").replace("\\", "/").strip()
    if not rel:
        return ""
    for prefix in ("/mnt/code/", "/workspaces/",):
        if prefix in rel:
            rel = rel.split(prefix, 1)[-1]
    return rel.lstrip("/")


def chat_path_allowed(path: str, thread_id: str) -> bool:
    """True only for files under this Thread's Artifact dir or its `.sage/threads/` dir."""
    if not _THREAD_ID.match(thread_id or ""):
        return False
    rel = normalize_write_path(path)
    if not rel or rel.endswith("/"):
        return False
    examples = f"examples/{thread_id}/"
    meta = f".sage/threads/{thread_id}/"
    return rel.startswith(examples) or rel.startswith(meta)


def write_path_from_tool_call(call: dict[str, Any]) -> str | None:
    """The file a write/edit tool_call would touch, or None if this call is not a write.

    Understands the OpenAI tool_call shape the shim sees and the OpenCode `state.input` shape
    the orchestrator sees.
    """
    name = ""
    args: Any = {}
    fn = call.get("function") if isinstance(call, dict) else None
    if isinstance(fn, dict):
        name = str(fn.get("name") or "")
        raw = fn.get("arguments")
        if isinstance(raw, dict):
            args = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
    elif isinstance(call, dict):
        name = str(call.get("tool") or call.get("name") or "")
        args = (call.get("state") or {}).get("input") or call.get("input") or {}
    if name.lower() not in WRITE_TOOLS:
        return None
    if not isinstance(args, dict):
        return None
    path = args.get("path") or args.get("filePath") or args.get("file_path") or ""
    return str(path) if path else None


def strip_denied_writes(messages: list[Any], thread_id: str) -> list[Any]:
    """Drop write/edit tool_calls (and their matching tool results) whose path is outside
    this Thread's allowlist, so the model is not asked to continue an illegal write."""
    if not isinstance(messages, list):
        return messages
    drop_ids: set[str] = set()
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            path = write_path_from_tool_call(call)
            if path and not chat_path_allowed(path, thread_id):
                cid = call.get("id")
                if cid:
                    drop_ids.add(str(cid))
    if not drop_ids:
        return messages
    out: list[Any] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        if m.get("role") == "tool" and str(m.get("tool_call_id") or "") in drop_ids:
            continue
        calls = m.get("tool_calls")
        if m.get("role") == "assistant" and isinstance(calls, list):
            kept = [c for c in calls if not (isinstance(c, dict) and str(c.get("id") or "") in drop_ids)]
            if not kept:
                # An assistant message that was only illegal writes would be an empty tool_calls
                # list, which some providers reject — drop the message if it has no content either.
                content = m.get("content")
                if not content:
                    continue
                out.append({**m, "tool_calls": []})
                continue
            if len(kept) != len(calls):
                out.append({**m, "tool_calls": kept})
                continue
        out.append(m)
    return out
