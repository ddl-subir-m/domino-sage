"""Analyze relevant CSV text with a source-bound manifest and exact ID coverage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import result

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_BATCH_SIZE = 100
MAX_CONCURRENCY = 4
MAX_TEXT_CHARS = 2_000


@dataclass(frozen=True)
class Record:
    task_id: str
    source_id: str
    text: str
    row_number: int


@dataclass(frozen=True)
class BatchResult:
    index: int
    request: dict[str, Any]
    rows: list[list[str]]
    failed: int = 0
    unfinished: int = 0
    error: str = ""


def analyze(args: dict, turn) -> str:
    if turn.analyze_text_batch is None:
        return "Text analysis through the LLM Gateway is not available in this turn."
    source = str(args.get("path") or "")
    target = turn.upload_for(source) if turn.upload_for else None
    if target is None:
        return "This source is not available in this conversation."

    text_column = args.get("text_column")
    id_column = args.get("id_column")
    if not isinstance(text_column, str) or not text_column:
        return "Choose the text column to analyze."
    if id_column is not None and not isinstance(id_column, str):
        return "The source ID column must be a column name or null."

    labels = args.get("labels")
    if labels is not None:
        if (not isinstance(labels, list) or not labels
                or any(not isinstance(label, str) or not label for label in labels)):
            return "Labels must be a non-empty list of strings."
        labels = list(dict.fromkeys(labels))
    output_field = args.get("output_field") or ("label" if labels else "summary")
    if not isinstance(output_field, str) or not output_field:
        return "The output field must be a string."
    batch_size = args.get("batch_size")
    if batch_size is None:
        batch_size = 50
    if type(batch_size) is not int or batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        return f"Batch size must be between 1 and {MAX_BATCH_SIZE}."
    concurrency = args.get("max_concurrency")
    if concurrency is None:
        concurrency = 1
    if type(concurrency) is not int or concurrency < 1 or concurrency > MAX_CONCURRENCY:
        return f"Concurrency must be between 1 and {MAX_CONCURRENCY}."
    row_limit = args.get("row_limit")
    if row_limit is not None and (type(row_limit) is not int or row_limit < 1):
        return "The row limit must be a positive integer."

    try:
        raw, columns, rows = _read_csv(Path(target))
    except (OSError, UnicodeError, csv.Error):
        return "The CSV could not be analyzed. Check its encoding and rows. No result was saved."
    if text_column not in columns or (id_column and id_column not in columns):
        return "The CSV must have the selected text column and source ID column."
    if len(rows) > MAX_RECORDS and row_limit is None:
        return (f"This CSV has {len(rows)} records, above the {MAX_RECORDS} record analysis limit. "
                "No sample was used. Ask for an explicitly labelled sample or add a limit.")

    source_sha = hashlib.sha256(raw).hexdigest()
    records, excluded = _manifest(rows, text_column, id_column, row_limit)
    if not records:
        return "No records with text were available to analyze. No result was saved."

    operation = "du_" + uuid4().hex
    name = args.get("result_name") or f"{Path(source).stem}-analysis-{operation[-8:]}"
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        return "The result name must be one filename without a directory."
    output = turn.examples_dir / f"{name}.table.json"
    if (not turn.examples_dir.resolve().is_relative_to(turn.examples_dir.parent.parent.resolve())
            or output.is_symlink() or not output.resolve().is_relative_to(turn.examples_dir.resolve())):
        return "The result must stay in this conversation's Artifacts."

    batches = [records[i:i + batch_size] for i in range(0, len(records), batch_size)]
    batch_results: list[BatchResult] = []
    unfinished = 0
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(_run_batch, i, batch, args, output_field, labels, turn)
                for i, batch in enumerate(batches)
            ]
            for future in as_completed(futures):
                batch_results.append(future.result())
    except CancelledError:
        unfinished = len(records) - sum(len(r.rows) for r in batch_results)

    batch_results.sort(key=lambda item: item.index)
    analyzed_rows = [row for batch in batch_results for row in batch.rows]
    failed = sum(batch.failed for batch in batch_results)
    unfinished += sum(batch.unfinished for batch in batch_results)
    source_changed = _source_changed(Path(target), source_sha)
    if source_changed:
        unfinished = max(unfinished, len(records) - len(analyzed_rows) - failed)

    columns_out = ["Record ID", output_field]
    rows_out = analyzed_rows
    receipt = result.record(turn.examples_dir, name, str(args.get("title") or "Text analysis"),
                            columns_out, rows_out, keep_rows=turn.keep_rows)
    coverage = {
        "total": len(rows),
        "processed": 0 if source_changed else len(rows_out),
        "excluded": excluded,
        "failed": failed,
        "unfinished": (len(records) if source_changed else unfinished),
    }
    event = {
        "operation_id": operation,
        "operation": "text_analysis",
        # Always drawn, for the reason `calculate` states: the turn was asked to do this
        # (ADR-0063, *What this does not decide*).
        "role": "answer",
        "source": source,
        "source_sha256": source_sha,
        "artifact": receipt.path,
        "columns": [c for c in [id_column, text_column] if c],
        "selected_fields": ["task_id", output_field],
        "result_rows": len(rows_out),
        "coverage": coverage,
        "manifest": {
            "records": len(records),
            "id_column": id_column,
            "text_column": text_column,
            "source_sha256": source_sha,
            "source_changed": source_changed,
        },
        "batches": [{"index": b.index, "failed": b.failed, "unfinished": b.unfinished,
                     "error": b.error} for b in batch_results],
        "purpose": str(args.get("purpose") or "Analyze CSV text"),
        "requests": [b.request for b in batch_results],
        "delivery": "unknown",
    }
    selected = _selected(rows_out, columns_out)
    reply = {
        "data_use": operation,
        "operation": "text_analysis",
        "columns": columns_out,
        "result_rows": len(rows_out),
        "local_reference": receipt.path,
        "coverage": coverage,
        "selected_fields": ["task_id", output_field],
        "selected": selected,
        "kept_rows": receipt.kept,
    }
    if source_changed:
        reply["warning"] = "The source changed while analysis ran, so this is not a complete result."
    if turn.record_data_use:
        turn.record_data_use(event, reply)
    return json.dumps(reply)


def _read_csv(path: Path) -> tuple[bytes, list[str], list[dict[str, str]]]:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise csv.Error("too large")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
    columns = reader.fieldnames or []
    if not columns or len(columns) != len(set(columns)):
        raise csv.Error("bad header")
    rows = []
    for row in reader:
        if None in row:
            raise csv.Error("wrong field count")
        rows.append({k: "" if v is None else v for k, v in row.items()})
    return raw, columns, rows


def _manifest(rows: list[dict[str, str]], text_column: str, id_column: str | None,
              row_limit: int | None) -> tuple[list[Record], int]:
    records = []
    excluded = 0
    stop = row_limit or len(rows)
    for index, row in enumerate(rows[:stop], start=1):
        text = row.get(text_column, "").strip()
        if not text:
            excluded += 1
            continue
        source_id = row.get(id_column, "").strip() if id_column else ""
        records.append(Record(f"r{index:06d}", source_id, text[:MAX_TEXT_CHARS], index))
    excluded += max(0, len(rows) - stop)
    return records, excluded


def _run_batch(index: int, records: list[Record], args: dict, output_field: str,
               labels: list[str] | None, turn) -> BatchResult:
    request_id = "req_" + uuid4().hex
    evidence = {"request_id": request_id,
                "requested_alias": str(args.get("model") or "auto"),
                "serving_model": None,
                "provider_receipt": "unknown",
                "cache": "unknown",
                "decision_stage": "unknown",
                "delivery": "unknown",
                "state": "attempted",
                "records": len(records)}
    last_error = ""
    for attempt in range(2):
        request = _request(evidence["requested_alias"], records, args, output_field, labels)
        try:
            text, state, denied = _collect(turn.analyze_text_batch(request))
        except CancelledError:
            return BatchResult(index, evidence | {"state": "interrupted"}, [], unfinished=len(records),
                               error="cancelled")
        except Exception as error:
            state, denied, text = "failed", False, ""
            last_error = type(error).__name__
        if denied:
            return BatchResult(index, evidence | {"state": "failed"}, [], failed=len(records),
                               error="policy_denied")
        if state != "response_completed":
            last_error = state
            if attempt == 0:
                continue
            return BatchResult(index, evidence | {"state": state}, [], unfinished=len(records),
                               error=last_error)
        try:
            rows = _validated_rows(text, records, output_field, labels)
        except ValueError as error:
            last_error = str(error)
            if attempt == 0:
                continue
            return BatchResult(index, evidence | {"state": "failed"}, [], failed=len(records),
                               error=last_error)
        return BatchResult(index, evidence | {"state": state}, rows)
    return BatchResult(index, evidence | {"state": "failed"}, [], failed=len(records),
                       error=last_error or "failed")


def _request(model: str, records: list[Record], args: dict, output_field: str,
             labels: list[str] | None) -> dict[str, Any]:
    payload = {
        "task": str(args.get("purpose") or "Analyze each record."),
        "output_field": output_field,
        "records": [{"id": r.task_id, "text": r.text} for r in records],
    }
    if labels:
        payload["labels"] = labels
    instruction = ("Return only JSON with a records array. Each item must have id and "
                   f"{output_field}. Use each provided id exactly once. Do not add unknown ids.")
    return {"model": model, "stream": True, "messages": [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]}


def _collect(stream) -> tuple[str, str, bool]:
    if isinstance(stream, dict):
        return json.dumps(stream), "response_completed", False
    if isinstance(stream, str):
        return stream, "response_completed", False
    text = ""
    completed = False
    denied = False
    buffer = ""
    for chunk in stream:
        buffer += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                body = json.loads(payload)
            except ValueError:
                continue
            error = body.get("error")
            if error:
                denied = _is_policy_error(error)
                return text, "failed", denied
            for choice in body.get("choices", []):
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    text += delta["content"]
                if choice.get("finish_reason") == "stop":
                    completed = True
    return text, "response_completed" if completed else "interrupted", False


def _is_policy_error(error: Any) -> bool:
    text = json.dumps(error).lower() if isinstance(error, (dict, list)) else str(error).lower()
    return any(word in text for word in ("policy", "guardrail", "refusal", "denied"))


def _validated_rows(text: str, records: list[Record], output_field: str,
                    labels: list[str] | None) -> list[list[str]]:
    body = _json_body(text)
    items = body.get("records") if isinstance(body, dict) else body
    # ValueError and not the TypeError the isinstance suggests, for the reason the sibling at
    # `service.py:16014` gives: this parses a MODEL's answer, the caller above catches ValueError
    # for every way that answer can be malformed, and the three raises below are ValueError too.
    # Splitting one condition across two exception types would let these two escape that handler.
    if not isinstance(items, list):
        raise ValueError("missing records")  # noqa: TRY004
    expected = {r.task_id: r for r in records}
    seen: set[str] = set()
    out: list[list[str]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("malformed id")  # noqa: TRY004 — see the note above
        task_id = item["id"]
        if task_id in seen:
            raise ValueError("duplicate id")
        if task_id not in expected:
            raise ValueError("unknown id")
        value = item.get(output_field)
        if not isinstance(value, str) or not value:
            raise ValueError("malformed output")
        if labels and value not in labels:
            raise ValueError("unknown label")
        out.append([task_id, value])
        seen.add(task_id)
    missing = set(expected) - seen
    if missing:
        raise ValueError("missing id")
    return out


def _json_body(text: str):
    try:
        return json.loads(text)
    except ValueError:
        start = min([i for i in [text.find("{"), text.find("[")] if i >= 0], default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start < 0 or end < start:
            raise ValueError("malformed json")
        return json.loads(text[start:end + 1])


def _selected(rows: list[list[str]], columns: list[str]) -> dict[str, Any]:
    values = {"rows": rows}
    if len(json.dumps(values)) <= result.VALUES_BUDGET_CHARS:
        return values
    counts: dict[str, int] = {}
    field_index = len(columns) - 1
    for row in rows:
        counts[str(row[field_index])] = counts.get(str(row[field_index]), 0) + 1
    return {"counts": counts}


def _source_changed(path: Path, expected_sha: str) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha
    except OSError:
        return True
