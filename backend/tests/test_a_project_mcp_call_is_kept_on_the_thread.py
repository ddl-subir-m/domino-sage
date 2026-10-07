"""A Chat turn that calls a Project MCP server says so on the Thread (#666).

Two halves. The server keeps one compact row per completed call and one "Data used" event naming
the server; the transcript folds those rows above the answer, on reload and live. The fold is
driven through `js/working_reads_fold_harness.mjs`, which renders the real store and components.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage import extension_mcp

from .fake_opencode import Turn
from .test_chat_turn import _orch

_HARNESS = Path(__file__).resolve().parent / "js" / "working_reads_fold_harness.mjs"
_SEARCH = "tavily_tavily_search"
_RESULT = "Reuters: the central bank held rates at 4.25% on Tuesday."

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _turn(tmp_path: Path, turn: Turn, servers: tuple[str, ...] = ("tavily",)):
    orch, _ = _orch(tmp_path, [turn])
    root = orch.project(start_preview=False).record.path
    for name in servers:
        extension_mcp.add(root, name, f"https://mcp.{name}.example/mcp/",
                          {"Authorization": "Bearer {env:TAVILY_API_KEY}"})
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "what is in the news about rates?"))
    return events, orch.get_thread(tid)["history"]


def _searches(*queries: str) -> list[tuple[str, dict, str]]:
    return [(_SEARCH, {"query": q, "max_results": 5}, _RESULT, "completed") for q in queries]


def _external(history: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "at"} for r in history
            if r.get("type") == "agent" and r.get("kind") == "tool"]


def test_each_completed_project_mcp_call_is_one_row_on_the_thread(tmp_path):
    _, history = _turn(tmp_path, Turn(calls=_searches("rates news", "fed decision"),
                                      text="Rates held."))

    assert _external(history) == [
        {"type": "agent", "kind": "tool", "tool": _SEARCH, "external": True,
         "detail": "rates news"},
        {"type": "agent", "kind": "tool", "tool": _SEARCH, "external": True,
         "detail": "fed decision"},
    ]


def test_a_built_in_tool_or_an_unregistered_prefix_keeps_no_row(tmp_path):
    _, history = _turn(tmp_path, Turn(tools=["read", "grep"],
                                      calls=[("github_list_repos", {"query": "x"}, "y",
                                              "completed")],
                                      text="Done."))

    assert _external(history) == []


def test_a_failed_project_mcp_call_keeps_no_row_and_no_data_used(tmp_path):
    events, history = _turn(tmp_path, Turn(calls=[(_SEARCH, {"query": "q"}, "boom", "error")],
                                           text="The search failed."))

    assert _external(history) == []
    assert "dataUsed" not in next(e for e in events if e["type"] == "done")


def test_the_row_carries_neither_the_result_nor_an_env_reference(tmp_path):
    long = "{env:TAVILY_API_KEY} " + "rates " * 60
    _, history = _turn(tmp_path, Turn(calls=[(_SEARCH, {"query": long}, _RESULT, "completed")],
                                      text="Ok."))

    [row] = _external(history)
    assert _RESULT not in json.dumps(history)
    assert "{env:" not in row["detail"]
    assert len(row["detail"]) <= 80


def test_the_done_row_names_the_server_as_data_used(tmp_path):
    events, history = _turn(tmp_path, Turn(calls=_searches("rates news", "fed decision"),
                                           text="Rates held."))

    done = next(e for e in events if e["type"] == "done")
    used = [(e["operation"], e["server"], e["tool"], e["carrier"]) for e in done["dataUsed"]]
    assert used == [("external_mcp", "tavily", _SEARCH, "external MCP result")] * 2
    assert done["dataUsed"] == next(r for r in history if r.get("type") == "done")["dataUsed"]


def _run(spec: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(spec),
                         check=False, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _rows(*details: str) -> list[dict]:
    return [{"type": "agent", "kind": "tool", "tool": _SEARCH, "external": True, "detail": d}
            for d in details]


_USED = [{"operation_id": f"du_{i}", "turn_id": "t1", "operation": "external_mcp",
          "server": "tavily", "tool": _SEARCH, "carrier": "external MCP result",
          "requests": []} for i in (1, 2)]


@needs_node
def test_a_reloaded_turn_draws_one_fold_of_its_calls_above_the_answer():
    history = [{"type": "user", "text": "news?"}, *_rows("rates news", "fed decision"),
               {"type": "agent", "kind": "text", "text": "Rates held."},
               {"type": "done", "ok": True, "dataUsed": _USED}]
    out = _run({"thread": {"id": "t1", "history": history}, "files": {}, "open": True,
                "dataAccessShown": True})

    assert [(b["type"], b["count"]) for b in out["blocks"]] == [
        ("text", None), ("external_calls_fold", 2), ("text", None), ("data_used", None)]
    assert "made 2 MCP calls" in out["words"]
    assert f"{_SEARCH} · fed decision" in out["words"]
    assert "Called the MCP server  tavily ." in out["words"]


@needs_node
def test_a_live_turn_draws_the_same_fold_as_its_reload():
    out = _run({"thread": {"id": "t1", "history": []}, "files": {},
                "live": [*_rows("rates news", "fed decision"),
                         {"type": "agent", "kind": "text", "text": "Rates held."},
                         {"type": "done", "ok": True, "dataUsed": _USED}]})

    assert [(b["type"], b["count"]) for b in out["blocks"]] == [
        ("text", None), ("external_calls_fold", 2), ("text", None), ("data_used", None)]
