"""OpenCode HTTP driver (DESIGN Seam 3) + the closed feedback loop (Step 5 wiring).

Talks to a running `opencode serve` (server.py). Creates a session scoped to a workspace,
sends prompts, waits for a turn to finish, streams events (normalized to AgentEvent), and runs
the prompt -> wait -> typecheck -> feed-errors-back loop until clean or the breaker stops it.

Leak rule (DESIGN): the shim/router never see OpenCode types; all OpenCode specifics live here.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from ..feedback.circuit_breaker import CircuitBreaker, Decision
from ..feedback.runner import FeedbackReport
from .agent_driver import AgentEvent

log = logging.getLogger("sage.driver")  # "sage.*" -> surfaced by /api/diag's log tail


def with_attachment_listing(text: str, attachments: list[dict] | None, *, chat: bool = False) -> str:
    """Append @mentioned file descriptors to a prompt. Chat must not get the Build-app preamble."""
    if not attachments:
        return text

    def _entry(a: dict) -> str:
        s = f"- {a['name']} — {a['summary']}\n  path: {a['path']}"
        if "image_uri" in a and not a["image_uri"]:
            s += ("\n  NOTE: this image was NOT shown to you — it is too large to inline. "
                  "You cannot see its contents and reading the file will not help. "
                  "Say so plainly rather than guessing or searching for it.")
        if a.get("detail"):
            s += f"\n{a['detail']}"
        return s

    listing = "\n\n".join(_entry(a) for a in attachments)
    if chat:
        return (
            f"{text}\n\nThe user @mentioned these files. Paths are relative to this Chat working "
            f"directory (examples/ and .sage/scratch/ are linked here). The lines below are shape, "
            f"not the rows — read the file at the path shown when you need the data:\n\n{listing}"
        )
    return (
        f"{text}\n\nAttached data files (the user @mentioned these). Below is a DESCRIPTION "
        f"OF SHAPE (schema/structure) for each — it is NOT the data. You MAY read a file at "
        f"the workspace-relative path shown if you genuinely need more than the descriptor, "
        f"but do not do so routinely, and do NOT read a large file: it bloats the context and "
        f"has previously wedged the OpenCode server. Judge from the size/shape given. "
        f"Do NOT hardcode, paste, or copy any sample values into the app as its data — the "
        f"descriptor is a summary and the real file has far more. The built app MUST load the "
        f"FULL file at runtime by fetching its served URL (see the 'Attached data' section in "
        f"AGENTS.md). Never copy a file into src/ — that leaks data into "
        f"git; public/data/ is gitignored on purpose:\n\n{listing}"
    )


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
                    attachments: list[dict] | None = None, *, chat: bool = False) -> None:
        """Send a prompt. `/prompt` returns before the turn completes (async), so callers must
        wait_for_completion() to know the edits landed.

        `agent` selects a named agent from opencode.json (e.g. "sage-ask", "sage-plan"), which
        applies that agent's system prompt. Do NOT rely on its `permission` block for read-only:
        OpenCode does not enforce `deny` on this path. Verified 2026-07-29 against 1.18.4 — the
        config loads, `GET /api/agent` lists sage-ask as resolved, the turn requests it, and it
        still ran `bash` and wrote a file. Only `"ask"` diverts a tool to the approval handler;
        `"deny"` is treated as preapproved and executes. The read-only guarantee lives entirely in
        the shim, which strips READ_ONLY_DENIED from the request so the tool is never offered.

        `attachments` are user-@mentioned files, already described and bounded by the caller
        (orchestrator/describe.py) — each is {"path", "name", "summary", "detail"} plus an optional
        "image_uri" (a `data:<mime>;base64,...` string). This method only RENDERS them; it never
        touches the filesystem, so a PDF/PNG can't get mojibake-inlined and a huge file can't stall
        the request.

        `path` MUST be workspace-relative (public/data/<slug>/uploads/<f>), not the /mnt/data
        absolute path: that mount lives outside OpenCode's project root and its (Node) read tool
        HANGS indefinitely on paths there — a confirmed production failure. The same bytes are
        reachable in-root through the public/data symlink, and reading THAT is confirmed working,
        which is why the old blanket "do NOT use the read tool" ban is lifted here: the ban only
        ever existed to dodge the hang, and the descriptor is now the reason not to read, not a
        prohibition.

        Descriptors ride the prompt TEXT — version-independent and definitely seen.

        IMAGES additionally ride `prompt.files`, which DOES exist on 1.18.4 (`PromptInput.files`,
        items `{uri, name}`) — an earlier note here claimed the field did not exist and that a
        `files` key was silently dropped; that was wrong, verified 2026-07-30 against the pinned
        binary's own OpenAPI spec and live. Two constraints found the same way:
          - `uri` MUST be a `data:<mime>;base64,...` URI. Every file-path form (file:///abs, bare
            /abs, and workspace-relative) makes OpenCode emit malformed media — the turn dies with
            "media must contain valid base64". Inlining also sidesteps the /mnt/data hang entirely,
            since no path leaves Sage.
          - The model must be vision-capable or the provider rejects the turn (bedrock-qwen3-coder
            returns HTTP 400). The shim strips image parts before they reach a model that can't take
            them, so this method attaches unconditionally and lets that policy live in one place.
        Confirmed end to end (OpenCode -> shim -> gateway -> sonnet): the model read a test image
        correctly."""
        text = with_attachment_listing(text, attachments, chat=chat)
        body: dict = {"prompt": {"text": text}}
        images = [{"uri": a["image_uri"], "name": a["name"]}
                  for a in (attachments or []) if a.get("image_uri")]
        if images:
            body["prompt"]["files"] = images
        if attachments:
            log.info("prompt: %d attachment(s), %d media part(s), body %d bytes",
                     len(attachments), len(images), len(json.dumps(body)))
        if model:
            body["model"] = model
        if agent:
            body["agent"] = agent
        r = httpx.post(f"{self.base_url}/api/session/{session_id}/prompt", json=body, timeout=self.timeout_s)
        r.raise_for_status()

    def summarize(self, session_id: str, provider_id: str, model_id: str, *, auto: bool = False) -> None:
        """Compact this session's model context (OpenCode 1.18.4: POST /summarize).

        Body is `{providerID, modelID, auto}`. `auto=False` so OpenCode does not inject a synthetic
        "continue" user turn after the summary — Sage only wants the checkpoint, then the next
        real user prompt. The call may block until the summary loop finishes; callers still
        wait_for_idle when the session is running, in case a later build returns before it idles.
        """
        body = {"providerID": provider_id, "modelID": model_id, "auto": auto}
        r = httpx.post(
            f"{self.base_url}/api/session/{session_id}/summarize",
            json=body, timeout=self.timeout_s)
        r.raise_for_status()

    def agent_summaries(self) -> list[dict]:
        """The agents OpenCode actually resolved from its config. `send_prompt(agent=...)` silently
        falls back to the default build agent when a name is missing, so a mode's `permission`/`prompt`
        block goes inert with no error — this is the only way to see that without a shell.

        Field names in the response aren't pinned by us, so keep every short scalar rather than
        picking an identifier key: whatever OpenCode calls it (name/id/...), it survives. Long values
        (agent system prompts) are dropped so the diag payload stays readable."""
        r = httpx.get(f"{self.base_url}/api/agent", timeout=30)
        r.raise_for_status()
        payload = r.json()
        agents = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(agents, dict):  # keyed by name rather than a list
            agents = [{"key": k, **v} if isinstance(v, dict) else {"key": k} for k, v in agents.items()]
        out = []
        for a in agents if isinstance(agents, list) else []:
            if not isinstance(a, dict):
                out.append({"raw": str(a)[:80]})
                continue
            out.append({k: v for k, v in a.items()
                        if isinstance(v, (str, bool, int, float, type(None)))
                        and len(str(v)) <= 80} or {"keys": sorted(a)[:12]})
        return out

    def is_running(self, session_id: str) -> bool:
        # 30s (was 15s): OpenCode's Node server can be briefly CPU-bound (serializing a large context)
        # and slow to answer this health poll. build_stream also tolerates a poll timeout, but a more
        # generous window avoids tripping that path on a normal busy turn.
        r = httpx.get(f"{self.base_url}/api/session/active", timeout=30)
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
