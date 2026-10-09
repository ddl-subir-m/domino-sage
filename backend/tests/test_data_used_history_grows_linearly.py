"""Data used history grows with operations plus requests, not with their product (#731).

`DataUse.observe` used to persist the WHOLE event, with its whole `requests` list, for every
operation a model request carried, at the start of the request and again at its end. A Build
carries the Chat history, so every Build inference re-saved every disclosed Chat read: one
conversation's `/history` reached 20.2 MB, 1,262 `data_used` rows, five operations re-written 196
times each with `requests` growing from ~9 to ~130.

The plant is that shape: N Chat reads, then a Build making M model requests that carry all of them.
What the readers show — the Data used panel, `restore()` and the Build handoff — must be the same
evidence the in-memory events hold, request for request and in the same order.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest

from sage.liveread.data_use import DataUse
from sage.orchestrator import handoff
from sage.workspace.threads import ThreadStore

_JS = Path(__file__).resolve().parent / "js"
_THREAD = "thr_plant"
_DONE = b'data: {"model":"served-model","choices":[{"finish_reason":"stop"}]}\n\n'

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _event(n: int) -> dict:
    """A Live read event of the size `DataUse.record` persists for a Chat read."""
    return {
        "operation_id": f"du_op_{n}",
        "operation": "live_read",
        "source": "SFDC_OPPORTUNITY",
        "source_kind": "data_source",
        "carrier": "live_read custom tool result",
        "purpose": f"Open pipeline by stage and team, read {n}",
        "artifact": f"examples/{_THREAD}/open-pipeline-{n}.table.json",
        "columns": ["stage", "team", "owner", "amount", "close_date", "region", "segment",
                    "product", "forecast_category", "probability", "created", "updated"],
        "selected_fields": ["stage", "team", "amount"],
        "coverage": {"total": 4210, "processed": 4210, "excluded": 0, "failed": 0,
                     "unfinished": 0},
        "status": "prepared",
        "requests": [],
    }


def _history_bytes(store: ThreadStore) -> int:
    """`GET /threads/{id}/history`'s body: `JSONResponse` renders with these exact options."""
    return len(json.dumps(list(store.read_history(_THREAD)), ensure_ascii=False,
                          allow_nan=False, indent=None, separators=(",", ":")).encode())


def _request(data: DataUse, used: set[str], alias: str) -> None:
    list(data.observe(iter([_DONE]), {"model": alias}, used))


def _plant(tmp_path: Path, reads: int, chat_requests: int, build_requests: int):
    """N Chat reads, a Chat turn's requests over them, then Build requests carrying them all.

    Returns the store, the DataUse that wrote it, and the history size after every Build request.
    """
    store = ThreadStore(tmp_path)
    data = DataUse()

    def persist(row: dict) -> None:
        store.append_history(_THREAD, row)

    for n in range(reads):
        data.record(_event(n), {"selected": {"amount": 780 + n}}, persist, "turn_chat")
    used = {f"du_op_{n}" for n in range(reads)}
    for _ in range(chat_requests):
        _request(data, used, "chat-alias")
    store.append_history(_THREAD, {"type": "done", "ok": True,
                                   "dataUsed": data.events("turn_chat")})
    sizes = [_history_bytes(store)]
    for _ in range(build_requests):
        _request(data, used, "build-alias")
        sizes.append(_history_bytes(store))
    return store, data, sizes


def test_each_build_request_adds_the_same_bytes_however_long_the_conversation(tmp_path):
    """The plant: 6 Chat reads, 9 Chat requests, then 98 Build requests carrying all six.

    Quadratic growth shows as a per-request increment that rises with the request count. Linear
    growth is a flat increment: the 98th request costs what the 1st did."""
    _store, _data, sizes = _plant(tmp_path, reads=6, chat_requests=9, build_requests=98)
    steps = [b - a for a, b in pairwise(sizes)]
    print(f"history bytes: before Build {sizes[0]}, after 98 Build requests {sizes[-1]}; "
          f"first request +{steps[0]}, last request +{steps[-1]}")
    assert steps[-1] <= steps[0] * 1.05, (
        f"request 98 added {steps[-1]} bytes against {steps[0]} for request 1")


def test_a_request_row_does_not_scale_with_the_operations_it_carries(tmp_path):
    """Per request, the cost of carrying one more operation is an id, not a copy of its event."""
    one = _plant(tmp_path / "one", reads=1, chat_requests=0, build_requests=2)[2]
    many = _plant(tmp_path / "many", reads=20, chat_requests=0, build_requests=2)[2]
    extra = (many[-1] - many[-2]) - (one[-1] - one[-2])
    assert extra <= 19 * 2 * 40, f"19 more carried operations cost {extra} bytes per request"


def _interleaved(tmp_path: Path):
    """Two requests in flight at once, the later one settling first: `requests` is kept in the
    order they SETTLED, which is the order the panel has always shown."""
    store = ThreadStore(tmp_path)
    data = DataUse()

    def persist(row: dict) -> None:
        store.append_history(_THREAD, row)

    for n in range(3):
        data.record(_event(n), {}, persist, "turn_chat")
    first = data.observe(iter([_DONE]), {"model": "first"}, {"du_op_0", "du_op_1"})
    second = data.observe(iter([b'data: {"error":{"message":"denied"}}\n\n']),
                          {"model": "second"}, {"du_op_1", "du_op_2"})
    next(first)
    next(second)
    list(second)
    list(first)
    _request(data, {"du_op_0", "du_op_1", "du_op_2"}, "third")
    return store, data


def test_restore_rebuilds_every_operation_with_its_whole_request_evidence(tmp_path):
    store, data = _interleaved(tmp_path)
    expected = {oid: copy.deepcopy(event) for oid, (event, _r, _p) in data.operations.items()}
    assert [r["requested_alias"] for r in expected["du_op_1"]["requests"]] == [
        "second", "first", "third"]
    restored = DataUse()
    restored.restore(list(store.read_history(_THREAD)), lambda row: None)
    assert {oid: event for oid, (event, _r, _p) in restored.operations.items()} == expected


def test_the_build_handoff_carries_the_same_request_lines(tmp_path):
    store, data = _interleaved(tmp_path)
    in_memory = handoff.data_use_summaries([{"dataUsed": data.events("turn_chat")}])
    assert "requested third" in in_memory[1]
    assert handoff.data_use_summaries(list(store.read_history(_THREAD))) == in_memory


@needs_node
def test_the_data_used_panel_shows_the_same_evidence(tmp_path):
    store, data = _interleaved(tmp_path)
    history = [{"type": "user", "text": "pipeline by stage"}, *store.read_history(_THREAD),
               {"type": "done", "text": "here you go"}]
    payload = {"thread": {"id": _THREAD, "history": history}}
    out = subprocess.run(["node", str(_JS / "data_used_grouping_harness.mjs")],
                         input=json.dumps(payload), check=False, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["events"] == [data.events("turn_chat")]
