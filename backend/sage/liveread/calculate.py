"""One authorized source snapshot, a local table, and an explicit model selection."""

import csv
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from ..orchestrator import brand
from . import grant, result

MAX_BYTES = 8 * 1024 * 1024


def _rows_from_dataset(args, turn):
    dataset = str(args.get("dataset") or "")
    rel = str(args.get("path") or "")
    if dataset == "upload":
        target = turn.upload_for(rel) if turn.upload_for else None
        if target is None:
            return None, "This source is not available in this conversation."
        return _read_csv_bytes(Path(target), rel)

    refused = grant.reachable("dataset", dataset,
                              bound=turn.bound.get("dataset", ()),
                              chips=turn.chips.get("dataset", ()))
    if refused:
        return None, refused.says
    if Path(rel).suffix.lower() != ".csv":
        return None, brand.text(
            "There is no CSV file at {path} in {name}. List the {dataset} first and name one it holds.",
            path=rel, name=dataset,
        )
    # `dataset_file` downloads this one path off the platform API and returns the local copy, or
    # None for anything it could not resolve — a bad path, an unreachable Dataset, a failed
    # download. One refusal covers all of those.
    target = turn.dataset_file(dataset, rel) if turn.dataset_file else None
    if target is None:
        return None, brand.text(
            "There is no CSV file at {path} in {name}. List the {dataset} first and name one it holds.",
            path=rel, name=dataset,
        )
    return _read_csv_bytes(target, f"{dataset}/{rel}")


def _read_csv_bytes(target: Path, source: str):
    try:
        with target.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        return None, "The CSV could not be calculated. Check its encoding and numeric column. No result was saved."
    if len(raw) > MAX_BYTES:
        return None, "This CSV exceeds the 8 MiB calculation limit. No sample was used."
    return (source, raw), ""


def _rows_from_table(args, turn, read_limit):
    name = str(args.get("source") or "")
    table = str(args.get("table") or "")
    refused = grant.reachable("datasource", name,
                              bound=turn.bound.get("datasource", ()),
                              chips=turn.chips.get("datasource", ()))
    if refused:
        return None, refused.says
    source = turn.source_for(name) if turn.source_for else None
    if source is None:
        return None, brand.text(
            "{assistantName} could not find {name} among the {dataSourcePlural} it can open here.",
            name=name or "that",
        )
    try:
        rows = turn.sample_rows(source, str(args.get("database") or ""),
                                str(args.get("schema") or ""), table, read_limit)
    except Exception:
        return None, "The table could not be calculated. The source did not answer. No result was saved."
    source_name = ".".join(p for p in [str(args.get("database") or ""),
                                       str(args.get("schema") or ""), table] if p)
    return (source_name or table, None, list(rows.columns), [list(r) for r in rows.rows]), ""


def calculate(args, turn):
    group = args.get("group_by")
    value = args.get("sum_column")
    selected = args.get("selected_fields")
    if selected is None:
        selected = []
    if (not isinstance(group, str) or not isinstance(value, str) or group == value
            or not isinstance(selected, list) or len(set(map(str, selected))) != len(selected)
            or any(field not in (group, value, "total") for field in selected)):
        return "Choose distinct group and numeric columns, and valid result fields."
    scope = args.get("row_limit")
    if scope is not None and (type(scope) is not int or scope < 1):
        return "The row limit must be a positive integer."
    read_limit = (scope or result.CAP_ROWS) + 1
    operation = "du_" + uuid4().hex
    source = str(args.get("path") or args.get("table") or "")
    name = args.get("result_name") or f"{Path(source).stem}-totals-{operation[-8:]}"
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        return "The result name must be one filename without a directory."
    output = turn.examples_dir / f"{name}.table.json"
    if (not turn.examples_dir.resolve().is_relative_to(turn.examples_dir.parent.parent.resolve())
            or output.is_symlink() or not output.resolve().is_relative_to(turn.examples_dir.resolve())):
        return "The result must stay in this conversation's Artifacts."
    try:
        if args.get("source"):
            loaded, refused = _rows_from_table(args, turn, read_limit)
            if refused:
                return refused
            source, raw, columns, source_rows = loaded
        else:
            loaded, refused = _rows_from_dataset(args, turn)
            if refused:
                return refused
            source, raw = loaded
            reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
            columns = next(reader, [])
            source_rows = [row for row in reader]
        if not columns or len(columns) != len(set(columns)) or group not in columns or value not in columns:
            return "The CSV must have unique column names and both selected columns."
        gi, vi = columns.index(group), columns.index(value)
        totals = {}
        count = processed = 0
        for row in source_rows:
            count += 1
            if len(row) != len(columns):
                return "A CSV row has the wrong number of fields. No result was saved."
            if scope is not None and count > scope:
                continue
            if scope is None and count > result.CAP_ROWS:
                return "The result exceeds the 500 row calculation limit. No partial result was saved."
            number = Decimal(row[vi])
            if not number.is_finite():
                raise InvalidOperation
            totals[row[gi]] = totals.get(row[gi], Decimal(0)) + number
            processed += 1
        if len(totals) > result.CAP_ROWS:
            return "The result exceeds 500 groups. No partial result was saved."
        rows = [[key, str(number)] for key, number in totals.items()]
        total = str(sum(totals.values(), Decimal(0)))
        values = {"rows": [[row[(group, value).index(field)] for field in selected if field != "total"]
                           for row in rows]} if any(field != "total" for field in selected) else {}
        if "total" in selected:
            values["total"] = total
        if len(json.dumps(values)) > result.VALUES_BUDGET_CHARS:
            return "The selected result exceeds the model output limit. Select fewer fields. No sample was used."
    except (OSError, UnicodeError, csv.Error, InvalidOperation):
        return "The CSV could not be calculated. Check its encoding and numeric column. No result was saved."
    receipt = result.record(turn.examples_dir, name, str(args.get("title") or "CSV totals"),
                            [group, value], rows, keep_rows=turn.keep_rows)
    unfinished = 1 if args.get("source") and scope is not None and count > processed else 0
    event = {
        "operation_id": operation, "source": source,
        "source_sha256": hashlib.sha256(raw or json.dumps([columns, source_rows]).encode()).hexdigest(),
        # A calculation is something the turn was ASKED to do, so its card always draws (ADR-0063,
        # *What this does not decide*). The field is on every operation so the event shape stays
        # uniform; only the read lanes compute it.
        "role": "answer",
        "artifact": receipt.path, "columns": [group, value], "selected_fields": selected,
        "result_rows": len(rows),
        "coverage": {"total": count, "processed": processed, "excluded": count - processed,
                     "failed": 0, "unfinished": unfinished},
        "purpose": str(args.get("purpose") or "Calculate CSV totals"),
        "requests": [], "delivery": "unknown",
    }
    reply = {"data_use": operation, "columns": [group, value], "result_rows": len(rows),
             "local_reference": receipt.path, "coverage": event["coverage"],
             "selected_fields": selected, "selected": values,
             "kept_rows": receipt.kept}
    if turn.record_data_use:
        turn.record_data_use(event, reply)
    return json.dumps(reply)
