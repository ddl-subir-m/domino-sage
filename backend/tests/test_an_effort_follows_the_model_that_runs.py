"""#282 / ADR-0049: a Build turn sends the effort belonging to the model that actually runs.

Before this, `enforcement.handle` sent `reasoning_effort` only on a tool-less Chat turn whose
resolved model was the one the person picked in Chat. A Build turn had never sent the field, on any
model — so the three per-slot efforts #281 persists were stored, validated, drawn, and never used.

Two rules are pinned here, because either one alone sends a 400 rather than a turn:

* the effort comes from whatever chose the model (the slot, the pin, the veto, the act), never from
  the slot that was asked for; and
* the field is only sent when the model on the wire was MEASURED to accept that level, given the
  shape of this request — which re-validates a stored effort that the deployment default moved out
  from under (the hazard `_effective_catalog` records and names this ticket for).
"""
from __future__ import annotations

from dataclasses import replace as _replace

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.router import llm_router
from sage.router.model_control import ModelControl
from sage.router.models import (
    EFFORTS_WITH_TOOLS,
    REASONING_EFFORTS,
    Mode,
    ModelCatalog,
    Phase,
    Reason,
    SessionState,
    reasoning_efforts_for,
    reasoning_efforts_with_tools,
)
from sage.shim.enforcement import EnforcementShim

# Real alias names throughout: every table in this area is keyed on the name the gateway spells, so
# a catalog of `strong-vendor`/`cheap-vendor` placeholders can only ever prove the field is dropped.
CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b",
    sovereign_implement="sovereign-8b",
    sovereign_ask="sovereign-8b",
    plan="gpt-5.4",
    implement="sonnet",
    ask="gpt-5.4",
)

TOOLS = [{"function": {"name": "read"}}]


def _sent(control: ModelControl, catalog: ModelCatalog, **request):
    gw = FakeGatewayClient()
    body = {"model": "opencode-default", "messages": [], **request}
    list(EnforcementShim(control, catalog, gw).handle(body, project="p"))
    return gw.seen[-1][0]


# ---------------------------------------------------------------- the measured tool-shape table


def test_the_efforts_an_alias_keeps_alongside_tools_are_measured_not_assumed():
    """The old guard dropped the field from EVERY tool-carrying request, for every alias.

    That is the wrong shape, not just the wrong scope. The limitation it was written for is
    gpt-5.4's ("Function tools with reasoning_effort are not supported for gpt-5.4 in
    /v1/chat/completions"); `domino/gemini-3.7-flash` was measured taking the field alongside tools,
    200, on both `low` and `high` (ADR-0049).
    """
    assert reasoning_efforts_with_tools("gemini-3.7-flash") == reasoning_efforts_for("gemini-3.7-flash")
    assert reasoning_efforts_with_tools("domino/gemini-3.7-flash") == ("low", "medium", "high", "max")
    # gpt-5.4 keeps exactly one level with tools: `none` answers 200 where every other level 400s.
    assert reasoning_efforts_with_tools("gpt-5.4") == ("none",)
    assert reasoning_efforts_with_tools("domino/gpt-5.4") == ("none",)
    # An alias nobody probed is offered nothing either way, so the shape question never arises.
    assert reasoning_efforts_with_tools("sonnet") == ()


def test_a_tool_shape_row_can_only_narrow_the_alias_row_it_belongs_to():
    """The with-tools table is a narrowing of the measured one, never a second source of levels.

    Without this, a row here could offer a level `reasoning_efforts_for` does not — and the send
    path tests one or the other depending on whether the request carries tools, so the level would
    reach the wire on exactly the requests the alias refuses it on.
    """
    for alias, efforts in EFFORTS_WITH_TOOLS.items():
        assert alias in REASONING_EFFORTS, f"{alias} has a tool-shape row but no measured row"
        assert set(efforts) <= set(REASONING_EFFORTS[alias]), alias


# ---------------------------------------------------------------- the router carries the effort


def _state(**kw) -> SessionState:
    return SessionState(mode=kw.pop("mode", Mode.AUTO), phase=kw.pop("phase", Phase.PLAN), **kw)


def test_each_build_slot_resolves_with_its_own_effort():
    catalog = _replace(CATALOG, plan_effort="high", implement_effort="low", ask_effort="medium")

    assert llm_router.resolve(_state(phase=Phase.PLAN), catalog).effort == "high"
    assert llm_router.resolve(_state(phase=Phase.IMPLEMENT), catalog).effort == "low"
    assert llm_router.resolve(_state(mode=Mode.ASK), catalog).effort == "medium"
    assert llm_router.resolve(_state(mode=Mode.PLAN), catalog).effort == "high"
    assert llm_router.resolve(_state(mode=Mode.IMPLEMENT), catalog).effort == "low"


def test_an_in_session_pick_replaces_the_effort_along_with_the_model():
    """An override replaces the model, so it replaces the effort (ADR-0049).

    Falling back to the slot's effort is the version anyone writes first, and it applies a level
    picked for a model that may not accept it — the pick has no effort of its own on Build, so the
    honest answer is none.
    """
    catalog = _replace(CATALOG, plan_effort="high", implement_effort="low")

    picked = llm_router.resolve(_state(mode=Mode.PLAN, picked_model="gemini-3.7-flash"), catalog)
    assert picked.reason is Reason.PLAN_OVERRIDE
    assert picked.effort is None
    assert llm_router.resolve(
        _state(mode=Mode.IMPLEMENT, picked_model="gpt-5.4"), catalog).effort is None


def test_the_signing_pin_takes_its_own_slots_effort_with_it():
    """The pin moves a plan turn onto implement's model, so it must move implement's effort too.

    Sending plan's `xhigh` to gemini is a 400 — its enum is low/medium/high/max — and sending it to
    an alias that discards the field is a silent lie in the log.
    """
    catalog = _replace(
        CATALOG, implement="gemini-3.7-flash", plan_effort="xhigh", implement_effort="low")

    decision = llm_router.resolve(_state(phase=Phase.PLAN), catalog)

    assert decision.reason is Reason.SIGNING_PIN
    assert decision.model == "gemini-3.7-flash"
    assert decision.effort == "low"


def test_the_signing_veto_takes_the_effort_of_the_slot_it_lands_on():
    catalog = _replace(
        CATALOG, plan="gemini-3.7-flash", plan_effort="max", implement_effort="medium")

    fallback = llm_router.resolve_unsigned(_state(phase=Phase.PLAN), catalog)

    assert fallback is not None
    assert fallback.model == "sonnet"           # the implement slot, which does not sign
    assert fallback.effort == "medium"


def test_the_sensitivity_lock_moves_the_model_off_every_slot_so_it_carries_no_effort():
    """The lock picks by approval, not by slot, so there is no assignment behind its answer.

    An approved model that happens to equal some other slot's model is still not that slot's
    assignment, and inheriting an effort from it would be the stale-effort defect wearing a
    coincidence.
    """
    catalog = _replace(CATALOG, plan_effort="high")

    decision = llm_router.resolve(
        _state(phase=Phase.PLAN, approved_models=frozenset({"sovereign-8b"})), catalog)

    assert decision.reason is Reason.SENSITIVITY
    assert decision.effort is None


def test_an_approved_model_keeps_the_effort_the_slot_gave_it():
    catalog = _replace(CATALOG, plan_effort="high")

    decision = llm_router.resolve(
        _state(phase=Phase.PLAN, approved_models=frozenset({"gpt-5.4"})), catalog)

    assert decision.locked is True
    assert decision.effort == "high"


def test_chat_keeps_its_two_answers_the_pick_and_the_ask_assignment():
    catalog = _replace(CATALOG, ask_effort="medium")

    picked = llm_router.resolve(
        _state(chat_thread_id="thr_1", chat_model="gemini-3.7-flash", reasoning_effort="max"),
        catalog)
    assert picked.reason is Reason.CHAT_OVERRIDE
    assert picked.effort == "max"

    default = llm_router.resolve(_state(chat_thread_id="thr_1"), catalog)
    assert default.reason is Reason.CHAT_DEFAULT
    assert default.effort == "medium"


# ---------------------------------------------------------------- the shim sends it


def test_a_build_turn_sends_the_slots_effort():
    """The whole ticket in one assertion: before this, no Build turn ever carried the field.

    Made on a tool-carrying request, because that is what a Build turn IS — the agent is offered
    read/edit/bash on every step, and a claim proved without them is a claim about a request shape
    the product does not send. gemini is the alias that takes an effort in that shape.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    catalog = _replace(CATALOG, implement="gemini-3.7-flash", implement_effort="high")

    assert _sent(control, catalog, tools=TOOLS)["reasoning_effort"] == "high"
    # And on the tool-less inferences of the same build, where the whole enum is available.
    gpt = _replace(CATALOG, implement="gpt-5.4", implement_effort="high")
    assert _sent(control, gpt)["reasoning_effort"] == "high"


def test_a_build_turn_with_no_effort_assigned_sends_no_field():
    """No assignment means the alias's own default, which is what every Build turn had before."""
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)

    assert "reasoning_effort" not in _sent(control, _replace(CATALOG, implement="gpt-5.4"))


def test_auto_sends_the_phases_own_effort_and_not_the_other_slots():
    """`think hard while planning, cheaply while implementing` is the reason the slots are split.

    One alias in both slots on purpose. gemini is the only other alias with a measured table and it
    signs tool calls, so putting it in `implement` would pin the plan phase onto it (ADR-0032) and
    the assertion would be reading the pin rather than the phase. The effort is the only thing that
    differs here, which is the whole claim.
    """
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    catalog = _replace(
        CATALOG, plan="gpt-5.4", implement="gpt-5.4",
        plan_effort="xhigh", implement_effort="low")

    assert _sent(control, catalog)["reasoning_effort"] == "xhigh"

    # The phase comes from the message tail, per request — a write flips Auto to implement, and the
    # effort has to follow the same step the model does. Setting it on the control would not: the
    # classifier overwrites `phase` on every Auto request.
    writing = [{"role": "assistant", "tool_calls": [{"function": {"name": "edit"}}]}]
    assert _sent(control, catalog, messages=writing)["reasoning_effort"] == "low"


def test_a_tool_carrying_build_turn_keeps_the_effort_on_an_alias_measured_to_take_it():
    """Trap 1. A Build plan phase is a tool-carrying turn; gemini takes the field there, 200.

    Dropping it for every alias is what made ADR-0049 call the old guard the wrong shape — on the
    one alias that accepts effort and tools together, the drop cost the whole feature.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    catalog = _replace(CATALOG, implement="domino/gemini-3.7-flash", implement_effort="high")

    assert _sent(control, catalog, tools=TOOLS)["reasoning_effort"] == "high"


def test_a_tool_carrying_turn_drops_a_level_gpt_5_4_refuses_beside_tools():
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    catalog = _replace(CATALOG, implement="gpt-5.4", implement_effort="high")

    sent = _sent(control, catalog, tools=TOOLS)

    assert sent["model"] == "gpt-5.4"           # only the effort is dropped, never the turn
    assert "reasoning_effort" not in sent


def test_a_tool_carrying_turn_keeps_the_one_level_gpt_5_4_does_accept():
    """`none` is a level, not an absence: it answers 200 beside tools where every other level 400s.

    Dropping it would silently run at the alias's own default the one time somebody asked for no
    reasoning at all, which is the cost lever they were reaching for.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    catalog = _replace(CATALOG, implement="gpt-5.4", implement_effort="none")

    assert _sent(control, catalog, tools=TOOLS)["reasoning_effort"] == "none"


def test_an_effort_the_running_model_does_not_accept_is_dropped_not_sent():
    """The hazard #281 left armed, and the reason this is a drop rather than a refusal.

    A stored effort is validated once, at save, against the model the slot ran THEN. The deployment
    default can move under it afterwards and nothing re-validates — so the first turn on the new
    default carries a level it never advertised. `qwen-2-5` 400s on unknown fields and two aliases
    validate the value, so sending it is a dead turn; the person's build is worth more than their
    stale level.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    # `sonnet` is the moved-under default: it was measured discarding the field entirely.
    catalog = _replace(CATALOG, implement="sonnet", implement_effort="high")

    assert "reasoning_effort" not in _sent(control, catalog)


def test_an_effort_outside_the_running_aliases_enum_is_dropped():
    """Same drop, one step subtler: the alias validates the field and this level is not in its list."""
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    catalog = _replace(CATALOG, implement="gemini-3.7-flash", implement_effort="xhigh")

    assert "reasoning_effort" not in _sent(control, catalog)


def test_the_signing_pin_sends_the_effort_of_the_model_it_moved_to():
    """End to end: never send an effort chosen for one model to a different one."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    catalog = _replace(
        CATALOG, plan="gpt-5.4", implement="gemini-3.7-flash",
        plan_effort="xhigh", implement_effort="low")

    sent = _sent(control, catalog)

    assert sent["model"] == "gemini-3.7-flash"
    assert sent["reasoning_effort"] == "low"    # implement's, not plan's `xhigh`, which gemini 400s


def test_an_in_session_pick_sends_no_effort_even_when_the_slot_has_one():
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    control.pick("gpt-5.4")
    catalog = _replace(CATALOG, implement="sonnet", implement_effort="high")

    sent = _sent(control, catalog)

    assert sent["model"] == "gpt-5.4"
    assert "reasoning_effort" not in sent


def test_an_ask_turn_sends_the_ask_slots_effort():
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    catalog = _replace(CATALOG, ask="gpt-5.4", ask_effort="medium")

    assert _sent(control, catalog)["reasoning_effort"] == "medium"


# ---------------------------------------------------------------- Chat is unchanged, plus one


def test_the_chat_pick_still_wins_and_the_floor_still_applies():
    control = ModelControl()
    control.pick_chat("gpt-5.4", "high")
    token = control.arm_chat("thr_1")
    try:
        assert _sent(control, CATALOG)["reasoning_effort"] == "high"
    finally:
        control.disarm_chat(token)

    control = ModelControl()
    token = control.arm_chat("thr_1")
    try:
        # Chat on Auto: no pick, so the floor answers rather than the alias's own default.
        assert _sent(control, _replace(CATALOG, ask="gpt-5.4"))["reasoning_effort"] == "low"
        # But a real Chat turn always carries tools, and gpt-5.4 — the shipped `ask` model — keeps
        # only `none` in that shape, so the floor does not reach the turns it was written for.
        # Stated here rather than left implied: the tool-less assertion above is the mechanism, and
        # on its own it reads as a promise the product does not keep. See the gpt-5.4 test above.
        assert "reasoning_effort" not in _sent(
            control, _replace(CATALOG, ask="gpt-5.4"), tools=TOOLS)
        # On an alias that takes an effort beside tools, the floor does reach the real shape.
        assert _sent(
            control, _replace(CATALOG, ask="gemini-3.7-flash"), tools=TOOLS
        )["reasoning_effort"] == "low"
    finally:
        control.disarm_chat(token)


def test_an_effort_on_the_ask_assignment_beats_the_chat_floor():
    """ADR-0049: the floor exists because no pick meant no field. An assignment IS a pick."""
    control = ModelControl()
    token = control.arm_chat("thr_1")
    try:
        catalog = _replace(CATALOG, ask="gpt-5.4", ask_effort="xhigh")
        assert _sent(control, catalog)["reasoning_effort"] == "xhigh"
    finally:
        control.disarm_chat(token)


def test_the_chat_floor_is_not_offered_to_an_alias_that_never_advertised_it():
    control = ModelControl()
    token = control.arm_chat("thr_1")
    try:
        assert "reasoning_effort" not in _sent(control, _replace(CATALOG, ask="sonnet"))
    finally:
        control.disarm_chat(token)


@pytest.mark.parametrize("alias", sorted(REASONING_EFFORTS))
def test_every_measured_level_reaches_the_wire_on_the_shape_that_accepts_it(alias: str):
    """Both shapes, because the honest invariant is per shape and asking only one hides the gap.

    Tool-less, the alias's whole enum must arrive: a row nothing can send is a row that lies to the
    panel #283 draws from. With tools, the narrowed row is the truth, and the levels outside it must
    be dropped rather than sent — sending one is the 400 this feature would otherwise ship.

    Asked tool-less alone, this certifies green the very thing it is written to catch. That is how
    `gpt-5.4` — the deployment's own `plan` and `ask` model — came to have exactly one usable Build
    level with the guard reporting no problem.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    with_tools = reasoning_efforts_with_tools(alias)
    for effort in REASONING_EFFORTS[alias]:
        catalog = _replace(CATALOG, implement=alias, implement_effort=effort)

        assert _sent(control, catalog)["reasoning_effort"] == effort

        sent = _sent(control, catalog, tools=TOOLS)
        if effort in with_tools:
            assert sent["reasoning_effort"] == effort
        else:
            assert "reasoning_effort" not in sent


def test_only_one_level_survives_a_real_build_turn_on_the_deployments_own_plan_model():
    """The narrowing is right and the reach of it is a gap somebody else has to close.

    `gpt-5.4` is the shipped `plan` and `ask` model, and every Build turn carries tools, so `none`
    is the only level it can actually run there. Sending any other would be a hard 400 on the whole
    turn, so this send path is not where the answer is — but `set_catalog` validates against the
    full enum and the panel draws from it, so a person saves `high`, sees it kept, and gets the
    alias's own default forever with one deduped INFO line as the only witness.

    Written down as a test rather than a comment because it is a live claim about shipped defaults:
    the day the write path or the panel narrows by request shape too, this goes red and names them.

    That day is #298, which carries the whole design — narrow `model_assignments()` first, then
    `_merge_assignment`, both against `reasoning_efforts_with_tools`. Invert or delete this test
    there. It asserts the gap on purpose and is not an invariant to preserve.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    runs = [e for e in REASONING_EFFORTS["gpt-5.4"]
            if "reasoning_effort" in _sent(
                control, _replace(CATALOG, implement="gpt-5.4", implement_effort=e), tools=TOOLS)]

    assert runs == ["none"]


# ---------------------------------------------------------------- what the send path owns


def test_a_stale_unacceptable_effort_falls_back_to_the_chat_floor():
    """The floor is asked AFTER acceptance, so the level that was dropped leaves no effort behind.

    Reachable, and it is the case the floor matters most on: an `ask` assignment saved as `xhigh`
    while the deployment default was gpt-5.4 (which `set_catalog` accepts), then the default moves
    to gemini, whose enum has no `xhigh`. Asked first, the floor would see a non-None effort, skip,
    and hand the turn gemini's own full-reasoning default — paying the exact bill it was installed
    to stop, on the one assignment already known to be stale.
    """
    control = ModelControl()
    token = control.arm_chat("thr_1")
    try:
        catalog = _replace(CATALOG, ask="gemini-3.7-flash", ask_effort="xhigh")
        assert _sent(control, catalog)["reasoning_effort"] == "low"
    finally:
        control.disarm_chat(token)


def test_an_effort_the_caller_sent_never_reaches_the_gateway():
    """`model` is overwritten on every request, so an incoming effort is a level for another model.

    OpenCode's own config can carry one, and both `/v1/chat/completions` doors accept whatever the
    body holds. Left alone it rides to a model that never advertised it — and on a tool-carrying
    gpt-5.4 turn that is the 400 this whole block exists to avoid, while the log claims the field
    was handled.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    catalog = _replace(CATALOG, implement="gpt-5.4")

    sent = _sent(control, catalog, reasoning_effort="high", tools=TOOLS)

    assert sent["model"] == "gpt-5.4"
    assert "reasoning_effort" not in sent


def test_an_assignment_beats_the_effort_the_caller_sent():
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    catalog = _replace(CATALOG, implement="gpt-5.4", implement_effort="low")

    assert _sent(control, catalog, reasoning_effort="xhigh")["reasoning_effort"] == "low"


def test_the_dropped_effort_is_announced_once_and_not_on_every_inference(caplog):
    """A standing bad level is one fact. Logged per inference it buries the turn it first appeared
    on — the Live read line directly above it in the shim is deduped for the same reason."""
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    catalog = _replace(CATALOG, implement="sonnet", implement_effort="high")
    gw = FakeGatewayClient()
    shim = EnforcementShim(control, catalog, gw)

    with caplog.at_level("INFO", logger="sage.shim"):
        for _ in range(3):
            list(shim.handle({"model": "opencode-default", "messages": []}, project="p"))

    dropped = [r for r in caplog.records if "dropping reasoning_effort" in r.getMessage()]
    assert len(dropped) == 1
    assert "high" in dropped[0].getMessage()
    assert "sonnet" in dropped[0].getMessage()
