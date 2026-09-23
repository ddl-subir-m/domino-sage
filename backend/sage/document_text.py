"""Text extraction shared by attachment descriptors and bounded reference preparation."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


class DocumentTextError(ValueError):
    """A content-free reason why a document could not be extracted safely."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class DocxText:
    paragraphs: tuple[str, ...]
    tables: tuple[tuple[str, ...], ...]

    def render(self) -> str:
        lines = ["Paragraphs:", *self.paragraphs] if self.paragraphs else [
            "No paragraphs outside tables."
        ]
        if self.tables:
            lines.append(
                f"Tables ({len(self.tables)}), one row per line, cells separated by ' | ':"
            )
            for number, rows in enumerate(self.tables, 1):
                lines.append(f"[table {number}, {len(rows)} rows]")
                lines.extend(rows)
        return "\n".join(lines)


@dataclass(frozen=True)
class PdfText:
    page_count: int
    selected_pages: tuple[int, ...]
    pages: tuple[int, ...]
    texts: tuple[str, ...]
    outline_titles: tuple[str, ...]
    pages_truncated: bool

    @property
    def extracted_characters(self) -> int:
        return sum(len(text) for text in self.texts)

    def render(self) -> str:
        return "\n\n".join(
            f"Page {page}:\n{text.strip()}" for page, text in zip(self.pages, self.texts)
            if text.strip()
        )


def extract_docx(source, *, max_xml_bytes: int) -> DocxText:
    """Extract Word paragraphs and table rows after bounding the uncompressed XML."""
    try:
        with zipfile.ZipFile(_zip_source(source)) as archive:
            matches = [info for info in archive.infolist() if info.filename == "word/document.xml"]
            if len(matches) != 1:
                raise DocumentTextError("malformed_document")
            info = matches[0]
            if info.flag_bits & 0x1:
                raise DocumentTextError("encrypted_document")
            if info.file_size > max_xml_bytes:
                raise DocumentTextError("document_xml_too_large")
            with archive.open(info) as handle:
                raw = handle.read(max_xml_bytes + 1)
            if len(raw) > max_xml_bytes:
                raise DocumentTextError("document_xml_too_large")
    except DocumentTextError:
        raise
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise DocumentTextError("malformed_document") from error

    try:
        root = ElementTree.fromstring(raw)
    except (ElementTree.ParseError, UnicodeError, ValueError) as error:
        raise DocumentTextError("malformed_document") from error

    paragraphs: list[str] = []
    tables: list[tuple[str, ...]] = []

    def walk(element) -> None:
        local = _local_name(element.tag)
        if local == "tbl":
            rows: list[str] = []
            for row in (item for item in element.iter() if _local_name(item.tag) == "tr"):
                cells = [_element_text(cell) for cell in row
                         if _local_name(cell.tag) == "tc"]
                if any(cells):
                    rows.append(" | ".join(cells))
            tables.append(tuple(rows))
            return
        if local == "p":
            text = _element_text(element)
            if text:
                paragraphs.append(text)
            return
        for child in element:
            walk(child)

    walk(root)
    return DocxText(tuple(paragraphs), tuple(tables))


def extract_pdf(source, *, pages=None, max_pages: int = 20,
                max_characters: int | None = None, include_outline: bool = False) -> PdfText:
    """Extract a validated bounded set of one-based PDF pages."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(_binary_source(source))
        if reader.is_encrypted:
            raise DocumentTextError("encrypted_document")
        page_count = len(reader.pages)
        selected_pages, pages_truncated = _pdf_pages(pages, page_count, max_pages)
        processed: list[int] = []
        texts: list[str] = []
        extracted_characters = 0
        for page in selected_pages:
            text = reader.pages[page - 1].extract_text() or ""
            processed.append(page)
            texts.append(text)
            extracted_characters += len(text)
            if max_characters is not None and extracted_characters >= max_characters:
                break
        outline_titles = _outline_titles(reader) if include_outline else ()
    except DocumentTextError:
        raise
    except Exception as error:
        raise DocumentTextError("malformed_document") from error
    return PdfText(page_count, selected_pages, tuple(processed), tuple(texts), outline_titles,
                   pages_truncated or tuple(processed) != selected_pages)


def _zip_source(source):
    return io.BytesIO(source) if isinstance(source, bytes) else source


def _binary_source(source):
    if isinstance(source, bytes):
        return io.BytesIO(source)
    return str(Path(source)) if isinstance(source, Path) else source


def _local_name(tag) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _element_text(element) -> str:
    pieces = [str(item.text or "") for item in element.iter()
              if _local_name(item.tag) == "t"]
    return " ".join("".join(pieces).split())


def _pdf_pages(pages, page_count: int, max_pages: int) -> tuple[tuple[int, ...], bool]:
    if pages is None:
        selected = tuple(range(1, min(page_count, max_pages) + 1))
        return selected, page_count > max_pages
    unique: set[int] = set()
    try:
        for page in pages:
            if isinstance(page, bool) or not isinstance(page, int):
                raise DocumentTextError("invalid_page_selection")
            unique.add(page)
            if len(unique) > max_pages:
                raise DocumentTextError("too_many_pages")
    except TypeError as error:
        raise DocumentTextError("invalid_page_selection") from error
    selected = tuple(sorted(unique))
    if not selected:
        raise DocumentTextError("invalid_page_selection")
    if selected[0] < 1 or selected[-1] > page_count:
        raise DocumentTextError("page_out_of_range")
    return selected, False


def _outline_titles(reader) -> tuple[str, ...]:
    out: list[str] = []

    def walk(items) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item)
            else:
                title = getattr(item, "title", None)
                if title:
                    out.append(str(title))

    try:
        walk(reader.outline)
    except Exception:
        return ()
    return tuple(out)
