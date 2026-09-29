"""mimo sends typed tool arguments as JSON text: `batch_size: "50"`, `step: "true"`, a list as its
JSON spelling. On 2026-09-29 `analyze_text` refused two calls with "Batch size must be between 1 and
100." for a 50 it could not see as a number, and the turn spent them retrying.
"""

from __future__ import annotations

import pytest

from sage.liveread import mcp


def _arguments_seen(name: str, arguments: dict) -> dict:
    seen = {}

    def run(_name, args):
        seen.update(args)
        return "ok"

    mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}, run=run)
    return seen


@pytest.mark.parametrize(("name", "key", "sent", "read"), [
    ("live_read_table", "batch_size", "50", 50),
    ("live_read_table", "limit", " 5 ", 5),
    ("live_read_table", "labels", '["yes", "no"]', ["yes", "no"]),
    ("live_read_files", "pages", "[1, 2]", [1, 2]),
    ("live_read_files", "row_limit", "200", 200),
    ("live_read_query", "step", "true", True),
])
def test_a_typed_argument_sent_as_json_text_arrives_as_its_type(name, key, sent, read):
    assert _arguments_seen(name, {"token": "t", key: sent})[key] == read


@pytest.mark.parametrize(("name", "key", "sent"), [
    ("live_read_table", "batch_size", "fifty"),
    ("live_read_table", "batch_size", "50.5"),
    ("live_read_table", "labels", "yes, no"),
    ("live_read_table", "labels", '{"a": "yes"}'),
    ("live_read_query", "step", "1"),
    ("live_read_query", "sql", "123"),
])
def test_text_that_does_not_spell_the_declared_type_reaches_the_tool_unchanged(name, key, sent):
    assert _arguments_seen(name, {"token": "t", key: sent})[key] == sent
