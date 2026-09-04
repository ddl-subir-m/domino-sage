"""A dead Alias stops a build before it starts, not five tool calls in (#125).

The live failure: a build died partway through on the gateway's own words — `Model 'GLM-5.2' not
found` — after the person had read a plan, approved it and watched five tool calls go by. Preflight
(#17) was already built to answer exactly that question and answered it correctly; it answered it
ONCE, at process boot, about the DEPLOYMENT catalog, into a log line nobody building ever reads.

So these tests are about the two things that moved: WHEN the question is asked, and WHICH model it is
asked about. A turn does not necessarily run the deployment slot — a Project can assign its own model
to a slot, and the composer can override one for a turn — and the boot answer is about a moment that
has passed. What is deliberately unchanged is just as load-bearing: a gateway that will not answer
lets the turn run, because "we could not check" is a state and never a refusal.

Nothing reaches the network. The check is a pure function over an alias listing, and the orchestrator
path runs on the injected fake, subclassed to force the gateway failure.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.resources.preflight import turn_refusal, turn_slots
from sage.resources.provider import (
    FakeResourceProvider,
    HostedEndpoint,
    LlmAlias,
    ResourceUnavailable,
)
from dataclasses import replace as _replace

from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

# Two vendor aliases and one Domino-hosted, which is the shape cloud-dogfood has: most aliases have
# nothing on Domino behind them, so only `hosted` can ever be judged on an endpoint.
ALIASES = [
    LlmAlias("id-plan", "plan-model", "Plan Model", None, ["chat"], {}),
    LlmAlias("id-impl", "implement-model", "Implement Model", None, ["chat"], {}),
    LlmAlias("id-hosted", "hosted-model", "Hosted Model", None, ["chat"], {},
             endpoint_url="https://apps.example.tech/endpoints/308f/v1"),
]
STOPPED = HostedEndpoint("308f", "somebody-elses-vllm", "https://apps.example.tech/endpoints/308f",
                         "Stopped")

# Every slot filled by an Alias the fake gateway offers, so a test breaks exactly one and knows that
# is the only reason anything was said.
LIVE = ModelCatalog(sovereign_plan="plan-model", sovereign_implement="plan-model",
                    sovereign_ask="plan-model", plan="plan-model",
                    implement="implement-model", ask="plan-model")


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the scope classifier is the only caller here."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class CountingResources(FakeResourceProvider):
    """The fake, with the one thing a cache test has to be able to see."""

    listings = 0

    def list_llm_aliases(self) -> list[LlmAlias]:
        self.listings += 1
        return list(self.aliases)


class EmptyGateway(FakeResourceProvider):
    """A gateway that ANSWERS and offers nothing. Not a subclass trick: `/v1/models` returning 200
    with no usable `data` reaches `list_llm_aliases` as `[]`, never as an exception."""

    def list_llm_aliases(self) -> list[LlmAlias]:
        return []


class DeadGateway(FakeResourceProvider):
    """The gateway will not answer. The house pattern: subclass the fake to force the failure."""

    def list_llm_aliases(self) -> list[LlmAlias]:
        raise ResourceUnavailable("The LLM Gateway did not answer (ConnectError).")


class BreakingOpenCode(FakeOpenCode):
    """A fake agent whose Nth turn ends with the gateway having failed, planted the way the shim
    plants a real one — on the project, after the prompt has gone out."""

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


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The same two waits test_turn_path strips: a scripted turn can only spend them."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path, *, catalog: ModelCatalog = LIVE, turns: list[Turn] | None = None,
          resources=None, break_on: set[int] | None = None, mode: str = "domino"):
    ws = tmp / "mnt" / "code"
    oc = BreakingOpenCode(ws, turns if turns is not None else [Turn(text="ok")], break_on=break_on)
    orch = Orchestrator(
        workspace_dir=ws, template=_template(tmp), gateway=ScriptedGateway(), catalog=catalog,
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc,
        resources=resources if resources is not None else FakeResourceProvider(list(ALIASES)),
        gateway_mode=mode,
    )
    oc.orch = orch
    orch.project(start_preview=False)   # no method under test starts the dev server
    return orch, oc


def _skip_planning(orch: Orchestrator) -> None:
    """A build turn that goes straight to building, so a test about the gate before it does not
    have to spend a plan turn first."""
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})


def _done(events: list[dict]) -> dict:
    return next(e for e in reversed(events) if e["type"] == "done")


def _error(events: list[dict]) -> str:
    return next(e["message"] for e in events if e["type"] == "error")


PLAN = Turn(text="1. Add the table\n2. Wire up the data")
BUILD = Turn(writes={"src/App.tsx": "// the table\n"})


# ---- which slots a turn routes to ----------------------------------------------------------------


def test_an_auto_turn_routes_to_both_the_slots_it_plans_and_builds_on():
    """The reason one slot is not enough. Auto plans on one and implements on the other, so a check
    that looked only at the first request's model would let the dead one fail the build later —
    which is the shape the failure actually had."""
    assert turn_slots(LIVE, Mode.AUTO) == [("plan", "plan-model", False),
                                           ("implement", "implement-model", False)]


@pytest.mark.parametrize(("mode", "expected"), [
    (Mode.ASK, ("ask", "plan-model")),
    (Mode.PLAN, ("plan", "plan-model")),
    (Mode.IMPLEMENT, ("implement", "implement-model")),
])
def test_a_pinned_mode_routes_to_its_own_slot_and_no_other(mode, expected):
    assert turn_slots(LIVE, mode) == [(*expected, False)]


def test_a_pick_in_the_composer_is_the_model_that_gets_checked():
    """`llm_router` precedence 3 and 4: an explicit pick shadows the slot. Checking the slot would
    check a model this turn is not going to call."""
    assert turn_slots(LIVE, Mode.IMPLEMENT, "picked-model") == [("implement", "picked-model", True)]


def test_auto_ignores_a_pick_exactly_as_the_router_does():
    assert turn_slots(LIVE, Mode.AUTO, "picked-model") == [("plan", "plan-model", False),
                                                           ("implement", "implement-model", False)]


def test_a_signing_slot_is_the_only_slot_a_turn_can_reach():
    """ADR-0032: the pin outranks every other line of precedence, so it decides what to preflight.
    Checking the phase's own slot would do BOTH kinds of damage — miss a dead signing alias, and
    refuse a turn because a slot it will never reach is dead."""
    catalog = _replace(LIVE, implement="gemini-3.7-flash")
    for mode in (Mode.AUTO, Mode.ASK, Mode.PLAN, Mode.IMPLEMENT):
        assert turn_slots(catalog, mode) == [("implement", "gemini-3.7-flash", False)], mode


def test_a_pick_still_shadows_a_signing_slot_the_way_the_router_says():
    """Precedence is veto > in-session act > pin, so a pick in the composer beats the pin — and the
    preflight has to check the picked model, not the pinned one."""
    catalog = _replace(LIVE, implement="gemini-3.7-flash")
    assert turn_slots(catalog, Mode.IMPLEMENT, "picked-model") == [
        ("implement", "picked-model", True)]
    # Auto and Ask never honour a pick, so the pin still stands there.
    assert turn_slots(catalog, Mode.AUTO, "picked-model") == [
        ("implement", "gemini-3.7-flash", False)]


def test_no_signing_slot_leaves_the_preflight_exactly_as_it_was():
    assert turn_slots(LIVE, Mode.AUTO) == [("plan", "plan-model", False),
                                           ("implement", "implement-model", False)]


def test_a_slot_is_handed_on_exactly_as_it_was_configured():
    """A provider-prefixed slot is reduced in `turn_refusal`, not here. Only there is the alias
    listing in hand, and only the listing tells an OpenCode provider prefix from an Alias whose own
    name has a slash in it — reducing here, with nothing to compare against, refused every turn on a
    gateway that offered `domino/gemini-3.7-flash` under that whole name."""
    catalog = ModelCatalog(sovereign_plan="", sovereign_implement="", sovereign_ask="",
                           plan="domino/plan-model", implement="i", ask="a")
    assert turn_slots(catalog, Mode.PLAN) == [("plan", "domino/plan-model", False)]


def test_a_blank_slot_is_not_reported_as_a_missing_alias():
    """The silence `unresolved_slots` keeps, for the same reason: "the Alias '' is missing" is a
    worse sentence than saying nothing."""
    blank = ModelCatalog(sovereign_plan="", sovereign_implement="", sovereign_ask="",
                         plan="", implement="", ask="")
    assert turn_slots(blank, Mode.AUTO) == []


# ---- what the person reads -----------------------------------------------------------------------


def test_the_refusal_names_the_slot_and_the_alias():
    message = turn_refusal("implement", "GLM-5.2", ALIASES, None)
    assert "implement" in message
    assert "GLM-5.2" in message


def test_the_refusal_names_an_action_the_person_can_take_from_the_workbench():
    """The half `SlotProblem.message` cannot supply. Its sentence ends "or register {alias} in the
    LLM Gateway" — a maintainer's action on the gateway's own configuration, which the person about
    to press Build cannot take from where they are standing."""
    message = turn_refusal("implement", "GLM-5.2", ALIASES, None)
    assert "Model assignments" in message
    assert "register" not in message


def test_a_picked_model_sends_the_reader_to_the_menu_they_picked_it_from():
    """Not to Model assignments. An override shadows the slot, so changing the assignment would
    change a setting this turn is not using and the build would fail again."""
    message = turn_refusal("implement", "GLM-5.2", ALIASES, None, picked=True)
    assert "model menu" in message
    assert "Model assignments" not in message


def test_a_slot_whose_endpoint_is_stopped_reads_as_a_stopped_endpoint_not_a_missing_alias():
    """The two faults #21 already tells apart, kept apart here: this Alias IS offered, and telling
    the reader to pick a different model would hide that starting the endpoint fixes it."""
    message = turn_refusal("implement", "hosted-model", ALIASES, [STOPPED])
    assert "somebody-elses-vllm" in message and "Stopped" in message
    # Lower-cased because it lands mid-sentence here, where the boot check starts a new one.
    assert "start that endpoint, or open Model assignments" in message


def test_a_slot_the_gateway_serves_says_nothing():
    assert turn_refusal("plan", "plan-model", ALIASES, None) is None


def test_a_provider_prefixed_slot_resolves_on_its_bare_id_and_says_nothing():
    """The prefix is the provider Sage registers with OpenCode, not part of any Alias name."""
    assert turn_refusal("plan", "sage-gateway/plan-model", ALIASES, None) is None


def test_an_alias_whose_own_name_contains_a_slash_does_not_stop_the_turn():
    """cloud-dogfood offers `domino/gemini-3.7-flash` under that whole name, so the whole name is
    what a turn resolves against. Reducing it first refused turns that were going to work."""
    offered = [LlmAlias("id-gemini", "domino/gemini-3.7-flash", "Gemini 3.7 Flash", None,
                        ["chat"], {})]
    assert turn_refusal("plan", "domino/gemini-3.7-flash", offered, None) is None


# ---- the turn stops before it spends anything ----------------------------------------------------


def test_a_turn_routed_at_an_alias_the_gateway_will_not_serve_never_opens_a_session(tmp_path: Path):
    """#125's first criterion. Before the planner runs and before the first prompt goes out — not
    after a plan has been read, approved and half built."""
    dead = ModelCatalog(sovereign_plan="plan-model", sovereign_implement="plan-model",
                        sovereign_ask="plan-model", plan="GLM-5.2", implement="implement-model",
                        ask="plan-model")
    orch, oc = _orch(tmp_path, catalog=dead, turns=[PLAN])

    events = list(orch.build_stream("build me a consumption dashboard"))

    assert _done(events) == {"type": "done", "ok": False, "decision": "model unavailable"}
    assert "plan" in _error(events) and "GLM-5.2" in _error(events)
    assert oc.prompts == [], "the turn spent a prompt on a model that would 404"
    assert oc.sessions == [], "the turn opened a session it could never use"


def test_either_of_the_two_slots_an_auto_turn_routes_to_stops_it(tmp_path: Path):
    """The implement slot is the one that would have failed LATE — after the plan, the approval and
    the tool calls. It stops the turn just as early as the plan slot does."""
    dead = ModelCatalog(sovereign_plan="plan-model", sovereign_implement="plan-model",
                        sovereign_ask="plan-model", plan="plan-model", implement="GLM-5.2",
                        ask="plan-model")
    orch, oc = _orch(tmp_path, catalog=dead, turns=[PLAN])

    events = list(orch.build_stream("build me a consumption dashboard"))

    assert _done(events)["decision"] == "model unavailable"
    assert "implement" in _error(events)
    assert oc.prompts == []


def test_a_dead_plan_slot_does_not_stop_a_turn_pinned_to_implement(tmp_path: Path):
    """The escalation target, and the way this went. A planning stall can pin the plan-tier model
    for an Implement retry, so that slot is REACHABLE from an Implement turn — but only down a
    rescue path most turns never take. Refusing up front on it would stop builds that were going to
    succeed; the mid-turn gateway-error path owns that one, and it keeps the approved plan."""
    dead_plan = ModelCatalog(sovereign_plan="plan-model", sovereign_implement="plan-model",
                             sovereign_ask="plan-model", plan="GLM-5.2",
                             implement="implement-model", ask="plan-model")
    orch, oc = _orch(tmp_path, catalog=dead_plan, turns=[BUILD])
    _skip_planning(orch)
    orch.project(start_preview=False).control.set_mode(Mode.IMPLEMENT)

    events = list(orch.build_stream("add a sortable table"))

    assert _done(events)["ok"] is True
    assert oc.prompts, "an Implement turn was refused for a slot it was not going to call"


def test_a_slot_whose_endpoint_is_stopped_stops_the_turn_too(tmp_path: Path):
    """`/v1/models` filters on permission alone, so the Alias behind a stopped endpoint is still
    offered (#21). The sovereign case this issue names: a vLLM endpoint in somebody else's Project,
    stopped after the boot check passed."""
    catalog = ModelCatalog(sovereign_plan="plan-model", sovereign_implement="plan-model",
                           sovereign_ask="plan-model", plan="plan-model",
                           implement="hosted-model", ask="plan-model")
    orch, oc = _orch(tmp_path, catalog=catalog, turns=[PLAN],
                     resources=FakeResourceProvider(list(ALIASES), hosted_endpoints=[STOPPED]))

    events = list(orch.build_stream("build me a consumption dashboard"))

    assert _done(events)["decision"] == "model unavailable"
    assert "somebody-elses-vllm" in _error(events)
    assert oc.prompts == []


# ---- it is THIS turn's model, not the deployment's ------------------------------------------------


def test_a_project_that_assigned_a_live_model_over_a_dead_default_still_builds(tmp_path: Path):
    """`preflight_slots` answers about the DEPLOYMENT catalog, before any project is attached. A
    Project that has already moved off a dead default must not be refused on its behalf."""
    dead_default = ModelCatalog(sovereign_plan="plan-model", sovereign_implement="plan-model",
                                sovereign_ask="plan-model", plan="plan-model",
                                implement="GLM-5.2", ask="plan-model")
    orch, oc = _orch(tmp_path, catalog=dead_default, turns=[BUILD])
    _skip_planning(orch)
    orch.set_catalog(implement="implement-model")

    events = list(orch.build_stream("add a sortable table"))

    assert _done(events)["ok"] is True
    assert oc.prompts, "the project's own assignment was checked against the deployment's default"


def test_a_project_that_assigned_a_dead_model_over_a_live_default_is_stopped(tmp_path: Path):
    """And the same fact the other way round: the deployment's slots all resolve, so the boot check
    is clean, and the turn would still 404."""
    orch, oc = _orch(tmp_path, turns=[BUILD])
    _skip_planning(orch)
    orch.set_catalog(implement="GLM-5.2")

    events = list(orch.build_stream("add a sortable table"))

    assert _done(events)["decision"] == "model unavailable"
    assert "GLM-5.2" in _error(events)
    assert oc.prompts == []


def test_a_model_picked_in_the_composer_is_the_one_the_turn_is_checked_against(tmp_path: Path):
    """A pick shadows the slot for this turn, so it is the pick that has to resolve — and the
    remedy names the menu it came from rather than the assignment it is shadowing."""
    orch, oc = _orch(tmp_path, turns=[BUILD])
    _skip_planning(orch)
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.IMPLEMENT)
    project.control.pick("GLM-5.2")

    events = list(orch.build_stream("add a sortable table"))

    assert _done(events)["decision"] == "model unavailable"
    assert "GLM-5.2" in _error(events) and "model menu" in _error(events)
    assert oc.prompts == []


# ---- the approve turn, which spends just as much --------------------------------------------------


def test_approving_a_plan_onto_a_dead_model_refuses_and_keeps_the_plan(tmp_path: Path):
    """An approve IS a build turn. Refused before `set_plan_retry_step` and before the archive, so
    the card, the plan and a phased build's resume point are all exactly as they were: the person
    changes the model and presses Approve again."""
    orch, oc = _orch(tmp_path, turns=[PLAN, BUILD])
    list(orch.build_stream("build me a consumption dashboard"))
    sent = len(oc.prompts)
    orch.set_catalog(implement="GLM-5.2")

    events = list(orch.approve_stream())

    assert _done(events)["decision"] == "model unavailable"
    assert "GLM-5.2" in _error(events)
    assert len(oc.prompts) == sent, "the approve turn sent a prompt to a model that would 404"
    ws = orch.project(start_preview=False).workspace
    assert "Add the table" in (ws.read_plan() or ""), "the plan it never built was archived"


def test_a_plan_kept_by_a_refused_approve_builds_once_the_model_is_changed(tmp_path: Path):
    """The whole point of keeping it. One planning turn paid for, one build."""
    orch, oc = _orch(tmp_path, turns=[PLAN, BUILD])
    list(orch.build_stream("build me a consumption dashboard"))
    orch.set_catalog(implement="GLM-5.2")
    list(orch.approve_stream())

    orch.set_catalog(implement="implement-model")
    events = list(orch.approve_stream())

    assert _done(events)["ok"] is True
    assert "Add the table" in oc.prompts[-1]["text"]


# ---- what must NOT change -------------------------------------------------------------------------


def test_a_gateway_that_will_not_answer_the_check_does_not_block_the_turn(tmp_path: Path):
    """#125's fourth criterion, and the rule `preflight.py` already gets right twice. We did not
    learn that the model is broken; we learned that we could not check. Refusing on that would take
    a whole Workbench down with the listing endpoint."""
    orch, oc = _orch(tmp_path, turns=[BUILD], resources=DeadGateway(list(ALIASES)))
    _skip_planning(orch)

    events = list(orch.build_stream("add a sortable table"))

    assert _done(events)["ok"] is True
    assert oc.prompts, "an unanswered check was read as a broken model"


def test_a_deployment_with_no_llm_gateway_behind_it_checks_nothing(tmp_path: Path):
    """In `openai` mode each model routes to its own vendor and in `fake` mode there is no gateway
    at all, so no listing is evidence about the turn. The same gate `_run_slot_preflight` applies,
    for the same reason: checking anyway would report every slot as missing."""
    vendor = ModelCatalog(sovereign_plan="gpt-4o", sovereign_implement="gpt-4o",
                          sovereign_ask="gpt-4o", plan="gpt-4o", implement="gpt-4o", ask="gpt-4o")
    orch, oc = _orch(tmp_path, catalog=vendor, turns=[BUILD], mode="openai")
    _skip_planning(orch)

    events = list(orch.build_stream("add a sortable table"))

    assert _done(events)["ok"] is True
    assert oc.prompts


def test_a_model_that_dies_mid_turn_still_lands_on_the_gateway_error_path(tmp_path: Path):
    """The fallback stays. A model can die between the check and the call, and that path keeps the
    approved plan and offers "try again" — which is what makes it a fallback rather than a dead end."""
    orch, oc = _orch(tmp_path, turns=[PLAN, BUILD], break_on={2})

    list(orch.build_stream("build me a consumption dashboard"))
    events = list(orch.approve_stream())

    assert _done(events)["decision"] == "gateway error"
    assert "still here" in _error(events) and "try again" in _error(events)
    ws = orch.project(start_preview=False).workspace
    assert "Add the table" in (ws.read_plan() or "")
    assert ws.read_plan_retry_step() == 1
    assert oc.prompts, "the mid-turn path was reached without a prompt going out"


def test_the_boot_check_still_answers_about_the_deployment_catalog(tmp_path: Path):
    """Added, not replaced. The boot check exists for a maintainer reading a log before any project
    is attached, and a Project assigning its own model must not silence it."""
    dead_default = ModelCatalog(sovereign_plan="plan-model", sovereign_implement="plan-model",
                                sovereign_ask="plan-model", plan="plan-model",
                                implement="GLM-5.2", ask="plan-model")
    orch, _oc = _orch(tmp_path, catalog=dead_default)
    orch.set_catalog(implement="implement-model")

    result = orch.preflight_slots()

    assert result["state"] == "problems"
    assert [s["slot"] for s in result["slots"]] == ["implement"]
    assert "GLM-5.2" in result["slots"][0]["alias"]


# ---- the cost of asking ---------------------------------------------------------------------------


def test_a_run_of_turns_pays_for_one_listing(tmp_path: Path):
    """A stale answer is fine; a per-turn round trip is not. The window is the module's
    `_TURN_SLOT_TTL_S`, and what it is not protecting is the remedy the refusal names — that changes
    the catalog, which is read live on every turn and never cached."""
    resources = CountingResources(list(ALIASES))
    orch, _oc = _orch(tmp_path, turns=[BUILD, BUILD], resources=resources)
    _skip_planning(orch)

    list(orch.build_stream("add a sortable table"))
    before = resources.listings
    list(orch.build_stream("make it sortable by date"))

    assert before == 1
    assert resources.listings == 1, "the second turn paid for a listing that could not have changed"


def test_a_reassigned_slot_is_believed_at_once_even_though_the_listing_is_cached(tmp_path: Path):
    """The stale answer must never outlive the fix. The cache holds what the GATEWAY offers; the
    catalog is not in it, so a person who changes the model builds on the next turn."""
    orch, oc = _orch(tmp_path, turns=[BUILD])
    _skip_planning(orch)
    orch.set_catalog(implement="GLM-5.2")
    orch.project(start_preview=False).control.set_mode(Mode.IMPLEMENT)
    assert _done(list(orch.build_stream("add a table")))["decision"] == "model unavailable"

    orch.set_catalog(implement="implement-model")
    events = list(orch.build_stream("add a table"))

    assert _done(events)["ok"] is True
    assert oc.prompts


def test_a_gateway_that_answers_and_offers_nothing_does_not_block_the_turn(tmp_path: Path):
    """The same rule as an unreachable gateway, because it is the same amount of knowledge. A
    listing that arrived empty — a permission-cache blip, a token that momentarily resolves to no
    grants — would otherwise make EVERY slot missing and refuse every turn for the whole window,
    with a remedy that cannot work: there is no model left to pick."""
    orch, oc = _orch(tmp_path, turns=[BUILD], resources=EmptyGateway([]))
    _skip_planning(orch)

    events = list(orch.build_stream("add a sortable table"))

    assert _done(events)["ok"] is True
    assert oc.prompts, "an empty listing was read as every model being missing"


# ---- and it is still there after a reload ---------------------------------------------------------


def _history(orch: Orchestrator) -> list[dict]:
    project = orch.project(start_preview=False)
    return project.workspace.read_history(project.build_conversation)


def test_a_refused_build_keeps_the_question_and_the_answer_in_the_transcript(tmp_path: Path):
    """The composer draws the person's prompt optimistically. A refusal the server never recorded
    leaves a reload showing neither what was asked nor why it did not run — the same reason
    `_ask_mode_refusal` and the two offers write theirs."""
    orch, _oc = _orch(tmp_path, turns=[BUILD])
    _skip_planning(orch)
    orch.set_catalog(implement="GLM-5.2")
    orch.project(start_preview=False).control.set_mode(Mode.IMPLEMENT)

    list(orch.build_stream("add a sortable table"))

    history = _history(orch)
    assert [e["type"] for e in history] == ["user", "error", "done"]
    assert history[0]["text"] == "add a sortable table"
    assert "GLM-5.2" in history[1]["message"]
    assert history[2]["decision"] == "model unavailable"


def test_a_refused_approve_records_no_bubble_for_an_approval_that_did_not_happen(tmp_path: Path):
    """The refusal is recorded; the "Approved the plan." above it is not. Nothing was approved, and
    the card is still asking to be — a transcript saying otherwise contradicts the card under it."""
    orch, _oc = _orch(tmp_path, turns=[PLAN, BUILD])
    list(orch.build_stream("build me a consumption dashboard"))
    orch.set_catalog(implement="GLM-5.2")

    list(orch.approve_stream())

    tail = _history(orch)[-2:]
    assert [e["type"] for e in tail] == ["error", "done"]
    assert not any(e.get("text") == "Approved the plan." for e in _history(orch))


# ---- the button the refusal points at ------------------------------------------------------------

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
def test_a_refused_approve_leaves_the_plan_card_still_approvable():
    """#125's second criterion is only met if the action it names can be taken. Every `done` whose
    decision is not a gate closes the plan card, so without this the person reads "pick a different
    model, then approve again" and has no Approve button left to press."""
    drawn = _drawn([
        {"type": "user", "text": "build me a consumption dashboard"},
        {"type": "plan-proposed", "plan": "1. Add the table", "planId": "pd_1", "steps": 2},
        {"type": "error", "message": "This turn would run on Sage's implement model, the LLM "
                                     "Alias GLM-5.2, which this LLM Gateway does not offer."},
        {"type": "done", "ok": False, "decision": "model unavailable"},
    ])

    assert drawn["plans"] == [{"pending": True, "cancelled": False}]
    assert any("GLM-5.2" in v for v in drawn["values"])
    # And not doubled: the sentence above already says what stopped it.
    assert not any("Stopped — model unavailable" in v for v in drawn["values"])


@needs_node
def test_a_build_that_really_stopped_still_closes_its_plan_card():
    """The property the new gate decision must not have widened."""
    drawn = _drawn([
        {"type": "user", "text": "build me a consumption dashboard"},
        {"type": "plan-proposed", "plan": "1. Add the table", "planId": "pd_1", "steps": 2},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ])

    assert drawn["plans"] == [{"pending": False, "cancelled": False}]
