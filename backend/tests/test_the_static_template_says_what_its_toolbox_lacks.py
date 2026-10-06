"""The static template's AGENTS.md names the two gaps a generated app fell into (#658).

Both crashed or failed for the person using the app, and neither is visible to a syntax check:
`d.isSameOrAfter is not a function` behind a tab, because the page loads only core Day.js; and a
remote MCP call that guessed an argument (`topic: 'news'`) the server's schema did not allow, so its
upstream validation error reached the page. The toolbox table and the MCP line are where the agent
looks, so that is where each gap is named.
"""
from __future__ import annotations

from pathlib import Path

AGENTS = (Path(__file__).resolve().parents[2] / "template" / "fastapi-antd" / "AGENTS.md").read_text()
INDEX = (Path(__file__).resolve().parents[2] / "template" / "fastapi-antd" / "static" / "index.html").read_text()


def _line(marker: str) -> str:
    return next(line for line in AGENTS.splitlines() if marker in line)


def test_the_dayjs_row_says_no_plugin_is_loaded_and_what_to_use_instead():
    row = _line("| `dayjs` |")
    assert "isSameOrAfter" in row
    assert "isAfter" in row and "isBefore" in row


def test_the_page_still_loads_core_dayjs_only():
    # The row is only true while this holds: a plugin script added here makes it a lie.
    scripts = [line for line in INDEX.splitlines() if "dayjs" in line]
    assert scripts == ['  <script src="static/vendor/dayjs.min.js"></script>']


def test_the_mcp_line_says_to_read_the_tool_schema_before_calling_it():
    block = AGENTS[AGENTS.index("**A remote MCP server**"):]
    block = block[:block.index("\n- ", 3)]
    assert "inputSchema" in block
    assert "list_tools" in block
