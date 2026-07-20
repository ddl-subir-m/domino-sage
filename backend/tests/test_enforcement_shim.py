"""Integration tests for the enforcement shim against the FakeGatewayClient (DESIGN.md Seam 2).

Proves the policy half of the guarantee without a network: model override under lock + mandatory
tagging. The containment half (egress allowlist) is an infra test, not here (Step 1.4).
"""
from __future__ import annotations

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

CATALOG = ModelCatalog(sovereign="sovereign-8b", plan="strong-vendor", implement="cheap-vendor", default="default-vendor")


def _shim(control: ModelControl, gw: FakeGatewayClient) -> EnforcementShim:
    return EnforcementShim(control, CATALOG, gw)


def test_override_forces_sovereign_when_locked():
    control = ModelControl(mode=Mode.MANUAL, phase=Phase.PLAN)
    control.pick("strong-vendor")
    control.on_assets_changed([True])  # sensitivity-tagged asset attached
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "strong-vendor", "messages": []}, project="p1"))

    sent_request, labels = gw.seen[-1]
    assert sent_request["model"] == "sovereign-8b"  # caller's model was overridden
    assert labels.model == "sovereign-8b"


def test_every_request_is_tagged_with_project_and_phase():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"messages": []}, project="proj-x"))

    _, labels = gw.seen[-1]
    assert labels.project == "proj-x"
    assert labels.phase == "implement"  # never empty -> avoids the gateway 'unknown' bucket


def test_sticky_lock_survives_detach():
    control = ModelControl(mode=Mode.MANUAL, phase=Phase.PLAN)
    control.on_assets_changed([True])   # attach tagged
    control.on_assets_changed([False])  # detach: must NOT clear the lock
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "strong-vendor", "messages": []}, project="p1"))

    assert gw.seen[-1][0]["model"] == "sovereign-8b"
