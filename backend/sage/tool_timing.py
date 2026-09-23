"""Bounded tool observations. No extra I/O, arguments or content in diagnostic records."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import posixpath
import secrets
import time

MAX_TOOLS = 500
MAX_INTERVALS = 2000
MAX_ARGUMENT_KEYS = 16
_READS = {"read", "glob", "grep", "list", "live_read_files", "live_read_table"}
_EDITS = {"edit", "write"}
_READ_ONLY = _READS | {"todoread", "todowrite"}
_SHELLS = {"bash", "shell", "sh", "run", "run_command", "execute", "exec", "terminal"}
_COMMAND_KEYS = ("command", "cmd", "code")


def harness_times(part: dict, *, event_type: str = "") -> dict:
    """Both observed OpenCode shapes, preserving the origin of each wall timestamp."""
    out = {}
    for container, keys, source in (((part.get("state") or {}).get("time") or {},
                                      ("start", "end"), "opencode.state.time"),
                                  (part.get("time") or {}, ("ran", "completed"), "opencode.part.time")):
        for name, key in zip(("start", "end"), keys):
            value = container.get(key)
            if name not in out and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                out[name] = value
                out[name + "Source"] = source + "." + key
    name = {"session.next.tool.called": "start", "session.next.tool.success": "end",
            "session.next.tool.failed": "end"}.get(event_type)
    timestamp = part.get("timestamp")
    if name and isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and math.isfinite(timestamp):
        out[name] = timestamp
        out[name + "Source"] = "opencode." + event_type + ".timestamp"
    return {"harness_time": out} if out else {}


class ToolObserver:
    def __init__(self, record, lock):
        self.record = record
        self.lock = lock
        self._runs = {}
        self._salt = secrets.token_bytes(32)
        self._last_read = {}
        self._edits = {}
        self._opaque = 0
        self._sequence = 0
        self._variants: dict[str, dict[str, int]] = {"executable": {}, "metadata": {}}
        self._brake_metadata: dict[str, list[int]] = {}

    def _fingerprint(self, value) -> str:
        return hmac.new(self._salt, json.dumps(value, sort_keys=True).encode(), hashlib.sha256).hexdigest()

    def _variant(self, kind: str, value) -> int:
        key = self._fingerprint(value)
        variants = self._variants[kind]
        if key not in variants:
            variants[key] = len(variants) + 1
        return variants[key]

    def part(self, session: str, part: dict, *, directory: str | None = None) -> None:
        state = part.get("state") or {}
        self.event(session, {"call_id": part.get("callID"), "part_id": part.get("id"),
                             "tool": part.get("tool") or part.get("name"),
                             "input": state.get("input"), "status": state.get("status"),
                             **harness_times(part)}, directory=directory)

    def event(self, session: str, payload: dict, *, directory: str | None = None) -> None:
        rec = self.record
        if rec is None:
            return
        with self.lock:
            if rec.t1 is not None:
                return
            call_id = str(payload.get("call_id") or "")
            part_id = str(payload.get("part_id") or "")
            key = (session, "call" if call_id else "part", call_id or part_id)
            if not key[2]:
                rec.counters["tools.unidentified_events"] = rec.counters.get("tools.unidentified_events", 0) + 1
                return
            now = (time.monotonic() - rec.t0) * 1000
            run = self._runs.get(key)
            if run is None:
                if len(rec.tools) >= MAX_TOOLS:
                    rec.tools_truncated = True
                    return
                run = {"sessionId": session, "harnessCallId": call_id or None,
                       "partId": part_id or None, "identitySource": key[1], "tool": "unknown",
                       "firstObservedMs": now, "lastObservedMs": now, "completedObservedMs": None,
                       "observationSource": "sage.consumer.monotonic",
                       "startUnixMs": None, "endUnixMs": None, "startSource": None, "endSource": None,
                       "executionMs": None, "completionLagMs": None,
                       "targetFingerprint": None, "queryFingerprint": None, "range": {},
                       "targetMetadataFinal": False,
                       "editSincePreviousRead": None, "opaqueOperationSincePreviousRead": None,
                       "targetState": "unknown", "status": "unknown"}
                self._runs[key] = run
                rec.tools.append(run)
            run["lastObservedMs"] = now
            if payload.get("tool"):
                run["tool"] = str(payload["tool"])[:80]
            for name in ("start", "end"):
                observed = (payload.get("harness_time") or {}).get(name)
                if isinstance(observed, (int, float)) and math.isfinite(observed):
                    run[name + "UnixMs"] = observed
                    run[name + "Source"] = ((payload.get("harness_time") or {}).get(name + "Source")
                                             or run[name + "Source"] or "opencode")
            status = {"called": "running", "success": "completed", "failed": "error"}.get(
                payload.get("status"), payload.get("status"))
            args = payload.get("input")
            # Ignore stale running input after completion; a completed transcript
            # can still fill metadata absent from the stream completion.
            if isinstance(args, dict) and (run["completedObservedMs"] is None or status in {"completed", "error"}):
                path = args.get("filePath", args.get("path"))
                if isinstance(path, str) and path and run["tool"] in _READS | _EDITS:
                    target = self._fingerprint(posixpath.normpath(posixpath.join(directory, path))
                                               if directory else [session, posixpath.normpath(path)])
                    run["targetFingerprint"] = target
                    run["range"] = {}
                    for name in ("offset", "limit", "startLine", "endLine"):
                        value = args.get(name)
                        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**9:
                            run["range"][name] = value
                pattern = args.get("pattern")
                if isinstance(pattern, str) and run["tool"] in {"grep", "glob"}:
                    run["queryFingerprint"] = self._fingerprint(pattern)
            # A repeated old running snapshot cannot reopen a completed call.
            if status in {"pending", "running", "in_progress", "completed", "error"} and run["completedObservedMs"] is None:
                run["status"] = status
            if status in {"completed", "error"}:
                run["targetMetadataFinal"] = run["targetFingerprint"] is not None
            if status in {"completed", "error"} and run["completedObservedMs"] is None:
                run["completedObservedMs"] = now
                self._sequence += 1
                target = run["targetFingerprint"]
                if target and run["tool"] in _READS:
                    previous = self._last_read.get(target)
                    if previous is not None:
                        run["opaqueOperationSincePreviousRead"] = self._opaque > previous[1]
                    if previous is not None and self._edits.get(target, 0) > previous[0]:
                        run["editSincePreviousRead"] = True
                        run["targetState"] = "observed_edit"
                    # No verified file revision exists on these events. No observed write
                    # still leaves unchanged content unknown, including after opaque shells.
                    self._last_read[target] = (self._sequence, self._opaque)
                if status == "completed" and run["tool"] in _EDITS and run["targetFingerprint"]:
                    self._edits[run["targetFingerprint"]] = self._sequence
                elif run["tool"] not in _READ_ONLY:
                    self._opaque += 1
            start, end = run["startUnixMs"], run["endUnixMs"]
            if start is not None and end is not None and end >= start:
                run["executionMs"] = end - start
            if end is not None and run["completedObservedMs"] is not None:
                # Translate the recorded observation, not the latest repeated snapshot.
                observed_wall = rec.started_at * 1000 + run["completedObservedMs"]
                run["completionLagMs"] = observed_wall - end if observed_wall >= end else None

    def brake(self, *, session_id: str, call_id: str, tool: str, fingerprint: str,
              consecutive: int, limit: int, stopped: bool, arguments=None) -> None:
        rec = self.record
        if rec is None:
            return
        with self.lock:
            if rec.t1 is not None:
                return
            if len(rec.repeat_brake) >= MAX_TOOLS:
                rec.repeat_brake_truncated = True
                return
            if rec.t1 is None:
                started = self._runs.get((session_id, "call", call_id), {})
                name = str(tool or started.get("tool", "unknown"))[:80]
                row = {"sessionId": session_id, "harnessCallId": call_id or None,
                       "tool": name, "inputFingerprint": self._fingerprint(fingerprint),
                       "consecutive": consecutive, "limit": limit, "stopped": stopped,
                       "atMs": (time.monotonic() - rec.t0) * 1000}
                if isinstance(arguments, dict):
                    keys = sorted(str(key) for key in arguments
                                  if isinstance(key, str) and key)
                    row["argumentKeys"] = keys[:MAX_ARGUMENT_KEYS]
                    row["argumentKeysTruncated"] = len(keys) > MAX_ARGUMENT_KEYS
                    if name.lower() in _SHELLS:
                        command = next((arguments.get(key) for key in _COMMAND_KEYS
                                        if isinstance(arguments.get(key), str)), None)
                        metadata = {key: value for key, value in arguments.items()
                                    if key not in _COMMAND_KEYS}
                        row["executableVariant"] = self._variant(
                            "executable", {"tool": name.lower(), "command": command})
                        row["metadataVariant"] = self._variant("metadata", metadata)
                        if consecutive == 1:
                            self._brake_metadata[fingerprint] = []
                        sequence = self._brake_metadata.setdefault(fingerprint, [])
                        sequence.append(row["metadataVariant"])
                        del sequence[:-12]
                        if stopped:
                            for cycle in range(1, len(sequence)):
                                if sequence[-1] == sequence[-1 - cycle]:
                                    row["detectedCycleLength"] = cycle
                                    break
                rec.repeat_brake.append(row)

    def interval(self, name: str, start: float, *, ok: bool = True, running: bool | None = None) -> None:
        rec = self.record
        if rec is None:
            return
        with self.lock:
            if rec.t1 is not None:
                return
            if len(rec.intervals) < MAX_INTERVALS:
                rec.intervals.append({"name": name, "atMs": (start - rec.t0) * 1000,
                                      "ms": max(0, (time.monotonic() - start) * 1000),
                                      "ok": ok, "running": running})
            else:
                rec.intervals_truncated = True


def tool_readout(run: dict, rec) -> dict:
    row = dict(run)
    row["range"] = dict(run["range"])
    row["observedMs"] = ((run["completedObservedMs"] if run["completedObservedMs"] is not None else rec.ms)
                         - run["firstObservedMs"])
    row["observationLimit"] = "sampled interval; not exact execution time"
    row["startAtMs"] = row["endAtMs"] = None
    start, end = run["startUnixMs"], run["endUnixMs"]
    if start is not None and start >= rec.started_at * 1000:
        row["startAtMs"] = start - rec.started_at * 1000
    if end is not None and end >= rec.started_at * 1000:
        row["endAtMs"] = end - rec.started_at * 1000
    row["clockPlacement"] = ("outside_turn_or_unknown" if row["startAtMs"] is None or row["startAtMs"] > rec.ms + 1 or
                            row["endAtMs"] is not None and row["endAtMs"] > rec.ms + 1 else "wall_clock")
    if row["clockPlacement"] != "wall_clock":
        row["startAtMs"] = row["endAtMs"] = None
        row["completionLagMs"] = None
    return row
