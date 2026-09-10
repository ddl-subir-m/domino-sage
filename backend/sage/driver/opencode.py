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
from dataclasses import field as dataclass_field

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


# OpenCode 1.18.4 serves TWO event streams, and only one of them streams text. Captured live
# against the pinned binary on 2026-08-26 (the capture agent_driver.OpenCodeDriver still has a TODO
# for), by driving a real turn and recording every frame:
#
#   /api/session/{id}/event   durable, resumable (?after=), one session. Checkpoints ONLY:
#                             text.started -> text.ended. NO text.delta, ever — measured 0 deltas
#                             on a reply that produced 24 on the other stream.
#   /event                    global, every session, no resume. Carries the deltas AND the
#                             authoritative end events. This is the one worth reading.
#
# A turn on /event runs:
#   step.started -> text.started -> text.delta xN -> text.ended (full text) -> step.ended finish=…
# and around a tool call:
#   tool.input.started -> tool.input.delta xN -> tool.input.ended -> tool.called (tool name here,
#   not before it) -> tool.success | tool.failed
#
# Envelope is {"id","type","properties"} — `properties`, not the durable stream's `data`. Because
# the stream is global it also carries server.connected, plugin.added, catalog.updated and other
# sessions' turns, so filtering on properties.sessionID is not optional.
_TOOL_STATUS = {
    "session.next.tool.called": "called",
    "session.next.tool.success": "success",
    "session.next.tool.failed": "failed",
}


def map_session_event(raw: dict, session_id: str) -> AgentEvent | None:
    """One frame of the global /event stream -> an AgentEvent for THIS session, or None to skip.

    None covers everything a turn watcher must ignore: another session's turn, and the stream's
    non-session traffic (server.connected, plugin.added, catalog.updated, ...) which carries no
    sessionID at all. Returning None rather than a "message" for those is the difference between a
    Thread that shows one turn and a Thread that shows the whole container's.
    """
    t = str(raw.get("type") or "")
    props = raw.get("properties") or {}
    if not t.startswith("session.next.") or props.get("sessionID") != session_id:
        return None

    if t == "session.next.text.delta":
        # The token stream. `delta` is a fragment — the caller appends; it is never the whole text.
        return AgentEvent(kind="message", payload={"delta": str(props.get("delta") or ""),
                                                   "final": False})
    if t == "session.next.text.ended":
        # Authoritative and complete, so a watcher that dropped deltas still lands the whole answer.
        return AgentEvent(kind="message", payload={"text": str(props.get("text") or ""),
                                                   "final": True})
    if t in _TOOL_STATUS:
        # Only tool.called names the tool. Measured live: a `write` that succeeded came back with
        # tool="" and nothing but its callID, so a consumer must remember the name from the call
        # and correlate on call_id rather than expect it again on the completion.
        return AgentEvent(kind="tool_run", payload={
            "tool": str(props.get("tool") or ""),
            "input": props.get("input"),
            "call_id": str(props.get("callID") or ""),
            "status": _TOOL_STATUS[t],
        })
    if t == "session.next.shell.started":
        # The command itself, at the moment it starts — what drives Chat's "Running Python…" line.
        return AgentEvent(kind="tool_run", payload={"tool": "bash",
                                                    "command": str(props.get("command") or ""),
                                                    "call_id": str(props.get("callID") or ""),
                                                    "status": "called"})
    if t == "session.next.step.failed":
        return AgentEvent(kind="error", payload={"error": props.get("error")})
    if t == "session.next.step.ended":
        # `finish` is the model's stop reason: "stop" ends the turn, "tool-calls" means another step
        # follows. A turn is NOT over at the first step.ended.
        return AgentEvent(kind="phase", payload={"finish": str(props.get("finish") or "")})
    return None


class SessionEvents:
    """A live read of the global /event stream, narrowed to one session. Iterate for AgentEvents.

    Connect is bounded but READ IS NOT: a stream that is idle because the model is thinking is
    working exactly as intended, and a read timeout here would sever it mid-turn — the same mistake
    that once showed up downstream as "TypeError: network error". A bounded connect is what lets a
    caller fall back to polling quickly when the stream cannot be had at all.

    `close()` is safe from another thread, and closing is not optional. /event never ends on its
    own, so a watcher that merely stopped iterating would leave a reader parked on a socket that
    goes on buffering — this session's NEXT turn, and the turn after that, into a queue nobody
    drains. A Chat session is reused across turns, so one abandoned reader per turn is a leak that
    grows with the conversation rather than with the turn.

    /event has no `?after=`, so a reconnect cannot replay what was missed. That is why text.ended
    is authoritative rather than the deltas being the only record of the answer.
    """

    def __init__(self, base_url: str, session_id: str, directory: str | None = None) -> None:
        self.base_url = base_url
        self.session_id = session_id
        self.directory = directory
        self._response: httpx.Response | None = None
        self._closed = False

    def close(self) -> None:
        """Drop the connection so a reader blocked in iter_lines unwinds instead of parking."""
        self._closed = True
        r, self._response = self._response, None
        if r is not None:
            try:
                r.close()
            except Exception:  # a socket that is already gone is the goal, not an error
                log.debug("session_events: close raced the reader", exc_info=True)

    def __iter__(self) -> Iterator[AgentEvent]:
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        # `directory` is not optional in practice, whatever the spec says it is. /event delivers
        # only the events of the directory the connection asks for, so a subscriber that omits it
        # gets the server's own working directory — and a session created for a workspace anywhere
        # else produces a stream of nothing but heartbeats. Measured: 0 session frames without it,
        # every frame of the turn with it. The failure is silent, which is what makes it dangerous:
        # the caller just falls back to polling and the streaming never happens.
        params = {"directory": self.directory} if self.directory else None
        with httpx.stream("GET", f"{self.base_url}/event", params=params, timeout=timeout) as r:
            r.raise_for_status()
            self._response = r
            for line in r.iter_lines():
                # Checked per frame as well as by close(): a reader that wakes on other traffic
                # should stop on its own rather than depend on the socket teardown winning a race.
                if self._closed:
                    return
                if not line.startswith("data: "):
                    continue
                try:
                    raw = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                ev = map_session_event(raw, self.session_id)
                if ev is not None:
                    yield ev


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


def _flatten_message(m: dict) -> dict:
    """v1's `{info, parts}` -> the flat message every caller in Sage already reads.

    v2 answered a message flat — `{id, type, text, content, ...}` — and callers key off
    `type == "assistant"`, walk `content`, and identify a part by `m["id"]` plus the part's own id
    (`_part_key`). v1 nests the same information under `info` and `parts`. Normalising here is what
    keeps the surface change inside this file: the test double stands in at this class's METHODS,
    not at the HTTP routes, so every test that speaks the old shape keeps speaking it.

    `role` becomes `type` because that is the key the callers branch on, and `text` is rebuilt from
    the text parts so a caller that wants the whole answer need not walk them itself.
    """
    info = dict(m.get("info") or {})
    parts = list(m.get("parts") or [])
    return {
        **info,
        "type": str(info.get("role") or ""),
        "content": parts,
        "text": "".join(str(p.get("text") or "") for p in parts if p.get("type") == "text"),
    }


@dataclass
class OpenCodeClient:
    """Sage's half of the OpenCode HTTP API — and it speaks v1 for everything a TURN does.

    OpenCode 1.18.4 serves TWO complete APIs from one process: 162 routes, of which the 58 under
    `/api/*` are v2 (`operationId: v2.session.*`) and the rest are v1. They are not two spellings of
    one thing. They keep SEPARATE MESSAGE STORES, and — the reason for this class's shape —
    **v2's prompt path sends the model no custom tools and no MCP tools.**

    Measured 2026-09-09 by pointing the provider `baseURL` at a logging stand-in gateway and reading
    the `tools` array that actually left OpenCode, with the real config, agent and model:

        POST /session/{id}/prompt_async   apply_patch bash glob grep LIVE_READ_FILES
                                          LIVE_READ_TABLE read skill task todowrite webfetch
        POST /api/session/{id}/prompt     apply_patch bash edit glob grep question read skill
                                          todowrite webfetch websearch write

    Sage used to be entirely on v2, so Live read could never arrive — as an MCP server, and then
    again as a custom tool. Every surface that reported it healthy (`opencode mcp list`, `GET /mcp`,
    `/experimental/tool`) reads the REGISTRY, which was correctly populated the whole time; v2 simply
    never handed that registry to the model. See ADR-0041.

    v2 also silently drops `agent` and `model` from the prompt body — its schema takes only
    `delivery, id, prompt, resume` — so every Chat turn ran as OpenCode's default `build` agent, not
    `sage-chat`. v1 takes both in the body and honours them.

    WHAT STAYS ON v2: session creation, because `POST /api/session` accepts `location.directory` and
    a v1 prompt into a v2-created session works end to end — verified for tools, messages, busy
    status and events. `agent_summaries` too: it reads resolved config, not session state.

    The method signatures and return shapes here are the contract the test double copies, so
    `messages()` normalises v1's `{info, parts}` back to the flat shape every caller already reads.
    """

    base_url: str
    timeout_s: float = 300.0
    # Only `GET /session/status` needs the workspace, and only the client knows which session
    # belongs to which. Remembered here rather than threaded through every caller; a session made
    # before this process started is simply absent, and `is_running` degrades to what it already
    # did when it could not tell — see there.
    _dirs: dict[str, str] = dataclass_field(default_factory=dict)

    def create_session(self, directory: str, model: dict | None = None) -> str:
        body: dict = {"location": {"directory": directory}}
        if model:
            body["model"] = model
        r = httpx.post(f"{self.base_url}/api/session", json=body, timeout=30)
        r.raise_for_status()
        payload = r.json()
        # /api/* responses wrap the resource in {"data": {...}}.
        sid = (payload.get("data") or payload)["id"]
        self._dirs[sid] = directory
        return sid

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        """This session's messages, OLDEST FIRST. Pass `limit` for only the newest few.

        READ FROM v1, because that is where the turns are: the two APIs keep separate message
        stores, so a v1 prompt is invisible to `GET /api/session/{id}/message` and vice versa —
        measured, 2 messages on one surface and 0 on the other for the same session id.

        v1 has no `order`. It answers chronologically already, and `limit` gives the NEWEST N still
        in that order (measured on a six-message session: `limit=2` returned the last user/assistant
        pair, oldest of the two first). So the desc-then-reverse dance v2 needed is gone — along
        with the bug it existed for, where a desc list made the poll loops keep the EARLIEST text of
        a turn and show an intermediate "let me try..." as the finished answer.

        `limit` exists because this is polled once a second for the length of a turn, and the whole
        transcript came back every time — a cost that grows with the conversation rather than with
        the question. The newest N messages are enough: a part that scrolls out of the window was
        emitted on an earlier poll and is already in the caller's `seen` set, so nothing is lost by
        not looking at it again.
        """
        params: dict = {"limit": limit} if limit is not None else {}
        r = httpx.get(f"{self.base_url}/session/{session_id}/message",
                      params=params, timeout=30)
        r.raise_for_status()
        return [_flatten_message(m) for m in r.json()]

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
        # v1 carries text and media as PARTS, where v2 took `prompt.text` and `prompt.files`. The
        # base64 constraint above is unchanged — it is a property of how OpenCode forwards media,
        # not of which API asked it to.
        parts: list[dict] = [{"type": "text", "text": text}]
        images = [a for a in (attachments or []) if a.get("image_uri")]
        for a in images:
            uri = str(a["image_uri"])
            mime = uri[5:].split(";", 1)[0] if uri.startswith("data:") else "application/octet-stream"
            parts.append({"type": "file", "mime": mime, "url": uri, "filename": a["name"]})
        body: dict = {"parts": parts}
        if attachments:
            log.info("prompt: %d attachment(s), %d media part(s), body %d bytes",
                     len(attachments), len(images), len(json.dumps(body)))
        # v1 HONOURS these. v2 took only `delivery, id, prompt, resume` and dropped both on the
        # floor, which is why every Chat turn ran as the default `build` agent instead of
        # `sage-chat` — the agent whose prompt is the mirrored AGENTS.md.
        if model:
            body["model"] = model
        if agent:
            body["agent"] = agent
        r = httpx.post(f"{self.base_url}/session/{session_id}/prompt_async",
                       json=body, timeout=self.timeout_s)
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
            f"{self.base_url}/session/{session_id}/summarize",
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

    def is_running(self, session_id: str, directory: str | None = None) -> bool:
        # 30s (was 15s): OpenCode's Node server can be briefly CPU-bound (serializing a large context)
        # and slow to answer this health poll. build_stream also tolerates a poll timeout, but a more
        # generous window avoids tripping that path on a normal busy turn.
        # v1 answers {sid: {"type": "busy"}} and needs the WORKSPACE to answer at all — without
        # `directory` it returns {} for a session that is plainly running. v2's /session/active
        # sees only v2 turns, so it reported every v1 turn as finished the instant it started.
        work = directory or self._dirs.get(session_id)
        params = {"directory": work} if work else {}
        r = httpx.get(f"{self.base_url}/session/status", params=params, timeout=30)
        r.raise_for_status()
        return str((r.json().get(session_id) or {}).get("type") or "") == "busy"

    def wait_for_idle(self, session_id: str, timeout_s: float = 300, poll_s: float = 1.0,
                      appear_grace_s: float = 10.0, directory: str | None = None) -> None:
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
            running = self.is_running(session_id, directory)
            if running:
                appeared = True
            elif appeared:
                return  # was running, now idle -> turn complete
            elif time.monotonic() - start > appear_grace_s:
                return  # never registered (trivial/no-op turn)
            time.sleep(poll_s)

    def interrupt(self, session_id: str) -> None:
        # v1's spelling of interrupt. v2's `/interrupt` only knows about v2 turns, and the turn
        # is a v1 one now, so stopping through it would report success and stop nothing.
        httpx.post(f"{self.base_url}/session/{session_id}/abort", timeout=30)

    def session_events(self, session_id: str, *, directory: str | None = None) -> SessionEvents:
        """This session's turn events, live, off the global /event stream. See SessionEvents.

        `directory` is the workspace the session was created for. Omit it and the stream is silent.
        """
        return SessionEvents(self.base_url, session_id, directory)

    def events(self, session_id: str) -> Iterator[AgentEvent]:
        """The per-session DURABLE stream: resumable (?after=), but checkpoints only — it carries
        no text.delta. Use session_events() to watch a turn; this is for replaying one.

        UNUSED, and v2-only: it can no longer replay anything Sage does, because the turns run on
        v1 now and the two APIs do not share a store. Left in place rather than deleted because
        nothing calls it either way — but do not reach for it without reading the class docstring.
        """
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
