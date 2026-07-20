"""EnforcementShim — the core of the sovereign / zero-vendor guarantee (DESIGN.md Seam 2).

Per request: consult the router, override the `model` field when locked, tag with
project + phase, forward to the gateway. This is a thin shim in front of the EXISTING
OpenAI-compatible Domino gateway, not a proxy built from scratch (SPEC.md C4).

Containment ("zero direct-to-vendor") is provided by the container egress allowlist, NOT by
this code — this shim only guarantees the *policy* half (right model + tagging). See Step 1.4.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..gateway.client import CostLabels, GatewayClient
from ..router import llm_router
from ..router.model_control import ModelControl
from ..router.models import ModelCatalog


class EnforcementShim:
    def __init__(self, control: ModelControl, catalog: ModelCatalog, gateway: GatewayClient) -> None:
        self._control = control
        self._catalog = catalog
        self._gateway = gateway

    def handle(self, request: dict[str, Any], project: str) -> Iterator[bytes]:
        """OpenAI-compatible request in, streamed response out. OpenCode points at this."""
        decision = llm_router.resolve(self._control.snapshot(), self._catalog)

        # Enforce policy: when locked, override whatever the caller asked for.
        if decision.locked:
            request = {**request, "model": decision.model}
        elif "model" not in request:
            request = {**request, "model": decision.model}

        # Mandatory tagging so the gateway attributes cost (avoids the 'unknown' bucket).
        labels = CostLabels(
            project=project,
            phase=self._control.snapshot().phase.value,
            model=request["model"],
        )
        return self._gateway.route(request, labels)
