"""EnforcementShim — the core of the sovereign / zero-vendor guarantee (DESIGN.md Seam 2).

Per request: consult the router, override the `model` field when locked, tag with
project + phase, forward to the gateway. This is a thin shim in front of the EXISTING
OpenAI-compatible Domino gateway, not a proxy built from scratch (SPEC.md C4).

Containment ("zero direct-to-vendor") is provided by the container egress allowlist, NOT by
this code — this shim only guarantees the *policy* half (right model + tagging). See Step 1.4.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from ..gateway.client import CostLabels, GatewayClient
from ..router import llm_router
from ..router.model_control import ModelControl
from ..router.models import Mode, ModelCatalog, supports_vision
from ..router.phase_classifier import READ_ONLY_DENIED, TODO_TOOLS, WEB_TOOLS, classify

# What the agent sees in place of an image its model can't accept. It must know an image WAS
# attached — a silently dropped part reads as "the user sent nothing", and the agent then invents
# what it thinks the screenshot showed instead of asking.
IMAGE_OMITTED = "[image omitted: the active model cannot process images]"


def _strip_images(messages: list[Any]) -> tuple[list[Any], int]:
    """Replace image parts with a text marker. Rebuilds only the messages that actually carry an
    image (plain-string content and image-free part lists are passed through by identity), so the
    common text-only request is untouched and the caller's dicts are never mutated."""
    out: list[Any] = []
    dropped = 0
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list) or not any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            out.append(m)
            continue
        parts = [
            {"type": "text", "text": IMAGE_OMITTED}
            if isinstance(p, dict) and p.get("type") == "image_url" else p
            for p in content
        ]
        dropped += sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")
        out.append({**m, "content": parts})
    return out, dropped


def _resolve_sage_version() -> str | None:
    """Deploy-level Sage git rev for the `sage-version` cost tag. Best-effort: the baked image is a
    git clone (see environment/Dockerfile), so read HEAD once at import. SAGE_VERSION env wins if set
    (e.g. dogfood). Returns None rather than raising — a missing tag is better than a failed boot."""
    v = os.environ.get("SAGE_VERSION")
    if v:
        return v[:64]
    try:
        import subprocess

        home = os.environ.get("SAGE_APP_HOME", "/opt/sage")
        out = subprocess.run(
            ["git", "-C", home, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


_SAGE_VERSION = _resolve_sage_version()


class EnforcementShim:
    def __init__(
        self,
        control: ModelControl,
        catalog: ModelCatalog,
        gateway: GatewayClient,
        component: str = "builder",
        project_name: str | None = None,
    ) -> None:
        self._control = control
        self._catalog = catalog
        self._gateway = gateway
        # component: the `sage-component` cost tag — which Sage process this shim serves. Lets cost
        # analysis separate real build inference (builder) from orchestration overhead (probe).
        self._component = component
        # project_name: the `sage-project` cost tag — which Sage deployment spent this, as
        # "<owner>/<project>" (see preview/prefix.py domino_project_label). It's what makes a build
        # findable in the gateway's usage dashboard; without it every Sage install shares one bucket.
        self._project_name = project_name

    @property
    def catalog(self) -> ModelCatalog:
        return self._catalog

    @property
    def gateway(self) -> GatewayClient:
        """The upstream client, for the orchestrator's own one-off calls (see orchestrator/scope.py).

        Exposed rather than given a wrapper method here on purpose: a caller that wants to ask the
        gateway a question of its own supplies its own model and labels, and routing that through the
        shim would put product decisions inside the enforcement seam. What the shim owns is the
        guarantee that a LOCKED project only ever reaches a sovereign model — callers of this must
        honour it themselves, which is why `locked` is a required argument over there."""
        return self._gateway

    @property
    def version(self) -> str | None:
        """Sage git rev for the `sage-version` cost tag, so a caller tagging its own request can
        attribute it to the same deploy the shim's own traffic is attributed to."""
        return _SAGE_VERSION

    def set_catalog(self, catalog: ModelCatalog) -> None:
        """Swap the catalog this shim's requests resolve against (e.g. a per-project override of
        which model Auto uses for plan/implement). Takes effect on the next request."""
        self._catalog = catalog

    def handle(self, request: dict[str, Any], project: str, session: str | None = None) -> Iterator[bytes]:
        """OpenAI-compatible request in, streamed response out. OpenCode points at this.

        `project` is kept for the log line only — the gateway captures the caller's Domino project
        as a first-class column, so it's not tagged (a `project` tag would be dropped). `session` is
        the OpenCode session id, tagged as sage-session for per-build cost rollup."""
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

        # Read-only turns (Ask mode, or a plan turn held at the approval gate) get every write AND
        # shell tool stripped from the request, so the model is never offered one. This is the whole
        # read-only guarantee, not a best-effort layer on top of another: OpenCode's per-agent
        # `permission: {edit: deny, bash: deny}` does nothing on the headless server path (see
        # READ_ONLY_DENIED), so if a tool survives this filter, it runs. Shell matters most — that's
        # the hole that let Ask mode write files with `printf > file` for as long as it existed.
        # Web tools are default-denied on EVERY turn and only survive when the orchestrator armed
        # web_allowed for this turn (the current prompt asked for the web). Same enforcement reason as
        # read-only: OpenCode's per-agent permission is inert on the headless path, so stripping the
        # tool from the request is the only thing that stops the agent wandering off to fetch URLs.
        denied = set(READ_ONLY_DENIED) if (state.mode is Mode.ASK or state.read_only_turn) else set()
        # An answering turn also loses the task-list tool: it answers and returns without building, so
        # a task list on it reads as a build in progress that never arrives. A gated plan turn keeps it.
        if state.read_only_reason in ("ask", "question") or state.mode is Mode.ASK:
            denied |= TODO_TOOLS
        if not state.web_allowed:
            denied |= WEB_TOOLS
        if denied and "tools" in request:
            tools = [
                t for t in request["tools"]
                if (t.get("function") or {}).get("name", "").lower() not in denied
            ]
            request = {**request, "tools": tools}

        decision = llm_router.resolve(state, self._catalog)

        # The router's decision is the model, unconditionally. It used to apply only when locked,
        # when force_model was set, or when the caller sent no model — which meant that in domino
        # mode (nothing locked, force_model off, OpenCode always sending its configured model) the
        # decision was computed, logged and discarded on every request. Model assignments, Auto's
        # per-phase switching, the per-turn pick and the strong-model escalation were all inert;
        # everything ran on whatever opencode.json named. See _ensure_session, which has always
        # documented this as the contract: no session-level model, the shim decides per request.
        request = {**request, "model": decision.model}

        # Attached images against a non-vision model: strip them here rather than switch models or
        # let it fly. The resolved model is only known at this point (per request), and switching to
        # a vision model would defeat the sovereignty lock — sending sovereign data to a vendor to
        # read a screenshot is worse than not reading it. Passing it through is worse still:
        # bedrock-qwen3-coder (the default implement model) hard-400s, killing the turn.
        dropped = 0
        if not supports_vision(request["model"]) and isinstance(request.get("messages"), list):
            messages, dropped = _strip_images(request["messages"])
            if dropped:
                request = {**request, "messages": messages}

        log = logging.getLogger("sage.shim")
        log.info(
            "model policy: requested=%s -> resolved=%s (%s, phase=%s, locked=%s)",
            requested, request["model"], decision.reason.value, state.phase.value, decision.locked,
        )
        if dropped:
            log.info(
                "model policy: dropped %d image part(s) — %s cannot process images",
                dropped, request["model"],
            )

        # Cost-attribution tags (sent as X-LLM-Tag-sage-*, queryable in the gateway usage dashboard).
        # sovereign = an asset lock forced the model, the dimension worth isolating spend on.
        labels = CostLabels(
            phase=state.phase.value,
            mode="sovereign" if decision.locked else state.mode.value,
            component=self._component,
            session=session,
            version=_SAGE_VERSION,
            project_name=self._project_name,
        )
        return self._gateway.route(request, labels)
