"""ONE-APP-PLAN.md §2.4: many projects' previews share ONE process, so ONE process's event loop.

`get_upstream()` (`_preview_upstream` in `app.py`) can block synchronously for as long as
`UvicornSupervisor.start()`'s own timeout — up to 30s — the first time a project's preview boots, or
after its server crashed. Before this fix the preview proxy called it directly inside an `async def`
route, on the ONE event loop the whole process shares: a cold start in project A froze every request
for every OTHER open project (and every other async route) for the whole wait, not just project A's
own preview. `run_in_threadpool` moves the blocking call off the loop, so a slow project cannot starve
its neighbours.

Driven through `httpx.AsyncClient` + `ASGITransport` inside ONE `asyncio.run()`, not
`fastapi.testclient.TestClient` from two Python threads: `TestClient` gave both requests their own
event loop when called from separate threads, so it showed the two calls overlapping regardless of
whether the fix was in place — it could not tell a fixed proxy from a broken one. Only one shared
loop, the shape production actually runs under, does.
"""
from __future__ import annotations

import asyncio
import threading
import time

import httpx

from sage.preview.proxy import make_preview_app


def test_two_requests_on_one_event_loop_are_inside_get_upstream_at_the_same_time():
    lock = threading.Lock()
    state = {"concurrent": 0, "max_concurrent": 0}

    def slow_get_upstream() -> str:
        with lock:
            state["concurrent"] += 1
            state["max_concurrent"] = max(state["max_concurrent"], state["concurrent"])
        time.sleep(0.2)
        with lock:
            state["concurrent"] -= 1
        # Nothing is actually listening here — the proxy's own connect-refused handling (502) is
        # fine; this test is about whether BOTH calls overlapped, not what they returned.
        return "http://127.0.0.1:1"

    app = make_preview_app(slow_get_upstream, "")

    async def main() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await asyncio.gather(client.get("/anything"), client.get("/anything"))

    asyncio.run(main())

    assert state["max_concurrent"] == 2, (
        "both requests should have been inside get_upstream() at once — a max of 1 means the second "
        "request's handler never got scheduled until the first's blocking call returned, which is "
        "exactly the whole-process freeze this test guards against"
    )
