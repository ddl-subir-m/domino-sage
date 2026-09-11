"""What a LIVE READ's own Artifact looks like by the time it reaches the card (ADR-0045).

`test_a_shape_only_table_card_says_what_was_read.py` holds the card itself — the sentences, the
stamp, the empty-grid floor — against a hand-written receipt body. This holds the two claims that
one cannot: that the bytes `liveread.result.record` actually writes are bytes that renderer reads,
and that a read which stopped at its LIMIT says so on the card as well as in the receipt.

The first is the seam a shared contract can break silently. Both halves pass their own tests while
the writer emits a key the renderer does not read, and the failure shows up as a card that says
less than the file knows, which nothing is watching for.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.liveread import run

from .test_a_live_read_artifact_commits_its_shape_not_its_rows import turn_for

_JS = Path(__file__).resolve().parent / "js"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PATH = "examples/thr_a/gong-calls.table.json"


def _node(harness: str, payload: object) -> object:
    out = subprocess.run(["node", str(_JS / harness)], input=json.dumps(payload), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _live_read_artifact(tmp_path: Path, limit: int) -> dict:
    """One real Live read, and the file it left behind."""
    run.perform("live_read_table", {"source": "DWH", "table": "GONG__CALLS", "limit": limit},
                turn_for(tmp_path))
    return json.loads((tmp_path / "examples" / "thr_a" / "gong-calls.table.json").read_text())


def _block(body: dict) -> dict:
    out = _node("table_artifact_harness.mjs", [
        {"thread": {"id": "thr_a", "history": [{
            "type": "done",
            "artifacts": [{"kind": "table", "path": _PATH, "title": "gong calls table"}],
        }]},
         "file": {"path": _PATH, "body": body}},
        {"open": "thr_a"},
    ])
    tables = out[1]["tables"]
    assert len(tables) == 1
    return tables[0]


@needs_node
def test_what_the_writer_leaves_behind_is_what_the_renderer_reads(tmp_path: Path):
    """Held from both ends, because a shared file contract fails quietly: the writer emits a key
    the renderer does not read, each side's own tests stay green, and the card says less than the
    file knows."""
    block = _block(_live_read_artifact(tmp_path, 2))

    assert block["keptRows"] is False, "or the receipt branch never runs and antd paints the grid"
    assert block["rowCount"] == 2
    assert block["readAt"]
    assert block["columns"] == ["ID", "EMAIL"]
    assert block["rows"] == []


@needs_node
def test_a_read_that_stopped_at_its_limit_says_so_on_the_card(tmp_path: Path):
    """ADR-0029: truncation is a fact the caller reads, not a silence. Without this the card under
    a capped read says "500 rows" and is read as the whole table — and the assistant beside it was
    told "the first 500 rows (there are more)", so the two describe different tables."""
    said = " ".join(_node("table_empty_card_harness.mjs", _block(_live_read_artifact(tmp_path, 2)))
                    ["said"])

    assert "the first 2 rows, read " in said


@needs_node
def test_a_read_that_did_not_fill_its_limit_does_not_claim_it_stopped_short(tmp_path: Path):
    block = _block(_live_read_artifact(tmp_path, 2))
    block["truncated"] = False
    said = " ".join(_node("table_empty_card_harness.mjs", block)["said"])

    assert "2 rows, read " in said
    assert "first" not in said
