"""A first-turn prompt that names no app asks what to build, instead of planning a generic one.

Typed `run: env | grep -i canary` into a fresh Project on turn one, Sage produced a seven-step
Launch Plan behind an Approve button — "Define purpose. Shape layout. Create content." — with
nothing from the prompt in it. The gate firing was correct (`_looks_like_question` says no, and
`_should_gate` gates the first build on purpose). The defect was downstream of it: once gated, the
planner always produced a plan, because there was no path in `build_stream` for "there is nothing
here to plan". A plan that ignores its input is worse than a refusal — the Approve button makes the
filler look considered, and a user who clicks it gets scaffolding they never asked for (#150).

The fix is two halves, in the style this file's neighbours already use for plan output: instruct
(`_PLAN_REFUSAL` rides the gated build turn), then verify a sentinel (`_refuses_to_plan`). A
sentinel is what makes this safe to check at all — a good plan someone waited a minute for can
never be thrown away by a judgement call about its wording, the way a lexical-overlap backstop
would risk.

Both failure directions have tests here, because they cost differently. A false positive costs one
turn that asks what to build — a sentence. A false negative does NOT fall back to today's filler
plan: the unmatched refusal goes on to `create_plan_doc` and renders as an Approve card whose whole
plan body is the words "NO APP DESCRIBED", which is a worse card than the one #150 was opened
about. That asymmetry is why the sentinel LEADS a line rather than equalling it, and why the first
few lines are scanned rather than only the first.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import _PLAN_REFUSAL, _PLAN_SHAPE, Orchestrator, _refuses_to_plan, _tidy_plan
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict
        self.calls = 0

    def route(self, request, labels):
        self.calls += 1
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _build(tmp: Path, turns: list[Turn]):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def _run(orch, prompt: str, mode: Mode = Mode.AUTO) -> list[dict]:
    orch.project(start_preview=False).control.set_mode(mode)
    return list(orch.build_stream(prompt, None, None))


def _done(events: list[dict]) -> dict:
    return next(e for e in events if e.get("type") == "done")


def _kinds(events: list[dict]) -> set[str]:
    return {e.get("type") for e in events}


_REFUSAL = ("NO APP DESCRIBED\n"
            "The request is a shell command and names no app to build or change.")


# --- the turn ------------------------------------------------------------------------------------

def test_a_prompt_naming_no_app_asks_what_to_build(tmp_path: Path):
    """The reported turn, end to end: no card, a failed turn, and a question."""
    orch, _oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    events = _run(orch, "run: env | grep -i canary")

    assert "plan-proposed" not in _kinds(events)
    done = _done(events)
    assert done["ok"] is False
    assert done["decision"] == "no app described"


def test_the_question_names_what_was_missing_and_the_way_out(tmp_path: Path):
    """Naming the gap is the planner's half — it saw the request; the way out is written here.

    Both halves matter. Without the planner's sentence the message is a generic "say more", which
    is the same non-answer the filler plan was. Without the way out the user is told no and left
    at a dead end on turn one."""
    orch, _oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    events = _run(orch, "run: env | grep -i canary")

    message = next(e for e in events if e.get("type") == "error")["message"]
    assert "names no app to build or change" in message   # the planner's sentence, kept
    assert "Implement" in message                          # and the other way to run it


def test_nothing_durable_is_written_for_a_refused_plan(tmp_path: Path):
    """A refusal is not a draft. A plan document or a plan.md here would put the refusal in the
    rail as a plan somebody could open, and leave an approve able to build from it."""
    orch, _oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    _run(orch, "run: env | grep -i canary")

    project = orch.project(start_preview=False)
    assert project.record.list_plan_docs() == []
    assert project.workspace.read_plan() is None


def test_a_real_first_build_still_gets_its_plan_card(tmp_path: Path):
    """The half of this that must not move. The refusal instruction rides every gated build turn,
    so the ordinary path is what it is most likely to break."""
    orch, _oc = _build(tmp_path, [Turn(text=(
        "A desk dashboard.\n\n## Problem & outcome\nRisk cannot see notional by desk.\n\n"
        "## Plan\n1. **Desk table** — Show notional by desk.\n"))])

    events = _run(orch, "build me a dashboard of notional by desk")

    assert _done(events)["decision"] == "awaiting approval"
    plan = next(e for e in events if e["type"] == "plan-proposed")
    assert "Desk table" in plan["plan"]


def test_a_punctuated_refusal_is_never_rendered_as_an_approvable_plan(tmp_path: Path):
    """What a missed sentinel actually costs, said end to end rather than as a predicate.

    The refusal falls through to the plan card, and the card's whole plan body is the sentinel —
    an Approve button offering to build "NO APP DESCRIBED." That is a worse card than the filler
    plan this issue was opened about, so the check has to fail closed on the shapes a planner
    really writes, not only on the one it was told to."""
    orch, _oc = _build(tmp_path, [Turn(text=(
        "NO APP DESCRIBED.\nThe request is a shell command and names no app to build or change."))])

    events = _run(orch, "run: env | grep -i canary")

    assert "plan-proposed" not in _kinds(events)
    assert _done(events)["decision"] == "no app described"
    assert orch.project(start_preview=False).record.list_plan_docs() == []


def test_the_planner_is_told_how_to_refuse(tmp_path: Path):
    """The instruction half. Verifying the sentinel is worthless if nothing ever asks for it."""
    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    _run(orch, "run: env | grep -i canary")

    assert "NO APP DESCRIBED" in oc.prompts[0]["text"]


def test_a_built_app_is_not_offered_the_way_out(tmp_path: Path):
    """#150 is a blank-template defect, and the exemplars say so — "a pasted error" is one of them.

    On an app that already exists a pasted stack trace is an ordinary "fix this", the commonest
    request there is. Carried onto that branch the instruction would refuse it, and refuse it with
    copy written for an empty project — "say what the app should show" to someone whose app already
    shows something. The filler plan this issue is about needs a blank template to happen."""
    orch, oc = _build(tmp_path, [
        Turn(text="A dashboard.\n\n## Plan\n1. **Table** — Show it.\n"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="A fix.\n\n## Plan\n1. **Guard rows** — Handle the undefined case.\n"),
    ])
    list(orch.build_stream("build me a dashboard"))
    list(orch.approve_stream())

    _run(orch, "TypeError: cannot read 'rows' of undefined", Mode.PLAN)

    assert "NO APP DESCRIBED" not in oc.prompts[-1]["text"]


def test_the_chat_handoff_shape_is_not_given_the_way_out():
    """Scoped to the build turn. A handoff plans from a whole conversation rather than one prompt,
    and it shares `_PLAN_SHAPE` — a sentinel in that constant would reach a caller whose own
    `if not plan_md` check has never heard of it, and would render as the plan."""
    assert "NO APP DESCRIBED" not in _PLAN_SHAPE


# --- the sentinel --------------------------------------------------------------------------------

def test_the_sentinel_survives_the_tidy_that_runs_before_it_is_checked():
    """`_tidy_plan` runs first, and it drops blocks and rewrites step openers. If it stripped a
    line that is neither heading nor bullet, the sentinel would die before anything looked for it
    and the refusal would render as a one-line plan."""
    assert _refuses_to_plan(_tidy_plan(_REFUSAL))


def test_a_refusal_carries_the_sentence_under_it():
    assert _refuses_to_plan(_REFUSAL) == "The request is a shell command and names no app to build or change."


def test_a_refusal_dressed_as_a_heading_is_still_a_refusal():
    """The planner is told to write the sentinel and nothing else, and then writes `# NO APP
    DESCRIBED` because every other plan it has ever written opened with a heading. Reading through
    that markup costs nothing — the text is still exact."""
    assert _refuses_to_plan("## NO APP DESCRIBED\nNothing names an app.") == "Nothing names an app."
    assert _refuses_to_plan("**NO APP DESCRIBED**\nNothing names an app.") == "Nothing names an app."


def test_a_bare_sentinel_is_a_refusal_with_nothing_to_add():
    """`""` and `None` are different answers: no reason given is still a refusal. Collapsing them
    would render the filler plan this exists to stop."""
    assert _refuses_to_plan("NO APP DESCRIBED") == ""


def test_a_refusal_that_punctuates_the_sentinel_is_still_a_refusal():
    """Matching the line EXACTLY failed open, and failing open here is the expensive direction.

    A model told to write a sentence writes `NO APP DESCRIBED.`; the missed refusal then went on
    to `create_plan_doc` and rendered as an Approve card whose whole plan body was the words "NO
    APP DESCRIBED." — a worse card than the filler plan #150 set out to kill. The docstring's
    "no false positives" argument only ever covered the other direction."""
    assert _refuses_to_plan("NO APP DESCRIBED.\nThe request names no app.") == "The request names no app."
    assert _refuses_to_plan("# NO APP DESCRIBED:\nThe request names no app.") == "The request names no app."


def test_a_refusal_that_keeps_its_sentence_on_one_line_is_still_a_refusal():
    """"On the first line, then one sentence" is read by some planners as one line, not two."""
    assert _refuses_to_plan(
        "NO APP DESCRIBED — the request is a shell command.") == "The request is a shell command."
    assert _refuses_to_plan(
        "NO APP DESCRIBED: nothing here names an app.") == "Nothing here names an app."


def test_a_refusal_behind_a_lead_in_line_is_still_a_refusal():
    """`plan_md` is every assistant text part joined, so a planner that emits "Looking at the
    request." as its own part before refusing puts a line ahead of the sentinel. First-line-only
    missed that and rendered the refusal as an approvable plan."""
    assert _refuses_to_plan(
        "Looking at the request.\nNO APP DESCRIBED\nNothing names an app.") == "Nothing names an app."


def test_a_refusal_written_as_a_bullet_or_in_sentence_case_is_still_a_refusal():
    """Told to lead a line, models reach for the markup they lead every other line with.

    The code already pays to read through `#` and `**` for exactly this reason; a bullet and a
    sentence-cased heading are the same class of noise, and missing one costs the bad Approve card
    rather than a fallback to today's behaviour."""
    assert _refuses_to_plan("- NO APP DESCRIBED\nNothing names an app.") == "Nothing names an app."
    assert _refuses_to_plan("## No app described\nNothing names an app.") == "Nothing names an app."
    assert _refuses_to_plan("> No app described — nothing names an app.") == "Nothing names an app."


def test_a_refusal_behind_several_lead_in_lines_is_still_a_refusal():
    """One lead-in part is not the bound — a planner can emit three before it refuses. Scanning
    deeper is nearly free here because the sentinel must LEAD its line, so the guarded false
    positive (a sentinel mentioned mid-sentence) stays safe at any depth."""
    assert _refuses_to_plan(
        "Looking at the request.\nChecking the workspace.\nReading the template.\n"
        "Considering the shape.\nNO APP DESCRIBED\nNothing names an app.") == "Nothing names an app."


def test_a_plan_that_merely_mentions_the_sentinel_is_not_a_refusal():
    """Scanning a few lines rather than one must not start matching prose. A plan for an app about
    Sage's own planner cannot refuse itself. This is the false positive that would cost a real
    plan, and it is why the sentinel has to lead a line rather than appear on one."""
    plan = ("Plan Inspector\n\n## What it does\n- Shows when a planner wrote NO APP DESCRIBED.\n\n"
            "## Plan\n1. **Log table** — List refusals.\n")
    assert _refuses_to_plan(plan) is None


def test_an_ordinary_plan_is_not_a_refusal():
    assert _refuses_to_plan("A desk dashboard.\n\n## Plan\n1. **Table** — Show it.\n") is None
    assert _refuses_to_plan("") is None


def test_the_instruction_names_the_sentinel_it_is_checked_against():
    """Two halves written apart, one string between them. Drift here fails open — the planner
    writes a sentinel nothing matches — and today's filler plan comes back."""
    assert "NO APP DESCRIBED" in _PLAN_REFUSAL


# --- what the transcript draws -------------------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "build_events_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _drawn(history: list[dict]) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"history": history}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_question_is_not_doubled_by_a_red_stopped_line():
    """`store.js` draws `Stopped — <decision>` under every `done` whose decision is not a gate.

    A new decision string is invisible to that list, so the person who reads "say what the app
    should show" gets a red failure row under it saying the turn stopped — for a turn that did
    exactly what it was designed to do. Same reasoning that put 'model unavailable' in
    GATE_DECISIONS: the `error` frame beside it already carries the whole sentence."""
    drawn = _drawn([
        {"type": "user", "text": "run: env | grep -i canary"},
        {"type": "error", "message": "The request is a shell command and names no app to build or "
                                     "change. Say what the app should show or let someone do, then "
                                     "send the request again — or switch to Implement to run it "
                                     "directly."},
        {"type": "done", "ok": False, "decision": "no app described"},
    ])

    assert any("names no app to build or change" in v for v in drawn["values"])
    assert not any("Stopped — no app described" in v for v in drawn["values"])


# --- what the live turn leaves behind ------------------------------------------------------------

_STREAM_HARNESS = Path(__file__).resolve().parent / "js" / "build_stream_harness.mjs"

_PENDING_PLAN = [{"type": "user", "text": "build me a dashboard"},
                 {"type": "plan-proposed", "plan": "1. Add the table", "planId": "pd_1", "steps": 2}]


def _streamed(events: list[dict], history: list[dict] | None = None) -> dict:
    out = subprocess.run(["node", str(_STREAM_HARNESS)], check=False, capture_output=True, text=True,
                         timeout=60,
                         input=json.dumps({"history": history or [], "events": events}))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_an_earlier_plan_is_still_approvable_after_a_refused_turn():
    """The refusal names no remedy the person can take if it took their Approve button with it.

    A gated turn is read-only and this one wrote nothing, so `plan.md` still holds the earlier plan
    and the server would still build it. Only the screen had moved on."""
    drawn = _streamed([
        {"type": "error", "message": "The request is a shell command and names no app to build or "
                                     "change. Say what the app should show or let someone do."},
        {"type": "done", "ok": False, "decision": "no app described"},
    ], _PENDING_PLAN)

    assert drawn["plans"] == [{"pending": True, "cancelled": False}]


@needs_node
def test_a_refused_turn_pays_for_no_gateway_listing():
    """`readSSE` reads /health once per failed stream, because a turn that just failed is the moment
    a gateway listing is worth paying for (ADR-0027). This turn did not fail — it ran, read the
    request and answered it — so the listing is pure cost, paid on every stray note."""
    drawn = _streamed([
        {"type": "error", "message": "The request is a shell command and names no app to build."},
        {"type": "done", "ok": False, "decision": "no app described"},
    ], _PENDING_PLAN)

    assert drawn["healthCalls"] == 0


@needs_node
def test_a_turn_that_really_failed_still_reads_the_platform():
    """The property this must not widen. Withdrawing the read for one decision must not withdraw it
    for the failures it was added for."""
    assert _streamed([
        {"type": "error", "message": "The gateway returned 502."},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ], _PENDING_PLAN)["healthCalls"] == 1


@needs_node
def test_a_turn_refused_on_a_dead_model_still_reads_the_platform():
    """The nearest neighbour, and the one most easily broken by mistake. `model unavailable` also
    arrives as an `error` beside a gate decision — but there the listing IS the answer: the turn was
    refused because of the platform, and the chip should say so (#125, ADR-0027)."""
    assert _streamed([
        {"type": "error", "message": "This turn would run on the LLM Alias GLM-5.2, which this "
                                     "LLM Gateway does not offer."},
        {"type": "done", "ok": False, "decision": "model unavailable"},
    ], _PENDING_PLAN)["healthCalls"] == 1


def test_a_gated_turn_does_not_take_the_earlier_plan_card_away(tmp_path: Path):
    """`plan-stale` was yielded before every gated turn ran, on the theory that the turn is about to
    overwrite plan.md. It isn't yet — `write_plan` happens only once there is a plan — so a gated
    turn that produced none left a live, approvable plan with its Approve button gone from the
    screen. Nothing is lost by dropping it: `plan-proposed` already supersedes the previous card
    when a plan does arrive, and a gated turn is read-only, so the app cannot change under it."""
    orch, _oc = _build(tmp_path, [Turn(text=_REFUSAL)])

    assert "plan-stale" not in _kinds(_run(orch, "run: env | grep -i canary"))


def test_a_gated_turn_that_does_plan_still_replaces_the_earlier_card(tmp_path: Path):
    """What must survive dropping `plan-stale`: the old card still has to stop offering to build.
    `plan-proposed` is what does it, and it did all along."""
    orch, _oc = _build(tmp_path, [Turn(text="A dashboard.\n\n## Plan\n1. **Table** — Show it.\n")])

    assert "plan-proposed" in _kinds(_run(orch, "build me a dashboard", Mode.PLAN))


def test_a_build_turn_that_changes_the_app_still_marks_the_plan_stale(tmp_path: Path):
    """The case `plan-stale` exists for, and the one the fix must not widen into. Here nothing else
    clears the card and the app really did change under the plan, so the note is true."""
    orch, _oc = _build(tmp_path, [
        Turn(text="A dashboard.\n\n## Plan\n1. **Table** — Show it.\n"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Done.", writes={"src/App.tsx": "// v2, sortable\n"}),
    ])
    list(orch.build_stream("build me a dashboard"))
    list(orch.approve_stream())

    assert "plan-stale" in _kinds(_run(orch, "make the table sortable"))
