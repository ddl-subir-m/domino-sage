"""A Project skill is offered by the composer's @ menu, in Chat and in Build (#628).

A skill is not a Resource: picking it puts `@<name>` in the box and attaches nothing, because the
shim reads the token off the prompt (`test_a_projects_own_extensions_reach_the_next_turn.py`).
Driven through `tests/js/mention_skill_harness.mjs`, which records every request the composer sends.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "mention_skill_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(mode: str, query: str, pick: str = "report-style") -> dict:
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"mode": mode, "query": query, "pick": pick}),
                         capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("mode", ["chat", "build"])
def test_the_menu_offers_a_skill_and_picking_it_attaches_nothing(mode):
    got = _run(mode, "rep")
    names = [r["name"] for r in got["rows"]]
    # Switched off for this conversation and still offered: a mention beats the switch.
    assert "report-style" in names and "reports" in names
    # A shadowed skill is not the one OpenCode loads.
    assert "report-old" not in names
    assert got["inserted"].strip() == "@report-style"
    assert got["posts"] == []


@pytest.mark.parametrize("mode", ["chat", "build"])
@pytest.mark.parametrize("pick", ["report_tool", "reports-wiki"])
def test_the_menu_offers_a_tool_and_an_mcp_server_and_picking_one_attaches_nothing(mode, pick):
    """#633: the Project's tools and MCP servers are named like its skills; the MCP server here is
    switched off for this conversation and still offered."""
    got = _run(mode, "rep", pick)
    assert {"report_tool", "reports-wiki"} <= {r["name"] for r in got["rows"]}
    assert got["inserted"].strip() == f"@{pick}"
    assert got["posts"] == []
