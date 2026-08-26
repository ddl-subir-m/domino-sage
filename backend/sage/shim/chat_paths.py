"""Which paths a sage-chat turn may write. Pure: no I/O, no OpenCode.

The shim and the orchestrator both call `chat_path_allowed`. OpenCode's own permission
config does not enforce this (see phase_classifier.READ_ONLY_DENIED); a denied write must
be rejected in the tool result (so the model retries the Artifact path) and reverted on
disk if it still landed. Silent-dropping the tool_call made the model retry `src/` forever.
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


def denied_write_result(thread_id: str) -> str:
    """What the model sees when a Chat write missed the Artifact dir.

    Addressed to the model, not the user. Must name the path so the next call lands
    under examples/<threadId>/ instead of retrying src/.
    """
    return (
        f"Write rejected: this Chat turn cannot change the app. "
        f"Save a PNG chart at examples/{thread_id}/<slug>.png or a table at "
        f"examples/{thread_id}/<slug>.table.json. Never write under src/ "
        f"(including src/examples/)."
    )


def strip_denied_writes(messages: list[Any], thread_id: str) -> list[Any]:
    """Turn illegal Chat writes into tool errors so the model retries the Artifact path.

    Dropping the tool_call (the previous behaviour) hid the attempt, so the model
    called write on src/ again — dozens of times — and Chat filled with 'Ran write'.
    """
    if not isinstance(messages, list):
        return messages
    denied: dict[str, str] = {}
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
                    denied[str(cid)] = path
    if not denied:
        return messages
    have_result = {
        str(m.get("tool_call_id"))
        for m in messages
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id")
    }
    err = denied_write_result(thread_id)
    out: list[Any] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        if m.get("role") == "tool" and str(m.get("tool_call_id") or "") in denied:
            out.append({**m, "content": err})
            continue
        out.append(m)
        if m.get("role") == "assistant":
            for call in m.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                cid = str(call.get("id") or "")
                if cid in denied and cid not in have_result:
                    out.append({"role": "tool", "tool_call_id": cid, "content": err})
    return out
