"""A build turn that never returns (#39).

The live shape: OpenCode reported the session running, Sage's own log went quiet, and thirty-six
minutes later `/api/project/build/state` still said `{"running": true}`. The poll loop had two exits
once a turn had started and neither could fire, so `_turn_lock` was held for the life of the process
and every later turn in the Project was refused as busy. Restarting the workspace was the only cure.

What these tests pin is the shape of giving up, not the timing of it. Silence is measured from the
last thing OpenCode produced, so a turn still making progress is never at risk however long it runs.
Giving up stops OpenCode FIRST and releases the lock only once the session confirms it stopped —
a lock released under a session that may still be writing is the two-turns-on-one-tree corruption
the lock exists to prevent, arriving from the fix instead of from the bug. A stop that will not
confirm therefore keeps the lock, and says so.

The clock here is scripted, not waited out: `time.sleep` advances a counter that `time.monotonic`
reads, so a poll costs a second of the turn's time and none of the suite's.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator import service as svc
from sage.orchestrator.service import Orchestrator, ResetBusy, TurnBusy
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the scope classifier is the only caller here."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class WedgedOpenCode(FakeOpenCode):
    """A session that reports running for ever and produces nothing after the first message.

    `poll_cap` is what makes this a test rather than a hang. Before the give-up existed the loop
    below spun here without end, and a test that never returns reports nothing at all — so the fake
    counts polls and fails out loud once the loop has had far more of them than any exit needs.

    `stops` is the second failure the give-up has to survive: an interrupt that is accepted and
    changes nothing, leaving a session that may still be writing to the working tree.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 stops: bool = True, poll_cap: int = 600) -> None:
        super().__init__(workspace, turns)
        self.stay_running = True
        self.stops = stops
        self.poll_cap = poll_cap
        self.polls = 0
        self.orch: Orchestrator | None = None
        self.locked_at_interrupt: bool | None = None

    def is_running(self, session_id: str) -> bool:
        self.polls += 1
        assert self.polls <= self.poll_cap, "the poll loop never gave up on a wedged turn"
        return super().is_running(session_id)

    def interrupt(self, session_id: str) -> None:
        # Read the lock as the stop happens: the whole ordering rule is that this runs while the
        # turn still owns the tree, never after the lock has been handed to somebody else.
        if self.orch is not None:
            self.locked_at_interrupt = self.orch.turn_busy()
        if self.stops:
            super().interrupt(session_id)   # counts the call and goes idle
        else:
            self.interrupted += 1           # accepted, and nothing changes: the second failure


class ChattyOpenCode(FakeOpenCode):
    """A turn that runs far longer than the quiet window without ever being quiet for it.

    One new tool part per poll for `steps` polls, then idle. This is the healthy long turn the
    ticket was worried about: a wall-clock deadline kills it, a deadline measured from the last
    event never touches it.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *, steps: int = 30) -> None:
        super().__init__(workspace, turns)
        self.steps = steps
        self.emitted = 0

    def _live(self) -> bool:
        # Nothing is emitted before the prompt is sent: `_ensure_session` and `_seen_baseline` both
        # read the session first, and parts invented for them would land in the turn's baseline.
        return self._next > 0 and self.emitted < self.steps

    def is_running(self, session_id: str) -> bool:
        return self._live()

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        if self._live():
            self.emitted += 1
            n = self.emitted
            self._by_session.setdefault(session_id, []).append(
                {"id": f"live-{n}", "type": "assistant",
                 "content": [{"id": f"live-{n}-t", "type": "tool", "tool": "read",
                              "state": {"status": "completed"}}]})
        return super().messages(session_id, limit=limit)


class NeverAppearsOpenCode(FakeOpenCode):
    """A session that never registers as running and never says anything — the case the existing
    twelve-second deadline was written for, and the one the new deadline must keep its hands off."""

    def is_running(self, session_id: str) -> bool:
        return False

    def send_prompt(self, session_id: str, text: str, **kwargs) -> None:
        self.prompts.append({"text": text, "session": session_id, **kwargs})
        self._next += 1


@pytest.fixture(autouse=True)
def _scripted_clock(monkeypatch):
    """Polls cost the turn a second each and the suite nothing.

    Patched on the stdlib module rather than on `service`, which imports `time` function-locally in
    places. `monotonic` keeps moving forward on its own as well, so nothing that reads it sees time
    stand still — the offset only adds the seconds the loop believes it slept.
    """
    import time

    real = time.monotonic
    offset = {"s": 0.0}
    monkeypatch.setattr(time, "sleep", lambda s=0.0: offset.__setitem__("s", offset["s"] + (s or 0.0)))
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset["s"])
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _short_windows(monkeypatch):
    """Five polls of silence rather than five minutes of it. The rule under test is which polls
    reset the clock, and that is the same rule at either scale."""
    monkeypatch.setattr(svc, "_BUILD_QUIET_TIMEOUT_S", 5.0)
    monkeypatch.setattr(svc, "_BUILD_STOP_GRACE_S", 5.0)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building the app\n")
    return t


def _orch(tmp: Path, oc: FakeOpenCode) -> Orchestrator:
    """An orchestrator on the fake agent, with the plan gate off so a prompt reaches the build path."""
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp), gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch


def _wedged(tmp: Path, *, stops: bool = True, turns: list[Turn] | None = None):
    ws = tmp / "mnt" / "code"
    oc = WedgedOpenCode(ws, turns if turns is not None else [Turn(text="working on it")], stops=stops)
    orch = _orch(tmp, oc)
    oc.orch = orch
    return orch, oc


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def test_a_turn_that_goes_quiet_is_given_up_on(tmp_path: Path):
    orch, oc = _wedged(tmp_path)

    events = list(orch.build_stream("add a chart"))

    offer = _of(events, "build-stalled")
    assert len(offer) == 1
    assert _of(events, "done")[0]["decision"] == "stalled"
    assert oc.interrupted == 1


def test_the_clock_runs_from_the_last_event_not_from_the_start_of_the_turn(tmp_path: Path):
    """Thirty polls of work with a five-poll quiet window, and not one of them is quiet for five."""
    ws = tmp_path / "mnt" / "code"
    oc = ChattyOpenCode(ws, [Turn(text="done", writes={"src/chart.tsx": "chart\n"})], steps=30)
    orch = _orch(tmp_path, oc)

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "build-stalled") == []
    assert _of(events, "done")[0]["ok"] is True
    assert oc.emitted == 30


def test_the_twelve_second_never_appeared_deadline_is_untouched(tmp_path: Path):
    """A turn that never registers as running is still the older rule's to answer, even with the
    quiet window set shorter than twelve seconds."""
    ws = tmp_path / "mnt" / "code"
    oc = NeverAppearsOpenCode(ws, [Turn(), Turn(), Turn(), Turn()])
    orch = _orch(tmp_path, oc)

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "build-stalled") == []
    assert _of(events, "done")


def test_giving_up_stops_opencode_before_it_releases_the_lock(tmp_path: Path):
    orch, oc = _wedged(tmp_path)

    list(orch.build_stream("add a chart"))

    assert oc.locked_at_interrupt is True   # stopped while the turn still owned the tree
    assert orch.turn_busy() is False        # and only then was the lock let go


def test_the_next_turn_is_accepted_once_the_session_confirms_it_stopped(tmp_path: Path):
    orch, oc = _wedged(tmp_path, turns=[Turn(text="wedged"),
                                        Turn(text="done", writes={"src/chart.tsx": "chart\n"})])
    list(orch.build_stream("add a chart"))
    # The stop above already cleared `stay_running`, so the second turn runs like any other.

    events = list(orch.build_stream("try that again"))

    assert _of(events, "error") == []
    assert _of(events, "done")[0]["ok"] is True


def test_a_stop_that_does_not_return_keeps_the_lock(tmp_path: Path):
    """The second failure. Sage cannot show that OpenCode let go of the working tree, so it does not
    pretend otherwise: the lock stays held, and the workspace refuses further turns rather than run
    one over a session that may still be writing."""
    orch, oc = _wedged(tmp_path, stops=False)

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "done")[0]["decision"] == "wedged"
    assert "would not stop" in _of(events, "build-stalled")[0]["message"]
    assert orch._turn_lock.locked() is True


def test_the_wedged_notice_survives_a_reload(tmp_path: Path):
    """The one outcome guaranteed to outlive the tab, since it ends in a restart. An `error` frame
    is not persisted; the card is."""
    orch, _ = _wedged(tmp_path, stops=False)

    list(orch.build_stream("add a chart"))

    history = orch.project(start_preview=False).app_for_turn().read_history()
    row = [e for e in history if e.get("type") == "build-stalled"][0]
    assert "Restart the workspace" in row["message"]
    assert row["prompt"] == ""   # nothing a retry could reach until the restart


def test_a_wedged_workspace_does_not_report_a_running_build(tmp_path: Path):
    """`/api/project/build/state` reads this. Left saying "running" it would spin the header on a
    build nobody can stop and disable the composer against it — the very screen this fixes."""
    orch, _ = _wedged(tmp_path, stops=False)
    list(orch.build_stream("add a chart"))

    assert orch.turn_busy() is False        # no turn is running in there
    assert orch._turn_lock.locked() is True  # and none can start, either


def test_a_workspace_stuck_on_a_wedged_turn_says_what_it_is(tmp_path: Path):
    """Not "a build is already running" — true, and useless, because there is nothing left to wait
    for and nothing left to stop. And not flagged `busy`, which the UI drops on sight."""
    orch, _ = _wedged(tmp_path, stops=False)
    list(orch.build_stream("add a chart"))

    events = list(orch.build_stream("anything at all"))

    assert _of(events, "done")[0]["decision"] == "wedged"
    refusal = _of(events, "error")[0]
    assert "Restart the workspace" in refusal["message"]
    assert "busy" not in refusal


def test_a_read_only_turn_that_wrote_and_then_stalled_is_still_reverted(tmp_path: Path):
    """Stalling is not a way around the read-only gate. The ordinary exit reverts a planning turn's
    edits and says so; this exit has to reach the same answer."""
    ws = tmp_path / "mnt" / "code"
    oc = WedgedOpenCode(ws, [Turn(text="here is a plan", writes={"src/sneak.tsx": "nope\n"})])
    orch = _orch(tmp_path, oc)
    oc.orch = orch
    # Back to a first turn, which is gated onto the read-only planner whatever the mode.
    orch.project(start_preview=False).record.write_settings({"skip_planning": False})
    app = orch.project(start_preview=False).workspace.path

    offer = _of(list(orch.build_stream("plan me a dashboard")), "build-stalled")[0]

    assert not (app / "src" / "sneak.tsx").exists()
    assert offer["kept"] is False
    assert "not allowed" in offer["message"]


def test_a_transient_poll_failure_after_the_interrupt_does_not_brick_the_workspace(tmp_path: Path):
    """One 30-second httpx timeout on a busy OpenCode is the main loop's routine, not a verdict.
    Reading it as "it never stopped" would condemn the workspace to a restart over a slow read."""
    orch, oc = _wedged(tmp_path)
    first = {"done": False}
    stopped_running = super(WedgedOpenCode, oc).is_running

    def flaky(session_id: str) -> bool:
        oc.polls += 1
        if oc.interrupted and not first["done"]:
            first["done"] = True
            raise httpx.ReadTimeout("busy")
        return stopped_running(session_id)

    oc.is_running = flaky

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "done")[0]["decision"] == "stalled"   # it recovered and confirmed the stop
    assert orch.turn_busy() is False


def test_files_the_turn_wrote_before_it_wedged_are_kept(tmp_path: Path):
    orch, _ = _wedged(tmp_path, turns=[Turn(text="here you go", writes={"src/chart.tsx": "chart\n"})])
    app = orch.project(start_preview=False).workspace.path

    events = list(orch.build_stream("add a chart"))

    assert (app / "src" / "chart.tsx").read_text() == "chart\n"
    offer = _of(events, "build-stalled")[0]
    assert offer["kept"] is True
    assert "is kept" in offer["message"]
    # The app really changed, so it owes the same receipt any other turn that changes it leaves —
    # without one, "you can see how far it got" points at nothing.
    assert _of(events, "app_change")


def test_a_turn_that_wrote_nothing_does_not_claim_it_kept_anything(tmp_path: Path):
    orch, _ = _wedged(tmp_path, turns=[Turn(text="thinking")])

    offer = _of(list(orch.build_stream("add a chart")), "build-stalled")[0]

    assert offer["kept"] is False
    assert "hadn't written anything" in offer["message"]


class SilentStepsOpenCode(FakeOpenCode):
    """A turn whose only output is steps starting — none of which the transcript can show.

    `task`, `webfetch`, `glob` and the rest sit outside the five tools that produce an "active"
    label, and an in-progress part never enters `seen`, so a turn made entirely of them registers
    nothing at all in the UI. Each one is still a message OpenCode sent, and the quiet clock has to
    count it — a turn taking twelve steps is working, whatever it has to show for it.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *, steps: int = 12) -> None:
        super().__init__(workspace, turns)
        self.steps = steps
        self.started = 0

    def is_running(self, session_id: str) -> bool:
        return self._next > 0 and self.started < self.steps

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        if self._next > 0 and self.started < self.steps:
            self.started += 1
            n = self.started
            self._by_session.setdefault(session_id, []).append(
                {"id": f"m{n}", "type": "assistant",
                 "content": [{"id": f"m{n}-t", "type": "tool", "tool": "task",
                              "state": {"status": "running"}}]})
        return super().messages(session_id, limit=limit)


def test_a_step_that_starts_counts_even_with_nothing_to_show_for_it(tmp_path: Path):
    """Twelve steps against a five-poll window. Judged on what reaches the transcript this turn is
    silent throughout; judged on what OpenCode sent, it never stops working."""
    ws = tmp_path / "mnt" / "code"
    oc = SilentStepsOpenCode(ws, [Turn(text="done", writes={"src/chart.tsx": "chart\n"})], steps=12)
    orch = _orch(tmp_path, oc)

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "build-stalled") == []
    assert oc.started == 12


def test_a_slow_interrupt_does_not_spend_the_whole_grace_window(tmp_path: Path):
    """`interrupt` is an httpx POST at the same server that stopped answering, so it is the call
    most likely to hang — and its timeout is the length of the whole grace window. Started before
    the call, that window is already gone by the time the session gets its first chance to read
    idle, and the workspace is condemned to a restart over a slow POST rather than over a session
    that refused to stop."""
    import time

    orch, oc = _wedged(tmp_path, stops=False)   # the interrupt lands, but not while it is in flight
    winding_down = {"polls": 0}

    def slow(session_id: str) -> None:
        oc.interrupted += 1
        time.sleep(svc._BUILD_STOP_GRACE_S)     # the scripted clock: burns the whole window

    def settling(session_id: str) -> bool:
        oc.polls += 1
        if not oc.interrupted:
            return True
        winding_down["polls"] += 1
        return winding_down["polls"] < 3        # two more polls, then genuinely idle

    oc.interrupt = slow
    oc.is_running = settling

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "done")[0]["decision"] == "stalled"   # confirmed, not condemned
    assert orch.turn_busy() is False


def test_a_wedge_stops_re_arming_the_chat_save_timer(tmp_path: Path):
    """The lock never comes back, so a timer that re-arms against it would spawn a thread every
    thirty seconds for the life of the process."""
    orch, _ = _wedged(tmp_path, stops=False)
    list(orch.build_stream("add a chart"))
    orch._chat_dirty = True

    orch._on_chat_save_idle()

    assert orch._chat_save_timer is None


def test_the_offer_is_replayable_from_the_transcript(tmp_path: Path):
    """The prompt rides along so the button can ask again, and the row is persisted so a reload
    still shows what happened rather than a turn that trails off."""
    orch, _ = _wedged(tmp_path)

    offer = _of(list(orch.build_stream("add a chart")), "build-stalled")[0]

    assert offer["prompt"] == "add a chart"
    history = orch.project(start_preview=False).app_for_turn().read_history()
    assert [e for e in history if e.get("type") == "build-stalled"]


# The same refusal, off the entry points that do not stream (#97).
#
# #39 gave the streaming paths the sentence above. New app, Delete, Reset and the three handoff
# drafts take the same lock and did not get it: they raised a bare "busy" that each route turned
# into "a build is running". After a wedge no build is running, and the one thing that clears the
# workspace — restarting it — was the one thing those sentences never said. #39 made that likelier
# to be read, not rarer: `turn_busy()` now answers false when wedged, so the rail looks idle and
# invites exactly the click that landed on the wrong sentence.


def _refusal_messages(orch: Orchestrator) -> dict[str, str]:
    """Refuse every non-streaming entry point in whatever state `orch` is in, and collect what each
    one said. Three shapes answer here — `TurnBusy`, `ResetBusy`, and `build`'s dict — and gathering
    them in one place is the point: the sentence has to be the same one."""
    app_id = orch.project(start_preview=False, seed_app=False).workspace.app_id
    calls = {
        "create_app": orch.create_app,
        "delete_app": lambda: orch.delete_app(app_id),
        "draft_handoff_plan": lambda: orch.draft_handoff_plan("thread-1"),
        "confirm_handoff": lambda: orch.confirm_handoff("thread-1"),
        "recross_handoff": lambda: orch.recross_handoff("thread-1"),
    }
    said = {}
    for name, call in calls.items():
        with pytest.raises(TurnBusy) as caught:
            call()
        said[name] = str(caught.value)
    with pytest.raises(ResetBusy) as reset:
        orch.reset_app()
    said["reset_app"] = str(reset.value)
    said["build"] = orch.build("add a chart")["message"]
    return said


def test_after_a_wedge_every_non_streaming_entry_point_names_the_restart(tmp_path: Path):
    """Word for word the streaming refusal's sentence. A wedged workspace has nothing to wait for
    and nothing to stop, so there is no per-entry-point tail to add — the answer is the same
    wherever it is asked, and a seventh hand-written copy fails here."""
    orch, _ = _wedged(tmp_path, stops=False)
    list(orch.build_stream("add a chart"))

    said = _refusal_messages(orch)
    streamed = _of(list(orch._busy_refusal()), "error")[0]["message"]

    assert set(said) == {"create_app", "delete_app", "draft_handoff_plan", "confirm_handoff",
                         "recross_handoff", "reset_app", "build"}
    for name, message in said.items():
        assert message == streamed, name
        assert "Restart the workspace" in message


def test_a_turn_that_is_genuinely_running_still_says_wait_or_stop_it_first(tmp_path: Path):
    """The other half of the distinction. A held lock with no wedge behind it is the ordinary case,
    and there the old advice is the right advice — with the tail that names what was refused."""
    orch = _orch(tmp_path, FakeOpenCode(tmp_path / "mnt" / "code"))

    assert orch._turn_lock.acquire(blocking=False)      # a turn in flight, and nothing wrong with it
    try:
        said = _refusal_messages(orch)
    finally:
        orch._turn_lock.release()

    for name, message in said.items():
        assert message.startswith(
            "A build is already running. Wait for it to finish or stop it first, then "), name
        assert "Restart the workspace" not in message
    # The tails stay per entry point: the shared opening cannot name the thing that was refused.
    assert said["create_app"].endswith("then start a new app.")
    assert said["delete_app"].endswith("then delete the app.")
    assert said["reset_app"].endswith("then reset.")


def test_reset_does_not_wait_out_a_lock_that_is_never_coming_back(tmp_path: Path):
    """`_acquire_for_reset` waits up to fifteen seconds when `stop_requested` is set, on the reading
    that the lock is about to free itself. A wedge breaks that reading: the turn that would have
    cleared the flag is the one that never came back, and nothing else clears it.

    Reachable, not theoretical. Stop is refused once a workspace is wedged — `turn_busy()` is false
    by then — but the thirty seconds Sage spends asking the session to stop come BEFORE that, with
    the lock still held and the workspace still reading as busy. A person watching a build that
    stopped responding presses Stop in exactly that window."""
    import time as _time

    orch, oc = _wedged(tmp_path, stops=False)
    real_interrupt = oc.interrupt
    pressed = {"stop": False}

    def stop_pressed_while_sage_gives_up(session_id: str) -> None:
        if not pressed["stop"]:
            pressed["stop"] = True
            orch.stop_build()          # still busy from the outside, so the flag lands
        real_interrupt(session_id)

    oc.interrupt = stop_pressed_while_sage_gives_up
    list(orch.build_stream("add a chart"))

    assert orch.project(start_preview=False).stop_requested   # nothing left to clear it
    started = _time.monotonic()
    with pytest.raises(ResetBusy):
        orch.reset_app()
    assert _time.monotonic() - started < 1.0


def test_the_routes_hand_the_wedged_sentence_to_the_person(tmp_path: Path, monkeypatch):
    """The service side is where the two states are told apart, and the routes' whole job is now to
    carry the sentence out unchanged. 409 either way — the status is what the rail reads to tell a
    refusal from a failure — with the body saying which refusal it was.

    Nothing swallows it on the way. Unlike the streaming refusal, whose `busy` flag makes the UI
    substitute its own line, a 409 body reaches `err.message` verbatim (js/api.js)."""
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    orch, _ = _wedged(tmp_path, stops=False)
    list(orch.build_stream("add a chart"))
    monkeypatch.setattr(appmod, "orchestrator", orch)

    with TestClient(appmod.control_app) as client:
        for response in (client.post("/api/apps"), client.post("/api/project/reset")):
            assert response.status_code == 409
            assert "Restart the workspace" in response.json()["error"]
