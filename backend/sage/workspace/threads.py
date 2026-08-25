"""Chat Threads — one conversation's files inside a project's workspace.

Build keeps `.sage/session.json` and `.sage/history.jsonl`. A Thread is a sibling tree so a Chat
turn cannot append to the Build transcript (docs/workbench/chat.md).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

_INDEX_LOCK = threading.Lock()
_TITLE_MAX = 60

_KIND_SUFFIX = {
    ".png": "chart",
    ".table.json": "table",
    ".sql": "query",
    ".md": "note",
}


def new_id(prefix: str) -> str:
    """Sortable id (`thr_` / `art_` / `ctx_`) without a ULID dependency: epoch-ms + random."""
    ms = int(time.time() * 1000)
    return f"{prefix}_{ms:011x}{secrets.token_hex(5)}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def title_from_prompt(prompt: str) -> str:
    line = " ".join((prompt or "").strip().split())
    if len(line) <= _TITLE_MAX:
        return line or "Untitled"
    return line[: _TITLE_MAX - 1].rstrip() + "…"


def artifact_kind(name: str) -> str:
    lower = name.lower()
    for suffix, kind in _KIND_SUFFIX.items():
        if lower.endswith(suffix):
            return kind
    return "file"


class ThreadStore:
    def __init__(self, workspace: Path) -> None:
        self._root = Path(workspace)

    @property
    def index_path(self) -> Path:
        return self._root / ".sage" / "threads.json"

    def thread_dir(self, thread_id: str) -> Path:
        return self._root / ".sage" / "threads" / thread_id

    def examples_dir(self, thread_id: str) -> Path:
        return self._root / "examples" / thread_id

    def list(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def get(self, thread_id: str) -> dict | None:
        return next((t for t in self.list() if t.get("id") == thread_id), None)

    def create(self, title: str = "") -> dict:
        now = _now()
        row = {
            "id": new_id("thr"),
            "title": title or "New conversation",
            "createdAt": now,
            "updatedAt": now,
            "pinned": False,
        }
        with _INDEX_LOCK:
            rows = self.list()
            rows.insert(0, row)
            self._write_index(rows)
        self.thread_dir(row["id"]).mkdir(parents=True, exist_ok=True)
        self.examples_dir(row["id"]).mkdir(parents=True, exist_ok=True)
        self.write_context(row["id"], {"items": []})
        self._write_json(self.thread_dir(row["id"]) / "artifacts.json", {"items": []})
        return row

    def touch(self, thread_id: str, *, title: str | None = None) -> dict | None:
        with _INDEX_LOCK:
            rows = self.list()
            for row in rows:
                if row.get("id") != thread_id:
                    continue
                row["updatedAt"] = _now()
                if title is not None:
                    row["title"] = title
                self._write_index(rows)
                return row
        return None

    def read_session_id(self, thread_id: str) -> str | None:
        p = self.thread_dir(thread_id) / "session.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text()).get("session_id")
        except (json.JSONDecodeError, OSError):
            return None

    def write_session_id(self, thread_id: str, session_id: str) -> None:
        p = self.thread_dir(thread_id) / "session.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"session_id": session_id}))

    def history_path(self, thread_id: str) -> Path:
        return self.thread_dir(thread_id) / "history.jsonl"

    def append_history(self, thread_id: str, entry: dict) -> None:
        p = self.history_path(thread_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def read_history(self, thread_id: str) -> list[dict]:
        p = self.history_path(thread_id)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def read_context(self, thread_id: str) -> dict:
        p = self.thread_dir(thread_id) / "context.json"
        if not p.exists():
            return {"items": []}
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {"items": []}
        items = data.get("items") if isinstance(data, dict) else None
        return {"items": items} if isinstance(items, list) else {"items": []}

    def write_context(self, thread_id: str, body: dict) -> None:
        self._write_json(self.thread_dir(thread_id) / "context.json", body)

    def add_context(self, thread_id: str, item: dict) -> dict:
        ctx = self.read_context(thread_id)
        row = {"id": new_id("ctx"), "addedBy": "user", "addedAt": _now(), **item}
        ctx["items"].append(row)
        self.write_context(thread_id, ctx)
        return row

    def remove_context(self, thread_id: str, item_id: str) -> bool:
        ctx = self.read_context(thread_id)
        kept = [i for i in ctx["items"] if i.get("id") != item_id]
        if len(kept) == len(ctx["items"]):
            return False
        self.write_context(thread_id, {"items": kept})
        return True

    def update(self, thread_id: str, *, title: str | None = None, pinned: bool | None = None) -> dict | None:
        with _INDEX_LOCK:
            rows = self.list()
            for row in rows:
                if row.get("id") != thread_id:
                    continue
                if title is not None:
                    row["title"] = title
                if pinned is not None:
                    row["pinned"] = bool(pinned)
                row["updatedAt"] = _now()
                self._write_index(rows)
                return row
        return None

    def delete(self, thread_id: str) -> bool:
        with _INDEX_LOCK:
            rows = self.list()
            kept = [r for r in rows if r.get("id") != thread_id]
            if len(kept) == len(rows):
                return False
            self._write_index(kept)
        return True

    def read_artifacts(self, thread_id: str) -> list[dict]:
        p = self.thread_dir(thread_id) / "artifacts.json"
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    def record_artifact(self, thread_id: str, *, path: str, message_id: str | None = None) -> dict:
        name = Path(path).name
        row = {
            "id": new_id("art"),
            "kind": artifact_kind(name),
            "name": name,
            "title": Path(name).name.rsplit(".", 1)[0].replace("-", " ").replace("_", " "),
            "path": path,
            "producedAt": _now(),
        }
        if message_id:
            row["messageId"] = message_id
        items = self.read_artifacts(thread_id)
        items.append(row)
        self._write_json(self.thread_dir(thread_id) / "artifacts.json", {"items": items})
        # Artifacts belong under IN CONTEXT. Skip if this path is already a chip.
        ctx = self.read_context(thread_id)
        if not any(i.get("path") == path for i in ctx["items"]):
            self.add_context(thread_id, {
                "kind": "artifact",
                "name": row.get("title") or name,
                "path": path,
                "addedBy": "sage",
            })
        return row

    def read_handoff(self, thread_id: str) -> dict | None:
        p = self.thread_dir(thread_id) / "handoff.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def mark_handoff_suggested(self, thread_id: str) -> dict:
        row = self.read_handoff(thread_id) or {}
        if row.get("suggestedAt"):
            return row
        row = {**row, "suggestedAt": _now(), "suppressed": False, "status": "suggested"}
        self._write_json(self.thread_dir(thread_id) / "handoff.json", row)
        return row

    def suppress_handoff(self, thread_id: str) -> dict:
        row = self.read_handoff(thread_id) or {}
        if not row.get("suggestedAt"):
            row["suggestedAt"] = _now()
        row["suppressed"] = True
        row["status"] = "suppressed"
        self._write_json(self.thread_dir(thread_id) / "handoff.json", row)
        return row

    def mark_handoff_planned(self, thread_id: str) -> dict:
        row = self.read_handoff(thread_id) or {}
        if not row.get("suggestedAt"):
            row["suggestedAt"] = _now()
        row["suppressed"] = False
        row["status"] = "planned"
        row["planPath"] = ".sage/plan.md"
        self._write_json(self.thread_dir(thread_id) / "handoff.json", row)
        return row

    def mark_handoff_bound(self, thread_id: str) -> dict:
        row = self.mark_handoff_planned(thread_id)
        row["status"] = "bound"
        row["boundAt"] = _now()
        self._write_json(self.thread_dir(thread_id) / "handoff.json", row)
        return row

    def _write_index(self, rows: list[dict]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(self.index_path, rows)

    @staticmethod
    def _write_json(path: Path, body: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2))


_SKIP_SNAPSHOT_PARTS = frozenset({"node_modules", ".git", "dist", "__pycache__"})


def snapshot_files(root: Path) -> dict[str, bytes]:
    """Workspace-relative file bytes, skipping heavy/generated trees. Used to revert Chat writes
    that landed outside the allowlist."""
    out: dict[str, bytes] = {}
    root = Path(root)
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part in _SKIP_SNAPSHOT_PARTS for part in Path(rel).parts):
            continue
        try:
            out[rel] = p.read_bytes()
        except OSError:
            continue
    return out


def revert_denied_writes(root: Path, thread_id: str, before: dict[str, bytes]) -> list[str]:
    """Undo writes a Chat turn made outside examples/<threadId>/ and .sage/threads/<threadId>/."""
    from ..shim.chat_paths import chat_path_allowed

    reverted: list[str] = []
    root = Path(root)
    after = snapshot_files(root)
    for rel, data in after.items():
        if chat_path_allowed(rel, thread_id):
            continue
        path = root / rel
        prev = before.get(rel)
        if prev is None:
            path.unlink(missing_ok=True)
            reverted.append(rel)
        elif prev != data:
            path.write_bytes(prev)
            reverted.append(rel)
    return reverted


def new_artifact_paths(root: Path, thread_id: str, before: dict[str, bytes]) -> list[str]:
    prefix = f"examples/{thread_id}/"
    after = snapshot_files(root)
    return sorted(
        rel for rel, data in after.items()
        if rel.startswith(prefix) and before.get(rel) != data
    )
