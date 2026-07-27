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
from dataclasses import replace
from typing import Any

from ..gateway.client import CostLabels, GatewayClient
from ..router import llm_router
from ..router.model_control import ModelControl
from ..router.models import ModelCatalog, Mode
from ..router.phase_classifier import WRITE_TOOLS, classify


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

    @property
    def catalog(self) -> ModelCatalog:
        return self._catalog

    def set_catalog(self, catalog: ModelCatalog) -> None:
        """Swap the catalog this shim's requests resolve against (e.g. a per-project override of
        which model Auto uses for plan/implement). Takes effect on the next request."""
        self._catalog = catalog

    def handle(self, request: dict[str, Any], project: str) -> Iterator[bytes]:
        """OpenAI-compatible request in, streamed response out. OpenCode points at this."""
        requested = request.get("model")
        state = self._control.snapshot()

        # Per-step phase: in Auto mode, classify THIS inference from its own message tail (plan
        # while reasoning/reading, implement while writing code). Done here, per request, so
        # interleaved turns route correctly step by step — not from a laggy background poll. The
        # lock still wins in resolve(), so skip classifying when locked. Reflect the phase back to
        # the control so the UI's live indicator matches what actually routed.
        if state.mode is Mode.AUTO and not state.sensitivity_locked:
            phase = classify(request.get("messages"))
            state = replace(state, phase=phase)
            self._control.set_phase(phase)

        # Ask mode is read-only: strip write tools from the request so the model is never
        # offered them. This only controls what this shim advertises upstream — it can't reach
        # into OpenCode's own tool-execution layer, which is out of scope for this guarantee.
        if state.mode is Mode.ASK and "tools" in request:
            tools = [
                t for t in request["tools"]
                if (t.get("function") or {}).get("name", "").lower() not in WRITE_TOOLS
            ]
            request = {**request, "tools": tools}

        decision = llm_router.resolve(state, self._catalog)

        # Override the model when locked (sovereignty), when force_model is on (single-provider
        # host), or when the caller sent none. Otherwise honor the caller's choice.
        if decision.locked or self._force_model or "model" not in request:
            request = {**request, "model": decision.model}

        logging.getLogger("sage.shim").info(
            "model policy: requested=%s -> resolved=%s (%s, phase=%s, locked=%s)",
            requested, request["model"], decision.reason.value, state.phase.value, decision.locked,
        )

        # Mandatory tagging so the gateway attributes cost (avoids the 'unknown' bucket).
        labels = CostLabels(
            project=project,
            phase=state.phase.value,
            model=request["model"],
        )
        return self._gateway.route(request, labels)
