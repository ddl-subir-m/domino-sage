"""OpenCode HTTP driver (DESIGN Seam 3) + the closed feedback loop (Step 5 wiring).

Talks to a running `opencode serve` (server.py). Creates a session scoped to a workspace,
sends prompts, waits for a turn to finish, streams events (normalized to AgentEvent), and runs
the prompt -> wait -> typecheck -> feed-errors-back loop until clean or the breaker stops it.

Leak rule (DESIGN): the shim/router never see OpenCode types; all OpenCode specifics live here.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from ..feedback.circuit_breaker import CircuitBreaker, Decision
from ..feedback.runner import FeedbackReport
from .agent_driver import AgentEvent

log = logging.getLogger(__name__)


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

    def messages(self, session_id: str) -> list[dict]:
        r = httpx.get(f"{self.base_url}/api/session/{session_id}/message", timeout=30)
        r.raise_for_status()
        return r.json().get("data", [])

    def last_message_id(self, session_id: str) -> str | None:
        ms = self.messages(session_id)
        return ms[-1]["id"] if ms else None

    def send_prompt(self, session_id: str, text: str, model: dict | None = None, agent: str | None = None,
                    files: list[str] | None = None) -> None:
        """Send a prompt. `/prompt` returns before the turn completes (async), so callers must
        wait_for_completion() to know the edits landed.

        `agent` selects a named agent from opencode.json (e.g. "sage-ask", "sage-plan") whose
        `permission` block OpenCode enforces at its own tool-execution layer — the real read-only
        guarantee for Ask/Plan modes, since the shim's tools-list filtering only hides tools from
        the model's view of one request and can't stop OpenCode from running a tool it already
        knows about (e.g. `bash`, which the tools filter never covered either).

        `files` are absolute paths to attach as prompt file parts (`file://` URIs), so the agent
        receives the referenced data directly. Attachments are best-effort: if the server rejects
        the prompt because of them, we retry text-only rather than lose the turn — the agent can
        still resolve the same paths from the workspace AGENTS.md block."""
        def _body() -> dict:
            b: dict = {"prompt": {"text": text}}
            if model:
                b["model"] = model
            if agent:
                b["agent"] = agent
            return b

        url = f"{self.base_url}/api/session/{session_id}/prompt"
        body = _body()
        if files:
            body["prompt"]["files"] = [{"uri": f"file://{p}", "name": os.path.basename(p)} for p in files]
        r = httpx.post(url, json=body, timeout=self.timeout_s)
        if files and r.status_code >= 400:
            log.warning("send_prompt: server rejected file attachments (HTTP %s); retrying text-only", r.status_code)
            r = httpx.post(url, json=_body(), timeout=self.timeout_s)
        r.raise_for_status()

    def is_running(self, session_id: str) -> bool:
        r = httpx.get(f"{self.base_url}/api/session/active", timeout=15)
        r.raise_for_status()
        return session_id in r.json().get("data", {})

    def wait_for_idle(self, session_id: str, timeout_s: float = 300, poll_s: float = 1.0, appear_grace_s: float = 10.0) -> None:
        """Block until the whole multi-step turn finishes.

        A turn spans several steps (model->tool->model); /api/session/active reports
        {sid: {"type":"running"}} for the duration and {} when idle. We wait for the session to
        register as running, then for it to go idle. `/wait` on the server 503s, and a single
        'completed assistant message' fires mid-turn — active-polling is the reliable signal.
        """
        import time

        start = time.monotonic()
        appeared = False
        while time.monotonic() - start < timeout_s:
            running = self.is_running(session_id)
            if running:
                appeared = True
            elif appeared:
                return  # was running, now idle -> turn complete
            elif time.monotonic() - start > appear_grace_s:
                return  # never registered (trivial/no-op turn)
            time.sleep(poll_s)

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
