"""EnforcementShim — consult the router, set `model`, tag, forward (DESIGN.md Seam 2).

Per request: consult the router, overwrite the `model` field with the decision, tag with
project + phase, forward to the gateway. This is a thin shim in front of the EXISTING
OpenAI-compatible Domino gateway, not a proxy built from scratch (SPEC.md C4).

Containment ("no direct-to-vendor") is provided by the container egress allowlist, NOT by
this code — this shim only guarantees the *policy* half (right model + tagging). See Step 1.4.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from ..build_policy import BuildPolicy
from ..gateway.capabilities import legacy
from ..gateway.client import CostLabels, GatewayClient, GatewayUpstreamError
from ..implementation_request import (
    IMPLEMENT_SECTIONS,
    apply_instruction_profile,
    assemble_for_route,
)
from ..router import llm_router
from ..router.model_control import ModelControl
from ..router.models import (
    EffortDecision,
    EffortSource,
    EffortStatus,
    Mode,
    ModelCatalog,
    Phase,
    Reason,
    is_bedrock,
    supports_vision,
)
from ..router.phase_classifier import (
    # ERROR_CORROBORATION and PATCH_TOOL were imported here for #494's patch withdrawal, retired
    # in #551. Both still live in phase_classifier with their meaning unchanged: the bar is still
    # the rescue's, and `patch_refusals` is still counted and logged.
    READ_ONLY_DENIED,
    READ_TOOLS,
    TODO_TOOLS,
    WEB_TOOLS,
    assess,
)
from ..tool_result_window import apply_tool_result_window
from . import keepalive as ka
from .chat_paths import apply_withheld, strip_denied_writes

# What the agent sees in place of an image its model can't accept. It must know an image WAS
# attached — a silently dropped part reads as "the user sent nothing", and the agent then invents
# what it thinks the screenshot showed instead of asking.
IMAGE_OMITTED = "[image omitted: the active model cannot process images]"
IMAGE_AMBIGUOUS = "[image omitted: reference markers did not match image carriers]"

_PLAN_LOOP_TOOLS = ("task", "todoread", "todowrite", "todo_read", "todo_write")


def _is_plan_loop_tool(name: str) -> bool:
    """Match OpenCode's bare task tools and any server-prefixed spelling of the same tools."""
    value = name.lower()
    return any(value == tool or value.endswith(("_" + tool, "-" + tool, "/" + tool))
               for tool in _PLAN_LOOP_TOOLS)


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


def _strip_current_images(messages: list[Any]) -> list[Any]:
    """Remove ambiguous carriers from only the request's last user message."""
    out = list(messages)
    index = next((i for i in range(len(out) - 1, -1, -1)
                  if isinstance(out[i], dict) and out[i].get("role") == "user"), None)
    if index is None:
        return out
    message = out[index]
    content = message.get("content")
    if not isinstance(content, list):
        return out
    out[index] = {**message, "content": [
        {"type": "text", "text": IMAGE_AMBIGUOUS}
        if isinstance(part, dict) and part.get("type") == "image_url" else part
        for part in content
    ]}
    return out


def _capture_refusal(stream: Iterator[bytes], request: dict[str, Any], on_refused) -> Iterator[bytes]:
    """Hand the payload to `on_refused` if — and only if — a guardrail is what refused it.

    This is the one place the refused payload exists and the refusal is known at the same moment.
    `route` is lazy, so the rewritten `request` is already alive in its frame; capturing here keeps
    it for a few seconds longer rather than retaining anything new. Capturing EARLIER, where
    `on_resolved` fires, would mean holding every payload permanently against the chance one is
    refused — a standing in-process copy of exactly what the guardrail exists to stop moving.

    Deliberately narrow. `guardrail_blocked` is the gateway's own machine field (measured: the body
    is 93 bytes and byte-identical whatever matched), so an auth failure or a bad model id never
    reaches the callback. Nothing is copied: the caller owns what it does with the list, and the
    orchestrator drops it as soon as the search is over.

    Hands back the MODEL as well as the messages, and that is not incidental: a guardrail can be
    attached per alias, so a search that probed a different alias than the one that was refused
    could come back clean on every subset and report that nothing was refused. Which aliases a
    guardrail covers belongs to the gateway administrator and changes — never encode that set here,
    probe it with `scripts/guardrail-probe.py`.

    A raising callback is logged loudly and swallowed. `on_resolved`'s site downgrades its failures
    to `log.debug`, which is right for telemetry and wrong here — a capture that fails silently
    turns into a search that finds nothing, with no way to tell that from a clean conversation.
    """
    try:
        yield from stream
    except GatewayUpstreamError as err:
        if on_refused is not None and "guardrail_blocked" in (err.body or ""):
            try:
                on_refused(str(request.get("model") or ""), request.get("messages"))
            except Exception:
                logging.getLogger("sage.shim").exception(
                    "shim: could not hand back the payload a guardrail refused")
        raise


def _frame_outcome(line: str) -> tuple[str, str | None] | None:
    payload = line.strip()
    if payload.startswith("data:"):
        payload = payload[5:].strip()
    if not payload or payload == "[DONE]" or payload.startswith("event:"):
        return None
    try:
        body = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    if body.get("error"):
        from ..liveread.data_use import gateway_failure_kind
        return "failed", gateway_failure_kind(body["error"])
    if body.get("type") in ("response.failed", "response.incomplete"):
        return "failed", "incomplete"
    if body.get("type") in ("response.created", "response.in_progress", "message_start"):
        return None
    return "responded", None


def _track_image_delivery(stream: Iterator[bytes], data_use, operation_ids: tuple[str, ...],
                          model: str) -> Iterator[bytes]:
    """Settle image delivery from parsed upstream response frames."""
    buffer = ""
    settled = False

    def inspect(line: str) -> None:
        nonlocal settled
        if settled:
            return
        outcome = _frame_outcome(line)
        if outcome is None:
            return
        state, kind = outcome
        if state == "failed":
            data_use.fail_image_delivery(operation_ids, model, kind or "gateway_error")
        else:
            data_use.confirm_image_delivery(operation_ids, model)
        settled = True

    try:
        for chunk in stream:
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                inspect(line)
            yield chunk
    except Exception:
        if not settled:
            data_use.fail_image_delivery(operation_ids, model, "gateway")
            settled = True
        raise
    finally:
        # A caller can close the generator after a setup-only frame. That is still a terminal exit:
        # do not leave the audit row pending until some later turn or process restart closes it.
        inspect(buffer)
        if not settled:
            data_use.fail_image_delivery(operation_ids, model, "no_response")


class _CloseAwareImageDelivery:
    """Close a delivery row even when the wrapped generator was never started."""

    def __init__(self, stream: Iterator[bytes], data_use, operation_ids: tuple[str, ...],
                 model: str):
        self.stream = stream
        self.data_use = data_use
        self.operation_ids = operation_ids
        self.model = model

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        return next(self.stream)

    def close(self) -> None:
        try:
            close = getattr(self.stream, "close", None)
            if close is not None:
                close()
        finally:
            # Closing an unstarted generator does not enter its body or run its finally block.
            # This outer boundary is therefore the only place that can settle a zero-pull exit.
            self.data_use.fail_image_delivery(self.operation_ids, self.model, "no_response")


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
        build_policy: BuildPolicy | None = None,
    ) -> None:
        self._control = control
        self._catalog = catalog
        self._gateway = gateway
        from ..liveread.data_use import DataUse
        self.data_use = DataUse()
        # component: the `sage-component` cost tag — which Sage process this shim serves. Lets cost
        # analysis separate real build inference (builder) from orchestration overhead (probe).
        self._component = component
        # project_name: the `sage-project` cost tag — which Sage deployment spent this, as
        # "<owner>/<project>" (see preview/prefix.py domino_project_label). It's what makes a build
        # findable in the gateway's usage dashboard; without it every Sage install shares one bucket.
        self._project_name = project_name
        self._build_policy = build_policy
        # The last (Conversation, Live read tools offered) pair logged, so the line below says
        # something on the turn it changes and nothing on the dozen requests inside one turn.
        self._live_read_offered: tuple[str, tuple[str, ...]] | None = None
        # The last (model, effort, carries-tools) triple whose effort was dropped, so the line below
        # says something when the answer changes and nothing on the requests that repeat it.
        self._effort_dropped: tuple[str, str, bool] | None = None
        # One pending progress note (#544), set by the orchestrator's poll loop and taken by the
        # next request. Locked, unlike the two dedupe slots above: those are written and read on
        # the same request-serving thread, and this one crosses from the turn's poll loop to that
        # thread. An unlocked read-and-clear can drop the note, and a dropped note is a soft limit
        # that silently did not fire.
        self._progress_note = ""
        self._progress_note_lock = threading.Lock()
        # One pending syntax note PER FILE (#547), set by the same poll loop and taken by the same
        # next request. A dict where the progress note above is a single string, and the difference
        # is the thing being carried: that note is a COUNT, where only the newest is worth telling,
        # and this one is a SET of distinct errors. Overwriting would drop four broken files out of
        # five and tell the model to fix one per turn across five turns, which is the loop this
        # exists to end. Keyed by file, so a file written twice reports once, at its latest state.
        self._syntax_notes: dict[str, str] = {}
        self._syntax_note_lock = threading.Lock()
        self.resolve_capability = legacy

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

    def note_no_progress(self, calls: int) -> None:
        """Queue one progress note for the next request this shim serves (#544).

        The turn has landed a change and has since made `calls` tool calls that changed nothing.
        Overwrites rather than queues: only the newest count is worth telling the model, and an
        unread note from a window that has already reset is worse than none.
        """
        with self._progress_note_lock:
            self._progress_note = (
                f"you have made {calls} tool calls since you last changed a file, and none of "
                "them changed anything. Sage checks the app itself after this turn — it runs the "
                "typecheck and opens the preview — so you do not need to verify it. Make the next "
                "change you intend to make, or finish the turn now."
            )

    def _take_progress_note(self) -> str:
        with self._progress_note_lock:
            note, self._progress_note = self._progress_note, ""
        return note

    def note_syntax_error(self, file: str, detail: str) -> None:
        """Queue one file's failed syntax check for the next request this shim serves (#547).

        `detail` is already the `file:line:col CODE: message` the repair turn is told, so the model
        reads the same shape from the check that ran the moment it wrote the file and from the one
        that runs after the turn.
        """
        with self._syntax_note_lock:
            self._syntax_notes[file] = detail

    def _take_syntax_note(self) -> str:
        with self._syntax_note_lock:
            notes, self._syntax_notes = self._syntax_notes, {}
        if not notes:
            return ""
        # One wording for one file and for several: a note that has to pluralise is a note with a
        # branch nobody tests.
        return ("Sage checks each file as your write lands, and these do not parse:\n"
                + "\n".join(f"- {detail}" for detail in notes.values())
                + "\nFix this before you write anything else.")

    def handle(self, request: dict[str, Any], project: str, session: str | None = None,
               on_resolved=None, on_refused=None) -> Iterator[bytes]:
        """OpenAI-compatible request in, streamed response out. OpenCode points at this."""
        request, labels, used, capability, _effort = self.prepare(
            request, project, session, on_resolved)
        image_delivery, strip_current_images = self.data_use.begin_image_delivery(
            request, request["model"], capable=supports_vision(request["model"])
        )
        if strip_current_images and isinstance(request.get("messages"), list):
            request = {**request, "messages": _strip_current_images(request["messages"])}
        from ..gateway.protocol import Protocol
        try:
            if capability.protocol is not Protocol.CHAT:
                from .native import text_stream
                stream = text_stream(self._gateway, request, labels, capability)
            else:
                stream = self._gateway.route(request, labels)
        except Exception:
            self.data_use.fail_image_delivery(image_delivery, request["model"], "gateway")
            raise
        stream = _capture_refusal(stream, request, on_refused)
        stream = _track_image_delivery(
            stream, self.data_use, image_delivery, request["model"]
        )
        observed = self.data_use.observe(stream, request, used)
        return _CloseAwareImageDelivery(
            observed, self.data_use, image_delivery, request["model"]
        )

    def prepare(self, request: dict[str, Any], project: str, session: str | None = None,
                on_resolved=None, *, native: bool = False, rewrite_counts=None):
        """Route the request and resolve its capability, ready for a protocol to stream it.

        `project` is kept for the log line only — the gateway captures the caller's Domino project
        as a first-class column, so it's not tagged (a `project` tag would be dropped). `session` is
        the OpenCode session id, tagged as sage-session for per-build cost rollup.

        `on_resolved(model, phase, reason)` is called once the router has decided, so the caller's
        record names the model the request actually ran on rather than the one OpenCode asked for —
        every request asks for the same placeholder, and the override is the whole point of the
        shim. Optional and swallowed: a recorder must never be able to fail an inference.

        `reason` is `Reason`'s own value, and it is handed over rather than left to be re-derived
        because it cannot be re-derived: four rules move a request off the picked model and three of
        them land on the same alias, so "ran on gpt-5.4" alone does not say whether the person's
        pick was honoured, overruled by the Ask pin, or dropped by the veto (#316). It is the reason
        AS REBOUND below — the veto's rebind included — for the reason that rebind exists: the
        router's `resolve_unsigned` returns the reason of the path it fell back to, which is the
        path an ordinary turn with no pick at all takes.

        `phase` is empty for the turns that have none — Chat and Ask; see the call site.
        """
        requested = request.get("model")
        state = self._control.snapshot()

        # Per-step phase: in Auto mode, classify THIS inference from its own message tail (plan
        # while reasoning/reading, implement while writing code). Done here, per request, so
        # interleaved turns route correctly step by step — not from a laggy background poll.
        # Reflect the phase back to the control so the UI's live indicator matches what routed.
        signals = None
        # SCORED for every non-Chat turn; APPLIED only in Auto. Until #498 the scoring itself was
        # behind the Auto test, which was harmless only while Auto was the only mode that got here.
        # Approvals now pin Implement, and a pinned turn was silently losing both things assess()
        # pays for: the rescue, and #494's `apply_patch` withdrawal. A withdrawal is a fact about
        # the TOOL and is true in any mode; a phase is a fact about ROUTING and only Auto routes by
        # it. Ask reaches this too and is a no-op by construction — write and shell tools are
        # stripped from that request, so there are no results for assess() to examine.
        if state.chat_thread_id is None:
            # assess() scores BOTH directions: the write-flip down to the cheap model, and a rescue
            # back up to PLAN when the turn starts failing (see phase_classifier). `signals.phase`
            # is the resolved answer; `base_phase` is the write-flip rule alone, kept for the log.
            #
            # This shipped observe-only first and was flipped on 2026-08-13 once live builds showed
            # the signal fires on real failures (a vite build exiting 2) and stays silent across
            # five healthy turns.
            signals = assess(request.get("messages"))
            if state.mode is Mode.AUTO:
                state = replace(state, phase=signals.phase)
                self._control.set_phase(signals.phase)
        # A rescue is a change of MODEL, so it only happened where the phase is what routes. In a
        # pinned mode the classifier can still want PLAN while the slot does not move, and a line or
        # a note that says otherwise is exactly the lie #494 rewrote this log to stop telling.
        rescued_phase = (signals is not None and state.mode is Mode.AUTO
                         and signals.phase is not signals.base_phase)

        # Read-only turns (Ask mode, or a plan turn held at the approval gate) get every write AND
        # shell tool stripped from the request, so the model is never offered one. This is the whole
        # read-only guarantee, not a best-effort layer on top of another: OpenCode's per-agent
        # `permission: {edit: deny, bash: deny}` does nothing on the headless server path (see
        # READ_ONLY_DENIED), so if a tool survives this filter, it runs. Shell matters most — that's
        # the hole that let Ask mode write files with `printf > file` for as long as it existed.
        # Chat turns usually do the opposite: they may write Artifacts, so write/bash stay, and
        # strip_denied_writes turns an out-of-path write into a tool error so the model retries
        # examples/<threadId>/ instead of src/. The files are also reverted on disk at turn end.
        # A Chat turn that was explicitly armed read-only is not an Artifact turn; it is a plain
        # answer. It keeps Chat's Thread scoping but inherits the same no-shell/no-write guarantee
        # as Ask, or one simple question can become a multi-step agent loop.
        # Web tools are default-denied on EVERY turn and only survive when the orchestrator armed
        # web_allowed for this turn (the current prompt asked for the web). Same enforcement reason as
        # read-only: OpenCode's per-agent permission is inert on the headless path, so stripping the
        # tool from the request is the only thing that stops the agent wandering off to fetch URLs.
        chat_id = state.chat_thread_id
        if chat_id and state.read_only_turn and state.read_only_reason == "greeting":
            # Exact greetings need no tools. Keep the model, history and output budget; omit only
            # unused schemas and their choice directive for this turn (#417).
            request = {k: v for k, v in request.items() if k not in {"tools", "tool_choice"}}
        denied = set(READ_ONLY_DENIED) if (state.mode is Mode.ASK or state.read_only_turn) else set()
        plan_tools_removed: dict[str, int] = {}
        # An answering turn also loses tools that create a visible work loop: it answers and returns
        # without building, so a task list or sub-task on it reads as a build in progress that never
        # arrives. A gated plan removes the same loop tools below: it owes one written plan.
        if state.read_only_reason in ("ask", "question") or state.mode is Mode.ASK:
            denied |= TODO_TOOLS | {"task"}
        # A Chat turn answers and returns too, and it cannot be the build the list implies: Chat's
        # writes are confined to the Thread's examples/ and .sage/threads/ dirs, so no Chat turn can
        # touch src/ however many steps it opens. It is NOT expressible above — an unbounded Chat
        # data turn is not read-only at all, so its `read_only_reason` is "", the same value an
        # ordinary Build turn carries, and that turn must keep its list. `chat_thread_id` is the
        # axis that separates them, and the idiom `chat_thread_id or mode is ASK` is already how
        # this file (the `on_resolved` phase blank) and llm_router's `_lock_preferences` say
        # "answers rather than builds". Measured live 2026-09-18: a 21-call Chat investigation turn
        # spent call 4 on `todowrite` (#400).
        #
        # Only the task list. `task` stays behind deliberately: the 2026-09-14 latency work tried
        # hiding `skill`, `task` and `todowrite` from Chat together and that profile was rejected
        # and restored, on the ground that it lost the installed skill catalogue and prevented
        # delegation (docs/performance/2026-09-14-latency.md). Neither loss is the task list, so
        # that rejection does not reach `todowrite` — but it names `task` directly, and a sub-task
        # is real work being done rather than a promise of work to come. An Ask/question turn still
        # loses both, above; this line does not narrow that.
        if chat_id:
            denied |= TODO_TOOLS
        if not state.web_allowed:
            denied |= WEB_TOOLS
        # #494 used to withdraw `apply_patch` here after two corroborated refusals, so the model
        # would fall back on `edit`. RETIRED (#551, owner's call 2026-09-24), and deliberately not
        # replaced. #539 solves the same problem one layer earlier and structurally: OpenCode picks
        # the edit tools from the model HANDLE, so a GPT turn is offered `apply_patch` and no
        # `edit`/`write`, and every other turn is offered `edit`/`write` and no `apply_patch`. The
        # withdrawal required BOTH on one request, which OpenCode cannot emit — it could not fire,
        # and on the only requests that can refuse a patch it would have taken away the turn's one
        # edit tool and left it unable to edit at all.
        #
        # The DETECTOR stays. `signals.patch_refusals` is still counted and still printed on the
        # rescue line below, because a GPT model that repeatedly refuses its own envelope has never
        # been observed and would need to be seen before anything is built for it. Acting on the
        # handle — two refusals stop sending `gpt-`, so OpenCode offers `edit`/`write` — is the
        # route that would give such a turn a real escape; it was considered and refused as
        # speculative. If the log line ever shows it happening, that is the evidence to build on.
        # Do not re-add a runtime tool-stripper here.
        if denied and "tools" in request:
            tools = []
            for tool in request["tools"]:
                name = str((tool.get("function") or {}).get("name", "")).lower()
                plan_loop = state.read_only_reason == "plan" and _is_plan_loop_tool(name)
                if name in denied or plan_loop:
                    if state.read_only_reason == "plan":
                        plan_tools_removed[name] = plan_tools_removed.get(name, 0) + 1
                    continue
                tools.append(tool)
            request = {**request, "tools": tools}
        if state.chat_artifact_turn and chat_id and isinstance(request.get("tools"), list):
            # `delegated_model_call` belongs on a data-artifact turn and not by extension: the turn
            # #370 opens on IS one — a classification pass over support-case text, ending in a
            # table. This list is an allowlist, so a tool absent from it is stripped, and leaving it
            # out would take the capability away from exactly the turns that want it (ADR-0057).
            #
            # `live_read_query` is here for #402, and it is the half of ADR-0058 that this list
            # makes a separate edit. The read-only lane reaches its tools by a DENYLIST above —
            # `READ_ONLY_DENIED` strips write and shell, and anything unnamed survives — so a new
            # read tool arrives there by doing nothing. This lane is an ALLOWLIST, so the same tool
            # arrives here only by being written down. Two unlike mechanisms, one-entry difference:
            # a change that gave the SQL tool to the read-only path and stopped would have shipped a
            # `data_artifact` turn that still cannot compute, which is exactly what #402 filed.
            #
            # It matters more here than on the other lane. A read-only turn that cannot compute
            # answers in prose and disappoints; an artifact turn has already been labelled
            # `data_artifact`, armed `artifact_write`, and been told by its own prompt to write the
            # table with it — and then has no number to put in one (#425).
            allowed = READ_TOOLS | {"glob", "grep", "live_read_table", "live_read_files",
                                    "live_read_query", "artifact_write", "delegated_model_call"}
            if state.web_allowed:
                allowed |= WEB_TOOLS
            request = {**request, "tools": [
                tool for tool in request["tools"]
                if (name := str((tool.get("function") or {}).get("name", "")).lower()) in allowed
                # The NAMESPACED spellings, which are a second list and not a restatement of the
                # one above: OpenCode prefixes an MCP tool with its `opencode.json` key before
                # offering it to the model, so `live_read_query` and
                # `sage-live-read_live_read_query` are two names for one tool and only the bare one
                # is in `allowed`. Adding a live-read tool to this lane is therefore TWO entries.
                # Miss the second and the tool is stripped from the model's list on this lane only,
                # which reads exactly like the tool not existing.
                or name in {"sage-live-read_live_read_table", "sage-live-read_live_read_files",
                            "sage-live-read_live_read_query"}
            ]}
        elif isinstance(request.get("tools"), list):
            # `artifact_write` is scoped to the artifact lane. `delegated_model_call` is scoped to a
            # CHAT turn, which is what `chat_id` says: its step line and its receipt are both wired
            # on the Chat turn, so a Build turn that called it would be gated, counted and capped
            # and would still spend with nothing on screen and nothing in the transcript — the
            # silence ADR-0057's last two bounds exist to stop. Build mints a valid turn token of
            # its own (`service.py`'s build prompt), so this is a reachable path and not a
            # hypothetical one; if Build should have the capability it needs those two bounds first,
            # not this line removed.
            outside = {"artifact_write"} if chat_id else {"artifact_write", "delegated_model_call"}
            request = {**request, "tools": [tool for tool in request["tools"]
                if (tool.get("function") or {}).get("name", "").lower() not in outside]}
        if chat_id and isinstance(request.get("tools"), list):
            # Whether the model was actually OFFERED Live read, which nothing else can say. Sage has
            # told people it could not see their data — naming the `sage-live-read_` tools from its
            # own prompt as "not available in this turn" — while `opencode mcp list` reported the
            # server connected and /api/diag reached it and listed both tools. Every one of those
            # can be true at once: the tool list is fixed when the turn starts, and the MCP
            # handshake for a session has been seen landing half a minute after that. This request
            # is the only place the list the model actually got is written down.
            names = sorted(str((t.get("function") or {}).get("name", ""))
                            for t in request["tools"])
            offered = [n for n in names if "live_read" in n.lower()]
            # The WHOLE list beside the verdict, because "NOT OFFERED" alone cannot say which of two
            # very different things happened. Live on 2026-09-09 three turns read NOT OFFERED while
            # the server was connected and the config was clean, and the next question — did
            # OpenCode send NO MCP tools, or send them under names nothing here recognises — had
            # nothing on any surface to answer it. This is the list the model actually got: it is
            # read AFTER the denial filter above, so it is what went to the gateway, not what
            # OpenCode proposed. Names only. An argument would be the file being written.
            #
            # Deduped on the whole list rather than the Live read part of it: a turn whose tools
            # changed in some other way is a different fact, and must not be silenced by the first
            # turn that happened to be missing the same two.
            if self._live_read_offered != (chat_id, tuple(names)):
                self._live_read_offered = (chat_id, tuple(names))
                logging.getLogger("sage.shim").info(
                    "chat tools: live read %s — all %d: %s",
                    ", ".join(offered) or "NOT OFFERED", len(names), ", ".join(names))
        if chat_id and isinstance(request.get("messages"), list):
            request = {**request, "messages": strip_denied_writes(request["messages"], chat_id)}

        # Content this Conversation has stopped sending, because the gateway's guardrail refuses it
        # and would otherwise refuse every later turn along with it (ADR-0022).
        #
        # Deliberately OUTSIDE the `chat_id` guard above. A Build turn carries no Chat thread id and
        # needs this exactly as much; keying on the armed set instead of on the surface is what makes
        # Chat and Build one code path rather than two implementations that drift.
        #
        # Replaces content, never drops a message: a `role:"tool"` message removed while its
        # `tool_call` stays behind is an HTTP 400 on gpt-5.4, sonnet, haiku and gemini alike
        # (measured). That also keeps this step invisible to `unsigned_tool_messages` and
        # `split_parallel_tool_calls` below, which read ids and roles rather than content.
        if state.withheld and isinstance(request.get("messages"), list):
            request = {**request, "messages": apply_withheld(request["messages"], state.withheld)}

        decision = llm_router.resolve(state, self._catalog)

        # The router's decision is the model, unconditionally. It used to apply only when locked,
        # when force_model was set, or when the caller sent no model — which meant that in domino
        # mode (nothing locked, force_model off, OpenCode always sending its configured model) the
        # decision was computed, logged and discarded on every request. Model assignments, Auto's
        # per-phase switching, the per-turn pick and the strong-model escalation were all inert;
        # everything ran on whatever opencode.json named. See _ensure_session, which has always
        # documented this as the contract: no session-level model, the shim decides per request.
        request = {**request, "model": decision.model}

        log = logging.getLogger("sage.shim")

        # ADR-0032: the veto. Signing is a session-level contract, and a tool-call message whose
        # FIRST call is unsigned is a hard 400 on the WHOLE request for a signing model (verified
        # live, #155). The router's pin keeps a session single-model going forward; this catches the
        # sessions it cannot help — one poisoned before the pin shipped, one a rescue escalation took
        # out of the signing model for good, or one where the user changed their pick mid-session.
        #
        # Read from the history rather than from what we remember resolving. `_recover_session`
        # resurrects a session out of opencode.db with the poison intact and our memory empty, which
        # is the exact path this bug lives on; and a text-only turn from another model leaves nothing
        # to reject, which a memory of "we resolved sonnet once" would latch on anyway.
        #
        # Runs BEFORE _strip_images and split_parallel_tool_calls, both of which read
        # request["model"] to decide whether to rewrite history.
        if ka.unsigned_tool_messages(request["model"], request.get("messages")):
            fallback = llm_router.resolve_unsigned(state, self._catalog)
            log.info(
                "model policy: %s refused, this session's history carries unsigned tool calls "
                "(#155) — %s",
                request["model"],
                f"falling back to {fallback.model}" if fallback is not None
                else "every assignable slot signs, so there is nowhere safe to go; sending anyway",
            )
            if fallback is not None:
                request = {**request, "model": fallback.model}
                # Rebind the decision, do not just edit the request. Everything downstream reads it
                # — the model-policy summary and the rescue line — and a summary that says
                # "resolved=gpt-5.4 (signing-pin)" contradicts itself: signing-pin means pinned TO
                # the signing model. Verified live 2026-09-04, which is how this was caught.
                decision = replace(fallback, reason=Reason.SIGNING_VETO)

        # The reasoning effort, for Build and Chat alike. Three questions, in this order, and the
        # order is the rule: WHICH level (the decision's, never the slot's), then may this MODEL
        # take it, then may it take it on a request of this SHAPE.
        #
        # 1. The decision carries it (ADR-0049). The router re-resolves per request, so the model on
        #    the wire is not always the model an effort was chosen for — the signing pin, the veto
        #    and the lock all move it. This replaced `request["model"] == state.chat_model`, which
        #    was the same rule asked of Chat alone; written per slot it would be three comparisons
        #    drifting two ways.
        # 2. The measured table decides acceptance, per alias (#280). This is also the ONLY
        #    re-validation a stored effort ever gets: `set_catalog` checks the level against the
        #    model the slot ran at save time, and a deployment default can move under it long
        #    afterwards (see `_effective_catalog`). Dropped rather than sent, because the person's
        #    build is worth more than their stale level — two aliases 400 on a level they do not
        #    list and qwen-2-5 400s on the field itself, and a 400 here kills the whole turn.
        # 3. Tools narrow it for gpt-5.4 only ("Function tools with reasoning_effort are not
        #    supported for gpt-5.4 in /v1/chat/completions") — with gpt-5.4 as the Chat alias EVERY
        #    turn failed, down to "hi". Narrowed by alias and not by request shape alone, which is
        #    what the guard before this did: it dropped the field from every tool-carrying turn for
        #    every alias, so a Build plan phase — always tool-carrying — could never send one, and
        #    gemini's measured 200 with tools and all was thrown away with it.
        tool_call = bool(request.get("tools"))
        capability = self.resolve_capability(request["model"])
        accepted = capability.efforts_with_tools if tool_call else capability.efforts
        # Whatever the caller sent is not an answer to any of the three. `model` is overwritten
        # above on every request, so an incoming effort was chosen for a model that is no longer on
        # the wire — the exact stale pairing the rest of this block exists to prevent, arriving
        # through the door instead of off the disk. Dropped rather than checked: this seam decides
        # the effort, and honouring one from outside would make that untrue on the one path
        # (OpenCode's own config, or a direct POST to /v1/chat/completions) nothing here can see.
        if "reasoning_effort" in request:
            request = {k: v for k, v in request.items() if k != "reasoning_effort"}

        source = decision.effort_source
        configured = decision.effort
        # A missing BuildPolicy is the legacy/test seam. Production always injects the one loaded
        # policy. Keeping this seam on provider default avoids silently changing standalone shim
        # callers that do not represent an armed Build turn.
        if source is EffortSource.STAGE_DEFAULT:
            if self._build_policy is None:
                source = EffortSource.PROVIDER_DEFAULT
                configured = None
            elif state.phase is Phase.PLAN:
                configured = self._build_policy.plan_reasoning_effort
            else:
                configured = self._build_policy.implement_reasoning_effort

        effort = configured
        status = (EffortStatus.PROVIDER_DEFAULT
                  if source is EffortSource.PROVIDER_DEFAULT
                  else EffortStatus.APPLIED)
        if effort is not None and effort not in accepted:
            if source is not EffortSource.STAGE_DEFAULT and (native or capability.identity):
                # Names no menu row. The way back out of a saved level is spelled "Automatic" on a
                # Build plan or implement row and "Model default" on the Chat and Ask one since
                # #545, and this seam serves both — so it says what to DO, which is the same act on
                # either surface, rather than a label that would be wrong on one of them.
                raise ValueError(f"{request['model']} cannot use the saved reasoning setting {effort!r}. "
                                 "Clear it or choose a supported setting. " + capability.reason)
            # Said out loud. A dropped effort is a silent bill — the turn runs at the alias's own
            # default, costs more or thinks less than the person asked for, and looks exactly like a
            # turn nobody configured. This line is what tells a stale stored level from a slot that
            # was never given one.
            #
            # Deduped like the Live read line above, and for the same reason: an unacceptable stored
            # level is a STANDING fact, so an unkeyed line repeats on every inference of every turn
            # for the life of the assignment and buries the turn it first appeared on.
            if (source is not EffortSource.STAGE_DEFAULT
                    and self._effort_dropped != (request["model"], effort, tool_call)):
                self._effort_dropped = (request["model"], effort, tool_call)
                log.info(
                    "model policy: dropping reasoning_effort=%s — %s accepts %s%s",
                    effort, request["model"], ", ".join(accepted) or "no effort",
                    " on a request carrying tools" if tool_call else "",
                )
            effort = None
            status = EffortStatus.UNSUPPORTED
        # Still the only place the field is added, and still no fallback of its own: `configured`
        # above is the whole answer, and a hidden level chosen here would contradict both the picker
        # and the saved assignment. What changed with #545 is only where an UNSET level resolves —
        # the stage supplies one for a Build plan or implement turn, a dozen lines up and in the
        # open, while Chat and Ask still send no field at all.
        if effort is not None:
            request = {**request, "reasoning_effort": effort}
        effort_decision = EffortDecision(
            configured_effort=configured,
            source=source,
            effective_effort=effort,
            status=status,
        )
        log.info(
            "model effort: model=%s phase=%s configured=%s effective=%s source=%s status=%s",
            request["model"], state.phase.value, configured, effort,
            source.value, status.value,
        )
        # Handoff note. A rescued step lands on a different model mid-turn with the transcript but
        # no account of why it was called in — so it re-attempts the edit that just failed. Appended
        # as `system`, NOT `user`: _current_turn() treats a user message as a turn boundary, so
        # injecting one would reset the very error window that triggered the rescue and flap
        # straight back to the cheap model. Not persisted anywhere — OpenCode owns the history and
        # we only rewrite the outgoing request, so the note appears while rescued and is gone once
        # a write lands.
        #
        # The note tells the truth about what changed. "Running on a different model" was written
        # when the plan and implement slots always held two models; with one model in both, the
        # rescue resolves to the model that just failed, and a note claiming otherwise sends it
        # looking for a difference that is not there.
        #
        # There was a third branch here, for #494's patch withdrawal: it told the model the patch
        # tool was gone and to use `edit` instead. It went with the withdrawal (#551). It could not
        # be reached, and on the only turns that refuse a patch it would have named `edit` at a
        # model OpenCode never offered `edit` to.
        if rescued_phase and isinstance(request.get("messages"), list):
            same_model = self._catalog.plan == self._catalog.implement
            if same_model:
                what = ("earlier tool calls in this turn failed. Work out why before editing again "
                        "— re-read the file you are changing and fix the cause, rather than "
                        "repeating the change that just failed.")
            else:
                what = ("earlier tool calls in this turn failed, so this step is running on a "
                        "different model. Work out why before editing again — re-read the file you "
                        "are changing and fix the cause, rather than repeating the change that just "
                        "failed.")
            request = {**request, "messages": [*request["messages"], {
                "role": "system", "content": f"[sage] Routing note: {what}",
            }]}

        # The progress budget's soft half (#544). Its own block rather than a third branch above:
        # the two notes answer different questions and a turn can legitimately earn both, so
        # folding them into one `what` would silently drop whichever lost.
        #
        # `system`, NOT `user`, for the reason the routing note is: `_current_turn` treats a user
        # message as a turn boundary, so a `user` note would truncate the very window the phase
        # classifier and the rescue signals read — and the rescue would flap on the next request.
        #
        # Taken only once there is somewhere to put it. A request whose `messages` is not a list is
        # a shape this cannot append to, and consuming the note there would spend the one warning
        # the turn gets on a request that never carried it.
        if isinstance(request.get("messages"), list):
            progress_note = self._take_progress_note()
            if progress_note:
                request = {**request, "messages": [*request["messages"], {
                    "role": "system", "content": f"[sage] Progress note: {progress_note}",
                }]}

        # The per-file syntax check (#547), a third block for the reason the second one is its own:
        # these notes answer different questions, a turn can earn more than one, and folding them
        # together would silently drop whichever lost. `system` rather than `user` for the reason
        # given above — `_current_turn` treats a user message as a turn boundary — and taken only
        # where there is somewhere to put it, so the one warning is not spent on a request that
        # could not carry it.
        if isinstance(request.get("messages"), list):
            syntax_note = self._take_syntax_note()
            if syntax_note:
                request = {**request, "messages": [*request["messages"], {
                    "role": "system", "content": f"[sage] Syntax check: {syntax_note}",
                }]}

        request = self.data_use.apply_restrictions(
            request, withheld=state.withheld, rewrite_counts=rewrite_counts)

        # Attached images against a non-vision model: strip them here rather than switch models or
        # let it fly. The resolved model is only known at this point (per request). Passing an image
        # through is worse: bedrock-qwen3-coder (the default implement model) hard-400s, killing
        # the turn.
        dropped = 0
        vision_capable = supports_vision(request["model"])
        if not vision_capable and isinstance(request.get("messages"), list):
            messages, dropped = _strip_images(request["messages"])
            if dropped:
                request = {**request, "messages": messages}
        # Bedrock-served models only: serialise parallel tool calls the gateway's adapter can't group.
        # Same reasoning as the image strip above — the resolved model is the earliest point this is
        # decidable, and it must run after the override or a request routed TO Bedrock would slip past.
        if is_bedrock(request["model"]) and isinstance(request.get("messages"), list):
            request = {**request, "messages": split_parallel_tool_calls(request["messages"])}

        # A route that HAS a gateway identity but no matching proof is on the CHAT default because
        # nothing is known about it, not because CHAT was measured (#509). Keyed on `identity` and
        # not on `verified` alone: `legacy` — the fake/development contract, and this shim's own
        # default — is unverified by construction and carries no identity, so a dev turn must not
        # raise this. Said at `warning` so it reaches the warn tail and the Workspace Logs panel,
        # which is where somebody looking at a dead turn will actually meet it.
        unverified = bool(capability.identity) and not capability.verified
        log.log(
            logging.WARNING if unverified else logging.INFO,
            "model policy: requested=%s -> resolved=%s (%s, phase=%s, locked=%s, effort=%s)%s",
            requested, request["model"], decision.reason.value, state.phase.value, decision.locked,
            # What went on the wire, not what the decision proposed: the two differ whenever a level
            # was dropped or the Chat floor answered. Named here and not only on the drop path,
            # because a turn running at an effort nobody expected is the same question asked from
            # the other side, and the drop line is deduped — after the first one it says nothing.
            #
            # The default must not contain the word `none`, which is a LEVEL here. This read
            # "none sent" and was taken as "none was sent" for an hour of #505 — the exact
            # distinction that issue turns on, since `none` explicitly sent and the field omitted
            # are the two requests gpt-5.4 answers differently.
            request.get("reasoning_effort", "<absent>"),
            f" ROUTE UNVERIFIED — {capability.reason}" if unverified else "",
        )
        if on_resolved is not None:
            try:
                # No phase for the two kinds of turn that have none, and that is not a tidy-up:
                # `state.phase` is only a fact about THIS turn where something set it for this turn.
                # Measured against a control left in IMPLEMENT by an Auto build:
                #
                #   Chat        -> phase=implement   (the classifier above skips Chat entirely)
                #   Ask mode    -> phase=implement   (it skips non-Auto too, and `_sync_phase` has
                #                                     no branch for ASK)
                #   Plan mode   -> phase=plan        (`ModelControl._sync_phase` set it)
                #   Implement   -> phase=implement   (likewise)
                #   Auto        -> phase=plan        (the classifier reclassified it)
                #
                # So only the first two are stale, and only they are blanked. Blanking the pinned
                # modes as well would throw away a phase their own `set_mode` just made true — the
                # over-wide fix, and the one that looks more consistent.
                on_resolved(request["model"],
                            "" if (state.chat_thread_id or state.mode is Mode.ASK)
                            else state.phase.value,
                            decision.reason.value)
            except Exception:
                # Swallowed, because a recorder must never be able to fail an inference — but said
                # at `warning` and named for what it is. This callback stopped being telemetry when
                # it started feeding the turn's terminal row (#316): a failure here does not merely
                # lose a waterfall line, it drops the `resolved` key from a row whose readers are
                # told to take a missing key as "no model ran". That is a false statement in the
                # product's voice, and it used to be reported at `debug` under the word "timing".
                log.warning("model policy: on_resolved failed — this turn's record will name no "
                            "model although one ran", exc_info=True)
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
            # `rescued=` names what the rescue CHANGED. With one model on both slots it changed
            # nothing, and a line that printed that model looked like a rescue firing on every
            # call for thirteen minutes (#494). `patch=` is the other lever, on the same line.
            if signals.phase is signals.base_phase:
                rescued = "no"
            elif not rescued_phase:
                # The classifier wanted PLAN and the mode is pinned, so no model moved (#498).
                # Naming which of the two it was matters: "no" alone reads as "no signal", and
                # anything warmer reads as a rescue that never happened.
                rescued = f"no (mode pinned to {state.mode.value})"
            elif self._catalog.plan == self._catalog.implement:
                rescued = f"no-op (both slots are {decision.model})"
            else:
                rescued = decision.model
            log.info(
                "model policy: rescue examined=%d errors=%d episodes=%d rescued=%s (%s) "
                "patch_refusals=%d apply_patch=%s — %s",
                signals.examined, signals.errors_since_write, signals.rescues, rescued,
                # The field is kept and is now a constant: nothing withdraws `apply_patch` any more
                # (#551). `patch_refusals` beside it is the half that still measures something.
                signals.reason, signals.patch_refusals, "offered",
                " | ".join(signals.samples),
            )

        # What the tool calls in this request look like on the way OUT, grouped by message. Behind
        # the debug flag: the unsigned-first-call shape used to be warned about here, and the veto
        # above now refuses it instead, so there is nothing left to warn about unconditionally.
        if ka.debug_stream_enabled():
            sig_entries = ka.tool_call_signatures(request.get("messages"))
            if sig_entries:
                shown = sig_entries[:ka.DEBUG_REQUEST_MAX_CALLS]
                log.info(
                    "outgoing tool calls, by message (%d%s): %s",
                    len(sig_entries),
                    f", first {len(shown)} shown" if len(shown) < len(sig_entries) else "",
                    " | ".join(shown),
                )

        # Cost-attribution tags (sent as X-LLM-Tag-sage-*, queryable in the gateway usage dashboard).
        labels = CostLabels(
            phase=state.phase.value,
            mode=state.mode.value,
            route_reason=(signals.reason if rescued_phase else None),
            component=self._component,
            session=session,
            version=_SAGE_VERSION,
            project_name=self._project_name,
        )
        request, used = self.data_use.prepare(
            request, withheld=state.withheld, rewrite_counts=rewrite_counts)
        build_profile = None
        if (state.chat_thread_id is None and state.read_only_reason == "plan"):
            request, build_profile = apply_instruction_profile(
                request, "plan", removed_tools=plan_tools_removed)
        elif (state.chat_thread_id is None and not state.read_only_turn
              and (state.mode is Mode.IMPLEMENT
                   or state.mode is Mode.AUTO and state.phase is Phase.IMPLEMENT)):
            # Every optional section, deliberately: the grammar can now withhold `design` and
            # `platform`, but nothing has yet been chosen to decide WHICH turn needs them, and a
            # default of "withhold" would delete that guidance from every implement turn rather
            # than defer it. Today's prompt is therefore unchanged in content. See #548.
            request, build_profile = apply_instruction_profile(
                request, "implement", sections=IMPLEMENT_SECTIONS)
        if build_profile and rewrite_counts is not None:
            rewrite_counts["buildInstructionProfile"] = build_profile
        request, assembly = assemble_for_route(
            request, mode=state.mode.value, phase=state.phase.value,
            chat_thread_id=state.chat_thread_id)
        if assembly and rewrite_counts is not None:
            rewrite_counts["implementationAssembly"] = assembly
        if (self._build_policy is not None
                and state.chat_thread_id is None
                and (state.mode is Mode.IMPLEMENT
                     or state.mode is Mode.AUTO and state.phase is Phase.IMPLEMENT)
                and not state.read_only_turn):
            request, window = apply_tool_result_window(request, self._build_policy)
            if rewrite_counts is not None:
                rewrite_counts["toolResultWindow"] = window
            if (window["perResultShortenedCount"] or window["aggregateCompactedCount"]
                    or window["emptyFallbackCount"]):
                log.info(
                    "tool result window: project=%s session=%s policy_version=%d "
                    "results=%d original_bytes=%d forwarded_bytes=%d per_result_shortened=%d "
                    "aggregate_compacted=%d empty_fallback=%d per_result_limit=%d "
                    "aggregate_limit=%d",
                    project, session, window["policyVersion"], window["resultCount"],
                    window["originalModelFacingBytes"], window["forwardedModelFacingBytes"],
                    window["perResultShortenedCount"], window["aggregateCompactedCount"],
                    window["emptyFallbackCount"], window["perResultLimitBytes"],
                    window["aggregateLimitBytes"],
                )
        return request, labels, used, capability, effort_decision
