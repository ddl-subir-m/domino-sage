"""Integration tests for the enforcement shim against the FakeGatewayClient (DESIGN.md Seam 2).

Proves the policy half of the guarantee without a network: model override under lock + mandatory
tagging. The containment half (egress allowlist) is an infra test, not here (Step 1.4).
"""
from __future__ import annotations

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b",
    sovereign_implement="sovereign-8b",
    sovereign_ask="sovereign-8b",
    plan="strong-vendor",
    implement="cheap-vendor",
    ask="ask-vendor",
)


def _shim(control: ModelControl, gw: FakeGatewayClient) -> EnforcementShim:
    return EnforcementShim(control, CATALOG, gw)


def test_override_forces_sovereign_when_locked():
    control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
    control.pick("strong-vendor")
    control.on_assets_changed([True])  # sensitivity-tagged asset attached
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "strong-vendor", "messages": []}, project="p1"))

    sent_request, labels = gw.seen[-1]
    assert sent_request["model"] == "sovereign-8b"  # caller's model was overridden
    assert labels.model == "sovereign-8b"


def test_every_request_is_tagged_with_project_and_phase():
    # Implement mode: phase comes straight from the control (no per-step classification), so this
    # deterministically exercises tag propagation. Auto-mode classification has its own test.
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"messages": []}, project="proj-x"))

    _, labels = gw.seen[-1]
    assert labels.project == "proj-x"
    assert labels.phase == "implement"  # never empty -> avoids the gateway 'unknown' bucket


def test_auto_mode_classifies_phase_per_request():
    # Auto mode picks the model per step from the message tail: writing code -> implement model.
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    gw = FakeGatewayClient()
    messages = [{"role": "assistant", "tool_calls": [{"function": {"name": "edit"}}]}]

    list(_shim(control, gw).handle({"messages": messages}, project="p"))

    sent_request, labels = gw.seen[-1]
    assert labels.phase == "implement"
    assert sent_request["model"] == "cheap-vendor"  # catalog.implement


def test_sticky_lock_survives_detach():
    control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
    control.on_assets_changed([True])   # attach tagged
    control.on_assets_changed([False])  # detach: must NOT clear the lock
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "strong-vendor", "messages": []}, project="p1"))

    assert gw.seen[-1][0]["model"] == "sovereign-8b"


def test_user_can_override_asset_lock():
    # The sticky asset lock is user-removable (with a UI warning); once cleared, routing is no
    # longer forced to sovereign — until another sensitivity-tagged asset is attached.
    control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
    control.on_assets_changed([True])   # sensitivity-tagged asset attached -> locked
    assert control.locked
    control.clear_asset_lock()          # user override
    assert not control.locked
    control.on_assets_changed([True])   # attaching another sensitive asset re-locks
    assert control.locked


def test_ask_mode_strips_write_tools_from_request():
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "edit"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    tool_names = [t["function"]["name"] for t in sent_request["tools"]]
    assert "edit" not in tool_names
    assert "read" in tool_names


def test_ask_mode_with_no_tools_key_is_untouched():
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"messages": []}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert "tools" not in sent_request


def test_ask_mode_respects_locked_sovereign_ask():
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    control.on_assets_changed([True])  # sensitivity-tagged asset attached
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "ask-vendor", "messages": []}, project="p"))

    assert gw.seen[-1][0]["model"] == "sovereign-8b"
