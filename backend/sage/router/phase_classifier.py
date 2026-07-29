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
_TURN_BOUNDARY_ROLES = frozenset({"user", "human"})


def _tool_names(message: dict[str, Any]) -> list[str]:
    """Tool-call names on an assistant message, lowercased. Empty when it made none."""
    names: list[str] = []
    for call in message.get("tool_calls") or []:
        name = (call.get("function") or {}).get("name") or call.get("name") or ""
        if name:
            names.append(name.lower())
    return names


def classify(messages: Sequence[dict[str, Any]] | None) -> Phase:
    """Phase for the next inference, within the current turn.

    IMPLEMENT if the agent has already written a file since the last user message; otherwise PLAN.
    Scanning stops at the turn boundary so prior turns' writes don't leak forward.
    """
    for message in reversed(messages or []):
        role = message.get("role")
        if role in _TURN_BOUNDARY_ROLES:
            break  # reached the start of this turn with no write -> still planning
        if role == "assistant" and any(n in WRITE_TOOLS for n in _tool_names(message)):
            return Phase.IMPLEMENT
    return Phase.PLAN
