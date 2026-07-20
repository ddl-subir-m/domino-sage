"""OpenCode HTTP driver (DESIGN Seam 3) + the closed feedback loop (Step 5 wiring).

Talks to a running `opencode serve` (server.py). Creates a session scoped to a workspace,
sends prompts, waits for a turn to finish, streams events (normalized to AgentEvent), and runs
the prompt -> wait -> typecheck -> feed-errors-back loop until clean or the breaker stops it.

Leak rule (DESIGN): the shim/router never see OpenCode types; all OpenCode specifics live here.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from ..feedback.circuit_breaker import CircuitBreaker, Decision
from ..feedback.runner import FeedbackReport
from .agent_driver import AgentEvent


def map_event(raw: dict) -> AgentEvent:
    """OpenCode SSE envelope {id,type,properties} -> our harness-agnostic AgentEvent.

    Dotted `type` (message.updated, session.idle, ...) maps to a small kind set; everything
    else passes through as 'message' with the raw type kept in the payload.
    """
    t = raw.get("type", "")
    props = raw.get("properties", {})
    if t.startswith("message.part") or t == "message.updated":
        kind = "message"
    elif "error" in t:
        kind = "error"
    elif t.startswith("session"):
        kind = "phase"
    else:
        kind = "message"
    return AgentEvent(kind=kind, payload={"type": t, **props})


@dataclass
class OpenCodeClient:
    base_url: str
    timeout_s: float = 300.0

    def create_session(self, directory: str, model: dict | None = None) -> str:
        body: dict = {"location": {"directory": directory}}
        if model:
            body["model"] = model
        r = httpx.post(f"{self.base_url}/api/session", json=body, timeout=30)
        r.raise_for_status()
        payload = r.json()
        # /api/* responses wrap the resource in {"data": {...}}.
        return (payload.get("data") or payload)["id"]

    def send_prompt(self, session_id: str, text: str, model: dict | None = None) -> None:
        body: dict = {"prompt": {"text": text}}
        if model:
            body["model"] = model
        r = httpx.post(f"{self.base_url}/api/session/{session_id}/prompt", json=body, timeout=self.timeout_s)
        r.raise_for_status()

    def wait(self, session_id: str) -> None:
        """Block until the current turn finishes."""
        httpx.post(f"{self.base_url}/api/session/{session_id}/wait", timeout=self.timeout_s).raise_for_status()

    def interrupt(self, session_id: str) -> None:
        httpx.post(f"{self.base_url}/api/session/{session_id}/interrupt", timeout=30)

    def events(self, session_id: str) -> Iterator[AgentEvent]:
        with httpx.stream("GET", f"{self.base_url}/api/session/{session_id}/event", timeout=None) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    try:
                        yield map_event(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        continue


def run_feedback_loop(
    initial_prompt: str,
    send_and_wait: Callable[[str], None],
    check: Callable[[], FeedbackReport],
    breaker: CircuitBreaker,
) -> tuple[FeedbackReport, Decision]:
    """prompt -> wait -> typecheck -> (if errors) feed them back -> ... until clean or bounded.

    Pure control flow: `send_and_wait` runs one agent turn (prompt+wait); `check` typechecks the
    workspace. Injectable so it's testable without a live OpenCode/gateway.
    """
    send_and_wait(initial_prompt)
    while True:
        report = check()
        decision = breaker.record(report.signature(), report.ok)
        if decision.action == "stop":
            return report, decision
        send_and_wait(report.as_agent_message())
