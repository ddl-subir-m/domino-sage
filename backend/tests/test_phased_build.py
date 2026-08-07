"""A phased build, end to end through the fake OpenCode.

The claim under test is not "the parser works" (test_plan_steps covers that) but that an approved
plan actually runs as N isolated phases: a fresh session each, one commit and one `done` for the
build, earlier phases kept when a later one fails, and everything thrown away on Stop.

The isolation assertion is the load-bearing one. Phased execution exists so a cheap model never sees
more than one step's worth of context; a version that quietly shared context would pass every other
test here while delivering none of the benefit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

PHASED_PLAN = """A dashboard for exploring trades.

## Plan

### 1. Data module
- Files — src/data.ts
- Do — Export two hundred sample trade rows.
- Done when — src/data.ts exports rows and the app compiles.

### 2. Trades table
- Files — src/Table.tsx
- Do — Render the rows in a sortable table.
- Done when — The preview shows a sortable table.
- Don't touch — src/data.ts

### 3. Currency filter
- Files — src/Filter.tsx
- Do — Add a currency dropdown above the table.
- Done when — Picking a currency narrows the visible rows.
"""

PROSE_PLAN = """A dashboard for exploring trades.

## Plan
1. **Data module** — Export sample rows.
2. **Trades table** — Render them in a table.
3. **Currency filter** — Add a dropdown.
"""


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """Only the scope classifier reaches this; the fake agent never does."""

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="strong-model", implement="cheap-coder", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _build(tmp: Path, turns: list[Turn], *, phased: bool = True):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    project = orch.project(start_preview=False)
    if phased:
        project.workspace.write_settings({"phased_build": True})
    project.control.set_mode(Mode.AUTO)
    return orch, oc, project


def _writes(rel: str) -> Turn:
    return Turn(writes={rel: f"// {rel}\nexport const x = 1;\n"})


def _plan_then_phases(tmp: Path, *, phased: bool = True, plan: str = PHASED_PLAN):
    """The real flow: a gated first turn writes the plan, approval runs it."""
    turns = [Turn(text=plan), _writes("src/data.ts"), _writes("src/Table.tsx"), _writes("src/Filter.tsx")]
    orch, oc, project = _build(tmp, turns, phased=phased)
    plan_events = list(orch.build_stream("build me a trades dashboard"))
    return orch, oc, project, plan_events


def _kinds(events): return [e.get("type") for e in events]
def _of(events, kind): return [e for e in events if e.get("type") == kind]


# --- the happy path -------------------------------------------------------------------------------

def test_each_phase_runs_in_its_own_fresh_session(tmp_path: Path):
    orch, oc, _project, _ = _plan_then_phases(tmp_path)
    events = list(orch.approve_stream())

    # One project session from the plan turn, then one per phase. Distinct ids is the whole feature:
    # same id would mean every phase inherited the last one's context.
    phase_sessions = [s for s in oc.sessions if s["id"] != "fake-session"]
    assert len(phase_sessions) == 3
    assert len({s["id"] for s in phase_sessions}) == 3
    # Same working tree, so a cold session can still read what earlier phases wrote.
    assert {s["directory"] for s in oc.sessions} == {str(oc.workspace)}
    assert [e["n"] for e in _of(events, "step-start")] == [1, 2, 3]
    assert all(e["ok"] for e in _of(events, "step-done"))


def test_the_build_reports_one_done_and_one_commit(tmp_path: Path):
    orch, _oc, project, _ = _plan_then_phases(tmp_path)
    events = list(orch.approve_stream())

    # Six "build is clean" dividers and six preview refreshes is what a leaked per-phase `done` looks
    # like from the UI.
    assert len(_of(events, "done")) == 1
    assert _of(events, "done")[0]["ok"] is True
    assert len(_of(events, "saved")) <= 1
    assert project.workspace.has_built()
    # One approval is one user bubble, not one per phase.
    history = [json.loads(ln) for ln in project.workspace.history_path.read_text().splitlines() if ln.strip()]
    approve_bubbles = [h for h in history if h.get("type") == "user" and "dashboard" not in h.get("text", "")]
    assert len(approve_bubbles) == 1
    assert len([h for h in history if h.get("type") == "done"]) == 2  # the plan turn's, and the build's


def test_the_checklist_is_announced_before_any_work(tmp_path: Path):
    orch, _oc, _project, _ = _plan_then_phases(tmp_path)
    events = list(orch.approve_stream())

    assert _kinds(events)[0] == "build-plan"
    assert [s["label"] for s in events[0]["steps"]] == ["Data module", "Trades table", "Currency filter"]
    assert events[0]["steps"][0]["files"] == ["src/data.ts"]


def test_a_phase_sees_its_own_brief_and_not_the_others(tmp_path: Path):
    orch, oc, _project, _ = _plan_then_phases(tmp_path)
    list(orch.approve_stream())

    phase_prompts = [p["text"] for p in oc.prompts if "You are executing step" in p["text"]]
    assert len(phase_prompts) == 3
    second = phase_prompts[1]
    # Its own work, in full.
    assert "Render the rows in a sortable table." in second
    assert "Don't touch — src/data.ts" in second
    # Not the other steps' instructions — that context is exactly what a fresh session bought.
    assert "Add a currency dropdown above the table." not in second
    assert "Export two hundred sample trade rows." not in second
    # But it knows where it is, so it doesn't rebuild step 1 or start step 3.
    assert "1. Data module (done)" in second
    assert "2. Trades table (this step)" in second
    assert "3. Currency filter (later)" in second


def test_phases_run_as_implement_not_plan(tmp_path: Path):
    # A phase starts with a fresh user message, so an unpinned mode would let the per-inference
    # classifier read PLAN and route the expensive plan-tier model on every phase.
    orch, oc, _project, _ = _plan_then_phases(tmp_path)
    list(orch.approve_stream())

    phase_agents = [p["agent"] for p in oc.prompts if "You are executing step" in p["text"]]
    assert phase_agents == ["sage-implement"] * 3


# --- failure --------------------------------------------------------------------------------------

def test_a_failed_phase_aborts_the_build_but_keeps_finished_work(tmp_path: Path, monkeypatch):
    # No nudges, so "the agent wrote nothing" fails the phase immediately instead of looping.
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    turns = [Turn(text=PHASED_PLAN), _writes("src/data.ts"),
             Turn(text="I looked around."), Turn(text="Still stuck.")]  # phase 2 both attempts
    orch, oc, project = _build(tmp_path, turns)
    list(orch.build_stream("build me a trades dashboard"))
    events = list(orch.approve_stream())

    done = _of(events, "done")
    assert len(done) == 1
    assert done[0]["ok"] is False
    assert "phase 2 of 3" in done[0]["decision"]
    # Phase 1's work stays: throwing away finished phases because a later one broke is the worst
    # available behaviour, and a follow-up turn is the cheapest recovery.
    assert (oc.workspace / "src" / "data.ts").exists()
    # But the build never happened, so no commit and no "built" latch.
    assert not _of(events, "saved")
    assert not project.workspace.has_built()
    # Which makes the NEXT turn plan first, via the existing failure-replan gate.
    assert project.workspace.read_last_turn_failed()
    # Phase 3 was never attempted — its brief assumed phase 2's "Done when" held.
    assert not any("Add a currency dropdown" in p["text"] for p in oc.prompts)


def test_a_failed_phase_retries_once_in_another_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    turns = [Turn(text=PHASED_PLAN), Turn(text="stuck"), _writes("src/data.ts"),
             _writes("src/Table.tsx"), _writes("src/Filter.tsx")]
    orch, oc, _project = _build(tmp_path, turns)
    list(orch.build_stream("build me a trades dashboard"))
    events = list(orch.approve_stream())

    # Phase 1 failed, retried in a NEW session (the failed attempt is poison in the old one) and
    # succeeded, so the build completes: 3 phases + 1 retry = 4 phase sessions.
    assert len([s for s in oc.sessions if s["id"] != "fake-session"]) == 4
    assert _of(events, "done")[0]["ok"] is True
    assert [e["n"] for e in _of(events, "step-done")] == [1, 2, 3]


def test_the_retry_escalates_to_the_strong_model(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    turns = [Turn(text=PHASED_PLAN), Turn(text="stuck"), _writes("src/data.ts"),
             _writes("src/Table.tsx"), _writes("src/Filter.tsx")]
    orch, _oc, project = _build(tmp_path, turns)
    list(orch.build_stream("build me a trades dashboard"))
    events = list(orch.approve_stream())

    retries = [e for e in events if e.get("type") == "active" and e.get("tool") == "retry"]
    assert len(retries) == 1
    assert "strong-model" in retries[0]["detail"]
    # And the escalation lasts exactly one phase — it must not silently upgrade the rest of the build.
    assert project.control.snapshot().picked_model != "strong-model"


# --- stop -----------------------------------------------------------------------------------------

def test_stop_mid_build_reverts_every_phase(tmp_path: Path):
    orch, oc, project, _ = _plan_then_phases(tmp_path)

    events = []
    for ev in orch.approve_stream():
        events.append(ev)
        # Stop once phase 2 is under way, with phase 1 already on disk.
        if ev.get("type") == "step-start" and ev.get("n") == 2:
            project.stop_requested = True

    assert _of(events, "stopped")
    assert not _of(events, "done")
    # A Stop is the user rejecting the whole build, not just the phase in flight — leaving phase 1
    # behind would leave a state they never asked for and can't describe.
    assert not (oc.workspace / "src" / "data.ts").exists()
    assert not (oc.workspace / "src" / "Table.tsx").exists()


# --- degrade + regression -------------------------------------------------------------------------

def test_an_unparseable_plan_builds_the_ordinary_way(tmp_path: Path):
    # Half-phasing is worse than not phasing, so a plan the parser can't read falls back rather than
    # running whatever steps it managed to find.
    orch, oc, _project, plan_events = _plan_then_phases(tmp_path, plan=PROSE_PLAN)
    assert _of(plan_events, "plan-proposed")[0]["steps"] == 0

    events = list(orch.approve_stream())
    assert not _of(events, "build-plan")
    assert len([s for s in oc.sessions if s["id"] != "fake-session"]) == 0
    assert _of(events, "done")[0]["ok"] is True


def test_the_toggle_off_leaves_the_approve_path_untouched(tmp_path: Path):
    orch, oc, _project, plan_events = _plan_then_phases(tmp_path, phased=False)
    # No step count on the card, so the Approve button reads exactly as it does today.
    assert _of(plan_events, "plan-proposed")[0]["steps"] == 0

    events = list(orch.approve_stream())
    assert "build-plan" not in _kinds(events)
    assert "step-start" not in _kinds(events)
    assert len(_of(events, "done")) == 1
    assert _of(events, "done")[0]["ok"] is True
    # One session for the whole project, as before.
    assert [s["id"] for s in oc.sessions] == ["fake-session"]


def test_the_first_phase_is_not_told_earlier_work_exists(tmp_path: Path):
    """Step 1 opens on the untouched starter template. Telling it "the earlier steps are already done
    and their code is in the workspace" sent the agent hunting for files no phase had created yet —
    live on 2026-08-06 it reported App.tsx "may not exist in the expected format" and went looking for
    a types file, burning the phase on reconciliation instead of building."""
    orch, oc, _project, _ = _plan_then_phases(tmp_path)
    list(orch.approve_stream())

    first, second = [p["text"] for p in oc.prompts if "You are executing step" in p["text"]][:2]

    assert "already done" not in first
    assert "starter template" in first
    assert "already done" in second      # ...and from step 2 on it IS true, so it must still be said


def test_a_phase_is_told_files_outranks_dont_touch(tmp_path: Path):
    """A step often has to edit an earlier file to wire itself in — a table needing a row-click
    handler for this step's drawer. Live on 2026-08-06 that file sat under "Don't touch", and the
    agent spent the phase deliberating ("we're not supposed to modify existing components... let me
    think differently") and shipped a drawer nothing could open. Precedence has to be explicit,
    because the plan is written by a model and can contradict itself."""
    orch, oc, _project, _ = _plan_then_phases(tmp_path)
    list(orch.approve_stream())

    for prompt in [p["text"] for p in oc.prompts if "You are executing step" in p["text"]]:
        assert "Files is your allowlist" in prompt
        assert "Files wins" in prompt
        assert "Never abandon the step" in prompt
