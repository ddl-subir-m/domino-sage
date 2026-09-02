"""A scripted stand-in for OpenCodeClient, so the turn path in `_build_stream` can be tested.

Everything in `_build_stream` past the dispatch decision — the plan gate, the answer-only short
circuit, the read-only violation check, the failure recording — was previously verified by reading,
because reaching it needed a live OpenCode server. Three commits of gate logic accumulated behind
that gap. This closes it.

The fake is scripted, not simulated: you hand it a list of `Turn`s and the Nth `send_prompt` performs
the Nth turn. It does the two things the orchestrator actually observes about an agent — it produces
assistant message parts, and it writes files into the workspace — and nothing else. It is not a model
and makes no decisions; a test that wants "the agent wrote nothing" says so in the script.

File writes are REAL writes into the workspace, not recorded intentions. `agent_wrote()` in the
orchestrator asks the git snapshot what changed on disk, not what tools claimed to run, so a fake
that only emitted `write` tool parts would pass the tool-name check and fail the ground-truth one —
which is the exact discrepancy the gate-violation code exists to catch. Faking the tool parts without
the writes would make that code untestable in the one direction that matters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Turn:
    """What the agent does for one prompt.

    `text` becomes assistant text parts — streamed to the user on a build turn, collected into the
    plan card on a gated one. `writes` maps workspace-relative paths to contents and produces both
    the real file write and the matching `write` tool part, because the orchestrator cross-checks
    those two against each other. `tools` is for calls with no file effect (read, grep, bash)."""

    text: str = ""
    prelude: str = ""
    writes: dict[str, str] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    # OpenCode usage on the assistant message. None means the fake omits `tokens` (turn-count
    # fallback). Tests that want the token threshold set this to `{input, output, ...}`.
    tokens: dict | None = None
    # A live OpenCode has sent a bare string in `content` instead of a `{"type": ...}` dict — real,
    # not hypothetical (the crash this reproduces). True prepends one ahead of every other part, so
    # a test can assert the turn survives it rather than reconstructing the shape by hand.
    stray_content: bool = False


class FakeOpenCode:
    """Implements the slice of OpenCodeClient that `_build_stream` and `_ensure_session` call.

    Polling: `is_running` reports True exactly once per prompt, then False. The orchestrator's loop
    needs to SEE a turn start before it will believe it finished (`appeared and not running`), and a
    fake that was never running would sit in the 12-second not-appeared timeout instead — turning
    every test into a 12-second test. One True is the shortest script that exercises the real exit."""

    def __init__(self, workspace: Path, turns: list[Turn] | None = None) -> None:
        self.workspace = Path(workspace)
        self.turns = list(turns or [])
        # Recorded for assertions: which agent each prompt asked for is how a test checks that a
        # gated turn ran as sage-plan rather than the build agent.
        self.prompts: list[dict] = []
        self.interrupted = 0
        # Per-session message stores. A phased build runs each phase in its own session, and the
        # whole point is that a phase CANNOT see the others' context — a single shared list would
        # hand every phase the previous ones' transcript and quietly test the opposite of the
        # feature. Keyed by session id; `sessions` records the create calls for assertions.
        self.sessions: list[dict] = []
        self._by_session: dict[str, list[dict]] = {"fake-session": []}
        self._running: dict[str, bool] = {}
        self._next = 0
        self.compacts: list[dict] = []
        self.compact_error: Exception | None = None
        # When True, is_running stays true until interrupt — a hung DataSourceClient.query.
        self.stay_running = False

    # --- session ---------------------------------------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        rec = next((s for s in self.sessions if s["id"] == session_id), None)
        return Path(rec["directory"]) if rec else self.workspace

    def create_session(self, directory: str, model: dict | None = None) -> str:
        # The first session keeps the historic id so every pre-existing test is untouched; phases
        # get distinct ones.
        sid = "fake-session" if not self.sessions else f"fake-session-{len(self.sessions) + 1}"
        self.sessions.append({"id": sid, "directory": directory})
        self._by_session.setdefault(sid, [])
        return sid

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        # A copy: the orchestrator iterates this while its own emit-once bookkeeping mutates, and a
        # shared list would let a test's assertions and the loop's state drift apart.
        # Oldest first, like the real client — which has to ask for that order explicitly.
        msgs = list(self._by_session.get(session_id, []))
        return msgs[-limit:] if limit is not None else msgs

    def last_message_id(self, session_id: str) -> str | None:
        msgs = self._by_session.get(session_id, [])
        return msgs[-1]["id"] if msgs else None

    def agent_summaries(self) -> list[dict]:
        return []

    # --- the turn --------------------------------------------------------------------------------

    def send_prompt(self, session_id: str, text: str, model: dict | None = None,
                    agent: str | None = None, attachments: list[dict] | None = None,
                    chat: bool = False) -> None:
        # `session` recorded too: a phased build's assertions are mostly about WHICH session saw
        # which prompt.
        self.prompts.append({"text": text, "agent": agent, "attachments": attachments,
                             "session": session_id})
        # One flat script consumed in order, regardless of session — the Nth send_prompt across the
        # whole run performs the Nth scripted turn, so tests read top-to-bottom.
        turn = self.turns[self._next] if self._next < len(self.turns) else Turn()
        self._next += 1
        self._running[session_id] = True

        msgs = self._by_session.setdefault(session_id, [])
        parts: list[dict] = []
        n = self._next
        if turn.stray_content:
            parts.append("a bare string OpenCode sent in place of a part dict")
        for j, tool in enumerate(turn.tools):
            parts.append({"id": f"m{n}-t{j}", "type": "tool", "tool": tool,
                          "state": {"status": "completed"}})
        # Writes land where the real agent's would: relative to the directory the session was
        # opened in. A Build session stands in the Built App (`apps/<appId>/`) and a Chat session
        # in `.sage/chat-work`, whose links are what make a Chat path resolve at all.
        base = self._session_dir(session_id)
        for j, (rel, body) in enumerate(turn.writes.items()):
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            parts.append({"id": f"m{n}-w{j}", "type": "tool", "tool": "write",
                          "state": {"status": "completed", "input": {"filePath": rel}}})
        if turn.prelude:
            parts.append({"id": f"m{n}-p", "type": "text", "text": turn.prelude})
        if turn.text:
            parts.append({"id": f"m{n}-x", "type": "text", "text": turn.text})
        assistant: dict = {"id": f"m{n}", "type": "assistant", "content": parts}
        if turn.tokens is not None:
            assistant["tokens"] = turn.tokens
        msgs.append(assistant)

    def summarize(self, session_id: str, provider_id: str, model_id: str, *, auto: bool = False) -> None:
        if self.compact_error is not None:
            raise self.compact_error
        n = len(self.compacts) + 1
        self.compacts.append({
            "session": session_id, "providerID": provider_id, "modelID": model_id, "auto": auto,
        })
        msgs = self._by_session.setdefault(session_id, [])
        msgs.append({"id": f"c{n}", "type": "user",
                     "content": [{"type": "compaction", "auto": auto}]})
        msgs.append({"id": f"cs{n}", "type": "assistant", "summary": True,
                     "content": [{"type": "text", "text": "compacted"}]})

    def is_running(self, session_id: str) -> bool:
        if self.stay_running:
            return True
        was = self._running.get(session_id, False)
        self._running[session_id] = False
        return was

    def wait_for_idle(self, session_id: str, timeout_s: float = 300, poll_s: float = 1.0,
                      appear_grace_s: float = 10.0) -> None:
        self._running[session_id] = False

    def interrupt(self, session_id: str) -> None:
        self.interrupted += 1
        self.stay_running = False
        self._running[session_id] = False
