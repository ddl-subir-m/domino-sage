"""A turn that has written code and then stops changing anything is noticed, then stopped (#544).

Measured twice, on two models, and neither existing cap could see it. Haiku made 18 tool calls
after its last big write — the last eight shell calls in a row, every command different — and the
person pressed Stop at 160s. GLM made one read and five shell calls in a 268s typecheck-repair
turn that edited nothing, and the person pressed Stop at 769s. The repeat brake only counts
IDENTICAL calls in a row; the shell cap is asked once, at forty, and only when nothing has been
written; the quiet windows are refreshed by every one of those calls; and a single-session Build
has no wall-clock ceiling at all.

So the budget counts BOTH calls and wall time since the last landed change, and the two traces are
caught by different halves — which is the argument for having both rather than either.
"""
from __future__ import annotations

import json
from dataclasses import replace as _replace
from pathlib import Path

import pytest

from sage import timing
from sage.build_policy import BuildPolicy, load_build_policy
from sage.feedback.runner import FeedbackReport
from sage.orchestrator import service as svc
from sage.orchestrator.service import _PROGRESS_TIME_MIN_CALLS, Orchestrator, _progress_armed
from sage.pre_edit_guard import PreEditState
from sage.router.models import ModelCatalog
from sage.router.phase_classifier import _current_turn
from sage.tool_timing import program_name

from .fake_opencode import FakeOpenCode, Turn

POLICY = BuildPolicy()
TEST_POLICY = _replace(POLICY, stop_grace_seconds=1.0)


# ---- the program-name rule --------------------------------------------------------------------

def test_a_program_name_is_never_a_path_and_never_an_assignment():
    """The diagnostic records what ran, and nothing about what it ran ON.

    A command line is where the paths, hostnames and occasional secrets live. The program name is
    the only part of it that answers "what was this turn doing" and carries none of them.
    """
    # Leading `NAME=value` assignments are a prefix, not the program. This is also the half of a
    # command line most likely to hold a credential.
    assert program_name("FOO=1 npm run build") == "npm"
    assert program_name("SECRET=hunter2 TOKEN=abc curl https://example.test/x") == "curl"
    assert program_name("PATH=/a:/b tsc --noEmit") == "tsc"
    # An assignment whose value itself contains `=` is still one assignment, as the shell reads it.
    assert program_name("a=b=c ls") == "ls"

    # A path is reduced to its basename, so no directory ever reaches the record.
    assert program_name("/usr/local/bin/node server.js") == "node"
    assert program_name("FOO=1 /Users/someone/secret-project/bin/vite build") == "vite"

    # And nothing that fails to reduce is recorded at all: refused outright rather than truncated,
    # because a mangled name is a leak with a length limit while "other" is a fact.
    assert program_name("(cd /Users/someone/private && ls)") == "other"
    assert program_name("x" * 33) == "other"
    assert program_name("") == "other"
    assert program_name("   ") == "other"
    assert program_name(None) == "other"
    assert program_name({"command": "ls"}) == "other"


def test_a_quoted_path_does_not_leak_a_directory_name():
    """The case that found a real hole, so it is pinned rather than described.

    Whitespace splitting cuts a quoted path at its space, and the basename of what is left is a
    piece of somebody's directory name — `My`, out of `'/opt/My Tools/run'`. It matches the name
    pattern perfectly, so nothing downstream would have caught it. A quoted token is therefore
    refused before the basename is taken.
    """
    assert program_name("'/opt/My Tools/run'") == "other"
    assert program_name('"/Users/someone/Client Work/bin/deploy" --now') == "other"
    assert program_name("FOO=1 '/opt/Secret Project/run'") == "other"


def test_no_command_text_survives_the_program_name_rule():
    """The property the rule exists for, stated over a command that is all data."""
    command = "PGPASSWORD=s3cret psql -h patients.internal -c 'select ssn from people'"
    assert program_name(command) == "psql"
    for leak in ("s3cret", "patients.internal", "ssn", "people", "select", "-h"):
        assert leak not in program_name(command)


# ---- the settings -----------------------------------------------------------------------------

def test_the_four_limits_are_settings_with_the_approved_defaults():
    policy = BuildPolicy()
    assert policy.progress_notice_call_limit == 4
    assert policy.progress_stop_call_limit == 8
    assert policy.progress_notice_seconds == 90.0
    assert policy.progress_stop_seconds == 180.0

    loaded = load_build_policy({
        "SAGE_BUILD_PROGRESS_NOTICE_CALL_LIMIT": "2",
        "SAGE_BUILD_PROGRESS_STOP_CALL_LIMIT": "3",
        "SAGE_BUILD_PROGRESS_NOTICE_SECONDS": "1.5",
        "SAGE_BUILD_PROGRESS_STOP_SECONDS": "9",
    })
    assert (loaded.progress_notice_call_limit, loaded.progress_stop_call_limit) == (2, 3)
    assert (loaded.progress_notice_seconds, loaded.progress_stop_seconds) == (1.5, 9.0)


def test_the_call_halfs_time_floor_sits_between_the_measured_populations():
    """The floor is a judgement, so the judgement is written down where a change must face it.

    The pair of tests either side of it proves the floor is a floor rather than an off switch, but
    neither pins its VALUE — both would stay green if it were a century. These are the three
    measurements it has to sit between, from #544 and from the two regression tests it broke.
    """
    healthy_2026_09_21 = 0.0      # 45 shell calls after a heredoc write, measured working
    chatty_thirty_steps = 7.0     # 8 calls, the wedged-turn regression test
    haiku_after_its_write = 71.0  # 8 calls at ~8.9s each, the trace this budget exists for

    assert max(healthy_2026_09_21, chatty_thirty_steps) < svc._PROGRESS_CALL_MIN_SECONDS
    assert svc._PROGRESS_CALL_MIN_SECONDS < haiku_after_its_write


def test_a_notice_at_or_past_its_own_stop_is_refused_by_the_environment_key():
    """A notice that can never be sent would stop a turn with nothing having warned it."""
    with pytest.raises(ValueError, match="SAGE_BUILD_PROGRESS_NOTICE_CALL_LIMIT"):
        load_build_policy({"SAGE_BUILD_PROGRESS_NOTICE_CALL_LIMIT": "8"})
    with pytest.raises(ValueError, match="SAGE_BUILD_PROGRESS_STOP_SECONDS"):
        load_build_policy({"SAGE_BUILD_PROGRESS_STOP_SECONDS": "90"})
    with pytest.raises(ValueError, match="progress_notice_call_limit"):
        BuildPolicy(progress_notice_call_limit=9)


# ---- arming is a property of the BUILD, not of the turn ---------------------------------------

class _Guard:
    def __init__(self, state: PreEditState) -> None:
        self.state = state


class _Project:
    def __init__(self, guard) -> None:
        self.pre_edit_guard = guard


def test_the_budget_arms_on_the_build_having_written_not_on_this_turn_having_written():
    """Finding A, and the reason the GLM trace is covered at all.

    That trace was `agent-turn.3`, a typecheck-repair turn that made no edit of its own. A budget
    that armed on THIS turn's first write would never have armed on it. The pre-edit guard is
    built once per build and its DISARMED state is terminal, so asking the guard asks "has this
    BUILD written", which is the question the symptom is about.
    """
    assert _progress_armed(_Project(_Guard(PreEditState.DISARMED))) is True
    # Still armed pre-edit: the guard owns the turn, and the two must not both own it.
    assert _progress_armed(_Project(_Guard(PreEditState.INITIAL_ARMED))) is False
    assert _progress_armed(_Project(_Guard(PreEditState.RECOVERY_ARMED))) is False
    assert _progress_armed(_Project(_Guard(PreEditState.TERMINAL))) is False
    # A gated, answering or architect turn has no guard. Those are armed read-only, so there is no
    # loop of shell calls for this to end.
    assert _progress_armed(_Project(None)) is False


# ---- the turn loop ----------------------------------------------------------------------------

class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    return t


def _bash(n: int, *, status: str = "completed", command: str = "") -> dict:
    """One distinct shell call — distinct, so the repeat brake can never be what stopped it."""
    return {"id": f"sh-{n}", "type": "tool", "tool": "bash",
            "state": {"status": status, "input": {"command": command or f"ls dir-{n}"}}}


def _write(n: int, *, tool: str = "write", path: str = "src/App.tsx") -> dict:
    return {"id": f"w-{n}", "type": "tool", "tool": tool,
            "state": {"status": "completed", "input": {"filePath": path, "content": f"v{n}\n"}}}


class ScriptedPartsOpenCode(FakeOpenCode):
    """Answers each poll with the next scripted step, then goes idle.

    A step is a LIST of parts, because `tool_open` is read per poll: putting a completed call and
    an in-progress one in the same step is the only way to reach a poll that has both several
    completed calls behind it and a call open right now.

    Going idle when the script runs out is what keeps a test that does NOT expect a stop from
    running to the poll cap. The cap is still there, because the behaviour under repair ran until
    the person pressed Stop and a test that reproduces that reports nothing at all.
    """

    def __init__(self, workspace: Path, steps: list[list[dict]], turns: list[Turn] | None = None,
                 *, tree_write_at: int | None = None) -> None:
        super().__init__(workspace, turns)
        self.stay_running = True
        self.polls = 0
        self.emitted = 0
        self.script = list(steps)
        self.tree_write_at = tree_write_at

    def is_running(self, session_id: str) -> bool:
        self.polls += 1
        assert self.polls <= 120, "the build loop never ended a turn that changed nothing"
        return self.stay_running

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        if self._next > 0:
            if self.script:
                step = self.script.pop(0)
                self.emitted += 1
                # An opaque write: the tree moves with no write TOOL announcing it, which is what
                # a heredoc inside a shell call does. Into the SESSION directory, which is where
                # `Turn.writes` puts a real one and the only tree the snapshot hashes.
                if self.tree_write_at == self.emitted:
                    opaque = self._session_dir(session_id) / "src" / "opaque.tsx"
                    opaque.parent.mkdir(parents=True, exist_ok=True)
                    opaque.write_text(f"v{self.emitted}\n")
                self._by_session.setdefault(session_id, []).append(
                    {"id": f"m{self.emitted}", "type": "assistant", "content": list(step)})
            else:
                self.stay_running = False
        return super().messages(session_id, limit=limit)


def _orch(tmp: Path, oc: FakeOpenCode, policy: BuildPolicy) -> Orchestrator:
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway("BUILD"),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc,
                        build_policy=policy)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _notes(orch: Orchestrator) -> list[str]:
    """The note left pending on the shim, if the soft limit queued one.

    Read off the shim rather than off a gateway request because these turns route through a
    scripted gateway that never calls `prepare`. `test_the_note_reaches_the_model_as_a_system_
    message` is the one that pins what `prepare` does with it.
    """
    taken = orch.project(start_preview=False).shim._take_progress_note()
    return [taken] if taken else []


# ---- the diagnostic ----------------------------------------------------------------------------

def _recorded(**kwargs) -> dict:
    timing.start_turn("build", turn_id="turn", app_id="app", conversation_id="conversation")
    timing.progress_budget(**kwargs)
    return timing.as_dict(timing.finish_turn())["progressBudget"]


def _build_record() -> dict:
    """The record the build stream opened and closed for itself.

    Not one this test opens: `build_stream` calls `start_turn` and `finish_turn` around its own
    turn, so a record opened out here is already closed and detached by the time the stream
    returns, and `finish_turn()` hands back `None`. Reading the ring is what asks the question the
    test means — what did the build record — rather than what this test could still get hold of.
    """
    builds = [r for r in timing.recent(10) if r.kind == "build"]
    assert builds, "the build stream recorded no turn"
    return timing.as_dict(builds[0])


def test_the_diagnostic_records_the_high_water_mark_and_which_limit_fired():
    got = _recorded(max_calls_since_change=7, limit_fired="stop",
                    programs={"npm": 4, "tsc": 2, "other": 1})
    assert got == {"maxCallsSinceChange": 7, "limitFired": "stop",
                   "programs": {"npm": 4, "other": 1, "tsc": 2}}


def test_the_diagnostic_drops_anything_that_is_not_a_bare_program_name():
    """Re-checked against the rule that produced it rather than trusted.

    This is the last place before a record someone will read and share, and the names arrive from
    a caller that could be changed later by somebody who has not read `program_name`.
    """
    got = _recorded(max_calls_since_change=1, limit_fired="notice",
                    programs={"npm": 1, "/usr/local/bin/node": 1, "rm -rf /tmp/x": 1,
                              "patients.internal": 1})
    # `patients.internal` survives — it IS a bare name by the rule, which is the honest answer:
    # the rule bounds the SHAPE of what is recorded, and a program really can be called that.
    # What cannot survive is anything carrying a path separator or a space.
    assert set(got["programs"]) == {"npm", "patients.internal"}


def test_a_diagnostic_record_refuses_a_progress_budget_it_cannot_re_derive():
    from sage.build_diagnostics import _progress_budget
    good = {"maxCallsSinceChange": 3, "limitFired": "none", "programs": {"npm": 2}}
    assert _progress_budget(good) == good
    assert _progress_budget({**good, "limitFired": "gave_up"}) is None
    assert _progress_budget({**good, "maxCallsSinceChange": -1}) is None
    assert _progress_budget({**good, "maxCallsSinceChange": True}) is None
    assert _progress_budget({**good, "programs": {"npm install .": 2}}) is None
    assert _progress_budget({**good, "programs": {"npm": -2}}) is None
    assert _progress_budget({**good, "programs": "npm"}) is None
    assert _progress_budget(None) is None


# ---- the note ----------------------------------------------------------------------------------

def test_the_note_reaches_the_model_as_a_system_message(tmp_path: Path):
    """`system`, not `user`, and exactly once.

    `_current_turn` treats a user message as a turn boundary, so a note sent as `user` would
    truncate the very window the phase classifier and the rescue signals read — the note would
    buy one warning and cost the turn its routing.
    """
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code", [], [Turn(text="hi")])
    shim = _orch(tmp_path, oc, TEST_POLICY).project(start_preview=False).shim

    assert shim._take_progress_note() == ""
    shim.note_no_progress(5)
    note = shim._take_progress_note()
    assert "5 tool calls since you last changed a file" in note
    # One-shot: a second request must not carry it again.
    assert shim._take_progress_note() == ""

    # The window the classifier reads is unchanged by the note, which is the whole reason for
    # `system`. Sent as `user` the window would be the note alone.
    history = [{"role": "user", "content": "build me a chart"},
               {"role": "assistant", "content": "on it"},
               {"role": "assistant", "content": "still going"}]
    as_system = [*history, {"role": "system", "content": f"[sage] Progress note: {note}"}]
    assert len(_current_turn(as_system)) == len(_current_turn(history)) + 1
    as_user = [*history, {"role": "user", "content": note}]
    assert _current_turn(as_user) == []


# ---- the two halves ---------------------------------------------------------------------------

def test_a_turn_that_writes_once_and_then_works_quickly_is_not_stopped(tmp_path: Path):
    """THE SHAPE THAT REACHED THE LANDING GATE. It had no test and no plant, which is why.

    One write, then many calls that change nothing — which is, call for call, the Haiku trace this
    budget was built to stop. The two separate on RATE and on nothing else, and this is the end of
    that range: `test_a_build_turn_whose_app_has_changed_is_not_stopped_for_its_shell_calls` stands
    for a build measured healthy on 2026-09-21 that ran 45 shell calls after a heredoc write in 0s,
    and `test_the_clock_runs_from_the_last_event_not_from_the_start_of_the_turn` reached 8 calls in
    7s. Both were red on the gate before the call half grew a time floor.

    Twelve calls is past the stop limit of eight, and the turn must run all of them.
    """
    policy = _replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_write(1)]] + [[_bash(n)] for n in range(12)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))

    assert oc.script == []
    assert oc.interrupted == 0
    assert _notes(orch) == []


def test_the_call_half_fires_on_the_same_turn_once_the_clock_has_run(tmp_path: Path, monkeypatch):
    """The plant for the floor, as a test: the same turn, the same twelve calls, floor removed.

    Without this beside the one above, a floor set so high it disabled the call half entirely would
    look exactly as green. The pair is what pins the floor as a FLOOR.
    """
    monkeypatch.setattr(svc, "_PROGRESS_CALL_MIN_SECONDS", 0.0)
    policy = _replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_write(1)]] + [[_bash(n)] for n in range(12)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))

    assert oc.interrupted == 1
    assert oc.emitted == 1 + policy.progress_stop_call_limit


@pytest.mark.parametrize("stop_state", ["running", "unreadable", "idle"])
def test_the_progress_budget_checks_only_after_a_confirmed_stop(
        tmp_path: Path, monkeypatch, request, stop_state: str):
    monkeypatch.setattr(svc, "_PROGRESS_CALL_MIN_SECONDS", 0.0)
    policy = _replace(TEST_POLICY, stop_grace_seconds=0.0,
                      progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_write(1)]] + [[_bash(n)] for n in range(12)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    orch = _orch(tmp_path, oc, policy)
    checks, saves, releases, queued = [], [], [], []
    # If the regression returns, discard the promoted but unstarted test ticket too.
    request.addfinalizer(lambda: [orch.release_stream_turn(t) for t in queued
                                  if not orch._turn_wedged])
    original_check = orch._feedback.check
    original_running = oc.is_running
    original_release = orch._release_turn

    def check(path):
        checks.append(oc.stay_running)
        return original_check(path)

    def interrupt(sid):
        assert orch._turn_lock.locked()
        ticket, state = orch.prepare_stream_turn("next-writer", kind="build", app=True)
        assert state == "pending"
        queued.append(ticket)
        oc.interrupted += 1
        oc.stay_running = stop_state != "idle"

    def is_running(sid):
        if oc.interrupted and stop_state == "unreadable":
            raise OSError("session status unavailable")
        return original_running(sid)

    def release():
        releases.append(oc.stay_running)
        original_release()

    monkeypatch.setattr(oc, "interrupt", interrupt)
    monkeypatch.setattr(oc, "is_running", is_running)
    monkeypatch.setattr(orch._feedback, "check", check)
    monkeypatch.setattr(orch, "_save_to_git", lambda *a, **k: saves.append(True))
    monkeypatch.setattr(orch, "_release_turn", release)
    events = list(orch.build_stream("build me a chart"))
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert oc.interrupted == 1
    assert queued

    if stop_state == "idle":
        assert done[0]["ok"] is True
        assert checks == [False]
        assert saves == [True]
        assert releases == [False]
        assert orch._turn_wedged is False
        assert queued[0].outcome == "ready"
        orch.release_stream_turn(queued[0])
        assert not orch._turn_lock.locked()
    else:
        assert done[0].pop("turnId")  # ADR-0069 (#565): every Build `done` names its turn; the rest is unchanged
        assert done[0] == {"type": "done", "ok": False, "decision": "wedged"}
        assert checks == saves == releases == []
        assert not any(e["type"] in {"typecheck-start", "app-change", "saved"}
                       for e in events)
        assert orch._turn_wedged and orch._turn_lock.locked()
        assert queued[0].outcome == "wedged"
        assert list(orch.build_stream("start another build"))[-1]["decision"] == "wedged"
        history = orch.project(start_preview=False).app_for_turn().read_history()
        assert next(e for e in history if e["type"] == "build-stalled")["stuck"]


def test_a_heredoc_write_before_the_first_threshold_still_resets_the_window(tmp_path: Path,
                                                                           monkeypatch):
    """The window is baselined where it STARTS, not at its first threshold.

    Baselined lazily, the first read has nothing earlier to compare against and can never rescue
    anything — so a shell write landing before the notice is already folded into the baseline by
    the time the stop reads it, and the turn is stopped for the one write it made. The write here
    lands at the second call, well before the notice at four.
    """
    monkeypatch.setattr(svc, "_PROGRESS_CALL_MIN_SECONDS", 0.0)
    policy = _replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_bash(n)] for n in range(11)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})],
                               tree_write_at=2)
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))

    assert oc.interrupted == 0
    assert oc.script == []


def test_a_turn_that_writes_then_runs_distinct_calls_is_noticed_then_stopped_then_checked(
        tmp_path: Path, monkeypatch):
    """The Haiku shape, end to end: write, then a run of different shell commands.

    Every command differs, so the repeat brake cannot be what ends this; there are eight calls,
    not forty, so the shell cap cannot be either; and a write landed, so the pre-edit guard has
    handed off. The call half of the budget is the only thing here that can see it.
    """
    # The floor is about RATE, and this test is about the COUNT. Removed here so the count is the
    # only thing that can fire; `test_a_turn_that_writes_once_and_then_works_quickly_is_not_stopped`
    # is the other half of the pair and keeps the floor honest.
    monkeypatch.setattr(svc, "_PROGRESS_CALL_MIN_SECONDS", 0.0)
    calls = 12
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_write(1)]] + [[_bash(n)] for n in range(calls)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    policy = _replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    orch = _orch(tmp_path, oc, policy)

    events = list(orch.build_stream("build me a chart"))

    # Stopped at the eighth call after the write, not at the fortieth and not never.
    assert oc.interrupted == 1
    assert oc.emitted == 1 + policy.progress_stop_call_limit
    # The soft note went first, and it says the two things the model needs: how many calls it has
    # made since it last changed anything, and that it does not have to verify the app itself.
    note = _notes(orch)
    assert note and "since you last changed a file" in note[0]
    assert "Sage checks the app itself" in note[0]

    # Stopped, and then CHECKED — the whole point of Finding C. This turn wrote code, so it gets
    # Sage's own after-turn check and its repair loop rather than a "gave up" card.
    kinds = [e.get("type") for e in events]
    assert "typecheck-start" in kinds
    assert "build-stalled" not in kinds
    assert orch._turn_gave_up is False


def test_the_time_half_ends_a_turn_that_is_slow_rather_than_chatty(tmp_path: Path):
    """The GLM shape: few calls, long gaps, nothing changing.

    Six calls would never reach a limit of eight. The clock is the only thing that sees this one,
    which is why both halves exist — a fast model makes many short calls and a reasoning model
    makes few long ones.
    """
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_write(1)]] + [[_bash(n)] for n in range(6)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    # Calls can never fire: the limit is far above anything scripted here.
    policy = _replace(TEST_POLICY, progress_notice_call_limit=500,
                      progress_stop_call_limit=501,
                      progress_notice_seconds=0.001, progress_stop_seconds=0.002)
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))

    assert oc.interrupted == 1
    # The floor held: the clock was already past both limits on the first call after the write,
    # and it still waited for a second completed call before it would stop. So the write, then
    # exactly two calls — never one.
    assert oc.emitted == 1 + _PROGRESS_TIME_MIN_CALLS
    assert oc.script != []


def test_the_clock_does_not_stop_a_turn_while_one_long_command_is_still_running(tmp_path: Path):
    """`npm install` on a cold cache is one call that legitimately runs for minutes.

    `open_tool_quiet_timeout_seconds` is 600 here, so this repo already says a single open call may
    take that long. A clock that stopped the turn during one would contradict that, so the time
    half is asked only with nothing open.
    """
    open_call = {"id": "sh-open", "type": "tool", "tool": "bash",
                 "state": {"status": "running", "input": {"command": "npm install"}}}
    # The third step carries a completed call AND the open one, so the poll that follows it has
    # two completed calls behind it — past the floor — and a call open right now. Without the
    # `not tool_open` guard the clock alone would stop the turn there.
    steps = ([[_write(1)], [_bash(0)], [_bash(1), open_call]]
             + [[dict(open_call)] for _ in range(20)])
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code", steps,
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    policy = _replace(TEST_POLICY, progress_notice_call_limit=500,
                      progress_stop_call_limit=501,
                      progress_notice_seconds=0.001, progress_stop_seconds=0.002)
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))

    # The clock was past both limits from the third step onward, with two completed calls behind
    # it, and neither half fired: not the stop, and not even the advisory note.
    assert oc.emitted > 3
    assert _notes(orch) == []


# ---- what counts as a landed change -----------------------------------------------------------

def test_a_completed_apply_patch_resets_the_count_like_any_other_write(tmp_path: Path):
    """Finding B, and the one that would have shipped a bug.

    OpenCode offers `apply_patch` IN PLACE OF `edit`/`write` to a model whose handle starts `gpt-`
    (#539), so a GPT build lands every change it makes through `apply_patch` and announces no
    `edit` and no `write` at all. Keyed on that pair, this budget would never see a reset on such
    a build and would stop working code at the eighth call.
    """
    policy = _replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    # Seven calls, a patch, then seven more. Neither run reaches eight, so a budget that counts
    # the patch as a landed change never fires and a budget that ignores it fires in the second
    # run. Fourteen calls with one reset is the whole discrimination.
    script = ([[_write(1)]]
              + [[_bash(n)] for n in range(7)]
              + [[_write(2, tool="apply_patch", path="src/Chart.tsx")]]
              + [[_bash(n)] for n in range(100, 107)])
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code", script,
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))

    # Every scripted part was consumed and the turn was never stopped by the budget.
    assert oc.script == []
    assert oc.interrupted == 0


def test_a_working_tree_that_moved_resets_the_count_even_with_no_write_tool(tmp_path: Path):
    """A heredoc inside a shell call changes files and announces no write tool at all.

    The threshold re-reads the tree before it fires, which is what makes a limit as tight as eight
    safe: such a turn is making progress, its tree moves, and it is never stopped. It can still
    draw one advisory note per window, because the first read of a window happens AT the notice
    and so has nothing earlier to compare with.
    """
    policy = _replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    # The notice reads the tree at call 4 and the stop reads it again at call 8. The opaque write
    # lands between them, so the stop must find a different tree and reset instead of firing.
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_write(1)]] + [[_bash(n)] for n in range(10)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})],
                               tree_write_at=7)
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))

    assert oc.interrupted == 0
    assert oc.script == []


def test_the_diagnostic_survives_a_turn_that_something_else_ended(tmp_path: Path):
    """Published on change, so a turn the budget did NOT end still reports how close it got.

    The poll loop has three `return`s — the repeat brake, the quiet window, a refused stop — and
    none of them reaches an end-of-turn write. Those are exactly the turns whose calls-since-change
    number is most worth having, so an end-of-turn write would bias the tuning data towards the
    turns this budget itself ended: the one population that cannot say whether 4 and 8 are right.
    """
    # Three identical calls after a write: the REPEAT BRAKE ends this turn, not the budget.
    same = {"id": "sh-same", "type": "tool", "tool": "bash",
            "state": {"status": "completed", "input": {"command": "npm run build"}}}
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_write(1)]] + [[dict(same, id=f"sh-{n}")] for n in range(4)],
                               [Turn(text="built it", writes={"src/App.tsx": "v1\n"})])
    policy = _replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))
    recorded = _build_record()["progressBudget"]

    # The brake ended it, and the budget still said what it saw on the way past.
    assert recorded["limitFired"] == "none"
    assert recorded["maxCallsSinceChange"] >= 2
    assert recorded["programs"] == {"npm": recorded["maxCallsSinceChange"]}


def test_a_turn_that_has_not_written_is_left_to_the_shell_cap(tmp_path: Path):
    """Out of scope by the issue's own words, and the two owners must not overlap.

    Before the first change the pre-edit guard owns the turn, and after forty shell calls the
    shell cap does. The budget is not armed for either.
    """
    policy = _replace(TEST_POLICY, progress_stop_seconds=3600.0, progress_notice_seconds=1800.0)
    oc = ScriptedPartsOpenCode(tmp_path / "mnt" / "code",
                               [[_bash(n)] for n in range(12)], [Turn(text="looking")])
    orch = _orch(tmp_path, oc, policy)

    list(orch.build_stream("build me a chart"))
    recorded = _build_record()["progressBudget"]

    # Twelve calls, well past the stop limit of eight, and the budget never armed.
    assert oc.emitted > policy.progress_stop_call_limit
    assert _notes(orch) == []
    # And the high-water mark stays at zero, because before the build's first change there is no
    # last change to count from. A large number here beside `limitFired: "none"` would read as a
    # limit that is too loose when it is really a limit that was never in play — and that number
    # is what the four settings get tuned from.
    assert recorded["maxCallsSinceChange"] == 0
    assert recorded["limitFired"] == "none"
