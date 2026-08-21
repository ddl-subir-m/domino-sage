"""End-to-end turn dispatch, driven through a fake OpenCode.

The three gate changes before this one — the explicit plan request, the scope classifier, the
failure-triggered replan — were each tested as predicates and then wired in by hand, with the wiring
verified by reading. Predicates that pass in isolation and a turn that behaves correctly are not the
same claim. These tests make the second one: prompt in, event stream out, nothing stubbed between the
dispatch decision and the events the UI renders.

What is still faked is deliberate and narrow: the agent (scripted, see fake_opencode), the typecheck
(no tsc in the suite), and the gateway (scripted verdicts, so a test asserts what the ORCHESTRATOR
does with an answer, never what a model would say).
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
    """Typecheck always passes. Type errors have their own tests; here they would only add a second
    reason for a turn to end badly and blur which one a failing assertion meant."""

    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """Answers every routed request with one scripted word. The only caller on this path is the scope
    classifier — the fake agent never reaches the shim — so this controls exactly one decision."""

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict
        self.calls = 0

    def route(self, request, labels):
        self.calls += 1
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Strip the two waits a scripted turn can only ever spend, never use.

    The poll loop sleeps a second between polls — real latency against a real agent, dead wall-clock
    against a fake one. Patched on the stdlib module rather than on `service`, which imports `time`
    function-locally.

    `_await_runtime_error` then polls for four seconds for the preview to report a crash. These tests
    run with `start_preview=False`, so there is no preview, nothing can ever set `project.runtime_error`,
    and the poll is four guaranteed-fruitless seconds per successful build. Worse with sleep patched
    out: the wait stops sleeping and starts spinning, same wall-clock, 43M clock reads. Skipping it
    removes a wait for an event that cannot occur, not a behaviour under test."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _build(tmp: Path, turns: list[Turn], *, verdict: str = "BUILD"):
    """An orchestrator wired to fakes, plus the fake agent and gateway for assertions."""
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    gateway = ScriptedGateway(verdict)
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=gateway, catalog=_catalog(),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc, gateway


def _run(orch, prompt: str, mode: Mode = Mode.AUTO, resources: list[dict] | None = None) -> list[dict]:
    orch.project(start_preview=False).control.set_mode(mode)
    return list(orch.build_stream(prompt, None, resources))


def _get_built(orch) -> None:
    """Take the project through the real first-build flow, consuming the first two scripted turns.

    There is no shortcut worth taking here: the first-build gate fires in every mode including
    Implement, so a turn that "just builds" on turn one doesn't exist, and `has_built` — which every
    later gate decision keys on — is set by a build that actually completed, not by a flag a test
    could plant. Plan, approve, build."""
    list(orch.build_stream("build me a dashboard"))
    list(orch.approve_stream())


def _done(events: list[dict]) -> dict:
    return next(e for e in events if e.get("type") == "done")


def _kinds(events: list[dict]) -> set[str]:
    return {e.get("type") for e in events}


# --- the answer-only path ------------------------------------------------------------------------

def test_a_question_is_answered_without_building(tmp_path: Path):
    orch, oc, gateway = _build(tmp_path, [Turn(text="It uses a bar chart from Highcharts.")])
    events = _run(orch, "what charting library does this use?")

    assert _done(events)["decision"] == "answered"
    assert "plan-proposed" not in _kinds(events)
    # The typecheck is skipped entirely: nothing changed, so running tsc would be dead time and its
    # "passed" line would read as though a build had been verified.
    assert "typecheck" not in _kinds(events)
    # A question never reaches the scope classifier — it's decided for free, before anything paid.
    assert gateway.calls == 0
    assert oc.prompts[0]["agent"] == "sage-ask"


# --- the plan gate -------------------------------------------------------------------------------

def test_a_first_build_gates_and_proposes_a_plan(tmp_path: Path):
    orch, _oc, _ = _build(tmp_path, [Turn(text="1. Add a table\n2. Wire up the data")])
    events = _run(orch, "build me a dashboard")

    assert _done(events)["decision"] == "awaiting approval"
    plan = next(e for e in events if e["type"] == "plan-proposed")
    assert "Add a table" in plan["plan"]
    assert plan["kind"] == "plan"
    # Written where approve_stream will look for it, not just streamed and lost.
    assert (orch.project(start_preview=False).workspace.path / ".sage" / "plan.md").exists()


def test_the_scope_classifier_gates_a_substantial_change_on_a_built_app(tmp_path: Path):
    orch, _oc, gateway = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="1. Add an auth provider\n2. Add an orgs page"),
    ], verdict="PLAN")
    _get_built(orch)
    events = _run(orch, "add auth, orgs and a billing page")

    assert gateway.calls == 1  # the classifier ran, and only once
    assert _done(events)["decision"] == "awaiting approval"
    assert "plan-proposed" in _kinds(events)


def test_a_small_change_on_a_built_app_just_builds(tmp_path: Path):
    orch, _oc, gateway = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Done.", writes={"src/App.tsx": "// v2, sortable\n"}),
    ], verdict="BUILD")
    _get_built(orch)
    events = _run(orch, "make the table sortable")

    assert gateway.calls == 1
    assert "plan-proposed" not in _kinds(events)
    assert _done(events)["ok"] is True
    assert (orch.project(start_preview=False).workspace.path / "src" / "App.tsx").read_text() == "// v2, sortable\n"


# --- failure-triggered replan --------------------------------------------------------------------

def test_a_failed_turn_makes_the_next_one_plan_first(tmp_path: Path):
    # The failure here is a gated turn that produced no plan text — a real, reachable failure ("no
    # plan text" is usually "no inference reached us") rather than an exception injected to force one.
    orch, _oc, _gateway = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text=""),                       # fails: gated, wrote nothing, said nothing
        Turn(text="1. Check the data source\n2. Then retry"),
    ], verdict="BUILD")
    _get_built(orch)

    failed = _run(orch, "plan the retraining work", Mode.PLAN)
    assert _done(failed)["ok"] is False
    ws = orch.project(start_preview=False).workspace
    assert ws.read_last_turn_failed() is True

    # The retry would be an ungated build turn on a built project; the failure gates it instead.
    retry = _run(orch, "try that again")
    assert _done(retry)["decision"] == "awaiting approval"
    # And the signal is spent: the classifier's BUILD verdict is what decides the turn after.
    assert ws.read_last_turn_failed() is False


def test_a_question_after_a_failure_does_not_spend_the_gate(tmp_path: Path):
    orch, _oc, _gateway = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text=""),                       # fails
        Turn(text="Because the data source was empty."),
        Turn(text="1. Fix the data source"),
    ], verdict="BUILD")
    _get_built(orch)
    _run(orch, "plan the retraining work", Mode.PLAN)

    ws = orch.project(start_preview=False).workspace
    _run(orch, "why did that fail?")
    assert ws.read_last_turn_failed() is True  # asking about the failure must not consume it

    assert _done(_run(orch, "try that again"))["decision"] == "awaiting approval"


# --- a turn with nothing to build (#29) -----------------------------------------------------------

def _app(orch) -> str:
    return (orch.project(start_preview=False).workspace.path / "src" / "App.tsx").read_text()


def test_a_question_on_a_built_app_is_answered_without_touching_it(tmp_path: Path):
    """The first half of #29: the user asked to be TOLD something about a built app.

    `_a_question_is_answered_without_building` covers the same short-circuit on turn one, where the
    app doesn't exist yet; this is the case that actually shipped broken, because a built app is the
    only state in which a turn has somewhere to write the answer."""
    orch, oc, _gw = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="There is no clickstream table attached to this project yet."),
    ], verdict="BUILD")
    _get_built(orch)

    events = _run(orch, "explore the clickstream table and tell me what information it has "
                        "we will then use it to build a new dashboard")

    assert _done(events)["decision"] == "answered"
    assert oc.prompts[-1]["agent"] == "sage-ask"
    # The whole defect in one assertion: the app is exactly as the build left it.
    assert _app(orch) == "// v1\n"


def test_a_request_that_cannot_be_acted_on_ends_the_turn_instead_of_writing_the_app(tmp_path: Path):
    """The purer half of #29. "i attached it" is a statement, not a question, so the answer-only
    path above can't catch it — the turn dispatches as a build and the agent has genuinely nothing
    to build. Before the marker existed its only legal move was to write its explanation into
    src/App.tsx, and the creator ended up with a dashboard whose UI said their file wasn't showing."""
    orch, _oc, _gw = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="I still can't see a clickstream table in this project. Once it's attached I'll "
                  "build the dashboard on top of it.\nNOTHING_TO_BUILD"),
    ], verdict="BUILD")
    _get_built(orch)

    events = _run(orch, "i attached it")

    assert _done(events) == {"type": "done", "ok": True, "decision": "nothing to build"}
    assert _app(orch) == "// v1\n"
    # Not nudged. The nudge is what turned a correct refusal into a write: it force-switches the
    # turn to Implement and pushes until something lands in src/.
    assert "iterate" not in _kinds(events)
    # And no typecheck, for the same reason a plan turn skips it — dead time over an untouched tree,
    # with a "passed" line that reads like a build was verified.
    assert "typecheck" not in _kinds(events)

    # The user is TOLD, and the marker isn't part of what they read.
    said = "\n".join(e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text")
    assert "clickstream table" in said
    assert "NOTHING_TO_BUILD" not in said


def test_a_turn_that_writes_nothing_without_the_marker_is_still_a_failure(tmp_path: Path):
    """The rule the marker is an exception to has to stay closed. An agent that stalls at a plan
    looks identical from outside — no edits, some prose — and must still be nudged and then reported,
    or #29's fix quietly re-opens the failure AGENTS.md's src/ rule was written against."""
    orch, _oc, _gw = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Here's how I'd approach it: first the schema, then the table."),
    ], verdict="BUILD")
    _get_built(orch)

    events = _run(orch, "add a severity filter")

    assert _done(events)["ok"] is False
    assert "iterate" in _kinds(events)


def test_the_marker_cannot_unmake_edits(tmp_path: Path):
    """Claimed AND wrote. The filesystem is the ground truth: the marker excuses a turn from
    editing, it can't retroactively excuse the edits it made. Falls through to the normal build
    path so those edits are typechecked and kept like any other build's."""
    orch, _oc, _gw = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Nothing to do here.\nNOTHING_TO_BUILD", writes={"src/App.tsx": "// v2\n"}),
    ], verdict="BUILD")
    _get_built(orch)

    events = _run(orch, "make the table sortable")

    assert _done(events)["decision"] != "nothing to build"
    assert _done(events)["ok"] is True
    assert _app(orch) == "// v2\n"


def test_a_marker_on_a_plan_turn_is_stripped_from_the_card(tmp_path: Path):
    """A gated turn writes nothing by design, so honouring the marker there would let any planner
    end the turn with no plan to approve. The gate resolves first; the marker is only stripped, so
    it can't be persisted into plan.md or shown on the approval card."""
    orch, _oc, _gw = _build(tmp_path, [
        Turn(text="1. Add a table\n2. Wire up the data\nNOTHING_TO_BUILD"),
    ])
    events = _run(orch, "build me a dashboard")

    assert _done(events)["decision"] == "awaiting approval"
    plan = next(e for e in events if e["type"] == "plan-proposed")["plan"]
    assert "Add a table" in plan
    assert "NOTHING_TO_BUILD" not in plan


# --- @mentioned Resources (#31) ------------------------------------------------------------------


def test_a_mentioned_resource_reaches_the_agent_as_a_binding_not_a_word(tmp_path: Path):
    """What the creator typed is "@sonnet", which is a word. What the agent gets is the record behind
    it — the kind, the Resource, and what the app already does with it — because the word alone is
    what a creator holding several Bindings has no way to disambiguate."""
    orch, oc, _gw = _build(tmp_path, [Turn(text="1. A table\n2. A chart")])
    orch.bind_llm_alias("f-sonnet")

    _run(orch, "use @sonnet to summarise the rows", resources=[{"kind": "llm_alias", "id": "f-sonnet"}])

    sent = oc.prompts[0]["text"]
    assert "LLM Alias **Claude Sonnet 4.6 (`sonnet`)**" in sent
    assert "The model this app calls" in sent
    # After the request, not before it: the gate wraps the prompt in its own preamble, and a block
    # that landed in the middle would read as part of the instructions rather than as the reference.
    assert sent.index("@sonnet to summarise") < sent.index("LLM Alias")


def test_a_mention_rides_the_user_turn_only(tmp_path: Path):
    """Same rule the attached-file listing follows. A nudge carries no new user reference, and the
    block repeated on one reads as a second request for the same Resource."""
    orch, oc, _gw = _build(tmp_path, [
        Turn(text="1. A table\n2. A chart"),
        Turn(text="Building it.", writes={"src/App.tsx": "// v1\n"}),
        Turn(text="Here's how I'd approach it: first the schema, then the table."),  # nothing -> nudged
        Turn(text="Done.", writes={"src/App.tsx": "// v2\n"}),
    ], verdict="BUILD")
    orch.bind_llm_alias("f-sonnet")
    _get_built(orch)

    _run(orch, "wire @sonnet into the header", resources=[{"kind": "llm_alias", "id": "f-sonnet"}])

    user_turn, nudge = oc.prompts[2]["text"], oc.prompts[3]["text"]
    assert "LLM Alias **Claude Sonnet 4.6 (`sonnet`)**" in user_turn
    assert "LLM Alias" not in nudge
