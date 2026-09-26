"""Keep one question through source/table clarification; never derive it from model prose."""
from __future__ import annotations

import re

from ..workspace.threads import ThreadStore, new_id

_SOURCE_KINDS = {"data_source", "datasource", "table"}
_SOURCE_REPLY_WORDS = {
    "the", "a", "our", "my", "data", "warehouse", "source", "table", "is", "are", "here",
    "attached", "added", "this", "that", "it", "use", "please", "now", "have", "i", "provided", "in",
}


def _sources(ctx: dict) -> list[dict]:
    return [i for i in ctx.get("items", []) if i.get("kind") in _SOURCE_KINDS and i.get("id")]


def _source_reply(prompt: str, sources: list[dict]) -> bool:
    text = prompt
    for item in sources:
        for name in (item.get("name"), item.get("sourceName")):
            if name:
                text = re.sub(r"@?" + re.escape(str(name)) + r"(?!\w)", " ", text,
                              flags=re.IGNORECASE)
    # An unknown mention is not attachment evidence and must not be stripped.
    words = re.findall(r"[\w@-]+", text.lower())
    return set(words) <= _SOURCE_REPLY_WORDS


def _new(question: str, awaiting: str, ctx: dict) -> dict:
    return {"id": new_id("task"), "question": question, "awaiting": awaiting,
            "sourceIds": [i["id"] for i in _sources(ctx)], "offerDecision": ""}


def require(ctx: dict, task_id: str) -> dict:
    task = ctx.get("pendingTask") or {}
    if task_id and task.get("id") != task_id:
        raise ValueError("This card belongs to an earlier question. Send the current question again.")
    return task


def resolve(store: ThreadStore, thread_id: str, prompt: str, *, task_id: str = "",
            asking: bool, needs_source: bool) -> str:
    effective = prompt

    def apply(ctx):
        nonlocal effective
        task = require(ctx, task_id)
        if task_id:
            if not task.get("awaiting"):
                raise ValueError("This question has already started. Wait for its answer.")
            effective = task["question"]
        elif asking:
            sources = _sources(ctx)
            added = any(i["id"] not in task.get("sourceIds", []) for i in sources)
            if (task.get("awaiting") == "source" and added
                    and _source_reply(prompt, sources)):
                effective = task["question"]
            else:
                ctx.pop("pendingTask", None)
                if needs_source:
                    ctx["pendingTask"] = _new(prompt, "source", ctx)
        elif task and task.get("question") != prompt:
            # Older clients may lack taskId; they still cannot replay a different pending card.
            raise ValueError("This card belongs to an earlier question. Send the current question again.")
        return ctx

    store.update_context(thread_id, apply)
    return effective


def await_input(store: ThreadStore, thread_id: str, question: str, awaiting: str) -> dict:
    def apply(ctx):
        task = ctx.get("pendingTask") or {}
        if task.get("question") != question:
            task = _new(question, awaiting, ctx)
        ctx["pendingTask"] = {**task, "awaiting": awaiting,
                              "offerDecision": "offered" if awaiting == "investigation"
                              else task.get("offerDecision", "")}
        return ctx

    return store.update_context(thread_id, apply)["pendingTask"]


def awaiting_source(store: ThreadStore, thread_id: str, question: str) -> bool:
    """True when `question` is the pending task and it still waits for a source (#566).

    Read off the Thread's own record, which `resolve` wrote at the top of this turn, and never off
    model prose. The third condition is the one `started` applies: a source that arrived while the
    turn was setting up ends the wait, so a turn that reads True here is one that has nothing to
    query and knows it.
    """
    ctx = store.read_context(thread_id)
    task = ctx.get("pendingTask") or {}
    return (task.get("question") == question and task.get("awaiting") == "source"
            and not _sources(ctx))


def started(store: ThreadStore, thread_id: str, question: str) -> None:
    def apply(ctx):
        task = ctx.get("pendingTask") or {}
        if task.get("question") == question and (
                task.get("awaiting") != "source" or _sources(ctx)):
            ctx["pendingTask"] = {**task, "awaiting": ""}
        return ctx

    store.update_context(thread_id, apply)
