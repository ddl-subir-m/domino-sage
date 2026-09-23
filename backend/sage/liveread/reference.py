"""Bounded preparation of explicitly referenced attachments.

This is the common policy boundary for reference material.  Authorization is an exact attachment
manifest match.  Extraction is typed.  The first handler is text/Markdown; later handlers must keep
the same authorization and Data-used contract rather than opening another file door.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SELECTED_CHARS = 8_000
MAX_SELECTOR_CHARS = 200

_TEXT_SUFFIXES = frozenset({".txt", ".text"})
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Authorized:
    """One exact manifest file and the local target it reaches."""

    source: str
    path: Path


@dataclass(frozen=True)
class Prepared:
    """A bounded typed result.  `text` is never written into the event metadata."""

    source: str
    source_type: str
    text: str
    requested_selector: str
    selected_selector: str
    source_bytes: int
    selected_characters: int
    sent_characters: int
    truncated: bool
    status: str
    source_sha256: str
    selected_sha256: str

    def prompt_block(self) -> str:
        if self.status != "prepared":
            return (
                f"Reference preparation for {self.source} did not transfer document content.\n"
                f"Status: {self.status}. {self.text}"
            )
        coverage = f"{self.sent_characters} of {self.selected_characters} characters"
        if self.selected_selector:
            coverage += f' from heading "{self.selected_selector}"'
        if self.truncated:
            coverage += "; truncated at the 8,000-character limit"
        return (
            f"Prepared reference text from {self.source} ({coverage}).\n"
            "Use this user-provided reference as requirements for this turn.\n"
            "--- BEGIN PREPARED REFERENCE ---\n"
            f"{self.text}\n"
            "--- END PREPARED REFERENCE ---"
        )


def authorize(root: Path, manifest: Iterable[dict], source: str,
              withheld: Iterable[str] = (), *,
              target_for: Callable[[dict], Path | None] | None = None) -> Authorized | None:
    """Resolve one exact manifest identity without granting a sibling or a folder."""
    authorized = _resolve(root, manifest, source, target_for=target_for)
    if authorized is None or _is_withheld(root, authorized, source, withheld):
        return None
    return authorized


def _resolve(root: Path, manifest: Iterable[dict], source: str, *,
             target_for: Callable[[dict], Path | None] | None = None) -> Authorized | None:
    root = Path(root)
    matches = [row for row in manifest if isinstance(row, dict)
               and source in (str(row.get("path") or ""),
                              str(root / str(row.get("path") or "")))]
    if len(matches) != 1:
        return None
    row = matches[0]
    rel = str(row.get("path") or "")
    posix = PurePosixPath(rel)
    if posix.is_absolute() or ".." in posix.parts or not posix.name:
        return None
    logical = root.joinpath(*posix.parts)
    # Check the path as written before following an intentional attachment symlink outside the app.
    lexical_root = Path(os.path.abspath(root))
    lexical = Path(os.path.abspath(logical))
    if not lexical.is_relative_to(lexical_root):
        return None
    try:
        target = logical.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not target.is_file():
        return None
    if target_for is None:
        # An ordinary manifest grants only a file that stays inside its root. Dataset attachments
        # are symlinks outside the app, so their caller must prove the exact trusted target.
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not target.is_relative_to(resolved_root):
            return None
    else:
        try:
            expected = target_for(row)
            expected = Path(expected).resolve(strict=True) if expected is not None else None
        except (OSError, RuntimeError):
            return None
        if expected is None or expected != target or not expected.is_file():
            return None
    return Authorized(rel, target)


def _is_withheld(root: Path, authorized: Authorized, source: str,
                 withheld: Iterable[str]) -> bool:
    hidden = set(withheld or ())
    identities = {
        source, authorized.source, str(Path(root) / authorized.source), str(authorized.path)
    }
    return any("file:" + identity in hidden for identity in identities)


def prepare(authorized: Authorized, *, selector: str = "", prompt: str = "") -> Prepared | None:
    """Dispatch an authorized file through its typed bounded handler.

    `None` means no core handler owns this type.  It is not permission to read it generically.
    """
    kind = source_type(authorized.path)
    if not kind:
        return None
    return _prepare_text(authorized, kind=kind, selector=selector, prompt=prompt)


def prepare_explicit(root: Path, manifest: Iterable[dict], sources: Iterable[str], *,
                     prompt: str, withheld: Iterable[str] = (),
                     target_for: Callable[[dict], Path | None] | None = None) -> list[Prepared]:
    """Prepare only exact structured file references, in mention order."""
    out: list[Prepared] = []
    seen: set[str] = set()
    rows = list(manifest)
    for source in sources:
        source = str(source)
        authorized = _resolve(root, rows, source, target_for=target_for)
        if authorized is None or authorized.source in seen:
            continue
        kind = source_type(authorized.path)
        if not kind:
            continue
        prepared = (
            _failure(authorized.source, kind, "withheld", "")
            if _is_withheld(root, authorized, source, withheld)
            else prepare(authorized, prompt=prompt)
        )
        if prepared is not None:
            out.append(prepared)
            seen.add(authorized.source)
    return out


def data_use(prepared: Prepared, *, purpose: str) -> tuple[dict, dict]:
    """Build the common content-free event and the in-memory model reply."""
    operation_id = "du_" + uuid4().hex
    unfinished = max(prepared.selected_characters - prepared.sent_characters, 0)
    coverage = {
        "total": prepared.selected_characters,
        "processed": prepared.sent_characters,
        "excluded": 0,
        "failed": 0 if prepared.status == "prepared" else 1,
        "unfinished": unfinished,
        "source_bytes": prepared.source_bytes,
        "selected_characters": prepared.selected_characters,
        "sent_characters": prepared.sent_characters,
        "truncated": prepared.truncated,
    }
    event = {
        "operation_id": operation_id,
        "operation": "document_reference",
        "source": prepared.source,
        "source_type": prepared.source_type,
        "requested_selector": prepared.requested_selector,
        "selected_selector": prepared.selected_selector,
        "coverage": coverage,
        "source_sha256": prepared.source_sha256,
        "selected_sha256": prepared.selected_sha256,
        "purpose": purpose,
        "status": prepared.status,
        "requests": [],
    }
    reply = {
        "data_use": operation_id,
        "source": prepared.source,
        "source_type": prepared.source_type,
        "selector": prepared.selected_selector or None,
        "coverage": coverage,
        "truncated": prepared.truncated,
        "status": prepared.status,
        # This is the deliberate transfer. DataUse keeps it in memory and persists only `event`.
        "selected": prepared.text,
    }
    return event, reply


def _prepare_text(authorized: Authorized, *, kind: str, selector: str, prompt: str) -> Prepared:
    requested = " ".join(str(selector or "").split())
    if len(requested) > MAX_SELECTOR_CHARS:
        return _failure(authorized.source, kind, "selector_too_long", "")
    try:
        size = authorized.path.stat().st_size
    except OSError:
        return _failure(authorized.source, kind, "unavailable", selector)
    if size > MAX_SOURCE_BYTES:
        return _failure(authorized.source, kind, "source_too_large", selector, source_bytes=size)
    try:
        raw = authorized.path.read_bytes()
    except OSError:
        return _failure(authorized.source, kind, "unavailable", selector, source_bytes=size)
    source_hash = hashlib.sha256(raw).hexdigest()
    if b"\x00" in raw:
        return _failure(authorized.source, kind, "not_text", selector,
                        source_bytes=size, source_sha256=source_hash)
    try:
        whole = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return _failure(authorized.source, kind, "not_text", selector,
                        source_bytes=size, source_sha256=source_hash)

    if kind == "text" and requested:
        return _failure(authorized.source, kind, "heading_not_supported", requested,
                        source_bytes=size, source_sha256=source_hash)
    selected_name = ""
    selected = whole
    if kind == "markdown":
        chosen = requested or _heading_named_in_prompt(whole, prompt)
        if chosen:
            section = _markdown_section(whole, chosen)
            if section is None:
                return _failure(authorized.source, kind, "heading_not_unique", requested or chosen,
                                source_bytes=size, source_sha256=source_hash)
            selected_name, selected = chosen, section

    selected_chars = len(selected)
    if not selected.strip():
        return _failure(authorized.source, kind, "empty_document", requested,
                        source_bytes=size, source_sha256=source_hash)
    sent = selected[:MAX_SELECTED_CHARS]
    return Prepared(
        source=authorized.source,
        source_type=kind,
        text=sent,
        requested_selector=requested,
        selected_selector=selected_name,
        source_bytes=size,
        selected_characters=selected_chars,
        sent_characters=len(sent),
        truncated=len(sent) < selected_chars,
        status="prepared",
        source_sha256=source_hash,
        selected_sha256=hashlib.sha256(selected.encode("utf-8")).hexdigest(),
    )


def _failure(source: str, kind: str, status: str, selector: str, *, source_bytes: int = 0,
             source_sha256: str = "") -> Prepared:
    messages = {
        "unavailable": "The referenced document is unavailable.",
        "source_too_large": "The referenced document exceeds the 8 MiB source limit.",
        "not_text": "The referenced file is not valid UTF-8 text.",
        "heading_not_unique": "The requested Markdown heading is missing or is not unique.",
        "heading_not_supported": "A heading can be selected only from a Markdown document.",
        "selector_too_long": "The requested heading exceeds the 200-character selector limit.",
        "empty_document": "The referenced document contains no text to transfer.",
        "withheld": "The person or administrator withheld this document from model requests.",
    }
    text = messages[status]
    return Prepared(source, kind, text, selector, "", source_bytes, 0, 0, False, status,
                    source_sha256, "")


def _headings(text: str) -> list[tuple[int, str, int, int]]:
    out = []
    for match in _HEADING.finditer(text):
        title = " ".join(match.group(2).split())
        if len(title) <= MAX_SELECTOR_CHARS:
            out.append((len(match.group(1)), title, match.start(), match.end()))
    return out


def _heading_named_in_prompt(text: str, prompt: str) -> str:
    haystack = " ".join(str(prompt or "").split())
    named: list[str] = []
    for _level, title, _start, _end in _headings(text):
        if re.search(rf"(?<!\w){re.escape(title)}(?!\w)", haystack, re.IGNORECASE):
            named.append(title)
    unique = {title.casefold(): title for title in named}
    return next(iter(unique.values())) if len(unique) == 1 else ""


def _markdown_section(text: str, selector: str) -> str | None:
    headings = _headings(text)
    matches = [(i, row) for i, row in enumerate(headings)
               if row[1].casefold() == selector.casefold()]
    if len(matches) != 1:
        return None
    index, (level, _title, start, _end) = matches[0]
    end = len(text)
    for next_level, _next_title, next_start, _next_end in headings[index + 1:]:
        if next_level <= level:
            end = next_start
            break
    return text[start:end].rstrip()


def source_type(path: Path | str) -> str:
    """Return the core handler name for a path, or empty when this wave does not own it."""
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix in _MARKDOWN_SUFFIXES:
        return "markdown"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    return ""
