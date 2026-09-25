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
