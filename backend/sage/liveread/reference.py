"""Bounded preparation of explicitly referenced attachments.

This is the common policy boundary for reference material. Authorization is an exact attachment
manifest match. Extraction is typed, and every handler keeps the same authorization and Data-used
contract rather than opening another file door.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..document_text import DocumentTextError, extract_docx, extract_pdf

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SELECTED_CHARS = 8_000
MAX_SELECTOR_CHARS = 200
MAX_PDF_PAGES = 20

_TEXT_SUFFIXES = frozenset({".txt", ".text"})
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_DOCX_SUFFIXES = frozenset({".docx"})
_PDF_SUFFIXES = frozenset({".pdf"})
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLANNING_FAILURES = frozenset({
    "unavailable", "source_too_large", "not_text", "heading_not_unique",
    "heading_not_supported", "selector_too_long", "empty_document", "withheld",
    "malformed_document", "encrypted_document", "document_xml_too_large",
    "no_extractable_text", "invalid_page_selection", "too_many_pages",
    "page_out_of_range", "page_selection_not_supported", "extraction_unavailable",
})


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
    source_pages: int = 0
    selected_pages: tuple[int, ...] = ()
    processed_pages: tuple[int, ...] = ()
    pages_truncated: bool = False
    extracted_characters: int = 0

    def prompt_block(self) -> str:
        if self.status != "prepared":
            return (
                f"Reference preparation for {self.source} did not transfer document content.\n"
                f"Status: {self.status}. {self.text}"
            )
        coverage = f"{self.sent_characters} of {self.selected_characters} characters"
        if self.source_type == "pdf" and self.processed_pages:
            coverage += f" from pages {', '.join(str(page) for page in self.processed_pages)}"
        elif self.selected_selector:
            coverage += f' from heading "{self.selected_selector}"'
        if self.sent_characters < self.selected_characters:
            coverage += "; truncated at the 8,000-character limit"
        if self.pages_truncated:
            coverage += "; page coverage stopped at the 20-page or 8,000-character bound"
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


def prepare(authorized: Authorized, *, selector: str = "", prompt: str = "",
            pages: Iterable[int] | None = None) -> Prepared | None:
    """Dispatch an authorized file through its typed bounded handler.

    `None` means no core handler owns this type.  It is not permission to read it generically.
    """
    kind = source_type(authorized.path)
    if not kind:
        return None
    requested = " ".join(str(selector or "").split())
    if len(requested) > MAX_SELECTOR_CHARS:
        return _failure(authorized.source, kind, "selector_too_long", "")
    if kind in {"text", "markdown"}:
        if pages is not None:
            return _failure(authorized.source, kind, "page_selection_not_supported", requested)
        return _prepare_text(authorized, kind=kind, selector=requested, prompt=prompt)
    if kind == "docx":
        if requested:
            return _failure(authorized.source, kind, "heading_not_supported", requested)
        if pages is not None:
            return _failure(authorized.source, kind, "page_selection_not_supported", requested)
        return _prepare_docx(authorized)
    if requested:
        return _failure(authorized.source, kind, "heading_not_supported", requested)
    return _prepare_pdf(authorized, pages=pages)


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


def plan_record(prepared: Prepared) -> dict:
    """The content-free identity needed to prepare this exact reference again."""
    return {
        "source": prepared.source,
        "handler": prepared.source_type,
        # A failed selection has no selected selector. The attempted selector is what makes approval
        # repeat the same bounded failure instead of silently widening to the whole document.
        "selector": prepared.requested_selector or prepared.selected_selector,
        "sha256": prepared.source_sha256,
        "status": prepared.status,
    }


def plan_records(value: object) -> list[dict]:
    """Read durable plan records without trusting extra or malformed metadata.

    Plans written before reference persistence have no records and therefore return an empty list.
    """
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "")
        handler = str(raw.get("handler") or "")
        selector = " ".join(str(raw.get("selector") or "").split())
        digest = str(raw.get("sha256") or "").casefold()
        kind = source_type(source)
        if not source or source in seen or not kind:
            continue
        valid_digest = _SHA256.fullmatch(digest) is not None
        raw_status = str(raw.get("status") or "")
        # Records written by the first #517 revision have no status. They remain transferable only
        # when they have the complete digest that revision promised. Missing or malformed evidence
        # becomes a visible bounded failure, never permission to use today's bytes.
        status = raw_status or ("prepared" if valid_digest else "saved_record_invalid")
        if (handler != kind or len(selector) > MAX_SELECTOR_CHARS
                or (digest and not valid_digest)
                or (status == "prepared" and not valid_digest)
                or status not in _PLANNING_FAILURES | {"prepared"}):
            status = "saved_record_invalid"
        out.append({"source": source, "handler": kind,
                    "selector": selector if len(selector) <= MAX_SELECTOR_CHARS else "",
                    "sha256": digest if valid_digest else "", "status": status})
        seen.add(source)
    return out


def prepare_plan_records(root: Path, manifest: Iterable[dict], records: object, *,
                         withheld: Iterable[str] = (),
                         target_for: Callable[[dict], Path | None] | None = None) -> list[Prepared]:
    """Re-authorize and re-prepare only the references saved with an approved plan."""
    rows = list(manifest)
    out: list[Prepared] = []
    for record in plan_records(records):
        source = record["source"]
        kind = record["handler"]
        selector = record["selector"]
        authorized = _resolve(root, rows, source, target_for=target_for)
        if authorized is None:
            out.append(_failure(source, kind, "not_authorized", selector))
            continue
        if _is_withheld(root, authorized, source, withheld):
            out.append(_failure(source, kind, "withheld", selector))
            continue
        if record["status"] != "prepared":
            out.append(_failure(source, kind, record["status"], selector))
            continue
        prepared = prepare(authorized, selector=selector)
        if prepared is None:
            out.append(_failure(source, kind, "handler_changed", selector))
            continue
        expected = record["sha256"]
        if prepared.source_sha256 != expected:
            out.append(_failure(source, kind, "source_changed", selector,
                                source_bytes=prepared.source_bytes,
                                source_sha256=prepared.source_sha256))
            continue
        out.append(prepared)
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
    if prepared.source_pages:
        coverage.update({
            "source_pages": prepared.source_pages,
            "selected_pages": list(prepared.selected_pages),
            "processed_pages": list(prepared.processed_pages),
            "pages_truncated": prepared.pages_truncated,
            "extracted_characters": prepared.extracted_characters,
        })
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
        "pages": list(prepared.processed_pages) or None,
        "coverage": coverage,
        "truncated": prepared.truncated,
        "status": prepared.status,
        # This is the deliberate transfer. DataUse keeps it in memory and persists only `event`.
        "selected": prepared.text,
    }
    return event, reply


def _prepare_text(authorized: Authorized, *, kind: str, selector: str, prompt: str) -> Prepared:
    requested = selector
    try:
        size = authorized.path.stat().st_size
    except OSError:
        return _failure(authorized.source, kind, "unavailable", requested)
    if size > MAX_SOURCE_BYTES:
        return _failure(authorized.source, kind, "source_too_large", requested, source_bytes=size)
    try:
        raw = authorized.path.read_bytes()
    except OSError:
        return _failure(authorized.source, kind, "unavailable", requested, source_bytes=size)
    source_hash = hashlib.sha256(raw).hexdigest()
    if b"\x00" in raw:
        return _failure(authorized.source, kind, "not_text", requested,
                        source_bytes=size, source_sha256=source_hash)
    try:
        whole = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return _failure(authorized.source, kind, "not_text", requested,
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


def _prepare_docx(authorized: Authorized) -> Prepared:
    loaded = _load_source(authorized, "docx")
    if isinstance(loaded, Prepared):
        return loaded
    raw, size, source_hash = loaded
    try:
        extracted = extract_docx(raw, max_xml_bytes=MAX_SOURCE_BYTES)
    except DocumentTextError as error:
        return _failure(authorized.source, "docx", error.status, "", source_bytes=size,
                        source_sha256=source_hash)
    return _prepared_text(authorized.source, "docx", extracted.render(), size, source_hash)


def _prepare_pdf(authorized: Authorized, *, pages: Iterable[int] | None) -> Prepared:
    loaded = _load_source(authorized, "pdf")
    if isinstance(loaded, Prepared):
        return loaded
    raw, size, source_hash = loaded
    try:
        extracted = extract_pdf(
            raw, pages=pages, max_pages=MAX_PDF_PAGES, max_characters=MAX_SELECTED_CHARS
        )
    except ImportError:
        return _failure(authorized.source, "pdf", "extraction_unavailable", "",
                        source_bytes=size, source_sha256=source_hash)
    except DocumentTextError as error:
        return _failure(authorized.source, "pdf", error.status, "", source_bytes=size,
                        source_sha256=source_hash)
    page_count = extracted.page_count
    selected_pages = extracted.selected_pages
    selected = extracted.render()
    if not selected.strip():
        return _failure(authorized.source, "pdf", "no_extractable_text", "",
                        source_bytes=size, source_sha256=source_hash,
                        source_pages=page_count, selected_pages=selected_pages)
    return _prepared_text(
        authorized.source, "pdf", selected, size, source_hash,
        source_pages=page_count, selected_pages=selected_pages, processed_pages=extracted.pages,
        pages_truncated=extracted.pages_truncated,
        extracted_characters=extracted.extracted_characters,
    )


def _load_source(authorized: Authorized, kind: str) -> tuple[bytes, int, str] | Prepared:
    try:
        size = authorized.path.stat().st_size
    except OSError:
        return _failure(authorized.source, kind, "unavailable", "")
    if size > MAX_SOURCE_BYTES:
        return _failure(authorized.source, kind, "source_too_large", "", source_bytes=size)
    try:
        raw = authorized.path.read_bytes()
    except OSError:
        return _failure(authorized.source, kind, "unavailable", "", source_bytes=size)
    return raw, size, hashlib.sha256(raw).hexdigest()


def _prepared_text(source: str, kind: str, selected: str, size: int, source_hash: str, *,
                   source_pages: int = 0, selected_pages: tuple[int, ...] = (),
                   processed_pages: tuple[int, ...] = (), pages_truncated: bool = False,
                   extracted_characters: int = 0) -> Prepared:
    selected_chars = len(selected)
    if not selected.strip():
        return _failure(source, kind, "empty_document", "", source_bytes=size,
                        source_sha256=source_hash, source_pages=source_pages,
                        selected_pages=selected_pages)
    sent = selected[:MAX_SELECTED_CHARS]
    return Prepared(
        source=source, source_type=kind, text=sent, requested_selector="",
        selected_selector="", source_bytes=size, selected_characters=selected_chars,
        sent_characters=len(sent), truncated=(len(sent) < selected_chars or pages_truncated),
        status="prepared", source_sha256=source_hash,
        selected_sha256=hashlib.sha256(selected.encode("utf-8")).hexdigest(),
        source_pages=source_pages, selected_pages=selected_pages,
        processed_pages=processed_pages, pages_truncated=pages_truncated,
        extracted_characters=extracted_characters,
    )


def _failure(source: str, kind: str, status: str, selector: str, *, source_bytes: int = 0,
             source_sha256: str = "", source_pages: int = 0,
             selected_pages: tuple[int, ...] = ()) -> Prepared:
    messages = {
        "unavailable": "The referenced document is unavailable.",
        "source_too_large": "The referenced document exceeds the 8 MiB source limit.",
        "not_text": "The referenced file is not valid UTF-8 text.",
        "heading_not_unique": "The requested Markdown heading is missing or is not unique.",
        "heading_not_supported": "A heading can be selected only from a Markdown document.",
        "selector_too_long": "The requested heading exceeds the 200-character selector limit.",
        "empty_document": "The referenced document contains no text to transfer.",
        "withheld": "The person or administrator withheld this document from model requests.",
        "malformed_document": "The referenced document is malformed or corrupt.",
        "encrypted_document": "The referenced document is encrypted and cannot be prepared.",
        "document_xml_too_large": "The Word document XML exceeds the 8 MiB extraction limit.",
        "no_extractable_text": "The PDF has no extractable text. OCR is not available.",
        "invalid_page_selection": "PDF pages must be a non-empty list of one-based integers.",
        "too_many_pages": "A PDF reference can select at most 20 unique pages.",
        "page_out_of_range": "The PDF page selection is outside the document.",
        "page_selection_not_supported": "Page selection is supported only for PDF documents.",
        "extraction_unavailable": "PDF text extraction is unavailable in this Sage runtime.",
        "not_authorized": "The saved reference is unavailable or is no longer authorized.",
        "handler_changed": "The saved reference no longer has its approved content type.",
        "source_changed": "The referenced document changed after the plan was prepared.",
        "saved_record_invalid": (
            "The saved reference record is incomplete or invalid, so no document content was sent."
        ),
    }
    text = messages[status]
    return Prepared(source, kind, text, selector, "", source_bytes, 0, 0, False, status,
                    source_sha256, "", source_pages, selected_pages)


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
    if suffix in _DOCX_SUFFIXES:
        return "docx"
    if suffix in _PDF_SUFFIXES:
        return "pdf"
    return ""
