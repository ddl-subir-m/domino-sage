"""A `.table.json` of positional rows and no `columns` reached a Thread as a captioned blank
grid: the right title (from the filename), the right "Show all N rows" count, and no cells.

`blocksForArtifacts` is right to leave the header empty — inventing "0", "1" is worse than no
header, and `test_a_bare_array_of_positional_rows_is_not_given_index_headers` holds that. The
fault is one step later. `TableBlock` builds antd columns only from `block.columns`, so a body
with no names produced zero column defs. antd then paints a row per record with nothing in it,
which is how `df.to_json(orient="values")` and `{"rows": df.values.tolist()}` looked after a
workspace restart (the file is what reload reads).

Same harness as `test_a_table_that_recovered_no_rows_does_not_render_a_blank_card.py`: the
claim is what the card hands antd, not what antd paints.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "table_empty_card_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                 reason="node is not on PATH (it is in the Sage image)")


def _card(block: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(block), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_positional_rows_with_no_columns_still_put_values_in_cells():
    """The exact reload shape: filename title, 50-row body, no header list."""
    rows = [[f"2026-01-{i:02d}", f"k{i}", i * 100] for i in range(1, 51)]
    card = _card({
        "type": "table",
        "title": "api usage detail.table",
        "path": "examples/thr_api/api_usage_detail.table.json",
        "columns": [],
        "rows": rows,
    })
    assert card["table"] is True
    assert card["title"] == "api usage detail.table"
    assert card["said"] == []
    assert card["cells"] == ["2026-01-01", "k1", 100]
    assert "2026-01-01" in card["copied"] and "k1" in card["copied"]


@needs_node
def test_a_named_table_is_untouched():
    card = _card({
        "type": "table",
        "title": "API usage detail",
        "columns": ["date", "key", "tokens"],
        "rows": [["2026-01-01", "k1", 100]],
    })
    assert card["headers"] == ["date", "key", "tokens"]
    assert card["cells"] == ["2026-01-01", "k1", 100]
