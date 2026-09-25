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


def execution_plan(name: str = "Test App", summary: str = "A test app.",
                   step: str = "Build the app", *, files: str = "src/App.tsx",
                   work: str | None = None, done_when: str = "The requested change is visible.",
                   include_title: bool = True) -> str:
    """A minimal valid v1 plan for tests whose subject is not the plan contract."""
    title = f"# {name}\n\n" if include_title else ""
    return title + (
        f"{summary}\n\n"
        "## Problem & outcome\n"
        "The requested workflow is unavailable; this app makes it available.\n\n"
        "## Who uses this\n"
        "The app user.\n\n"
        "## What it does\n"
        f"- {step}.\n\n"
        "## Screens\n"
        f"- **{name}** — Shows the requested workflow.\n\n"
        "## Done when\n"
        f"- {done_when}\n\n"
        "## Plan\n"
        f"### 1. {step}\n"
        f"- Files — {files}\n"
        f"- Do — {work or step + '.'}\n"
        f"- Done when — {done_when}"
    )


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
    # A live OpenCode has left a tool call's arguments as the raw, unparsed string when the model
    # emitted invalid JSON for them, then failed the session — real, not hypothetical (the
    # AttributeError of 2026-09-05 reproduces from this shape). True appends one still-running
    # `write` after this turn's other parts, which is where the live one sat: last, and open.
    broken_write: bool = False
    # The other shape a malformed call takes, and the one OpenCode 1.18.4 actually emits (pinned
    # by test_an_invalid_tool_call_never_ran_in_the_pinned_opencode.py): the AI SDK cannot parse
    # or validate the arguments, `experimental_repairToolCall` rewrites the call to the built-in
    # `invalid` tool with `{tool, error}` as its input, that tool runs, and the part lands
    # COMPLETED. The session goes on; the intended tool never executed. Each name here is one such
    # part, in order, ahead of `tools_after` and the writes. `invalid_error` is the SDK's message,
    # which live carries the model's raw arguments — so the default holds argument-like content on
    # purpose, and a test that asserts nothing of it leaks is asserting against the real shape.
    invalid_calls: list[str] = field(default_factory=list)
    invalid_error: str = ('Invalid input for tool write: Type validation failed: Value: '
                          '{"filePath":"src/Dashboard.tsx","conte')
    # `completed` is what the binary emits. Another status is a shape the classifier must treat
    # as uncertain, and a test sets it to prove that.
    invalid_status: str = "completed"
    # Completed calls with no file effect that land AFTER the invalid ones: a different tool
    # finishing is not proof the invalid call was recovered from, and this is how a test plants it.
    tools_after: list[str] = field(default_factory=list)
    # Tool calls left IN FLIGHT with a WELL-FORMED input, as {tool: subject} — a path for
    # write/edit/read, a command for bash. `broken_write` above is the other shape, where OpenCode
    # hands over a raw unparsed string; this one is the ordinary case and it is where the time goes.
    # Measured on #497: OpenCode reports a part `pending` with `input={}` for the whole of a 7.7 s
    # streamed argument and never shows a character of it, so "in flight with a subject and no
    # content" is the state a long call is really in.
    streaming: dict[str, str] = field(default_factory=dict)
    # The message's OWN failure, which is where a refusal of the REQUEST lands — a content filter,
    # an auth error — as opposed to a step's, which arrives on the event stream. Set it to the
    # OpenCode error shape, `{"name": ..., "data": {"message": ...}}`.
    error: dict | None = None


class FakeOpenCode:
    """Implements the slice of OpenCodeClient that `_build_stream` and `_ensure_session` call.

    Polling: `is_running` reports True exactly once per prompt, then False. The orchestrator's loop
    needs to SEE a turn start before it will believe it finished (`appeared and not running`), and a
    fake that was never running would sit in the 12-second not-appeared timeout instead — turning
    every test into a 12-second test. One True is the shortest script that exercises the real exit.

    THIS FAKE DISPATCHES SYNCHRONOUSLY AND THE REAL DRIVER DOES NOT, which is safe for every caller
    in the tree today and is a trap for the next one. `send_prompt` here marks the session running
    and performs the scripted writes inline, both before it returns. `OpenCodeClient.send_prompt`
    posts to `/prompt_async` and returns before the turn begins (`driver/opencode.py:354`), and
    `/session/status` does not go busy with the POST — so a real "is it still running?" a moment
    after dispatching answers "not yet" far more often than "done". `wait_for_idle` carries
    `appear_grace_s` and an `appeared` latch for exactly that, and the poll loops in `_chat_stream`
    and `_build_stream` keep the same latch themselves.

    The hazard is not that the gap exists; it is that this fake makes the CORRECT wait and the
    BROKEN one indistinguishable. A wait written as "return on the first not-busy reading" is green
    here and returns on its first poll against the real server. Measured on #454: a bounded wait
    after a dispatch ended the slice immediately, the work it had just asked for was interrupted,
    and the feature did nothing at all — with seventeen tests green over it.

    Reachable today? No, and it was checked rather than assumed. Derive the population again
    rather than trusting this sentence — `grep -rn '\\.is_running(\\|\\.wait_for_idle(' sage/` —
    because the list rots the next time somebody waits on a session, and because the FIRST pass
    over it here was short. Four shapes, not the three it originally named:

    * `wait_for_idle`, which carries the grace itself.
    * A poll loop holding its own `appeared` latch (`_chat_stream`, `_build_stream`).
    * A wait after an INTERRUPT (`_stop_wedged_session`), where returning on the first
      not-running reading is the point rather than the bug.
    * A one-shot `is_running` GUARD in front of a graced wait — `_maybe_compact_chat` dispatches
      `summarize` and then `if client.is_running(sid): client.wait_for_idle(sid,
      appear_grace_s=2.0)`. The guard is a single unlatched read, so it has the shape, and it is
      tolerated rather than safe by construction: `summarize` usually blocks until the summary
      loop finishes (its own docstring says so), and when it does not, the cost is a compaction
      running on past the lock rather than a feature that does nothing. Not the same blast radius,
      and worth knowing it is the same shape.

    That fourth one was missed on the first sweep and found by a second pair of eyes re-deriving
    the same grep. A classification is a claim about a population, and a three-item one written
    from the instance that bit you is short in depth as well as in count.

    If you are writing a new wait that follows a dispatch, this fake cannot test it. Subclass and
    lag the status the way the server does; `DispatchIsAsync` in
    `test_a_turn_stopped_at_the_ceiling_keeps_what_it_measured.py` is one that does."""

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
        self._dirs: dict[str, str] = {}
        # session id -> directory, for the sessions the orchestrator reused rather than created.
        self.noted: dict[str, str] = {}
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
        self._dirs[sid] = directory
        self._by_session.setdefault(sid, [])
        return sid

    def note_session_dir(self, session_id: str, directory: str) -> None:
        # Recorded rather than acted on: the real client needs this to answer `is_running` for a
        # session it did not create, and the only thing a test can check is that it was told.
        self.noted[session_id] = directory
        self._dirs[session_id] = directory

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
                    chat: bool = False, tail: str = "") -> None:
        # `session` recorded too: a phased build's assertions are mostly about WHICH session saw
        # which prompt.
        self.prompts.append({"text": text, "agent": agent, "attachments": attachments,
                             "session": session_id, "model": model, "tail": tail})
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
                          # The wire's own shape: a top-level `time`, and no time under `state`.
                          # A double that times its parts somewhere OpenCode does not would make
                          # _tool_duration_ms testable and still wrong (see that function).
                          "time": {"created": 1_700_000_000_000, "ran": 1_700_000_000_100,
                                   "completed": 1_700_000_000_350},
                          "state": {"status": "completed"}})
        for j, intended in enumerate(turn.invalid_calls):
            parts.append({"id": f"m{n}-i{j}", "type": "tool", "callID": f"call-m{n}-i{j}",
                          "tool": "invalid",
                          "time": {"created": 1_700_000_000_000, "ran": 1_700_000_000_100,
                                   "completed": 1_700_000_000_350},
                          "state": {"status": turn.invalid_status,
                                    "input": {"tool": intended, "error": turn.invalid_error},
                                    "output": ("The arguments provided to the tool are invalid: "
                                               + turn.invalid_error),
                                    "title": "Invalid Tool", "metadata": {}}})
        for j, tool in enumerate(turn.tools_after):
            parts.append({"id": f"m{n}-a{j}", "type": "tool", "tool": tool,
                          "time": {"created": 1_700_000_000_000, "ran": 1_700_000_000_100,
                                   "completed": 1_700_000_000_350},
                          "state": {"status": "completed", "input": {}}})
        # Writes land where the real agent's would: relative to the directory the session was
        # opened in. A Build session stands in the Built App (`apps/<appId>/`) and a Chat session
        # in `.sage/chat-work`, whose links are what make a Chat path resolve at all.
        base = self._session_dir(session_id)
        for j, (rel, body) in enumerate(turn.writes.items()):
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            parts.append({"id": f"m{n}-w{j}", "type": "tool", "tool": "write",
                          "time": {"created": 1_700_000_000_000, "ran": 1_700_000_000_100,
                                   "completed": 1_700_000_000_350},
                          "state": {"status": "completed", "input": {"filePath": rel}}})
        if turn.broken_write:
            parts.append({"id": f"m{n}-b", "type": "tool", "tool": "write",
                          "state": {"status": "running",
                                    "input": '{"filePath": "src/Dashboard.tsx", "conte'}})
        for j, (tool, subject) in enumerate(turn.streaming.items()):
            key = "command" if tool == "bash" else "pattern" if tool == "grep" else "filePath"
            parts.append({"id": f"m{n}-s{j}", "type": "tool", "tool": tool,
                          "state": {"status": "running", "input": {key: subject}}})
        if turn.prelude:
            parts.append({"id": f"m{n}-p", "type": "text", "text": turn.prelude})
        if turn.text:
            parts.append({"id": f"m{n}-x", "type": "text", "text": turn.text})
        assistant: dict = {"id": f"m{n}", "type": "assistant", "content": parts}
        if turn.tokens is not None:
            assistant["tokens"] = turn.tokens
        if turn.error is not None:
            assistant["error"] = turn.error
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
