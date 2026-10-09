"""After a failed Build, a small fix is planned against a fix contract, not a first-build one (#677).

A failure-gated turn used to be held to the first-build contract, so a good three-step fix plan was
refused for having no Screens, and a planner that answered in prose was reported as a list of
missing headings. The first-build gate keeps the full contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.plan_steps import FIX_SECTIONS, validate_execution_contract
from sage.orchestrator.service import Orchestrator

from .fake_opencode import Turn
from .test_plan_reference_persistence import _orchestrator

FIX_PLAN = """# Push Fix

Stop the push from failing on the missing remote.

## Done when
- A push from the app succeeds.

## Plan
### 1. Remote check
- Files — src/App.tsx
- Do — Check the remote before pushing.
- Done when — The push no longer errors.
"""

PROSE = ("Signal Room can't call @haiku or @sonnet yet. Attach those models to the app, "
         "then ask again and I'll plan the change.")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _after_a_failed_build(tmp_path: Path, turns: list[Turn]):
    orch, client = _orchestrator(tmp_path, FakeAssetProvider(), turns)
    app = orch.project(start_preview=False).app_for_turn()
    app.mark_built()
    app.set_last_turn_failed(True)
    return orch, client


def test_a_fix_plan_needs_no_screens_but_a_first_build_plan_does():
    assert not validate_execution_contract(FIX_PLAN).valid
    assert "screens" in validate_execution_contract(FIX_PLAN).missing_sections
    assert validate_execution_contract(FIX_PLAN, required=FIX_SECTIONS).valid


def test_a_failure_gated_fix_plan_with_no_screens_reaches_approval_and_builds(tmp_path: Path):
    orch, _ = _after_a_failed_build(tmp_path, [
        Turn(text=FIX_PLAN),
        Turn(text="Fixed.", writes={"src/App.tsx": "export default function App() { return 1 }\n"}),
    ])

    events = list(orch.build_stream("resolve this push error"))

    assert not any(e.get("decision") == "invalid execution plan" for e in events)
    proposed = next(e for e in events if e.get("type") == "plan-proposed")
    assert next(e for e in events if e.get("type") == "done")["decision"] == "awaiting approval"

    approved = list(orch.approve_stream(plan_id=proposed["planId"]))

    done = [e for e in approved if e.get("type") == "done"]
    assert [e["decision"] for e in done] == ["typecheck clean"]
    assert done[0]["ok"] is True


def test_a_failure_gated_prose_answer_is_shown_not_reported_as_an_invalid_plan(tmp_path: Path):
    orch, client = _after_a_failed_build(tmp_path, [Turn(text=PROSE)])

    events = list(orch.build_stream("Make Write deal brief use @haiku."))

    assert not any(e.get("decision") == "invalid execution plan" for e in events)
    assert not any(e.get("type") == "plan-proposed" for e in events)
    assert any(e.get("type") == "agent" and e.get("kind") == "text" and PROSE in e.get("text", "")
               for e in events)
    assert next(e for e in events if e.get("type") == "done")["decision"] == "answered"
    assert len(client.prompts) == 1


def test_the_first_build_gate_still_rejects_a_plan_with_no_screens(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, FakeAssetProvider(), [Turn(text=FIX_PLAN), Turn(text=FIX_PLAN)])

    events = list(orch.build_stream("Build a push tool."))

    assert not any(e.get("type") == "plan-proposed" for e in events)
    assert any(e.get("decision") == "invalid execution plan" for e in events)


# A plan step that needed no edit is not "not built" (#725). Live (Signal Room 17, #720): a fix plan
# after a crashed build had step 3 "Fix script load order" name `static/index.html`, which already
# loaded the scripts in the right order. The build rightly wrote nothing there, and the turn ended
# "Incomplete — plan step 3 (Fix script load order) not built" over an app that was fine. The
# implementer may now say a step needs no edit, with a reason the person is shown and the plan
# review (#716) reads beside that step's files as they are.
LOAD_ORDER_FIX = Turn(text="""# Signal Room fix

Stop the brief panel crashing the page on open.

## Done when
- The page opens with the brief panel.

## Plan
### 1. Guard the empty brief
- Files — static/app.js
- Do — Render the panel only once the brief has rows.
- Done when — The page opens with no rows.

### 2. Brief panel
- Files — static/components/Brief.js
- Do — Default the rows to an empty list.
- Done when — The panel shows "No brief yet" with no rows.

### 3. Fix script load order
- Files — static/index.html
- Do — Load static/components/Brief.js above static/app.js.
- Done when — Brief.js loads before app.js.
""")

INDEX = ('<script src="static/components/Brief.js"></script>\n'
         '<script src="static/app.js"></script>\n')

FIXED = Turn(writes={"static/app.js": "// guarded\n",
                     "static/components/Brief.js": "window.sr.Brief = () => null;\n"})

CLAIM = "index.html already loads static/components/Brief.js above static/app.js."
CLAIMED = Turn(text=f"Step 3 was already right.\n\nNO_EDIT_NEEDED 3: {CLAIM}\n")

STEP_3_UNMET = {"unmet": [{"step": 3, "done_when": "Brief.js loads before app.js.",
                           "file": "static/index.html", "why": "app.js is loaded first"}]}


def _fix_with_index_on_disk(tmp_path: Path, review: object, *after: Turn):
    from .test_a_planned_build_is_reviewed_against_its_done_when import ReviewGateway
    from .test_a_planned_build_is_reviewed_against_its_done_when import _orch as _review_orch

    gateway = ReviewGateway(review)
    orch, oc = _review_orch(tmp_path, gateway, [LOAD_ORDER_FIX, FIXED, *after])
    app = orch.project(start_preview=False).app_for_turn()
    (app.path / "static").mkdir(exist_ok=True)
    (app.path / "static" / "index.html").write_text(INDEX)
    app.mark_built()
    app.set_last_turn_failed(True)
    list(orch.build_stream("the page crashes when it opens", conversation="c1"))
    events = list(orch.approve_stream(conversation="c1"))
    return events, app.read_history("c1"), gateway, oc


def test_a_step_that_needed_no_edit_is_not_reported_as_unbuilt(tmp_path: Path):
    events, history, gateway, oc = _fix_with_index_on_disk(
        tmp_path, json.dumps({"unmet": []}), CLAIMED)

    [continuation] = [p["text"] for p in oc.prompts[2:]]
    assert "NO_EDIT_NEEDED" in continuation
    [done] = [e for e in events if e["type"] == "done"]
    assert done["ok"] is True
    assert "plan-unbuilt" not in [r["type"] for r in history]
    # The person is told which step needed no edit, and why.
    [shown] = [r for r in history if r["type"] == "plan-no-edit"]
    assert shown["steps"] == [3]
    assert shown["message"] == f"Plan step 3 (Fix script load order) needed no edit: {CLAIM}"
    # The marker is addressed to Sage; the transcript keeps the prose around it.
    said = [r["text"] for r in history if r["type"] == "agent" and r.get("kind") == "text"]
    assert "Step 3 was already right." in "\n".join(said)
    assert "NO_EDIT_NEEDED" not in "\n".join(said)
    # The review read the claim beside the file it is about, as that file is now.
    [request] = gateway.reviews
    text = "\n".join(m["content"] for m in request["messages"])
    assert CLAIM in text and INDEX in text


def test_a_step_that_needed_an_edit_and_got_none_is_still_reported(tmp_path: Path):
    events, history, _, _ = _fix_with_index_on_disk(tmp_path, "{}", Turn(text="All done."))

    [done] = [e for e in events if e["type"] == "done"]
    assert done["ok"] is False
    assert done["decision"] == "incomplete — plan step 3 (Fix script load order) not built"
    assert [r["steps"] for r in history if r["type"] == "plan-unbuilt"] == [[3]]
    assert "plan-no-edit" not in [r["type"] for r in history]


def test_a_claim_the_review_refuses_is_sent_back_and_cannot_be_made_again(tmp_path: Path):
    """The sibling the instance did not have: the claim is wrong. The review names the step's
    Done-when, the claim is withdrawn, and the same claim in the reply is not honoured, so a step
    that needed an edit and got none is still reported (#684)."""
    events, history, gateway, oc = _fix_with_index_on_disk(
        tmp_path, json.dumps(STEP_3_UNMET), CLAIMED, CLAIMED)

    repairs = [p["text"] for p in oc.prompts[2:]]
    assert len(repairs) == 2 and "app.js is loaded first" in repairs[1]
    assert len(gateway.reviews) == 1
    [done] = [e for e in events if e["type"] == "done"]
    assert done["ok"] is False
    assert done["decision"] == "incomplete — plan step 3 (Fix script load order) not built"
    assert [r["steps"] for r in history if r["type"] == "plan-unbuilt"] == [[3]]
    assert "plan-no-edit" not in [r["type"] for r in history]


def test_the_transcript_draws_a_no_edit_step_as_a_plain_line():
    from .test_build_conversation_return import run as render

    line = f"Plan step 3 (Fix script load order) needed no edit: {CLAIM}"
    rows = render({"savedHistory": [
        {"type": "user", "text": "Approved the plan."},
        {"type": "done", "ok": True, "decision": "clean"},
        {"type": "plan-no-edit", "steps": [3], "message": line},
    ]})
    assert rows[-1] == {"type": "status", "value": line}
