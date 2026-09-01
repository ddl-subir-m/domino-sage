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
from ..workspace.threads import handoff_unresolved
from .scope import _extract, _model_for

log = logging.getLogger(__name__)

TIMEOUT_S = 8.0
MAX_UNREADABLE = 3

# Only the last user + last assistant + title. Truncate rather than refuse.
_TITLE_CHARS = 200
_TURN_CHARS = 1500
# How much of the judged prompt the verdict line carries. Enough to recognise the sentence in a log
# without copying the turn into it.
_LOG_CHARS = 120

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
    # "convert" belongs with "turn": both name the same move, and only one of them was here. Live,
    # "lets convert this into an app i can share" fell through to the classifier — which cannot run
    # before a turn — so it spent the full turn timeout AND a model call to classify, then answered
    # "Building an app is Build's job". That answer was available in the first millisecond.
    # "build" joined them for the third time the same sentence arrived: "lets build this into an
    # app that i can share". It reads as intent to every human, and it matched nothing — the branch
    # below cannot take it, because "into" eats the one adjective slot and leaves the article "an"
    # where the noun has to be.
    #
    # The noun set is the generic branch's, not "app" alone. "build this into a dashboard" is the
    # same sentence with the same intent, and it missed for no reason a person could see. This
    # frame can hold them safely where the generic branch could not: "into a" pins the noun to the
    # end of the phrase, so the analysis questions that "dashboard" and "tool" appear in — "which
    # tool do they use", "a dashboard of app downloads" — never reach it.
    r"|\b(?:turn|convert|build) (?:this|it|that) into (?:an? )?(?:app|dashboard|ui|tool)\b"
    r"|\bmake (?:this|it|that)(?: into)? (?:an? )?(?:app|dashboard|ui|tool)\b"
    # A verb of intent anywhere ahead of "web app" / "website". Unlike "dashboard", nobody asks an
    # analysis question about a webapp, so the noun alone carries the intent and the gap can be
    # loose — "lets build the webapp" and "build this chart ... as a webapp" both landed on the
    # classifier before, which meant a timed-out turn detected nothing at all.
    r"|\b(?:build|make|turn|ship|publish|deploy)\b[^.?!]{0,60}?\bweb\s?(?:app|site)\b"
    # The plainest phrasing there is, and the one that fell through: "lets build an app from a
    # sample of 100 rows" needs neither "me" nor "web", so it missed every branch above and paid
    # the whole tool-quiet window before the classifier said what the verb had already said.
    #
    # Ordered and tight — verb, article, at most one adjective, then the noun — because the noun
    # alone cannot carry the intent the way "webapp" can. "app" and "dashboard" are ordinary words
    # in clickstream data, and the gap is what would swallow them: "make a chart of app downloads"
    # gets no further than "of", and in "which app category converts best?" the noun comes first.
    # "page" stays out for the same reason, since "build a page view report" would read as intent.
    r"|\b(?:build|make|create)\s+(?:me\s+)?(?:an?|the|this)\s+(?:\w+\s+)?(?:app|dashboard|ui|tool)\b"
    # Naming Build as the DESTINATION, which every branch above missed because none of them names
    # it. "ok lets move this over to build" is the plainest way there is to ask for the handoff and
    # it matched nothing — so it ran as a chat turn, and the turn answered that the plan was already
    # waiting in Build. There was no plan; a Conversation cannot write one from here.
    #
    # The whole risk is the infinitive. "move this to build a chart" is the same nine leading
    # characters and the opposite request, so Build has to be where the clause STOPS — end of the
    # message or a mark. That one lookahead is what separates them, and it also disposes of the
    # noun: "how long did the build take" and "send it to the build team" both continue past it.
    r"|\b(?:move|take|send|bring|continue|carry|do)\s+(?:this|it|that|these)\b"
    r"[^.?!]{0,20}?\b(?:in|into|to)\s+(?:the\s+)?build(?:er)?\b\s*(?=[.!?,\n]|$)"
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


def should_classify(entries: list[dict] | None) -> bool:
    """False while this Thread's newest handoff is unresolved, and forever once one was declined.

    Not once per Thread: a Thread that already produced a Built App may produce another (ADR-0008),
    so `bound` opens it to a fresh suggestion about a different app. `Not now` is the one permanent
    answer — the person saying stop, rather than a step finishing (handoff spec §8, criterion 10).
    """
    rows = [r for r in (entries or []) if isinstance(r, dict)]
    if any(r.get("suppressed") or r.get("status") == "suppressed" for r in rows):
        return False
    return not handoff_unresolved(rows[-1] if rows else None)


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
    thread: str | None = None,
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

    def _record(hit: bool) -> bool:
        """Say what was judged and what came back — the only trace a clean verdict leaves (#131).

        Both verdicts used to return in silence, which made an under-firing classifier invisible
        by construction: a CHAT answer and a call that never happened look identical from outside,
        and the gateway's own logs are not reachable from the app. `session` and `version` are the
        identifiers this call is already cost-tagged with, so a gateway-side view, if one ever
        arrives, keys on the same row this line names.
        """
        log.info("handoff: verdict=%s thread=%s session=%s version=%s prompt=%r",
                 "APP" if hit else "CHAT", thread or "-", session or "-", version or "-",
                 (user or "").strip()[:_LOG_CHARS])
        _health.answered()
        return hit

    verdict = answer.strip().upper()
    # An empty body is a route that said nothing, not a model that answered badly. It belongs with
    # the timeout and the exception above, which both return False, and not with the garbage the
    # breaker below counts. Live, a Builder whose Environment was rebuilt without GATEWAY_BASE_URL
    # ran in `fake` mode, where every call returns `{"model": ..., "phase": ...}` — no `choices`, so
    # `_extract` yields "". The turn itself answered nothing for the same reason, and on top of that
    # blank answer the classifier offered to build an app from "what info is there in <file>.json".
    # Nothing about that question was judged; there was no verdict to judge it with.
    if not verdict:
        log.warning("handoff: classifier returned an empty body — no suggestion")
        return False
    if verdict.startswith("APP"):
        return _record(True)
    if verdict.startswith("CHAT"):
        return _record(False)
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


def plan_prompt(thread_id: str, digest: str, *, voice: str, shape: str) -> str:
    """The prompt sage-plan writes the first plan from (docs/workbench/handoff.md §5).

    Deliberately NOT the implement line below. That line says "the plan is what to build", which
    is true for the turn that reads an approved plan and false for this one — there is no plan yet,
    this turn is writing it. Live, a planner given it produced a plan for a page ABOUT the work
    ("a shareable planning page with a build brief"), and the build that followed put a "Next build"
    roadmap card inside the app instead of building the app. So this turn is told the opposite: the
    Thread is background, the app does not exist, write the plan for the app.

    `voice` and `shape` are the gated build turn's own (service._PLAN_VOICE, service._PLAN_SHAPE),
    restated here because this turn writes a plan document too and the document is parsed out of
    those headings. Asking only for "a concrete build plan" left the shape to the agent prompt
    alone, and it did not hold: live, sage-plan answered the digest in narration ("I'm turning that
    background work into a concrete app brief…"). Prose has no headings, so parse_sections found no
    sections, and the plan page showed a title over eight empty ones while the same text sat whole
    in the transcript. The spec has always asked for the sections (docs/workbench/handoff.md §5);
    now the prompt does.
    """
    return (
        f"A Chat Thread in this project asked the questions below and produced the files under "
        f"examples/{thread_id}/. That work is background — no app has been built yet, and this "
        f"turn is where the plan for one gets written.\n\n"
        f"{digest}\n\n"
        "Write a concrete build plan for an app colleagues can open from this work. "
        + voice + "\n\n" + shape
    )


_HANDOFF_LINE = (
    "A Chat Thread produced the files under `examples/` and the digest in "
    "`.sage/handoff.md`. The plan is what to build. The digest is background."
)


def implement_note(workspace: Path) -> str:
    """Extra implement-turn context when Chat handed off. Empty if there is no digest.

    One line and the digest, which is what the spec asks for and what the digest is already shaped
    to sit under. It used to also walk `examples/` and append a second file listing. That repeated
    the digest's own "Artifacts to treat as examples" list back to the model — and worse, it walked
    the directory rather than reading the digest, so unchecking Artifacts on the handoff sheet
    listed the files anyway. The checkbox is supposed to omit them (docs/workbench/handoff.md §4);
    the digest already honours it, so reading the digest honours it too.
    """
    digest_path = workspace / ".sage" / "handoff.md"
    if not digest_path.is_file():
        return ""
    digest = digest_path.read_text().strip()
    if not digest:
        return ""
    return "\n".join([_HANDOFF_LINE, "", digest])


# A leading list marker, which `lstrip("#")` never removed and which the plan shape invites: the
# shape is itself a bulleted instruction, so a planner writing the opening line to it writes a bullet.
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_TITLE_MAX = 80


def plan_title(plan_md: str) -> str:
    """The name this plan gives the plan card, the plan document and the Built App.

    A `# ` heading is the answer whenever there is one, and the plan shape now asks for it. It has
    to be asked for, because a name is written, not derived: nothing here can turn "This app will be
    an AI consumption dashboard for exploring daily usage, spend, and model activity across teams
    and users" into "AI Consumption Dashboard", and the only writer in the loop is the planner.

    Everything else is a plan drafted before the shape asked, and then its first line is the best
    answer available. That line is cleaned rather than taken raw, which it used to be: live, an app
    was called `- This app will be an AI consumption dashboard for exploring daily usage, spend,` in
    the rail — the bullet marker kept because only `#` was stripped, the trailing comma because the
    cut was at 80 characters and landed mid-clause, and the whole thing shouted in the panel header
    that uppercases what it is given. Cleaning does not make it a name. It stops it being that.
    """
    for line in (plan_md or "").splitlines():
        text = _BULLET.sub("", line.strip()).strip()
        if not text:
            continue
        if text.startswith("#"):
            # A heading is already a name. Any depth, because a plan that opened straight into a
            # section has always been read this way and a plan is not refused for its shape.
            return text.lstrip("#").strip()[:_TITLE_MAX] or "App"
        return _clip(text)
    return "App"


def _clip(text: str) -> str:
    """A long sentence made presentable: whole words, and no punctuation left hanging off the end.

    Not made into a name, and no attempt at it. Cutting at the first comma would have given the live
    sentence a better ending than cutting at a word does — "...for exploring daily usage" rather than
    "...for exploring daily usage, spend" — and would be wrong the moment a plan opens on a subclause.
    Guessing where a sentence's meaning stops is the writer's job, the writer is asked for a name
    above, and this is only what is left for the plans drafted before it was.

    A full stop is NOT stripped. It only survives when the whole sentence fitted, which means the
    writer put it there rather than the cut leaving it — and a plan that was already named after its
    closing full stop keeps that name, instead of every such app being quietly renamed by this fix.
    """
    if len(text) > _TITLE_MAX:
        cut = text[:_TITLE_MAX]
        space = cut.rfind(" ")
        text = cut[:space] if space > 0 else cut
    return text.rstrip(" ,;:—-") or "App"


# The two states that mean a plan document exists for this Conversation. `suggested` is an offer
# nobody has answered and `suppressed` is one that was declined — neither wrote anything.
_PLANNED = frozenset({"planned", "bound"})


def has_plan(entries: list[dict] | None) -> bool:
    """Whether this Conversation has ever had a plan written from it.

    Any entry, not the newest: a Conversation may hand off more than once (ADR-0008), and a fresh
    offer sitting unanswered on top of a plan that was built does not un-write the plan.
    """
    return any(isinstance(r, dict) and r.get("status") in _PLANNED for r in (entries or []))


def unanswered_ask(history: list[dict]) -> str:
    """The question Chat offered Build instead of answering, or "" when there is no such question.

    The explicit-build regex short-circuits BEFORE the turn (`service.chat_stream`): it records the
    question, offers Build and returns without running anything. That is the right trade while the
    offer stands — sage-chat writes an Artifact, never an app, so running the turn first spends
    ninety seconds to arrive where the offer already is. It is the wrong one the moment the offer is
    declined, and declining used to do nothing but switch the offer off. Live, that left the question
    sitting there answered by nothing, and the person retyped it by hand — which worked, because the
    suppression the decline had just written stopped the regex from firing a second time.

    "Never answered" is: walking back from the end, the offer comes before the question and nothing
    that answers comes between them. An offer the CLASSIFIER raised cannot match, because it is
    written after a turn that did answer and that turn's text is in the way — declining one of those
    has nothing to run, which is the truth about it.

    `done` is skipped rather than treated as an answer: the short-circuit writes one itself, and a
    turn ending is not a turn saying anything.
    """
    offered = False
    for event in reversed(history or []):
        kind = event.get("type")
        if kind == "handoff-suggest":
            offered = True
        elif kind == "user":
            return str(event.get("text") or "") if offered else ""
        elif kind in ("agent", "artifacts", "stopped", "error"):
            return ""
    return ""


def user_texts(history: list[dict]) -> list[str]:
    return [e.get("text") or "" for e in history if e.get("type") == "user" and e.get("text")]


def transcript_markdown(history: list[dict]) -> str:
    """The Chat transcript written to `.sage/handoff-transcript.md` in the Built App's repo.

    `Agent`, not the pack's assistantName: a transcript is a record, and ADR-0014:108 says a pack
    change cannot re-brand a conversation that already happened — so the label Sage wrapped it in is
    de-branded ONCE, the way `sage: ` became `build: `. `User` is a role, and what either of them
    said is reproduced verbatim.
    """
    lines: list[str] = []
    for e in history or []:
        if e.get("type") == "user" and e.get("text"):
            lines.append(f"**User:** {e['text']}")
        elif e.get("type") == "agent" and e.get("kind") == "text" and e.get("text"):
            lines.append(f"**Agent:** {e['text']}")
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
    # No closing "the plan is what to build" line. `implement_note` puts that sentence in front of
    # this file, which is the one place the spec asks for it; having it here too meant the implement
    # turn read the same instruction twice, once at each end of the same block.
    return "\n".join(parts).strip() + "\n"
