"""Opening an old conversation hides blank tables and their explicit links, without a turn."""
import json

import pytest

from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node, needs_node


@needs_node
@pytest.mark.parametrize("content", ["", " \n\t"])
def test_an_old_blank_file_has_no_card_or_link_beside_the_chart(content):
    path = "examples/thr_old/moves.table.json"
    result = _node("table_artifact_harness.mjs", [
        {"thread": {"id": "thr_old", "history": [
            {"type": "agent", "kind": "text", "text": (
                "Total: 42; chart: [file:examples/thr_old/trend.png]; "
                f"the table is ready: [file:{path}]")},
            {"type": "done", "artifacts": [
                {"kind": "chart", "path": "examples/thr_old/trend.png"},
                {"kind": "table", "path": path}]}]},
         "file": {"path": path, "content": content}},
        {"open": "thr_old"},
    ])[1]
    assert result["tables"] == []
    assert len(result["images"]) == 1
    text = " ".join(b.get("value", "") for b in result["blocks"])
    assert "Total: 42" in text
    assert "[file:examples/thr_old/trend.png]" in text
    assert path not in text
    assert "table is ready" not in text
    assert not any(b["type"] == "file" for b in result["blocks"])


@needs_node
def test_a_valid_zero_row_table_remains_visible():
    path = "examples/thr_old/moves.table.json"
    result = _node("table_artifact_harness.mjs", [
        {"thread": {"id": "thr_old", "history": [{"type": "done", "artifacts": [
            {"kind": "table", "path": path}]}]},
         "file": {"path": path, "content": json.dumps({"columns": ["name"], "rows": []})}},
        {"open": "thr_old"},
    ])[1]
    assert result["tables"][0]["columns"] == ["name"]


@needs_node
def test_empty_authoritative_text_removes_a_streamed_broken_offer():
    result = _node("chat_stream_harness.mjs", [
        {"type": "delta", "text": "The table is ready: [file:examples/t1/moves.table.json]", "final": True},
        {"type": "agent", "kind": "text", "text": ""},
        {"type": "error", "reason": "table generation failed",
         "message": "I could not generate the table: moves."},
        {"type": "done", "ok": False},
    ])
    assert "table is ready" not in json.dumps(result["final"])
    assert "moves.table.json" not in json.dumps(result["final"])
