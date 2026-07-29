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


def _file_preview(path: str, max_lines: int = 30, max_bytes: int = 8000) -> str:
    """A bounded, read-tool-free preview of an attached file so the agent learns the schema WITHOUT
    opening it. OpenCode's (Node) read tool hangs on files outside its project root (e.g. /mnt/data
    dataset mounts), so we read a small head here (Python reads the mount fine) and inline it. Bounded
    by bytes AND lines so a huge or single-giant-line file can't blow up the prompt."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.read(max_bytes)
    except OSError as e:
        return f"  (preview unavailable: {type(e).__name__})"
    lines = data.splitlines()
    shown = lines[:max_lines]
    body = "\n".join(shown)
    more = len(data) == max_bytes or len(lines) > max_lines
    note = f"\n  … (preview truncated; first {len(shown)} lines)" if more else ""
    return f"{body}{note}"


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

        `files` are absolute paths of user-@mentioned attachments. We surface them by appending an
        explicit "Attached files" section to the prompt TEXT (not as OpenCode file parts): 1.18.4's
        `/prompt` body carries no `files`/attachment field, so a `files` key is silently dropped and
        the reference never reaches the agent. Naming the real local paths in the text is
        version-independent and definitely seen — the agent's read tool follows the symlinks. This
        rides the same `{"prompt":{"text":...}}` shape the base turn already uses."""
        if files:
            listing = "\n\n".join(f"- {p}\n{_file_preview(p)}" for p in files)
            text = (
                f"{text}\n\nAttached data files (the user @mentioned these). A PREVIEW of each is "
                f"included below. Do NOT open these with the read tool — they can be large and reading "
                f"them is unnecessary and slow (and files on mounts outside the project root can stall "
                f"the read tool). Use the preview to learn the schema; the built app loads the FULL file "
                f"at runtime from its served URL (see the 'Attached data' section in AGENTS.md). The "
                f"absolute path is shown only for reference:\n\n{listing}")
        body: dict = {"prompt": {"text": text}}
        if model:
            body["model"] = model
        if agent:
            body["agent"] = agent
        r = httpx.post(f"{self.base_url}/api/session/{session_id}/prompt", json=body, timeout=self.timeout_s)
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
