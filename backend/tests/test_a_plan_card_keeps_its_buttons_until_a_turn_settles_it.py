"""A plan card keeps its Approve and Cancel until a turn actually settles it (#178).

Two events took them away when nothing had. They are fixed together because they clear the same
flag: fixing one alone leaves the other free to hide it.

--- The question (permanent) ---


A plan is on screen waiting for approval. The person types a question — "what does the table sort
by?" — which Sage answers read-only, changing nothing. The plan they were reading loses both its
buttons, and no reload brings them back: the answer-only turn ends with a persisted `done` whose
decision is `answered`, and `buildHistoryToMessages` closes the plan card under every `done` whose
decision is not a gate decision. So the row replays out of `.sage/history.jsonl` on every load and
clears the card again. `plan.md` still holds the plan and the server would still build it; only the
screen had given up on it. Archive then refuses it for having an open Approve card the person can no
longer see — the dead end #178 was opened about.

The server always intended this. `plan-stale` is skipped for an answer-only turn on purpose, with
the reason written beside it: asking a question shouldn't cost the user the plan they were reading.
It was the client that disagreed.

The trap in the one-line fix is that `GATE_DECISIONS` was answering two questions at once — whether
the plan card keeps its buttons, and whether the ending draws a status line of its own. Adding
`answered` to that list gives the buttons back and deletes the "Answered" chip, which is the one
thing that tells the person their question was heard. The two lists are split, so both tests below
have to pass together; either alone is a fix that breaks the other half.

--- The gated turn that failed (permanent) ---

The third door to the same refusal, found reviewing the first two. A gated turn that dies —
`gateway error`, `stalled`, `wedged`, an empty plan — ends on a decision that is not a gate
decision, so it closed the earlier card. But a gated turn is read-only by construction and
`write_plan` runs only once there IS a plan, so `plan.md` still held the earlier one and Archive
went on refusing it against two buttons no longer on the screen.

A decision cannot answer this by itself: `gateway error` ends a gated turn that wrote nothing and a
half-finished build alike, and only the first may keep the card. So the server says it. Every
terminal `done` passes through one `persist`, and it now carries the read-only reason when there was
one — which is also why this is not a list the next failure decision can fall off.

--- The change request (for the length of one turn) ---

`plan-stale` closed the card too, and it is never persisted — neither sender calls `persist`. So the
buttons vanished on a change request and came back on F5: the only thing on that card drawn from
state a reload throws away. The client now ignores the event.

Nothing is lost. The card draws no actions while a turn is running, and the turn that yields
`plan-stale` ends with a persisted `done` that settles the card a moment later — so the reload
answer was always the honest one and it always arrived. Where the two disagreed, `plan-stale` was
the wrong one: a typed approval yields it and can then be refused for an unbound model, and that
refusal tells the person to approve again with the Approve button already gone (#125).
"""
from __future__ import annotations

import json
import shutil
import subprocess
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

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
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


def _run(orch, prompt: str, mode: Mode) -> list[dict]:
    orch.project(start_preview=False).control.set_mode(mode)
    return list(orch.build_stream(prompt, None, None))


# --- what the reloaded transcript draws -----------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "build_events_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

_PENDING_PLAN = [{"type": "user", "text": "build me a trades dashboard"},
                 {"type": "plan-proposed", "plan": "1. Add the table", "planId": "pd_1", "steps": 2}]

_ANSWERED = [{"type": "user", "text": "what does the table sort by?"},
             {"type": "agent", "kind": "text", "text": "By trade date, newest first."},
             {"type": "done", "ok": True, "decision": "answered"}]


def _drawn(history: list[dict]) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"history": history}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_an_answered_question_leaves_the_plan_card_approvable():
    """The bug itself, read where it is permanent. This history is what a reload replays, so a card
    that comes back without its buttons here comes back without them forever."""
    drawn = _drawn(_PENDING_PLAN + _ANSWERED)

    assert drawn["plans"] == [{"pending": True, "cancelled": False}]


@needs_node
def test_an_answered_question_still_says_it_was_answered():
    """The half a one-line `GATE_DECISIONS` entry would have quietly deleted. The "Answered" chip is
    the only thing under a read-only turn that says the question was heard — a gate decision draws no
    chip precisely because its own card speaks for it, and an answer has no card."""
    assert "Answered" in _drawn(_PENDING_PLAN + _ANSWERED)["values"]


@needs_node
def test_a_build_that_really_ran_still_closes_its_plan_card():
    """The property the split must not widen. Only `answered` joins the gate decisions in keeping
    the card; every other ending still settles it."""
    drawn = _drawn(_PENDING_PLAN + [{"type": "done", "ok": True, "decision": "typecheck clean"}])

    assert drawn["plans"] == [{"pending": False, "cancelled": False}]


# --- what the live turn leaves behind -------------------------------------------------------------

_STREAM_HARNESS = Path(__file__).resolve().parent / "js" / "build_stream_harness.mjs"

_STALE = {"type": "plan-stale", "note": "Approved in chat — building this plan."}

_REFUSED_ON_A_DEAD_MODEL = [
    {"type": "error", "message": "This turn would run on Sage's implement model, the LLM Alias "
                                 "GLM-5.2, which this LLM Gateway does not offer. Pick a model this "
                                 "gateway offers and approve again."},
    {"type": "done", "ok": False, "decision": "model unavailable"},
]


def _streamed(events: list[dict], history: list[dict]) -> dict:
    out = subprocess.run(["node", str(_STREAM_HARNESS)], check=False, capture_output=True, text=True,
                         timeout=60,
                         input=json.dumps({"history": history, "events": events}))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_card_keeps_its_buttons_while_the_answer_is_still_arriving():
    """Live and reloaded are different code paths over the same branch (`readSSE` accumulates into
    `buildHistory` and re-runs `buildHistoryToMessages`), and the acceptance is that the card says
    the same thing on both. Pinned on both sides so it stays that way."""
    assert _streamed(_ANSWERED[1:], _PENDING_PLAN)["plans"] == [{"pending": True,
                                                                "cancelled": False}]


# --- and the event that only ever existed live ---------------------------------------------------


@needs_node
def test_a_typed_approval_refused_on_a_dead_model_leaves_the_plan_approvable():
    """The case where `plan-stale` was not merely early but wrong.

    A typed "yes, build it" yields `plan-stale` and then hands off to the approve path, which
    preflights the model and can refuse there. The refusal names its remedy — pick another model and
    approve again — and `plan-stale` had already taken the Approve button, which is the #125 dead end
    reopened through the typed door. `model unavailable` keeps the card; nothing before it may
    quietly have closed it first."""
    drawn = _streamed([_STALE] + _REFUSED_ON_A_DEAD_MODEL, _PENDING_PLAN)

    assert drawn["plans"] == [{"pending": True, "cancelled": False}]


@needs_node
def test_a_change_request_says_the_same_thing_before_and_after_a_reload():
    """The acceptance for this half, written as the comparison it actually is.

    `plan-stale` is never persisted, so the reloaded transcript is the same turn with that row
    dropped. The two surfaces disagreed: the buttons went live and came back on F5. Both readings are
    taken here so neither can drift from the other."""
    live = _streamed([_STALE, {"type": "done", "ok": True, "decision": "typecheck clean"}],
                     _PENDING_PLAN)
    reloaded = _drawn(_PENDING_PLAN + [{"type": "done", "ok": True, "decision": "typecheck clean"}])

    assert live["plans"] == reloaded["plans"] == [{"pending": False, "cancelled": False}]


@needs_node
def test_a_refused_approval_says_the_same_thing_before_and_after_a_reload():
    """The same comparison where the settled answer is the other one. Ignoring `plan-stale` is only
    right if the `done` behind it is what decides — including when it decides to keep the card."""
    live = _streamed([_STALE] + _REFUSED_ON_A_DEAD_MODEL, _PENDING_PLAN)
    reloaded = _drawn(_PENDING_PLAN + _REFUSED_ON_A_DEAD_MODEL)

    assert live["plans"] == reloaded["plans"] == [{"pending": True, "cancelled": False}]


# --- the turn that failed before it planned anything ---------------------------------------------


@needs_node
def test_a_gated_turn_that_died_leaves_the_earlier_plan_approvable():
    """The third route, read where it is permanent. Nothing here is a gate decision, so before the
    read-only mark this card came back without its buttons on every load — and `plan.md` still held
    the plan, so Archive went on refusing it and naming buttons that were gone."""
    drawn = _drawn(_PENDING_PLAN + [
        {"type": "user", "text": "add a column for settlement date"},
        {"type": "error", "message": "The gateway returned 502."},
        {"type": "done", "ok": False, "decision": "gateway error", "readOnly": "plan"},
    ])

    assert drawn["plans"] == [{"pending": True, "cancelled": False}]


@needs_node
def test_a_half_finished_build_still_closes_its_plan_card():
    """The same decision on a turn that COULD write, which is why the decision alone cannot answer
    it. This build had edit tools and got partway before the gateway died, so the app really may
    have moved under the earlier plan and the card must settle."""
    drawn = _drawn(_PENDING_PLAN + [
        {"type": "user", "text": "add a column for settlement date"},
        {"type": "error", "message": "The gateway returned 502."},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ])

    assert drawn["plans"] == [{"pending": False, "cancelled": False}]


def test_a_gated_turn_that_produces_no_plan_says_it_was_read_only(tmp_path: Path):
    """The server half, end to end. The mark rides every terminal `done` through one `persist`
    rather than the seven yield sites that can end a turn, so this pins the reason reaches history
    at all — the UI can only keep the card if the row a reload replays carries it."""
    orch, _oc = _build(tmp_path, [Turn(text="   ")])

    events = _run(orch, "build me a trades dashboard", Mode.PLAN)
    done = next(e for e in events if e.get("type") == "done")

    assert done["decision"] == "empty plan"
    assert done["readOnly"] == "plan"


def test_a_build_turn_that_can_write_carries_no_read_only_mark(tmp_path: Path):
    """The other side of the same server fact. A turn with edit tools must not claim the plan
    survived it — that is the mark being handed to the one turn it would be wrong for.

    The app is planned and built first because the first build gates whatever the mode says, and a
    gated turn is read-only: without that the change request comes back `gate violated` and the mark
    it carries is the honest one."""
    orch, _oc = _build(tmp_path, [
        Turn(text="A dashboard.\n\n## Plan\n1. **Table** — Show it.\n"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Done.", writes={"src/App.tsx": "// v2, sortable\n"}),
    ])
    list(orch.build_stream("build me a dashboard"))
    list(orch.approve_stream())

    events = _run(orch, "make the table sortable", Mode.IMPLEMENT)
    done = next(e for e in events if e.get("type") == "done")

    assert "readOnly" not in done


# --- and what the server never says --------------------------------------------------------------


def test_an_answer_only_turn_marks_no_plan_stale(tmp_path: Path):
    """The server's half of the same sentence, and it needed no fix. A question changes nothing, so
    the plan under it did not go stale, and `build_stream` has always skipped the note for an
    answer-only turn with that reason written beside it. Pinned because the client no longer reads
    `plan-stale` at all: if this regressed, nothing on screen would say so."""
    orch, _oc = _build(tmp_path, [Turn(text="By trade date, newest first.")])

    events = _run(orch, "what does the table sort by?", Mode.ASK)

    assert "plan-stale" not in {e.get("type") for e in events}
    assert next(e for e in events if e.get("type") == "done")["decision"] == "answered"
