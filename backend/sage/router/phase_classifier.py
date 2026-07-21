"""PhaseClassifier — per-step plan-vs-implement classification (DESIGN.md Seam 1).

A "step" is one model inference: one `/v1/chat/completions` call. The shim classifies each
step at the moment it routes it, so Auto mode picks the strong (plan) model while the agent is
reasoning/reading and the cheaper (implement) model while it is writing code — and flips *back*
to plan when the agent returns to exploring. That bidirectional behaviour is what makes
interleaved turns (plan → edit → re-plan → edit) route correctly, per step.

Design notes:
  - Pure function of the OpenAI-compatible `messages`. No I/O, no shared mutable state, so
    concurrent requests never race over a phase flag.
  - Sticky, not per-action-flip. Reads, searches, shell, and pure reasoning are *neutral* — they
    hold whatever phase the last STRONG signal set. Only a file write (-> implement) or an explicit
    plan/todo tool or the start of the task (-> plan) changes the phase. That keeps planning
    concentrated: PLAN through the whole initial explore-and-think pass until the first write, then
    IMPLEMENT through the build (reads between edits don't bounce it) until the agent re-plans.
  - Harness-specific tool names live HERE, localized. The router (resolve) stays pure Phase->model
    and the gateway proxy stays harness-agnostic (DESIGN.md leak rule).
  - Bias to PLAN (the stronger model) when no strong signal is present — a safe default.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .models import Phase

# Strong IMPLEMENT signal: tools that mutate source files. OpenCode's names plus common aliases
# from other OpenAI-tool-calling harnesses, so the signal survives a driver swap.
WRITE_TOOLS = frozenset(
    {"edit", "write", "patch", "multiedit", "multi_edit", "str_replace", "str_replace_editor",
     "create", "create_file", "apply_patch"}
)
# Strong PLAN signal: explicit (re)planning tools. Everything else (read, grep, glob, list, bash,
# webfetch, reasoning-only turns) is NEUTRAL and does not move the phase.
PLAN_TOOLS = frozenset({"todowrite", "todoread", "plan"})


def _tool_names(message: dict[str, Any]) -> list[str]:
    """Tool-call names on an assistant message, lowercased. Empty when it made none."""
    names: list[str] = []
    for call in message.get("tool_calls") or []:
        name = (call.get("function") or {}).get("name") or call.get("name") or ""
        if name:
            names.append(name.lower())
    return names


def classify(messages: Sequence[dict[str, Any]] | None) -> Phase:
    """Phase for the next inference: scan back to the most recent STRONG signal.

    IMPLEMENT once the agent has started writing files (held through intervening reads/reasoning);
    PLAN before the first write or after an explicit plan/todo tool. Neutral actions are skipped.
    Defaults to PLAN.
    """
    for message in reversed(messages or []):
        if message.get("role") != "assistant":
            continue
        names = _tool_names(message)
        if any(n in WRITE_TOOLS for n in names):
            return Phase.IMPLEMENT
        if any(n in PLAN_TOOLS for n in names):
            return Phase.PLAN
        # neutral (read/search/bash/reasoning): keep scanning for the last strong signal
    return Phase.PLAN
