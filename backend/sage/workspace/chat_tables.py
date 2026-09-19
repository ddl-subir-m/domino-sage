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


def without_failed_tables(text: str, failed: set[str]) -> str:
    """Drop failed file offers and table-ready claims; keep the surrounding answer."""
    if not failed:
        return text
    # Remove references, not whole sentences: a total and a valid chart can share that sentence.
    links = r"!?\[[^\]\n]*\]\([^\n)]*\)|\[file:[^\]\n]*\]|`[^`\n]*`"
    text = re.sub(links, lambda m: "" if any(p in m[0] for p in failed) else m[0], text)
    for path in failed:
        text = text.replace(path, "")
    offer = (r"\b(?:and\s+)?(?:(?:the|your|this|a)\s+)?tables?\s+"
             r"(?:(?:is|are|was|were|has been|have been)\s+)?(?:now\s+)?"
             r"(?:ready|generated|created|saved|attached|available|complete|below|above)\b"
             r"|\b(?:and\s+)?(?:I|we)\s+(?:have\s+)?(?:created|generated|saved|attached|wrote)\s+"
             r"(?:(?:the|your|this|a)\s+)?tables?\b"
             r"|\b(?:here is|here's|here are|see|open|download)\s+(?:the\s+)?tables?\b")
    text = re.sub(r"(?:" + offer + r")\s*[:.!]?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:;\s*)?\btable\s*:\s*(?=$|[.!?;])", "", text, flags=re.IGNORECASE)
    return text.strip(" ;\n")


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
    """One turn's candidates survive deletion during repair; old files are never repair targets."""

    def __init__(self, root: Path, thread_id: str, before: dict[str, bytes] | None):
        """`before` is this turn's baseline, or `None` when no snapshot was taken (#419).

        `None` is not `{}`, and the difference is the whole of #419's hazard. `self.before` is what
        marks a file as THIS turn's candidate, so an empty baseline says every pre-existing table
        was written now and offers all of them up for repair. `None` says the opposite, and says it
        truthfully: the turn held no tool that could write, so nothing on disk is its work.
        """
        self.root, self.thread_id, self.before = root, thread_id, before
        self.candidates: set[str] = set()
        self.failures: dict[str, str] = {}
        self.prior_paths: set[str] = set()
        self.references: set[str] = set()
        self.repair_ran = False

    def check(self, text: str = "") -> dict[str, str]:
        prefix = f"examples/{self.thread_id}/"
        paths = {p.relative_to(self.root).as_posix()
                 for p in (self.root / prefix).rglob(f"*{table_shape.SUFFIX}")}
        self.references.update(p for p in table_references(text)
                               if p.startswith(prefix) and ".." not in Path(p).parts)
        paths.update(self.references)
        invalid = {}
        for rel in sorted(paths | self.candidates):
            try:
                raw = (self.root / rel).read_bytes()
            except OSError:
                self.prior_paths.discard(rel)
                reason = "missing or unreadable file"
            else:
                # A `None` baseline means the turn could not write, so every file here is unchanged
                # from before it — the same answer a real baseline gives for a file the turn left
                # alone. References still validate below: the model can name a table in prose
                # without holding a tool to make one, and a name with nothing under it is the thing
                # this pass exists to catch.
                if self.before is None or self.before.get(rel) == raw:
                    if rel not in self.references and rel not in self.candidates:
                        continue
                    self.prior_paths.add(rel)
                else:
                    self.prior_paths.discard(rel)
                if not raw.strip():
                    reason = "empty file"
                else:
                    try:
                        body = json.loads(raw, parse_constant=_reject_constant)
                    except (ValueError, UnicodeError):
                        reason = "invalid JSON"
                    else:
                        reason = table_shape.validation_reason(body)
            self.candidates.add(rel)
            if reason:
                invalid[rel] = reason
        self.failures.update(invalid)
        return invalid

    def repair_prompt(self, invalid: dict[str, str]) -> str:
        paths = "\n".join(f"- {p}: {reason}" for p, reason in invalid.items())
        return ("Repair these table artifacts from this turn. This is the only repair attempt.\n"
                f"{paths}\n"
                "Read the files and fix their contents using the already authorized data. "
                "Check that each file contains valid table JSON. Keep valid sibling artifacts. "
                "Do not regenerate intentionally withheld rows or alter unchanged files from earlier turns. "
                "Do not substitute invented data. If a read or request is refused, stop. "
                "The original answer will be kept; do not repeat it.")

    def diagnose(self, invalid: dict[str, str], outcome: str) -> None:
        for path, reason in self.failures.items():
            with timing.span("chat.table_validation", thread=self.thread_id, path=path,
                             reason=reason, repair_ran=self.repair_ran,
                             result=invalid.get(path, "valid"), outcome=outcome):
                pass
