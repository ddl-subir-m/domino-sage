"""Scoped harness endpoints for all three gateway protocols."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import build_intent, timing
from ..context_rollover import ContextAction
from ..gateway.client import GatewayUpstreamError, StreamCancellation
from ..gateway.events import StreamEvents
from ..gateway.protocol import Protocol
from ..pre_edit_guard import PreEditAction
from ..request_composition import measure, wire_bytes
from ..shim import keepalive as ka
from ..shim.enforcement import _capture_refusal
from ..shim.native import (
    NativeCheckpointRequired,
    NativePolicyError,
    prepare_native,
    sdk_view,
    session_policy,
)
from ..tool_result_window import completed_tool_results

# The same logger the legacy `/v1/chat/completions` handler writes to, so its "model call ->
# streaming" line and the ones below land in the one ring `/api/diag/log` reads.
log = logging.getLogger("sage.orchestrator")
_BUILD_INTENT_ERROR = (
    "The Build instructions were not intact at the model boundary. "
    "Sage stopped before sending the request."
)
_PRE_EDIT_REJECTION = "Sage stopped this native request under the active pre-edit Build policy."
_PRE_EDIT_MEASUREMENT_ERROR = (
    "Sage could not measure the final native request under the active pre-edit Build policy."
)
_PRE_EDIT_WITNESS_ERROR = (
    "Sage could not verify the app working tree under the active pre-edit Build policy."
)
_CONTEXT_ROLLOVER_REQUIRED = "Sage stopped this request so the Build can continue in a clean context."
_CONTEXT_CONTINUE_REQUIRED = "Sage stopped this request because the Build reached its context limit."
_CONTEXT_MEASUREMENT_ERROR = "Sage could not measure the final model request safely."
_TURN_SCOPE_CHANGED = "Sage stopped this request because its Build turn ended before it was ready."
_MODEL_OUTPUT_LIMIT_ERRORS = frozenset({"length", "max_tokens", "max_output_tokens"})
_MODEL_NO_ACTION_MESSAGE = (
    "The model kept streaming without producing text or starting a tool call. "
    "Sage stopped this attempt safely."
)


class _ModelNoActionTimeout(Exception):
    def __init__(self, snapshot: dict) -> None:
        super().__init__(_MODEL_NO_ACTION_MESSAGE)
        self.snapshot = snapshot


def _record_build_intent(call, intent, check, failure_stage):
    call.intent(
        kind=intent.kind,
        status=check.status,
        carrier_count=check.carrier_count,
        carrier_bytes=check.carrier_bytes,
        source_request_count=len(intent.source_requests),
        plan_present=bool(intent.authoritative_plan or intent.phase_brief),
        failure_stage=failure_stage,
    )


def _scope(orchestrator, request):
    project = orchestrator._project
    session = request.headers.get("x-session-id")
    active = project.active_session_id if project is not None else None
    allowed = bool(active and session == active)
    retired = False
    context_state = project.context_rollover if project is not None else None
    client = getattr(orchestrator, "_oc_client", None)
    belongs = getattr(client, "session_belongs_to", None)
    if context_state is not None and session:
        retired = context_state.rejects_session(session, belongs)
        allowed = allowed or retired
    if active and session and not allowed and orchestrator._turn_lock.locked():
        allowed = bool(belongs and belongs(session, active))
    if (project is None or not orchestrator._turn_lock.locked() or not allowed
            or project.active_session_id != active):
        raise NativePolicyError("This model request does not belong to an active Conversation session.")
    return project, session


def _error(message, status=400):
    return JSONResponse(status_code=status, content={"error": {"message": message}})


def _native_local_error(protocol: Protocol, message: str, code: str) -> JSONResponse:
    """Return one fixed, protocol-shaped local refusal. No request data enters it."""
    if protocol is Protocol.MESSAGES:
        body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    elif protocol is Protocol.RESPONSES:
        body = {"error": {"type": "invalid_request_error", "code": code,
                          "message": message, "param": None}}
    else:
        body = {"error": {"message": message, "type": "invalid_request_error",
                          "param": None, "code": code}}
    return JSONResponse(status_code=400, content=body)


def _error_event(protocol, message):
    if protocol is Protocol.CHAT:
        return b'data: ' + json.dumps({"error": {"message": message, "type": "sage_gateway_error"}}).encode() + b'\n\n'
    event = ({"type": "error", "sequence_number": 0, "code": "sage_gateway_error", "message": message, "param": None}
             if protocol is Protocol.RESPONSES else
             {"type": "error", "error": {"type": "sage_gateway_error", "message": message}})
    return b'event: error\ndata: ' + json.dumps(event).encode() + b'\n\n'


def install(app, get_orchestrator):
    @app.post("/v1/sage/resolve")
    async def resolve_route(request: Request):
        try:
            project, session = _scope(get_orchestrator(), request)
            body = await request.json()
            view = sdk_view(body.get("prompt", []), body.get("tools", []))
            prepared, _, _, capability, _effort = project.shim.prepare(
                view, project.id, session, native=True)
            return {"model": prepared["model"], "protocol": capability.protocol.value,
                    "effort": prepared.get("reasoning_effort"), "native": capability.native}
        except (ValueError, KeyError, TypeError) as error:
            return _error(str(error))

    @app.post("/v1/sage/anthropic/messages")
    @app.post("/v1/sage/responses")
    @app.post("/v1/sage/chat/completions")
    async def inference(request: Request):
        protocol = (Protocol.MESSAGES if request.url.path.endswith("/messages") else
                    Protocol.RESPONSES if request.url.path.endswith("/responses") else Protocol.CHAT)
        call = timing.model_call(record=None)
        intent = None
        installed = None
        original_results = ()
        try:
            orchestrator = get_orchestrator()
            project, session = _scope(orchestrator, request)
            running_ticket = orchestrator._turns.running()
            if running_ticket is None:
                return _native_local_error(
                    protocol, _TURN_SCOPE_CHANGED, "sage_turn_scope_changed")
            record = running_ticket.timing_record
            root_session = project.active_session_id
            raw = await request.body()
            # Reading a body can yield long enough for its Build turn to finish and another one to
            # start. Check only the ownership captured above before reading any mutable successor
            # state. A late request must not install its intent, advance a guard, or reach a gateway
            # under the new turn.
            same_owner = (
                orchestrator._project is project
                and orchestrator._turn_lock.locked()
                and orchestrator._turns.running() is running_ticket
                and project.active_session_id == root_session
                and request.headers.get("x-session-id") == session
            )
            if not same_owner:
                return _native_local_error(
                    protocol, _TURN_SCOPE_CHANGED, "sage_turn_scope_changed")
            diagnostic_record = record if (
                record is not None
                and record.turn_id == running_ticket.id
            ) else None
            call = timing.model_call(
                record=diagnostic_record, session_id=session, root_session_id=root_session,
                app_id=(project.app_for_turn().app_id
                        if diagnostic_record is not None
                        and diagnostic_record.kind != "chat" else None),
                conversation_id=project.build_conversation)
            context_state = project.context_rollover
            client = getattr(get_orchestrator(), "_oc_client", None)
            belongs = getattr(client, "session_belongs_to", None)
            if (context_state is not None
                    and context_state.rejects_session(session, belongs)):
                call.done(ok=False, error=_CONTEXT_ROLLOVER_REQUIRED,
                          outcome="context_rollover_required")
                return _native_local_error(
                    protocol, _CONTEXT_ROLLOVER_REQUIRED, "sage_context_rollover_required")
            body = json.loads(raw)
            if body.get("stream") is not True:
                return _error("This scoped harness endpoint requires streaming. Other calls keep their existing gateway path.")
            call.request(len(raw))

            intent = project.active_build_intent
            if intent is not None:
                try:
                    body = build_intent.install(body, protocol, intent)
                    installed = build_intent.inspect(body, protocol, intent)
                    _record_build_intent(call, intent, installed, "none")
                    if installed.status != "ok":
                        _record_build_intent(call, intent, installed, "install")
                        project.last_gateway_error = {"message": _BUILD_INTENT_ERROR}
                        call.done(ok=False, error=_BUILD_INTENT_ERROR, outcome="error")
                        return _error(_BUILD_INTENT_ERROR)
                except (TypeError, ValueError):
                    failed = build_intent.BuildIntentCheck("unsupported", 0, 0)
                    _record_build_intent(call, intent, failed, "install")
                    project.last_gateway_error = {"message": _BUILD_INTENT_ERROR}
                    call.done(ok=False, error=_BUILD_INTENT_ERROR, outcome="error")
                    return _error(_BUILD_INTENT_ERROR)

            guard = project.pre_edit_guard
            if guard is not None:
                original_results = completed_tool_results(body, protocol)

            resolution = None
            effort_decision = None
            native_preparation = None
            rewrite_counts = {}
            def resolved(model, phase, reason):
                nonlocal resolution
                resolution = (model, phase, reason)

            if protocol is Protocol.CHAT:
                opaque = any(call.get("extra_content", {}).get("google", {}).get("thought_signature")
                             for message in body.get("messages", []) for call in message.get("tool_calls", []))
                session_policy(project.record.path, session, project.control.snapshot(), opaque=bool(opaque))
                outbound, labels, used, capability, effort_decision = project.shim.prepare(
                    body, project.id, session, resolved, native=True,
                    rewrite_counts=rewrite_counts)
                if outbound["model"] != body.get("model") or capability.protocol is not protocol:
                    raise NativePolicyError("The resolved model route changed. Retry this turn.")
                view = outbound
            else:
                native_preparation = prepare_native(
                    project.shim, body, protocol, project.id, session, resolved,
                    policy_directory=project.record.path,
                    rewrite_counts=rewrite_counts)
                outbound, labels, used, view, capability = native_preparation
        except NativeCheckpointRequired as error:
            if intent is not None and installed is not None:
                _record_build_intent(call, intent, installed, "prepare")
            project.last_gateway_error = {"message": str(error)}
            call.done(ok=False, error=str(error))
            return _error(str(error))
        except (ValueError, KeyError, TypeError) as error:
            if intent is not None and installed is not None:
                _record_build_intent(call, intent, installed, "prepare")
            call.done(ok=False, error="Request preparation failed", outcome="error")
            return _error(str(error))

        def refused(model, messages):
            # Withhold search uses the ordinary policy view, never native state.
            project.last_refused = (model, [{k: v for k, v in m.items() if k != "_wire"}
                                           for m in messages if not m.get("_wire", {}).get("opaque")])

        contract = None
        if protocol is Protocol.RESPONSES:
            nonce = uuid4().hex
            outbound["metadata"] = {**outbound.get("metadata", {}), "sage_route_check": nonce}
            contract = {"nonce": nonce, "effort": (outbound.get("reasoning") or {}).get("effort")}
        # Match httpx's JSON body encoder, after the final native contract rewrite. Only the
        # length survives; neither payload nor private native state enters the timing record.
        forwarded_bytes = None
        try:
            forwarded_bytes = wire_bytes(outbound)
            composition = measure(outbound, forwarded_bytes, rewrite_counts)
        except Exception:
            # Let the transport retain its existing invalid-payload error path. Diagnostics
            # must not replace that response with an uncaught serializer exception.
            composition = None
        call.prepared(forwarded_bytes, requested_alias=outbound.get("model"),
                      request_composition=composition)
        events = StreamEvents(protocol, response_contract=contract)
        cancel = StreamCancellation()
        if intent is not None:
            checked = build_intent.inspect(outbound, protocol, intent)
            _record_build_intent(
                call, intent, checked, "none" if checked.status == "ok" else "final_check")
            if checked.status != "ok":
                project.last_gateway_error = {"message": _BUILD_INTENT_ERROR}
                call.done(ok=False, error=_BUILD_INTENT_ERROR, outcome="error")
                return _error(_BUILD_INTENT_ERROR)
        # Read the typed effort only after the final Build-intent check. Test and compatibility
        # wrappers may still expose the historical five-item iterable on a request that this check
        # rejects; that local rejection must happen before any new preparation metadata is needed.
        if effort_decision is None and native_preparation is not None:
            effort_decision = native_preparation.effort_decision
        if resolution is not None:
            project.note_resolved(*resolution, protocol=protocol.value,
                                  effort=effort_decision.effective_effort,
                                  native=capability.native)
            call.model(*resolution)
        guard = project.pre_edit_guard
        if guard is not None:
            if forwarded_bytes is None:
                with project.pre_edit_tree_lock:
                    decision = guard.fail_request_measurement()
            else:
                # Unknown classification is non-media. If composition failed after serialization,
                # the whole final wire request is therefore the safe non-media count.
                media_bytes = 0
                if isinstance(composition, dict):
                    categories = composition.get("categories")
                    if isinstance(categories, dict):
                        raw_media = categories.get("mediaBytes")
                        if (isinstance(raw_media, int) and not isinstance(raw_media, bool)
                                and raw_media >= 0):
                            media_bytes = raw_media
                non_media_bytes = max(0, forwarded_bytes - media_bytes)
                with project.pre_edit_tree_lock:
                    decision = guard.decide_request(original_results, non_media_bytes)
            if decision.action in {
                    PreEditAction.RECOVER, PreEditAction.STOP, PreEditAction.FAIL}:
                message = (
                    _PRE_EDIT_MEASUREMENT_ERROR
                    if decision.trigger.value == "request_measurement_unavailable"
                    else _PRE_EDIT_WITNESS_ERROR
                    if decision.trigger.value == "tree_witness_unavailable"
                    else _PRE_EDIT_REJECTION
                )
                call.done(ok=False, error=message, outcome="pre_edit_policy")
                return _error(message, 409)
        context_state = project.context_rollover
        if context_state is not None:
            categories = composition.get("categories") if isinstance(composition, dict) else None
            raw_media = categories.get("mediaBytes") if isinstance(categories, dict) else None
            status = composition.get("status") if isinstance(composition, dict) else "unavailable"
            media_bytes = (raw_media if isinstance(raw_media, int)
                           and not isinstance(raw_media, bool) and raw_media >= 0 else -1)
            decision = context_state.decide(
                total_wire_bytes=forwarded_bytes if forwarded_bytes is not None else -1,
                media_bytes=media_bytes,
                measurement_status=status if isinstance(status, str) else "unavailable",
            )
            if decision.action is not ContextAction.ROUTE:
                if decision.action is ContextAction.ROLLOVER:
                    message = _CONTEXT_ROLLOVER_REQUIRED
                    code = "sage_context_rollover_required"
                    outcome = "context_rollover_required"
                elif decision.action is ContextAction.OFFER_CONTINUE:
                    message = _CONTEXT_CONTINUE_REQUIRED
                    code = "sage_context_continue_required"
                    outcome = "context_continue_required"
                else:
                    message = _CONTEXT_MEASUREMENT_ERROR
                    code = "sage_context_measurement_unavailable"
                    outcome = "context_measurement_unavailable"
                project.last_gateway_error = {"message": message}
                call.done(ok=False, error=message, outcome=outcome)
                return _native_local_error(protocol, message, code)
        if effort_decision is not None:
            call.route(protocol.value, effort_decision,
                       capability.verified if capability.identity else None)
        project.model_calls += 1
        call_id = call.call_id or uuid4().hex
        build_watchdog = bool(
            diagnostic_record is not None and diagnostic_record.kind in {"build", "approve"}
            or project.control.snapshot().read_only_reason == "plan")
        policy = orchestrator._build_policy
        terminal_logged = False
        if build_watchdog:
            project.begin_active_model_call(call_id, running_ticket.id, time.monotonic())
        upstream = project.shim.gateway.route(outbound, labels, protocol=protocol, cancel=cancel)

        def log_no_action_terminal(active, action):
            nonlocal terminal_logged
            if active is None or not active["noticeSent"] or terminal_logged:
                return
            log.warning(
                "model no-action terminal: turn_id=%s call_id=%s elapsed_seconds=%.1f "
                "chunks=%d action=%s",
                running_ticket.id, call_id, active["elapsedSeconds"],
                active["chunkCount"], action)
            terminal_logged = True

        def validated():
            nonlocal received
            try:
                for chunk in upstream:
                    # A scoped Stop or ended turn always owns the result of this frame.
                    if cancel.event.is_set():
                        break
                    call.first_byte()
                    call.chunk()
                    received += 1
                    try:
                        frames = events.feed(chunk)
                    finally:
                        call.stream_metadata(events)
                    project.last_stream_chunk_at = time.monotonic()
                    active = None
                    if build_watchdog:
                        active = project.observe_active_model_call(
                            call_id, time.monotonic(),
                            first_action_kind=events.first_action_kind,
                            reasoning_only_chunks=events.reasoning_only_chunks)
                    # Provider terminal failures, including output limits, own the frame on which
                    # they arrive. A text or tool announcement on the same frame is then action.
                    if events.error:
                        raise ValueError("The model stream failed: " + events.error)
                    if build_watchdog:
                        if active is not None and active["firstActionKind"] is not None:
                            log_no_action_terminal(active, active["firstActionKind"])
                        elif active is not None:
                            elapsed = active["elapsedSeconds"]
                            if (elapsed >= policy.model_no_action_notice_seconds
                                    and project.mark_active_model_notice(call_id)):
                                call.no_action_notice()
                                log.warning(
                                    "model no-action notice: turn_id=%s call_id=%s "
                                    "elapsed_seconds=%.1f chunks=%d action=pending",
                                    running_ticket.id, call_id, elapsed,
                                    active["chunkCount"])
                            if elapsed >= policy.model_no_action_timeout_seconds:
                                project.mark_active_model_timeout(call_id)
                                call.no_action_timeout()
                                raise _ModelNoActionTimeout(active)
                    yield from frames
                if not cancel.event.is_set():
                    events.finish()
                    if build_watchdog:
                        log_no_action_terminal(
                            project.active_model_snapshot(),
                            events.first_action_kind or "completed_no_action")
            except _ModelNoActionTimeout:
                raise
            except Exception:
                if build_watchdog:
                    log_no_action_terminal(
                        project.active_model_snapshot(),
                        events.error or "stream_error")
                raise
            finally:
                upstream.close()

        gen = _capture_refusal(validated(), view, refused)
        gen = project.shim.data_use.observe(gen, view, used)
        q = queue.Queue(maxsize=8)

        def put(item):
            while not cancel.event.is_set():
                try:
                    q.put(item, timeout=0.1)
                    return
                except queue.Full:
                    continue

        # How many chunks the gateway sent, for the line a cancelled call leaves behind. The ledger
        # counts the same thing, but a Builder is read through the log ring and not the ledger, and
        # the one question a stalled call raises — did the gateway send anything at all — needs a
        # number in the ring to answer it. Measured 2026-09-21: a Build call went 121s with no
        # OpenCode output, the quiet window stopped it, and nothing in the ring said whether a
        # single byte had arrived.
        received = 0

        def pump():
            flagged = False
            try:
                for chunk in gen:
                    if cancel.event.is_set():
                        break
                    if events.tool_ids and not flagged:
                        project.tool_call_responses += 1
                        flagged = True
                    # Carried up for the live "active" label (#497). The argument streams for
                    # several seconds while OpenCode's transcript shows the part `pending` with an
                    # input of `{}`, so this is the only place in the process that knows the write
                    # is progressing. Published per chunk rather than at the end, because the whole
                    # point is the window BEFORE the call completes.
                    if events.tool_input_lines:
                        project.tool_input_lines = dict(events.tool_input_lines)
                    put(chunk)
                if not cancel.event.is_set():
                    put(ka.DONE)
            except Exception as error:
                put(("error", error))
            finally:
                # The count belongs to the call that produced it. Left standing, it would decorate
                # the NEXT `write` the moment its part appears — a label that looks live and is
                # describing a file finished a minute ago, which is the defect this fixes wearing
                # the other face.
                project.tool_input_lines = {}
                if build_watchdog:
                    project.clear_active_model_call(call_id)
                gen.close()

        started = time.monotonic()
        threading.Thread(target=pump, daemon=True).start()

        async def take(seconds):
            return await asyncio.get_running_loop().run_in_executor(None, ka.get, q, seconds)

        def failure(error):
            # Provider error bodies can contain reasoning or a signature. Do not log or echo them.
            if isinstance(error, _ModelNoActionTimeout):
                message = _MODEL_NO_ACTION_MESSAGE
            elif isinstance(error, GatewayUpstreamError):
                message = f"The model gateway refused this request (HTTP {error.status})."
                if "guardrail_blocked" in error.body:
                    message = "The model gateway refused content under its guardrail policy (guardrail_blocked)."
            elif isinstance(error, ValueError):
                message = str(error)
            else:
                message = f"The model gateway stream stopped ({type(error).__name__}). Retry the turn."
            project.last_gateway_error = {"message": message}
            if isinstance(error, _ModelNoActionTimeout):
                snapshot = error.snapshot
                project.last_gateway_error.update({
                    "code": "model_no_action_timeout",
                    "call_id": call_id,
                    "turn_id": running_ticket.id,
                    "elapsed_ms": round(snapshot["elapsedSeconds"] * 1000),
                    "chunk_count": snapshot["chunkCount"],
                })
            if isinstance(error, GatewayUpstreamError):
                project.last_gateway_error["upstream_status"] = error.status
            outcome = "error"
            if (events.refused or events.error == "content_filter" or
                    isinstance(error, GatewayUpstreamError) and "guardrail_blocked" in error.body):
                outcome = "refusal"
            elif isinstance(error, ValueError) and (events.terminal is None or events.error in
                    ("length", "max_tokens", "pause_turn", "max_output_tokens", "response.incomplete")):
                outcome = "incomplete"
            if events.error in _MODEL_OUTPUT_LIMIT_ERRORS:
                project.last_gateway_error.update({
                    "code": "model_output_limit",
                    "finish_reason": events.error,
                })
                outcome = "model_output_limit"
            elif isinstance(error, _ModelNoActionTimeout):
                outcome = "no_action_timeout"
            call.done(ok=False, error=message, outcome=outcome)
            return message

        try:
            first = await take(ka.FIRST_BYTE_BUDGET_S)
        except BaseException:
            cancel.cancel()
            call.done(ok=False, error="Model request cancelled", outcome="cancelled")
            raise
        if ka.is_error(first):
            cancel.cancel()
            # Retrying an invalid request cannot repair it. Some native providers
            # report quota/request refusals inside an HTTP 200 error stream.
            return _error(failure(first[1]), 400 if events.error == "invalid_request_error" else 502)
        # Word for word the legacy handler's line, so one grep of the ring reads both routes.
        log.info("model call -> streaming (first byte %.1fs%s)", time.monotonic() - started,
                 ", pending; keepalive engaged" if first is ka.EMPTY else "")

        async def stream():
            item = first
            settled = False
            try:
                while True:
                    if item is ka.DONE:
                        settled = True
                        call.done(ok=not events.refused, outcome="refusal" if events.refused else "success")
                        return
                    if ka.is_error(item):
                        settled = True
                        yield _error_event(protocol, failure(item[1]))
                        return
                    yield ka.KEEPALIVE if item is ka.EMPTY else item
                    item = await take(ka.KEEPALIVE_INTERVAL_S)
            finally:
                cancel.cancel()
                if not settled:
                    call.done(ok=False, error="Model request cancelled", outcome="cancelled")
                    # A cancel is the quiet window or the person's Stop ending a call that was
                    # still open, so it is the failure line for that turn and it goes in the warn
                    # ring. The count is the whole point: zero says the gateway never answered,
                    # any other number says it was answering and OpenCode showed none of it.
                    log.warning("model call cancelled after %.0fs — %s", time.monotonic() - started,
                                "no bytes from the gateway" if received == 0
                                else f"{received} chunks had arrived")
        return StreamingResponse(stream(), media_type="text/event-stream")
