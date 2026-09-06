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

import json
import threading
import time
from pathlib import Path

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

    before = oc.interrupted
    assert orch.stop_build(kind="build", conversation=tid, app="another-app") is False
    assert oc.interrupted == before, "a Stop aimed at another app stopped this build"

    assert orch.stop_build(kind="build", conversation=tid, app=running["app"]) is True
    assert build_done.wait(30) is True
