"""EnforcementShim — consult the router, set `model`, tag, forward (DESIGN.md Seam 2).

Per request: consult the router, overwrite the `model` field with the decision, tag with
project + phase, forward to the gateway. This is a thin shim in front of the EXISTING
OpenAI-compatible Domino gateway, not a proxy built from scratch (SPEC.md C4).

Containment ("no direct-to-vendor") is provided by the container egress allowlist, NOT by
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
from ..router.models import (Mode, ModelCatalog, is_bedrock, reasoning_efforts_for,
                            supports_vision)
from ..router.phase_classifier import READ_ONLY_DENIED, TODO_TOOLS, WEB_TOOLS, assess
from .chat_paths import strip_denied_writes

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


def split_parallel_tool_calls(messages: list[Any]) -> list[Any]:
    """Rewrite one assistant message holding N tool calls into N single-tool-call exchanges.

    WORKAROUND for a Domino gateway bug, not something OpenAI-compatible clients should need. Bedrock's
    Converse API wants every `toolUse` in an assistant turn answered by `toolResult` blocks grouped
    into the ONE following user message, but the gateway's adapter (services/provider_adapter.py, the
    `role == "tool"` branch) emits a separate user message per tool result. With N>1 parallel calls the
    first is short the other ids and Bedrock rejects the whole request:

        ValidationException: Expected toolResult blocks at messages.6.content for the following Ids: …

    which the gateway then relays as a 200 with one error frame, so the turn dies inside OpenCode with
    no usable message. Serialising the calls sidesteps it: same calls, same order, same results, and
    each assistant toolUse is immediately followed by its own toolResult — the 1:1 shape the adapter
    does map correctly.

    Left alone when any result is missing (the in-flight turn, where the model is being asked to
    continue): splitting there would emit a toolUse with no toolResult, which is the very thing Bedrock
    rejects. Delete this once the gateway groups tool results.
    """
    out: list[Any] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        calls = m.get("tool_calls") if isinstance(m, dict) and m.get("role") == "assistant" else None
        if not calls or len(calls) < 2:
            out.append(m)
            i += 1
            continue
        # The tool results for these calls are the messages immediately following.
        j = i + 1
        results: dict[Any, Any] = {}
        while j < len(messages) and isinstance(messages[j], dict) and messages[j].get("role") == "tool":
            results[messages[j].get("tool_call_id")] = messages[j]
            j += 1
        if any(c.get("id") not in results for c in calls):
            out.extend(messages[i:j])
            i = j
            continue
        for n, call in enumerate(calls):
            fragment = {**m, "tool_calls": [call]}
            if n:
                fragment["content"] = None  # the assistant's prose belongs to the first fragment only
            out.append(fragment)
            out.append(results[call.get("id")])
        i = j
    return out


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
        shim would put product decisions inside the enforcement seam."""
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
        # interleaved turns route correctly step by step — not from a laggy background poll.
        # Reflect the phase back to the control so the UI's live indicator matches what routed.
        signals = None
        if state.chat_thread_id is None and state.mode is Mode.AUTO:
            # assess() scores BOTH directions: the write-flip down to the cheap model, and a rescue
            # back up to PLAN when the turn starts failing (see phase_classifier). `signals.phase`
            # is the resolved answer; `base_phase` is the write-flip rule alone, kept for the log.
            #
            # This shipped observe-only first and was flipped on 2026-08-13 once live builds showed
            # the signal fires on real failures (a vite build exiting 2) and stays silent across
            # five healthy turns.
            signals = assess(request.get("messages"))
            state = replace(state, phase=signals.phase)
            self._control.set_phase(signals.phase)

        # Read-only turns (Ask mode, or a plan turn held at the approval gate) get every write AND
        # shell tool stripped from the request, so the model is never offered one. This is the whole
        # read-only guarantee, not a best-effort layer on top of another: OpenCode's per-agent
        # `permission: {edit: deny, bash: deny}` does nothing on the headless server path (see
        # READ_ONLY_DENIED), so if a tool survives this filter, it runs. Shell matters most — that's
        # the hole that let Ask mode write files with `printf > file` for as long as it existed.
        # Chat turns are the opposite: they must write Artifacts, so write/bash stay, and
        # strip_denied_writes turns an out-of-path write into a tool error so the model retries
        # examples/<threadId>/ instead of src/. The files are also reverted on disk at turn end.
        # Web tools are default-denied on EVERY turn and only survive when the orchestrator armed
        # web_allowed for this turn (the current prompt asked for the web). Same enforcement reason as
        # read-only: OpenCode's per-agent permission is inert on the headless path, so stripping the
        # tool from the request is the only thing that stops the agent wandering off to fetch URLs.
        chat_id = state.chat_thread_id
        if chat_id:
            denied: set[str] = set()
        else:
            denied = set(READ_ONLY_DENIED) if (state.mode is Mode.ASK or state.read_only_turn) else set()
        # An answering turn also loses the task-list tool: it answers and returns without building, so
        # a task list on it reads as a build in progress that never arrives. A gated plan turn keeps it.
        if not chat_id and (state.read_only_reason in ("ask", "question") or state.mode is Mode.ASK):
            denied |= TODO_TOOLS
        if not state.web_allowed:
            denied |= WEB_TOOLS
        if denied and "tools" in request:
            tools = [
                t for t in request["tools"]
                if (t.get("function") or {}).get("name", "").lower() not in denied
            ]
            request = {**request, "tools": tools}
        if chat_id and isinstance(request.get("messages"), list):
            request = {**request, "messages": strip_denied_writes(request["messages"], chat_id)}

        decision = llm_router.resolve(state, self._catalog)

        # The router's decision is the model, unconditionally. It used to apply only when locked,
        # when force_model was set, or when the caller sent no model — which meant that in domino
        # mode (nothing locked, force_model off, OpenCode always sending its configured model) the
        # decision was computed, logged and discarded on every request. Model assignments, Auto's
        # per-phase switching, the per-turn pick and the strong-model escalation were all inert;
        # everything ran on whatever opencode.json named. See _ensure_session, which has always
        # documented this as the contract: no session-level model, the shim decides per request.
        request = {**request, "model": decision.model}

        # Function tools and `reasoning_effort` are mutually exclusive on chat/completions for the
        # GPT-5 family: the gateway answers 400 with "Function tools with reasoning_effort are not
        # supported for gpt-5.4 in /v1/chat/completions". Chat turns always carry tools, so with
        # gpt-5.4 as the Chat alias EVERY turn failed, down to "hi" — the same request succeeded the
        # moment it routed to sonnet, which advertises no efforts and so was never given one. The
        # field is dropped rather than sent as 'none' (the other half of the gateway's advice):
        # `none` is not in the enum the alias advertises, and an alias that does not advertise a
        # value 400s on it, which is the failure this whole block already guards against twice.
        # Cost is what is lost — the turn runs at the alias default. A tool-less Chat turn keeps it.
        tool_call = bool(request.get("tools"))

        # Chat-only: the user picked an effort for THIS alias. Do not send it when routing landed
        # on a different model — qwen-2-5 400s on unknown fields.
        if (
            not tool_call
            and state.chat_thread_id
            and state.reasoning_effort
            and state.chat_model
            and request["model"] == state.chat_model
        ):
            request = {**request, "reasoning_effort": state.reasoning_effort}
        elif not tool_call and state.chat_thread_id and "low" in reasoning_efforts_for(request["model"]):
            # Chat on Auto never reached the branch above: no pick means no effort, so a data
            # question was answered at the alias's own default — a full reasoning pass, paid before
            # the first token, on turns as small as "hi". Low is the floor for this kind of work;
            # someone who wants more picks it, and that pick still wins because it is tested first.
            # Gated the same way, and for the same reason: an alias that does not advertise the
            # field 400s on it.
            request = {**request, "reasoning_effort": "low"}

        # Handoff note. A rescued step lands on a different model mid-turn with the transcript but
        # no account of why it was called in — so it re-attempts the edit that just failed. Appended
        # as `system`, NOT `user`: _current_turn() treats a user message as a turn boundary, so
        # injecting one would reset the very error window that triggered the rescue and flap
        # straight back to the cheap model. Not persisted anywhere — OpenCode owns the history and
        # we only rewrite the outgoing request, so the note appears while rescued and is gone once
        # a write lands.
        if (signals is not None and signals.phase is not signals.base_phase
                and isinstance(request.get("messages"), list)):
            request = {**request, "messages": [*request["messages"], {
                "role": "system",
                "content": ("[sage] Routing note: earlier tool calls in this turn failed, so this "
                            "step is running on a different model. Work out why before editing "
                            "again — re-read the file you are changing and fix the cause, rather "
                            "than repeating the change that just failed."),
            }]}

        # Attached images against a non-vision model: strip them here rather than switch models or
        # let it fly. The resolved model is only known at this point (per request). Passing an image
        # through is worse: bedrock-qwen3-coder (the default implement model) hard-400s, killing
        # the turn.
        dropped = 0
        if not supports_vision(request["model"]) and isinstance(request.get("messages"), list):
            messages, dropped = _strip_images(request["messages"])
            if dropped:
                request = {**request, "messages": messages}

        # Bedrock-served models only: serialise parallel tool calls the gateway's adapter can't group.
        # Same reasoning as the image strip above — the resolved model is the earliest point this is
        # decidable, and it must run after the override or a request routed TO Bedrock would slip past.
        if is_bedrock(request["model"]) and isinstance(request.get("messages"), list):
            request = {**request, "messages": split_parallel_tool_calls(request["messages"])}

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
        # Logs whenever the scorer read a shell/write result at all, not just when it escalates: a
        # clean build and a build whose failure the markers missed have to be told apart, and
        # only-on-rescue made them both silent. `rescued=no` is the ordinary case. Samples are
        # head-and-tail only and capped in the classifier.
        if signals is not None and signals.examined:
            log.info(
                "model policy: rescue examined=%d errors=%d episodes=%d rescued=%s (%s) — %s",
                signals.examined, signals.errors_since_write, signals.rescues,
                decision.model if signals.phase is not signals.base_phase else "no",
                signals.reason, " | ".join(signals.samples),
            )

        # Cost-attribution tags (sent as X-LLM-Tag-sage-*, queryable in the gateway usage dashboard).
        labels = CostLabels(
            phase=state.phase.value,
            mode=state.mode.value,
            route_reason=(signals.reason
                          if signals is not None and signals.phase is not signals.base_phase
                          else None),
            component=self._component,
            session=session,
            version=_SAGE_VERSION,
            project_name=self._project_name,
        )
        return self._gateway.route(request, labels)
