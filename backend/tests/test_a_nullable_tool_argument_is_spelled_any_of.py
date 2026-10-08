"""A nullable custom-tool argument is spelled `anyOf`, never `type: [x, "null"]` (#609).

OpenCode marks every argument of a custom tool required, so each optional one is declared nullable.
The two spellings of nullable are the same JSON Schema, but not the same to the Gateway: measured
2026-09-28, its mimo-v2.6-pro route cut every call off at the first argument declared with a type
list (`..."title": ` and nothing after), on Responses and Chat alike, whether the model meant a value
or a null. 12 of 12 calls broke that way; with `anyOf` 9 of 9 parsed, and every other model the
Gateway serves parsed both. A broken call never runs, and the repeat brake ends the turn in 30s.

The population is every file the installer copies, read through node as OpenCode reads it, so a
tool file added later is covered without being named here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.app import _OPENCODE_TOOL_DIRS

REPO = Path(__file__).resolve().parents[2]

_DUMP = """
import { readFileSync } from 'node:fs';
const out = {};
for (const path of process.argv.slice(1)) {
  const source = readFileSync(path);
  const mod = await import('data:text/javascript;base64,' + source.toString('base64'));
  for (const [name, tool] of Object.entries(mod)) {
    if (tool && typeof tool === 'object' && tool.args) out[path + ':' + name] = tool.args;
  }
}
console.log(JSON.stringify(out));
"""


def _tool_args() -> dict[str, dict]:
    files = [str(f) for parts in _OPENCODE_TOOL_DIRS for f in sorted(REPO.joinpath(*parts).glob("*.ts"))]
    assert files, "the installer's tool directories hold no tool files"
    done = subprocess.run(["node", "--input-type=module", "-e", _DUMP, *files],
                          check=True, capture_output=True, text=True)
    return json.loads(done.stdout)


def _type_lists(schema: object, where: str) -> list[str]:
    found = []
    if isinstance(schema, dict):
        if isinstance(schema.get("type"), list):
            found.append(where)
        for key, value in schema.items():
            found.extend(_type_lists(value, f"{where}.{key}"))
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            found.extend(_type_lists(value, f"{where}[{i}]"))
    return found


def _accepts_null(schema: dict) -> bool:
    return any(branch == {"type": "null"} for branch in schema.get("anyOf", []))


def test_no_tool_argument_declares_a_type_list():
    tools = _tool_args()
    found = [hit for tool, args in tools.items() for hit in _type_lists(args, tool)]

    assert not found, f"declare these nullable with anyOf, not a type list: {found}"


@pytest.mark.parametrize("tool, arg", [
    ("live_read.ts:query", "title"),
    ("live_read.ts:query", "purpose"),
    ("live_read.ts:query", "step"),
    ("live_read.ts:table", "operation"),
    ("live_read.ts:table", "labels"),
    ("live_read.ts:files", "pages"),
    ("delegated_model_call.ts:default", "system"),
    ("delegated_model_call.ts:default", "max_tokens"),
    ("sage_source_map.ts:default", "paths"),
    ("sage_source_map.ts:default", "symbol"),
])
def test_an_optional_argument_still_takes_null(tool, arg):
    """The spelling changed and the meaning did not: the model can still send null, and `call`
    still drops it before Python sees the arguments."""
    args = next(a for key, a in _tool_args().items() if key.endswith("/" + tool))

    assert _accepts_null(args[arg]), f"{tool} {arg}: {args[arg]}"


def test_an_enum_keeps_its_values_on_the_non_null_branch():
    args = next(a for key, a in _tool_args().items() if key.endswith("/live_read.ts:table"))

    assert args["operation"]["anyOf"][0] == {"type": "string", "enum": ["sum", "analyze_text"]}
