"""Bounded tool observations. No extra I/O, arguments or content in diagnostic records."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import posixpath
import re
import secrets
import time

MAX_TOOLS = 500
MAX_INTERVALS = 2000
MAX_ARGUMENT_KEYS = 16
MAX_UNKNOWN_ARGUMENT_KEYS = 16
_READS = {"read", "glob", "grep", "list", "live_read_files", "live_read_table"}
_EDITS = {"edit", "write"}
_READ_ONLY = _READS | {"todoread", "todowrite"}
_SHELLS = {"bash", "shell", "sh", "run", "run_command", "execute", "exec", "terminal"}
_COMMAND_KEYS = ("command", "cmd", "code")
_SHELL_ARGUMENT_KEYS = frozenset({
    "command", "cmd", "code", "description", "timeout", "workdir", "cwd",
})
_KNOWN_ARGUMENT_KEYS = {
    **{name: _SHELL_ARGUMENT_KEYS for name in _SHELLS},
    **{name: frozenset({"filePath", "path", "file_path", "offset", "limit",
                        "startLine", "endLine"})
       for name in ("read", "read_file", "readfile", "view", "cat", "open", "get_file")},
    "glob": frozenset({"pattern", "path"}),
    "grep": frozenset({"pattern", "path", "include"}),
    "list": frozenset({"path"}),
    "write": frozenset({"filePath", "path", "file_path", "content"}),
    "edit": frozenset({"filePath", "path", "file_path", "oldString", "newString", "replaceAll"}),
}


_PROGRAM_NAME = re.compile(r"[A-Za-z0-9._+-]{1,32}\Z")
# `NAME=value` exactly: a leading environment assignment, which is a prefix and not the program.
# Anchored on both ends so `--flag=x` and `a=b=c` are not mistaken for one.
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[^\s]*\Z")
MAX_PROGRAMS = 24


def program_name(command: object) -> str:
    """The program a shell command runs, as a bare name — never the command (#544).

    The whole point is that nothing here can carry a person's data. A command line holds file
    paths, table names, hostnames and occasionally a secret; a program name holds none of those,
    and it is the only part of the line that answers "what was this turn doing" at all. So this
    reduces to the basename of the first non-assignment token and then REFUSES anything that does
    not look like a plain program name, rather than truncating or escaping it — a rejected token
    becomes "other", which is a fact, where a mangled one would be a leak with a length limit.

    Leading `NAME=value` assignments are stripped because `FOO=1 npm run build` runs npm, and an
    unstripped first token would record the variable — which is the half of the line most likely
    to hold a credential.

    A quoted token is refused OUTRIGHT, before the basename is taken, and that check is load-
    bearing rather than tidy. Splitting on whitespace cuts `'/opt/My Tools/run'` at the space, so
    the first token is `'/opt/My` and its basename is `My` — a fragment of somebody's DIRECTORY
    NAME, which passes the name pattern cleanly and is exactly the leak this exists to prevent.
    Caught by testing the rule against a path with a space in it; a quoting-free path never shows
    it, and neither does reading the code.

    `other` also covers everything else this deliberately cannot read: a pipeline starting in a
    subshell, `./script.sh` with an odd name, an empty command. Those are not worth a parser;
    knowing the call was a shell call whose program could not be named is enough.
    """
    if not isinstance(command, str):
        return "other"
    for token in command.strip().split():
        if _ASSIGNMENT.fullmatch(token):
            continue
        if "'" in token or '"' in token:
            return "other"
        name = posixpath.basename(token)
        return name if _PROGRAM_NAME.fullmatch(name) else "other"
    return "other"


def argument_keys_for_tool(tool: str, keys) -> list[str]:
    """Known schema keys safe to name in a diagnostic, in stable order."""
    allowed = _KNOWN_ARGUMENT_KEYS.get(str(tool or "").lower(), frozenset())
    return sorted(key for key in keys if key in allowed)


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
                    allowed = _KNOWN_ARGUMENT_KEYS.get(name.lower(), frozenset())
                    keys = argument_keys_for_tool(name, arguments)
                    unknown_keys = sum(1 for key in arguments if key not in allowed)
                    row["argumentKeys"] = keys[:MAX_ARGUMENT_KEYS]
                    row["argumentKeysTruncated"] = len(keys) > MAX_ARGUMENT_KEYS
                    row["unknownArgumentKeyCount"] = min(unknown_keys, MAX_UNKNOWN_ARGUMENT_KEYS)
                    row["unknownArgumentKeysTruncated"] = unknown_keys > MAX_UNKNOWN_ARGUMENT_KEYS
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
