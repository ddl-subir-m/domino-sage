"""Chat Threads — one conversation's files inside a project's workspace.

Build keeps `.sage/session.json` and `.sage/history.jsonl`. A Thread is a sibling tree so a Chat
turn cannot append to the Build transcript (docs/workbench/chat.md).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_META_LOCK = threading.Lock()
_ID_LOCK = threading.Lock()
_last_id_ms = 0
_TITLE_MAX = 60

_KIND_SUFFIX = {
    ".png": "chart",
    ".table.json": "table",
    ".sql": "query",
    ".md": "note",
}


def safe_id(value: str, what: str) -> str:
    """`value` if it can only ever name one path segment, else ValueError.

    Every id here is minted by `new_id` and could not climb out of anything. They do not stay that
    way: a conversation id arrives in a POST body (`/api/project/build/stream`) and a thread id in a
    URL, and from there they are joined onto a root and written through `mkdir(parents=True)`. A
    `..` segment then walks out of the project volume and takes the write with it.

    Deliberately a whitelist rather than a `..` check. `..` is one way out; an absolute path is
    another, and on the way to a filename a `/` is a third. What the callers actually need is the
    much smaller promise that this is one segment, which is all a minted id has ever been.

    Same rule `_plan_doc_dir` has enforced since plan documents started arriving in URLs — this is
    that rule, moved somewhere the other two callers can reach it."""
    if not re.fullmatch(r"[0-9a-zA-Z_-]{1,64}", value or ""):
        raise ValueError(f"bad {what}: {value!r}")
    return value


def new_id(prefix: str) -> str:
    """Sortable id (`thr_` / `art_` / `ctx_`) without a ULID dependency: epoch-ms + random.

    Strictly increasing inside the process, because the random half is not an order. Two Threads
    made in the same millisecond would otherwise sort by coin flip, and which of them is the
    OLDEST decides who inherits an upgraded Project's untagged Build history."""
    global _last_id_ms
    with _ID_LOCK:
        ms = max(int(time.time() * 1000), _last_id_ms + 1)
        _last_id_ms = ms
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


def handoff_unresolved(row: dict | None) -> bool:
    """True while a handoff entry is still waiting on the person (docs/workbench/handoff.md §6).

    `suggested` and `planned` are unresolved. `bound` is a step finishing — the app exists, and the
    Thread is free to hand off again. `suppressed` is the person saying stop, which ends this entry
    too; what makes that answer permanent is that the entry stays on the record."""
    if not row:
        return False
    status = str(row.get("status") or "")
    if status:
        return status in ("suggested", "planned")
    # Written before entries carried a status: a date is a suggestion that was made.
    return bool(row.get("suggestedAt"))


def _is_live(row: dict | None) -> bool:
    """A Thread record that is readable and not a tombstone. See `ThreadStore.delete`."""
    return row is not None and not row.get("deleted")


def _is_auto_artifact(item: dict) -> bool:
    """Sage-produced charts/tables are Thread outputs, not user pins."""
    if str(item.get("kind") or "") != "artifact":
        return False
    return str(item.get("addedBy") or "sage") != "user"


class ThreadStore:
    def __init__(self, workspace: Path) -> None:
        self._root = Path(workspace)

    @property
    def _legacy_index_path(self) -> Path:
        """The single `threads.json` this store was built on, kept only long enough to read it
        once. See `_adopt_legacy_index`."""
        return self._root / ".sage" / "threads.json"

    def thread_dir(self, thread_id: str) -> Path:
        return self._root / ".sage" / "threads" / safe_id(thread_id, "thread id")

    def meta_path(self, thread_id: str) -> Path:
        return self.thread_dir(thread_id) / "meta.json"

    def examples_dir(self, thread_id: str) -> Path:
        return self._root / "examples" / safe_id(thread_id, "thread id")

    def list(self) -> list[dict]:
        """Every Thread in the Project, newest activity first.

        A scan, not an index: two viewers in one Project are two Sage Builders and two Builders
        are two processes, so a shared list rewritten whole means the second one to write drops
        the first one's Thread while its history sits on disk (ADR-0008). Each Thread's record is
        written only by the Builder that owns it, and no two of them are the same file."""
        self._adopt_legacy_index()
        rows = [r for r in (self._read_meta(d.name) for d in self._thread_dirs()) if _is_live(r)]
        rows.sort(key=lambda r: (str(r.get("updatedAt") or ""), str(r.get("id") or "")), reverse=True)
        return rows

    def get(self, thread_id: str) -> dict | None:
        self._adopt_legacy_index()
        row = self._read_meta(thread_id)
        return row if _is_live(row) else None

    def create(self, title: str = "") -> dict:
        now = _now()
        row = {
            "id": new_id("thr"),
            "title": title or "New conversation",
            "createdAt": now,
            "updatedAt": now,
            "pinned": False,
        }
        self.thread_dir(row["id"]).mkdir(parents=True, exist_ok=True)
        self._write_json(self.meta_path(row["id"]), row)
        self.examples_dir(row["id"]).mkdir(parents=True, exist_ok=True)
        self.write_context(row["id"], {"items": []})
        self._write_json(self.thread_dir(row["id"]) / "artifacts.json", {"items": []})
        return row

    def touch(self, thread_id: str, *, title: str | None = None) -> dict | None:
        def edit(row: dict) -> None:
            row["updatedAt"] = _now()
            if title is not None:
                row["title"] = title

        return self._edit_meta(thread_id, edit)

    def read_session_id(self, thread_id: str) -> str | None:
        rec = self.read_session(thread_id)
        return rec.get("session_id") if rec else None

    def read_session(self, thread_id: str) -> dict | None:
        p = self.thread_dir(thread_id) / "session.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def write_session_id(self, thread_id: str, session_id: str, directory: str | None = None) -> None:
        p = self.thread_dir(thread_id) / "session.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        body: dict[str, str] = {"session_id": session_id}
        if directory:
            body["directory"] = directory
        p.write_text(json.dumps(body))

    def history_path(self, thread_id: str) -> Path:
        return self.thread_dir(thread_id) / "history.jsonl"

    def append_history(self, thread_id: str, entry: dict) -> None:
        """Write one event, stamped with the time it was written.

        The stamp is applied here rather than passed in, for the same reason the Build writer
        stamps its own entries (Workspace.append_history): a conversation's transcript is these
        two logs merged, and one order needs one clock and one format. Left to the call sites it
        would be neither."""
        p = self.history_path(thread_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps({**entry, "at": _now()}) + "\n")

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
        if not isinstance(items, list):
            return {"items": []}
        kept = [i for i in items if not _is_auto_artifact(i)]
        if len(kept) != len(items):
            self.write_context(thread_id, {"items": kept})
        return {"items": kept}

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
        def edit(row: dict) -> None:
            if title is not None:
                row["title"] = title
            if pinned is not None:
                row["pinned"] = bool(pinned)
            row["updatedAt"] = _now()

        return self._edit_meta(thread_id, edit)

    def delete(self, thread_id: str) -> bool:
        """A tombstone, not a removal. The old delete dropped the index row and left
        `threads/<id>/` on disk with its history, so a scan that trusted the directory alone would
        put the Thread back in the rail. Marking the record keeps that history exactly where it
        was and still answers False the second time."""
        def edit(row: dict) -> None:
            row["deleted"] = True
            row["updatedAt"] = _now()

        return self._edit_meta(thread_id, edit) is not None

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
        return row

    def read_handoffs(self, thread_id: str) -> list[dict]:
        """Every handoff this Thread has made, oldest first.

        A list because a Thread may hand off more than once, to a different Built App each time
        (docs/workbench/handoff.md §6, ADR-0008). A file written before that is one bare object,
        and it is read as the single entry it always was."""
        p = self.thread_dir(thread_id) / "handoff.json"
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, dict):
            return []
        items = data.get("items")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
        return [data] if data else []

    def read_handoff(self, thread_id: str) -> dict | None:
        """The newest handoff: the one the Chat pane is showing and the sheet is about."""
        entries = self.read_handoffs(thread_id)
        return entries[-1] if entries else None

    def mark_handoff_suggested(self, thread_id: str) -> dict:
        entries = self.read_handoffs(thread_id)
        row = entries[-1] if entries else None
        if handoff_unresolved(row):
            return row
        row = {"suggestedAt": _now(), "suppressed": False, "status": "suggested"}
        entries.append(row)
        self._write_handoffs(thread_id, entries)
        return row

    def suppress_handoff(self, thread_id: str) -> dict:
        entries = self.read_handoffs(thread_id)
        row = entries[-1] if entries else None
        if row is not None and row.get("status") == "suppressed":
            return row
        if not handoff_unresolved(row):
            row = {"suggestedAt": _now()}
            entries.append(row)
        row["suppressed"] = True
        row["status"] = "suppressed"
        self._write_handoffs(thread_id, entries)
        return row

    def mark_handoff_planned(self, thread_id: str, plan_id: str = "") -> dict:
        entries = self.read_handoffs(thread_id)
        row = entries[-1] if entries else None
        # A finished handoff — bound, or declined — is not the one this plan belongs to. The next
        # plan is the next handoff and gets its own entry, so neither the app the last one built nor
        # the answer the person gave is written over.
        if not handoff_unresolved(row):
            row = {"suggestedAt": _now()}
            entries.append(row)
        row["suppressed"] = False
        row["status"] = "planned"
        row["planPath"] = ".sage/plan.md"
        # The plan document this handoff drafted. planPath above is the transient copy the builder
        # reads; this one still resolves after a build has archived that copy.
        if plan_id:
            row["planId"] = plan_id
        self._write_handoffs(thread_id, entries)
        return row

    def mark_handoff_bound(self, thread_id: str, app_id: str = "") -> dict:
        """Confirm the newest handoff, naming the Built App it made.

        Deliberately not `mark_handoff_planned` first: a re-confirm of the same sheet is one
        handoff, and re-planning a bound entry would open a second one — a second app nobody
        asked for."""
        entries = self.read_handoffs(thread_id)
        row = entries[-1] if entries else None
        if row is None or row.get("status") == "suppressed":
            row = {"suggestedAt": _now()}
            entries.append(row)
        row["suppressed"] = False
        row["status"] = "bound"
        row["boundAt"] = _now()
        row.setdefault("planPath", ".sage/plan.md")
        # Each entry carries its own app, because the next handoff from this Thread builds another.
        if app_id:
            row["appId"] = app_id
        self._write_handoffs(thread_id, entries)
        return row

    def _write_handoffs(self, thread_id: str, entries: list[dict]) -> None:
        # `{"items": [...]}`, the shape `artifacts.json` and `context.json` next to it already use.
        # The record is the list; the object around it is how this directory holds a list.
        self._write_json(self.thread_dir(thread_id) / "handoff.json", {"items": entries})

    def _thread_dirs(self) -> list[Path]:
        root = self._root / ".sage" / "threads"
        if not root.is_dir():
            return []
        return [d for d in root.iterdir() if d.is_dir()]

    def _read_meta(self, thread_id: str) -> dict | None:
        p = self.meta_path(thread_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def _edit_meta(self, thread_id: str, edit: Callable[[dict], None]) -> dict | None:
        """Read-modify-write of ONE Thread's record. The lock orders this Builder's own threads.
        A second Builder renaming a DIFFERENT Thread at the same time writes a different file and
        cannot cost this one anything, which is the whole point of the record being per Thread."""
        self._adopt_legacy_index()
        with _META_LOCK:
            row = self._read_meta(thread_id)
            if not _is_live(row):
                return None
            edit(row)
            self._write_json(self.meta_path(thread_id), row)
            return row

    def _adopt_legacy_index(self) -> None:
        """Move a Project written before ADR-0008 onto one record per Thread, once.

        Titles, pins and times come across as they were. A directory with no row is a Thread the
        old delete removed from the index and left on disk; it gets a tombstone so the scan does
        not resurrect it. Every record is written before the index goes, so a second Builder
        racing this one either reads the index and writes the same bytes, or finds it gone and
        finds every record already there."""
        if not self._legacy_index_path.exists():
            return
        with _META_LOCK:
            if not self._legacy_index_path.exists():
                return
            self._migrate_index_rows()

    def _migrate_index_rows(self) -> None:
        # An index that will not read is NOT an empty index. The old `_write_index` truncated the
        # file in place with two Builders racing it, so a half-written read is the very failure
        # this change exists to end — and reading it as "no Threads" would tombstone every one of
        # them below and then delete the only evidence. Leave it and read it again next time.
        try:
            rows = json.loads(self._legacy_index_path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(rows, list):
            return
        in_legacy_index: set[str] = set()
        for row in rows:
            thread_id = str(row.get("id") or "") if isinstance(row, dict) else ""
            if not thread_id:
                continue
            in_legacy_index.add(thread_id)
            if not self.meta_path(thread_id).exists():
                self._write_json(self.meta_path(thread_id), row)
        for d in self._thread_dirs():
            if d.name not in in_legacy_index and not self.meta_path(d.name).exists():
                self._write_json(self.meta_path(d.name), {"id": d.name, "deleted": True})
        self._legacy_index_path.unlink(missing_ok=True)

    @staticmethod
    def _write_json(path: Path, body: Any) -> None:
        """Written whole or not at all: another Builder scanning the tree reads these files while
        this one writes them, and a truncated read is a Thread that disappears for one poll."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps(body, indent=2))
        os.replace(tmp, path)


_SKIP_SNAPSHOT_PARTS = frozenset({"node_modules", ".git", "dist", "__pycache__", "chat-work"})
# Where attached Dataset files live, and the one tree whose bytes are not ours to read. A mounted
# Dataset is attached as a SYMLINK into /mnt/data, so snapshotting the tree pulled the whole file
# across the mount — before the turn and again after it, on every Chat turn, for a file the turn
# may never touch. A prefix, not a bare part name: `data` alone would also skip `src/data/`, which
# IS the agent's to write and must stay revertible.
#
# What this gives up: a write that lands under public/data is no longer undone. It is a narrow loss
# — the tree is gitignored, so nothing there reaches the repo — but a write THROUGH one of those
# symlinks reaches the Dataset, and reverting it used to write the old bytes back over the mount.
# Refusing that write at the tool boundary is the right place for it; undoing it here never was.
#
# `.sage/scratch/` is the same tree under another name. Chat fetches a Dataset file there to answer
# a question about it (see fetch_dataset_file_for_chat), and a person's upload lands there too — so
# skipping only public/data left the whole transactions CSV being read across the mount twice per
# turn, which is the cost this list exists to avoid. Worse, neither is a path a Chat turn may write,
# so revert_denied_writes treated both as denied writes: a fetch or an upload the `before` snapshot
# had not managed to read was UNLINKED at the end of the turn, taking the file out of the rail while
# the chip that named it stayed. Nothing here is the agent's, and nothing here reaches git.
# `apps/` is the third tree skipped, and for a plainer reason than the other two: the Built Apps
# live there (ADR-0008), a Chat turn never writes one, and reverting one is not Chat's to do.
# Without this line every Chat turn would read the whole app tree, twice.
_SKIP_SNAPSHOT_PREFIXES = ("public/data/", ".sage/scratch/", "apps/")

CHAT_WORK = Path(".sage") / "chat-work"


def ensure_chat_workdir(workspace: Path, agents_md: str, data_dir: Path | None = None) -> Path:
    """OpenCode directory for sage-chat: Chat AGENTS.md plus links into examples/, scratch and data.

    Chat must not use the Built App's directory as cwd. Paths in the prompt stay workspace-shaped
    (`examples/<threadId>/…`, `.sage/scratch/…`, `public/data/<slug>/…`) because those names are
    linked in here. `workspace` is the Project root, where Chat's own trees live; `data_dir` is the
    app's `public/data/`, which is what an attached Dataset file is named by — the context line
    and the attachment descriptor both hand the agent that path, and without the link it resolves
    to nothing from this cwd, so a file the person can see in the rail cannot be read at all.
    Only `data` is linked, not the whole of `public/`: the rest of it belongs to the app, which
    Chat has no business reading. None means there is no app yet, so there is nothing to link.
    """
    root = Path(workspace) / CHAT_WORK
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(agents_md)
    (Path(workspace) / "examples").mkdir(exist_ok=True)
    (Path(workspace) / ".sage" / "scratch").mkdir(parents=True, exist_ok=True)
    _ensure_dir_link(root / "examples", Path(workspace) / "examples")
    sage = root / ".sage"
    sage.mkdir(exist_ok=True)
    _ensure_dir_link(sage / "scratch", Path(workspace) / ".sage" / "scratch")
    public = root / "public"
    public.mkdir(exist_ok=True)
    if data_dir is not None:
        _ensure_dir_link(public / "data", data_dir)
    return root


def _ensure_dir_link(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        return
    link.symlink_to(Path(os.path.relpath(target, link.parent)), target_is_directory=True)


def snapshot_files(root: Path) -> dict[str, bytes]:
    """Workspace-relative file bytes, skipping heavy/generated trees and attached data. Used to
    revert Chat writes that landed outside the allowlist.

    `p.is_file()` follows symlinks, so what is skipped here is skipped before anything is read —
    which is the point for public/data, where the file on the other end of the link is a Dataset."""
    out: dict[str, bytes] = {}
    root = Path(root)
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(_SKIP_SNAPSHOT_PREFIXES):
            continue
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
