"""Model assignments — the Project-scoped choice of what each Build mode runs on (ADR-0017).

Two controls sit inches apart and are not the same control. A **Model assignment** is a catalog
slot: it belongs to the Project, it is persisted, and every mode honours it. A **Model override**
is `picked_model`: one viewer's Sage Builder, gone on restart, honoured only in Plan and Implement.

The router is deliberately untouched by all of this — Ask still resolves `ASK_PINNED` without ever
reading `picked_model`, and Auto still follows the phase. So the tests that matter are about what
those two branches now resolve *against*, and they assert through `llm_router.resolve` rather than
against the catalog dataclass: a slot that changed but did not change what a mode runs is the
failure this whole change exists to prevent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator, TurnBusy
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.llm_router import resolve
from sage.router.models import Mode, ModelCatalog, Phase, SessionState

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)", None, ["chat"], {}),
    LlmAlias("id-opus", "opus", "Claude Opus 4.6", None, ["chat"], {}),
]

# The DEPLOYMENT catalog — what a cleared slot has to fall back to.
CATALOG = ModelCatalog(
    sovereign_plan="qwen-2-5", sovereign_implement="qwen-2-5", sovereign_ask="qwen-2-5",
    plan="gpt-5.4", implement="bedrock-qwen3-coder", ask="sonnet",
)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Template rules\n")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES)),
        browser_gateway_base="https://apps.example.com/apps/llm_gateway/v1",
        cost_project_label="my-app",
    )
    orch.project(start_preview=False)
    return orch


def _runs(orch: Orchestrator, mode: Mode, phase: Phase = Phase.PLAN) -> str:
    """What a mode would actually resolve to right now, through the real router."""
    catalog = orch.project().shim.catalog
    return resolve(SessionState(mode, phase), catalog).model


# ---- setting -------------------------------------------------------------------------------------


def test_assigning_the_plan_slot_changes_what_auto_plans_on(tmp_path):
    orch = _orch(tmp_path)
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "gpt-5.4"
    orch.set_catalog(plan="opus")
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "opus"


def test_assigning_the_ask_slot_changes_what_ask_runs_on(tmp_path):
    # The whole point of ADR-0017: Ask has no override, so the assignment is the only door.
    orch = _orch(tmp_path)
    orch.set_catalog(ask="opus")
    assert _runs(orch, Mode.ASK) == "opus"


def test_an_assignment_survives_a_restart(tmp_path):
    _orch(tmp_path).set_catalog(implement="opus")
    assert _runs(_orch(tmp_path), Mode.AUTO, Phase.IMPLEMENT) == "opus"


# ---- clearing (the path that did not exist) --------------------------------------------------------


def test_clearing_a_slot_puts_the_deployment_default_back(tmp_path):
    """`set_catalog` used to drop falsy fields and only ever `update()` the overrides file, so an
    assignment once made could never be taken back. The 'Use the default' row had nothing to call."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan="opus")
    orch.set_catalog(plan=None)
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "gpt-5.4"


def test_a_cleared_slot_stays_cleared_after_a_restart(tmp_path):
    # Reverting has to reach the file, not just the live catalog: otherwise the default comes back
    # for this session and the override returns on the next one.
    orch = _orch(tmp_path)
    orch.set_catalog(plan="opus")
    orch.set_catalog(plan="")
    assert _orch(tmp_path).project().record.read_catalog_overrides() == {}


def test_a_slot_nobody_mentioned_is_not_a_slot_somebody_cleared(tmp_path):
    """The distinction the whole clear path turns on: absent means 'leave it', present-and-empty
    means 'take it back'. A drawer that saves one row must not silently revert the other two."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan="opus", implement="opus")
    orch.set_catalog(plan=None)
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "gpt-5.4"
    assert _runs(orch, Mode.AUTO, Phase.IMPLEMENT) == "opus"


def test_a_slot_the_catalog_does_not_have_is_refused(tmp_path):
    # `replace()` raises TypeError on an unknown field, which the route would have served as a 500.
    # The drawer sends slot names over the wire, so a typo is a client error, not a crash.
    with pytest.raises(ValueError):
        _orch(tmp_path).set_catalog(plann="opus")


# ---- the collision with the override ---------------------------------------------------------------


def test_saving_an_assignment_clears_a_standing_override(tmp_path):
    """Otherwise a person changes the assignment and nothing happens — the worst outcome available.

    Any assignment clears it, not just the one being shadowed: `picked_model` is a single
    mode-independent field, so a narrower clear is not expressible without reshaping it (ADR-0017).
    """
    orch = _orch(tmp_path)
    orch.project().control.pick("sonnet")
    orch.set_catalog(implement="opus")
    assert orch.project().control.snapshot().picked_model is None
    assert _runs(orch, Mode.IMPLEMENT, Phase.IMPLEMENT) == "opus"


def test_a_call_that_assigns_nothing_leaves_the_override_alone(tmp_path):
    # An empty catalog body is a no-op, not a reset. The chip's own clear route is `pick`.
    orch = _orch(tmp_path)
    orch.project().control.pick("sonnet")
    orch.set_catalog()
    assert orch.project().control.snapshot().picked_model == "sonnet"


# ---- the guard -------------------------------------------------------------------------------------


def test_an_assignment_is_refused_while_a_turn_runs(tmp_path):
    """Nothing pins the catalog for the duration of a turn, unlike `arm_turn_mode`. Accepting a
    change mid-build would move the rest of that build onto another model with the first half's
    tool calls in context — the hazard the override chip already closes against."""
    orch = _orch(tmp_path)
    orch._turn_lock.acquire()
    try:
        with pytest.raises(TurnBusy):
            orch.set_catalog(plan="opus")
    finally:
        orch._turn_lock.release()
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "gpt-5.4"


# ---- what the panel is drawn from -------------------------------------------------------------------
#
# `preflight_slots` deliberately answers about the DEPLOYMENT catalog — its own docstring says a
# project's overrides "are reported to them by the model panel, not here". This is that panel, and it
# is a second, project-scoped read rather than a use of `PREFLIGHT_SLOTS`, which is a module global
# computed once at startup over a catalog no project has touched.

STOPPED_ALIASES = ALIASES + [
    LlmAlias("id-local", "local-domino-llm", "Mistral (Domino-hosted)", None, ["chat"], {},
             endpoint_url="https://domino.example.com/models/mistral/v1"),
]
def _orch_with(tmp_path: Path, aliases, endpoints=()) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(aliases), hosted_endpoints=list(endpoints)),
        browser_gateway_base="https://apps.example.com/apps/llm_gateway/v1",
        cost_project_label="my-app",
    )
    orch.project(start_preview=False)
    return orch


def test_the_panel_offers_the_three_slots_a_person_can_assign(tmp_path):
    """Three, not six. The sovereign slots are persisted and preflighted but the router reads none
    of them, and a row that changes nothing is worse than no row (ADR-0017)."""
    panel = _orch(tmp_path).model_assignments()
    assert [row["slot"] for row in panel["slots"]] == ["plan", "implement", "ask"]


def test_a_slot_says_what_it_would_revert_to(tmp_path):
    # The "Use the default (X)" row needs the X, and after an assignment the live model is no
    # longer it — so the default cannot be read off the catalog the panel is showing.
    orch = _orch(tmp_path)
    orch.set_catalog(plan="opus")
    plan = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "plan")
    assert (plan["model"], plan["default"], plan["assigned"]) == ("opus", "gpt-5.4", True)


def test_an_untouched_slot_is_not_reported_as_assigned(tmp_path):
    plan = next(r for r in _orch(tmp_path).model_assignments()["slots"] if r["slot"] == "plan")
    assert (plan["model"], plan["assigned"]) == ("gpt-5.4", False)


def test_an_alias_with_nothing_on_domino_behind_it_is_offered_as_serving(tmp_path):
    # The common case, not the edge one: 12 of 14 aliases on cloud-dogfood are vendor models with
    # no endpoint to be stopped.
    panel = _orch(tmp_path).model_assignments()
    assert {a["name"] for a in panel["aliases"]} == {"sonnet", "qwen-2-5", "opus"}
    assert all(a["serving"] and a["problem"] is None for a in panel["aliases"])


def test_an_alias_whose_endpoint_is_stopped_is_still_offered_but_marked(tmp_path):
    """`/v1/models` filters on permission alone, so a granted Alias whose endpoint is stopped is
    offered anyway (#21). Assigning one is how a build fails opaquely mid-turn, which is the
    failure preventing it at draw time is worth a listing for."""
    from sage.resources.provider import HostedEndpoint
    orch = _orch_with(
        tmp_path, STOPPED_ALIASES,
        [HostedEndpoint("ep-1", "mistral-endpoint", "https://domino.example.com/models/mistral",
                        "Stopped")],
    )
    row = next(a for a in orch.model_assignments()["aliases"] if a["name"] == "local-domino-llm")
    assert row["serving"] is False
    assert "mistral-endpoint" in row["problem"] and "Stopped" in row["problem"]
    # The remedy has to be the one that fits a stopped endpoint, not the generic one.
    assert "Start that endpoint" in row["problem"]


def test_a_gateway_that_will_not_answer_still_leaves_the_assignments_readable(tmp_path):
    """The drawer opens read-only with a reason and a retry. It must not fall back to the
    assigned-models-only list, which is the defect this change exists to fix (ADR-0017)."""
    from sage.resources.provider import ResourceUnavailable
    orch = _orch(tmp_path)

    def _boom():
        raise ResourceUnavailable("The LLM Gateway is not answering.")

    orch._resources.list_llm_aliases = _boom
    panel = orch.model_assignments()
    assert panel["aliases"] == []
    assert "not answering" in panel["error"]
    assert [r["slot"] for r in panel["slots"]] == ["plan", "implement", "ask"]


def test_a_slot_assigned_to_the_model_that_is_already_the_default_still_reads_as_assigned(tmp_path):
    """`assigned` is key presence, not `live != default`. Pinning a slot to the model that happens to
    BE the deployment default writes an override all the same, and calling that "following the
    default" is a lie with a consequence: the day the deployment default moves, this project will not
    follow it, and the panel said it would.

    It had no test because the panel's own fake implemented the honest rule while the server did not
    — the two agreed on every input the fake could produce.
    """
    orch = _orch(tmp_path)
    orch.set_catalog(plan="gpt-5.4")  # CATALOG.plan is already "gpt-5.4"
    plan = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "plan")
    assert plan["assigned"] is True
    assert orch.project().record.read_catalog_overrides() == {"plan": "gpt-5.4"}


def test_a_slot_whose_model_the_gateway_will_not_serve_reports_it_on_the_slot(tmp_path):
    """The save-time re-check (ADR-0017). A greyed menu row says the model is bad; only this says the
    SLOT is, and preflight already owns both sentences — a second set for the same two faults is how
    they come to disagree."""
    from sage.resources.provider import HostedEndpoint
    orch = _orch_with(
        tmp_path, STOPPED_ALIASES,
        [HostedEndpoint("ep-1", "mistral-endpoint", "https://domino.example.com/models/mistral",
                        "Stopped")],
    )
    orch.set_catalog(implement="local-domino-llm")
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "implement")
    assert "mistral-endpoint" in row["problem"] and "Start that endpoint" in row["problem"]
    # A slot nobody broke says nothing at all. `ask` is the clean one here: this fixture's gateway
    # offers `sonnet` but not the `gpt-5.4`/`bedrock-qwen3-coder` the other two slots default to, so
    # asserting on those would be reading the OTHER preflight question's answer.
    assert next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")["problem"] is None


def test_a_slot_naming_an_alias_the_gateway_does_not_offer_reports_it_too(tmp_path):
    # The other half of preflight's question, and the reachable one: assign a model, then have it
    # deregistered from the LLM Gateway. The panel offers only what the listing held, so this cannot
    # be produced from the drawer — but a stale override or an env default reaches it.
    orch = _orch(tmp_path)
    orch.set_catalog(ask="gone-model")
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")
    assert "does not offer" in row["problem"]
