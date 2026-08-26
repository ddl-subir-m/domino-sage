"""Chat → Build detect-once classifier (docs/workbench/handoff.md).

After a sage-chat turn, decide whether this Thread has started asking for a lasting app.
One bounded gateway call, no tools. Biased to CHAT (no suggestion). Fail open on timeout
or error; fail safe (suggest) on an unreadable answer; breaker after three garbage replies.

Explicit "build me a dashboard" / "open this in the builder" skips the model and counts as a hit.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re
from pathlib import Path

from ..gateway.client import CostLabels, GatewayClient
from ..resources.bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, KIND_MODEL_API, Binding
from ..router.models import ModelCatalog
from .scope import _extract, _model_for

log = logging.getLogger(__name__)

TIMEOUT_S = 8.0
MAX_UNREADABLE = 3

# Only the last user + last assistant + title. Truncate rather than refuse.
_TITLE_CHARS = 200
_TURN_CHARS = 1500

_SYSTEM = """\
You decide whether a Chat Thread has started asking for a lasting UI that other people would open, \
rather than a one-off answer about data.

Answer with exactly one word, nothing else:

APP - the user now wants a lasting app: more than one view, something that should keep working \
tomorrow, a dashboard or tool colleagues would open.
CHAT - everything else: a question, a chart, a table, exploration, a follow-up about the same numbers.

Default to CHAT. Suggesting an app every few messages is worse than missing a real app request. \
Answer APP only if you would be uncomfortable treating this as a one-off analysis."""

# Explicit confirm-intent. Keep this tight: "dashboard" in an analysis question is the classifier's
# job, not this regex. The spec's examples are "build me a dashboard" and "open this in the builder".
_EXPLICIT_BUILD = re.compile(
    r"(?:"
    r"\bbuild me (?:an? )?(?:app|dashboard|ui|page)\b"
    r"|\bopen (?:this|it) in (?:the )?(?:builder|build)\b"
    r"|\bturn (?:this|it) into an app\b"
    r"|\bmake (?:this|it)(?: into)? an app\b"
    r")",
    re.IGNORECASE,
)


class _Health:
    """Consecutive unreadable answers, and the breaker they trip. Process-wide: the thing being
    tracked is the gateway route, not a Thread."""

    def __init__(self) -> None:
        self.unreadable = 0
        self.broken = False

    def reset(self) -> None:
        self.unreadable = 0
        self.broken = False

    def answered(self) -> None:
        self.unreadable = 0

    def unreadable_answer(self, answer: str) -> bool:
        """Record an answer in neither vocabulary; return what wants_an_app should return.

        True (suggest) until the breaker trips, then False (stay in Chat) forever after."""
        self.unreadable += 1
        if self.unreadable < MAX_UNREADABLE:
            log.warning("handoff: unrecognised verdict %r (%d in a row) — suggesting",
                        answer[:60], self.unreadable)
            return True
        if not self.broken:
            self.broken = True
            log.error("handoff: classifier BROKEN — %d unreadable verdicts in a row (last %r). "
                      "Not calling it again; Chat will not suggest Open in Build from the classifier. "
                      "Check the gateway route for the ask model.",
                      self.unreadable, answer[:60])
        return False


_health = _Health()


def looks_like_build_request(prompt: str) -> bool:
    """True when the user already asked to build — skip the classifier, go to the sheet."""
    return bool(_EXPLICIT_BUILD.search(prompt or ""))


def should_classify(handoff: dict | None) -> bool:
    """False once this Thread has been suggested or suppressed. Classifier runs at most once."""
    if not handoff:
        return True
    if handoff.get("suggestedAt") or handoff.get("suppressed"):
        return False
    return handoff.get("status") not in ("suggested", "suppressed", "planned", "bound")


def _payload(title: str, user: str, assistant: str) -> str:
    parts = [f"Thread title: {(title or '').strip()[:_TITLE_CHARS]}",
             f"User: {(user or '').strip()[:_TURN_CHARS]}"]
    text = (assistant or "").strip()
    if text:
        parts.append(f"Assistant: {text[:_TURN_CHARS]}")
    return "\n\n".join(parts)


def last_assistant_text(history: list[dict]) -> str:
    texts = [e.get("text") or "" for e in history
             if e.get("type") == "agent" and e.get("kind") == "text" and e.get("text")]
    return texts[-1] if texts else ""


def wants_an_app(
    *,
    title: str,
    user: str,
    assistant: str,
    gateway: GatewayClient,
    catalog: ModelCatalog,
    session: str | None = None,
    version: str | None = None,
    timeout_s: float = TIMEOUT_S,
) -> bool:
    """True when this Thread should be offered Open in Build.

    False on timeout or error (fail open). True on an unreadable answer until the breaker trips."""
    if _health.broken:
        return False
    text = _payload(title, user, assistant).strip()
    if not text:
        return False

    request = {
        "model": _model_for(catalog),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": text},
        ],
        "max_tokens": 256,
        "temperature": 0,
        "stream": True,
    }
    labels = CostLabels(phase="ask", mode="auto", component="handoff",
                        session=session, version=version)

    def _call() -> str:
        return _extract(b"".join(gateway.route(request, labels)))

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sage-handoff")
    try:
        answer = pool.submit(_call).result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        log.warning("handoff: classify timed out after %.1fs — no suggestion", timeout_s)
        return False
    except Exception as e:
        log.warning("handoff: classify failed (%s: %s) — no suggestion", type(e).__name__, e)
        return False
    finally:
        pool.shutdown(wait=False)

    verdict = answer.strip().upper()
    if verdict.startswith("APP"):
        _health.answered()
        return True
    if verdict.startswith("CHAT"):
        _health.answered()
        return False
    return _health.unreadable_answer(answer)


_BINDABLE = {KIND_DATA_SOURCE, KIND_MODEL_API, KIND_LLM_ALIAS}


def draft_digest(*, title: str, asked: list[str], context: list[dict],
                 artifacts: list[dict]) -> str:
    """One-paragraph background for sage-plan. Not the transcript."""
    bits: list[str] = []
    if (title or "").strip():
        bits.append(f'Thread "{title.strip()}".')
    if asked:
        bits.append("Asked: " + "; ".join(a.strip() for a in asked if a.strip()) + ".")
    names = [str(i.get("name") or "").strip() for i in context]
    names = [n for n in names if n]
    if names:
        bits.append("In context: " + ", ".join(names) + ".")
    arts = []
    for a in artifacts:
        label = (a.get("title") or a.get("name") or "").strip()
        path = (a.get("path") or "").strip()
        if path and label and label not in path:
            arts.append(f"{label} at {path}")
        elif path or label:
            arts.append(path or label)
    if arts:
        bits.append("Artifacts: " + "; ".join(arts) + ".")
    return " ".join(bits) or "An empty Chat Thread."


def plan_prompt(thread_id: str, digest: str) -> str:
    return (
        f"A Chat Thread produced the files under examples/{thread_id}/ and the digest in "
        f".sage/handoff.md. The plan is what to build. The digest is background.\n\n"
        f"{digest}\n\n"
        "Write a concrete build plan for an app colleagues can open from this work."
    )


_HANDOFF_LINE = (
    "A Chat Thread produced the files under `examples/` and the digest in "
    "`.sage/handoff.md`. The plan is what to build. The digest is background."
)


def implement_note(workspace: Path) -> str:
    """Extra implement-turn context when Chat handed off. Empty if there is no digest."""
    digest_path = workspace / ".sage" / "handoff.md"
    if not digest_path.is_file():
        return ""
    digest = digest_path.read_text().strip()
    if not digest:
        return ""
    listing: list[str] = []
    examples = workspace / "examples"
    if examples.is_dir():
        for p in sorted(examples.rglob("*")):
            if p.is_file():
                listing.append(str(p.relative_to(workspace)))
    lines = [_HANDOFF_LINE, "", digest]
    if listing:
        lines += ["", "Example files:", *[f"- {x}" for x in listing]]
    return "\n".join(lines)


def plan_title(plan_md: str) -> str:
    for line in (plan_md or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:80]
    return "App"


def user_texts(history: list[dict]) -> list[str]:
    return [e.get("text") or "" for e in history if e.get("type") == "user" and e.get("text")]


def transcript_markdown(history: list[dict]) -> str:
    lines: list[str] = []
    for e in history or []:
        if e.get("type") == "user" and e.get("text"):
            lines.append(f"**User:** {e['text']}")
        elif e.get("type") == "agent" and e.get("kind") == "text" and e.get("text"):
            lines.append(f"**Sage:** {e['text']}")
    return ("\n\n".join(lines) + "\n") if lines else ""


def binding_from_context(item: dict) -> Binding | None:
    """A Binding for a Session context row that names a Resource, or None.

    A table chip is still a Data Source Binding: the id is the source, and Scope rides
    on the Binding. Leaf ids (`table:…`, `dsfile:…`) are not Resource ids.
    """
    kind = item.get("kind")
    if kind == "datasource":
        kind = KIND_DATA_SOURCE
    if kind not in _BINDABLE:
        return None
    key = item.get("bindingKey")
    rid = ""
    if isinstance(key, (list, tuple)) and len(key) >= 2:
        rid = str(key[1] or "")
    if not rid:
        rid = str(item.get("parentId") or item.get("resourceId") or "")
    for prefix in ("data_source:", "datasource:", "llm_alias:", "model_api:"):
        if rid.startswith(prefix):
            rid = rid[len(prefix):]
            break
    if not rid or rid.startswith(("ctx_", "table:", "dsfile:", "file:")):
        return None
    name = str(item.get("name") or rid)
    scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
    database = schema = table = None
    if kind == KIND_DATA_SOURCE:
        database = scope.get("database") or None
        schema = scope.get("schema") or None
        table = scope.get("table") or None
    return Binding(kind, rid, name, name, database, schema, table)


def confirm_digest(draft: str, *, artifacts: list[dict], context: list[dict],
                   include_artifacts: bool, include_resources: bool) -> str:
    parts = [draft.strip(), ""]
    if include_artifacts:
        parts.append("Artifacts to treat as examples:")
        if artifacts:
            parts.extend(f"- {a.get('path')}" for a in artifacts if a.get("path"))
        else:
            parts.append("- none")
        parts.append("")
    if include_resources:
        parts.append("What the app needs:")
        names = [i.get("name") for i in context if i.get("name")]
        if names:
            parts.extend(f"- {n}" for n in names)
        else:
            parts.append("- none")
        parts.append("")
    parts.append(
        "A Chat Thread produced the files under examples/ and this digest. "
        "The plan is what to build. The digest is background."
    )
    return "\n".join(parts).strip() + "\n"
