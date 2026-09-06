"""A build that gave up keeps the plan it never built, and "try again" builds it.

The live shape: a plan was approved, the build wrote a file and then the gateway answered 404 for a
model that no longer exists. The user typed "try again" and got a SECOND plan card for the same
request, approved that, hit the same 404, typed "try again", got a THIRD. Three planning turns paid
for, three approvals asked for, one build.

Two things had to be true for that loop. The approve turn's `finally` archived the plan even though
nothing was ever built from it, so there was no approved plan left to retry; and the retry turn was
then indistinguishable from a first build request, so the first-build gate (`has_built` is still
false — the build failed) and the failure-replan gate both fired and planned it again.

What these tests pin is the pair: a turn that gave up does not consume its plan, and a bare retry
typed at that kept plan builds it instead of planning it again. The gateway is broken by hand rather
than by a real 404 — the fake agent never reaches the shim, so `last_gateway_error` is the only
thing about a broken gateway the turn path can observe.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


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


class BreakingOpenCode(FakeOpenCode):
    """A fake agent whose Nth turn ends with the gateway having failed.

    `break_on` counts sends across the whole run, exactly as the scripted turns do, so a test names
    the turn it wants broken in the same terms it writes the script in. The error is planted the way
    the shim plants a real one: on the project, after the prompt goes out and after `_build_stream`
    has cleared the previous turn's."""

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 break_on: set[int] | None = None) -> None:
        super().__init__(workspace, turns)
        self.orch: Orchestrator | None = None
        self.break_on = set(break_on or ())

    def send_prompt(self, session_id: str, text: str, model: dict | None = None,
                    agent: str | None = None, attachments: list[dict] | None = None,
                    chat: bool = False) -> None:
        super().send_prompt(session_id, text, model, agent, attachments, chat)
        if self._next in self.break_on and self.orch is not None:
            self.orch.project(start_preview=False).last_gateway_error = {
                "message": "gateway returned 404: Model 'GLM-5.2' not found",
                "upstream_status": 404,
            }


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The same two waits test_turn_path strips: a scripted turn can only spend them."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _build(tmp: Path, turns: list[Turn], *, break_on: set[int] | None = None,
           phased: bool = False):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    oc = BreakingOpenCode(ws, turns, break_on=break_on)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    oc.orch = orch
    project = orch.project(start_preview=False)
    if phased:
        project.record.write_settings({"phased_build": True})
    return orch, oc


def _kinds(events: list[dict]) -> set[str]:
    return {e.get("type") for e in events}


def _done(events: list[dict]) -> dict:
    return next(e for e in reversed(events) if e["type"] == "done")


def _workspace(orch: Orchestrator):
    return orch.project(start_preview=False).workspace


PLAN = Turn(text="1. Add the table\n2. Wire up the data")


def test_a_build_that_hits_a_gateway_error_keeps_the_plan_it_never_built(tmp_path: Path):
    # Turn 1 plans, turn 2 is the approved build — and the gateway dies during it.
    orch, _oc = _build(tmp_path, [PLAN, Turn(writes={"src/App.tsx": "// half a table\n"})],
                       break_on={2})

    list(orch.build_stream("build me a consumption dashboard"))
    events = list(orch.approve_stream())

    assert _done(events)["decision"] == "gateway error"
    # Nothing was built from it, so the plan is still the app's live one rather than an archive
    # entry — and it is marked as a plan that is owed its build.
    ws = _workspace(orch)
    assert "Add the table" in (ws.read_plan() or "")
    assert ws.read_plan_retry_step() == 1
    # And the row that stopped the person says how to get out of it. A kept plan nobody can see is
    # a dead end: the card's Approve button was spent when this turn started.
    error = next(e for e in events if e["type"] == "error")
    assert "still here" in error["message"] and "try again" in error["message"]


def test_a_plain_build_that_hits_a_gateway_error_says_nothing_about_a_plan(tmp_path: Path):
    """The same error on a turn with no approved plan behind it. Offering "try again" there would
    point at a plan that does not exist — the message is only true of an approve turn."""
    orch, _oc = _build(tmp_path, [PLAN, Turn(writes={"src/App.tsx": "// the table\n"}),
                                  Turn(writes={"src/App.tsx": "// more\n"})], break_on={3})

    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())
    events = list(orch.build_stream("make the table sortable"))

    assert _done(events)["decision"] == "gateway error"
    error = next(e for e in events if e["type"] == "error")
    assert "still here" not in error["message"]


def test_try_again_builds_the_approved_plan_instead_of_proposing_a_second_one(tmp_path: Path):
    orch, oc = _build(tmp_path, [
        PLAN,                                              # 1. the plan
        Turn(writes={"src/App.tsx": "// half a table\n"}),  # 2. the approved build — gateway dies
        Turn(writes={"src/App.tsx": "// the table\n"}),     # 3. "try again" — the same build again
    ], break_on={2})

    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())
    events = list(orch.build_stream("try again"))

    # The retry BUILT. No second plan card, no second approval to give.
    assert "plan-proposed" not in _kinds(events)
    assert _done(events)["ok"] is True
    assert _workspace(orch).has_built() is True
    # It ran the plan, not the sentence: the approve prompt carries the approved plan, and the
    # agent that got it is the builder rather than the read-only planner.
    assert oc.prompts[-1]["agent"] != "sage-plan"
    assert "Add the table" in oc.prompts[-1]["text"]
    # The build consumed the plan this time, so nothing is left owing a retry.
    ws = _workspace(orch)
    assert ws.read_plan() is None
    assert ws.read_plan_retry_step() == 0


def test_a_retry_that_fails_again_can_still_be_retried(tmp_path: Path):
    """One-shot per failure, not one-shot per plan: the loop the fix breaks was three failures deep."""
    orch, _oc = _build(tmp_path, [
        PLAN,
        Turn(writes={"src/App.tsx": "// half\n"}),   # approved build — dies
        Turn(writes={"src/App.tsx": "// half\n"}),   # first retry — dies too
        Turn(writes={"src/App.tsx": "// done\n"}),   # second retry — builds
    ], break_on={2, 3})

    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())
    first = list(orch.build_stream("try again"))
    assert _done(first)["decision"] == "gateway error"
    assert _workspace(orch).read_plan_retry_step() == 1

    second = list(orch.build_stream("try again"))
    assert "plan-proposed" not in _kinds(second)
    assert _done(second)["ok"] is True


def test_try_again_at_a_plan_awaiting_approval_still_plans(tmp_path: Path):
    """The other reading of the same two words, and the reason the flag exists rather than the
    phrase alone: a plan nobody approved has nothing to retry, so "try again" asks for a new one."""
    orch, _oc = _build(tmp_path, [PLAN, Turn(text="1. Add a chart\n2. Wire up the data")])

    list(orch.build_stream("build me a consumption dashboard"))
    events = list(orch.build_stream("try again"))

    plan = next(e for e in events if e["type"] == "plan-proposed")
    assert "Add a chart" in plan["plan"]
    assert _done(events)["decision"] == "awaiting approval"


def test_a_clean_build_still_archives_its_plan(tmp_path: Path):
    """The behaviour the retry path must not have widened: a plan a build DID consume is archived,
    so no later turn reads it as current intent."""
    orch, _oc = _build(tmp_path, [PLAN, Turn(writes={"src/App.tsx": "// the table\n"})])

    list(orch.build_stream("build me a consumption dashboard"))
    events = list(orch.approve_stream())

    assert _done(events)["ok"] is True
    ws = _workspace(orch)
    assert ws.read_plan() is None
    assert "Add the table" in (ws.read_archived_plan() or "")
    assert ws.read_plan_retry_step() == 0


def test_a_retry_typed_in_implement_mode_still_builds_the_approved_plan(tmp_path: Path):
    """Mode is not what decides this. The approval path already runs from any mode, and a retry is
    the same instruction — so Implement, where people retry failures deliberately, reaches it too."""
    orch, oc = _build(tmp_path, [
        PLAN,
        Turn(writes={"src/App.tsx": "// half\n"}),
        Turn(writes={"src/App.tsx": "// done\n"}),
    ], break_on={2})

    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())
    orch.project(start_preview=False).control.set_mode(Mode.IMPLEMENT)
    events = list(orch.build_stream("try again"))

    assert "plan-proposed" not in _kinds(events)
    assert "Add the table" in oc.prompts[-1]["text"]


# --- phased builds ---------------------------------------------------------------------------
#
# A phased build gets somewhere before it dies: the finished phases are kept on disk on purpose,
# because throwing away forty minutes of good work because step 4 of 6 broke is the worst available
# behaviour. That is exactly why the retry must not start over — it would buy a session per phase to
# redo work that is already there, and each redone phase would be editing files the first attempt
# wrote. So the retry resumes at the phase that broke.

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

### 3. Currency filter
- Files — src/Filter.tsx
- Do — Add a currency dropdown above the table.
- Done when — Picking a currency narrows the visible rows.
"""


def _writes(rel: str) -> Turn:
    return Turn(writes={rel: f"// {rel}\nexport const x = 1;\n"})


def _phased_run_that_dies_in_phase_two(tmp_path: Path):
    """Send 1 plans; 2 is phase 1; 3 and 4 are phase 2 and the retry _run_step gives it, both
    broken; 5 and 6 are what the resumed build needs."""
    return _build(tmp_path, [
        Turn(text=PHASED_PLAN),
        _writes("src/data.ts"),      # 2. phase 1 — lands
        _writes("src/Table.tsx"),    # 3. phase 2, first attempt — gateway dies
        _writes("src/Table.tsx"),    # 4. phase 2, _run_step's own retry — dies too
        _writes("src/Table.tsx"),    # 5. the resumed build's phase 2
        _writes("src/Filter.tsx"),   # 6. and its phase 3
    ], break_on={3, 4}, phased=True)


def test_a_phased_build_that_dies_keeps_the_plan_and_remembers_the_phase(tmp_path: Path):
    orch, _oc = _phased_run_that_dies_in_phase_two(tmp_path)

    list(orch.build_stream("build me a trades dashboard"))
    events = list(orch.approve_stream())

    assert _done(events)["decision"].startswith("phase 2 of 3 failed")
    ws = _workspace(orch)
    assert "Trades table" in (ws.read_plan() or "")   # not archived: it still owes two phases
    assert ws.read_plan_retry_step() == 2
    # Phase 1's work is kept, which is what makes resuming the right thing to do.
    assert (ws.path / "src" / "data.ts").exists()


def test_try_again_resumes_a_phased_build_at_the_phase_that_broke(tmp_path: Path):
    orch, oc = _phased_run_that_dies_in_phase_two(tmp_path)

    list(orch.build_stream("build me a trades dashboard"))
    list(orch.approve_stream())
    events = list(orch.build_stream("try again"))

    assert "plan-proposed" not in _kinds(events)
    assert _done(events)["ok"] is True
    # It picked up at 2 rather than starting over.
    assert [e["n"] for e in events if e["type"] == "step-start"] == [2, 3]
    # Phase 1 is still shown, marked as inherited rather than run — a build that opened at "step 2
    # of 3" with no account of the one before it would read as a build that lost a step.
    kept = [e for e in events if e["type"] == "step-done" and e.get("kept")]
    assert [e["n"] for e in kept] == [1]
    # And it really did not re-run: phase 1's brief was written once, by the first attempt.
    assert sum("You are executing step 1 of 3" in p["text"] for p in oc.prompts) == 1
    # A finished build consumes its plan like any other.
    ws = _workspace(orch)
    assert ws.read_plan() is None
    assert ws.read_plan_retry_step() == 0
    assert ws.has_built() is True


def test_putting_a_partly_built_plan_away_names_it_as_what_the_app_was_built_from(tmp_path: Path):
    """Cancelling a plan two phases into its build means "stop offering to finish this", not
    "nothing here came from it". Archived as cancelled, the pin skipped it and named an EARLIER
    plan, so the phases that ran left no trace anywhere (#173)."""
    orch, _oc = _phased_run_that_dies_in_phase_two(tmp_path)

    list(orch.build_stream("build me a trades dashboard"))
    list(orch.approve_stream())
    ws = _workspace(orch)
    assert ws.read_plan_retry_step() == 2       # phase 1 finished and is on disk

    orch.archive_plan_doc(ws.live_plan_doc_id(), True)

    assert "Trades table" in (ws.read_archived_plan() or "")
    assert orch.read_plan_pin()["status"] == "built"


def test_the_transcript_cancel_puts_a_partly_built_plan_away_the_same_way(tmp_path: Path):
    """The rule lives in `archive_plan` so that the three doors that press Cancel cannot disagree,
    and this is the one people actually reach: the plan card's own Cancel. The plan page's Archive
    button refuses while the Conversation that proposed the plan still answers, which after a build
    that died it usually does."""
    orch, _oc = _phased_run_that_dies_in_phase_two(tmp_path)

    list(orch.build_stream("build me a trades dashboard"))
    list(orch.approve_stream())

    assert orch.cancel_plan()["archived"] is True

    ws = _workspace(orch)
    assert ws.read_plan() is None
    assert "Trades table" in (ws.read_archived_plan() or "")


def _phased_run_replaced_by_a_new_request(tmp_path: Path):
    """Sends 1-4 are the run that dies in phase 2; send 5 is the NEW request's plan, which is what
    supersedes the standing one."""
    return _build(tmp_path, [
        Turn(text=PHASED_PLAN),
        _writes("src/data.ts"),              # 2. phase 1 — lands
        _writes("src/Table.tsx"),            # 3. phase 2, first attempt — gateway dies
        _writes("src/Table.tsx"),            # 4. phase 2, _run_step's own retry — dies too
        Turn(text="1. A risk heatmap\n2. Wire up data"),   # 5. the replacement plan
    ], break_on={3, 4}, phased=True)


def test_a_new_request_replacing_a_partly_built_plan_keeps_it_as_what_was_built(tmp_path: Path):
    """The supersede door, through the orchestrator rather than the Workspace, because the ordering
    is the whole trap (#175): `_supersede_live_plan` archives the standing plan and the new
    `write_plan` zeroes the step on the very NEXT line. A fix that read the step any later would
    read 0, call this an ordinary supersede, and still pass a Workspace-level test.

    Both surfaces are asserted together, which is the sub-decision the issue asked to settle: the
    FILE reads as what this app was built from, and the DOCUMENT keeps its `superseded` stamp for
    the panel badge and the card's "This plan is kept" copy. Two questions, two true answers."""
    orch, _oc = _phased_run_replaced_by_a_new_request(tmp_path)

    list(orch.build_stream("build me a trades dashboard"))
    list(orch.approve_stream())
    ws = _workspace(orch)
    assert ws.read_plan_retry_step() == 2         # phase 1 finished and is on disk
    replaced = ws.live_plan_doc_id()

    list(orch.build_stream("actually build me a risk heatmap"))

    assert "Trades table" in (ws.read_archived_plan() or "")
    assert orch.read_plan_doc(replaced)["status"] == "superseded"


def test_a_phased_build_that_finishes_first_time_owes_nothing(tmp_path: Path):
    """The behaviour the resume point must not have widened: an unbroken phased build archives its
    plan, so no later turn reads it as current intent."""
    orch, _oc = _build(tmp_path, [
        Turn(text=PHASED_PLAN), _writes("src/data.ts"), _writes("src/Table.tsx"),
        _writes("src/Filter.tsx"),
    ], phased=True)

    list(orch.build_stream("build me a trades dashboard"))
    events = list(orch.approve_stream())

    assert _done(events)["ok"] is True
    assert [e["n"] for e in events if e["type"] == "step-start"] == [1, 2, 3]
    ws = _workspace(orch)
    assert ws.read_plan() is None
    assert ws.read_plan_retry_step() == 0
