"""A second question waits its turn instead of being turned away (#79).

The incident this comes from was not somebody wanting two answers at once. It was somebody with a
second question in their head and nowhere to put it: the first turn would not finish, and the
composer answered "A build is already running". Wall-clock was never the complaint.

So turns still run ONE AT A TIME here. Every test below holds the same invariant the turn lock
always held; what changed is what happens to the turn that loses the race. It is accepted, it says
so, and it waits — on its own HTTP connection, because a streaming turn IS its request and a turn
nobody is connected to would have nowhere to stream (ADR-0013).

Three rules do the work, and each has a test that fails without it:

  * FIFO by enqueue time across the whole Project. `threading.Lock` picks a waiter; a queue picks
    the one that asked first, which is the only order that needs no explaining on screen.
  * A snapshot taken at enqueue and checked at the front of the queue. What a turn was written
    against can move while it waits, and both ways of ignoring that produce a turn that quietly did
    something other than what was on screen.
  * A wedge fails everything waiting behind it, because the lock it is waiting for is never coming
    back (#39), and refuses anything asked afterwards.

And one rule about what does NOT queue: a control with no composer behind it still refuses on the
spot, because a control that sat silently until a long build finished would look like a control
that did nothing (#89).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from sage.build_policy import BuildPolicy
from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator, ResetBusy, TurnBusy
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn

# The real clock, captured before any fixture can patch `time` out from under it. The waits below
# are the test standing beside a background turn, not part of any turn's own timing.
_SLEEP = time.sleep
_NOW = time.monotonic


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the scope classifier is the only caller here."""

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class WedgesOpenCode(FakeOpenCode):
    """A session that reports running for ever and refuses to confirm a stop — #39's incident.

    `on_poll` is the seam this file needs and `test_wedged_turn.py` does not: the turn that wedges
    has to have something QUEUED behind it before it gives up, and the poll loop is the only place
    a test can stand while a turn is still running.
    """

    def __init__(self, workspace: Path, on_poll=None) -> None:
        super().__init__(workspace, [Turn(text="working on it")])
        self.stay_running = True
        self.on_poll = on_poll
        self.polls = 0

    def is_running(self, session_id: str) -> bool:
        self.polls += 1
        assert self.polls <= 200, "the poll loop never gave up on a wedged turn"
        if self.on_poll is not None:
            hook, self.on_poll = self.on_poll, None
            hook()
        return super().is_running(session_id)

    def interrupt(self, session_id: str) -> None:
        # Accepted and changes nothing, which is the failure that wedges: Sage cannot show that
        # OpenCode let go of the working tree, so it keeps the lock rather than run over it.
        self.interrupted += 1


@pytest.fixture(autouse=True)
def _no_runtime_error_wait(monkeypatch):
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building the app\n")
    return t


def _orch(tmp: Path, oc: FakeOpenCode, *, verdict: str = "BUILD",
          build_policy: BuildPolicy | None = None) -> Orchestrator:
    """An orchestrator on the fake agent, with the plan gate off so a prompt reaches the build path."""
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc,
                        build_policy=build_policy)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch


def _stream(events):
    """Drain a turn generator on its own thread, the way the SSE route does (`app.py:_turn_sse`).

    A queued turn blocks until it is its go, so `list()`ing one is a hung suite rather than a failing
    test. `finished` is how a test says "and it is still waiting"."""
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
    """The `pending` row a queued turn yields as it joins the queue, once it has."""
    _wait_for(lambda: any(e.get("type") == "pending" for e in events))
    return next(e for e in events if e["type"] == "pending")


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def _chip(orch: Orchestrator, thread_id: str, name: str) -> dict:
    """A context chip on a Conversation, written where the turn reads it from."""
    store = ThreadStore(orch.project(start_preview=False, seed_app=False).record.path)
    return store.add_context(thread_id, {"resourceId": name, "resourceName": name,
                                         "resourceKind": "dataset"})


# ---- accepted and pending -----------------------------------------------------------------------


def test_a_turn_asked_while_one_is_running_is_accepted_and_pending(tmp_path: Path):
    """The composer's answer to "a turn is running" stops being a refusal. The turn joins the queue,
    says what it is waiting for, and runs when the lock frees — nothing about the turn itself
    changes on the way through."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="Six million rows.")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]

    assert orch._turn_lock.acquire(blocking=False)          # a turn is in flight
    events, finished = _stream(orch.chat_stream(tid, "how many rows?"))
    pending = _pending(events)
    assert finished.wait(0.3) is False                      # waiting, not refused and not running
    assert oc.prompts == []                                 # and nothing of it has run
    orch._release_turn()

    assert finished.wait(20) is True
    assert _of(events, "done")[0]["ok"] is True
    assert "Six million rows." in json.dumps(events)
    # A pending turn is an intention, not a commitment, and the sentence it arrives with says so.
    assert "Nothing has run yet" in pending["message"]
    assert pending["ticket"]
    orch._cancel_chat_idle_save()


def test_the_queue_drains_in_the_order_the_turns_were_asked(tmp_path: Path):
    """FIFO by enqueue time across the whole Project, not round-robin per Conversation. There are no
    tenants here — every pending turn was asked by the same person — so fairness between
    Conversations is not a thing to protect, it is a thing that would reorder somebody's own
    questions against each other for no benefit they can perceive (ADR-0013)."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code",
                      [Turn(text="first answer"), Turn(text="second answer")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    one = orch.create_thread()["id"]
    two = orch.create_thread()["id"]

    assert orch._turn_lock.acquire(blocking=False)
    first, first_done = _stream(orch.chat_stream(one, "asked first"))
    _pending(first)                                         # on the deque before the next is admitted
    second, second_done = _stream(orch.chat_stream(two, "asked second"))
    _pending(second)
    orch._release_turn()

    assert first_done.wait(20) is True
    assert second_done.wait(20) is True
    asked = [p["text"] for p in oc.prompts]
    assert [i for i, t in enumerate(asked) if "asked first" in t] < \
           [i for i, t in enumerate(asked) if "asked second" in t]
    orch._cancel_chat_idle_save()


def test_a_second_build_on_an_app_that_is_already_building_waits(tmp_path: Path):
    """The eligibility rule — a pending turn runs when its Conversation is free AND its target Built
    App is free — is true by construction while one turn runs at a time: when the lock frees, every
    Conversation and every app is free. So there is no scheduler here, and this is the test that
    says the missing scheduler costs nothing. What it must NOT be is a refusal: a Workbench that
    queued Chat and refused Build is a rule people would have to learn instead of guess."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code",
                      [Turn(text="added", writes={"src/Chart.tsx": "chart\n"})])
    orch = _orch(tmp_path, oc)
    app = orch.project(start_preview=False).workspace

    assert orch._turn_lock.acquire(blocking=False)          # this app is already building
    events, finished = _stream(orch.build_stream("add another chart"))
    _pending(events)
    assert finished.wait(0.3) is False
    orch._release_turn()

    assert finished.wait(30) is True
    assert _of(events, "done")[0]["ok"] is True
    assert (app.path / "src" / "Chart.tsx").read_text() == "chart\n"


# ---- the grant, said out loud ------------------------------------------------------------------


def test_a_queued_build_says_when_the_queue_lets_it_go(tmp_path: Path):
    """The row this ticket exists for (#377). A send names its turn so the Stop bar has something to
    match (#371), and the `pending` row takes that name back off a turn that is only waiting. What
    put it back was Chat's `user` frame — and Build has none, because every build path writes its
    user row with `append_history` and does not stream it.

    So a build let out of the queue used to reach the gate and the model's first token with nothing
    on the wire to say the lock was now its own, and for that whole window the bar read "The
    workspace is busy." over the person's own turn. The assertion is deliberately about position and
    not about presence: a `running` row that arrived after the first frame of real work would close
    nothing."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code",
                      [Turn(text="added", writes={"src/Chart.tsx": "chart\n"})])
    orch = _orch(tmp_path, oc)

    assert orch._turn_lock.acquire(blocking=False)          # a turn is already building
    events, finished = _stream(orch.build_stream("add another chart"))
    pending = _pending(events)
    assert [e["type"] for e in events] == ["pending"]        # waiting, and it has said only that
    orch._release_turn()

    assert finished.wait(30) is True
    assert [e["type"] for e in events][:2] == ["pending", "running"]
    # Named by its ticket, the way `pending` is: a reader with two queued turns on one connection
    # has to be able to tell which of them was let go.
    assert events[1]["ticket"] == pending["ticket"]
    assert pending["epoch"] == orch._turn_epoch
    assert events[1]["epoch"] == orch._turn_epoch
    assert _of(events, "done")[0]["ok"] is True


def test_a_turn_that_never_waited_says_nothing_about_being_granted(tmp_path: Path):
    """The uncontended grant stays silent. Its exact identity travels in the HTTP response header,
    outside the established event sequence; direct service callers still see the original rows."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="Six million rows.")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]

    events, finished = _stream(orch.chat_stream(tid, "how many rows?"))

    assert finished.wait(20) is True
    assert _of(events, "pending") == []
    assert _of(events, "running") == []
    assert _of(events, "done")[0]["ok"] is True
    orch._cancel_chat_idle_save()


def test_a_turn_taken_out_of_the_queue_is_never_said_to_be_running(tmp_path: Path):
    """`running` means the lock is this turn's, so the ways a queued turn ends without it must not
    send one. Cancel is the sharp one: a row here would put a Stop button over the very turn the
    person had just decided not to ask."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="Six million rows.")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]

    assert orch._turn_lock.acquire(blocking=False)
    events, finished = _stream(orch.chat_stream(tid, "asked and then thought better of"))
    ticket = _pending(events)["ticket"]

    assert orch.cancel_pending_turn(ticket) is True
    assert finished.wait(20) is True
    assert _of(events, "running") == []
    assert _of(events, "done")[0]["decision"] == "cancelled"
    orch._release_turn()


def test_a_build_abandoned_on_the_grant_hands_the_lock_straight_back(tmp_path: Path):
    """What the new row costs, paid where it is incurred. Before it, the grant and the caller's own
    `try` were the same instant and nothing could be abandoned between them; a yield there is a
    place a client can hang up, and by then the ticket has been popped off the deque and is holding
    the lock — so `cancel` cannot see it and the queue behind it would wait on a turn nobody is
    streaming. Same failure the `pending` yield already guards, one step further along."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="never asked")])
    orch = _orch(tmp_path, oc)

    assert orch._turn_lock.acquire(blocking=False)          # a turn is already building
    gen = orch.build_stream("add another chart")
    assert next(gen)["type"] == "pending"
    orch._release_turn()
    assert next(gen)["type"] == "running"

    gen.close()                                             # the browser hangs up on the grant

    assert orch._turns.running() is None
    assert orch._turn_lock.acquire(blocking=False), "the grant row leaked the turn lock"
    orch._release_turn()
    assert oc.prompts == []                                 # and nothing of it ran


# ---- the snapshot -------------------------------------------------------------------------------


def test_a_pending_turn_whose_chips_moved_does_not_run_and_hands_the_text_back(tmp_path: Path):
    """Chips are read server-side when a turn RUNS and echoed into the transcript bubble client-side
    when it was SENT. Queue those apart and they stop describing the same turn.

    Neither alternative is honest. Running against the snapshot resurrects a chip the person
    deliberately removed; running against live context makes the bubble a lie about what the turn
    was given. So the turn does not run, and the text goes back where they typed it."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="never asked")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]
    chip = _chip(orch, tid, "clickstream")

    assert orch._turn_lock.acquire(blocking=False)
    events, finished = _stream(orch.chat_stream(tid, "how many rows in @clickstream?"))
    _pending(events)
    assert orch.remove_thread_context(tid, chip["id"])            # the chip goes while it waits
    orch._release_turn()

    assert finished.wait(20) is True
    assert oc.prompts == []                                      # the turn never ran
    refusal = _of(events, "error")[0]
    assert "attachments changed" in refusal["message"]
    assert refusal["prompt"] == "how many rows in @clickstream?"  # back to the composer
    assert _of(events, "done")[0]["decision"] == "context changed"
    # Nothing of it reaches the transcript: a turn that did not run leaves no receipt.
    assert orch.thread_history(tid) == []
    # And the lock it declined is handed straight back, for whatever is behind it.
    assert orch._turn_lock.acquire(blocking=False)
    orch._release_turn()


def test_a_pending_build_whose_rail_moved_does_not_run(tmp_path: Path):
    """The other half of the snapshot, and the one only Build has. A build is written for the Built
    App on the rail; resolving that at the front of the queue would build into wherever the rail had
    drifted to, which is the failure #77 fixed for a running turn arriving through the queue
    instead."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="never asked")])
    orch = _orch(tmp_path, oc)
    second = orch.create_app()["id"]
    orch.select_app(orch.list_apps()[0]["id"])                   # written for the first app

    assert orch._turn_lock.acquire(blocking=False)
    events, finished = _stream(orch.build_stream("add a chart"))
    _pending(events)
    orch.select_app(second)                                      # the rail moves while it waits
    orch._release_turn()

    assert finished.wait(20) is True
    assert oc.prompts == []
    assert _of(events, "done")[0]["decision"] == "context changed"
    assert _of(events, "error")[0]["prompt"] == "add a chart"


# ---- the wedge ----------------------------------------------------------------------------------


def test_a_pending_turn_nobody_is_reading_any_more_gives_up_its_place(tmp_path: Path):
    """A queue is a line, and a place in it that nobody is standing in stops everybody behind them.

    Closing the generator is what a dropped client looks like from in here: the wait is held on the
    connection the turn arrived on, so a connection that goes away is a turn that will never take
    the lock it is queued for."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="Six million rows.")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]

    assert orch._turn_lock.acquire(blocking=False)
    abandoned = orch.chat_stream(tid, "asked and walked away from")
    assert next(abandoned)["type"] == "pending"
    abandoned.close()
    assert orch._turns.depth() == 0

    # And the next question is not queued behind a turn that is never coming back.
    events, finished = _stream(orch.chat_stream(tid, "how many rows?"))
    _pending(events)
    orch._release_turn()
    assert finished.wait(20) is True
    assert _of(events, "done")[0]["ok"] is True
    orch._cancel_chat_idle_save()


def test_a_wedged_workspace_refuses_a_new_turn_instead_of_queueing_it(tmp_path: Path):
    """A wedge holds the lock for the life of the process by design (#39), so a turn queued behind
    one is a spinner that never resolves. Refused at the door, naming the only thing that clears it.

    The wedge is set here rather than provoked — `test_wedged_turn.py` owns how a turn gets into this
    state; what this file owns is that the queue does not accept work into it."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="never asked")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]
    assert orch._turn_lock.acquire(blocking=False)
    orch._turn_wedged = True

    events = list(orch.chat_stream(tid, "anything at all"))       # returns, does not wait

    assert _of(events, "pending") == []
    assert _of(events, "done")[0]["decision"] == "wedged"
    assert "stuck on a build" in _of(events, "error")[0]["message"]
    assert orch._turns.depth() == 0


def test_a_wedge_fails_every_turn_waiting_behind_it(tmp_path: Path, monkeypatch):
    """The lock the queue is waiting on is never coming back, so waiting is the one thing these
    turns must not be left doing. Each is failed where it stands, loudly, with the restart
    sentence — a held connection and a spinner is the failure this replaces."""
    queued: dict = {}

    def queue_one_behind_it() -> None:
        # Standing inside the wedging turn's own poll loop: the lock is held, so this build joins
        # the queue rather than starting.
        events, finished = _stream(orch.build_stream("and while you're there"))
        queued["events"], queued["finished"] = events, finished
        _pending(events)

    oc = WedgesOpenCode(tmp_path / "mnt" / "code", on_poll=queue_one_behind_it)
    policy = replace(BuildPolicy(), quiet_timeout_seconds=0.5,
                     open_tool_quiet_timeout_seconds=0.5, stop_grace_seconds=0.5)
    orch = _orch(tmp_path, oc, build_policy=policy)

    wedging = list(orch.build_stream("add a chart"))

    assert _of(wedging, "done")[0]["decision"] == "wedged"
    assert orch._turn_lock.locked() is True                       # held for good, on purpose
    assert queued["finished"].wait(20) is True
    assert _of(queued["events"], "done")[0]["decision"] == "wedged"
    assert "stuck on a build" in _of(queued["events"], "error")[0]["message"]


def test_the_turn_state_route_reports_a_wedge_and_the_queue_depth(tmp_path: Path, monkeypatch):
    """`turn_busy()` answers "not running" for a wedged workspace on purpose, and a client could not
    see why. That was a fine answer while the next send got an immediate refusal that said so. Under
    a queue the send waits instead, so the reason has to be readable without sending anything."""
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="never asked")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    assert client.get("/api/project/build/state").json() == {
        "running": False, "wedged": False, "turn_epoch": orch._turn_epoch,
        "pending": 0, "running_turn": None}

    assert orch._turn_lock.acquire(blocking=False)
    events, finished = _stream(orch.chat_stream(tid, "anything at all"))
    _pending(events)
    assert client.get("/api/project/build/state").json() == {
        "running": True, "wedged": False, "turn_epoch": orch._turn_epoch,
        "pending": 1, "running_turn": None}

    orch._turn_wedged = True
    orch._turns.fail_pending()
    assert finished.wait(20) is True
    assert client.get("/api/project/build/state").json() == {
        "running": False, "wedged": True, "turn_epoch": orch._turn_epoch,
        "pending": 0, "running_turn": None}


# ---- Stop, and Cancel, which are not the same control -------------------------------------------


def test_stop_ends_one_turn_and_the_queue_advances(tmp_path: Path):
    """"One project runs one turn, so there is one thing to interrupt" was true and is not any more.
    Stop targets the turn that is running: you stopped that answer, not your other questions. A
    timeout or an error advances the queue too, because those are answers as well."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code",
                      [Turn(text="never emitted"), Turn(text="Six million rows.")])
    oc.stay_running = True                                        # the first turn will not finish
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]

    running, running_done = _stream(orch.chat_stream(tid, "the one that hangs"))
    _wait_for(orch.turn_busy)
    queued, queued_done = _stream(orch.chat_stream(tid, "the one asked after it"))
    _pending(queued)

    orch.stop_build()

    assert running_done.wait(30) is True
    assert _of(running, "done")[0]["decision"] == "stopped"
    # The queue advanced rather than being emptied with it.
    assert queued_done.wait(30) is True
    assert _of(queued, "done")[0]["ok"] is True
    assert "Six million rows." in json.dumps(queued)
    orch._cancel_chat_idle_save()


def test_cancel_drops_a_pending_turn_without_touching_the_running_one(tmp_path: Path):
    """A separate control, because they answer different questions. Stop is "I have seen enough of
    this answer"; Cancel is "I have changed my mind about asking". A Stop that emptied the queue
    would throw away questions somebody still wants answered."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="Six million rows.")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]

    running, running_done = _stream(orch.chat_stream(tid, "the one that is running"))
    _wait_for(orch.turn_busy)
    queued, queued_done = _stream(orch.chat_stream(tid, "the one asked after it"))
    ticket = _pending(queued)["ticket"]

    assert orch.cancel_pending_turn(ticket) is True
    assert queued_done.wait(20) is True
    assert _of(queued, "done")[0]["decision"] == "cancelled"

    # The running turn is untouched: it was never interrupted and its answer still arrives.
    assert oc.interrupted == 0
    assert running_done.wait(30) is True
    assert _of(running, "done")[0]["ok"] is True
    assert "Six million rows." in json.dumps(running)
    # Cancelling twice is not an error: the turn may have started between the click and the call.
    assert orch.cancel_pending_turn(ticket) is False
    orch._cancel_chat_idle_save()


def test_the_queue_lives_in_memory_and_dies_with_the_process(tmp_path: Path):
    """Not persisted, deliberately. A workspace restart is the remedy for a wedge, and a queue that
    survived it would replay the turns that were stuck behind the wedge — possibly the ones that
    caused it (ADR-0013)."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="never asked")])
    orch = _orch(tmp_path, oc, verdict="CHAT")
    tid = orch.create_thread()["id"]

    assert orch._turn_lock.acquire(blocking=False)
    events, finished = _stream(orch.chat_stream(tid, "anything at all"))
    ticket = _pending(events)["ticket"]

    project = orch.project(start_preview=False, seed_app=False).record.path
    written = [p for p in project.rglob("*") if p.is_file() and ticket in p.read_text(errors="ignore")]
    assert written == []

    # A restart is a new process, so a new Orchestrator over the same workspace is the closest a
    # test gets to one: it inherits the Threads on disk and none of the queue.
    assert _orch(tmp_path, oc, verdict="CHAT")._turns.depth() == 0

    assert orch.cancel_pending_turn(ticket) is True
    assert finished.wait(20) is True
    orch._release_turn()


# ---- what does not queue ------------------------------------------------------------------------


def test_the_controls_with_no_composer_behind_them_still_refuse(tmp_path: Path):
    """The scope line, and the reason this change is smaller than a grep for the turn lock suggests.

    Only the three streaming turn entry points queue, because only they have somewhere to put a
    "queued" row and somebody watching it. New app, Delete, Publish, Reset and the non-streaming
    build are buttons: one that sat silently until a long build finished would look like a control
    that did nothing (#89). Three answer shapes, all immediate."""
    oc = FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="never asked")])
    orch = _orch(tmp_path, oc)

    assert orch._turn_lock.acquire(blocking=False)
    try:
        with pytest.raises(TurnBusy):
            orch.create_app()
        with pytest.raises(ResetBusy):
            orch.reset_app()
        refused = orch.build("add a chart")
    finally:
        orch._release_turn()

    assert refused["decision"] == "busy"
    assert "already running" in refused["message"]
    assert orch._turns.depth() == 0          # nothing of theirs is waiting anywhere
