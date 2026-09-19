"""Opening a Thread hydrated its `.table.json` Artifacts one at a time. `blocksForArtifacts`
awaited the fetch inside its own loop, so a 60-table investigation cost 60 serial round trips
through the Domino proxy before the first card drew, and the rail read as a dead click (#451).

The reads now run through a pool of `ARTIFACT_HYDRATION_POOL`. See
`js/artifact_hydration_harness.mjs`: it drives the real `store.openThread` against a fetch that
serves each file after a per-file delay, so a later table can come back before an earlier one, and
it records both the URLs the store asked for and the high-water mark of reads in flight. These are
claims about the shape of the traffic and the order of the result — what any one block CONTAINS is
`test_a_table_artifact_with_record_rows_still_fills_its_cells.py` and its neighbours.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "artifact_hydration_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(thread: dict, files: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"thread": thread,
                                                                    "files": files}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _thread(thread_id: str, artifacts: list[dict], text: str | None = None) -> dict:
    history: list[dict] = []
    if text is not None:
        history.append({"type": "agent", "kind": "text", "text": text})
    history.append({"type": "done", "artifacts": artifacts})
    return {"id": thread_id, "history": history}


def _table(path: str, title: str) -> dict:
    return {"kind": "table", "path": path, "title": title}


def _body(title: str) -> dict:
    return {"title": title, "columns": ["Event", "Count"], "rows": [["Nausea", 12]]}


def _cards(out: dict) -> list[tuple[str, str | None]]:
    """The blocks that stand for an Artifact, as (type, path) pairs in transcript order."""
    return [(b["type"], b["path"]) for b in out["blocks"]
            if b["type"] in {"image", "table", "file", "page"}]


@needs_node
def test_twelve_tables_are_read_six_at_a_time():
    """The regression itself, pinned from both sides.

    A high-water mark of 1 is the serial loop this replaced; 12 is the unbounded `Promise.all`
    that was rejected for putting 60 connections through the proxy at once. Only the pool gives 6.
    """
    thread_id = "thr_wide"
    paths = [f"examples/{thread_id}/t{i:02d}.table.json" for i in range(12)]
    out = _run(_thread(thread_id, [_table(p, f"Table {i}") for i, p in enumerate(paths)]),
               {p: {"body": _body(f"Table {i}"), "delayMs": 8} for i, p in enumerate(paths)})

    assert out["maxInFlight"] == 6
    assert sorted(out["requests"]) == sorted(paths)
    assert _cards(out) == [("table", p) for p in paths]


@needs_node
def test_one_unreadable_table_still_leaves_every_other_card_drawn():
    """The serial loop already caught this per item, so the failing table has always fallen back
    to its link. What changed is the cost of a leak: inside a pool a rejection would take the
    whole batch, including tables that had already come back, not just the ones behind it."""
    thread_id = "thr_broken"
    paths = [f"examples/{thread_id}/t{i}.table.json" for i in range(5)]
    files = {p: {"body": _body(f"Table {i}")} for i, p in enumerate(paths)}
    files[paths[2]] = {"fail": "network"}
    out = _run(_thread(thread_id, [_table(p, f"Table {i}") for i, p in enumerate(paths)]), files)

    # The unreadable one falls back to the "Open the file" link, in its own place in the order.
    assert _cards(out) == [
        ("table", paths[0]), ("table", paths[1]), ("file", paths[2]),
        ("table", paths[3]), ("table", paths[4]),
    ]


@needs_node
def test_a_table_that_answers_with_an_error_status_loses_only_its_own_card():
    """The other failure the read can meet: the request completes, the server says no."""
    thread_id = "thr_status"
    paths = [f"examples/{thread_id}/t{i}.table.json" for i in range(3)]
    files = {p: {"body": _body(f"Table {i}")} for i, p in enumerate(paths)}
    files[paths[0]] = {"fail": "status"}
    out = _run(_thread(thread_id, [_table(p, f"Table {i}") for i, p in enumerate(paths)]), files)

    assert _cards(out) == [("file", paths[0]), ("table", paths[1]), ("table", paths[2])]


@needs_node
def test_a_chart_and_an_image_cost_no_round_trip():
    """Non-table Artifacts are built from their own row. Dragging one into the pool would make it
    wait behind a table for a file the card never needed."""
    thread_id = "thr_mixed_kinds"
    chart = f"examples/{thread_id}/trend.png"
    table = f"examples/{thread_id}/summary.table.json"
    page = f"examples/{thread_id}/report.html"
    other = f"examples/{thread_id}/notes.txt"
    out = _run(
        _thread(thread_id, [{"kind": "chart", "path": chart, "title": "Trend"},
                            _table(table, "Summary"),
                            {"path": page, "title": "Report"},
                            {"path": other, "name": "notes.txt"}]),
        {table: {"body": _body("Summary")}},
    )

    assert out["requests"] == [table]
    assert [c[0] for c in _cards(out)] == ["image", "table", "page", "file"]


@needs_node
def test_blocks_keep_the_order_the_turn_wrote_them_when_a_later_read_lands_first():
    """The pool resolves out of order by construction. The callers splice this list straight into
    a message's blocks, so completion order would silently rearrange the transcript."""
    thread_id = "thr_order"
    first = f"examples/{thread_id}/a-slow.table.json"
    chart = f"examples/{thread_id}/middle.png"
    second = f"examples/{thread_id}/z-fast.table.json"
    out = _run(
        _thread(thread_id, [_table(first, "Slow"),
                            {"kind": "chart", "path": chart, "title": "Middle"},
                            _table(second, "Fast")]),
        {first: {"body": _body("Slow"), "delayMs": 60}, second: {"body": _body("Fast"),
                                                                 "delayMs": 1}},
    )

    # Both reads are in flight together, and the second comes back first.
    assert out["maxInFlight"] == 2
    assert _cards(out) == [("table", first), ("image", chart), ("table", second)]


@needs_node
def test_a_blank_table_file_is_still_hidden_and_still_stripped_from_the_sentence():
    """A table file that is there but blank gets neither a card nor a link, and the sentence
    around it loses the link too.

    This pins the hiding, not the order `hiddenTables` is walked in. Nothing here pins that
    order, and nothing can: see the comment on the assembly pass in `store.js` for why no two
    real Artifact paths can make it observable."""
    thread_id = "thr_blank"
    blank_one = f"examples/{thread_id}/first.table.json"
    blank_two = f"examples/{thread_id}/second.table.json"
    good = f"examples/{thread_id}/real.table.json"
    out = _run(
        _thread(thread_id,
                [_table(blank_one, "First"), _table(good, "Real"), _table(blank_two, "Second")],
                text=f"Here you go: [First]({blank_one}) and [Second]({blank_two})."),
        {blank_one: {"content": "   "}, blank_two: {"content": ""},
         good: {"body": _body("Real")}},
    )

    assert _cards(out) == [("table", good)]
    texts = [b["value"] for b in out["blocks"] if b["type"] == "text"]
    assert texts, "the sentence never reached the transcript, so this pins nothing"
    sentence = " ".join(texts)
    assert blank_one not in sentence
    assert blank_two not in sentence
