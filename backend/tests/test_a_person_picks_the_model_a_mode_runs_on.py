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

import logging
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


def test_assigning_a_signing_slot_takes_the_whole_session_with_it(tmp_path):
    """ADR-0032: a signing model cannot share a harness session with one that does not sign, so one
    assignment pins every mode and every phase. This is Auto's phase switch being spent on purpose,
    not a routing bug."""
    orch = _orch(tmp_path)
    orch.set_catalog(implement="gemini-3.7-flash")
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "gemini-3.7-flash"
    assert _runs(orch, Mode.ASK) == "gemini-3.7-flash"


def test_the_status_names_the_slot_that_pinned_the_session(tmp_path):
    """The picker restates the router's precedence in JS and cannot see the pin, so the slot name is
    sent rather than left to be recomputed — otherwise the panel shows a model the turn will not
    run on (ADR-0032)."""
    orch = _orch(tmp_path)
    assert orch.project().status()["model"]["signing_slot"] is None
    orch.set_catalog(implement="gemini-3.7-flash")
    assert orch.project().status()["model"]["signing_slot"] == "implement"


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
    # `None`, not the model it is following: the field answers what the file assigns, and a slot
    # following the default has no assignment to report.
    assert plan["assigned_model"] is None


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
    assert "Stopped" in row["problem"]
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
    # The model the FILE names, beside the boolean drawn from it. The panel cannot recover this by
    # comparing `model` with `default`, because on this row they are equal and `model` is read from
    # the shim catalog rather than from the file — so the one state where those two are equal for a
    # reason other than a pin would be indistinguishable from this one (#299).
    assert plan["assigned_model"] == "gpt-5.4"
    # `{model, effort}` since ADR-0049: an assignment is a pair, and a slot with no effort carries
    # the same `None` a slot that never had one does.
    assert orch.project().record.read_catalog_overrides() == {
        "plan": {"model": "gpt-5.4", "effort": None}}


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
    assert "Stopped" in row["problem"] and "Start that endpoint" in row["problem"]
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
    assert "isn't available" in row["problem"]


# ---- the pin the panel could not see (#276) ---------------------------------------------------------


SIGNING = "gemini-3.7-flash"


def _problems(orch: Orchestrator) -> dict[str, str | None]:
    return {r["slot"]: r["problem"] for r in orch.model_assignments()["slots"]}


def test_a_slot_the_signing_pin_has_shadowed_says_so(tmp_path):
    """The drawer is where the choice gets made and it knew least about it: a slot the pin had taken
    out of play drew exactly like a live one — same row, same text, `problem: null`. The sentence
    names the cause and the cure, because neither is the row it lands on."""
    orch = _orch(tmp_path)
    orch.set_catalog(implement=SIGNING, plan="opus")
    plan = _problems(orch)["plan"]
    assert f"The implement model ({SIGNING})" in plan
    assert "this model won't run" in plan
    assert "Change the implement model" in plan


def test_the_slot_holding_the_signing_model_is_left_alone(tmp_path):
    """It is running exactly what it says. A row telling the reader to change the model they are
    looking at, on the one row where that model is the one running, would send them backwards."""
    orch = _orch(tmp_path)
    orch.set_catalog(implement=SIGNING, plan="opus")
    assert "won't run" not in (_problems(orch)["implement"] or "")


def test_the_ask_row_says_chat_still_runs_on_it(tmp_path):
    """`ask` is one row with two consumers and the pin reaches only one: `llm_router.resolve` sends a
    Chat turn down `_resolve_chat` before the pin is applied. "This model won't run" is true of the
    other two slots and false here, and the row is labelled "Ask and Chat" for that very reason."""
    orch = _orch(tmp_path)
    orch.set_catalog(implement=SIGNING)
    ask = _problems(orch)["ask"]
    assert "only runs in Chat" in ask
    assert "won't run" not in ask


def test_two_slots_holding_one_signing_model_shadow_neither(tmp_path):
    """The claim is about the model, not about which slot's assignment carries it there. Assign the
    signing model to Plan as well and Plan runs exactly the model its row shows — saying otherwise
    would be the same false row this fixes, pointed the other way."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan=SIGNING, implement=SIGNING)
    assert "won't run" not in (_problems(orch)["plan"] or "")


def test_the_shadow_outranks_a_verdict_about_a_model_no_turn_will_reach(tmp_path):
    """Plan's default (`gpt-5.4`) is not on this fixture's gateway, so the row carried "isn't
    available" — a sentence whose subject is turns that use this model, of which there are now none,
    and whose remedy sends the reader to the wrong slot. Release the pin and it comes back."""
    orch = _orch(tmp_path)
    assert "isn't available" in _problems(orch)["plan"]
    orch.set_catalog(implement=SIGNING)
    assert "isn't available" not in _problems(orch)["plan"]
    orch.set_catalog(implement=None)
    assert "isn't available" in _problems(orch)["plan"]


def test_a_gateway_that_will_not_answer_still_reports_the_shadow(tmp_path):
    """The only slot verdict that is a property of the catalog rather than of the gateway, so it is
    settled before one is asked. Hiding it here would drop the pin's explanation on exactly the read
    that already has the least to say."""
    from sage.resources.provider import ResourceUnavailable
    orch = _orch(tmp_path)
    orch.set_catalog(implement=SIGNING)

    def _boom():
        raise ResourceUnavailable("The LLM Gateway is not answering.")

    orch._resources.list_llm_aliases = _boom
    panel = orch.model_assignments()
    assert panel["aliases"] == []
    assert "Change the implement model" in _problems(orch)["plan"]


def test_the_row_says_which_kind_of_verdict_it_is_carrying(tmp_path):
    """The panel has to tell them apart and must not do it by reading the sentence — a sentence is
    what a brand pack is allowed to change. The sensitivity lock outranks the pin
    (`llm_router._lock_sensitivity` wraps `_pin_signing`), so on a row the lock has moved the shadow
    is false while the other two verdicts stay true, and this flag is what lets the drawer drop the
    one without dropping the others."""
    orch = _orch(tmp_path)
    orch.set_catalog(implement=SIGNING)
    flags = {r["slot"]: r["shadowed"] for r in orch.model_assignments()["slots"]}
    assert flags == {"plan": True, "implement": False, "ask": True}
    # The other two verdicts are on rows that report `shadowed: False`, so the drawer's gate cannot
    # reach them. `plan` here carries "isn't available" and nothing else.
    orch.set_catalog(implement=None)
    assert all(r["shadowed"] is False for r in orch.model_assignments()["slots"])
    assert "isn't available" in _problems(orch)["plan"]


def test_no_signing_assignment_leaves_every_row_as_it_was(tmp_path):
    orch = _orch(tmp_path)
    orch.set_catalog(implement="opus")
    assert all("won't run" not in (p or "") for p in _problems(orch).values())


# ---- a row the file could not hand over (#289) ---------------------------------------------


def _seed(orch: Orchestrator, text: str) -> None:
    """Write `model_overrides.json` by hand, which is the only way to get a malformed row into it.

    Not through `set_catalog`, and not only because it refuses these shapes: it reads the whole file
    and writes the whole file back, so saving ANY slot drops every unreadable row along with it. A
    test that reached for the route would be asserting about a file it had just repaired."""
    path = orch.project().record.catalog_overrides_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_an_unreadable_row_says_so_where_an_unassigned_slot_stays_silent(tmp_path):
    """One row through both states, asserting opposite answers, because that is the whole fault:
    #281 made a dropped row draw EXACTLY like a slot nobody ever assigned, and two tests each
    asserting one of these would both pass with the sentence never drawn at all.

    `ask` is the slot this is driven through — its default is the one this fixture's gateway serves,
    so no preflight verdict can reach the row and the answers below are about the file alone."""
    orch = _orch(tmp_path)
    assert _problems(orch)["ask"] is None
    _seed(orch, '{"ask": true}')
    assert "couldn't be read" in _problems(orch)["ask"]
    # Names the slot, so a panel of three rows says which file row to go and look at.
    assert "ask row" in _problems(orch)["ask"]
    _seed(orch, "{}")
    assert _problems(orch)["ask"] is None


def test_the_row_that_could_not_be_read_still_follows_the_default(tmp_path):
    """The witness, not a refusal. #281 measured the alternative — a read that raises is a durable
    brick in a file that is committed and shared — and the drop is the house line; this ticket adds
    the sentence and changes nothing about what the slot resolves to.

    Booted a second time over the same volume so the catalog is BUILT from the malformed file,
    rather than inherited from a process that read it before the bad row was written."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": true}')
    orch = _orch(tmp_path)
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")
    assert "couldn't be read" in row["problem"]
    assert row["model"] == CATALOG.ask
    # Still reads as unassigned, because it IS: the sentence is the only thing that changes. A row
    # that reported `assigned: True` would put the person's lost model back in the closed Select
    # and the panel would be claiming an assignment the catalog does not hold.
    assert row["assigned"] is False
    assert _runs(orch, Mode.ASK) == CATALOG.ask


def test_the_unreadable_row_outranks_a_verdict_about_a_model_nobody_chose(tmp_path):
    """`plan`'s default is not on this fixture's gateway, so that row already carried "isn't
    available" — a sentence that stays true and whose remedy is for somebody else's slot. On a row
    that could not be read, the model those two verdicts name is the DEPLOYMENT DEFAULT, which the
    reader never picked; sending them to pick a different one answers a question they did not ask."""
    orch = _orch(tmp_path)
    assert "isn't available" in _problems(orch)["plan"]
    _seed(orch, '{"plan": true}')
    assert "couldn't be read" in _problems(orch)["plan"]
    assert "isn't available" not in _problems(orch)["plan"]
    _seed(orch, "{}")
    assert "isn't available" in _problems(orch)["plan"]


def test_the_shadow_outranks_the_unreadable_row(tmp_path):
    """Both sentences are true here, so the rule the other clash turns on — false in its SUBJECT —
    is silent, and what settles it instead is which remedy the reader can reach. While the pin
    holds, fixing the file changes nothing observable on this row; releasing it brings the row's own
    sentence back on the next read, which is where the shadow's own precedence already ends.

    Re-booted after each write for the reason the shadow needs: `shadowed_slots` reads the catalog
    the shim holds, and that is rebuilt at boot and on save, not when a file changes under it."""
    orch = _orch(tmp_path)
    _seed(orch, '{"implement": "' + SIGNING + '", "ask": true}')
    orch = _orch(tmp_path)
    assert "only runs in Chat" in _problems(orch)["ask"]
    assert "couldn't be read" not in _problems(orch)["ask"]
    _seed(orch, '{"ask": true}')
    orch = _orch(tmp_path)
    assert "couldn't be read" in _problems(orch)["ask"]


def test_the_log_warning_stays_beside_the_sentence(tmp_path, caplog):
    """Two audiences, not one moved. The sentence reaches somebody who will never open a log; the
    log outlives the Builder and is the only record once the drawer is closed."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": true}')
    with caplog.at_level(logging.WARNING, logger="sage.workspace.manager"):
        assert "couldn't be read" in _problems(orch)["ask"]
    assert [r for r in caplog.records if "ask" in r.getMessage()]


def test_saving_any_slot_takes_the_unreadable_row_with_it(tmp_path):
    """When the sentence stops. `set_catalog` reads the whole file and writes the whole dict back,
    so the bad row is gone after the next save of ANY slot — not just of the slot it was on. That
    is the pre-existing rewrite this ticket does not change, and it is why the sentence offers
    "remove it" as an exit: it is the one the product takes by itself.

    Pinned because the alternative is a sentence that outlives the row it is about. What the panel
    says afterwards is whatever is true of the slot then — here `plan`'s default is not on this
    gateway, so preflight's verdict is what comes back."""
    orch = _orch(tmp_path)
    _seed(orch, '{"plan": true}')
    assert "couldn't be read" in _problems(orch)["plan"]
    orch.set_catalog(ask="opus")
    assert orch.project().record.catalog_overrides_path.read_text() == (
        '{"ask": {"model": "opus", "effort": null}}')
    assert "couldn't be read" not in _problems(orch)["plan"]
    assert "isn't available" in _problems(orch)["plan"]


def test_a_row_corrupted_under_an_open_builder_does_not_claim_the_default(tmp_path):
    """The sentence's second clause and the model beside it come from different reads. The clause is
    a FRESH read of the file; `row["model"]` is the shim's catalog, which is rebuilt at boot and on
    save and not when the file moves under it. A bad merge arriving in an open Builder sits in that
    gap — and it is this ticket's own scenario, since the whole reason the row is worth a sentence is
    that the file is committed and shared.

    So the clause is drawn only where the slot really is on the default. Every test above this one
    re-boots between the seed and the read, which is exactly how a sentence that lies here passes."""
    orch = _orch(tmp_path)
    orch.set_catalog(ask="opus")
    _seed(orch, '{"ask": true}')
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")
    assert "couldn't be read" in row["problem"]
    assert "following the default" not in row["problem"]
    # And it NAMES what is still running. Declining to claim the default is not enough on its own:
    # a dropped row is absent from the file, so `assigned` is False and the Select closes on
    # "Use default (sonnet)" — this sentence is the row's only line, so it has to answer that.
    assert "still runs opus" in row["problem"]
    assert row["model"] == "opus" != CATALOG.ask
    # Rebuilt, and now it really is on the default — so the clause comes back rather than being
    # dropped from the sentence for good.
    row = next(r for r in _orch(tmp_path).model_assignments()["slots"] if r["slot"] == "ask")
    assert "following the default" in row["problem"]


def test_a_row_whose_key_is_misspelled_is_also_a_row_that_could_not_be_read(tmp_path):
    """`{"ask": {"modell": "opus"}}` is a dict, so it passed the shape check and then contributed
    nothing, in silence — the same invisible drop this ticket exists to end, reached by the likelier
    hand-edit. A dict carrying neither half says nothing, and saying nothing is what makes it
    unreadable; `{}` is the same row with the typo removed."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": {"modell": "opus"}}')
    assert "couldn't be read" in _problems(orch)["ask"]
    _seed(orch, '{"ask": {}}')
    assert "couldn't be read" in _problems(orch)["ask"]


def test_an_effort_with_no_model_is_a_row_that_was_read_perfectly_well(tmp_path):
    """The one model-less dict that means something: ADR-0049's effort alone, with the model still
    following the deployment default. The line the test above must not cross — a witness that fired
    here would be calling a legal assignment malformed."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": {"effort": "high"}}')
    assert _problems(orch)["ask"] is None
    assert orch.project().record.read_catalog_overrides() == {
        "ask": {"model": None, "effort": "high"}}


def test_a_model_that_is_not_text_is_a_witness_and_not_a_brick(tmp_path):
    """`{"model": 123}` reaches `slot_alias`, which calls `.rsplit` on it — so before this the
    AttributeError came out of EVERY resolve of the project's catalog and the panel 500'd. That is
    the durable brick #281's drop exists to prevent, reached by the one malformed shape that got
    past the check by being a dict.

    `set_catalog` already refuses this on the route ("must be text"), which is the point: the
    committed file is a second door, and the route's 400 never stood in it."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": {"model": 123}}')
    orch = _orch(tmp_path)
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")
    assert "couldn't be read" in row["problem"]
    assert row["model"] == CATALOG.ask


def test_both_spellings_of_an_empty_row_get_the_same_answer(tmp_path):
    """`""` and `{"model": ""}` are one row written two ways — `set_catalog` treats an empty model
    as clearing the slot, so as a STORED row neither names anything. Driven through both spellings
    in one test because the failure this guards is that they disagree: the guard went on the dict
    branch first and left its neighbour reading the legacy shape unchecked, so the same fault drew a
    sentence under one spelling and vanished under the other."""
    orch = _orch(tmp_path)
    for row in ('{"ask": ""}', '{"ask": {"model": ""}}'):
        _seed(orch, row)
        assert "couldn't be read" in _problems(orch)["ask"], row
        assert orch.project().record.read_catalog_overrides() == {}, row


def test_a_stale_effort_also_stops_the_row_claiming_the_default(tmp_path):
    """Both halves of the assignment decide it, not the model alone. An effort is a full half under
    ADR-0049 and a row carrying one with no model sets `<slot>_effort` while the MODEL follows the
    default — so a row that breaks from that state leaves the models equal and the level stale, and
    a check on the model alone would claim "following the default" with a non-default level drawn in
    the effort control an inch away."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": {"effort": "high"}}')
    orch = _orch(tmp_path)
    assert _problems(orch)["ask"] is None
    _seed(orch, '{"ask": {}}')
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")
    assert "couldn't be read" in row["problem"]
    # The models agree, which is exactly what would have let the false claim through.
    assert row["model"] == row["default"] == CATALOG.ask
    assert row["effort"] == "high" != row["default_effort"]
    assert "following the default" not in row["problem"]


def test_a_file_that_will_not_parse_is_a_witness_on_every_row(tmp_path):
    """A real `git merge` conflict writes `<<<<<<<` markers into this committed file, and the raise
    came out of every resolve of the project's catalog — the panel 500s and nobody gets a witness at
    all, which is #289's own outcome reached through the file's commonest fault.

    Every assignable row, because when the file will not parse there is no way to know which rows it
    held, and each of them really could not be read. Three sentences naming one file point at the one
    place to go and look."""
    orch = _orch(tmp_path)
    _seed(orch, '<<<<<<< HEAD\n{"ask": "opus"}\n=======\n{"ask": "sonnet"}\n>>>>>>> main\n')
    problems = _problems(orch)
    assert all("couldn't be read" in (problems[s] or "") for s in ("plan", "implement", "ask")), problems
    # Every slot still resolves, which is the half that was a brick.
    assert _runs(orch, Mode.ASK) == CATALOG.ask


def test_a_file_holding_something_other_than_an_object_is_the_same_answer(tmp_path):
    """`[]` and `"x"` parse perfectly well and then die on `.items()`, so the `isinstance` guard sits
    beside the `except` rather than inside it — a parse that succeeds is not a file that can be read
    as assignments."""
    orch = _orch(tmp_path)
    for raw in ("[]", '"x"', "null", "3"):
        _seed(orch, raw)
        assert "couldn't be read" in (_problems(orch)["ask"] or ""), raw


def test_a_save_over_an_unparseable_file_is_refused_not_written(tmp_path):
    """The file-level guard's own consequence, closed rather than documented. Every save here reads
    the whole dict and writes the whole dict back — harmless when the rows it dropped were ones
    nothing could honour, but on a file that would not PARSE the read returns `{}` and writing that
    back replaces every assignment the file held.

    The sovereign slots are why this is a refusal and not a warning: no panel draws them, so a
    colleague's committed `sovereign_plan` would go without a word. The panel is already saying the
    file cannot be read, so the refusal is that same sentence arriving where it bites."""
    orch = _orch(tmp_path)
    conflict = '<<<<<<< HEAD\n{"ask": "opus"}\n=======\n{"ask": "sonnet"}\n>>>>>>> main\n'
    _seed(orch, conflict)
    with pytest.raises(ValueError, match="couldn't be read"):
        orch.set_catalog(plan="opus")
    # Untouched, which is the whole point — git still holds both sides of the conflict.
    assert orch.project().record.catalog_overrides_path.read_text() == conflict


def test_a_save_over_one_unreadable_row_still_goes_through(tmp_path):
    """The line the refusal must not cross. A single bad row leaves the rest of the file parseable,
    so the read still returns every other assignment and writing it back loses only the row nothing
    could honour — which is the behaviour the sentence's "or remove it" describes. Refusing here
    would strand a person behind one typo they may not be able to reach."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": true, "implement": "opus"}')
    orch.set_catalog(plan="opus")
    assert orch.project().record.read_catalog_overrides() == {
        "implement": {"model": "opus", "effort": None},
        "plan": {"model": "opus", "effort": None}}
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "opus"


def test_a_garbage_model_beside_a_usable_effort_is_still_a_witness(tmp_path):
    """The half-row hole. `{"model": 123, "effort": "high"}` used to pass because SOMETHING in it was
    usable, so the garbage half was dropped in silence — the same invisible drop, on the shape a bad
    merge most easily produces. The reader's rule is now `set_catalog`'s own rule for the same value:
    a half is text or it is absent.

    A key that is not `model` or `effort` goes the same way, and for the same evidence: the route
    refuses to write one, so it is a hand-edit every time."""
    orch = _orch(tmp_path)
    for row in ('{"ask": {"model": 123, "effort": "high"}}',
                '{"ask": {"model": [], "effort": "high"}}',
                '{"ask": {"modell": "opus", "effort": "high"}}'):
        _seed(orch, row)
        assert "couldn't be read" in (_problems(orch)["ask"] or ""), row


def test_a_null_model_beside_an_effort_is_the_products_own_output(tmp_path):
    """The line the test above must not cross, and it is a narrow one. Saving an effort alone writes
    literally `{"model": null, "effort": "high"}` — key PRESENT, value null — so a rule that tested
    mere presence would call the product's own output malformed. Present-and-null is legal;
    present-and-garbage is not."""
    orch = _orch(tmp_path)
    for row in ('{"ask": {"model": null, "effort": "high"}}',
                # The same assignment one step on. The route normalises `""` to null, so these are
                # one row written two ways and a reader that split them would answer the same file
                # two different things. It is the TYPE that is checked, never the emptiness.
                '{"ask": {"model": "", "effort": "high"}}'):
        _seed(orch, row)
        assert _problems(orch)["ask"] is None, row
        assert orch.project().record.read_catalog_overrides() == {
            "ask": {"model": None, "effort": "high"}}, row


def test_a_file_whose_bytes_are_not_text_is_a_witness_too(tmp_path):
    """`read_text()` raises `UnicodeDecodeError` on invalid UTF-8, and that is a `ValueError` rather
    than the `JSONDecodeError` the guard first caught — so the brick stood for exactly the merge that
    goes binary, which is the one a person is least able to read for themselves."""
    orch = _orch(tmp_path)
    path = orch.project().record.catalog_overrides_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"ask": "\xff\xfe opus"}')
    assert "couldn't be read" in (_problems(orch)["ask"] or "")


def test_a_garbage_half_is_reported_without_costing_the_usable_half(tmp_path):
    """Reporting the fault and honouring the row are two decisions. `set_catalog` collapses them —
    it refuses the whole assignment for this value — and is right to, because its refusal writes
    nothing and costs a retry. Collapsing them HERE costs the assignment: the slot reverts to the
    deployment default, and on a sovereign slot, which no panel draws, it does so in total silence.

    So the bad half is nulled, the good half stands, and the row still draws its sentence. That is
    not the silent half-drop this ticket closed earlier — the witness is exactly what makes keeping
    the good half safe.

    Re-booted, because the shim's catalog is what carries the honoured half and it is rebuilt at
    boot. Without that this asserts the default and passes whatever the reader decided."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": {"model": "opus", "effort": 7}}')
    orch = _orch(tmp_path)
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")
    assert "couldn't be read" in row["problem"]
    assert row["model"] == "opus" != CATALOG.ask
    assert row["effort"] is None
    # The mirror image: a garbage MODEL beside a usable effort keeps the effort.
    _seed(orch, '{"ask": {"model": 123, "effort": "high"}}')
    orch = _orch(tmp_path)
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")
    assert "couldn't be read" in row["problem"]
    assert (row["model"], row["effort"]) == (CATALOG.ask, "high")
    # A level no model accepts is not a garbage level: nothing re-validates one on read,
    # `_effective_catalog` says so, and #282 owns it. One refused turn, not a durable brick.
    _seed(orch, '{"ask": {"model": "opus", "effort": "ludicrous"}}')
    assert _problems(orch)["ask"] is None


def test_a_malformed_sovereign_row_keeps_the_model_it_can_still_read(tmp_path):
    """Why the two decisions had to be split, in the place it actually mattered. A sovereign row has
    no panel row, so `unreadable` reaches nobody — which means dropping it whole reverted a working
    assignment to the deployment default with the only trace a once-per-process log line.

    Now the readable half survives the unreadable one, and the slot still runs what the person
    committed. It is still silent, because no surface draws these slots; it is no longer lossy."""
    orch = _orch(tmp_path)
    _seed(orch, '{"sovereign_plan": {"model": "opus", "efort": "high"}}')
    orch = _orch(tmp_path)
    assert orch.project().shim.catalog.sovereign_plan == "opus"


def test_a_malformed_sovereign_row_is_silent_but_no_longer_a_brick(tmp_path):
    """The recorded gap, pinned at the boundary it actually sits on. `sovereign_plan` gets no
    sentence because the panel draws no row for it — closing that needs a surface, not a fourth
    sentence — but it no longer takes the Project down on the way, which is the half that was
    reachable from here."""
    orch = _orch(tmp_path)
    _seed(orch, '{"sovereign_plan": {"model": 123}}')
    orch = _orch(tmp_path)
    assert orch.project().shim.catalog.sovereign_plan == CATALOG.sovereign_plan
    assert all(r["problem"] is None or "couldn't be read" not in r["problem"]
               for r in orch.model_assignments()["slots"])


def test_a_row_literally_named_empty_string_is_one_bad_row_not_a_bad_file(tmp_path):
    """JSON lets an object key be `""`, and the population of this file is hand-edits and bad
    merges, so it is reachable. It was briefly indistinguishable from "the whole file is broken",
    because that fault travelled as a sentinel INSIDE the set of bad slot names — which locked every
    save and drew no sentence on any row, a worse silence than the one this ticket started on.

    The two faults are separate fields now, so this asserts the thing a sentinel cannot give:
    the file still parses, the real rows are still honoured, and saving still works."""
    orch = _orch(tmp_path)
    _seed(orch, '{"plan": "opus", "": {}}')
    orch = _orch(tmp_path)
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "opus"
    assert next(r for r in orch.model_assignments()["slots"] if r["slot"] == "plan")["assigned"]
    # The save path is open, which the sentinel had closed for every slot.
    orch.set_catalog(ask="opus")
    assert _runs(orch, Mode.ASK) == "opus"


def test_a_poll_landing_mid_write_does_not_see_a_broken_file(tmp_path):
    """The read no longer raises on a file it cannot parse, so a half-written one is now plausible
    rather than loud — it would tell the reader their file is broken and refuse their next save, for
    a file that is fine a millisecond later. The panel polls this path without the turn lock, so the
    window is real and the write is atomic to close it.

    Asserted on the mechanism rather than by racing a thread: nothing but the finished file is ever
    at that path, so no reader can observe a partial one."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan="opus")
    path = orch.project().record.catalog_overrides_path
    assert list(path.parent.glob("*.tmp")) == []
    assert path.read_text() == '{"plan": {"model": "opus", "effort": null}}'


def test_a_file_that_is_not_there_is_not_a_file_that_could_not_be_read(tmp_path):
    """`FileNotFoundError` IS an `OSError`, so the whole-file catch swallowed it and reported a
    Project that has simply assigned nothing as one whose file is broken — a witness on every row
    and every save refused, for the ordinary empty case.

    Reachable as a RACE, not only as the empty case, which is why the question is asked by catching
    rather than by `exists()` first: a `git checkout` to a branch without this file, landing between
    the check and the read, put a perfectly fine Project in the unreadable branch. A witness that
    the file being fine can provoke is not worth having."""
    orch = _orch(tmp_path)
    path = orch.project().record.catalog_overrides_path
    assert not path.exists()
    assert all(r["problem"] is None or "couldn't be read" not in r["problem"]
               for r in orch.model_assignments()["slots"])
    orch.set_catalog(ask="opus")
    assert _runs(orch, Mode.ASK) == "opus"
    # And the same answer once it has existed and gone again, which is the shape of the race.
    path.unlink()
    assert all(r["problem"] is None or "couldn't be read" not in r["problem"]
               for r in orch.model_assignments()["slots"])
    orch.set_catalog(plan="opus")
    assert _runs(orch, Mode.AUTO, Phase.PLAN) == "opus"


def test_a_save_drops_a_malformed_sovereign_row_with_nothing_said(tmp_path):
    """The sovereign gap reached through the SAVE rather than through the panel, pinned because the
    refusal above it does not cover this and a comment claiming otherwise would be the third time
    tonight a sentence stood in for a check.

    A single bad sovereign row leaves the file parseable, so every other row is honoured and this
    one is dropped by the next save of any slot, with nothing said: no panel draws these slots.
    Refusing here would strand every model change in the Project behind a row no surface shows and
    no message names — a worse trade than the silence. It closes when those slots get a surface,
    not here."""
    orch = _orch(tmp_path)
    _seed(orch, '{"sovereign_plan": {"modell": "x"}, "ask": "opus"}')
    assert all(r["problem"] is None or "couldn't be read" not in r["problem"]
               for r in orch.model_assignments()["slots"])
    orch.set_catalog(plan="opus")
    assert "sovereign_plan" not in orch.project().record.catalog_overrides_path.read_text()
    # The rows that ARE drawn came through untouched, which is why refusing the save is the worse
    # trade: this save is a legitimate one that happens to share a file with a broken row.
    assert _runs(orch, Mode.ASK) == "opus"


def _leftover_tmps(d) -> list[str]:
    """Staging files left in `d`, found by iterating rather than by `glob("*.tmp")`.

    `glob` does not match a leading dot, and every staging name here has one — so the two
    assertions below were reporting an empty list because they could not see the files, not
    because there were none. They had been vacuous since #289 introduced the dotted name.
    """
    return sorted(p.name for p in d.iterdir() if p.name.endswith(".tmp"))


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path):
    """Uniqueness is what makes a failed write accumulate rather than overwrite, so every ENOSPC or
    EACCES would drop a fresh dotfile into `.sage/` — the one directory this change keeps calling
    committed and shared. The leading dot is not the protection: `git status` and `git add -A` both
    include dotfiles."""
    orch = _orch(tmp_path)
    record = orch.project().record
    record.write_catalog_overrides({"plan": {"model": "opus", "effort": None}})
    assert _leftover_tmps(record.catalog_overrides_path.parent) == []
    # And on the failing path, which is the one that leaked. Injected at `os.fsync` rather than at
    # `Path.write_text`: #308 moved the staged write into `_write_atomic`, which writes through an
    # open handle, so the old patch target stopped being on this path at all and the `raises` below
    # started reporting DID NOT RAISE. `fsync` is inside the helper's `try`, so the `finally` that
    # this test is about still runs.
    import unittest.mock
    with unittest.mock.patch("os.fsync", side_effect=OSError("no space")):
        with pytest.raises(OSError):
            record.write_catalog_overrides({"plan": {"model": "coder", "effort": None}})
    assert _leftover_tmps(record.catalog_overrides_path.parent) == []
    # The previous contents survived the failed write, which is the other half of atomicity.
    assert record.read_catalog_overrides() == {"plan": {"model": "opus", "effort": None}}


def test_a_partly_honoured_row_says_so_rather_than_claiming_the_default(tmp_path):
    """The sentence that had to exist once one bad half stopped costing the good one. A row can now
    be in `rows` AND in `unreadable`, and neither of the other two sentences is true of it: the slot
    is not following the default, and it is not running something stale — it runs exactly what the
    readable part says, and the next save writes that part BACK rather than dropping it.

    So the two clauses that were wrong here are the ones asserted: "until the next save" was false,
    and "or remove it" told somebody to delete an assignment that is in effect. Both checked against
    the file after a save, because that is what makes the first one false."""
    orch = _orch(tmp_path)
    _seed(orch, '{"ask": {"model": "opus", "modell": "x"}}')
    orch = _orch(tmp_path)
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "ask")
    assert "Part of the ask row" in row["problem"]
    assert "until the next save" not in row["problem"]
    assert "remove it" not in row["problem"]
    # The half that could be read is genuinely in effect, which is why the other sentences lie.
    assert (row["model"], row["assigned"]) == ("opus", True)
    # And the next save keeps it, which is the fact that made "until the next save" false.
    orch.set_catalog(plan="coder")
    assert orch.project().record.read_catalog_overrides()["ask"] == {
        "model": "opus", "effort": None}
    # A row where nothing survived must NOT get this sentence — the two cases must not collapse.
    # Re-booted, because which of the two whole-row sentences it lands on depends on the shim's
    # catalog; what is being asserted is only that it is no longer the partial one.
    _seed(orch, '{"ask": true}')
    orch = _orch(tmp_path)
    whole = _problems(orch)["ask"]
    assert "Part of the ask row" not in whole
    assert "so this slot is following the default" in whole


def test_a_save_keeps_the_permissions_the_file_already_had(tmp_path):
    """`os.replace` swaps the inode, so without this the file takes the temp file's mode under the
    current umask. On a shared Project volume that silently drops group-write on the first save by
    any Builder, and the next collaborator's save fails EACCES — which this same reader reports as
    "couldn't be read. Fix that file", sending them to edit contents that are fine.

    The failure it prevents is two changes of mine compounding, which is why it is pinned here
    rather than left to the umask."""
    import stat
    orch = _orch(tmp_path)
    record = orch.project().record
    record.write_catalog_overrides({"plan": {"model": "opus", "effort": None}})
    record.catalog_overrides_path.chmod(0o664)
    record.write_catalog_overrides({"plan": {"model": "coder", "effort": None}})
    assert stat.S_IMODE(record.catalog_overrides_path.stat().st_mode) == 0o664
