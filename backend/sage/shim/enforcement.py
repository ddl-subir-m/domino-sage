"""EnforcementShim — the core of the sovereign / zero-vendor guarantee (DESIGN.md Seam 2).

Per request: consult the router, override the `model` field when locked, tag with
project + phase, forward to the gateway. This is a thin shim in front of the EXISTING
OpenAI-compatible Domino gateway, not a proxy built from scratch (SPEC.md C4).

Containment ("zero direct-to-vendor") is provided by the container egress allowlist, NOT by
this code — this shim only guarantees the *policy* half (right model + tagging). See Step 1.4.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from ..gateway.client import CostLabels, GatewayClient
from ..router import llm_router
from ..router.model_control import ModelControl
from ..router.models import ModelCatalog


class EnforcementShim:
    def __init__(
        self,
        control: ModelControl,
        catalog: ModelCatalog,
        gateway: GatewayClient,
        force_model: bool = False,
    ) -> None:
        self._control = control
        self._catalog = catalog
        self._gateway = gateway
        # force_model: always route to the router's resolved model, ignoring what the caller
        # asked. Needed for a single-provider host (e.g. DeepSeek) where OpenCode's other model
        # aliases don't exist upstream. Off for the real multi-model Domino gateway.
        self._force_model = force_model

    def handle(self, request: dict[str, Any], project: str) -> Iterator[bytes]:
        """OpenAI-compatible request in, streamed response out. OpenCode points at this."""
        requested = request.get("model")
        decision = llm_router.resolve(self._control.snapshot(), self._catalog)

        # Override the model when locked (sovereignty), when force_model is on (single-provider
        # host), or when the caller sent none. Otherwise honor the caller's choice.
        if decision.locked or self._force_model or "model" not in request:
            request = {**request, "model": decision.model}

        logging.getLogger("sage.shim").info(
            "model policy: requested=%s -> resolved=%s (%s, locked=%s)",
            requested, request["model"], decision.reason.value, decision.locked,
        )

        # Mandatory tagging so the gateway attributes cost (avoids the 'unknown' bucket).
        labels = CostLabels(
            project=project,
            phase=self._control.snapshot().phase.value,
            model=request["model"],
        )
        return self._gateway.route(request, labels)
