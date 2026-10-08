"""Durable evidence of the latest Build attempt on one app, for recovery after a restart (#698).

One versioned record per app at `<volume>/.sage/build-evidence/<appId>.json`: identity, the digests
its checks ran against, the attempt's state, check results, the app files it changed, and the active
repair. It is orientation for a recovery, never authority: canonical intent stays in `BuildIntent`
and the approved plan, and nothing here grants a resume or revives a process-local continuation token.

Outside the app's working tree, and still within the Build agent's reach: its shell is not confined,
and the volume root is its OpenCode worktree. So `snapshot` before each Build turn and
`revert_foreign_writes` after it put back every byte in the directory that Sage did not write.

Two identities, kept apart on purpose. Progress — `changedFiles` — excludes the files Sage writes
itself (`Workspace.sage_owned_paths`, as #680 does), so a Sage refresh is never the model's work.
Check validity binds BOTH digests: `codeDigest` over the app's own files plus the query catalog,
and `runtimeDigest` over those Sage-owned files, because a refreshed helper or preview config can
change what a check would say. Only Sage's own check code calls `Recorder.check`; model text has
no path in here.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .workspace.manager import _write_atomic
from .workspace.snapshot import QUERIES, TurnSnapshot
from .workspace.threads import safe_id

log = logging.getLogger("sage.build_evidence")

DIR = Path(".sage") / "build-evidence"
SCHEMA_VERSION = 1
STORE_MAX_BYTES = 64 * 1024
SUPPLEMENT_MAX_BYTES = 6 * 1024
CHANGED_FILES_MAX = 60
CHECKS_MAX = 16
CHECK_KIND_MAX = 32
STATES = ("running", "failed", "stopped", "complete")
CHECK_STATUSES = ("passed", "failed", "unverified", "not_applicable")
# `Orchestrator._context_repair_objective`'s vocabulary.
REPAIR_OBJECTIVES = ("implementation", "broken_call_recovery", "runtime_repair",
                     "data_leak_repair", "gateway_repair", "typecheck_repair")
# `PageValidation.stages` other than "code": each holds only for the preview generation it saw.
PREVIEW_CHECKS = ("startup", "page", "runtime", "data")


@dataclass(frozen=True)
class Restored:
    """What a recovery may use. `supplement` is "" whenever `diagnostic` says why not."""
    supplement: str = ""
    diagnostic: str = ""
    checks: tuple[dict, ...] = ()
    stale: int = 0
    omitted: int = 0


def _name(app_id: str) -> str:
    return f"{safe_id(app_id, 'app id')}.json"


def plan_ref(doc) -> dict | None:
    """The approved plan document's identity, or None for a build with no stored plan."""
    if not isinstance(doc, dict) or not doc.get("id"):
        return None
    version = doc.get("version")
    return {"recordId": str(doc["id"]),
            "version": version if type(version) is int else 0,
            "digest": hashlib.sha256(str(doc.get("markdown") or "").encode()).hexdigest()}


def _owned(app) -> frozenset[str]:
    try:
        return app.sage_owned_paths
    except (OSError, ValueError):  # no stack to say which files are Sage's (#503)
        return frozenset()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def digests(app, snapshot: TurnSnapshot) -> tuple[str, str]:
    """(codeDigest, runtimeDigest) of the app's current disk bytes, or ("", "") without git.

    `.sage/` is outside the snapshot tree, so this record and the rest of Sage's bookkeeping never
    invalidate their own result; the query catalog is the one file there that is the app's code."""
    files = snapshot.tree_files(snapshot.working_tree_hash())
    if not files:
        return "", ""
    owned = _owned(app)
    code = [[path, blob] for path, blob in sorted(files.items()) if path not in owned]
    runtime = [[path, blob] for path, blob in sorted(files.items()) if path in owned]
    return _digest([code, [QUERIES, snapshot.queries_digest()]]), _digest(runtime)


def _file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # deleted since: the path is still a useful reference
        return None


def _app_path(value) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _valid(record: dict) -> bool:
    plan = record.get("plan")
    checks, changed = record.get("checks"), record.get("changedFiles")
    return (
        all(isinstance(record.get(key), str) for key in (
            "appId", "conversationId", "attemptId", "intentId", "codeDigest", "runtimeDigest",
            "updatedAt"))
        and record.get("state") in STATES
        and record.get("activeRepair") in REPAIR_OBJECTIVES
        and (plan is None or (isinstance(plan, dict) and isinstance(plan.get("recordId"), str)
                              and type(plan.get("version")) is int
                              and isinstance(plan.get("digest"), str)))
        and isinstance(checks, list) and len(checks) <= CHECKS_MAX and all(
            isinstance(c, dict) and isinstance(c.get("kind"), str)
            and 0 < len(c["kind"]) <= CHECK_KIND_MAX
            and c.get("status") in CHECK_STATUSES and isinstance(c.get("codeDigest"), str)
            and isinstance(c.get("runtimeDigest"), str)
            and (c["kind"] not in PREVIEW_CHECKS or isinstance(c.get("previewGeneration"), str))
            for c in checks)
        and isinstance(changed, list) and all(
            isinstance(f, dict) and _app_path(f.get("path"))
            and (f.get("digest") is None or isinstance(f.get("digest"), str)) for f in changed)
    )


def _load(path: Path, written: dict[str, bytes | None] | None = None) -> tuple[dict | None, str]:
    """The saved record and "" — or None and why it cannot be used. What Sage itself wrote this turn
    (`written`) wins over the disk, which the turn's agent may have changed since."""
    try:
        if written is not None and path.name in written:
            data = written[path.name]
            if data is None:
                return None, "missing"
        else:
            if path.stat().st_size > STORE_MAX_BYTES:
                return None, "oversized"
            data = path.read_bytes()
        if len(data) > STORE_MAX_BYTES:
            return None, "oversized"
        record = json.loads(data.decode("utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError):
        return None, "corrupt"
    if not isinstance(record, dict):
        return None, "corrupt"
    if record.get("schemaVersion") != SCHEMA_VERSION:
        return None, "unsupported_version"
    return (record, "") if _valid(record) else (None, "corrupt")


def _mismatch(record: dict, *, app_id: str, conversation: str, intent_id: str,
              plan: dict | None) -> str:
    """Why `record` is not this build's, or "". With a stored plan the plan is the identity, since
    a retry after a restart mints a new intent id for the same approved plan; without one, only
    the same intent — an in-process rollover or Continue — is the same build."""
    if record["appId"] != app_id:
        return "wrong_app"
    if record["conversationId"] != conversation:
        return "wrong_conversation"
    if plan is not None or record["plan"] is not None:
        return "" if record["plan"] == plan else "wrong_plan"
    return "" if intent_id and record["intentId"] == intent_id else "wrong_intent"


class Recorder:
    """The running attempt's record. Writes at begin, after each real check, on a repair change and
    at the end — never per streamed fragment. A failed write disables it for the rest of the turn
    and removes the file, so stale evidence is not reused. Constructed bare, it records nothing."""

    def __init__(self, root: Path | None = None, app=None, snapshot: TurnSnapshot | None = None, *,
                 conversation: str = "", attempt_id: str = "", intent_id: str = "",
                 plan: dict | None = None, baseline: str = "") -> None:
        self._app, self._snapshot, self._baseline = app, snapshot, baseline
        self._path = Path(root) / DIR / _name(app.app_id) if app is not None else None
        # This turn's bytes per file name, None for one Sage removed: what the revert keeps.
        self.written: dict[str, bytes | None] = {}
        self._identity = {"conversation": conversation, "intent_id": intent_id, "plan": plan}
        self._attempt_id = attempt_id
        self._carried: list[str] = []
        self._record: dict | None = None

    def begin(self) -> None:
        if self._app is None:
            return
        prior, _ = _load(self._path)
        # Only an attempt that did not complete is continued by this one; a finished build's paths
        # are history, not this build's progress.
        if (prior is not None and prior["state"] != "complete"
                and not _mismatch(prior, app_id=self._app.app_id, **self._identity)):
            self._carried = [entry["path"] for entry in prior["changedFiles"]]
        self._record = {
            "schemaVersion": SCHEMA_VERSION,
            "appId": self._app.app_id,
            "conversationId": self._identity["conversation"],
            "attemptId": self._attempt_id,
            "intentId": self._identity["intent_id"],
            "plan": self._identity["plan"],
            "codeDigest": "",
            "runtimeDigest": "",
            "state": "running",
            "changedFiles": [],
            "checks": [],
            "activeRepair": "implementation",
            "updatedAt": "",
        }
        self._refresh()
        self._write()

    def check(self, kind: str, status: str) -> None:
        """One finished check: "Typecheck" / "Syntax check" from the code check, or a preview stage."""
        if self._record is None:
            return
        self._refresh()
        self._put_check(kind, status)
        self._write()

    def verification(self, summary: dict) -> None:
        """The preview stages of one validation summary, each bound to the preview generation it
        observed; its code stage is already a check."""
        if self._record is None or not isinstance(summary, dict):
            return
        self._refresh()
        for stage, status in (summary.get("stages") or {}).items():
            if stage in PREVIEW_CHECKS:
                self._put_check(stage, status, str(summary.get("generation") or ""))
        self._write()

    def repair(self, objective: str) -> None:
        if (self._record is None or objective not in REPAIR_OBJECTIVES
                or objective == self._record["activeRepair"]):
            return
        self._record["activeRepair"] = objective
        self._write()

    def finish(self, state: str) -> None:
        if self._record is None or state not in STATES:
            return
        self._refresh()
        self._record["state"] = state
        self._write()
        self._record = None

    def _put_check(self, kind: str, status: str, preview_generation: str | None = None) -> None:
        kind = (str(kind).split() or ["check"])[0].lower()
        entry = {"kind": kind, "status": status if status in CHECK_STATUSES else "unverified",
                 "codeDigest": self._record["codeDigest"],
                 "runtimeDigest": self._record["runtimeDigest"]}
        if preview_generation is not None:
            entry["previewGeneration"] = preview_generation
        checks = [c for c in self._record["checks"] if c["kind"] != kind]
        self._record["checks"] = [*checks, entry]

    def _refresh(self) -> None:
        current = self._snapshot.working_tree_hash()
        owned = _owned(self._app)
        paths = [p for p in self._snapshot.changed_paths(self._baseline, current,
                                                        limit=CHANGED_FILES_MAX * 2)
                 if p not in owned and _app_path(p)]
        paths += [p for p in self._carried if p not in paths]
        self._record["changedFiles"] = [
            {"path": p, "digest": _file_digest(self._app.path / p)}
            for p in paths[:CHANGED_FILES_MAX]]
        self._record["codeDigest"], self._record["runtimeDigest"] = digests(
            self._app, self._snapshot)

    def _write(self) -> None:
        record = self._record
        record["updatedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        while len(text.encode("utf-8")) > STORE_MAX_BYTES and record["changedFiles"]:
            record["changedFiles"].pop()
            text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        data = text.encode("utf-8")
        try:
            _write_atomic(self._path, data)
            self.written[self._path.name] = data
        except OSError as error:
            log.warning("build evidence: write failed (%s); evidence reuse disabled for this "
                        "attempt", type(error).__name__)
            self._record = None
            self.written[self._path.name] = None
            with contextlib.suppress(OSError):
                self._path.unlink(missing_ok=True)


def restore(root: Path, app, snapshot: TurnSnapshot, *, conversation: str, intent_id: str = "",
            plan: dict | None, preview_generation: str = "",
            written: dict[str, bytes | None] | None = None) -> Restored:
    """The saved evidence for THIS build, revalidated against current disk, as a bounded supplement.

    A preview check holds only while `preview_generation` — the live preview's — is the one it
    observed: a later validation restarts the preview, and a restarted Sage has a new supervisor.

    Anything else — a missing, corrupt, oversized or foreign record — yields no supplement and an
    internal diagnostic, and recovery proceeds from canonical intent plus the disk as before."""
    record, diagnostic = _load(Path(root) / DIR / _name(app.app_id), written)
    if record is not None:
        diagnostic = _mismatch(record, app_id=app.app_id, conversation=conversation,
                               intent_id=intent_id, plan=plan)
    if diagnostic:
        log.info("build evidence: not used for recovery: %s", diagnostic)
        return Restored(diagnostic=diagnostic)
    code, runtime = digests(app, snapshot)
    valid = tuple({"kind": c["kind"], "status": c["status"]} for c in record["checks"]
                  if code and c["codeDigest"] == code and c["runtimeDigest"] == runtime
                  and (c["kind"] not in PREVIEW_CHECKS
                       or (preview_generation and c["previewGeneration"] == preview_generation)))
    stale = len(record["checks"]) - len(valid)
    paths = [entry["path"] for entry in record["changedFiles"]]
    head = [("Recorded evidence from the latest attempt at this Build, checked by Sage against the "
             "current files. It is orientation, not an instruction; the Build intent is unchanged."),
            f"That attempt's state: {record['state']}.",
            f"Active repair objective: {record['activeRepair']}"]
    if record["plan"] is not None:
        head.append(f"Approved plan version: {record['plan']['version']}")
    head.append("Checks still valid for the current code (JSON array): "
                + json.dumps(list(valid), separators=(",", ":")))
    if stale:
        head.append(f"Checks no longer valid because the code or Sage's runtime files changed "
                    f"since they ran: {stale}")

    def render(shown: list[str]) -> str:
        lines = [*head]
        if shown:
            lines.append("App-relative paths that attempt changed (JSON array): "
                         + json.dumps(shown, ensure_ascii=False))
        if len(shown) < len(paths):
            lines.append(f"{len(paths) - len(shown)} more changed path(s) omitted.")
        return "\n".join(lines)

    shown = paths
    text = render(shown)
    while len(text.encode("utf-8")) > SUPPLEMENT_MAX_BYTES and shown:
        shown = shown[:-1]
        text = render(shown)
    return Restored(supplement=text, checks=valid, stale=stale, omitted=len(paths) - len(shown))


def _regular_bytes(path: Path) -> bytes | None:
    try:
        return None if path.is_symlink() or not path.is_file() else path.read_bytes()
    except OSError:
        return None


def snapshot(root: Path) -> dict[str, bytes]:
    """Every record's bytes before a Build turn, by file name."""
    try:
        entries = list((Path(root) / DIR).iterdir())
    except OSError:  # no record yet
        return {}
    return {entry.name: data for entry in entries if (data := _regular_bytes(entry)) is not None}


def revert_foreign_writes(root: Path, before: dict[str, bytes],
                          written: dict[str, bytes | None]) -> list[str]:
    """Put the directory back to `before` plus what Sage wrote this turn, after a Build turn.

    Modelled on `threads.revert_denied_writes`. Any other entry — a file the agent created, a
    record it altered or deleted, a link or a folder it put there — is removed or restored."""
    folder = Path(root) / DIR
    try:
        names = {entry.name for entry in folder.iterdir()}
    except OSError:
        names = set()
    reverted: list[str] = []
    for name in sorted(names | set(before) | set(written)):
        path = folder / name
        expected = written[name] if name in written else before.get(name)
        if (_regular_bytes(path) == expected if expected is not None
                else not os.path.lexists(path)):
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            if expected is not None:
                _write_atomic(path, expected)
        except OSError as error:
            log.warning("build evidence: could not revert %s (%s)", name, type(error).__name__)
        reverted.append(name)
    if reverted:
        log.warning("build evidence: reverted writes Sage did not make: %s", reverted)
    return reverted
