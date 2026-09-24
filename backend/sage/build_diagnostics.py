"""Bounded, metadata-only Build captures on the existing workspace volume (#512).

Two writes per captured turn: its identity at the existing context bind, then its
finished snapshot. A process that cannot finish leaves explicit incomplete metadata.
"""
from __future__ import annotations

import contextvars
import copy
import json
import logging
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import timing
from .request_composition import diagnostic_tool_name
from .tool_timing import argument_keys_for_tool
from .workspace.stack import STACKS

log = logging.getLogger("sage.diagnostics")
SCHEMA_VERSION = 1
OUTCOMES = frozenset({"repeat_brake", "user_stop", "gateway_refusal", "error", "success"})
PHASES = frozenset({"planning", "implementation"})
CAPTURE_STATUSES = frozenset({"running", "finished", "interrupted"})
MAX_RECORDS = 20
MAX_RECORD_BYTES = 256 * 1024
MAX_EVENTS = 4000
SECTION_CAPS = {"calls": 256, "spans": 1000, "tools": 500, "intervals": 2000, "repeatBrake": 500}
MAX_STORE_BYTES = MAX_RECORDS * MAX_RECORD_BYTES + 4096
_lock = threading.RLock()
_active: set[tuple[str, str]] = set()
_TOKEN = re.compile(r"[\w.:/@+-]{1,160}\Z", re.ASCII)
_MODEL_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,160}\Z")

# No catch-all copy: new recorder fields are private until this contract admits them.
CALL_FIELDS = ["n", "model", "requestedAlias", "phase", "reason", "callId", "turnId", "protocol", "requestedEffort", "effortStatus", "sessionId", "rootSessionId", "firstTextMs", "firstToolArgumentMs", "lastChunkMs", "maxChunkGapMs", "outcome", "forwardedReqBytes", "toolsTruncated", "outTokens", "reasoningTokens", "atMs", "ttfbMs", "prepMs", "ms", "chunks", "reqBytes", "inTokens", "cachedTokens", "ok"]
TOOL_FIELDS = ["sessionId", "harnessCallId", "partId", "identitySource", "tool", "firstObservedMs", "lastObservedMs", "completedObservedMs", "observationSource", "startUnixMs", "endUnixMs", "startSource", "endSource", "executionMs", "completionLagMs", "targetFingerprint", "queryFingerprint", "targetMetadataFinal", "editSincePreviousRead", "opaqueOperationSincePreviousRead", "targetState", "status", "observedMs", "startAtMs", "endAtMs", "clockPlacement"]
INVOKE_FIELDS = ["name", "providerId", "protocolIndex", "identityStatus", "metadataTruncated"]
SPAN_FIELDS = ["name", "depth", "atMs", "ms", "open", "no_edit_attempt", "wrote_code", "retry_exhausted"]
INTERVAL_FIELDS = ["name", "atMs", "ms", "ok", "running"]
BRAKE_FIELDS = ["sessionId", "harnessCallId", "tool", "inputFingerprint", "consecutive", "limit",
                "stopped", "atMs", "argumentKeysTruncated", "executableVariant",
                "metadataVariant", "detectedCycleLength", "unknownArgumentKeyCount",
                "unknownArgumentKeysTruncated"]
COUNTERS = {"poll.iterations", "tools.unidentified_events", "attachments.requested",
            "attachments.eligible", "attachments.resolved", "attachments.unavailable",
            "attachments.restored.absent", "attachments.restored.dangling",
            *{"attachments.repair_failed." + name for name in
              ("ValueError", "OSError", "FileNotFoundError", "LookupError", "ResourceUnavailable")}}
OBSERVATIONS = {"attachments.resolution_ms", "emit.lag_ms", "poll.read_ms", "poll.sleep_ms"}
TEXT_FIELDS = {"appId", "conversationId", "kind", "name", "model", "requestedAlias", "phase", "reason", "callId", "turnId", "protocol", "requestedEffort",
               "effortStatus", "sessionId", "rootSessionId", "outcome", "providerId", "identityStatus",
               "harnessCallId", "partId", "identitySource", "tool", "observationSource", "startSource",
               "endSource", "targetFingerprint", "queryFingerprint", "targetState", "status",
               "clockPlacement", "inputFingerprint"}
BOOL_FIELDS = {"ok", "running", "open", "toolsTruncated", "metadataTruncated", "targetMetadataFinal",
               "editSincePreviousRead", "opaqueOperationSincePreviousRead", "stopped", "wrote_code",
               "retry_exhausted", "argumentKeysTruncated", "unknownArgumentKeysTruncated"}


def _metadata(row, keys):
    out = {}
    if not isinstance(row, dict):
        return out
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if key in {"model", "requestedAlias"}:
            valid = value is None or isinstance(value, str) and _MODEL_NAME.fullmatch(value)
        else:
            valid = (value is None
                     or key in BOOL_FIELDS and isinstance(value, bool)
                     or key not in TEXT_FIELDS | BOOL_FIELDS and isinstance(value, (int, float))
                     and not isinstance(value, bool) and math.isfinite(value)
                     or key in TEXT_FIELDS and isinstance(value, str)
                     and (_TOKEN.fullmatch(value) or key == "conversationId" and value == ""))
        if valid:
            out[key] = value
    return out


def _encode(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()


def _request_composition(value) -> dict | None:
    """Copy only the fixed numeric composition schema into a persisted capture."""
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    status = value.get("status")
    boundary = value.get("boundary")
    reason = value.get("limitReason")
    if status not in {"complete", "limited", "unavailable"}:
        return None
    if boundary != "final_forwarded_json":
        return None
    if reason not in {None, "payload_bytes", "nodes", "depth", "classification_overlap",
                      "measurement_error"}:
        return None

    def number(raw):
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 1 << 53 else 0

    def mapping(raw):
        return raw if isinstance(raw, dict) else {}

    category_keys = ("instructionsBytes", "toolSchemasBytes", "ordinaryTextBytes",
                     "toolCallsBytes", "toolResultsBytes", "mediaBytes", "opaqueStateBytes",
                     "unclassifiedBytes")
    role_keys = ("system", "developer", "user", "assistant", "tool", "unknown")
    rewrite_keys = ("redactedCalls", "localExecutionReceipts", "markerEchoCorrections",
                    "externalImageReceipts", "withheldImageReceipts")
    categories = mapping(value.get("categories"))
    roles = mapping(value.get("messagesByRole"))
    rewrites = mapping(value.get("rewrites"))
    sources = mapping(value.get("systemInstructionsBySource"))
    schemas = mapping(value.get("toolSchemasByName"))
    duplicates = mapping(value.get("exactDuplicateInstructionBlocks"))
    build_bytes = mapping(value.get("buildBytes"))
    assembly = mapping(value.get("implementationAssembly"))
    removed_schemas = mapping(assembly.get("removedToolSchemasByName"))
    removed_instruction_sources = mapping(
        assembly.get("duplicateInstructionBlocksRemovedBySource"))

    def named_counts(raw):
        out = {}
        for key, row in raw.items():
            identifier = diagnostic_tool_name(key)
            bucket = out.setdefault(identifier, {"count": 0, "bytes": 0})
            bucket["count"] += number(mapping(row).get("count"))
            bucket["bytes"] += number(mapping(row).get("bytes"))
        return out

    removed_by_name = {}
    for key, count in removed_schemas.items():
        identifier = diagnostic_tool_name(key)
        removed_by_name[identifier] = removed_by_name.get(identifier, 0) + number(count)

    out = {
        "version": 1, "boundary": boundary, "status": status,
        "totalBytes": number(value.get("totalBytes")),
        "categories": {key: number(categories.get(key)) for key in category_keys},
        "messagesByRole": {
            key: {"count": number(mapping(roles.get(key)).get("count")),
                  "bytes": number(mapping(roles.get(key)).get("bytes"))}
            for key in role_keys
        },
        "toolSchemaCount": number(value.get("toolSchemaCount")),
        "toolCallCount": number(value.get("toolCallCount")),
        "toolArgumentsBytes": number(value.get("toolArgumentsBytes")),
        "rewrites": {key: number(rewrites.get(key)) for key in rewrite_keys},
        "systemInstructionsBySource": {
            key: {"count": number(mapping(sources.get(key)).get("count")),
                  "bytes": number(mapping(sources.get(key)).get("bytes"))}
            for key in ("topLevelInstructions", "topLevelSystem",
                        "messageSystem", "messageDeveloper")
        },
        "toolSchemasByName": named_counts(schemas),
        "exactDuplicateInstructionBlocks": {
            "count": number(duplicates.get("count")),
            "bytes": number(duplicates.get("bytes")),
        },
        "buildBytes": {
            key: number(build_bytes.get(key))
            for key in ("fixedBytes", "dynamicBuildIntentBytes",
                        "toolResultBytes", "mediaBytes")
        },
        "implementationAssembly": {
            **{key: number(assembly.get(key)) for key in (
                "beforeBytes", "afterBytes", "removedBytes",
                "duplicateInstructionBlocksRemoved", "duplicateInstructionBytesRemoved",
                "duplicateToolSchemasRemoved", "unreachableToolSchemasRemoved")},
            "duplicateInstructionBlocksRemovedBySource": {
                key: number(removed_instruction_sources.get(key))
                for key in ("topLevelInstructions", "topLevelSystem",
                            "messageSystem", "messageDeveloper")
            },
            "removedToolSchemasByName": removed_by_name,
        } if assembly else {},
        "limitReason": reason,
    }
    window = value.get("toolResultWindow")
    if isinstance(window, dict):
        window_keys = (
            "policyVersion", "perResultLimitBytes", "aggregateLimitBytes", "resultCount",
            "originalModelFacingBytes", "forwardedModelFacingBytes", "perResultShortenedCount",
            "aggregateCompactedCount", "emptyFallbackCount",
        )
        out["toolResultWindow"] = {key: number(window.get(key)) for key in window_keys}
    return out


def _build_intent(value) -> dict | None:
    """Copy only the fixed, content-free Build-intent schema into a persisted capture."""
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    status = value.get("status")
    stage = value.get("failureStage")
    if kind not in {"direct_build", "approved_plan", "phase"}:
        return None
    if status not in {"ok", "missing", "changed", "duplicate", "unsupported"}:
        return None
    if stage not in {"none", "install", "prepare", "final_check"}:
        return None

    def count(key):
        number = value.get(key)
        return number if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else 0

    return {
        "kind": kind,
        "status": status,
        "carrierCount": count("carrierCount"),
        "carrierBytes": count("carrierBytes"),
        "sourceRequestCount": count("sourceRequestCount"),
        "planPresent": bool(value.get("planPresent")),
        "failureStage": stage,
    }


@lru_cache(maxsize=1)
def source_revision() -> str | None:
    home = os.environ.get("SAGE_APP_HOME") or str(Path(__file__).resolve().parents[2])
    try:
        result = subprocess.run(["git", "-C", home, "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=3, check=False)
        value = result.stdout.strip()
        return value if re.fullmatch(r"[a-f0-9]{40,64}", value) else None
    except Exception:
        return None


def _phase(raw: dict, kind: str) -> str:
    """A bounded display label, never a copy of model or transcript text."""
    phases = {row.get("phase") for row in raw.get("calls", []) if isinstance(row, dict)}
    if kind == "approve" or "implement" in phases:
        return "implementation"
    if "plan" in phases:
        return "planning"
    return "planning" if kind == "build" else "implementation"


def _outcome(value: object) -> str:
    """Read old persisted records through the new bounded terminal vocabulary."""
    if value in OUTCOMES:
        return str(value)
    if value == "stopped":
        return "user_stop"
    return "error"


def _started_at(value: object) -> str | float | int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    if isinstance(value, str) and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]{8,32}Z?", value):
        return value
    return None


def snapshot(rec: timing.TurnRecord | None, identity: dict, *, outcome="error",
             terminal=False, revision=None, plan_contract: dict | None = None) -> dict:
    raw = timing.as_dict(rec) if rec is not None else {}
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "sourceRevision": revision if isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{7,64}", revision) else None,
        "turn": {**_metadata(identity, ["turnId", "appId", "conversationId", "kind"]),
                 "phase": _phase(raw, str(identity.get("kind") or "")),
                 "startedAt": raw.get("startedAt", identity.get("startedAt", time.time()))},
        "buildOutcome": {"status": _outcome(outcome)},
        "capture": {"status": "finished" if terminal else "interrupted",
                    "complete": False, "recorderEnabled": timing.enabled(), "recordAvailable": rec is not None,
                    "droppedEvents": {}, "upstreamTruncated": {}},
        "timing": _metadata(raw, ["ms", "ok", "running"]),
    }
    data = record["timing"]
    if plan_contract is not None:
        record["planContract"] = plan_contract
    drops = record["capture"]["droppedEvents"]
    remaining = MAX_EVENTS
    for section, keys in (("calls", CALL_FIELDS), ("spans", SPAN_FIELDS), ("tools", TOOL_FIELDS),
                          ("intervals", INTERVAL_FIELDS), ("repeatBrake", BRAKE_FIELDS)):
        rows = raw.get(section, [])
        kept = []
        for row in rows[:SECTION_CAPS[section]]:
            if remaining <= 0:
                break
            remaining -= 1
            entry = _metadata(row, keys)
            if section == "calls":
                invocations = row.get("toolInvocations", [])
                entry["toolInvocations"] = [_metadata(item, INVOKE_FIELDS)
                                            for item in invocations[:min(40, remaining)]]
                remaining -= len(entry["toolInvocations"])
                drops["toolInvocations"] = (drops.get("toolInvocations", 0)
                                            + len(invocations) - len(entry["toolInvocations"]))
                entry["tools"] = [v for v in row.get("tools", [])[:40]
                                  if isinstance(v, str) and _TOKEN.fullmatch(v)]
                composition = _request_composition(row.get("requestComposition"))
                if composition is not None:
                    entry["requestComposition"] = composition
                intent = _build_intent(row.get("buildIntent"))
                if intent is not None:
                    entry["buildIntent"] = intent
                reported = row.get("responseReportedModel")
                if (isinstance(reported, str)
                        and reported in {entry.get("model"), entry.get("requestedAlias")}):
                    entry["responseReportedModel"] = reported
            if section == "spans":
                why = row.get("why")
                reasons = {"first send": "first_send", "runtime repair": "runtime_repair",
                           "copied attached data into source — moving it back to data/": "data_copy_repair",
                           "called the LLM Gateway directly — routing it through askModel": "gateway_repair"}
                if isinstance(why, str):
                    category = reasons.get(why, "unknown")
                    if re.fullmatch(r"iteration [0-9]+, errors remain", why):
                        category = "typecheck_repair"
                    elif re.fullmatch(r"(?:planned but wrote no code — switching to Implement|wrote no code — retrying)(?: with the strong model)?", why):
                        category = "no_edit"
                    entry["retryCategory"] = category
                if row.get("stack") in STACKS:
                    entry["stack"] = row["stack"]
                if row.get("retry_reason") in {
                    "no_edit", "no_edit_exhausted", "typecheck_repair"
                }:
                    entry["retry_reason"] = row["retry_reason"]
            if section == "tools":
                entry["range"] = _metadata(row.get("range", {}), ["offset", "limit", "startLine", "endLine"])
            if section == "repeatBrake":
                entry["argumentKeys"] = argument_keys_for_tool(
                    row.get("tool", ""), row.get("argumentKeys", []))[:16]
            kept.append(entry)
        data[section] = kept
        drops[section] = len(rows) - len(kept)
        if section == "calls":
            drops["toolInvocations"] = drops.get("toolInvocations", 0) + sum(
                len(row.get("toolInvocations", [])) for row in rows[len(kept):])
    data["counters"] = _metadata(raw.get("counters", {}), COUNTERS)
    data["observations"] = {key: _metadata(value, ["n", "p50", "p90", "max", "sum"])
                            for key, value in raw.get("observations", {}).items() if key in OBSERVATIONS}
    for key in ("toolsTruncated", "intervalsTruncated", "repeatBrakeTruncated"):
        record["capture"]["upstreamTruncated"][key] = bool(raw.get(key))
        data[key] = bool(raw.get(key))
    record["capture"]["upstreamTruncated"]["toolInvocations"] = any(
        call.get("toolsTruncated") or any(inv.get("metadataTruncated")
                                        for inv in call.get("toolInvocations", []))
        for call in raw.get("calls", []))
    # Cap the finished JSON too: bounded field lengths alone do not bound the sum.
    # Reserve space for the current retention metadata added by the download route.
    while len(_encode(record)) > MAX_RECORD_BYTES - 512:
        section = max(SECTION_CAPS, key=lambda key: len(data[key]))
        count = len(data[section])
        if not count:
            raise ValueError("diagnostic metadata exceeds record cap")
        remove = max(1, count // 2)
        if section == "calls":
            drops["toolInvocations"] += sum(len(row.get("toolInvocations", []))
                                             for row in data[section][-remove:])
        del data[section][-remove:]
        drops[section] += remove
    record["capture"]["complete"] = bool(
        terminal and rec is not None and not any(drops.values())
        and not any(record["capture"]["upstreamTruncated"].values()))
    return record


class Store:
    def __init__(self, root: Path):
        self.path = root / ".sage" / "build-diagnostics.json"

    def _load(self):
        if not self.path.exists():
            return {"schemaVersion": SCHEMA_VERSION, "droppedRecords": 0, "records": []}
        if self.path.stat().st_size > MAX_STORE_BYTES:
            raise ValueError("diagnostic store exceeds cap")
        body = json.loads(self.path.read_text())
        if body.get("schemaVersion") != SCHEMA_VERSION or not isinstance(body.get("records"), list):
            raise ValueError("unsupported diagnostic store")
        return body

    def put(self, record):
        # Failure is deliberately a return value. Diagnostics never fail the Build.
        try:
            with _lock:
                body = self._load()
                records = body["records"]
                identity = record["turn"]
                key = (identity["turnId"], identity["appId"], identity["conversationId"])
                records[:] = [old for old in records if tuple(old["turn"][k] for k in
                              ("turnId", "appId", "conversationId")) != key]
                records.append(record)
                excess = max(0, len(records) - MAX_RECORDS)
                body["droppedRecords"] += excess
                if excess:
                    del records[:excess]
                encoded = _encode(body)
                if len(_encode(record)) > MAX_RECORD_BYTES or len(encoded) > MAX_STORE_BYTES:
                    raise ValueError("diagnostic store exceeds cap")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".build-diagnostics-", dir=self.path.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(encoded)
                    os.replace(temporary, self.path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            return True
        except Exception as exc:
            log.warning("Build diagnostic persistence failed (%s)", type(exc).__name__)
            return False

    def get(self, turn_id: str, app_id: str, conversation_id: str):
        with _lock:
            body = self._load()
            record = next((row for row in body["records"]
                           if row["turn"].get("turnId") == turn_id
                           and row["turn"].get("appId") == app_id
                           and row["turn"].get("conversationId") == conversation_id), None)
            if record is None:
                return None
            result = copy.deepcopy(record)
            result.setdefault("turn", {})["phase"] = result.get("turn", {}).get("phase") or _phase(
                result.get("timing", {}), str(result.get("turn", {}).get("kind") or ""))
            result.setdefault("buildOutcome", {})["status"] = _outcome(
                result.get("buildOutcome", {}).get("status"))
            if result["capture"]["status"] == "capturing":
                result["capture"]["status"] = ("running" if (str(self.path), turn_id) in _active
                                                 else "interrupted")
            result["retention"] = {"recordLimit": MAX_RECORDS, "recordBytes": MAX_RECORD_BYTES,
                                   "eventLimit": MAX_EVENTS, "sectionCaps": SECTION_CAPS,
                                   "droppedRecords": body["droppedRecords"]}
            return result

    def list(self, app_id: str) -> list[dict]:
        """Bounded metadata-only summaries for one app, independent of its transcript."""
        with _lock:
            body = self._load()
            rows = []
            for record in body["records"]:
                turn = record.get("turn", {})
                if turn.get("appId") != app_id:
                    continue
                capture = record.get("capture", {})
                status = capture.get("status")
                if status == "capturing":
                    status = ("running" if (str(self.path), turn.get("turnId")) in _active
                              else "interrupted")
                if status not in CAPTURE_STATUSES:
                    status = "interrupted"
                phase = turn.get("phase")
                if phase not in PHASES:
                    phase = _phase(record.get("timing", {}), str(turn.get("kind") or ""))
                rows.append({
                    "turn": {
                        **_metadata(turn, ["turnId", "appId", "conversationId", "kind"]),
                        "phase": phase,
                        "startedAt": _started_at(turn.get("startedAt")),
                    },
                    "buildOutcome": {"status": _outcome(
                        record.get("buildOutcome", {}).get("status"))},
                    "capture": {"status": status, "complete": bool(capture.get("complete"))},
                })
            return rows


@dataclass
class Capture:
    store: Store
    identity: dict
    revision: str | None
    outcome: str = "error"
    terminal: bool = False
    plan_contract: dict | None = None


_current: contextvars.ContextVar[Capture | None] = contextvars.ContextVar("build_diagnostic_capture", default=None)


def begin(root: Path, *, turn_id: str, app_id: str, conversation_id: str, kind: str):
    _current.set(None)
    try:
        capture = Capture(Store(root), {"turnId": turn_id, "appId": app_id,
                          "conversationId": conversation_id or "", "kind": kind,
                          "startedAt": time.time()}, source_revision())
        _current.set(capture)
        rec = timing.current()
        if rec is not None and rec.turn_id != turn_id:
            rec = None
        record = snapshot(rec, capture.identity, revision=capture.revision)
        record["capture"]["status"] = "capturing"
        with _lock:
            _active.add((str(capture.store.path), turn_id))
        capture.store.put(record)
    except Exception as exc:
        log.warning("Build diagnostic start failed (%s)", type(exc).__name__)


def observe(event: dict) -> str | None:
    """Record one terminal event even when transcript rollback will remove that event."""
    capture = _current.get()
    if capture is None:
        return None
    if event.get("type") == "done":
        capture.terminal = True
        decision = str(event.get("decision") or "")
        capture.outcome = (
            "success" if event.get("ok") is True
            else "repeat_brake" if decision in {"repeat_brake", "repeated", "looped"}
            else "gateway_refusal" if decision in {"gateway error", "model unavailable"}
            else "error"
        )
    elif event.get("type") == "stopped":
        capture.terminal = True
        capture.outcome = "user_stop"
    return capture.identity["turnId"]


def record_plan_contract(*, execution_contract_version: int,
                         source_request_messages_version: int,
                         source_request_count: int, valid: bool, step_count: int,
                         malformed_step_count: int, invalid_file_count: int,
                         missing_sections: tuple[str, ...]) -> None:
    """Attach fixed, content-free plan validation facts to the active Build diagnostic."""
    capture = _current.get()
    if capture is None:
        return
    allowed_sections = {
        "title", "summary", "problem", "users", "outcomes", "screens", "acceptance", "plan"
    }
    capture.plan_contract = {
        "executionContractVersion": execution_contract_version,
        "sourceRequestMessagesVersion": source_request_messages_version,
        "sourceRequestCount": max(0, source_request_count),
        "valid": bool(valid),
        "stepCount": max(0, step_count),
        "malformedStepCount": max(0, malformed_step_count),
        "invalidFileCount": max(0, invalid_file_count),
        "missingSections": [key for key in missing_sections if key in allowed_sections],
    }


def history_metadata(app_id: str, conversation_id: str | None, event: dict) -> dict:
    capture = _current.get()
    if (capture is None or capture.identity["appId"] != app_id
            or capture.identity["conversationId"] != (conversation_id or "")):
        return {}
    observe(event)
    return {"turnId": capture.identity["turnId"]}


def finish(rec: timing.TurnRecord | None):
    capture = _current.get()
    _current.set(None)
    if capture is None:
        return
    try:
        if rec is not None and rec.turn_id != capture.identity["turnId"]:
            rec = None  # Never export a neighbour's record as this turn.
        capture.store.put(snapshot(rec, capture.identity, outcome=capture.outcome,
                                   terminal=capture.terminal, revision=capture.revision,
                                   plan_contract=capture.plan_contract))
    except Exception as exc:
        log.warning("Build diagnostic finish failed (%s)", type(exc).__name__)
    finally:
        with _lock:
            _active.discard((str(capture.store.path), capture.identity["turnId"]))
