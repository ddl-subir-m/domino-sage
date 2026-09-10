"""Compaction cannot leave the sensitivity lock (ADR-0043).

Compaction sends the WHOLE conversation to a model — every row a declared Dataset put in it. It is
safe today, but safe by an invariant rather than by a check: `chat_compact.COMPACT_FALLBACK` is the
vendor alias `gpt-5.4`, handed to OpenCode as a resolution handle whenever the real alias is absent
from `CONTEXT_LIMITS`, and the only thing that stops it becoming the model is `enforcement.handle`
overwriting `model` on every request from `llm_router.resolve`.

What this file cannot see is whether a turn is armed at all. It proves the rewrite on an ARMED turn;
`test_the_lock_follows_the_conversation` proves that a turn is still one after the creator unbinds
the Dataset — which is where these same rows used to walk out to `COMPACT_FALLBACK`.

That was a comment. Under a governance promise an untested invariant IS the vulnerability, so these
tests hold it: if anyone makes the shim's rewrite conditional again, or routes summarize around it,
this file goes red rather than a declared Dataset going quietly to a vendor.
"""
from __future__ import annotations

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.chat_compact import (
    COMPACT_FALLBACK,
    CONTEXT_LIMITS,
    compact_model,
    summarize_model_id,
)
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase, Reason, SessionState
from sage.shim.enforcement import EnforcementShim

CATALOG = ModelCatalog(
    sovereign_plan="qwen-2-5", sovereign_implement="qwen-2-5", sovereign_ask="qwen-2-5",
    plan="gpt-5.4", implement="gpt-5.4", ask="gpt-5.4",
)
APPROVED = frozenset({"qwen-2-5"})


def test_the_fallback_really_is_a_vendor_alias():
    """The premise. If this ever stops being true the rest of the file is testing nothing."""
    assert COMPACT_FALLBACK == "gpt-5.4"
    assert COMPACT_FALLBACK not in APPROVED


def test_compact_model_routes_through_the_lock():
    """`compact_model` calls the router, so a locked session compacts through an approved alias."""
    locked = SessionState(Mode.AUTO, Phase.PLAN, chat_thread_id="t1", chat_model="gpt-5.4",
                          approved_models=APPROVED)
    _provider, model = compact_model(locked, CATALOG)
    assert model == "qwen-2-5"

    unlocked = SessionState(Mode.AUTO, Phase.PLAN, chat_thread_id="t1", chat_model="gpt-5.4")
    assert compact_model(unlocked, CATALOG)[1] == "gpt-5.4"


def test_the_summarize_handle_can_still_name_a_vendor_alias():
    """Not a bug, and deliberately not 'fixed' here: OpenCode can only resolve what its config
    lists, so an unlisted alias has to be named as something else. The handle is not the routing."""
    # The gateway offers aliases opencode.json does not list — this one today, verified against
    # CONTEXT_LIMITS rather than asserted from memory, since the config gains entries over time.
    unlisted = "domino-gcp/claude-sonnet-5"
    assert unlisted.rsplit("/", 1)[-1] not in CONTEXT_LIMITS, "opencode.json now lists this alias"
    assert summarize_model_id(unlisted) == COMPACT_FALLBACK


def test_the_shim_rewrites_a_summarize_request_back_onto_the_approved_alias():
    """THE invariant. A request carrying the vendor handle, on a locked session, must leave the
    shim naming an approved alias — this is what makes the handle above harmless."""
    control = ModelControl()
    control.arm_chat("t1")
    control.pick_chat("gpt-5.4")
    token = control.arm_sensitivity(APPROVED)

    gw = FakeGatewayClient()
    shim = EnforcementShim(control, CATALOG, gw)

    request = {"model": COMPACT_FALLBACK, "messages": [{"role": "user", "content": "summarise"}]}
    list(shim.handle(dict(request), project="p1"))
    sent, _ = gw.seen[-1]
    assert sent["model"] == "qwen-2-5"
    assert sent["model"] in APPROVED

    # And the lock is per-turn: disarmed, the same request goes out on the Chat pick again.
    control.disarm_sensitivity(token)
    list(shim.handle(dict(request), project="p1"))
    assert gw.seen[-1][0]["model"] == "gpt-5.4"


def test_the_locked_decision_says_it_is_locked():
    """The picker reads this to disable the rest with a reason, so it has to survive the route."""
    from sage.router import llm_router

    decision = llm_router.resolve(
        SessionState(Mode.AUTO, Phase.PLAN, chat_thread_id="t1", chat_model="gpt-5.4",
                     approved_models=APPROVED),
        CATALOG,
    )
    assert decision.locked is True
    assert decision.reason is Reason.SENSITIVITY
