"""PhaseClassifier — per-step plan-vs-implement classification (DESIGN.md Seam 1).

A "step" is one model inference: one `/v1/chat/completions` call. The shim classifies each
step at the moment it routes it, so Auto mode picks the strong (plan) model while the agent is
reasoning/reading and the cheaper (implement) model while it is writing code — and flips *back*
to plan when the agent returns to exploring. That bidirectional behaviour is what makes
interleaved turns (plan → edit → re-plan → edit) route correctly, per step.

Design notes:
  - Pure function of the OpenAI-compatible `messages`. No I/O, no shared mutable state, so
    concurrent requests never race over a phase flag.
  - Scoped to the CURRENT turn. We scan back only to the most recent user message, so a follow-up
    prompt (a new feature, or a feedback-loop fix turn) starts fresh in PLAN even though earlier
    turns wrote files.
  - Within the turn: PLAN until the agent's first file write, then IMPLEMENT for the rest of the
    turn. Everything else — reads, search, shell, and todo/progress bookkeeping (todowrite is called
    repeatedly mid-build to tick off steps, NOT to re-plan) — is neutral and never flips the phase
    back. That keeps planning concentrated at the front of each turn, sparse by construction.
  - Harness-specific tool names live HERE, localized. The router (resolve) stays pure Phase->model
    and the gateway proxy stays harness-agnostic (DESIGN.md leak rule).
  - Bias to PLAN (the stronger model) when no write has happened yet — a safe default.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .models import Phase

# The only phase-changing signal: tools that mutate source files -> the agent is implementing.
# OpenCode's names plus common aliases from other OpenAI-tool-calling harnesses, so the signal
# survives a driver swap. Reads, search, shell, and todo tools are all neutral.
WRITE_TOOLS = frozenset(
    {"edit", "write", "patch", "multiedit", "multi_edit", "str_replace", "str_replace_editor",
     "create", "create_file", "apply_patch"}
)
# Shell tools are deliberately NOT in WRITE_TOOLS: that set also drives classify() below, where a
# tool call means "the agent started implementing". A read-only turn legitimately shells out to look
# around (grep/ls), so folding these in would misclassify exploration as implementation and reroute
# the model. They belong only to the read-only guarantee.
SHELL_TOOLS = frozenset({"bash", "shell", "sh", "run", "run_command", "execute", "exec", "terminal"})
# What a read-only turn (Ask mode, or a gated plan turn) must never be offered. Stripping these from
# the request is the ONLY thing that actually enforces read-only: OpenCode's per-agent
# `permission: {edit: deny, bash: deny}` is inert on the headless server path — its config loads and
# the agent resolves, but only `"ask"` diverts a tool to the approval handler, so `"deny"` is
# preapproved and runs. Verified 2026-07-29: sage-ask wrote a file via bash.
READ_ONLY_DENIED = WRITE_TOOLS | SHELL_TOOLS
# Tools that reach the public internet. Default-denied on every turn and stripped from the request
# unless the CURRENT prompt asked for the web (see _wants_web / web_allowed) — the agent otherwise
# wanders off to fetch Storybook/CDN URLs mid-plan and burns turns. Names cover OpenCode plus common
# aliases from other harnesses, same as the sets above, so the guard survives a driver swap.
WEB_TOOLS = frozenset({"webfetch", "web_fetch", "fetch", "websearch", "web_search"})
# The task-list tool, stripped on a turn that ANSWERS (Ask mode, or a question in Auto) — not on a
# gated plan turn, where tracking steps is the job. An answering turn returns without building, so a
# task list on it is a build the user is left waiting for: asked "what tech stack will be used", the
# agent opened a two-step list and said "Next I'm replacing the starter screen…", then stopped. The
# turn preamble already tells it not to; this is what makes that hold, on the same reasoning as
# READ_ONLY_DENIED — the agent's own prompt is guidance, a stripped tool is a guarantee.
#
# WRITE side only. Verified 2026-07-31 against the pinned 1.18.4 linux-x64 binary: "todowrite" is a
# tool-name literal, and there is no read counterpart — todos persist in the session db (todo_pk,
# todo_session_idx) and come back as context, not via a tool call. Reading an earlier build's list is
# also something an answering turn SHOULD be able to do: "what's left to do?" is a fair question, and
# the problem was only ever the agent opening a list of its own while claiming to build. `todo_write`
# is a defensive alias for a driver swap, same as WEB_TOOLS; no read name is listed, so a future
# driver that has one won't be blocked from answering with it.
TODO_TOOLS = frozenset({"todowrite", "todo_write"})
_TURN_BOUNDARY_ROLES = frozenset({"user", "human"})

# --- Rescue signals (see assess) ------------------------------------------------------------
# An unambiguous abort: one of these alone escalates, no corroboration needed. Modelled on
# Switchyard's stage_router, where a critical-error severity is a hard override that bypasses the
# score. Kept deliberately short — every entry here is a phrase that has no benign reading in the
# output of a shell or an edit.
#
# UNVERIFIED. Nothing in this repo has ever captured what OpenCode writes into a failed tool
# result, so these are informed guesses. That is exactly why assess() is wired observe-only first:
# the shim echoes the text of anything matched, so the next live failure replaces the guess.
CRITICAL_MARKERS = frozenset(
    {"traceback (most recent call last)", "modulenotfounderror", "cannot find module",
     "failed to compile", "segmentation fault", "syntaxerror:",
     # Live 2026-08-13: vite/rollup's phrasing when an import can't be resolved, which is the
     # single most common way an agent-written React file fails to build.
     "failed to resolve import", "rollup failed to resolve"}
)
# Phrases that usually mean failure but also show up in healthy output (a build log line, a lint
# summary, a test name). One is noise; two in the same window is a signal. This is the corroboration
# rule — a single one of these must never reroute a turn.
SOFT_ERROR_MARKERS = frozenset(
    {"error:", "err!", "error ts", "exception", "failed:", "exit code 1",
     # Live 2026-08-13, from a real failing build on the Sage image. npm on this image prints
     # lowercase `npm error` / `npm warn` — the `npm ERR!` of older npm never appears, so `err!`
     # alone matched nothing. Kept anyway for older npm; `npm error` is what actually fires.
     "npm error",
     # Same run: `ls: cannot access 'node_modules/.bin/': No such file or directory`, which the
     # agent hit repeatedly while flailing. Deliberately NOT matching the bare phrase "not found":
     # that same run also logged the benign `npm warn exec The following package was not found and
     # will be installed: tsc@2.0.4`, which must not count.
     "no such file or directory", "cannot access", "error during build"}
)
# Two errors since the last write, or one critical, escalates. Not ported from Switchyard's 0.5
# confidence bar — that number is calibrated against their unpublished per-signal weights on
# SWE-Bench Pro Python-75, so it means nothing here. What ports is the shape: one signal is not
# enough, a critical one is.
ERROR_CORROBORATION = 2
# Escalating twice in one turn means the cheap model is not converging. Stop flip-flopping and stay
# on the strong one for the rest of the turn — Switchyard's escalation router latches for the rest
# of the session; a turn is the right scope here, because a fresh turn already starts in PLAN.
RESCUE_LATCH = 2
# What the shim may echo, for as long as this runs observe-only: the first line of EVERY shell/write
# result the scorer looked at, tagged with whether it matched. Unmatched ones are the point — with
# matches alone, "the build was clean" and "the markers missed the failure" produce identical
# silence, which is exactly the ambiguity the first live run hit. First line only, hard-capped, so a
# log line can never carry a file's worth of a user's source.
#
# The LAST few results, not the first. Live 2026-08-13: a 10-result turn kept the first 6, so every
# sample was setup noise ("Wrote file successfully", "> react-vite@0.0.0 build") and the failures at
# the end of the turn — the entire reason for looking — were never echoed. First AND last line of
# each, because npm and vite both print a benign banner first and the actual error last.
_SAMPLE_CHARS = 200
_MAX_SAMPLES = 6


def _text(content: Any) -> str:
    """A tool result's text, whether it arrived as a string or as OpenAI content parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p["text"] if isinstance(p, dict) and isinstance(p.get("text"), str)
                 else p if isinstance(p, str) else "" for p in content]
        return "\n".join(p for p in parts if p)
    return ""


def _sample(text: str) -> str:
    """First and last non-empty line, capped. What the shim is allowed to echo of a result."""
    lines = [ln.strip()[:_SAMPLE_CHARS] for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    return lines[0] if len(lines) == 1 else f"{lines[0]} ... {lines[-1]}"


def _current_turn(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The messages since the last user prompt — the same window classify() has always used."""
    start = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") in _TURN_BOUNDARY_ROLES:
            start = i + 1
            break
    return list(messages[start:])


@dataclass(frozen=True)
class StepSignals:
    """What this step's tool-result history says. Pure data, like ModelDecision."""

    phase: Phase              # what routing SHOULD be, rescue included
    base_phase: Phase         # what the write-flip rule alone says — what actually routes today
    reason: str               # no-write | write-flip | rescue-errors | rescue-critical | rescue-latched
    errors_since_write: int
    rescues: int              # rescue episodes so far this turn
    examined: int = 0         # shell/write results the scorer read this turn (matched or not)
    samples: tuple[str, ...] = ()   # capped "<critical|soft|none> <first line>" echo, for tuning


def assess(messages: Sequence[dict[str, Any]] | None) -> StepSignals:
    """Score the current turn: the write-flip rule, plus a rescue back to PLAN when it goes wrong.

    `base_phase` is the long-standing rule verbatim. `phase` adds Switchyard stage_router's missing
    half — the signals that push *toward* the capable model — because today a single write pins the
    rest of the turn to the cheap coder even while it thrashes on the same error.

    A write and an error are opposing signals, so a write clears the error window. That yields
    de-escalation for free: the strong model lands a fix, the counter resets, and the next step
    goes back to the cheap model. Without that the rescue would be a one-way trip to the expensive
    tier and this would be a cost regression rather than a save.

    Only shell and write results are read for failure. A grep hit or a source file containing the
    word "error" is not a failure, and mapping tool_call_id back to the call that produced it is
    what keeps those out — cheaper and far more precise than tuning the marker strings.
    """
    turn = _current_turn(messages or [])
    origin: dict[str, str] = {}     # tool_call_id -> the tool that produced it
    has_write = False
    errors = 0
    rescues = 0
    examined = 0
    rescue_kind = ""                # non-empty while inside a rescue episode; cleared by a write
    latched = False
    samples: list[str] = []

    for message in turn:
        role = message.get("role")

        if role == "assistant":
            wrote = False
            for call in message.get("tool_calls") or []:
                name = ((call.get("function") or {}).get("name") or call.get("name") or "").lower()
                if not name:
                    continue
                if call.get("id"):
                    origin[call["id"]] = name
                wrote = wrote or name in WRITE_TOOLS
            if wrote:
                # Progress. Opposes the error window and ends the current rescue episode; the latch
                # (two failed episodes) deliberately survives it.
                has_write, errors, rescue_kind = True, 0, ""
            continue

        if role != "tool":
            continue
        if origin.get(message.get("tool_call_id") or "", "") not in (WRITE_TOOLS | SHELL_TOOLS):
            continue  # unknown or read-only origin: never counted, so a stray word can't escalate

        raw = _text(message.get("content"))
        lowered = raw.lower()
        critical = any(m in lowered for m in CRITICAL_MARKERS)
        soft = any(m in lowered for m in SOFT_ERROR_MARKERS)
        examined += 1
        samples.append(f"{'critical' if critical else 'soft' if soft else 'none'} {_sample(raw)}")
        if not critical and not soft:
            continue

        errors += 1
        if critical or errors >= ERROR_CORROBORATION:
            if not rescue_kind:                       # entering a new episode, not deepening one
                rescues += 1
                latched = latched or rescues >= RESCUE_LATCH
            rescue_kind = "rescue-critical" if critical else "rescue-errors"

    base_phase = Phase.IMPLEMENT if has_write else Phase.PLAN
    if latched:
        phase, reason = Phase.PLAN, "rescue-latched"
    elif rescue_kind:
        phase, reason = Phase.PLAN, rescue_kind
    else:
        phase, reason = base_phase, ("write-flip" if has_write else "no-write")
    return StepSignals(phase=phase, base_phase=base_phase, reason=reason,
                       errors_since_write=errors, rescues=rescues, examined=examined,
                       samples=tuple(samples[-_MAX_SAMPLES:]))


def classify(messages: Sequence[dict[str, Any]] | None) -> Phase:
    """Phase for the next inference, within the current turn.

    IMPLEMENT if the agent has already written a file since the last user message; otherwise PLAN.
    Scanning stops at the turn boundary so prior turns' writes don't leak forward.
    """
    return assess(messages).base_phase
