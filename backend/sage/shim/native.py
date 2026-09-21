"""Lossless native payload views for the existing message/tool policy path.

Opaque state stays a block. It is never rendered as ordinary assistant text.
Unknown carriers are refused rather than passing outside the policy view.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from ..gateway.protocol import Protocol


class NativePolicyError(ValueError):
    pass


class NativeCheckpointRequired(NativePolicyError):
    def __init__(self):
        super().__init__("Session policy changed. Use Clear recall before continuing.")


def _parts(content):
    if isinstance(content, str):
        return content
    out = []
    for part in content or []:
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            out.append({"type": "text", "text": part.get("text", "")})
        elif kind == "image":
            source = part.get("source") or {}
            url = (source.get("url") if source.get("type") == "url" else
                   f"data:{source.get('media_type')};base64,{source.get('data')}")
            out.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "input_image":
            out.append({"type": "image_url", "image_url": {"url": part.get("image_url")}})
        else:
            raise NativePolicyError(f"Unsupported native content carrier: {kind}")
    return out


def _native_parts(content, protocol, role):
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    parts = []
    for part in content or []:
        if part.get("type") == "text":
            kind = "text" if protocol is Protocol.MESSAGES else (
                "output_text" if role == "assistant" else "input_text")
            parts.append({"type": kind, "text": part.get("text", "")})
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            if protocol is Protocol.RESPONSES:
                parts.append({"type": "input_image", "image_url": url})
            elif url.startswith("data:"):
                header, data = url.split(",", 1)
                parts.append({"type": "image", "source": {"type": "base64",
                              "media_type": header[5:].split(";")[0], "data": data}})
            else:
                parts.append({"type": "image", "source": {"type": "url", "url": url}})
        else:
            raise NativePolicyError("Unsupported policy content part")
    return parts


def _call(identifier, name, arguments):
    return {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}


class NativeView:
    def __init__(self, body: dict, protocol: Protocol):
        self.body = copy.deepcopy(body)
        self.protocol = protocol
        self.opaque = False
        self.messages = []
        if protocol is Protocol.MESSAGES:
            system = body.get("system")
            if system:
                self._message("system", _parts(system), {"system": system})
            for message in body.get("messages", []):
                content = message.get("content", [])
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                ordinary = []

                def flush(parts, role):
                    if parts:
                        self._message(role, _parts(parts), {"_content": list(parts)})
                        parts.clear()

                for block in content:
                    kind = block.get("type")
                    role = message["role"]
                    if kind in ("text", "image"):
                        ordinary.append(block)
                        continue
                    flush(ordinary, role)
                    if kind in ("thinking", "redacted_thinking"):
                        self.opaque = True
                        self._message("assistant", "", block, opaque=True)
                    elif kind == "tool_use":
                        self._message("assistant", "", block, calls=[
                            _call(block["id"], block["name"], block.get("input", {}))])
                    elif kind == "tool_result":
                        self._message("tool", _parts(block.get("content", "")), block,
                                      call_id=block["tool_use_id"])
                    else:
                        self._message(role, _parts([block]), block)
                flush(ordinary, message["role"])
            tools = [{"type": "function", "function": {"name": t["name"],
                      "description": t.get("description", ""), "parameters": t.get("input_schema", {})}}
                     for t in body.get("tools", [])]
            if any(t.get("type") not in (None, "custom") for t in body.get("tools", [])):
                raise NativePolicyError("Only policy-scoped function tools are permitted")
        else:
            if body.get("previous_response_id") or body.get("conversation") or body.get("store") is True:
                raise NativePolicyError("Provider-stored conversation state is not permitted")
            if body.get("instructions"):
                self._message("system", body["instructions"], {"instructions": body["instructions"]})
            items = body.get("input", [])
            if isinstance(items, str):
                items = [{"role": "user", "content": items}]
            for item in items:
                kind = item.get("type", "message")
                if kind == "reasoning":
                    self.opaque = True
                    self._message("assistant", "", item, opaque=True)
                elif kind == "function_call":
                    self._message("assistant", "", item, calls=[
                        _call(item["call_id"], item["name"], item.get("arguments", "{}"))])
                elif kind == "function_call_output":
                    self._message("tool", _parts(item.get("output", "")), item, call_id=item["call_id"])
                elif kind == "message":
                    self._message(item["role"], _parts(item.get("content", "")), item)
                else:
                    raise NativePolicyError(f"Unsupported Responses input carrier: {kind}")
            tools = []
            for tool in body.get("tools", []):
                if tool.get("type") != "function":
                    raise NativePolicyError("Only policy-scoped function tools are permitted")
                tools.append({"type": "function", "function": {k: v for k, v in tool.items() if k != "type"}})
        self.request = {"model": body.get("model"), "messages": self.messages,
                        "tools": tools, "stream": body.get("stream", False)}

    def _message(self, role, content, original, *, calls=None, call_id=None, opaque=False):
        message = {"role": role, "content": content, "_wire": {
            "original": original, "content": copy.deepcopy(content), "opaque": opaque}}
        if calls:
            message["tool_calls"] = calls
        if call_id:
            message["tool_call_id"] = call_id
        self.messages.append(message)

    def render(self, request):
        body = {**self.body, "model": request["model"]}
        messages = []
        system = []
        for message in request["messages"]:
            role, content = message["role"], message.get("content", "")
            wire = message.get("_wire") or {}
            original = wire.get("original", {})
            if wire.get("opaque"):
                blocks = [copy.deepcopy(original)]
            elif role == "system":
                if content == wire.get("content") and "system" in original:
                    source = original["system"]
                    system.extend(source if isinstance(source, list) else [{"type": "text", "text": source}])
                else:
                    system.extend(_native_parts(content, Protocol.MESSAGES, role))
                continue
            elif role == "tool":
                if self.protocol is Protocol.MESSAGES:
                    blocks = [{**original, "type": "tool_result", "tool_use_id": message["tool_call_id"],
                               "content": (original.get("content", "") if content == wire.get("content")
                                           else _native_parts(content, self.protocol, "user"))}]
                else:
                    blocks = [{**original, "type": "function_call_output", "call_id": message["tool_call_id"],
                               "output": content if isinstance(content, str) else
                               _native_parts(content, self.protocol, "user")}]
                role = "user"
            elif message.get("tool_calls"):
                blocks = []
                for call in message["tool_calls"]:
                    function = call["function"]
                    if self.protocol is Protocol.MESSAGES:
                        blocks.append({**original, "type": "tool_use", "id": call["id"],
                                       "name": function["name"], "input": json.loads(function["arguments"])})
                    else:
                        blocks.append({**original, "type": "function_call", "call_id": call["id"],
                                       "name": function["name"], "arguments": function["arguments"]})
            elif content == wire.get("content"):
                blocks = copy.deepcopy(original.get("_content", [original]))
            elif self.protocol is Protocol.MESSAGES:
                blocks = _native_parts(content, self.protocol, role)
            else:
                blocks = [{"type": "message", "role": role,
                           "content": _native_parts(content, self.protocol, role)}]
            if self.protocol is Protocol.RESPONSES:
                messages.extend(blocks)
            elif messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
        if self.protocol is Protocol.MESSAGES:
            body["messages"] = messages
            if system:
                body["system"] = system
            else:
                body.pop("system", None)
            originals = {t["name"]: t for t in self.body.get("tools", [])}
            body["tools"] = [{**originals.get(t["function"]["name"], {}),
                              "name": t["function"]["name"],
                              "input_schema": t["function"].get("parameters", {})} for t in request.get("tools", [])]
        else:
            body["input"] = [{"role": "system", "content": _native_parts(system, self.protocol, "system")}] + messages if system else messages
            body.pop("instructions", None)
            body["tools"] = [{"type": "function", **t["function"]} for t in request.get("tools", [])]
        if not body["tools"]:
            body.pop("tool_choice", None)
        return body


def sdk_view(prompt, tools):
    """Routing view before codec selection; never sent as a native wire payload."""
    messages = []
    for message in prompt:
        role = message["role"]
        if isinstance(message.get("content"), str):
            messages.append({"role": role, "content": message["content"]})
            continue
        content, calls = [], []
        for part in message.get("content", []):
            kind = part.get("type")
            if kind == "text":
                content.append({"type": "text", "text": part["text"]})
            elif kind == "tool-call":
                call = _call(part["toolCallId"], part["toolName"], part.get("input", {}))
                signature = (part.get("providerOptions", {}).get("google") or {}).get("thoughtSignature")
                if signature:
                    call["extra_content"] = {"google": {"thought_signature": signature}}
                calls.append(call)
            elif kind == "tool-result":
                output = part.get("output") or {}
                value = output.get("value", "")
                messages.append({"role": "tool", "tool_call_id": part["toolCallId"],
                                 "content": value if isinstance(value, str) else json.dumps(value)})
        if content or calls:
            row = {"role": role, "content": content}
            if calls:
                row["tool_calls"] = calls
            messages.append(row)
    return {"messages": messages, "tools": [{"type": "function", "function": {
        "name": t["name"], "description": t.get("description", ""), "parameters": t.get("inputSchema", {})}}
        for t in tools or [] if t.get("type") == "function"]}


def session_policy(directory: Path, session: str, state, *, opaque: bool) -> None:
    """Persist policy provenance, not reasoning state. OpenCode owns the state itself.

    A changed restriction requires the existing Recall-with-summary checkpoint.
    Its new session may generate fresh opaque state under the new restrictions.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        raise NativePolicyError("Invalid active session identity")
    policy = json.dumps({"withheld": sorted(state.withheld),
                         "approved": sorted(state.approved_models) if state.approved_models is not None else None},
                        sort_keys=True).encode()
    fingerprint = hashlib.sha256(policy).hexdigest()
    path = directory / "native-policy" / (session + ".json")
    try:
        previous = path.read_text()
    except FileNotFoundError:
        previous = None
    if ((previous is not None and previous != fingerprint)
            or (opaque and previous is None and (state.withheld or state.approved_models is not None))):
        raise NativeCheckpointRequired()
    if previous != fingerprint:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fingerprint)


def prepare_native(shim, body, protocol, project, session, on_resolved=None, *, policy_directory=None):
    view = NativeView(body, protocol)
    state = shim._control.snapshot()
    if policy_directory is not None:
        session_policy(policy_directory, session, state, opaque=view.opaque)
    elif view.opaque and state.withheld:
        raise NativeCheckpointRequired()
    request, labels, used, capability = shim.prepare(view.request, project, session,
                                                   on_resolved, native=True)
    if body.get("model") != request["model"] or protocol is not capability.protocol:
        raise NativePolicyError("The resolved model route changed. Resolve the route again before sending.")
    result = view.render(request)
    for key in ("reasoning", "reasoning_effort", "thinking"):
        result.pop(key, None)
    if "output_config" in result:
        result["output_config"] = {k: v for k, v in result["output_config"].items() if k != "effort"}
        if not result["output_config"]:
            result.pop("output_config")
    result.update(capability.settings(request.get("reasoning_effort"), tools=bool(result.get("tools"))))
    if protocol is Protocol.MESSAGES and result.get("thinking", {}).get("type") == "adaptive":
        for key in ("temperature", "top_p", "top_k"):
            result.pop(key, None)
    if protocol is Protocol.RESPONSES:
        result["store"] = False
        result["include"] = sorted(set(result.get("include", [])) | {"reasoning.encrypted_content"})
    return result, labels, used, request, capability


def text_stream(gateway, request, labels, capability):
    """Native, stateless text calls for existing internal CC text consumers.

    The harness never uses this adapter. Tool history must use its native codec.
    Input has already passed the shared policy; only text deltas return to a helper.
    """
    from uuid import uuid4

    from ..gateway.events import StreamEvents
    if request.get("tools") or any(m.get("tool_calls") or m.get("role") == "tool"
                                   for m in request.get("messages", [])):
        raise NativePolicyError("Tool history requires the scoped native harness endpoint.")
    if any("_wire" in message for message in request.get("messages", [])):
        raise NativePolicyError("Internal native policy metadata is not accepted on the text endpoint.")
    protocol = capability.protocol
    seed = {"model": request["model"]}
    outbound = NativeView(seed, protocol).render(request)
    outbound.update(capability.settings(request.get("reasoning_effort"), tools=False))
    outbound["stream"] = True
    limit = request.get("max_tokens", 4096)
    contract = None
    if protocol is Protocol.MESSAGES:
        outbound["max_tokens"] = limit
    else:
        nonce = uuid4().hex
        outbound.update(max_output_tokens=limit, store=False, include=["reasoning.encrypted_content"],
                        metadata={"sage_route_check": nonce})
        contract = {"nonce": nonce, "effort": request.get("reasoning_effort")}
    parser = StreamEvents(protocol, response_contract=contract)
    upstream = gateway.route(outbound, labels, protocol=protocol)
    try:
        for chunk in upstream:
            frames = parser.feed(chunk)
            if parser.error or parser.refused:
                raise NativePolicyError("The model refused or could not complete the text request.")
            for frame in frames:
                for line in frame.splitlines():
                    if not line.startswith(b"data:"):
                        continue
                    event = json.loads(line[5:])
                    delta = event.get("delta")
                    text = (delta.get("text") if isinstance(delta, dict) and
                            delta.get("type") == "text_delta" else
                            delta if event.get("type") == "response.output_text.delta" else None)
                    if text:
                        yield b"data: " + json.dumps({"choices": [{"delta": {"content": text}}]}).encode() + b"\n\n"
        parser.finish()
        yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    finally:
        upstream.close()
