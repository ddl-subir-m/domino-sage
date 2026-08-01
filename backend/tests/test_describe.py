"""Bounded, typed attachment descriptors — the replacement for the raw utf-8 head.

The load-bearing case is binary: a PDF or PNG used to decode to mojibake that got inlined into the
prompt as a "SCHEMA SAMPLE". Several tests below assert that never happens again."""
from __future__ import annotations

import builtins
import json
import struct
import zipfile
from pathlib import Path

from sage.orchestrator.describe import describe, fit_image

REPLACEMENT = "�"


def _png(tmp: Path, w: int = 1920, h: int = 1080) -> Path:
    p = tmp / "shot.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
                  + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00" + bytes(400))
    return p


def _pdf(tmp: Path) -> Path:
    p = tmp / "report.pdf"
    p.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + bytes(range(256)) * 4)
    return p


def _csv(tmp: Path, rows: int = 500) -> Path:
    p = tmp / "sales.csv"
    body = "\n".join(f"{i},{i * 1.5},true,2026-01-0{i % 9 + 1},widget-{i}" for i in range(rows))
    p.write_text("qty,price,active,ordered_on,sku\n" + body + "\n")
    return p


def test_a_csv_reports_its_columns_inferred_types_and_row_count(tmp_path: Path):
    d = describe(str(_csv(tmp_path, rows=500)))
    assert d["kind"] == "tabular"
    assert d["summary"] == "CSV — 5 columns, 500 rows"
    for col in ("qty", "price", "active", "ordered_on", "sku"):
        assert col in d["detail"]
    assert "qty: int" in d["detail"]
    assert "price: float" in d["detail"]
    assert "active: bool" in d["detail"]
    assert "ordered_on: date" in d["detail"]
    assert "sku: string" in d["detail"]
    assert "500 data rows" in d["detail"]


def test_a_tsv_is_detected_and_labelled_separately_from_csv(tmp_path: Path):
    p = tmp_path / "t.tsv"
    p.write_text("a\tb\n1\t2\n3\t4\n")
    d = describe(str(p))
    assert d["kind"] == "tabular"
    assert d["summary"] == "TSV — 2 columns, 2 rows"


def test_a_pdf_is_never_decoded_into_the_prompt_as_text(tmp_path: Path):
    # The actual bug being fixed: a PDF head used to be utf-8-decoded with errors="replace" and
    # inlined as a schema sample. It must now be classified as a PDF with no mojibake at all.
    d = describe(str(_pdf(tmp_path)))
    assert d["kind"] == "pdf"
    assert d["kind"] not in ("tabular", "text")
    assert REPLACEMENT not in d["detail"]
    assert REPLACEMENT not in d["summary"]


def test_a_png_is_described_by_its_dimensions_and_never_by_its_bytes(tmp_path: Path):
    d = describe(str(_png(tmp_path)))
    assert d["kind"] == "image"
    assert d["kind"] not in ("tabular", "text")
    assert d["summary"] == "PNG image — 1920x1080"
    assert REPLACEMENT not in d["detail"]
    assert "1920x1080" in d["detail"]


def test_an_unrecognized_binary_says_so_instead_of_emitting_decoded_bytes(tmp_path: Path):
    p = tmp_path / "blob.dat"
    p.write_bytes(bytes(range(256)) * 20)
    d = describe(str(p))
    assert d["kind"] == "binary"
    assert REPLACEMENT not in d["detail"]
    assert "NOT previewed" in d["detail"]


def test_a_json_document_yields_a_schema_of_key_paths_and_never_its_values(tmp_path: Path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"name": "acme", "count": 3,
                             "nested": {"flag": True, "ratio": 0.5},
                             "items": [{"id": 1, "label": "secret-value"}]}))
    d = describe(str(p))
    assert d["kind"] == "json"
    assert d["summary"] == "JSON — object, 4 top-level keys"
    assert "name: string" in d["detail"]
    assert "nested.flag: bool" in d["detail"]
    assert "items[].id: int" in d["detail"]
    assert "secret-value" not in d["detail"]        # schema only, never values
    assert "acme" not in d["detail"]


def test_newline_delimited_json_is_recognized_and_counted(tmp_path: Path):
    p = tmp_path / "events.ndjson"
    p.write_text("".join(json.dumps({"id": i, "ok": True}) + "\n" for i in range(120)))
    d = describe(str(p))
    assert d["kind"] == "json"
    assert d["summary"].startswith("NDJSON — 120 records")
    assert "id: int" in d["detail"]


def test_a_json_schema_is_depth_limited_so_deep_documents_stay_small(tmp_path: Path):
    doc = cur = {}
    for i in range(12):
        cur["level"] = cur = {}
    cur["leaf"] = 1
    p = tmp_path / "deep.json"
    p.write_text(json.dumps(doc))
    d = describe(str(p))
    assert "not expanded" in d["detail"]
    assert len(d["detail"]) < 400


def test_a_plain_text_file_keeps_a_small_raw_head(tmp_path: Path):
    p = tmp_path / "notes.txt"
    p.write_text("hello there\nthis is prose, not a table\n")
    d = describe(str(p))
    assert d["kind"] == "text"
    assert "hello there" in d["detail"]


def test_detail_is_hard_capped_at_max_detail_chars(tmp_path: Path):
    wide = tmp_path / "wide.csv"
    header = ",".join(f"column_number_{i}" for i in range(300))
    wide.write_text(header + "\n" + ",".join("1" for _ in range(300)) + "\n")
    for limit in (1200, 300, 40):
        d = describe(str(wide), max_detail_chars=limit)
        assert len(d["detail"]) <= limit
    assert describe(str(wide), max_detail_chars=300)["detail"].endswith("… (truncated)")


def test_a_missing_file_is_unavailable_and_does_not_raise(tmp_path: Path):
    d = describe(str(tmp_path / "nope.csv"))
    assert d["kind"] == "unavailable"
    assert d["size"] == 0
    assert d["detail"] == ""
    assert "\n" not in d["summary"]


def test_a_directory_is_unavailable_rather_than_an_exception(tmp_path: Path):
    d = describe(str(tmp_path))
    assert d["kind"] == "unavailable"


def test_a_missing_optional_dependency_degrades_to_a_lesser_descriptor(tmp_path: Path, monkeypatch):
    # openpyxl/pyarrow/pypdf are not backend dependencies. Absence must cost detail, not the turn.
    xlsx = tmp_path / "book.xlsx"
    with zipfile.ZipFile(xlsx, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")

    real_import = builtins.__import__

    def blocked(name, *a, **kw):
        if name.split(".")[0] in ("openpyxl", "pyarrow", "pypdf"):
            raise ImportError(f"no {name}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)

    d = describe(str(xlsx))
    assert d["kind"] == "excel"
    assert "openpyxl" in d["detail"]
    assert d["size"] > 0

    p = describe(str(_pdf(tmp_path)))
    assert p["kind"] == "pdf"
    assert "pypdf" in p["detail"]
    assert REPLACEMENT not in p["detail"]

    parquet = tmp_path / "t.parquet"
    parquet.write_bytes(b"PAR1" + bytes(64) + b"PAR1")
    q = describe(str(parquet))
    assert q["kind"] == "parquet"
    assert "pyarrow" in q["detail"]


def test_every_kind_produces_a_single_line_summary_within_budget(tmp_path: Path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(bytes(range(256)) * 20)
    jsonf = tmp_path / "a.json"
    jsonf.write_text(json.dumps({"k": [1, 2, 3]}))
    txt = tmp_path / "a.txt"
    txt.write_text("just prose\n")
    parquet = tmp_path / "a.parquet"
    parquet.write_bytes(b"PAR1" + bytes(64) + b"PAR1")
    xlsx = tmp_path / "a.xlsx"
    with zipfile.ZipFile(xlsx, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")

    paths = [_csv(tmp_path, rows=10), _pdf(tmp_path), _png(tmp_path), blob, jsonf, txt, parquet,
             xlsx, tmp_path / "missing.csv"]
    kinds = set()
    for p in paths:
        d = describe(str(p))
        kinds.add(d["kind"])
        assert "\n" not in d["summary"]
        assert 0 < len(d["summary"]) <= 90
        assert REPLACEMENT not in d["summary"] and REPLACEMENT not in d["detail"]
        assert isinstance(d["size"], int)
        assert set(d) == {"kind", "summary", "detail", "size"}
    assert kinds == {"tabular", "pdf", "image", "binary", "json", "text", "parquet", "excel",
                     "unavailable"}


# --- real openpyxl / pypdf paths -----------------------------------------------------------------
# Both are hard dependencies, so these branches — not the ImportError fallbacks — are what runs in
# production. The fallbacks above are all that the suite exercised while the libs were unpinned.

def test_a_real_workbook_reports_every_sheet_with_its_dimensions_and_header(tmp_path: Path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Q3"
    ws.append(["region", "revenue"])
    for i in range(120):
        ws.append([f"r{i}", i * 1.5])
    wb.create_sheet("Notes").append(["comment"])
    p = tmp_path / "book.xlsx"
    wb.save(p)

    d = describe(str(p))
    assert d["kind"] == "excel"
    assert d["summary"] == "Excel — 2 sheets"
    assert "Q3: 121 rows x 2 columns" in d["detail"] and "header: region, revenue" in d["detail"]
    assert "Notes" in d["detail"]


def test_a_real_pdf_with_no_text_layer_says_extraction_will_not_work(tmp_path: Path):
    """All-zero per-page counts are the signal that matters: the agent must learn up front that a
    scanned PDF yields nothing, rather than building a text-extraction feature that silently
    returns empty."""
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=612, height=792)
    p = tmp_path / "scanned.pdf"
    with open(p, "wb") as f:
        w.write(f)

    d = describe(str(p))
    assert d["kind"] == "pdf"
    assert d["summary"] == "PDF — 3 pages"
    assert "p1=0, p2=0, p3=0" in d["detail"]
    assert "scanned/image-only" in d["detail"]


def test_a_corrupt_file_keeps_the_type_its_magic_bytes_proved(tmp_path: Path):
    """Parsing can fail; identification does not. A truncated PDF is still a PDF — degrading it to
    'unavailable' throws away something we know for certain and tells the agent nothing."""
    d = describe(str(_pdf(tmp_path)))   # %PDF header, garbage body — pypdf cannot parse it

    assert d["kind"] == "pdf"
    assert "could not be parsed" in d["summary"]
    assert "NOT previewed" in d["detail"]
    assert REPLACEMENT not in d["summary"] + d["detail"]


# --- fit_image: shrink rather than refuse -------------------------------------------------------

def _noisy_png(tmp: Path, name: str, n: int = 900) -> Path:
    """A PNG too big to compress away — solid colour would shrink to nothing and prove nothing."""
    import os as _os

    from PIL import Image
    img = Image.frombytes("RGB", (n, n), _os.urandom(n * n * 3))
    p = tmp / name
    img.save(p, "PNG")
    return p


def test_an_image_that_already_fits_is_returned_untouched(tmp_path: Path):
    p = _noisy_png(tmp_path, "small.png", 40)
    data, mime = fit_image(str(p), 3 * 1024 * 1024)
    assert data == p.read_bytes() and mime == "image/png"   # byte-identical: no needless re-encode


def test_an_oversized_image_is_shrunk_to_fit_rather_than_refused(tmp_path: Path):
    """Refusing costs the agent the whole picture. Vision models downsample anyway, so shrinking
    keeps the signal — verified live: gpt-5.4 read the correct quadrant colours off the shrunk
    version of the same file that was previously rejected."""
    p = _noisy_png(tmp_path, "big.png")
    cap = 512 * 1024
    assert p.stat().st_size > cap

    data, mime = fit_image(str(p), cap)

    assert len(data) <= cap
    assert mime in ("image/png", "image/jpeg")
    assert data != p.read_bytes()


def test_an_undecodable_image_is_reported_as_unshowable_not_crashed(tmp_path: Path):
    """A valid PNG header is no guarantee the pixels decode. The caller must be able to tell the
    agent it cannot see the image, rather than the turn dying."""
    p = tmp_path / "broken.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                  + (900).to_bytes(4, "big") * 2 + b"garbage" * 200_000)

    assert fit_image(str(p), 1024) is None


def test_a_non_image_is_never_offered_for_inlining(tmp_path: Path):
    p = tmp_path / "data.csv"
    p.write_text("a,b\n1,2\n")
    assert fit_image(str(p), 3 * 1024 * 1024) is None
