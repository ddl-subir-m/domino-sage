"""One authorized CSV snapshot, a local table, and an explicit model selection."""

import csv
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from . import result

MAX_BYTES = 8 * 1024 * 1024


def calculate(args, turn):
    if not turn.data_use_enabled:
        return "CSV calculation is available in new projects only."
    source = str(args.get("path") or "")
    target = turn.upload_for(source) if turn.upload_for else None
    if target is None:
        return "This source is not available in this conversation."
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
    operation = "du_" + uuid4().hex
    name = args.get("result_name") or f"{Path(source).stem}-totals-{operation[-8:]}"
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        return "The result name must be one filename without a directory."
    output = turn.examples_dir / f"{name}.table.json"
    if (not turn.examples_dir.resolve().is_relative_to(turn.examples_dir.parent.parent.resolve())
            or output.is_symlink() or not output.resolve().is_relative_to(turn.examples_dir.resolve())):
        return "The result must stay in this conversation's Artifacts."
    try:
        with Path(target).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return "This CSV exceeds the 8 MiB calculation limit. No sample was used."
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        columns = next(reader, [])
        if not columns or len(columns) != len(set(columns)) or group not in columns or value not in columns:
            return "The CSV must have unique column names and both selected columns."
        gi, vi = columns.index(group), columns.index(value)
        totals = {}
        count = processed = 0
        for row in reader:
            count += 1
            if len(row) != len(columns):
                return "A CSV row has the wrong number of fields. No result was saved."
            if scope is not None and count > scope:
                continue
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
    event = {
        "operation_id": operation, "source": source,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "artifact": receipt.path, "columns": [group, value], "selected_fields": selected,
        "result_rows": len(rows),
        "coverage": {"total": count, "processed": processed, "excluded": count - processed,
                     "failed": 0, "unfinished": 0},
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
