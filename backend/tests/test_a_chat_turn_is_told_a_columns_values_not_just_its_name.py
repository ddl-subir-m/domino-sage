"""A chat turn asked "are any of my longs moving together?" answered with a blank chart over
`{"title": "Long Position Correlation Matrix", "columns": [], "rows": []}`.

The turn had written `pnl[pnl.side == "long"]` against a column holding `LONG`. Pandas raises
nothing for a filter that matches nothing, so the empty frame went all the way through: an empty
correlation, a blank heatmap, and an artifact file with a correct title and no data in it.

The turn was guessing because Session context handed it `summary` — "CSV — 9 columns, 2,864
rows" — which names no column at all, let alone a value in one. `detail` names them but carries
three verbatim rows, and Session context is assembled for every attached file on every turn. So
`describe()` grew a third rendering: `shape`, which is `detail` without the rows, plus the value
vocabulary of any column that has few enough values to have one.

The bound is what makes it shape rather than content, and it is load-bearing twice: a column of
customer names is named without its values, and it stops being tracked at all once it outgrows
the bound, so nothing here holds a set the size of the file.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator.describe import _VOCABULARY_MAX, describe


def _csv(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_a_low_cardinality_column_reports_its_values():
    """The whole bug in one assertion: `LONG` is on the page the turn reads."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = _csv(Path(d), "pnl.csv",
                    "date,ticker,side,pnl\n"
                    "2026-01-02,VLTA,LONG,-577893\n"
                    "2026-01-02,TNDR,SHORT,41220\n"
                    "2026-01-05,VLTA,LONG,-62872\n")
        shape = describe(path)["shape"]
    assert "side: string (LONG | SHORT)" in shape


def test_a_high_cardinality_column_is_named_without_its_values():
    """Four hundred customer names would be the file's content. The column is still listed."""
    import tempfile
    rows = "\n".join(f"t{i},Person {i},12.5" for i in range(_VOCABULARY_MAX + 40))
    with tempfile.TemporaryDirectory() as d:
        path = _csv(Path(d), "txns.csv", f"txn_id,cardholder_name,amount\n{rows}\n")
        shape = describe(path)["shape"]
    assert "cardholder_name: string" in shape
    assert "Person 1" not in shape


def test_a_column_exactly_at_the_bound_still_reports_and_one_past_it_does_not():
    """The bound is a real edge and both sides of it are asserted, so moving the constant is a
    decision somebody makes rather than a number that drifts."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        at = "\n".join(f"v{i}" for i in range(_VOCABULARY_MAX))
        over = "\n".join(f"v{i}" for i in range(_VOCABULARY_MAX + 1))
        assert "(" in describe(_csv(Path(d), "at.csv", f"c\n{at}\n"))["shape"]
        assert "(" not in describe(_csv(Path(d), "over.csv", f"c\n{over}\n"))["shape"]


def test_shape_never_carries_a_row():
    """`detail` is the rendering that may. This one is handed out unconditionally."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = _csv(Path(d), "pnl.csv", "date,ticker,side\n2026-01-02,VLTA,LONG\n")
        d2 = describe(path)
    assert "Sample rows:" in d2["detail"] and "2026-01-02,VLTA,LONG" in d2["detail"]
    assert "Sample rows:" not in d2["shape"]
    # The date and the ticker are single-valued in this file, so they legitimately appear as
    # vocabularies. What must not appear is a ROW — the cells joined back together.
    assert "2026-01-02,VLTA,LONG" not in d2["shape"]


def test_a_numeric_column_gets_no_vocabulary():
    """A vocabulary is for a column somebody filters by name. Twelve distinct prices are not one,
    and printing them would be the content this module refuses to emit."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = _csv(Path(d), "px.csv", "ticker,close\nVLTA,74.59\nVLTA,74.51\n")
        shape = describe(path)["shape"]
    assert "close: float" in shape
    assert "74.59" not in shape


def test_the_empty_frame_guard_is_in_the_prompt_and_its_mirror():
    """`template/chat/AGENTS.md` is the source of truth and `opencode.json` is what the model is
    actually sent. Editing one alone is the failure this asserts against."""
    import json

    root = Path(__file__).resolve().parents[2]
    md = (root / "template" / "chat" / "AGENTS.md").read_text(encoding="utf-8")
    mirror = json.loads((root / "opencode.json").read_text(encoding="utf-8"))
    prompt = mirror["agent"]["sage-chat"]["prompt"]
    for probe in ("Never write a chart or a table from an empty frame",
                  'matches nothing in a column holding `LONG`',
                  "reset a labelled index into a column"):
        assert probe in md, probe
        assert probe in prompt, probe


def test_the_chat_context_line_carries_the_shape_and_not_the_summary():
    """The wiring, not the rendering: `_describe_context_file` is the one caller that had to
    change, and a chat turn reads what it returns. Asserted end to end because a `shape` nothing
    reads would have passed every test above while the turn kept guessing."""
    import tempfile

    from sage.orchestrator.service import _chat_context_line, _describe_context_file

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "pnl.csv").write_text("date,ticker,side\n2026-01-02,VLTA,LONG\n"
                                      "2026-01-02,TNDR,SHORT\n", encoding="utf-8")
        note = _describe_context_file(root, {"kind": "file", "path": "pnl.csv"})

    assert "side: string (LONG | SHORT)" in note
    assert "Sample rows:" not in note
    line = _chat_context_line({"kind": "file", "name": "pnl.csv", "path": "pnl.csv"},
                              file_note=note)
    # Every other Session-context row is one line, so a column listing joins on a newline and
    # keeps its own indent rather than running four more rows into the block.
    assert line.startswith("- file: pnl.csv")
    assert ".\n3 columns," in line
    assert "  side: string (LONG | SHORT)" in line
