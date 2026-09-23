"""Stop refers to the turn you can see; the spinner refers to the Project (#126).

#79 shipped the queue and left Stop on the pre-queue model. ADR-0013 had already decided the right
behaviour — *"Stop targets the running turn of one Conversation and the queue advances"* — but
`stop_build()` still interrupts whichever turn holds the project-wide lock, and both Stop bars render
off one project-wide boolean. So stopping a Build advances the queue into a Chat turn, the Build
spinner stays on, and a second press kills a question nobody aimed at.

The fix is an identity, not a second lock. Turns still run ONE AT A TIME here; what changes is that
the server can say WHICH turn is running, so a control can refuse to fire at one it was not aimed at.

`turn_state()` is the whole surface:

  * `running_turn` names the running turn as `{kind, conversation}` — the pair the UI gates its Stop
    bar on. Conversation alone is not enough: a Chat turn and a Build turn in one Conversation are
    both yours, and only one of them is on screen.
  * It is None when nothing that carries an identity holds the lock. A wedge reports None because the
    wedge has its own sentence naming the restart, and a Stop over it is a button that cannot work.
    Publish, reset and the other raw-lock callers report None because they never queued and have no
    ticket — which is also what stops Stop being offered during a publish.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

_SLEEP = time.sleep
_NOW = time.monotonic


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class WatchedOpenCode(FakeOpenCode):
    """Records what `turn_state()` said while a turn was genuinely running.

    The poll loop is the only place a test can stand while a turn still holds the lock. Sampling from
    the outside races the handover instead: `interrupt()` clears `stay_running`, so the moment Stop
    lands the queued turn starts and finishes on its own, and an assertion made after
    `finished.wait()` has already missed it."""

    def __init__(self, workspace: Path, turns: list[Turn] | None = None) -> None:
        super().__init__(workspace, turns)
        self.orch: Orchestrator | None = None
        self.seen: list[dict] = []

    def is_running(self, session_id: str) -> bool:
        if self.orch is not None:
            self.seen.append(self.orch.turn_state())
        return super().is_running(session_id)

    def running_kinds(self) -> list[str]:
        """The kinds seen holding the lock, in order, without the repeats a poll loop produces."""
        out: list[str] = []
        for state in self.seen:
            turn = state.get("running_turn")
            if turn and (not out or out[-1] != turn["kind"]):
                out.append(turn["kind"])
        return out


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building the app\n")
    return t


def _orch(tmp: Path, oc: FakeOpenCode, *, verdict: str = "BUILD") -> Orchestrator:
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    if isinstance(oc, WatchedOpenCode):
        oc.orch = orch
    return orch


def _stream(events):
    seen: list[dict] = []
    finished = threading.Event()

    def pump() -> None:
        try:
            for ev in events:
                seen.append(ev)   # noqa: PERF402 — one at a time, so a test can read it as it fills
        finally:
            finished.set()

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return seen, finished


def _wait_for(predicate, timeout: float = 20.0) -> None:
    deadline = _NOW() + timeout
    while _NOW() < deadline:
        if predicate():
            return
        _SLEEP(0.01)
    raise AssertionError("timed out waiting for the turn queue")


def _pending(events: list[dict]) -> dict:
    _wait_for(lambda: any(e.get("type") == "pending" for e in events))
    return next(e for e in events if e["type"] == "pending")


@pytest.mark.parametrize(
    ("path", "body", "method"),
    [
        ("/api/project/build/stream", {"prompt": "build it"}, "build_stream"),
        ("/api/project/build/approve", {}, "approve_stream"),
        ("/api/threads/thread_a/chat/stream", {"prompt": "answer it"}, "chat_stream"),
        ("/api/threads/thread_a/handoff/decline", {}, "decline_handoff_stream"),
    ],
)
def test_each_stream_header_is_the_exact_ticket_passed_to_the_service(
        monkeypatch, path: str, body: dict, method: str):
    """Headers arrive before SSE, so Stop can bind the ticket without adding a public event."""
    from sage.orchestrator import app as appmod

    class Streams:
        def __init__(self) -> None:
            self.turn_ids: dict[str, str] = {}
            self.released: list[str] = []

        def prepare_stream_turn(self, turn_id: str, **_kwargs):
            return type("Ticket", (), {
                "id": turn_id, "sequence": 41, "epoch": "boot_test"})(), "running"

        def release_stream_turn(self, ticket) -> None:
            self.released.append(ticket.id)

        def _stream(self, name: str, kwargs: dict):
            self.turn_ids[name] = kwargs["turn_ticket"].id
            yield {"type": "done", "ok": True}

        def build_stream(self, *args, **kwargs):
            return self._stream("build_stream", kwargs)

        def approve_stream(self, *args, **kwargs):
            return self._stream("approve_stream", kwargs)

        def chat_stream(self, *args, **kwargs):
            return self._stream("chat_stream", kwargs)

        def decline_handoff_stream(self, *args, **kwargs):
            return self._stream("decline_handoff_stream", kwargs)

    streams = Streams()
    monkeypatch.setattr(appmod, "orchestrator", streams)

    response = TestClient(appmod.control_app).post(path, json=body)

    turn_id = response.headers["X-Sage-Turn-Id"]
    assert turn_id.startswith("turn_")
    assert streams.turn_ids[method] == turn_id
    assert streams.released == [turn_id]
    assert response.headers["X-Sage-Turn-State"] == "running"
    assert response.headers["X-Sage-Turn-Sequence"] == "41"
    assert response.headers["X-Sage-Turn-Epoch"] == "boot_test"
    assert '"type": "running"' not in response.text


def test_stop_between_response_headers_and_body_cancels_before_model_work(monkeypatch, tmp_path: Path):
    """StreamingResponse publishes headers before it starts its lazy generator. The exact ticket
    must already be stoppable in that gap, and admission must consume the Stop once."""
    from starlette.requests import Request

    from sage.orchestrator import app as appmod

    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="must not run")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    monkeypatch.setattr(appmod, "orchestrator", orch)

    response = appmod.build_stream({"prompt": "build it", "conversation": tid})
    turn_id = response.headers["X-Sage-Turn-Id"]

    async def stop_then_read() -> tuple[dict, str]:
        payload = json.dumps({"kind": "build", "conversation": tid,
                              "app": orch.project().workspace.app_id,
                              "turnId": turn_id}).encode()
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}

        request = Request({"type": "http", "method": "POST",
                           "path": "/api/project/build/stop",
                           "headers": [(b"content-type", b"application/json")]}, receive)
        stopped = await appmod.stop_build(request)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return json.loads(stopped.body), "".join(chunks)

    stopped, body = asyncio.run(stop_then_read())

    assert stopped == {"stopped": True, "turnId": turn_id}
    assert [json.loads(line.removeprefix("data: ")) for line in body.splitlines()
            if line.startswith("data: ")] == [
                {"type": "done", "ok": False, "decision": "cancelled"},
            ]
    assert oc.prompts == []
    assert orch.stop_build(turn_id=turn_id) is False


@pytest.mark.parametrize("known_thread", [False, True])
def test_stop_before_a_decline_body_prevents_every_branch(
        monkeypatch, tmp_path: Path, known_thread: bool):
    """Unknown-thread output and no-pending suppression both sit behind the admitted ticket."""
    from sage.orchestrator import app as appmod
    from sage.workspace.threads import ThreadStore

    oc = FakeOpenCode(tmp_path / "mnt" / "code", [])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"] if known_thread else "th_nope"
    monkeypatch.setattr(appmod, "orchestrator", orch)
    store = ThreadStore(orch.project(start_preview=False).record.path)
    before = store.read_handoffs(tid) if known_thread else None

    response = appmod.decline_handoff(tid)
    turn_id = response.headers["X-Sage-Turn-Id"]
    assert orch.stop_build(kind="chat", conversation=tid, turn_id=turn_id) is True

    async def consume() -> list[dict]:
        events = []
        async for chunk in response.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            events.extend(json.loads(line.removeprefix("data: "))
                          for line in text.splitlines() if line.startswith("data: "))
        return events

    assert asyncio.run(consume()) == [
        {"type": "done", "ok": False, "decision": "cancelled"}]
    if known_thread:
        assert store.read_handoffs(tid) == before


def test_a_queued_no_pending_decline_waits_before_it_suppresses(
        monkeypatch, tmp_path: Path):
    """The route may publish pending, but it cannot mutate the Thread while A owns the turn."""
    from sage.orchestrator import app as appmod
    from sage.workspace.threads import ThreadStore

    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="A ran")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    build_a = orch.build_stream("A", conversation=tid, turn_id="turn_a")
    next(build_a)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    store = ThreadStore(orch.project(start_preview=False).record.path)
    response = appmod.decline_handoff(tid)
    assert response.headers["X-Sage-Turn-State"] == "pending"

    events: list[dict] = []
    finished = threading.Event()

    def consume() -> None:
        async def read() -> None:
            async for chunk in response.body_iterator:
                text = chunk.decode() if isinstance(chunk, bytes) else chunk
                events.extend(json.loads(line.removeprefix("data: "))
                              for line in text.splitlines() if line.startswith("data: "))
        try:
            asyncio.run(read())
        finally:
            finished.set()

    threading.Thread(target=consume, daemon=True).start()
    _wait_for(lambda: any(event.get("type") == "pending" for event in events))
    assert store.read_handoffs(tid) == []

    list(build_a)
    assert finished.wait(10)
    assert [event["type"] for event in events] == ["pending", "running", "done"]
    assert events[-1] == {"type": "done", "ok": True, "decision": "suppressed"}
    assert [entry["status"] for entry in store.read_handoffs(tid)] == ["suppressed"]


def test_decline_control_commit_finishes_before_a_later_exact_stop(
        monkeypatch, tmp_path: Path):
    """The production SSE pump holds the control boundary through suppression and release.

    A Stop that arrives inside that commit must wait, then find its exact ticket gone. It must not
    leave a flag for the next turn.
    """
    from sage.orchestrator import app as appmod
    from sage.workspace.threads import ThreadStore

    oc = FakeOpenCode(tmp_path / "mnt" / "code", [])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    monkeypatch.setattr(appmod, "orchestrator", orch)
    store = ThreadStore(orch.project(start_preview=False).record.path)
    original_suppress = ThreadStore.suppress_handoff
    suppress_entered = threading.Event()
    release_suppress = threading.Event()

    def blocked_suppress(self, thread_id):
        suppress_entered.set()
        assert release_suppress.wait(10)
        return original_suppress(self, thread_id)

    monkeypatch.setattr(ThreadStore, "suppress_handoff", blocked_suppress)
    response = appmod.decline_handoff(tid)
    turn_id = response.headers["X-Sage-Turn-Id"]
    events: list[dict] = []
    response_finished = threading.Event()

    def consume() -> None:
        async def read() -> None:
            async for chunk in response.body_iterator:
                text = chunk.decode() if isinstance(chunk, bytes) else chunk
                events.extend(json.loads(line.removeprefix("data: "))
                              for line in text.splitlines() if line.startswith("data: "))
        try:
            asyncio.run(read())
        finally:
            response_finished.set()

    threading.Thread(target=consume, daemon=True).start()
    assert suppress_entered.wait(10)
    stop_result: list[bool] = []
    stop_started = threading.Event()

    def stop() -> None:
        stop_started.set()
        stop_result.append(orch.stop_build(
            kind="chat", conversation=tid, turn_id=turn_id))

    stopper = threading.Thread(target=stop, daemon=True)
    stopper.start()
    assert stop_started.wait(10)
    assert stopper.is_alive()
    release_suppress.set()
    stopper.join(10)

    assert response_finished.wait(10)
    assert stop_result == [False]
    assert events == [{"type": "done", "ok": True, "decision": "suppressed"}]
    assert [entry["status"] for entry in store.read_handoffs(tid)] == ["suppressed"]
    assert orch.project().stop_requested is False


def test_exact_stop_before_decline_control_commit_aborts_without_a_stale_flag(
        monkeypatch, tmp_path: Path):
    """Stop wins after the production pump claims the ticket but before the control commit.

    Decline consumes that Stop without suppression. Its next route turn then completes normally.
    """
    from sage.orchestrator import app as appmod
    from sage.workspace.threads import ThreadStore

    oc = FakeOpenCode(tmp_path / "mnt" / "code", [])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    monkeypatch.setattr(appmod, "orchestrator", orch)
    store = ThreadStore(orch.project(start_preview=False).record.path)
    response = appmod.decline_handoff(tid)
    turn_id = response.headers["X-Sage-Turn-Id"]
    original_project = orch.project
    before_commit = threading.Event()
    resume_commit = threading.Event()
    paused = False

    def pause_pump_before_commit(*args, **kwargs):
        nonlocal paused
        project = original_project(*args, **kwargs)
        running = orch._turns.running()
        if (threading.current_thread().name == "sage-decline_handoff" and not paused
                and running is not None and running.id == turn_id and running.claimed):
            paused = True
            before_commit.set()
            assert resume_commit.wait(10)
        return project

    monkeypatch.setattr(orch, "project", pause_pump_before_commit)
    events: list[dict] = []
    response_finished = threading.Event()

    def consume(target, output: list[dict], finished: threading.Event) -> None:
        async def read() -> None:
            async for chunk in target.body_iterator:
                text = chunk.decode() if isinstance(chunk, bytes) else chunk
                output.extend(json.loads(line.removeprefix("data: "))
                              for line in text.splitlines() if line.startswith("data: "))
        try:
            asyncio.run(read())
        finally:
            finished.set()

    threading.Thread(target=consume, args=(response, events, response_finished), daemon=True).start()
    assert before_commit.wait(10)
    assert orch.stop_build(kind="chat", conversation=tid, turn_id=turn_id) is True
    resume_commit.set()

    assert response_finished.wait(10)
    assert [event["type"] for event in events] == ["stopped", "done"]
    assert events[-1] == {"type": "done", "ok": False, "decision": "stopped"}
    assert store.read_handoffs(tid) == []
    assert orch.project().stop_requested is False

    next_response = appmod.decline_handoff(tid)
    next_events: list[dict] = []
    next_finished = threading.Event()
    threading.Thread(target=consume, args=(next_response, next_events, next_finished),
                     daemon=True).start()
    assert next_finished.wait(10)
    assert next_events == [{"type": "done", "ok": True, "decision": "suppressed"}]
    assert orch.project().stop_requested is False


def test_decline_setup_failure_releases_only_its_ticket_and_promotes_once(
        monkeypatch, tmp_path: Path):
    """Failure before Chat's inner cleanup exists must release decline, not its successor."""
    from sage.orchestrator import service as service_mod
    from sage.workspace.threads import ThreadStore

    oc = FakeOpenCode(tmp_path / "mnt" / "code", [])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch.project(start_preview=False).record.path)
    store.append_history(tid, {"type": "user", "text": "build a dashboard"})
    store.append_history(tid, {"type": "handoff-suggest", "reason": "explicit"})
    store.append_history(tid, {"type": "done", "ok": True, "decision": "handoff"})

    decline_ticket, _ = orch.prepare_stream_turn(
        "turn_decline", kind="chat", conversation=tid)
    ticket_b, _ = orch.prepare_stream_turn("turn_b", kind="chat", conversation=tid)
    ticket_c, _ = orch.prepare_stream_turn("turn_c", kind="chat", conversation=tid)

    def fail_start(*_args, **_kwargs):
        raise RuntimeError("timing setup failed")

    monkeypatch.setattr(service_mod.timing, "start_turn", fail_start)
    with pytest.raises(RuntimeError, match="timing setup failed"):
        list(orch.decline_handoff_stream(tid, turn_ticket=decline_ticket))

    state = orch.turn_state()
    assert state["running_turn"]["turnId"] == "turn_b"
    assert state["pending"] == 1
    assert orch.stop_build(turn_id=ticket_b.id) is True
    assert orch.turn_state()["running_turn"]["turnId"] == "turn_c"
    assert orch.stop_build(turn_id=ticket_c.id) is True
    assert orch.turn_state()["running_turn"] is None


def test_exact_stop_cancels_a_route_ticket_after_admission_but_before_pending_is_delivered(
        monkeypatch, tmp_path: Path):
    """A route reservation becomes a waiting queue ticket before its pending byte reaches the
    browser. Exact Stop must find the same ticket on either side of that transition."""
    from starlette.requests import Request

    from sage.orchestrator import app as appmod

    oc = FakeOpenCode(tmp_path / "mnt" / "code",
                      [Turn(text="A ran"), Turn(text="B must not run")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    build_a = orch.build_stream("A", conversation=tid, turn_id="turn_a")
    next(build_a)
    prompts_before_b = len(oc.prompts)
    monkeypatch.setattr(appmod, "orchestrator", orch)

    pending_ready = threading.Event()
    deliver_pending = threading.Event()

    def withhold_pending(events, _what):
        first = next(events)
        assert first["type"] == "pending"
        pending_ready.set()
        assert deliver_pending.wait(10)
        yield f"data: {json.dumps(first)}\n\n"
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    monkeypatch.setattr(appmod, "_turn_sse", withhold_pending)
    response = appmod.build_stream({"prompt": "B", "conversation": tid})
    turn_id = response.headers["X-Sage-Turn-Id"]

    async def stop_while_pending_is_withheld() -> tuple[dict, str]:
        chunks = []

        async def consume():
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)

        reader = asyncio.create_task(consume())
        assert await asyncio.to_thread(pending_ready.wait, 10)
        assert orch.stop_build(kind="build", conversation=tid, app="another-app",
                               turn_id=turn_id) is False
        payload = json.dumps({"kind": "build", "conversation": tid,
                              "app": orch.project().workspace.app_id,
                              "turnId": turn_id}).encode()
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}

        request = Request({"type": "http", "method": "POST",
                           "path": "/api/project/build/stop",
                           "headers": [(b"content-type", b"application/json")]}, receive)
        stopped = await appmod.stop_build(request)
        deliver_pending.set()
        await reader
        return json.loads(stopped.body), "".join(chunks)

    stopped, body = asyncio.run(stop_while_pending_is_withheld())

    assert stopped == {"stopped": True, "turnId": turn_id}
    events = [json.loads(line.removeprefix("data: ")) for line in body.splitlines()
              if line.startswith("data: ")]
    assert [event["type"] for event in events] == ["pending", "done"]
    assert events[-1] == {"type": "done", "ok": False, "decision": "cancelled"}
    assert len(oc.prompts) == prompts_before_b
    list(build_a)


def test_three_pre_body_route_tickets_follow_server_admission_order(monkeypatch, tmp_path: Path):
    """Header delivery order cannot reorder A, B, C. Cancel B, then cancelling running A must
    promote C, which is the only exact ticket that can be stopped as running."""
    from sage.orchestrator import app as appmod

    oc = FakeOpenCode(tmp_path / "mnt" / "code",
                      [Turn(text="must not run"), Turn(text="must not run")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    monkeypatch.setattr(appmod, "orchestrator", orch)

    responses = [appmod.build_stream({"prompt": prompt, "conversation": tid})
                 for prompt in ("A", "B", "C")]
    ids = [response.headers["X-Sage-Turn-Id"] for response in responses]
    assert [response.headers["X-Sage-Turn-State"] for response in responses] == [
        "running", "pending", "pending"]
    assert [response.headers["X-Sage-Turn-Sequence"] for response in responses] == [
        "1", "2", "3"]
    assert [response.headers["X-Sage-Turn-Epoch"] for response in responses] == [
        orch._turn_epoch, orch._turn_epoch, orch._turn_epoch]

    # Read the static headers in the opposite order. The queue order remains A, B, C.
    assert [responses[index].headers["X-Sage-Turn-Id"] for index in (2, 1, 0)] == ids[::-1]
    app_id = orch.project().workspace.app_id
    target = {"kind": "build", "conversation": tid, "app": app_id}

    assert orch.stop_build(**target, turn_id=ids[1]) is True
    assert orch.stop_build(**target, turn_id=ids[0]) is True
    assert orch.turn_state()["running_turn"]["turnId"] == ids[2]
    assert orch.turn_state()["running_turn"]["sequence"] == 3
    assert orch.turn_state()["running_turn"]["epoch"] == orch._turn_epoch
    assert orch.stop_build(**target, turn_id=ids[1]) is False
    assert orch.stop_build(**target, turn_id=ids[2]) is True
    assert orch.turn_state()["running_turn"] is None

    async def consume_all() -> list[list[dict]]:
        out = []
        for response in responses:
            events = []
            async for chunk in response.body_iterator:
                text = chunk.decode() if isinstance(chunk, bytes) else chunk
                events.extend(json.loads(line.removeprefix("data: "))
                              for line in text.splitlines() if line.startswith("data: "))
            out.append(events)
        return out

    events = asyncio.run(consume_all())
    assert all(turn[-1] == {"type": "done", "ok": False, "decision": "cancelled"}
               for turn in events)
    assert oc.prompts == []


def test_response_cleanup_releases_an_unclaimed_ticket_and_promotes_the_next(tmp_path: Path):
    """A disconnect can run the response background task before either lazy body starts."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    ticket_a, state_a = orch.prepare_stream_turn(
        "turn_a", kind="build", conversation=tid, app=True)
    ticket_b, state_b = orch.prepare_stream_turn(
        "turn_b", kind="build", conversation=tid, app=True)
    assert (state_a, state_b) == ("running", "pending")

    orch.release_stream_turn(ticket_a)
    assert orch.turn_state()["running_turn"]["turnId"] == "turn_b"
    orch.release_stream_turn(ticket_b)
    assert orch.turn_state()["running_turn"] is None
    assert oc.prompts == []


def test_an_unknown_exact_ticket_cannot_poison_a_later_turn(tmp_path: Path):
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="ran")])
    orch = _orch(tmp_path, oc)

    assert orch.stop_build(turn_id="turn_future") is False
    events = list(orch.build_stream("build it", turn_id="turn_future"))

    assert oc.prompts
    assert events[-1]["type"] == "done"


# ---- the running turn has a name ----------------------------------------------------------------


def test_turn_state_names_the_running_chat_turn(tmp_path: Path):
    """`running` said a turn was going; it never said which. The Stop bar cannot gate on a boolean
    that a Chat turn, a Build turn and another tab's turn all set alike."""
    oc = WatchedOpenCode(tmp_path / "mnt" / "code", [Turn(text="Six million rows.")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]

    _events, finished = _stream(orch.chat_stream(tid, "how many rows?"))
    assert finished.wait(30) is True

    running = [s["running_turn"] for s in oc.seen if s.get("running_turn")]
    assert running, "no turn identity was reported while the turn was running"
    assert running[0]["kind"] == "chat"
    assert running[0]["conversation"] == tid
    orch._cancel_chat_idle_save()


def test_turn_state_names_the_running_build_turn(tmp_path: Path):
    """Approve is not a third kind. You approve a plan from Build, and the kind exists to answer
    "can I stop this from where I am standing" — so a third value would split a screen that never
    splits."""
    oc = WatchedOpenCode(tmp_path / "mnt" / "code",
                         [Turn(text="added", writes={"src/Chart.tsx": "chart\n"})])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    _events, finished = _stream(orch.build_stream("add a chart", conversation=tid))
    assert finished.wait(30) is True

    running = [s["running_turn"] for s in oc.seen if s.get("running_turn")]
    assert running, "no turn identity was reported while the turn was running"
    assert running[0]["kind"] == "build"
    assert running[0]["conversation"] == tid


# ---- the reproduction ---------------------------------------------------------------------------


def test_stopping_a_build_hands_the_lock_to_a_chat_turn_that_says_so(tmp_path: Path):
    """#126's report, as steps. Stop the Build, and the queue advances exactly as ADR-0013 says it
    should — you stopped that answer, not your other questions. What must NOT survive is the Build
    spinner: the turn now holding the lock is a Chat turn, and it says so, so the Build page has
    something to stop claiming."""
    oc = WatchedOpenCode(tmp_path / "mnt" / "code",
                         [Turn(text="building"), Turn(text="Six million rows.")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    oc.stay_running = True                                   # a build turn that will not end on its own
    _build, build_done = _stream(orch.build_stream("add a chart", conversation=tid))
    _wait_for(lambda: any(s.get("running_turn") for s in oc.seen))

    chat, chat_done = _stream(orch.chat_stream(tid, "how many rows?"))
    _pending(chat)                                           # queued behind the build, not refused

    orch.stop_build()
    assert build_done.wait(30) is True
    assert chat_done.wait(30) is True

    assert oc.running_kinds() == ["build", "chat"]
    orch._cancel_chat_idle_save()


def test_a_stop_aimed_at_the_build_does_not_kill_the_chat_turn_behind_it(tmp_path: Path):
    """The second press, and the race that arrives at the same place without one: you press Stop,
    the turn ends on its own, the queue advances, and the POST lands on the next turn. A Stop that
    names what it meant to stop is a no-op once that turn is gone."""
    oc = WatchedOpenCode(tmp_path / "mnt" / "code",
                         [Turn(text="building"), Turn(text="Six million rows.")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    oc.stay_running = True
    _build, build_done = _stream(orch.build_stream("add a chart", conversation=tid))
    _wait_for(lambda: any(s.get("running_turn") for s in oc.seen))

    chat, chat_done = _stream(orch.chat_stream(tid, "how many rows?"))
    _pending(chat)

    orch.stop_build(kind="build", conversation=tid)          # stops the build
    _wait_for(lambda: (orch.turn_state().get("running_turn") or {}).get("kind") == "chat")

    before = oc.interrupted
    orch.stop_build(kind="build", conversation=tid)          # the second press, mis-aimed
    assert oc.interrupted == before, "a Stop aimed at the build interrupted the chat turn"

    assert build_done.wait(30) is True
    assert chat_done.wait(30) is True
    assert "Six million rows." in json.dumps(chat), "the chat turn was killed before it answered"
    orch._cancel_chat_idle_save()


# ---- a lock holder that is not a turn -----------------------------------------------------------


def test_a_raw_lock_holder_reports_a_busy_project_and_no_turn(tmp_path: Path):
    """Publish, reset, create and delete app, the non-streaming build and the coalesced Chat save
    take the lock without queueing, so they have no ticket and no identity. They are busy, not
    stoppable — which is what stops the Stop bar rendering over a publish."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [])
    orch = _orch(tmp_path, oc)

    assert orch._turn_lock.acquire(blocking=False)
    try:
        state = orch.turn_state()
        assert state["running"] is True
        assert state["running_turn"] is None
    finally:
        orch._release_turn()


def test_an_unscoped_stop_cannot_flag_a_raw_lock_holder_or_the_next_turn(tmp_path: Path):
    """Legacy Stop still needs a real turn ticket. A publish/reset lock has none."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="next turn survives")])
    orch = _orch(tmp_path, oc)

    assert orch._turn_lock.acquire(blocking=False)
    try:
        assert orch.stop_build() is False
        assert orch.project().stop_requested is False
    finally:
        orch._release_turn()

    events = list(orch.build_stream("run next", turn_id="turn_next"))
    assert not any(event.get("type") == "stopped" for event in events)
    assert events[-1].get("decision") not in {"stopped", "cancelled"}


def test_a_wedged_lock_reports_no_turn(tmp_path: Path):
    """A wedge keeps the lock for the life of the process by design (#39) and has its own sentence
    naming the restart. A Stop bar over it is a button that cannot work."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [])
    orch = _orch(tmp_path, oc)

    assert orch._turn_lock.acquire(blocking=False)
    orch._turn_wedged = True

    state = orch.turn_state()
    assert state["wedged"] is True
    assert state["running_turn"] is None


def test_a_stop_aimed_at_another_app_does_not_reach_this_build(tmp_path: Path):
    """The Conversation is not the whole identity of a build. The rail is free to move while a turn
    streams (`_pin_turn_app`, #77) and the Build transcript is ONE app's log for one Conversation, so
    a build you switched away from is exactly as invisible as one in another conversation — and a
    Stop gated on the Conversation alone would reach it anyway."""
    oc = WatchedOpenCode(tmp_path / "mnt" / "code", [Turn(text="building")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    oc.stay_running = True
    _build, build_done = _stream(orch.build_stream("add a chart", conversation=tid))
    _wait_for(lambda: any(s.get("running_turn") for s in oc.seen))

    running = orch.turn_state()["running_turn"]
    assert running["app"], "the running build turn did not name the app it writes into"
    assert running["turnId"], "the running build turn did not name its diagnostic identity"

    before = oc.interrupted
    assert orch.stop_build(kind="build", conversation=tid, app="another-app") is False
    assert oc.interrupted == before, "a Stop aimed at another app stopped this build"
    assert orch.stop_build(kind="build", conversation=tid, app=running["app"],
                           turn_id="another-turn") is False
    assert oc.interrupted == before, "a Stop aimed at another turn stopped this build"

    assert orch.stop_build(kind="build", conversation=tid, app=running["app"],
                           turn_id=running["turnId"]) is True
    assert build_done.wait(30) is True


def test_a_stale_turn_id_alone_does_not_reach_the_running_build(tmp_path: Path):
    oc = WatchedOpenCode(tmp_path / "mnt" / "code", [Turn(text="building")])
    orch = _orch(tmp_path, oc)
    gen = orch.build_stream("add a chart", turn_id="turn_current")

    next(gen)
    assert orch.turn_state()["running_turn"]["turnId"] == "turn_current"
    before = oc.interrupted
    assert orch.stop_build(turn_id="a-turn-that-finished") is False
    assert oc.interrupted == before

    list(gen)


def test_a_stale_app_alone_does_not_reach_the_running_build(tmp_path: Path):
    oc = WatchedOpenCode(tmp_path / "mnt" / "code", [Turn(text="building")])
    orch = _orch(tmp_path, oc)
    gen = orch.build_stream("add a chart", turn_id="turn_current")

    next(gen)
    assert orch.turn_state()["running_turn"]["turnId"] == "turn_current"
    before = oc.interrupted
    assert orch.stop_build(app="another-app") is False
    assert oc.interrupted == before

    list(gen)


def test_a_delayed_stop_for_build_a_does_not_stop_build_b_in_the_same_scope(tmp_path: Path):
    """A Stop request keeps A's exact ticket while the queue advances to an identical scope."""
    oc = WatchedOpenCode(tmp_path / "mnt" / "code",
                         [Turn(text="first"), Turn(text="second")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    build_a = orch.build_stream("first build", conversation=tid, turn_id="turn_a")
    next(build_a)
    assert orch.turn_state()["running_turn"]["turnId"] == "turn_a"

    build_b = orch.build_stream("second build", conversation=tid, turn_id="turn_b")
    pending_b = next(build_b)
    assert pending_b["type"] == "pending"

    list(build_a)  # A finishes before its delayed Stop request reaches the route.
    granted_b = next(build_b)
    assert granted_b["type"] == "running"
    assert granted_b["ticket"] == "turn_b"

    current = orch.turn_state()["running_turn"]
    assert current["turnId"] == granted_b["ticket"]
    before = oc.interrupted
    assert orch.stop_build(kind="build", conversation=tid, app=current["app"],
                           turn_id="turn_a") is False
    assert oc.interrupted == before, "A's delayed Stop interrupted B"

    list(build_b)
