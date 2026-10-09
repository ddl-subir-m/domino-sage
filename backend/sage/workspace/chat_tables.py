"""Validation of this Chat turn's tables, before row retention or publication (#349)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import timing
from . import table_shape


def _reject_constant(_value: str) -> None:
    # Python accepts NaN/Infinity by default; the browser's JSON reader does not.
    raise ValueError("non-JSON numeric constant")


def table_references(text: str) -> set[str]:
    """Explicit artifact paths, in file tokens, Markdown links or plain text."""
    return set(re.findall(r'examples/[^\s<>"`\])]+\.table\.json', text))


def validate_table_bytes(raw: bytes) -> str:
    """The same strict JSON and table contract at controlled write and publication."""
    if not raw.strip():
        return "empty file"
    try:
        body = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, UnicodeError):
        return "invalid JSON"
    return table_shape.validation_reason(body)


def failed_table_name(rel: str) -> str:
    """What to call a table that failed, in the words its card is captioned with (#435).

    `record_artifact` titles a card from the file's own name with the separators opened out, so a
    failed table named the same way is read against the cards on screen with words in common. The
    path is not used: it carries the Thread id, which means nothing to the person reading it and is
    the longest part of the string.
    """
    name = Path(rel).name
    stem = name.removesuffix(table_shape.SUFFIX)
    return stem.replace("-", " ").replace("_", " ").strip() or name


def redundant_tables(root: Path, thread_id: str, before: dict[str, bytes],
                     roles: dict[str, str]) -> list[str]:
    """This turn's new tables that hold no rows, or the same result as another one does (#726).

    Run before rows are withheld: a receipt keeps no rows, so two receipts cannot be compared and
    are left alone. Of a repeated result the one kept is the one that draws, then a Live read's card
    (its read is recorded against that path), then the first written.
    """
    def order(rel: str):
        path = root / rel
        return (roles.get(rel, "answer") == "working", rel not in roles,
                path.stat().st_mtime_ns if path.exists() else 0, rel)

    new = [p.relative_to(root).as_posix()
           for p in (root / "examples" / thread_id).rglob(f"*{table_shape.SUFFIX}")]
    seen: set[str] = set()
    out: list[str] = []
    for rel in sorted((r for r in new if r not in before), key=order):
        try:
            body = json.loads((root / rel).read_bytes())
        except (OSError, ValueError, UnicodeError):
            continue
        if isinstance(body, dict) and body.get("keptRows") is False:
            if body.get("rowCount") == 0:
                out.append(rel)
            continue
        rows = body if isinstance(body, list) else next(
            (body[k] for k in ("rows", "data", "records")
             if isinstance(body, dict) and isinstance(body.get(k), list)), None)
        if rows is None:
            continue
        if not rows:
            out.append(rel)
            continue
        columns, _ = table_shape.columns_and_count(body)
        key = json.dumps([[c.casefold() for c in columns], [_row_key(r) for r in rows]],
                         default=str, sort_keys=True)
        if key in seen:
            out.append(rel)
        seen.add(key)
    return out


def _row_key(row):
    """One row, with `158` and `158.0` the same number and a record's keys in no order."""
    def value(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    if isinstance(row, dict):
        return sorted((str(k).casefold(), value(v)) for k, v in row.items())
    return [value(v) for v in row] if isinstance(row, list) else value(row)


class ChatTables:
    """Keep repair candidates; unchanged valid files from prior turns are not repair targets."""

    def __init__(self, root: Path, thread_id: str, before: dict[str, bytes]):
        self.root, self.thread_id, self.before = root, thread_id, before
        self.candidates: set[str] = set()
        self.failures: dict[str, str] = {}
        self.write_failures: dict[str, str] = {}
        self.prior_paths: set[str] = set()
        self.references: set[str] = set()
        self.repair_ran = False

    def check(self, text: str = "") -> dict[str, str]:
        prefix = f"examples/{self.thread_id}/"
        # The tool route writes this map on its own thread. Read one snapshot per validation.
        rejected = self.write_failures.copy()
        paths = {p.relative_to(self.root).as_posix()
                 for p in (self.root / prefix).rglob(f"*{table_shape.SUFFIX}")}
        self.references.update(p for p in table_references(text)
                               if p.startswith(prefix) and ".." not in Path(p).parts)
        paths.update(self.references)
        invalid = {}
        for rel in sorted(paths | self.candidates | rejected.keys()):
            try:
                raw = (self.root / rel).read_bytes()
            except OSError:
                self.prior_paths.discard(rel)
                reason = rejected.get(rel, "missing or unreadable file")
            else:
                unchanged = self.before.get(rel) == raw
                if (unchanged and rel not in self.references
                        and rel not in self.candidates and rel not in rejected):
                    continue
                reason = rejected.get(rel) or validate_table_bytes(raw)
                if unchanged and not reason:
                    self.prior_paths.add(rel)
                else:
                    self.prior_paths.discard(rel)
            self.candidates.add(rel)
            if reason:
                invalid[rel] = reason
        self.failures.update(invalid)
        return invalid

    def repair_prompt(self, invalid: dict[str, str]) -> str:
        paths = "\n".join(f"- {p}: {reason}" for p, reason in invalid.items())
        return ("Repair these table artifacts needed for the current answer. "
                "This is the only repair attempt.\n"
                f"{paths}\n"
                "Read the files and fix their contents using the already authorized data. "
                "Check that each file contains valid table JSON. Keep valid sibling artifacts. "
                "Do not regenerate intentionally withheld rows or alter valid files from earlier turns. "
                "Do not substitute invented data. If a read or request is refused, stop. "
                "If the tables validate, the original answer will be kept; do not repeat it.")

    def diagnose(self, invalid: dict[str, str], outcome: str) -> None:
        for path, reason in self.failures.items():
            with timing.span("chat.table_validation", thread=self.thread_id, path=path,
                             reason=reason, repair_ran=self.repair_ran,
                             result=invalid.get(path, "valid"), outcome=outcome):
                pass
