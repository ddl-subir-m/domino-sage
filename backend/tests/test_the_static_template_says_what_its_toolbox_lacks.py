"""The static template's AGENTS.md names the two gaps a generated app fell into (#658).

Both crashed or failed for the person using the app, and neither is visible to a syntax check:
`d.isSameOrAfter is not a function` behind a tab, because the page loads only core Day.js; and a
remote MCP call that guessed an argument (`topic: 'news'`) the server's schema did not allow, so its
upstream validation error reached the page. The toolbox table and the MCP line are where the agent
looks, so that is where each gap is named.

The Day.js gap is closed rather than named since #741: the page loads the common plugins, and
`test_a_static_app_can_call_the_common_dayjs_plugins.py` holds the row to what it loads.
"""
from __future__ import annotations

from pathlib import Path

AGENTS = (Path(__file__).resolve().parents[2] / "template" / "fastapi-antd" / "AGENTS.md").read_text()


def test_the_mcp_line_says_to_read_the_tool_schema_before_calling_it():
    block = AGENTS[AGENTS.index("**A remote MCP server**"):]
    block = block[:block.index("\n- ", 3)]
    assert "inputSchema" in block
    assert "list_tools" in block
