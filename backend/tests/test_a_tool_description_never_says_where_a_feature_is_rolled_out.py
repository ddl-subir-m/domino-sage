"""#423, closed by #431: a tool description that names a rollout scope is obeyed as a refusal.

Measured live. Four shipped `operation` descriptions opened *"Fresh projects: …"*, in a field only
the model sees, as a precondition on the parameter. A model applied it to itself, decided its
Project was not "fresh", and told the person the capability belonged to other projects — WITHOUT
EVER CALLING THE TOOL. No gate was reached and no refusal was involved: the sentence in the schema
did the declining on its own.

That was survivable only while the gate behind it was real. #431 deleted `dataUseVersion`, so the
same sentences became the one thing still stopping a working tool — the feature on, and Sage
saying it was off. A scope clause in a description is therefore not a style problem; it is a
refusal with no code path, which is the hardest kind to find, because nothing is gated and
everything is green.

THE POPULATION IS DERIVED, NEVER LISTED. Both walkers below find their own strings — the MCP one
by recursing `mcp.TOOLS`, the TypeScript one by reading every string literal in the file. A tool
added tomorrow, or a fifth description added to a tool that already exists, is covered without
anyone remembering this file. Keying it on a list of four files is the failure this repo already
has a note about: the writer records the subset they happened to hit, and the next session
inherits a narrower rule than the one they need.

BOTH DOORS. `mcp.py` and `tools/live_read.ts` are two doors on one tool, and they have drifted
before — which is why `test_the_live_read_tools_are_named_the_same_either_way` exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sage.liveread import mcp

TOOLS_TS = Path(mcp.__file__).parent / "tools" / "live_read.ts"

# The shape of the claim, not the wording that happened to ship. "Fresh projects:" is what #423
# measured, but a reworded "for newly created Projects" is the same sentence to a model, and the
# point of deleting rather than rewording is that NO version of it belongs here.
SCOPE = re.compile(
    r"(fresh|new|newer|newly[- ]created|existing|old|older|legacy|recent|migrated)\s+"
    r"(\w+\s+){0,2}projects?"
    r"|projects?\s+(created|made)\s+(since|before|after)"
    r"|rolled\s+out"
    r"|(only\s+)?available\s+(in|for|to)\s+\w*\s*projects?"
    r"|projects?\s+only",
    re.IGNORECASE,
)


def _mcp_descriptions() -> list[tuple[str, str]]:
    """Every `description` anywhere in the MCP tool declarations, found by walking them."""
    found: list[tuple[str, str]] = []

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            name = node.get("name")
            here = f"{where}.{name}" if isinstance(name, str) and name else where
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    found.append((here, value))
                else:
                    walk(value, here if key in ("inputSchema", "properties") else f"{here}.{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, where)

    walk(mcp.TOOLS, "mcp.TOOLS")
    return found


def _ts_descriptions() -> list[tuple[str, str]]:
    """Every string literal in the custom-tool file, with `//` comments stripped first.

    Over-broad on purpose. The prose descriptions here are built by `+`-joining literals across
    several lines, so matching `description:` and stopping at the next key would miss the
    continuation lines — which is exactly where two of #423's six sentences lived.
    """
    source = TOOLS_TS.read_text()
    body = "\n".join(re.sub(r"//.*$", "", line) for line in source.splitlines())
    return [(f"{TOOLS_TS.name}:{body[:m.start()].count(chr(10)) + 1}", m.group(1))
            for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', body)]


def _all() -> list[tuple[str, str]]:
    return _mcp_descriptions() + _ts_descriptions()


def test_the_walkers_actually_find_the_shipped_descriptions():
    """The positive control. A walker that silently found nothing would make every assertion below
    vacuous — the #399 shape, and the reason this test is not just the loop underneath it."""
    mcp_found, ts_found = _mcp_descriptions(), _ts_descriptions()
    assert len(mcp_found) > 20, mcp_found
    assert len(ts_found) > 20, len(ts_found)
    # All three tools, and their own top-level descriptions rather than only their parameters'.
    for tool in ("live_read_table", "live_read_query", "live_read_files"):
        assert any(where.endswith(tool) for where, _ in mcp_found), tool


@pytest.mark.parametrize("where,said", _all())
def test_no_shipped_tool_description_names_a_rollout_scope(where: str, said: str):
    found = SCOPE.search(said)
    assert not found, (
        f"{where} tells the model where the feature is rolled out: {found.group(0)!r}\n"
        f"  in: {said[:200]}\n"
        "  A model reads this as a precondition and declines without calling the tool (#423).")


def test_the_pattern_catches_the_sentence_that_actually_shipped():
    """The guard's own guard. A pattern that matched nothing would pass the whole file forever —
    and this repo has been bitten by a check string that never matched."""
    for shipped in ("Fresh projects: calculate CSV totals or analyze CSV text.",
                    "In fresh projects, operation sum calculates a bound table locally",
                    "Data calculation is available in new projects only.",
                    "This is available for newly created projects.",
                    "Projects created since then have it."):
        assert SCOPE.search(shipped), shipped
    for fine in ("Calculate CSV totals or analyze CSV text.",
                 "The table. A dotted database.schema.table is fine.",
                 "Write Artifacts under this project's examples folder.",
                 "Result columns and/or total. Omit for structure only."):
        assert not SCOPE.search(fine), fine
