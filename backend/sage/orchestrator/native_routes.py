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

from .. import timing
from ..gateway.client import GatewayUpstreamError, StreamCancellation
from ..gateway.events import StreamEvents
from ..gateway.protocol import Protocol
from ..shim import keepalive as ka
from ..shim.enforcement import _capture_refusal
from ..shim.native import (
    NativeCheckpointRequired,
    NativePolicyError,
    prepare_native,
    sdk_view,
    session_policy,
)

# The same logger the legacy `/v1/chat/completions` handler writes to, so its "model call ->
# streaming" line and the ones below land in the one ring `/api/diag/log` reads.
log = logging.getLogger("sage.orchestrator")


def _scope(orchestrator, request):
    project = orchestrator._project
    session = request.headers.get("x-session-id")
    active = project.active_session_id if project is not None else None
    allowed = bool(active and session == active)
    if active and session and not allowed and orchestrator._turn_lock.locked():
        client = getattr(orchestrator, "_oc_client", None)
        belongs = getattr(client, "session_belongs_to", None)
        allowed = bool(belongs and belongs(session, active))
    if (project is None or not orchestrator._turn_lock.locked() or not allowed
            or project.active_session_id != active):
        raise NativePolicyError("This model request does not belong to an active Conversation session.")
    return project, session


def _error(message, status=400):
    return JSONResponse(status_code=status, content={"error": {"message": message}})


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
            prepared, _, _, capability = project.shim.prepare(view, project.id, session, native=True)
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
        try:
            project, session = _scope(get_orchestrator(), request)
            raw = await request.body()
            body = json.loads(raw)
            if body.get("stream") is not True:
                return _error("This scoped harness endpoint requires streaming. Other calls keep their existing gateway path.")
            call = timing.model_call()
            call.request(len(raw))

            resolution = None
            def resolved(model, phase, reason):
                nonlocal resolution
                resolution = (model, phase, reason)

            if protocol is Protocol.CHAT:
                opaque = any(call.get("extra_content", {}).get("google", {}).get("thought_signature")
                             for message in body.get("messages", []) for call in message.get("tool_calls", []))
                session_policy(project.record.path, session, project.control.snapshot(), opaque=bool(opaque))
                outbound, labels, used, capability = project.shim.prepare(body, project.id, session,
                                                                         resolved, native=True)
                if outbound["model"] != body.get("model") or capability.protocol is not protocol:
                    raise NativePolicyError("The resolved model route changed. Retry this turn.")
                view = outbound
            else:
                outbound, labels, used, view, capability = prepare_native(
                    project.shim, body, protocol, project.id, session, resolved,
                    policy_directory=project.record.path)
            if resolution is not None:
                project.note_resolved(*resolution, protocol=protocol.value,
                                      effort=view.get("reasoning_effort"), native=capability.native)
                call.model(*resolution)
            project.model_calls += 1
            call.prepared()
        except NativeCheckpointRequired as error:
            project.last_gateway_error = {"message": str(error)}
            call.done(ok=False, error=str(error))
            return _error(str(error))
        except (ValueError, KeyError, TypeError) as error:
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
        events = StreamEvents(protocol, response_contract=contract)
        cancel = StreamCancellation()
        upstream = project.shim.gateway.route(outbound, labels, protocol=protocol, cancel=cancel)

        def validated():
            try:
                for chunk in upstream:
                    frames = events.feed(chunk)
                    project.last_stream_chunk_at = time.monotonic()
                    if events.error:
                        raise ValueError("The model stream failed: " + events.error)
                    yield from frames
                if not cancel.event.is_set():
                    events.finish()
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
            nonlocal received
            flagged = False
            try:
                for chunk in gen:
                    if cancel.event.is_set():
                        break
                    call.first_byte()
                    call.chunk()
                    received += 1
                    if events.tool_ids and not flagged:
                        project.tool_call_responses += 1
                        flagged = True
                    if events.tool_names:
                        call.tool(sorted(events.tool_names))
                    if events.input_tokens is not None:
                        call.usage(events.input_tokens, events.cached_tokens)
                    put(chunk)
                if not cancel.event.is_set():
                    put(ka.DONE)
            except Exception as error:
                put(("error", error))
            finally:
                gen.close()

        started = time.monotonic()
        threading.Thread(target=pump, daemon=True).start()

        async def take(seconds):
            return await asyncio.get_running_loop().run_in_executor(None, ka.get, q, seconds)

        def failure(error):
            # Provider error bodies can contain reasoning or a signature. Do not log or echo them.
            if isinstance(error, GatewayUpstreamError):
                message = f"The model gateway refused this request (HTTP {error.status})."
                if "guardrail_blocked" in error.body:
                    message = "The model gateway refused content under its guardrail policy (guardrail_blocked)."
            elif isinstance(error, ValueError):
                message = str(error)
            else:
                message = f"The model gateway stream stopped ({type(error).__name__}). Retry the turn."
            project.last_gateway_error = {"message": message}
            if isinstance(error, GatewayUpstreamError):
                project.last_gateway_error["upstream_status"] = error.status
            call.done(ok=False, error=message)
            return message

        try:
            first = await take(ka.FIRST_BYTE_BUDGET_S)
        except BaseException:
            cancel.cancel()
            call.done(ok=False, error="Model request cancelled")
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
                        call.done(ok=not events.refused)
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
                    call.done(ok=False, error="Model request cancelled")
                    # A cancel is the quiet window or the person's Stop ending a call that was
                    # still open, so it is the failure line for that turn and it goes in the warn
                    # ring. The count is the whole point: zero says the gateway never answered,
                    # any other number says it was answering and OpenCode showed none of it.
                    log.warning("model call cancelled after %.0fs — %s", time.monotonic() - started,
                                "no bytes from the gateway" if received == 0
                                else f"{received} chunks had arrived")
        return StreamingResponse(stream(), media_type="text/event-stream")
