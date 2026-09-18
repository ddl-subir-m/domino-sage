"""No description the model reads may tell it a working capability is for other projects.

`live_read_table` and `live_read_files` carry `operation`, and four of their descriptions opened
"Fresh projects:" while two more said "In fresh projects,". Nothing behind the phrase was ever a
gate: `grep -rn "template_version\\|is_fresh\\|seeded" backend/sage/liveread/` returned nothing then
and returns nothing now. The sentence was a note about when the capability SHIPPED, and it reached
the model in the one position that reads as a precondition on the parameter — so the model applied
it, decided its own Project was not "fresh", and declined to try a capability that works in every
Project (#423).

WHY THIS TEST IS KEYED ON A DERIVATION AND NOT ON A LIST OF FILES. The ticket named four sites. A
`grep -rni "fresh project"` over model-facing source found six: the two `.ts` TOOL-level
descriptions say it as well, and `mcp.py`'s tool-level descriptions do not, so the two doors
already disagreed about it. A list of four would have passed while two live sites still said it.
So this walks the schema Sage SERVES and the source OpenCode is HANDED, and reads every description
out of them. A new tool, a new argument, or a third door is covered the day it lands; nothing here
needs editing to keep covering it.

THE TWO DOORS ARE READ DIFFERENTLY ON PURPOSE. `mcp.TOOLS` is data, so it is walked as data. The
`.ts` file is TypeScript this process never executes — OpenCode does — so it is read as source. A
test that imported it could not exist, and one that only checked `mcp.py` would have missed four of
the six sites, including both that reach a model today.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from sage.liveread import mcp

# Not a spell-check. Each of these asserts a capability is scoped to how old the Project is, which
# is the class of claim that has no gate behind it anywhere in `liveread/`. "Fresh" alone is not
# here: a fresh READ and a fresh listing are both honest, frequent and unrelated.
VINTAGE = re.compile(
    r"\b(fresh|new|existing|older|legacy|newly[- ]created|recently[- ]created)\s+projects?\b",
    re.IGNORECASE,
)

TS_DOOR = pathlib.Path(__file__).resolve().parents[1] / "sage/liveread/tools/live_read.ts"


def _descriptions(node: object, where: str) -> list[tuple[str, str]]:
    """Every `description` string anywhere under a served schema, with the path that reached it.

    Recursive because the sites are at two depths — the tool's own description and each argument's,
    inside `inputSchema.properties` — and a third depth is one nested object away.
    """
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                found.append((f"{where}.{key}", value))
            else:
                found.extend(_descriptions(value, f"{where}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_descriptions(value, f"{where}[{i}]"))
    return found


def _served() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for tool in mcp.TOOLS:
        out.extend(_descriptions(tool, str(tool.get("name") or "?")))
    return out


def _handed() -> list[tuple[str, str]]:
    """Every `description:` literal in the custom-tool source, with its line number.

    Read as text rather than parsed. The literals are concatenations split across lines for width,
    so this takes the whole run of string pieces up to the line that closes it — which is what the
    model is handed, and the form the six sites were actually written in.
    """
    src = TS_DOOR.read_text()
    out: list[tuple[str, str]] = []
    for match in re.finditer(r"description:\s*((?:\s*\"(?:[^\"\\]|\\.)*\"\s*\+?)+)", src):
        line = src.count("\n", 0, match.start()) + 1
        text = " ".join(re.findall(r"\"((?:[^\"\\]|\\.)*)\"", match.group(1)))
        out.append((f"live_read.ts:{line}", text))
    return out


def test_both_doors_are_actually_read():
    """A sweep that found nothing proves nothing until it proves it looked.

    Both counts are lower bounds, not fixtures: they say the walk reached past the tool level into
    the arguments, which is where four of the six sites lived. Pinning exact counts would make
    every new argument a failing test for no reason.
    """
    served, handed = _served(), _handed()
    assert len(served) > 10, f"only {len(served)} descriptions off mcp.TOOLS — the walk is shallow"
    assert len(handed) > 10, f"only {len(handed)} descriptions off {TS_DOOR.name} — regex missed"
    assert TS_DOOR.is_file()


@pytest.mark.parametrize("where,text", _served() + _handed())
def test_no_description_scopes_a_capability_to_a_project_vintage(where: str, text: str):
    hit = VINTAGE.search(text)
    assert hit is None, (
        f"{where} tells the model a capability belongs to {hit.group(0)!r} if it matched. "
        f"Nothing in liveread/ gates on project vintage, so this reads as a precondition the "
        f"model will apply against a Project where the capability works (#423). Say what the "
        f"operation does, not when it shipped.\n  {text}"
    )
