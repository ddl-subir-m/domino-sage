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
# Reasoning counts against it: mimo spent up to 4,095 tokens thinking before a ~900-token answer.
MAX_OUTPUT_TOKENS = 16_000


@dataclass(frozen=True)
class Record:
    task_id: str
    source_id: str
    text: str
    row_number: int
    group: str = ""


@dataclass(frozen=True)
class BatchResult:
    index: int
    request: dict[str, Any]
    rows: list[list[str]]
    failed: int = 0
    unfinished: int = 0
    error: str = ""


def _load(args, turn, text_column, id_column, row_limit):
    """Rows from an upload, a mounted Dataset CSV, or a bound Data Source table.

    A string return is the sentence the model should read. A tuple is the source label, the
    bytes the coverage hash is taken from, the columns, the row dicts, and the upload path to
    re-read when the analysis finishes. A Dataset or a table has no second local file to watch.
    """
    if args.get("source") and args.get("sql"):
        return _load_statement(args, turn, row_limit)
    if args.get("source"):
        return _load_table(args, turn, text_column, id_column, row_limit)
    dataset = str(args.get("dataset") or "upload")
    path = str(args.get("path") or "")
    if dataset == "upload":
        target = turn.upload_for(path) if turn.upload_for else None
        if target is None:
            return "This source is not available in this conversation."
        try:
            raw, columns, rows = _read_csv(Path(target))
        except (OSError, UnicodeError, csv.Error):
            return "The CSV could not be analyzed. Check its encoding and rows. No result was saved."
        return path, raw, columns, rows, Path(target)
    from .calculate import _rows_from_dataset
    loaded, refused = _rows_from_dataset(args, turn)
    if refused:
        return refused
    source, raw = loaded
    try:
        columns, rows = _parse_csv(raw)
    except (UnicodeError, csv.Error):
        return "The CSV could not be analyzed. Check its encoding and rows. No result was saved."
    return source, raw, columns, rows, None


def _load_table(args, turn, text_column, id_column, row_limit):
    from ..orchestrator import brand
    from ..resources.provider import ResourceUnavailable, dialect_for, levels_missing
    from . import grant
    from .run import _scoped

    name, database, schema, table, _limit = _scoped({**args, "limit": 1}, turn)
    if not table:
        return "Name the table to analyze."
    refused = grant.reachable(
        "datasource", name,
        bound=turn.bound.get("datasource", ()),
        chips=turn.chips.get("datasource", ()),
    )
    if refused:
        return refused.says
    source = turn.source_for(name) if turn.source_for else None
    if source is None:
        return brand.text(
            "{assistantName} could not find {name} among the {dataSourcePlural} it can open here.",
            name=name or "that",
        )
    if turn.run_statement is None:
        return "This data source cannot be read for text analysis in this turn."
    try:
        dialect = dialect_for(source)
    except ResourceUnavailable as error:
        return str(error)
    if dialect.sample is None:
        return "This data source cannot be read for text analysis."
    missing = levels_missing(dialect.sample, database, schema)
    if missing:
        return brand.text(
            "{assistantName} does not know which {missing} {table} is in. Give the full name "
            "as database.schema.table.",
            missing=" and ".join(missing), table=table,
        )
    selected = []
    for column in (id_column, text_column):
        if isinstance(column, str) and column and column not in selected:
            selected.append(column)
    fetch = row_limit if row_limit is not None else MAX_RECORDS + 1
    try:
        quoted = ", ".join(dialect.ident(column) for column in selected)
        sql = dialect.statement(
            dialect.sample, database=database, schema=schema, table=table, limit=fetch)
    except ValueError as error:
        return str(error)
    if " * " not in sql:
        return "This data source cannot project columns for text analysis."
    sql = sql.replace(" * ", f" {quoted} ", 1)
    try:
        answer = turn.run_statement(source, sql, limit=fetch, cell_limit=MAX_TEXT_CHARS)
    except ResourceUnavailable as error:
        return str(error)
    columns = [str(column) for column in answer.columns]
    rows = []
    for row in answer.rows:
        if len(row) != len(columns):
            return ("The table could not be analyzed. A row had the wrong number of fields. "
                    "No result was saved.")
        rows.append({
            columns[i]: "" if row[i] is None else str(row[i]) for i in range(len(columns))
        })
    label = ".".join(part for part in (database, schema, table) if part)
    raw = json.dumps([columns, rows], ensure_ascii=True).encode()
    return label or table, raw, columns, rows, None


def _load_statement(args, turn, row_limit):
    """The rows one statement the agent composed chose: the candidates, not the whole table.

    Same boundary `_load_table` crosses, narrower: the rows go to the judging model through the
    Gateway and never to the chat model. The statement is returned to no one and written nowhere;
    `analyze` records its hash, for the reason `run._statement` gives.
    """
    from ..orchestrator import brand
    from ..resources.provider import ResourceUnavailable, ScopeIncomplete
    from . import grant
    from .run import _session_database

    name = str(args.get("source") or "")
    sql = str(args.get("sql") or "").strip()
    refused = grant.reachable(
        "datasource", name,
        bound=turn.bound.get("datasource", ()),
        chips=turn.chips.get("datasource", ()),
    )
    if refused:
        return refused.says
    source = turn.source_for(name) if turn.source_for else None
    if source is None:
        return brand.text(
            "{assistantName} could not find {name} among the {dataSourcePlural} it can open here.",
            name=name or "that",
        )
    if turn.run_statement is None:
        return "This data source cannot be read for text analysis in this turn."
    fetch = row_limit if row_limit is not None else MAX_RECORDS + 1
    try:
        answer = turn.run_statement(source, sql, limit=fetch, cell_limit=MAX_TEXT_CHARS)
    except ScopeIncomplete as error:
        return str(error)
    except ResourceUnavailable as error:
        return _session_database(str(error))
    columns = [str(column) for column in answer.columns]
    rows = []
    for row in answer.rows:
        if len(row) != len(columns):
            return ("The statement could not be analyzed. A row had the wrong number of fields. "
                    "No result was saved.")
        rows.append({
            columns[i]: "" if row[i] is None else str(row[i]) for i in range(len(columns))
        })
    raw = json.dumps([columns, rows], ensure_ascii=True).encode()
    return f"{name}.sql", raw, columns, rows, None


def _inside_dataset(path: str) -> str:
    """A file name inside the Dataset. A workspace path names the folder the person attached."""
    rel = path.replace("\\", "/").strip().lstrip("/")
    marker = "public/data/"
    if rel.startswith(marker):
        rest = rel[len(marker):]
        pieces = rest.split("/", 1)
        if len(pieces) == 2 and pieces[1]:
            return pieces[1]
    return rel


def _header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            line = handle.readline(8192)
    except OSError:
        return []
    return next(csv.reader([line]), [])


def _resolve_coarse_attachment(args: dict, turn):
    """A Dataset folder or a Data Source, when the person could not attach a file or a table.

    A string is what the model should do next. A dict is the same call with the file path filled
    in, when the folder holds one CSV and the text column was already named.
    """
    if (args.get("source") and not str(args.get("table") or "").strip()
            and not str(args.get("sql") or "").strip()):
        from .run import session_database_prompt
        return ("This Data Source was attached without a table. "
                "One live_read_query of INFORMATION_SCHEMA.TABLES and INFORMATION_SCHEMA.COLUMNS "
                "names the table and the text column. " + session_database_prompt() + " "
                "Call live_read_table again with "
                "operation=analyze_text, this source, that table, and text_column. "
                "Do not ask the person to attach a table.")
    dataset = str(args.get("dataset") or "")
    if not dataset or dataset == "upload" or str(args.get("path") or "").strip():
        if dataset and dataset != "upload" and args.get("path"):
            return {**args, "path": _inside_dataset(str(args.get("path") or ""))}
        return args
    from ..orchestrator import brand
    from . import grant
    refused = grant.reachable(
        "dataset", dataset,
        bound=turn.bound.get("dataset", ()),
        chips=turn.chips.get("dataset", ()),
    )
    if refused:
        return refused.says
    names: list[str] = []
    listing = turn.list_files(dataset) if turn.list_files else None
    if listing is not None:
        names = [str(f.path) for f in listing.files if str(f.path).lower().endswith(".csv")]
    root = turn.dataset_root(dataset) if turn.dataset_root else None
    base = Path(root).resolve() if root and Path(root).is_dir() else None
    if not names and base is not None:
        names = sorted(
            p.relative_to(base).as_posix()
            for p in base.rglob("*")
            if p.is_file() and p.suffix.lower() == ".csv" and p.resolve().is_relative_to(base)
        )
    if listing is None and base is None:
        return brand.text(
            "{name} isn't mounted here, so its files can't be listed. Tell the person that.",
            name=dataset,
        )
    found = []
    for name in names[:40]:
        cols: list[str] = []
        if base is not None:
            target = (base / name).resolve()
            if target.is_file() and target.is_relative_to(base):
                cols = _header(target)
        found.append((name, cols))
    if (len(found) == 1 and isinstance(args.get("text_column"), str) and args.get("text_column")):
        return {**args, "path": found[0][0]}
    if not found:
        return ("This Dataset folder has no CSV files to judge. Tell the person that. "
                "Do not ask them to attach a different file.")
    described = []
    for name, cols in found:
        described.append(f"{name} (columns: {', '.join(cols[:12])})" if cols else name)
    more = f" and {len(names) - 40} more" if len(names) > 40 else ""
    return ("This Dataset folder was attached without a single file. "
            f"CSV files: {'; '.join(described)}{more}. "
            "Call analyze_text again with path set to the file the question needs and "
            "text_column set to its text column. Do not ask the person to attach a file.")


def analyze(args: dict, turn) -> str:
    if turn.analyze_text_batch is None:
        return "Text analysis through the LLM Gateway is not available in this turn."
    if args.get("alias") and not args.get("model"):
        args = {**args, "model": args["alias"]}
    resolved = _resolve_coarse_attachment(args, turn)
    if isinstance(resolved, str):
        return resolved
    args = resolved

    text_column = args.get("text_column")
    id_column = args.get("id_column")
    if not isinstance(text_column, str) or not text_column:
        return "Choose the text column to analyze."
    if id_column is not None and not isinstance(id_column, str):
        return "The source ID column must be a column name or null."
    group_by = args.get("group_by") or None
    if group_by is not None and not isinstance(group_by, str):
        return "The group column must be a column name."

    labels = args.get("labels")
    if isinstance(labels, str):
        try:
            labels = json.loads(labels)
        except ValueError:
            pass
    if labels is not None:
        if (not isinstance(labels, list) or not labels
                or any(not isinstance(label, str) or not label for label in labels)):
            # Names what arrived, and never its values: this sentence is also the log line, and
            # without it six refusals in a row left no trace of what the model had sent.
            sent = ("an empty list" if labels == [] else
                    "a list with an empty or non-string item" if isinstance(labels, list) else
                    {str: "a string", dict: "an object"}.get(type(labels), type(labels).__name__))
            return ('Labels must be a non-empty JSON array of strings, such as ["yes", "no"]. '
                    f"This call sent {sent}.")
        labels = list(dict.fromkeys(labels))
    if group_by and not labels:
        return "Grouping counts judgments per group, so it needs labels. Pass labels, or omit group_by."
    output_field = args.get("output_field") or ("label" if labels else "summary")
    if not isinstance(output_field, str) or not output_field:
        return "The output field must be a string."
    batch_size = args.get("batch_size")
    if batch_size is None:
        batch_size = 20
    if type(batch_size) is not int or batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        return f"Batch size must be between 1 and {MAX_BATCH_SIZE}."
    concurrency = args.get("max_concurrency")
    if concurrency is None:
        concurrency = MAX_CONCURRENCY
    if type(concurrency) is not int or concurrency < 1 or concurrency > MAX_CONCURRENCY:
        return f"Concurrency must be between 1 and {MAX_CONCURRENCY}."
    row_limit = args.get("row_limit")
    if row_limit is not None and (type(row_limit) is not int or row_limit < 1):
        return (f"The row limit must be a positive integer, and this call sent "
                f"{json.dumps(row_limit)[:40]}. Omit row_limit to use every row.")

    serving = ""
    asked = str(args.get("model") or "").strip()
    if asked.lower() == "auto":
        asked = ""
    if asked:
        if turn.text_model_for is None:
            return f"{asked} could not be checked for this conversation, so no text was analyzed."
        serving, refused = turn.text_model_for(asked)
        if refused:
            return refused

    named = args.get("effort")
    if named is not None and not isinstance(named, str):
        return "The effort must be a reasoning level's name. Omit it for the default."
    named = (named or "").strip().lower()
    if named in ("", "auto", "automatic", "default"):
        named = None
    effort = None
    if turn.text_effort_for is not None:
        effort, refused = turn.text_effort_for(serving, named)
        if refused:
            return refused
    elif named:
        return f"The reasoning effort {named!r} could not be checked here, so no text was analyzed."

    loaded = _load(args, turn, text_column, id_column, row_limit)
    if isinstance(loaded, str):
        return loaded
    source, raw, columns, rows, watch = loaded
    if text_column not in columns or (id_column and id_column not in columns):
        return "The data must have the selected text column and source ID column."
    if group_by and group_by not in columns:
        return f"The data has no {group_by} column to group by. Select it beside the id and the text."
    if len(rows) > MAX_RECORDS and row_limit is None:
        if args.get("source") and args.get("sql"):
            return (f"This statement returned more than {MAX_RECORDS} records, above the analysis "
                    "limit. No sample was used. Narrow its WHERE to the candidate rows, or ask "
                    "for an explicitly labelled sample.")
        if args.get("source"):
            return (f"This table has more than {MAX_RECORDS} records, above the analysis limit. "
                    "No sample was used. Ask for an explicitly labelled sample or add a limit.")
        return (f"This CSV has {len(rows)} records, above the {MAX_RECORDS} record analysis limit. "
                "No sample was used. Ask for an explicitly labelled sample or add a limit.")

    source_sha = hashlib.sha256(raw).hexdigest()
    records, excluded = _manifest(rows, text_column, id_column, row_limit, group_by)
    if not records:
        return "No records with text were available to analyze. No result was saved."

    operation = "du_" + uuid4().hex
    leaf = source.rsplit("/", 1)[-1]
    stem = Path(leaf).stem if leaf.lower().endswith(".csv") else leaf.replace(".", "-")
    name = args.get("result_name") or f"{stem}-analysis-{operation[-8:]}"
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
                pool.submit(_run_batch, i, batch, args, output_field, labels, turn, serving,
                            effort)
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
    source_changed = _source_changed(watch, source_sha) if watch is not None else False
    if source_changed:
        unfinished = max(unfinished, len(records) - len(analyzed_rows) - failed)

    columns_out = ["Record ID", output_field]
    rows_out = analyzed_rows
    by_task = {r.task_id: r for r in records}
    saved_columns = ["Record ID", *([group_by] if group_by else []), output_field]
    saved_rows = [[by_task[task].source_id or task,
                   *([by_task[task].group] if group_by else []), value]
                  for task, value in rows_out]
    receipt = result.record(turn.examples_dir, name, str(args.get("title") or "Text analysis"),
                            saved_columns, saved_rows, keep_rows=turn.keep_rows)
    coverage = {
        "total": len(rows),
        "processed": 0 if source_changed else len(rows_out),
        "excluded": excluded,
        "failed": failed,
        "unfinished": (len(records) if source_changed else unfinished),
    }
    if group_by:
        selected_fields = [group_by, output_field, "records"]
        selected = _grouped(saved_rows)
    else:
        selected_fields = ["task_id", output_field]
        selected = _selected(rows_out, columns_out)
    event = {
        "operation_id": operation,
        "operation": "text_analysis",
        # Always drawn, for the reason `calculate` states: the turn was asked to do this
        # (ADR-0063, *What this does not decide*).
        "role": "answer",
        "source": source,
        "source_sha256": source_sha,
        "artifact": receipt.path,
        "columns": [c for c in [id_column, text_column, group_by] if c],
        "selected_fields": selected_fields,
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
    if args.get("source") and args.get("sql"):
        # The HASH, never the statement: this event is committed with the Thread's history, and a
        # literal in a WHERE is a row value (`run._statement`).
        event["statement_sha256"] = hashlib.sha256(
            str(args["sql"]).strip().encode()).hexdigest()
    reply = {
        "data_use": operation,
        "operation": "text_analysis",
        "columns": saved_columns,
        "result_rows": len(rows_out),
        "local_reference": receipt.path,
        "coverage": coverage,
        "selected_fields": selected_fields,
        "selected": selected,
        "kept_rows": receipt.kept,
    }
    if source_changed:
        reply["warning"] = "The source changed while analysis ran, so this is not a complete result."
    elif unjudged := failed + unfinished:
        # Measured 2026-09-28 (#606): told nothing when every batch failed, Gemini read the rows
        # itself with scripts and fed them to delegated_model_call, which ADR-0041 exists to stop.
        reply["warning"] = (
            f"{unjudged} of {len(records)} records were not judged: the model returned no usable "
            "answer for them. Their text must not pass through you, so do not read those rows "
            "yourself, script around this, or send them to delegated_model_call. Call this once "
            "more, or answer from the records that were judged and say how many were not.")
    if turn.record_data_use:
        turn.record_data_use(event, reply)
    if args.get("source") and args.get("sql") and rows_out and turn.remember_statement:
        turn.remember_statement(str(args["source"]), str(args["sql"]).strip())
    return json.dumps(reply)


def _read_csv(path: Path) -> tuple[bytes, list[str], list[dict[str, str]]]:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise csv.Error("too large")
    return raw, *_parse_csv(raw)


def _parse_csv(raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
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
    return columns, rows


def _manifest(rows: list[dict[str, str]], text_column: str, id_column: str | None,
              row_limit: int | None, group_by: str | None = None) -> tuple[list[Record], int]:
    records = []
    excluded = 0
    stop = row_limit or len(rows)
    for index, row in enumerate(rows[:stop], start=1):
        text = row.get(text_column, "").strip()
        if not text:
            excluded += 1
            continue
        source_id = row.get(id_column, "").strip() if id_column else ""
        group = row.get(group_by, "").strip() if group_by else ""
        records.append(Record(f"r{index:06d}", source_id, text[:MAX_TEXT_CHARS], index, group))
    excluded += max(0, len(rows) - stop)
    return records, excluded


def _run_batch(index: int, records: list[Record], args: dict, output_field: str,
               labels: list[str] | None, turn, serving: str = "",
               effort: str | None = None) -> BatchResult:
    request_id = "req_" + uuid4().hex
    evidence = {"request_id": request_id,
                "requested_alias": str(args.get("model") or "auto"),
                "serving_model": serving or None,
                "reasoning_effort": effort,
                "provider_receipt": "unknown",
                "cache": "unknown",
                "decision_stage": "unknown",
                "delivery": "unknown",
                "state": "attempted",
                "records": len(records)}
    last_error = ""
    for attempt in range(2):
        request = _request(serving or evidence["requested_alias"], records, args, output_field,
                           labels, effort)
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
             labels: list[str] | None, effort: str | None = None) -> dict[str, Any]:
    payload = {
        "task": str(args.get("purpose") or "Analyze each record."),
        "output_field": output_field,
        "records": [{"id": r.task_id, "text": r.text} for r in records],
    }
    if labels:
        payload["labels"] = labels
    instruction = ("Return only JSON with a records array. Each item must have id and "
                   f"{output_field}. Use each provided id exactly once. Do not add unknown ids.")
    request = {"model": model, "stream": True, "max_tokens": MAX_OUTPUT_TOKENS, "messages": [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
    ]}
    if effort is not None:
        request["reasoning_effort"] = effort
    return request


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


def _grouped(saved_rows: list[list[str]]) -> dict[str, Any]:
    """Judgments counted per group value and label, most first: what a SQL GROUP BY would hand
    back, so a turn can join judged text to its accounts without a record ID reaching it (#606)."""
    counts: dict[tuple[str, str], int] = {}
    for _record, group, value in saved_rows:
        counts[(group, value)] = counts.get((group, value), 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    groups = [[group, value, n] for (group, value), n in ordered]
    kept = len(groups)
    while kept and len(json.dumps(groups[:kept])) > result.VALUES_BUDGET_CHARS:
        kept //= 2
    return {"groups": groups[:kept], "total_groups": len(groups)}


def _source_changed(path: Path, expected_sha: str) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha
    except OSError:
        return True
