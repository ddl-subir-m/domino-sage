"""A Chat turn cut at the 600s ceiling writes down what it measured, and offers the way back in.

#454. An investigation never goes quiet — every tool call refreshes the activity clock — so it
reaches `_CHAT_TURN_MAX_S` rather than either quiet window, and #400 measured a live three-source
one at 598.6s, 1.4s under the cap. What the person got for those ten minutes was a sentence telling
them to ask something smaller, and nothing else: no record of the measurements, and no way to
resume that did not start from zero.

Three things change here and the cap is not one of them (#400 rejects raising it, and owns the real
remedy). The tail of the ceiling is reserved for writing `findings.md` — the file that already
outlives a turn, committed, and named into the next turn's prompt by `_findings_note`. The block
then says what actually ends a turn, which is the number of steps and not the size of the question.
And it carries Continue, which replays the original question as an ordinary turn — full clock, no
grant — so the resumed turn reads the measurements by the path every turn already reads them.

THE CEILING STAYS A CEILING, which is the claim most of this file is about. The slice is taken out
of the 600 seconds and never added to them, so a session that will not acknowledge the interrupt
and a write that never lands both end the turn at the same instant the work would have.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.threads import findings_file

from .fake_opencode import FakeOpenCode, Turn

# What a turn is asked and what its slice writes down. The question is investigation-shaped on
# purpose: `bounded_intent` leaves `suggestion` non-None for a build-shaped one, and the handoff
# arm fires BEFORE the ceiling arm — so a build-shaped prompt here would exercise a branch this
# file makes no claim about and read as though it had exercised this one.
QUESTION = "which accounts are at risk, and why"
MEASURED = "2026-09-19T14:02Z — DWH.MARTS.ACCOUNT.SFDC_CONTACT_ID populated 16,756/89,399 (18.7%)\n"


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """CHAT, so the handoff classifier leaves `suggestion` None and the ceiling arm is reached."""

    def route(self, request, labels):
        import json
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class WorksUntilStopped(FakeOpenCode):
    """A session that is alive and working — the one shape the ceiling exists for.

    `stay_running` from the first prompt, so nothing here ever goes quiet and the turn can only end
    on the wall clock. `deaf` is the other half of the exercise: a session that takes the interrupt
    and never goes idle, which is what the slice must not be able to wait forever on.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *, deaf: bool = False,
                 writes_forever: bool = False):
        super().__init__(workspace, turns)
        self.stay_running = True
        self.deaf = deaf
        # And the second way the slice could have hung: a session that takes the interrupt, takes
        # the prompt, and then never comes back from writing the file. The two waits are separate
        # code, so they are separate conditions.
        self.writes_forever = writes_forever

    def interrupt(self, session_id: str) -> None:
        self.interrupted += 1
        if self.deaf:
            return
        self.stay_running = False
        self._running[session_id] = False

    def send_prompt(self, *args, **kwargs) -> None:
        super().send_prompt(*args, **kwargs)
        if self.writes_forever and len(self.prompts) > 1:
            self.stay_running = True


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    from sage.orchestrator import handoff
    handoff._health.reset()
    yield
    handoff._health.reset()


def _orch(tmp: Path, oc: FakeOpenCode) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(workspace_dir=tmp / "mnt" / "code", template=template,
                        gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch


def _short_ceiling(monkeypatch, *, ceiling: float = 1.2, slice_s: float = 0.6) -> None:
    """Both numbers, always together.

    The product's pair is 600 and 60; a test that moved only the ceiling would leave a slice longer
    than the turn, and the loop declines to open one of those at all — so the run would pass by
    never reaching the code it claims to test.
    """
    from sage.orchestrator import service
    monkeypatch.setattr(service, "_CHAT_TURN_MAX_S", ceiling)
    monkeypatch.setattr(service, "_CHAT_FINDINGS_FLUSH_S", slice_s)


def _findings_write(thread_id: str, body: str = MEASURED) -> dict[str, str]:
    """What the flush's turn writes, at the path the agent actually stands at.

    A Chat session's cwd is `.sage/chat-work`, and `ensure_chat_workdir` links this Thread's
    directory in — so this relative path resolves to the Project's own findings file, which is the
    one `findings_file` names and the next turn reads.
    """
    return {f".sage/threads/{thread_id}/findings.md": body}


def _run(orch: Orchestrator, thread_id: str) -> list[dict]:
    return list(orch.chat_stream(thread_id, QUESTION))


# ---- the slice ------------------------------------------------------------------------------


def test_a_turn_cut_at_the_ceiling_leaves_what_it_measured_on_disk(tmp_path: Path, monkeypatch):
    """The whole point. Ten minutes of measurement used to end with the turn."""
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    oc.turns.append(Turn(writes=_findings_write(tid)))

    _run(orch, tid)

    root = orch.project(start_preview=False, seed_app=False).record.path
    assert findings_file(root, tid).read_text() == MEASURED


def test_the_slice_asks_for_the_file_by_the_path_the_turn_can_write(tmp_path: Path, monkeypatch):
    """The second prompt is the mechanism, so what it says is part of the feature.

    It must name the file — a turn told to "write down what you found" has no path — and it must
    forbid everything else, because a turn that arrives here has already spent its clock and any
    word that reads as "carry on" spends the slice on more of what ran out of time.
    """
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    oc.turns.append(Turn(writes=_findings_write(tid)))

    _run(orch, tid)

    assert len(oc.prompts) == 2, "the slice sends exactly one prompt, and only after the work"
    asked = oc.prompts[1]["text"]
    assert f".sage/threads/{tid}/findings.md" in asked
    assert oc.prompts[1]["agent"] == "sage-chat"
    assert "do not try to answer the question" in asked


def test_the_block_names_steps_and_says_where_the_measurements_went(tmp_path: Path, monkeypatch):
    """#400's lever, said out loud. 41 of 49 calls on the turn that prompted this were bash, and
    the investigate skill prescribes them — so the old sentence sent the person to make smaller a
    thing that was never the problem."""
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    oc.turns.append(Turn(writes=_findings_write(tid)))

    out = _run(orch, tid)

    said = next(e for e in out if e["type"] == "error")["message"]
    assert "number of steps it takes" in said
    assert f".sage/threads/{tid}/findings.md" in said
    assert "Ask a smaller question" not in said
    # And it must not read as an answer. The turn did not reach one.
    assert "ran out of time before" in said


def test_the_ceiling_still_ends_the_turn_the_way_it_did(tmp_path: Path, monkeypatch):
    """Constraint 3. `ok: false` and `decision: "timeout"` are unchanged — only the reading of the
    decision on the client changes, and only the platform-fault half of that."""
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    oc.turns.append(Turn(writes=_findings_write(tid)))

    out = _run(orch, tid)

    done = next(e for e in out if e["type"] == "done")
    assert done["ok"] is False
    assert done["decision"] == "timeout"


# ---- Continue -------------------------------------------------------------------------------


def test_continue_replays_the_original_question_on_the_same_thread(tmp_path: Path, monkeypatch):
    """Nothing is bolted onto the prompt. `_findings_note` already names the file into every turn
    that has one, and the slice has just made this Thread have one — so the resumed turn is handed
    the measurements by the path every turn already reads them, and is an ordinary turn."""
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    oc.turns.append(Turn(writes=_findings_write(tid)))

    out = _run(orch, tid)

    offer = next(e for e in out if e["type"] == "continue-offer")
    assert offer["prompt"] == QUESTION
    assert offer["threadId"] == tid
    assert f".sage/threads/{tid}/findings.md" in offer["message"]
    # After `done`, like every other card drawn under a settled turn.
    kinds = [e["type"] for e in out]
    assert kinds.index("done") < kinds.index("continue-offer")
    # And on the record, so a reload shows it where it was.
    assert any(e["type"] == "continue-offer" for e in orch.get_thread(tid)["history"])


def test_the_resumed_turn_is_handed_what_the_last_one_measured(tmp_path: Path, monkeypatch):
    """The link Continue rests on, end to end, because Continue itself attaches nothing.

    The card claims the next turn starts where this one stopped. Nothing in the card makes that
    true: it is true because `_findings_note` names this Thread's `findings.md` into every prompt
    that has one, and the reserved slice has just made this Thread have one. Two mechanisms, and
    the claim is about the second — so this drives the resumed turn and reads the prompt that
    actually reached the agent, rather than asserting the card's own sentence back at itself.
    """
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    oc.turns.append(Turn(writes=_findings_write(tid)))

    out = _run(orch, tid)
    offer = next(e for e in out if e["type"] == "continue-offer")

    # Continue, as the button sends it: the offer's own prompt, on the same Thread. The ceiling
    # goes back to something a turn can finish inside, because a resumed turn is an ordinary turn
    # and the claim here is about its PROMPT — left at 1.2s it would reach its own ceiling and its
    # own slice, and `prompts[-1]` would be a second findings request rather than the question.
    _short_ceiling(monkeypatch, ceiling=600.0, slice_s=60.0)
    oc.turns.append(Turn(text="Four accounts score above 0.8."))
    list(orch.chat_stream(offer["threadId"], offer["prompt"]))

    resumed = oc.prompts[-1]["text"]
    assert f".sage/threads/{tid}/findings.md" in resumed
    assert "Read it before you plan this turn" in resumed


def test_a_ceiling_that_measured_nothing_offers_nothing(tmp_path: Path, monkeypatch):
    """Constraint 4. No findings, no Continue — the block says what it can and offers nothing
    false. The slice ran and the turn wrote no file, which is the shape a person most needs the
    truth about: pressing Continue into an empty file re-runs the ten minutes from zero."""
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    oc.turns.append(Turn(text="I have nothing to write down"))

    out = _run(orch, tid)

    assert not [e for e in out if e["type"] == "continue-offer"]
    said = next(e for e in out if e["type"] == "error")["message"]
    assert "nothing it measured was written down" in said
    assert "number of steps it takes" in said


def test_findings_an_earlier_turn_wrote_are_not_this_turns_measurements(tmp_path: Path,
                                                                        monkeypatch):
    """The offer is about THIS turn's ten minutes, so the file being there cannot be the test.

    A Thread that investigated last week has `findings.md` already. Read for existence, every
    ceiling in that Thread would offer to continue from a file this turn added nothing to, and
    the resumed turn would re-derive exactly what the person was told it would not.
    """
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    root = orch.project(start_preview=False, seed_app=False).record.path
    earlier = findings_file(root, tid)
    earlier.parent.mkdir(parents=True, exist_ok=True)
    earlier.write_text(MEASURED)
    oc.turns.append(Turn(text="nothing new to add"))

    out = _run(orch, tid)

    assert not [e for e in out if e["type"] == "continue-offer"]
    assert earlier.read_text() == MEASURED, "and the earlier turn's record is left alone"


# ---- the ceiling is a ceiling ---------------------------------------------------------------


def test_a_session_that_will_not_stop_does_not_buy_itself_more_time(tmp_path: Path, monkeypatch):
    """Constraint 2, in the direction that could have cost the most.

    The slice interrupts the work and then waits for the session to go idle before it asks for
    anything. A session wedged badly enough to reach the ceiling is exactly the one that may ignore
    the interrupt, so that wait is the place an unbounded loop would have lived. Here the session
    never acknowledges anything: the turn must still end, and it must end saying it kept nothing.
    """
    _short_ceiling(monkeypatch)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")], deaf=True)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    out = _run(orch, tid)

    # Nothing was asked for, because the session never came back to be asked.
    assert len(oc.prompts) == 1
    assert not [e for e in out if e["type"] == "continue-offer"]
    done = next(e for e in out if e["type"] == "done")
    assert done["ok"] is False and done["decision"] == "timeout"


def test_a_findings_write_that_never_comes_back_does_not_extend_the_turn(tmp_path: Path,
                                                                         monkeypatch):
    """The other wait, and the other half of constraint 2.

    A session can take the interrupt, take the prompt, and then never come back from the write —
    the file it was told to append to sits on the same mount as everything else this turn was
    reading, and a mount that has stopped answering is the reason plenty of turns get here at all.
    The two waits are separate code and so they are separate conditions: the slice must end at the
    same instant either way, and a turn whose write did not land must say it kept nothing.
    """
    import time

    _short_ceiling(monkeypatch, ceiling=1.2, slice_s=0.6)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")],
                           writes_forever=True)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    began = time.monotonic()
    out = _run(orch, tid)
    ran = time.monotonic() - began

    assert len(oc.prompts) == 2, "the slice did ask, so this is the wait after the ask"
    assert ran < 1.2 + 1.5, f"the turn ran {ran:.2f}s against a 1.2s ceiling"
    assert not [e for e in out if e["type"] == "continue-offer"]
    said = next(e for e in out if e["type"] == "error")["message"]
    assert "nothing it measured was written down" in said


def test_the_turn_ends_inside_its_own_ceiling_however_the_slice_goes(tmp_path: Path, monkeypatch):
    """Measured, not argued. The slice is subtracted from the ceiling rather than added to it, so
    a turn that spends the whole of it still ends within the number the cap names."""
    import time

    _short_ceiling(monkeypatch, ceiling=1.2, slice_s=0.6)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")], deaf=True)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    began = time.monotonic()
    _run(orch, tid)
    ran = time.monotonic() - began

    # The ceiling plus what the end of a turn costs — the artifact scan, the commit, the save.
    # The claim is the ceiling, not the tidy-up: without the subtraction this would be 1.8s of
    # work and slice before any of that.
    assert ran < 1.2 + 1.5, f"the turn ran {ran:.2f}s against a {1.2}s ceiling"


def test_the_reserved_slice_fits_inside_the_ceiling_it_is_taken_from():
    """The pair, pinned. The slice is SUBTRACTED, so a slice as long as the ceiling leaves no turn
    in front of it and the loop declines to open one at all — correct, and silent. An edit that
    brings the two numbers together would turn this feature off without reddening anything else in
    the suite, which is the failure this reds for."""
    from sage.orchestrator import service

    assert service._CHAT_FINDINGS_FLUSH_S > 0
    assert service._CHAT_TURN_MAX_S - service._CHAT_FINDINGS_FLUSH_S > service._CHAT_FINDINGS_FLUSH_S


# ---- the other arms -------------------------------------------------------------------------


def test_a_turn_that_went_quiet_does_not_spend_the_slice(tmp_path: Path, monkeypatch):
    """Constraint 5, from the side that is easiest to break by widening.

    The slice opens only on the way to the ceiling. A quiet turn has already stopped saying
    anything, so there is nothing in flight to interrupt and no reason to think a session that went
    silent will answer a new prompt — and the idle-quiet arm's own sentence knows something this
    one does not. Widening the trigger to "any stop" would interrupt it, ask it for findings, and
    replace its copy with a ceiling's.

    THE TWO THRESHOLDS ARE SET TO THE SAME NUMBER on purpose, and that is the whole exercise. The
    slice is checked before the stop, so `not quiet` decides nothing at all unless both cross in
    one pass of the loop — a quiet window that expires earlier ends the turn before the slice is
    reached, and one that expires later loses to a slice that has already opened. Measured: with
    the windows apart, deleting `not quiet` changed nothing and this test stayed green. Silence
    and the ceiling both counted from `started` here, so the pass where they meet is the pass
    this runs on.
    """
    from sage.orchestrator import service

    _short_ceiling(monkeypatch, ceiling=1.2, slice_s=0.6)
    monkeypatch.setattr(service, "_CHAT_QUIET_TIMEOUT_S", 0.6)
    # Alive to `is_running` and saying nothing, so `last_activity` never moves off `started`.
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    out = _run(orch, tid)

    assert len(oc.prompts) == 1, "no findings prompt on a turn that had already stopped"
    said = next(e for e in out if e["type"] == "error")["message"]
    assert "stopped making progress" in said
    assert "number of steps it takes" not in said
    assert not [e for e in out if e["type"] == "continue-offer"]
