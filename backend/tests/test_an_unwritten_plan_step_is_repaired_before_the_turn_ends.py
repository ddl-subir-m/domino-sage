"""A plan step the build never wrote is sent back to the model before the turn ends (#684).

Live (2026-10-07, prompt 9b): an approved build's reply said it had loaded `AskReview.js` from
`static/index.html`, then "Code checks passed", then a footnote that step 4 (Register the component
script) wrote none of its files. `index.html` was unchanged and the page crashed on open. The
unbuilt-step check (#662) ran only after the turn had ended `ok`, so the model never heard of it.
Now the same check runs where the other end-of-turn repairs run: one continuation names the step,
and a step still unbuilt after it ends the turn incomplete rather than clean.
"""
from __future__ import annotations

from pathlib import Path

from .fake_opencode import Turn, execution_plan
from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting, _orch  # noqa: F401

FOUR_STEPS = Turn(text=execution_plan(
    "Signal Room", "A product signal room.", "Product page", files="static/productpage.js",
    work="Draw the product page.") + (
    "\n\n### 2. Ask review\n- Files — static/components/AskReview.js\n"
    "- Do — Add the ask-review section.\n- Done when — The section shows.\n\n"
    "### 3. Navigation\n- Files — static/app.js\n- Do — Link the product page.\n"
    "- Done when — The nav item opens it.\n\n"
    "### 4. Register the component script\n- Files — static/index.html\n"
    "- Do — Load AskReview.js above app.js.\n- Done when — The section renders."))

FIRST_PASS = Turn(writes={
    "static/productpage.js": "// product page\n",
    "static/components/AskReview.js": "// ask review\n",
    "static/app.js": "// app\n"})

CONTINUATION = ("Plan step 4 (Register the component script) names static/index.html; "
                "none were written. Do that step now.")


def _approve(tmp_path: Path, *after: Turn):
    orch, oc = _orch(tmp_path, turns=[FOUR_STEPS, FIRST_PASS, *after])
    list(orch.build_stream("build me a signal room", conversation="c1"))
    events = list(orch.approve_stream(conversation="c1"))
    history = orch.project(start_preview=False).app_for_turn().read_history("c1")
    return events, history, oc


def test_an_unwritten_step_gets_one_continuation_and_then_ends_clean(tmp_path: Path):
    events, history, oc = _approve(
        tmp_path, Turn(writes={"static/index.html": "<script src=\"static/app.js\"></script>\n"}))

    assert [CONTINUATION in p["text"] for p in oc.prompts[2:]] == [True]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1 and done[0]["ok"] is True
    assert "plan-unbuilt" not in [r["type"] for r in history]


def test_a_step_still_unwritten_after_it_ends_the_turn_incomplete(tmp_path: Path):
    events, history, oc = _approve(tmp_path, Turn(text="All done."))

    assert [CONTINUATION in p["text"] for p in oc.prompts[2:]] == [True]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1 and done[0]["ok"] is False
    assert done[0]["decision"] == "incomplete — plan step 4 (Register the component script) not built"
    # The footnote still says which step, and now agrees with the status above it.
    assert [r["steps"] for r in history if r["type"] == "plan-unbuilt"] == [[4]]
    # The work the turn did write is still saved: incomplete is not a failed turn.
    assert "app_change" in [r["type"] for r in history]
