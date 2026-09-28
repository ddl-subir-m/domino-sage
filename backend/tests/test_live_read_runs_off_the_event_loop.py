"""#604: `/mcp/live-read` answers off the control app's event loop, and a turn's reads take turns.

A Live read stopped being "one query" with `live_read_query` (#408) and `analyze_text` (#574): a
statement can run for `STATEMENT_TIMEOUT_S`, and an analysis pass is many Gateway generations. On
the loop, that froze the Chat stream, `/api/diag` and the Workbench for the whole call. Off the loop,
two reads from one turn can now overlap — OpenCode issues parallel tool calls — so the second test
holds what the loop used to guarantee by accident.

Both tests leave nothing running: every Event is released in a `finally`, every thread is joined
with a timeout, and the thread count is asserted back where it started.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import httpx

from sage.liveread import mcp as live_mcp
from sage.liveread import run as live_read
from sage.orchestrator import app as control

from .test_a_chat_turn_can_call_a_model_the_person_bound import _orch


def _join_new_threads(before: set[threading.Thread]) -> None:
    for t in set(threading.enumerate()) - before:
        t.join(timeout=2)


def test_another_request_is_served_while_a_live_read_is_in_flight():
    """The read blocks until the test releases it, and the test releases it only after `/healthz`
    has answered. On the loop, the read holds the loop, so `/healthz` cannot even be dispatched: the
    wait times out on its own and the read reports it was never released."""
    entered, release = threading.Event(), threading.Event()
    seen: dict[str, bool] = {}

    class Slow:
        _project_id = "Sage"
        _control_plane = None

        def live_read_call(self, message, *, probe=False):
            entered.set()
            seen["released"] = release.wait(timeout=2)
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {"content": []}}

    async def scenario():
        transport = httpx.ASGITransport(app=control.control_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            read = asyncio.create_task(client.post("/mcp/live-read", json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": live_mcp.TOOLS[0]["name"], "arguments": {}}}))
            try:
                for _ in range(2000):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.001)
                health = await asyncio.wait_for(client.get("/healthz"), timeout=2)
            finally:
                release.set()
            return health, await read

    before = set(threading.enumerate())
    try:
        with patch.object(control, "orchestrator", Slow()):
            health, read = asyncio.run(scenario())
    finally:
        release.set()
        _join_new_threads(before)

    assert threading.active_count() == len(before), "a worker thread outlived the test"
    assert health.status_code == 200
    assert read.status_code == 200
    assert seen["released"] is True, "the live read held the event loop: /healthz waited behind it"


def test_parallel_reads_in_one_conversation_take_turns(tmp_path: Path):
    """Several reads for one Conversation arrive at once. `result.record` names a card by its slug,
    so two untitled queries write the same file, and `count_statement` is a read-then-write on
    `_statements_tried`. Both were safe only because the loop ran one read at a time; per
    Conversation, they still must.

    The fake read waits until every caller is inside it, or 200ms, whichever comes first. Unserialized,
    they all get in and the high-water mark is the caller count; serialized, it stays at one."""
    callers, statements = 6, 200
    before = set(threading.enumerate())
    orch, _ = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    token = orch._mint_live_read_token(tid)

    lock = threading.Lock()
    inside, most = [0], [0]
    everyone_in, release = threading.Event(), threading.Event()

    def perform(name, args, turn):
        with lock:
            inside[0] += 1
            most[0] = max(most[0], inside[0])
            if inside[0] == callers:
                everyone_in.set()
        try:
            release.wait(timeout=2)
            for _ in range(statements):
                turn.run_statement(None, "select 1")
            turn.record_refusal(str(args["say"]))
            return "ok"
        finally:
            with lock:
                inside[0] -= 1

    def one(i: int):
        orch.live_read_call({
            "jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": live_mcp.TOOLS[0]["name"],
                       "arguments": {"token": token, "say": f"refusal {i}"}}})

    threads = [threading.Thread(target=one, args=(i,)) for i in range(callers)]
    try:
        with patch.object(live_read, "perform", perform), \
                patch.object(orch._resources, "run_statement", lambda *a, **kw: None):
            for t in threads:
                t.start()
            everyone_in.wait(timeout=0.2)
            release.set()
            for t in threads:
                t.join(timeout=5)
    finally:
        release.set()
        for t in threads:
            t.join(timeout=5)
        _join_new_threads(before)

    assert threading.active_count() == len(before), "a thread outlived the test"
    assert most[0] == 1, f"{most[0]} reads for one Conversation ran at once"
    assert orch._statements_tried[tid] == callers * statements
    assert orch._last_live_read_refusal(tid) in {f"refusal {i}" for i in range(callers)}
