"""Observed data operations and per-request evidence. No provider receipt is inferred."""

import copy
import json
import threading
from uuid import uuid4

from ..shim.chat_paths import read_path_from_tool_call, withheld_result


class DataUse:
    def __init__(self):
        self.operations = {}
        self.lock = threading.RLock()

    def record(self, event, reply, persist, turn_id):
        with self.lock:
            event = {**event, "turn_id": turn_id}
            self.operations[event["operation_id"]] = (event, copy.deepcopy(reply), persist)
            persist({"type": "data_used", "dataUsed": [copy.deepcopy(event)]})

    def restore(self, history, persist):
        """Reopen metadata after a restart. Saved Artifacts default to structure, never values."""
        latest = {event["operation_id"]: event for row in history for event in row.get("dataUsed", [])
                  if event.get("operation_id")}
        with self.lock:
            for oid, event in latest.items():
                if oid in self.operations:
                    continue
                reply = {"data_use": oid, "columns": event["columns"],
                         "result_rows": event.get("result_rows"),
                         "local_reference": event["artifact"], "coverage": event["coverage"],
                         "selected_fields": []}
                self.operations[oid] = (copy.deepcopy(event), reply, persist)

    def events(self, turn_id):
        with self.lock:
            return [copy.deepcopy(event) for event, _, _ in self.operations.values()
                    if event["turn_id"] == turn_id]

    def prepare(self, request):
        """Only known structured results and direct reads of their Artifacts are handled here."""
        if not self.operations or not isinstance(request.get("messages"), list):
            return request, set()
        calls = {}
        used = set()
        messages = []
        with self.lock:
            for message in request.get("messages", []):
                if not isinstance(message, dict):
                    messages.append(message)
                    continue
                for call in message.get("tool_calls") or []:
                    if isinstance(call, dict):
                        calls[call.get("id")] = call
                if message.get("role") == "tool":
                    content = message.get("content")
                    try:
                        body = json.loads(content) if isinstance(content, str) else {}
                    except ValueError:
                        body = {}
                    oid = body.get("data_use") if isinstance(body, dict) else None
                    entry = self.operations.get(oid) if isinstance(oid, str) else None
                    if entry:
                        # The operation's own result; preserve withholding already applied by the shim.
                        used.add(oid)
                    path = read_path_from_tool_call(calls.get(message.get("tool_call_id"), {}))
                    if path and content not in (withheld_result(path),
                                                withheld_result("a message in this conversation")):
                        for event, reply, _ in self.operations.values():
                            if path == event["artifact"] or path.endswith("/" + event["artifact"]):
                                shape = {k: v for k, v in reply.items() if k != "selected"}
                                shape["selected_fields"] = []
                                message = {**message, "content": json.dumps(shape)}
                                break
                messages.append(message)
        return {**request, "messages": messages}, used

    def observe(self, stream, request, used):
        if not used:
            yield from stream
            return
        request_id = "req_" + uuid4().hex
        evidence = {"request_id": request_id, "requested_alias": request.get("model"),
                    "serving_model": None, "provider_receipt": "unknown", "cache": "unknown",
                    "decision_stage": "unknown", "delivery": "unknown", "state": "attempted"}

        def save():
            with self.lock:
                for oid in used:
                    event, _, persist = self.operations[oid]
                    event["requests"] = [r for r in event["requests"] if r["request_id"] != request_id]
                    event["requests"].append(copy.deepcopy(evidence))
                    persist({"type": "data_used", "dataUsed": [copy.deepcopy(event)]})

        save()
        buffer = ""
        completed = False
        try:
            for chunk in stream:
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            continue
                        try:
                            body = json.loads(payload)
                        except ValueError:
                            continue
                        if body.get("error"):
                            evidence["state"] = "failed"
                        elif any(c.get("finish_reason") in ("stop", "tool_calls")
                                 for c in body.get("choices", [])):
                            completed = True
                yield chunk
        except Exception:
            evidence["state"] = "failed"
            raise
        finally:
            if evidence["state"] != "failed":
                evidence["state"] = "response_completed" if completed else "interrupted"
            save()
