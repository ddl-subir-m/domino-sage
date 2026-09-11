"""A build turn that hears the step finish instead of finding out on the next poll.

Build samples: it reads the transcript, sleeps one second, reads it again. A tool call that finished
the instant after a read therefore sat inside OpenCode for the rest of that second before its card
reached the screen — measured live on 2026-09-07 at 420ms p50, 764ms max, plus up to a second of
dead air at the end of every turn, because "the turn stopped running" is only ever discovered by a
read.

The stream is the doorbell, not the data. Frames from `/event` end the wait early; the transcript
poll is still the only thing that emits a card, so there is one source of truth, no second key space
to dedupe against, and no card whose wording depends on which path found it. That is also what makes
`/event` having no `?after=` stop mattering: nothing is carried on the stream, so a frame that is
missed costs one ordinary 1.0s wait and nothing else.

What these pin, in order: the wait ends on a frame worth reading for; a paragraph's deltas are not
worth reading for and do not end it; and a client that cannot stream at all still produces exactly
the events it produced before any of this existed.
"""
from __future__ import annotations

import gc
import json
import time
from pathlib import Path

from sage import timing
from sage.driver.agent_driver import AgentEvent
from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator, _EventTap
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building an app\n")
    return t


class _Streaming(FakeOpenCode):
    """A fake that also serves `/event`, and counts what the poll loop asks it for.

    `frames` is what the stream hands over, one list per session_events() call. The reads are counted
    because the whole risk of waking on frames is waking too often: every extra wake is a transcript
    read against the same single-threaded server the agent is using."""

    def __init__(self, *a, frames: list[AgentEvent] | None = None, **kw) -> None:
        super().__init__(*a, **kw)
        self._frames = list(frames or [])
        self.streamed: list[dict] = []
        self.opened: list[_Stream] = []
        self.reads = 0

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        self.reads += 1
        return super().messages(session_id, limit=limit)

    def session_events(self, session_id: str, *, directory: str | None = None):
        self.streamed.append({"session": session_id, "directory": directory})
        stream = _Stream(self._frames)
        self.opened.append(stream)
        return stream


class _Stream:
    def __init__(self, frames: list[AgentEvent]) -> None:
        self._frames = frames
        self.closed = False

    def __iter__(self):
        yield from self._frames
        # Then hold the socket the way a real one does: /event never ends of its own accord, and a
        # generator that returned would set `ok` False and quietly turn the wait back into a sleep.
        while not self.closed:
            time.sleep(0.01)

    def close(self) -> None:
        self.closed = True


def _orch(tmp: Path, turns: list[Turn], cls=FakeOpenCode, **kw) -> tuple[Orchestrator, FakeOpenCode]:
    oc = cls(tmp / "mnt" / "code", turns, **kw)
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch, oc


def _tool_done(name: str) -> AgentEvent:
    return AgentEvent(kind="tool_run", payload={"tool": name, "call_id": "c1", "status": "success"})


def _tool_called(name: str) -> AgentEvent:
    return AgentEvent(kind="tool_run", payload={"tool": name, "call_id": "c1", "status": "called"})


def _delta(text: str) -> AgentEvent:
    return AgentEvent(kind="message", payload={"delta": text, "final": False})


class _Client:
    """The one thing _EventTap asks of a client, and nothing else."""

    def __init__(self, frames: list[AgentEvent]) -> None:
        self.stream = _Stream(frames)

    def session_events(self, session_id, *, directory=None):
        return self.stream


# --- the wait itself -----------------------------------------------------------------------------


def test_a_frame_ends_the_wait_early():
    tap = _EventTap(_Client([_tool_done("write")]), "s1")
    t0 = time.monotonic()
    woke = tap.wait(2.0)
    elapsed = time.monotonic() - t0
    tap.close()
    assert woke is True
    # The point of the whole change: a second is not spent finding out.
    assert elapsed < 0.5, f"waited {elapsed:.2f}s for a frame that had already arrived"


def test_a_paragraphs_deltas_are_not_worth_a_read():
    """A model writing prose emits deltas by the dozen and completes no card doing it.

    Waking on those would turn one paragraph into thirty transcript reads — the cost the cheap
    alternative (a shorter blind sleep) pays everywhere, arriving here through the back door. The
    end of the text is a different matter: `final` is the part that lands."""
    tap = _EventTap(_Client([_delta("Hel"), _delta("lo "), _delta("there")]), "s1")
    t0 = time.monotonic()
    woke = tap.wait(0.4)
    elapsed = time.monotonic() - t0
    tap.close()
    assert woke is False
    assert elapsed >= 0.4, "deltas ended the wait; a paragraph is now a read storm"


def test_the_end_of_a_paragraph_is_worth_a_read():
    final = AgentEvent(kind="message", payload={"text": "Hello there", "final": True})
    tap = _EventTap(_Client([_delta("Hel"), final]), "s1")
    woke = tap.wait(2.0)
    tap.close()
    assert woke is True


def test_a_tap_nobody_holds_lets_go_of_the_socket():
    """The net under an abandoned turn, and the reason the reader holds only a weakref.

    A live Thread keeps its target alive, so a tap whose reader is a bound method can never be
    collected — the reader runs until the tap is closed, and closing it is what collection was for.
    The circle made the cleanup unreachable. This is the test that says it isn't."""
    client = _Client([])
    tap = _EventTap(client, "s1")
    del tap
    for _ in range(5):
        gc.collect()
        if client.stream.closed:
            break
        time.sleep(0.05)
    assert client.stream.closed, "the reader kept the tap alive; the socket is held for good"


def test_a_frame_that_nobody_waited_for_still_counts_as_a_stream_that_spoke():
    """`seen_any` is what says a stream carried nothing, and that warning has one job.

    A turn can end with frames still queued — the loop breaks the moment the session reads idle. A
    flag that counted only what was consumed would call that stream silent and send someone looking
    for a session-directory bug that isn't there."""
    tap = _EventTap(_Client([_tool_done("write")]), "s1")
    for _ in range(20):
        if tap.seen_any:
            break
        time.sleep(0.02)
    tap.close()
    assert tap.seen_any, "a frame arrived and the tap still reads as silent"


def test_a_client_that_cannot_stream_waits_out_the_timeout():
    """No stream is not an error, and it is not a fast path either — it is exactly the old sleep."""
    tap = _EventTap(object(), "s1")
    assert tap.ok is False
    t0 = time.monotonic()
    woke = tap.wait(0.3)
    elapsed = time.monotonic() - t0
    assert woke is False
    assert elapsed >= 0.3


# --- the build turn ------------------------------------------------------------------------------


def test_the_build_turn_subscribes_to_its_own_sessions_directory(tmp_path):
    """The directory is the one failure that is silent.

    /event delivers only the events of the directory the connection asks for. Ask for the wrong one
    and the connection succeeds, stays open, and carries nothing — no error anywhere, and the only
    symptom is that the turn is exactly as slow as it was before. So it is pinned: the stream is
    opened on the directory the session was created in, which for Build is the Built App."""
    orch, oc = _orch(tmp_path, [Turn(text="done", writes={"src/App.tsx": "x"})], cls=_Streaming)
    list(orch.build_stream("make a chart"))
    assert oc.streamed, "the build turn never opened the event stream"
    opened = oc.streamed[0]
    created = next(s for s in oc.sessions if s["id"] == opened["session"])
    assert opened["directory"] == created["directory"]


def test_a_streaming_turn_does_not_pay_the_poll_sleep(tmp_path):
    """The before/after, read off the metric that reports it live rather than off the wall clock.

    `poll.sleep_ms` is what scripts/turn-timing.py sums, so this is the same number the live run
    reports, and it is per-wait rather than per-turn — a turn's other costs (git, typecheck) cannot
    drown it, and a loaded CI worker cannot flake it. The fake reports running exactly once, so this
    is ONE wait; a real turn takes hundreds, which is why the size of the win is still something
    only a live builder can report."""
    def run(name: str, frames: list[AgentEvent]) -> list[float]:
        orch, _ = _orch(tmp_path / name, [Turn(text="done", writes={"src/App.tsx": "x"})],
                        cls=_Streaming, frames=frames)
        list(orch.build_stream("make a chart"))
        rec = timing.recent(1)[0]
        return rec.observations.get("poll.sleep_ms", [])

    silent = run("silent", [])
    woken = run("woken", [_tool_done("write")])
    assert silent and woken, "the turn recorded no waits at all"
    assert max(silent) > 900, f"a stream with nothing to say waited {max(silent):.0f}ms, not 1000"
    # The floor is deliberate and is the ceiling here: woken at once, then held so a burst of tool
    # frames cannot become a burst of reads.
    assert max(woken) < 500, f"the wait was not woken: {max(woken):.0f}ms"


def test_a_burst_of_tool_frames_is_not_a_burst_of_reads(tmp_path):
    """The read storm's other door.

    Deltas are excluded outright, but a step that reads six files emits a `called` and a `success`
    for each, and every one of those genuinely IS worth a read. Without a floor the loop would take
    them one at a time — thirteen back-to-back transcript reads out of the single-threaded server
    the agent is running in, which is the cost the whole design exists to avoid."""
    frames = [f(n) for n in ("read", "read", "read", "grep", "glob", "bash")
              for f in (_tool_called, _tool_done)]
    orch, oc = _orch(tmp_path, [Turn(text="done", writes={"src/App.tsx": "x"})],
                     cls=_Streaming, frames=frames)
    list(orch.build_stream("make a chart"))
    assert oc.reads < 10, f"{oc.reads} transcript reads for one step's tool calls"


def test_a_delta_storm_is_not_a_read_storm(tmp_path):
    """Two hundred deltas must not become two hundred transcript reads."""
    orch, oc = _orch(tmp_path, [Turn(text="done", writes={"src/App.tsx": "x"})],
                     cls=_Streaming, frames=[_delta("x") for _ in range(200)])
    list(orch.build_stream("make a chart"))
    assert oc.reads < 10, f"{oc.reads} transcript reads for one paragraph of prose"


def test_a_stopped_turn_lets_go_of_its_stream(tmp_path):
    """Stop is an exit, and an exit that leaks is the one that costs most.

    Stop is pressed on the builds that are going badly, which are the long ones, which are the ones
    someone presses Stop on again. Ten of those used to be ten reader threads parked on /event and
    ten connections held open against the single-threaded server — for the life of the process,
    because nothing else ever ends a read on a stream that has no end."""
    orch, oc = _orch(tmp_path, [Turn(text="done", writes={"src/App.tsx": "x"})],
                     cls=_Streaming, frames=[])
    stream = None
    for ev in orch.build_stream("make a chart"):
        if stream is None and oc.streamed:
            stream = oc.opened[-1]
            orch.project(start_preview=False).stop_requested = True
        if ev.get("type") == "done":
            break
    assert stream is not None and stream.closed, "Stop left the reader on the socket"


def test_a_turn_without_a_stream_emits_what_it_always_did(tmp_path):
    """Degradation is literal: the same turn, the same events, whether or not /event answered."""
    def run(cls, **kw):
        orch, _ = _orch(tmp_path / cls.__name__, [Turn(text="done", writes={"src/App.tsx": "x"})],
                        cls=cls, **kw)
        return [e for e in orch.build_stream("make a chart") if e.get("type") != "phase"]

    streamed = run(_Streaming, frames=[_tool_done("write")])
    polled = run(FakeOpenCode)
    assert [e.get("type") for e in streamed] == [e.get("type") for e in polled]
    assert [e.get("tool") for e in streamed] == [e.get("tool") for e in polled]


# --- the Chat half: a wait that streams what it wakes on -----------------------------------------


def test_a_delta_wakes_the_chat_wait():
    """Chat streams what it drains, so a delta IS the thing worth waking for.

    `wait` above deliberately sleeps through deltas because Build re-reads the transcript and a
    half-written paragraph renders the same either way. Chat has no such luxury: those deltas are
    the answer arriving on screen. Measured 2026-09-11 before this existed — a call producing 946
    chunks over ten seconds delivered them in ten clumps of ninety, because the loop drained once
    and then slept a flat second."""
    tap = _EventTap(_Client([_delta("Hel"), _delta("lo")]), "s1")
    t0 = time.monotonic()
    woke = tap.wait_any(2.0)
    elapsed = time.monotonic() - t0
    tap.close()
    assert woke is True
    assert elapsed < 0.5, f"waited {elapsed:.2f}s for a delta that had already arrived"


def test_the_chat_wait_takes_nothing_off_the_queue():
    """The property that makes this a separate method rather than a flag on `wait`.

    `wait` consumes the frame it wakes on and drops it, which is correct when the transcript is the
    source of every card. Draining is Chat's ONLY source, so a wait that ate a frame would drop
    words out of somebody's answer with nothing to recover them from."""
    tap = _EventTap(_Client([_delta("Hel"), _delta("lo "), _delta("there")]), "s1")
    assert tap.wait_any(2.0) is True
    drained = tap.drain()
    tap.close()
    assert [e.payload.get("delta") for e in drained] == ["Hel", "lo ", "there"], \
        "the wait swallowed a delta"


def test_the_chat_wait_still_bounds_how_often_it_wakes():
    """A delta storm must not become an `is_running` storm against the single-threaded server the
    agent is working on — the same objection `_POLL_FLOOR_S` answers for Build."""
    tap = _EventTap(_Client([_delta(str(i)) for i in range(200)]), "s1")
    t0 = time.monotonic()
    tap.wait_any(2.0, floor=0.2)
    elapsed = time.monotonic() - t0
    tap.close()
    assert elapsed >= 0.2, f"woke after {elapsed:.2f}s; 200 deltas is 200 reads"


def test_a_chat_turn_without_a_stream_waits_out_the_timeout():
    """No stream means the doorbell is never rung, so this degrades to exactly the blind sleep it
    replaced — no branch for the caller to take."""

    class _NoStream:
        pass

    tap = _EventTap(_NoStream(), "s1")
    t0 = time.monotonic()
    woke = tap.wait_any(0.3)
    elapsed = time.monotonic() - t0
    tap.close()
    assert woke is False
    assert elapsed >= 0.3
