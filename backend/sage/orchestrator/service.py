"""Orchestrator service — assembles a project's parts into one lifecycle (SPEC C1).

A Project bundles: workspace (from the warm template), a Vite supervisor (live preview), a
ModelControl (per-project switching state), and an EnforcementShim (routes model calls through
the gateway with the sovereign override). Per D9 a container hosts exactly one project, bound to
the Domino project's mounted volume and attached lazily on first use.

Deep module, narrow interface: project / build / build_stream / shutdown.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from pathlib import PurePosixPath as PurePosix
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ..provision.domino import ControlPlane

from ..assets.provider import DEFAULT_SENSITIVITY_TAG, Asset, AssetProvider, FakeAssetProvider, is_sensitive
from ..driver.opencode import OpenCodeClient, run_feedback_loop
from ..driver.server import OpenCodeServer
from ..feedback.circuit_breaker import CircuitBreaker
from ..feedback.runner import FeedbackRunner
from ..gateway.client import GatewayClient
from ..preview.prefix import domino_base_prefix
from ..preview.supervisor import ViteSupervisor
from ..resources.bindings import (
    KIND_DATA_SOURCE,
    KIND_LLM_ALIAS,
    KIND_MODEL_API,
    Binding,
    parse_bindings,
)
from ..resources.model_api_credentials import (
    Credential,
    CredentialRequired,
    CredentialStore,
    verify_credential,
)
from ..resources.model_api_snippet import parse_snippet
from ..resources.pinned_model import CONFIG_PATH, agents_block, pinned_alias, render_config
from ..resources.pinned_model_api import CONFIG_PATH as MODEL_API_CONFIG_PATH
from ..resources.pinned_model_api import agents_block as model_api_agents_block
from ..resources.pinned_model_api import pinned_model_api
from ..resources.pinned_model_api import render_config as render_model_api_config
from ..resources.preflight import stale_bindings, stale_message, unresolved_slots
from ..resources.provider import (
    DataSource,
    FakeResourceProvider,
    ResourceProvider,
    ResourceUnavailable,
    cascade_levels,
    safe_identifier,
)
from ..router.model_control import ModelControl
from ..router.models import Mode, ModelCatalog, Phase
from ..shim.enforcement import EnforcementShim
from ..workspace.manager import Workspace, WorkspaceManager
from ..workspace.snapshot import TurnSnapshot
from . import scope
from .describe import describe, fit_image
from .plan_steps import MIN_STEPS, PlanStep, is_phasable, parse_steps, step_index

log = logging.getLogger("sage.orchestrator")

# Consecutive OpenCode poll (is_running/messages) failures tolerated before halting a build. Each poll
# can block up to its httpx timeout, so this is ~a minute of sustained unresponsiveness, not a blip.
_MAX_POLL_FAILURES = 4

# Largest image inlined into a prompt as a data: URI. Base64 inflates by ~4/3, and the result rides
# in the request body through OpenCode -> shim -> gateway -> provider; anything larger degrades to
# its descriptor instead. Provider limits sit around 5 MB per image, so this stays well under.
_MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024

# Each explicit mode routes to a named opencode.json agent. Ask/Plan are read-only — their
# `permission` block is enforced natively by OpenCode (edit/bash denied), not just hidden from the
# model's tool list. Implement carries a strong system prompt that forces the model to actually
# edit files rather than reply with a plan (the gpt-5.4 "described it but wrote no code" stall).
# Auto stays on OpenCode's default agent: a single Auto turn may be a question, a plan, or an edit,
# so it can't be pinned to any one persona.
_MODE_AGENT = {Mode.ASK: "sage-ask", Mode.PLAN: "sage-plan", Mode.IMPLEMENT: "sage-implement"}

# Event types written to .sage/history.jsonl, i.e. what a page reload replays. The phased trio is
# here so a reload redraws the step checklist — a build that shows six phases live and nothing after
# F5 reads as lost work.
_PERSISTED_EVENTS = frozenset({
    "agent", "typecheck", "done", "saved", "data-leak", "plan-proposed",
    "build-plan", "step-start", "step-done",
})

# The entry script Domino runs to serve a published app (repo root). The builder has the working
# tree, so publish pre-checks it exists locally before deploying (a missing one fails opaquely).
_ENTRY_POINT = "app.sh"
# The Python server that entry script execs to serve the build (ADR-0002). Pre-checked too, but only
# when this app's app.sh actually calls it — an app still serving with Node doesn't need it.
_SERVER_SCRIPT = "serve.py"
# Published-app deploy status -> terminal phase (mirrors HubService.publish_status). Matched
# case-insensitively; anything else means the deploy is still in progress.
_RUNNING_STATES = frozenset({"running"})
_FAILED_STATES = frozenset({"failed", "error"})


class AttachTooLarge(Exception):
    """Attaching a file would push the total attached size over the configured cap."""

    def __init__(self, cap: int, current: int, incoming: int) -> None:
        self.cap, self.current, self.incoming = cap, current, incoming
        super().__init__(f"attach would exceed cap: {current + incoming} > {cap} bytes")


class UploadUnavailable(Exception):
    """No writable dataset is mounted to receive an upload. For a sensitive upload it means the
    per-project sensitive dataset isn't mounted (provisioned at project creation — rebuild the
    workspace); otherwise the project has no writable default dataset mount."""

    def __init__(self, sensitive: bool) -> None:
        self.sensitive = sensitive
        super().__init__("sensitive" if sensitive else "default")


class DataReferenced(Exception):
    """The app's source still uses an attached file — either fetches it (`refs`) or has copied its
    bytes into `src/` (`copies`, the git-leaking pattern we forbid). Deleting the data would leave
    that code dangling, so delete is blocked; the user edits the app to stop using it, or Detaches."""

    def __init__(self, path: str, refs: list[str], copies: list[str]) -> None:
        self.path, self.refs, self.copies = path, refs, copies
        super().__init__(f"{path} is used by the app")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _level(name: str) -> str:
    """One level of a Data Source cascade path, as it arrives from the panel (#11).

    Blank stays blank, because a store with no database level passes "" at that level legitimately
    and it is not a name that failed to be one. Anything non-blank is charset-checked here, at the
    edge, so a name Sage would refuse to send is a 400 the panel can read rather than a 502 blaming
    the store for a request it never saw.
    """
    name = (name or "").strip()
    return safe_identifier(name) if name else ""


def _slug(name: str) -> str:
    """Collapse a dataset name into a safe single path segment for public/data/<slug>/."""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name).strip("_") or "dataset"


def _attach_dest(dataset_name: str, file_path: str) -> str:
    """Workspace-relative POSIX path a dataset file is symlinked to."""
    rel = PurePosix(file_path.replace("\\", "/"))
    parts = [p for p in rel.parts if p not in ("", ".", "..")]
    return PurePosix("public/data", _slug(dataset_name), *parts).as_posix()


# Subfolders Sage writes uploaded bytes into: `uploads/` (non-sensitive) and `sensitive/` (a
# sensitive upload into the shared default dataset, which we deliberately don't tag). Both are
# Sage-created, so both are safe to delete; a genuine pre-existing dataset file is neither.
_SAGE_UPLOAD_PREFIXES = ("uploads/", "sensitive/")


def _is_sage_upload(entry: dict) -> bool:
    """A Sage-managed upload: bytes Sage wrote under a dataset's `uploads/` or `sensitive/` folder.
    True for `source=='upload'` and for such a file later re-attached from the dataset browser
    (source becomes 'dataset' but its dataset_rel_path still lives under one of those folders).
    These are safe to delete; a genuine pre-existing dataset file is not."""
    rel = str(entry.get("dataset_rel_path") or "")
    return entry.get("source") == "upload" or rel.startswith(_SAGE_UPLOAD_PREFIXES)


def _safe_join(root: Path, rel: str) -> Path:
    """Join rel under root, rejecting anything that escapes it (.., absolute). Resolves the escape
    check LEXICALLY (os.path.normpath) so it doesn't follow the attached symlink at the leaf —
    which points at the dataset mount OUTSIDE the workspace and would false-positive on detach."""
    base = root.resolve()
    target = Path(os.path.normpath(base / rel))
    if target != base and base not in target.parents:
        raise ValueError(f"path escapes {base}: {rel}")
    return target


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    """Remove now-empty dirs from `start` up to (not including) `stop`. Best-effort."""
    stop = stop.resolve()
    cur = start.resolve()
    while cur != stop and stop in cur.parents:
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


# Shell operators that end an install command's argument list. Split on these FIRST: without it,
# `npm install foo | tail -20` reads `tail` as a second package, since it's a valid package name.
_SHELL_SEGMENT = re.compile(r"&&|\|\||;|\||\n")
# `pkg`, `@scope/pkg`, `pkg@^1.2.3`. Excludes redirections like `2>&1` — the `>` can't match.
_PACKAGE_NAME = re.compile(r"^@?[a-z0-9][\w.-]*(/[\w.-]+)?(@[\w.^~*-]+)?$", re.IGNORECASE)
_INSTALL_VERBS = frozenset({("npm", "install"), ("npm", "i"), ("npm", "add"), ("yarn", "add"),
                            ("pnpm", "add"), ("pnpm", "install"), ("bun", "add")})


def _install_attempt(command: str) -> list[str]:
    """Packages a shell command is trying to add. Empty when it isn't adding any.

    Two jobs. It's the evidence behind "curated stack, revisit after real usage" — a running list of
    what agents reach for and don't find baked in. And it flags a turn that is about to lose its
    node_modules: npm won't install into a symlinked one, so it deletes the link during reify before
    it knows the install resolves (see WorkspaceManager.link_warm_deps).

    A bare `npm install` returns nothing — it's a reinstall, not a request for something new.
    """
    for segment in _SHELL_SEGMENT.split(command):
        tokens = segment.split()
        for i in range(len(tokens) - 1):
            if (tokens[i], tokens[i + 1]) not in _INSTALL_VERBS:
                continue
            packages = []
            for arg in tokens[i + 2:]:
                if arg.startswith("-"):
                    continue                       # a flag, not a package
                if not _PACKAGE_NAME.match(arg):
                    break                          # a redirect or trailing shell noise
                packages.append(arg)
            return packages
    return []


def _agent_for_mode(mode: Mode) -> str | None:
    return _MODE_AGENT.get(mode)


# Interrogative leads and build verbs for _looks_like_question. Kept tight on purpose: ambiguous
# leads ("give", "show", "tell", "list", "get") stay OUT so they fall through to gating.
_QUESTION_LEAD = frozenset({
    "what", "whats", "why", "how", "when", "where", "who", "whom", "whose", "which",
    "is", "are", "was", "were", "am", "do", "does", "did", "can", "could", "will",
    "would", "should", "explain", "describe",
})
_BUILD_VERB = frozenset({
    "build", "make", "create", "add", "implement", "generate", "scaffold", "design",
    "develop", "write", "code", "refactor", "fix", "change", "update", "modify",
    "rename", "remove", "delete", "replace", "wire", "setup",
})


# Leads that ask for information ABOUT a change rather than for the change ("how would you add a
# queue?"). These, and only these, survive the build-verb veto below, because after one of them the
# build verb sits in a subordinate clause describing the hypothetical work — "an architecture to ADD
# a queue" is a request for the architecture, not for the queue.
#
# A strict subset of _QUESTION_LEAD, and the omissions are the point: "can", "could", "would", "is",
# "do" stay out, so "can you remove the dataset?" is still a change request. A modal asking whether
# we'll do something is a polite imperative; "how"/"what"/"why" name the information wanted.
_INFO_LEAD = frozenset({
    "how", "what", "whats", "why", "when", "where", "which", "explain", "describe", "compare",
})
# Adjectives that may sit between the article and the artifact noun in _INFO_ASK — "give me a
# step-by-step plan", "show me a high-level architecture", "draft a rough technical design".
#
# Curated rather than a generic `[a-z]+` wildcard, in the style of _QUESTION_LEAD and _BUILD_VERB. A
# wildcard would let an arbitrary noun phrase reach the artifact nouns ("show me the dataset upload
# design"), and every consumer of _asks_about_a_change reads a match as "this prompt wants words, not
# work" — so an over-wide match here quietly stops Ask mode refusing real change requests. Two is the
# cap because "a rough high-level plan" is the realistic ceiling for stacked modifiers.
_ARTIFACT_MODIFIER = (
    r"(?:(?:high|low)[\s-]level|step[\s-]by[\s-]step|end[\s-]to[\s-]end|"
    r"detailed|rough|quick|brief|short|simple|basic|initial|draft|"
    r"technical|implementation|overall|general|concise|full|complete)"
)

# The other informational opening: a deliverable made of words. "give"/"show"/"tell" are too
# ambiguous alone to be question leads ("show me a dashboard" is a build) — it's the noun that
# settles it, so this pattern requires one.
#
# The indirect object is optional: "give me a plan" and "draft a plan" are the same request, and the
# verbs that read most clearly as informational are exactly the ones that never take it — nobody
# writes "propose me an approach" or "sketch me the architecture". Requiring "me"/"us" meant `draft`,
# `sketch`, `propose`, `suggest` and `recommend` sat in the verb list unreachable in natural phrasing.
# Dropping it is safe because the artifact noun is still mandatory, and that noun is what separates
# "show me a plan" from "show me a dashboard".
_INFO_ASK = re.compile(
    r"^(?:(?:please|can|could|would|will)\s+(?:you\s+)?)*"
    r"(?:give|show|tell|walk|talk|draft|sketch|outline|propose|suggest|recommend)\s+"
    r"(?:(?:me|us)\s+)?(?:through\s+)?(?:an?|the|some|your)?\s*"
    rf"(?:{_ARTIFACT_MODIFIER}\s+){{0,2}}"
    r"(?:architecture|design|plan|approach|strategy|outline|spec(?:ification)?|proposal|"
    r"option|options|recommendation|recommendations|idea|ideas|overview|breakdown|"
    r"tradeoffs?|trade[\s-]offs?)\b",
)


def _asks_about_a_change(prompt: str) -> bool:
    """True when a prompt asks us to DESCRIBE work rather than do it — "give me an architecture to add
    a real time queue", "how would you fix the race?". Consulted by both classifiers below, ahead of
    their build-verb scan.

    Without this, a build verb anywhere in the sentence wins, so Ask mode refused the single most
    natural thing to type into it: a design question that happens to name the change it's about."""
    text = prompt.strip().lower()
    words = re.findall(r"[a-z']+", text)
    # Apostrophes stripped so "what's the best way to add caching" leads with "whats", like "what".
    return bool(words) and (words[0].replace("'", "") in _INFO_LEAD
                            or _INFO_ASK.match(text) is not None)


# The design artifact a prompt can ask for by name. Paired with _asks_about_a_change below, this is
# what separates "give me an architecture to add a queue" (wants a document) from "how would you add
# a queue" (wants an answer) — both are questions, but only the first names a deliverable.
_ARCH_NOUN = re.compile(
    r"\b(?:architecture|architectural|design\s+doc(?:ument)?|diagram|data[\s-]?flow|"
    r"system\s+design|component\s+(?:map|diagram|breakdown)|blueprint|schematic)\b",
    re.IGNORECASE,
)


def _wants_architecture(prompt: str) -> bool:
    """True when the prompt asks for an ARCHITECTURE — a description of components, data flow and
    boundaries — rather than for the work or for a build plan. Routes the turn to the architecture
    deliverable in every mode (see the `arch` branch in _build_stream).

    Sage's only deliverable used to be app source code, so this request had nowhere to land: Plan mode
    turned "give me an architecture to add a real time queue" into a ten-step build plan, Implement
    built the feature, and the model — having no artifact channel of its own — offered to add an
    architecture *screen* to the user's app.

    Three things must hold: the prompt asks about a change, names the artifact, AND names work not yet
    done (a build verb — "an architecture to ADD a queue"). Narrow on purpose, because this is the
    HEAVY deliverable: a card, a persisted file, and a Build button. An explanation of code that
    already exists earns none of those — offering to build the answer to "how does the upload flow
    work" is nonsense — and any answer-only turn can already draw a diagram when one helps, so a
    question that misses here still gets a picture, just not a document.

    The build verb is a proxy for "doesn't exist yet" and an imperfect one: "what's the architecture
    for a live upload queue" names no verb and is answered with a diagram instead of a card. That
    errs toward the lighter deliverable, which is the right way to be wrong. Deliberately not chased
    any further — every cheap proxy for "does this exist yet" fails worse than this one."""
    words = re.findall(r"[a-z']+", (prompt or "").lower())
    return (_asks_about_a_change(prompt)
            and _ARCH_NOUN.search(prompt or "") is not None
            and any(w in _BUILD_VERB for w in words))


# The plan artifact a prompt can ask for by name. Tighter than _ARCH_NOUN's list on purpose: the
# nouns _INFO_ASK also accepts ("approach", "strategy", "outline", "proposal") stay OUT, because a
# request for an approach is answered well in prose, while a plan has a card and an Approve button
# waiting for it. Only the words that name THAT artifact earn it.
_PLAN_NOUN = re.compile(r"\b(?:plan|roadmap|step[\s-]by[\s-]step)\b", re.IGNORECASE)

# The imperative form: "plan this first", "just plan it out", "plan the auth flow". Anchored at the
# start, because a leading "plan" IS the request — mid-sentence it is usually a noun about something
# else ("the plan we discussed"). `\bplan\b` keeps "planning to use postgres" out: no word boundary
# after "plan" in "planning".
_PLAN_FIRST = re.compile(
    r"^(?:(?:please|can|could|would|will)\s+(?:you\s+)?)*(?:just\s+|first\s+)*\bplan\b",
    re.IGNORECASE,
)


def _wants_plan(prompt: str) -> bool:
    """True when the prompt explicitly asks for a PLAN before any building — "plan this first", "show
    me a plan to add auth". Forces the gate in every mode (see the `wants_plan` branch in _should_gate).

    Same bug as _wants_architecture, one artifact over: the plan card and its Approve button already
    exist, and a request for them had no way to reach them. "Show me a plan to add auth" matches
    _INFO_ASK, so it was classed a question and answered in prose — the user asked for the thing the
    gate produces and got an essay. "Plan this first" was worse: "plan" is neither a build verb nor a
    question lead, so on a built project it fell through to _should_gate's `has_built` check and
    silently built the feature it was asked to plan.

    Two accepted shapes. The imperative (_PLAN_FIRST) stands alone — asking for a plan IS the request,
    so it needs no other signal. The informational ask ("show me a plan to add auth") mirrors
    _wants_architecture exactly: asks about a change, names the artifact, and names work not yet done.

    That build-verb proxy carries the same imperfection it does there — "what's the plan for the upload
    flow" names no verb and is answered as prose rather than re-planning a feature that already exists.
    Erring toward the lighter deliverable is the right way to be wrong, and it is what stops a question
    about existing code from turning into a plan card nobody asked for."""
    if _PLAN_FIRST.match((prompt or "").strip()):
        return True
    words = re.findall(r"[a-z']+", (prompt or "").lower())
    return (_asks_about_a_change(prompt)
            and _PLAN_NOUN.search(prompt or "") is not None
            and any(w in _BUILD_VERB for w in words))


def _looks_like_question(prompt: str) -> bool:
    """True when a prompt asks for information ("what colour is this?") rather than asking us to build
    something ("build a file upload UI"). Used to tell a first-turn question from a build request so
    we answer it directly instead of proposing a build plan.

    Deliberately conservative — only a CLEAR question counts; anything ambiguous returns False and
    falls through to the plan gate, because a wrongly-skipped gate silently builds without approval
    (the worse failure). Pure and deterministic, no model call — matches the phase classifier's style.
    An explicit build verb anywhere wins, so "can you build me a dashboard?" is a build, not a question
    — unless the prompt opened by asking about the change (see _asks_about_a_change)."""
    text = prompt.strip().lower()
    words = re.findall(r"[a-z']+", text)
    if not words:
        return False
    if _asks_about_a_change(prompt):
        return True
    if any(w in _BUILD_VERB for w in words):
        return False
    return words[0] in _QUESTION_LEAD or text.endswith("?")


def _looks_like_change_request(prompt: str) -> bool:
    """True when a prompt asks for the app to CHANGE ("remove the dataset from the UI") rather than
    for information. Used only in Ask mode, to refuse the turn before it runs (see _ask_mode_refusal).

    An explicit build verb anywhere wins, exactly as it does in _looks_like_question, and both defer
    to _asks_about_a_change first — so the two agree on every prompt and one is never both."""
    if _asks_about_a_change(prompt):
        return False
    words = re.findall(r"[a-z']+", prompt.lower())
    return any(w in _BUILD_VERB for w in words)


# A whole prompt that says nothing but "yes, go" — used only while a proposed plan is waiting for
# approval (see _looks_like_approval). Anchored end to end on purpose: "ok build" approves the
# pending plan, "ok build me a dashboard" is a new request and must not.
_APPROVAL_ONLY = re.compile(
    r"^(?:ok(?:ay)?|yes|yep|yeah|sure|great|perfect|cool|sounds good|looks good|lgtm)?[\s,.!]*"
    r"(?:(?:please|now|then|lets|let's)\s+)*"
    r"(?:approve[d]?|proceed|continue|go(?:\s+ahead)?|ship it|do it|make it|"
    r"build(?:\s+(?:it|this|that|the plan))?|"
    r"(?:go\s+)?build(?:\s+(?:it|this|that))?)?[\s,.!]*$",
    re.IGNORECASE,
)


def _looks_like_approval(prompt: str) -> bool:
    """True when the whole prompt is the user saying "yes, build the plan you just showed me".

    Only consulted while a plan is actually pending. Typing approval in the composer is the same
    intent as clicking Approve, so it must run the approved plan — otherwise the gate fires again and
    the user gets a second plan for a request that was already planned. Conservative by construction:
    anything carrying content beyond the approval itself fails the anchored match and builds
    normally."""
    text = prompt.strip()
    if not text or len(text) > 40:
        return False
    return bool(_APPROVAL_ONLY.match(text)) and bool(re.search(r"[a-z]", text, re.IGNORECASE))


# Phrases that signal the user wants Sage to reach the internet this turn, in three parts: an
# explicit URL, a standalone verb that only ever means "hit the web", or a fetch-ish verb sitting
# before a web noun in the same sentence. Deliberately generous on vocabulary — the default is deny,
# but a false positive here is cheap (the webfetch tool is merely PRESENT; the agent still only calls
# it when the task needs it), so we favour catching real phrasings over minimising the word list.
_WEB_INTENT = re.compile(
    r"https?://"                                          # any explicit URL — the unambiguous case
    # Standalone: these words almost never mean anything but "reach the internet".
    r"|\b(?:web\s?fetch|web\s?search|scrape|crawl|google|duck\s?duck\s?go|stack\s?overflow|"
    r"curl|wget)\b"
    # A fetch-ish verb somewhere before a web noun in the same sentence (no . ? ! between them).
    r"|\b(?:fetch|download|look\s?up|lookup|search|browse|check|read|get|pull|grab|find|visit|"
    r"open|consult|reference|retrieve)\b[^.?!]*"
    r"\b(?:online|web|internet|url|link|site|website|page|docs?|documentation|changelog|repo|"
    r"repository|github|gitlab|readme|wiki|blog|article|release\s?notes|api\s?reference|api\s?docs|"
    r"npm|pypi|cdn|registry)\b",
    re.IGNORECASE,
)


def _wants_web(prompt: str) -> bool:
    """True when the current prompt asks Sage to reach the public internet (a URL, or an intent verb
    like fetch/look up/search paired with a web noun like online/docs/site). Default-deny: anything
    ambiguous returns False, so the shim keeps web tools stripped. Pure and deterministic, like the
    phase classifier and _looks_like_question — no model call."""
    return bool(_WEB_INTENT.search(prompt or ""))


def _should_gate(*, mode: Mode, has_built: bool, skip_planning: bool, is_question: bool = False,
                 wants_plan: bool = False) -> bool:
    """Plan gate (SPEC P6): run the read-only planner and stop for the user to approve before any
    code is written. Fires in Plan mode, when the prompt asks for a plan outright, or automatically on
    the first BUILD of a project that hasn't been built yet — unless the project opted out. Never
    gates Ask (read-only Q&A) otherwise.

    Keyed on has_built, not "first turn": a question asked before the first build (answered read-only,
    see answer_only in build_stream) must not consume the gate — the first real build request still
    gates. A *question* is not a build to be planned, so it skips the gate. Plan mode always gates:
    it's an explicit ask to plan. Once built, iteration turns don't gate."""
    # Asking for a plan in words is the same instruction as picking Plan mode, so it outranks
    # everything below — including Ask and skip_planning, exactly as an explicit Plan selection does.
    # Ask is safe to override for the same reason an architecture request is: the gated turn is
    # read-only, so nothing about Ask's contract changes, and _wants_plan needs work that doesn't
    # exist yet, which keeps "what's the plan for the upload flow" out.
    if wants_plan:
        return True
    if mode is Mode.ASK:
        return False
    # Plan mode is an explicit ask to plan — it always gates, even with skip_planning set. That flag
    # opts out of the *automatic* first-build gate (below); it must not override an explicit Plan
    # selection, or Plan mode would neither plan (no gate) nor build (its agent is read-only) and dead-end.
    if mode is Mode.PLAN:
        return True
    if skip_planning:
        return False
    return not has_built and not is_question


def _scope_gate_applies(*, mode: Mode, has_built: bool, gate: bool, answer_only: bool,
                        skip_planning: bool) -> bool:
    """Whether to spend a model call asking scope.wants_a_plan about this turn.

    Every deterministic signal gets to decide first and for free — this only runs when none of them
    did. The conditions are all "the classifier could change the outcome":

      * `gate` already True — the turn plans regardless, so there is nothing to ask.
      * `answer_only` — the turn answers and stops. It covers approvals and questions too, both of
        which are already excluded from gating upstream.
      * not `has_built` — the first-build gate has this turn; the hole opens only after it.
      * `skip_planning` — the project opted out of the automatic gate, and this IS the automatic
        gate, one turn later. Honouring the flag here is the same promise.
      * Auto only. Plan gates every turn already; Implement is the user saying "just build it", and
        Ask never builds. Auto is the mode that carries no explicit instruction, which is the whole
        reason it needs one inferred."""
    return (mode is Mode.AUTO and has_built and not gate and not answer_only and not skip_planning)


def _failure_gate_applies(*, mode: Mode, is_approval: bool, is_question: bool, skip_planning: bool,
                          prev_turn_failed: bool) -> bool:
    """Widen the plan gate for the turn that follows a FAILED turn. Today a failure changes nothing
    about how the next turn is dispatched, so the user's next message goes straight into another blind
    build — the retry loop. Right after something broke is the one moment a plan card is worth the
    interruption, because the thing most likely to be wrong is the approach, not the typing.

    Auto only, mirroring _scope_gate_applies/_should_gate: an explicit Plan/Implement/Ask pick is the
    user telling us how this turn should run and is never second-guessed. Implement in particular is
    where someone retries a failure deliberately, and gating it would fight them.

    Never gates an approval or a question. An approval is the user saying "build the plan we already
    agreed" — re-proposing a plan there is the loop this feature exists to break. A question is
    answered read-only and isn't a build at all. Both still CONSUME the signal (see the caller), so a
    failure can't strand anyone behind a permanent gate.

    Honours skip_planning for the same reason the first-build gate does: it's an explicit "don't stop
    to plan for me", and a project that opted out of automatic gating shouldn't get one back through
    the side door. Fails open everywhere else — prev_turn_failed is False whenever the state is
    missing or unreadable, which is exactly today's behaviour."""
    if not prev_turn_failed or mode is not Mode.AUTO:
        return False
    if is_approval or is_question:
        return False
    return not skip_planning


def _part_key(m: dict, i: int, part: dict) -> tuple[str, object]:
    """Identity of one assistant message part, for the emit-once `seen` set.

    Prefer the part's own id: the position of a part is NOT stable across polls. Parts stream in, and
    a part that is pending on one poll can be absent, merged, or reordered on the next, which shifts
    every later part's index — the same text then arrives under a fresh key and is emitted a second
    time. Falls back to the index when a part carries no id, which is the old behaviour and no worse
    than it was."""
    return (m["id"], part.get("id") or i)


def _read_only_reason(*, mode: Mode, answer_only: bool, gate: bool, arch: bool = False) -> str:
    """Why the shim is withholding edit tools this turn — "" when it isn't. Reported in the turn
    summary so a turn that wrote nothing can say which rule stopped it instead of blaming OpenCode for
    dropping edits it was never offered (the shim strips them from the request; see enforcement.handle).

    Ask is read-only by *mode*, with nothing armed, so this can't be read off the read-only token: that
    was the case that made an Ask-mode build look like a mysterious failure. Ask is checked first
    because an Ask turn is also answer-only, and the mode is the more useful thing to tell the user."""
    # Architecture first, ahead of even Ask: on that turn the artifact is what the user is waiting for
    # and the more useful thing to name, and an architecture request now gates in Ask mode too.
    if arch:
        return "architecture"
    if mode is Mode.ASK:
        return "ask"
    if answer_only:
        return "question"
    return "plan" if gate else ""


def _is_answer_only(*, mode: Mode, is_question: bool, is_approval: bool, arch: bool = False,
                    wants_plan: bool = False) -> bool:
    """A turn that answers read-only instead of building — no plan card, no edits, no implement-nudge.
    Ask mode is always read-only Q&A; a question in Auto or Implement is answered rather than built
    (whether or not the app is built — a question about a built app should be answered, not turned
    into edits). An approval is the user asking to build, never an answer.

    Implement is included because its agent is told "a turn in which you touched no files is a failed
    turn": asked a question there, it either built something nobody asked for or answered in prose and
    got reported as `Wrote nothing`. The mode says how to do work, not that every prompt is work.

    Plan still falls through — a build request there is what the gate is for. An architecture request
    produces its own artifact and is never answer-only, which keeps this mutually exclusive with the
    gate (a question is never gated). A request for a plan is excluded for the same reason and matters
    more: "show me a plan to add auth" reads as a question to _looks_like_question, and answering it in
    prose is precisely the failure — the plan card it asked for is what the gate already produces."""
    if is_approval or arch or wants_plan:
        return False
    return mode is Mode.ASK or (mode in (Mode.AUTO, Mode.IMPLEMENT) and is_question)


# A step that opens "I will …" — right after the list marker, or after the label's em dash. Weak
# planners latch onto one opener and repeat it for every step, and seven identical openers is the
# thing that makes a plan card read as filler. The opener carries no information the label and
# sentence don't already carry, so dropping it loses nothing.
_I_WILL_OPENER = re.compile(
    r"(?P<prefix>(?:^[ \t]*(?:[-*]|\d+[.)])[ \t]+(?:\*\*[^*\n]+\*\*[ \t]*[—:-][ \t]*)?)|(?:—[ \t]+))"
    r"(?:I|We)(?:[ \t]+(?:will|shall|am going to|are going to)|['’]ll)[ \t]+"
    r"(?P<verb>[a-z])",
    re.MULTILINE,
)


def _drop_i_will_openers(plan_md: str) -> str:
    """Rewrite 'I will define the schema' into 'Define the schema' at the start of plan steps."""
    return _I_WILL_OPENER.sub(lambda m: m["prefix"] + m["verb"].upper(), plan_md)


_OPEN_Q_HEADING = re.compile(r"^#{1,6}[ \t]*open questions\b[ \t]*:?[ \t]*$", re.IGNORECASE)


def _drop_empty_questions(plan_md: str) -> str:
    """Remove an "## Open questions" section whose only content is "None".

    The planner is told to write the section, so a plan with nothing to ask still ends in a heading
    followed by "None — ready to build.". That heading is scaffolding: it shows the user a slot that
    exists for the model's benefit, and reads as a prompt to answer questions that were never asked.
    A section with real questions is left alone."""
    lines = plan_md.splitlines()
    for i, line in enumerate(lines):
        if not _OPEN_Q_HEADING.match(line.strip()):
            continue
        end = i + 1
        while end < len(lines) and not lines[end].lstrip().startswith("#"):
            end += 1
        body = " ".join(lines[i + 1:end]).strip().strip("-*•_ ").strip()
        if re.match(r"none\b", body, re.IGNORECASE):
            return "\n".join(lines[:i] + lines[end:]).rstrip()
    return plan_md


def _tidy_plan(plan_md: str) -> str:
    """Drop verbatim repeated blocks and repeated "I will" step openers before a plan is shown.

    Planners — weak sovereign models especially — sometimes restate a whole paragraph word for word,
    which reads in the plan card as though Sage said the same thing twice. Only long blocks (>=120
    chars) are deduped, so short repeats that are legitimately identical (a bullet, "None — ready to
    build.") survive. Step openers are then de-padded by _drop_i_will_openers, and an empty
    "Open questions" section by _drop_empty_questions. Everything else, including order, is left
    exactly as written."""
    out: list[str] = []
    seen: set[str] = set()
    for block in re.split(r"\n\s*\n", plan_md.strip()):
        key = " ".join(block.split())
        if len(key) >= 120:
            if key in seen:
                continue
            seen.add(key)
        out.append(block.strip())
    return _drop_empty_questions(_drop_i_will_openers("\n\n".join(out)))


def _approve_prompt(plan_md: str, answers: str) -> str:
    """The Implement-turn prompt built from an approved plan (SPEC P6): the plan is fed in as
    context so the build turn constructs exactly what the user signed off on."""
    parts = ["The user approved this plan. Build the app it describes now — implement it, don't re-plan.",
             "", "## Approved plan", plan_md]
    if answers.strip():
        parts += ["", "## Answers to the open questions", answers.strip()]
    return "\n".join(parts)


def _phase_note(text: str, limit: int = 400) -> str:
    """One phase's closing summary, squeezed down to a handoff note for the phases after it."""
    return " ".join(str(text).split())[:limit]


def _phase_prompt(step: PlanStep, steps: list[PlanStep], answers: str,
                  notes: list[str] | None = None, retry_errors: str = "") -> str:
    """The prompt for ONE phase of a phased build, sent into a brand-new session.

    Deliberately NOT the whole plan: carrying it would re-pay the context a fresh session just
    bought us, which is the entire economics of building in phases. What goes in instead is the
    step's own brief plus a one-line-per-step index — about fifteen tokens a step, and the cheapest
    defence against a cold model reinventing something an earlier phase already built.
    """
    # Step 1 must NOT be told earlier work exists. It doesn't: the workspace is still the starter
    # template, so an agent sent looking for it finds a placeholder App.tsx and files later steps
    # haven't created yet, and burns the turn trying to reconcile that with its brief instead of
    # building. Observed live on 2026-08-06 ("App.tsx seems to be unreadable or may not exist in the
    # expected format... let me also look at the types file").
    prior = (
        "The workspace is the untouched starter template — nothing from this plan has been built yet, "
        "so treat the placeholder App.tsx as yours to replace."
        if step.n == 1 else
        "The earlier steps are already done and their code is in the workspace — read it if you need "
        "it, but do not redo it."
    )
    parts = [
        (f"You are executing step {step.n} of {len(steps)} of a plan the user already approved. "
         f"Do THIS step and nothing else. {prior} Later steps are someone else's job; "
         "do not start them."),
        "", "## The other steps, for context only", step_index(steps, step.n),
    ]
    if notes:
        # What the finished phases said they built. The filesystem already carries their code, but not
        # what's IN it — so without this a cold phase rediscovers the codebase by reading, which is
        # both the bootstrap tax and the amnesia risk. Observed live 2026-08-06: a drawer step read
        # types.ts, App.tsx and ReviewTable.tsx purely to learn what existed, and still got the props
        # wrong. These are the agents' own closing summaries, so they cost nothing extra to produce —
        # a few hundred tokens against the file reads they save.
        parts += ["", "## What earlier steps built, in their own words", *notes]
    parts += [
        "", "## Your step", step.raw,
        "",
        # Precedence, spelled out, because a plan can contradict itself and the agent then stops
        # rather than builds. Observed live 2026-08-06: a drawer step had ReviewTable.tsx under
        # "Don't touch" but needed a row-click handler in it, and the phase was spent deliberating
        # ("we're not supposed to modify existing components... let me think differently") before
        # shipping a drawer nothing could open. Also rescues plans written by an older Sage.
        ("Files is your allowlist: create or edit anything in it, including files an earlier step "
         "wrote — wiring your work into what already exists is part of your step, not a violation. "
         "Don't touch covers everything else, and if a file somehow appears in both, Files wins. "
         "Never abandon the step because a file looked off limits: make the edit, keep it as small "
         "as the wiring requires, and say what you touched in your summary."),
    ]
    if answers.strip():
        parts += ["", "## Answers to the open questions", answers.strip()]
    if retry_errors.strip():
        parts += ["", "## Your previous attempt at this step failed", retry_errors.strip()]
    parts += [
        "",
        # Every phase was shelling out to `npm run build` / `npx tsc --noEmit` to check itself, while
        # Sage typechecks after each phase anyway and feeds the errors back — so the project was
        # compiled twice per phase, the slower time on the model's clock. It can't know that unless
        # it's told. Ending your own summary here is what produces the handoff note above.
        ("Write the code now. Do not re-plan and do not describe what you would do. Don't run the "
         "build or the typechecker yourself — that happens automatically when your step ends, and "
         "any errors come back to you. Finish with one or two sentences naming what you built and "
         "what it exposes to the steps after you (component names, exported types, props)."),
    ]
    return "\n".join(parts)


def _opencode_base_port(opencode_cwd: Path) -> int | None:
    """The port from opencode.json's sage-gateway baseURL — the port OpenCode dials for every
    inference. Compared against SAGE_CONTROL_PORT (the port the shim's /v1 endpoint actually serves) to
    detect a wiring drift that silently routes inference around the shim. None if unreadable."""
    import json

    try:
        cfg = json.loads((opencode_cwd / "opencode.json").read_text())
        base = ((((cfg.get("provider") or {}).get("sage-gateway") or {}).get("options")) or {}).get("baseURL", "")
    except Exception:
        return None
    host_port = base.split("://", 1)[-1].split("/", 1)[0]  # e.g. "localhost:8080"
    port = host_port.rsplit(":", 1)[-1] if ":" in host_port else ""
    return int(port) if port.isdigit() else None


# Directories skipped when scanning the app's own source for data references/copies: dependencies,
# build output, git, sage metadata, and public/ (which holds the attached-data symlinks themselves).
_SCAN_SKIP_DIRS = frozenset({"node_modules", "dist", ".git", ".sage", "public"})
# Extensions whose text we read to look for a reference/inlined copy. Data files (.csv, …) aren't
# here — a copied data file is caught by its basename below, not by scanning its contents. AGENTS.md
# is excluded (Sage writes it and it lists every attachment) so it never reads as a real reference.
_SCAN_EXTS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".css", ".html",
                        ".vue", ".svelte"})
# Cap the bytes we compare for a verbatim copy — a source file that fully contains the data is the
# leak; larger attachments are still caught by the basename-copy check below without a big scan.
_COPY_SCAN_MAX = 512 * 1024
# A source file needn't contain the WHOLE file to be a leak: an agent that hardcodes the prompt
# preview (the first rows) into the app is inlining a partial copy — so the app renders a stale
# sample and never fetches the real file. Flag it when this many leading lines (header + rows) appear
# verbatim; requiring a multi-line contiguous run keeps false positives on ordinary code negligible.
_SAMPLE_MATCH_ROWS = 6
_SAMPLE_MATCH_MIN_BYTES = 64


def _is_inlined_copy(raw: bytes, text: str) -> bool:
    """True if `text` (a source file) inlines the attachment `raw` — the whole file, or just a
    leading sample of it (the agent hardcoding the prompt preview instead of fetching at runtime).

    The sample check requires the first `_SAMPLE_MATCH_ROWS` lines to appear verbatim as a contiguous
    block: a multi-line run makes an accidental match on ordinary code vanishingly unlikely.
    """
    body = raw.decode("utf-8", "ignore")
    if 64 <= len(raw) <= _COPY_SCAN_MAX and body in text:
        return True
    lines = body.splitlines()
    if len(lines) <= _SAMPLE_MATCH_ROWS:
        return False  # too few lines to tell a "sample" from the whole file (covered by the check above)
    sample = "\n".join(lines[:_SAMPLE_MATCH_ROWS]).strip()
    return len(sample) >= _SAMPLE_MATCH_MIN_BYTES and sample in text


def _tool_detail(tool: str, part: dict) -> str:
    """A short, human label for a tool call (the file it touched, the command it ran) so the UI
    can render dyad-style action cards instead of a bare tool name. Best-effort; '' when unknown."""
    inp = (part.get("state") or {}).get("input") or {}
    if tool in ("edit", "write", "read"):
        path = inp.get("path") or inp.get("filePath") or ""
        return path.split("/workspaces/", 1)[-1] if "/workspaces/" in path else path
    if tool == "bash":
        return (inp.get("command") or "").strip()
    if tool == "grep":
        return inp.get("pattern") or ""
    if tool == "todowrite":
        todos = inp.get("todos") or []
        n = len(todos)
        done = sum(1 for t in todos if (t or {}).get("status") == "completed")
        # First call = the plan ("7 steps"); later calls are progress updates ("3/7 done") so they
        # read as bookkeeping, not repeated planning.
        if done:
            return f"{done}/{n} done"
        return f"{n} step" + ("" if n == 1 else "s")
    return ""


@dataclass
class Project:
    id: str
    workspace: Workspace
    supervisor: ViteSupervisor
    control: ModelControl
    shim: EnforcementShim
    snapshot: TurnSnapshot
    session_id: str | None = None
    # The session a turn is CURRENTLY streaming into. Normally session_id, but a phased build runs
    # each phase in its own throwaway session, and two things must follow the live one rather than
    # the project's: Stop (interrupting the idle project session would leave the phase generating),
    # and the `sage-session` cost tag (tagging every phase with the project session collapses their
    # spend into one bucket — which is exactly the per-phase breakdown a phased build exists to be
    # judged on). None between turns.
    active_session_id: str | None = None
    # Attached dataset FILES: [{dataset_id, dataset, file, path, size}]. `path` is the
    # workspace-relative symlink under public/data/ (what OpenCode @mentions and the app fetches).
    attached: list[dict] = field(default_factory=list)
    # Set by the /v1/chat/completions handler when a model call the agent made this turn fails
    # upstream (bad model id, gateway auth, etc). build()/build_stream() check + clear this so a
    # failed turn is reported as an error instead of silently falling through to "typecheck clean"
    # on an unmodified workspace (the turn never touched any files).
    last_gateway_error: dict | None = None
    # Set by the /build/stop endpoint; build_stream() polls it to revert and stop early.
    stop_requested: bool = False
    # Set by /api/preview/runtime-error when the live preview reports an uncaught/render error.
    # Carries {"message", "stack", "ts"} (ts is a time.monotonic() stamp). build_stream() reads it
    # after a clean typecheck to catch runtime crashes that tsc can't see (a blank preview) and feed
    # them back to the agent to autofix. ts-gated so a stale error from a prior turn is ignored.
    runtime_error: dict | None = None
    # Per-turn model-call telemetry, wired by the /v1/chat/completions stream wrapper and read by
    # build_stream() to explain why a turn wrote nothing. model_calls = model inferences OpenCode ran
    # this turn; tool_call_responses = how many of those responses carried a tool_call. build_stream
    # resets both before each send. Reading them apart splits the three failure modes: 0 model calls =
    # OpenCode never invoked the model; model calls but 0 tool-call responses = the model never tried a
    # tool; tool calls but no disk edits = OpenCode received tool calls but didn't apply them.
    model_calls: int = 0
    tool_call_responses: int = 0
    # Working-tree hash the running turn compares against to tell whether anything on disk changed
    # (the ground-truth half of "did the agent write", alongside its edit-tool calls). Lives on the
    # project rather than inside build_stream because attach/upload/detach also write into the tree
    # — AGENTS.md, .gitignore, the public/data/ symlink — and they run on their own endpoints, not
    # under the turn lock. A user uploading a file mid-turn would otherwise look exactly like the
    # agent writing code, which on a read-only turn means a false "gate violated" AND a revert that
    # deletes the upload they just made. Those paths re-baseline it instead (see _rebaseline_turn).
    # Empty when no turn is running.
    turn_tree_baseline: str = ""
    # Where the UI sends a user to read what this project has cost, and the tag value to filter by
    # once they land. Both None off-Domino (or in fake/openai gateway mode), which hides the link —
    # a dead link to a dashboard that has no Sage data reads as a bug.
    cost_url: str | None = None
    cost_project: str | None = None

    def status(self) -> dict:
        s = self.control.snapshot()
        try:
            upstream = self.supervisor.upstream()
        except RuntimeError:
            upstream = None
        return {
            "id": self.id,
            "workspace": str(self.workspace.path),
            "preview_upstream": upstream,
            "attached": list(self.attached),
            "model": {
                # `mode` is what routes right now — the pin, while a turn is running (see
                # arm_turn_mode). `selected_mode` is where the user's picker actually sits, which is
                # what the picker must render: otherwise a mode changed mid-turn snaps back to the
                # pinned one on the next poll and looks like the click was dropped.
                "mode": s.mode.value,
                "selected_mode": self.control.selected_mode.value,
                "phase": s.phase.value,
                "picked_model": s.picked_model,
                "sensitivity_locked": s.sensitivity_locked,
                "asset_locked": self.control.asset_locked,
                "manual_locked": self.control.manual_locked,
                "catalog": {
                    "sovereign_plan": self.shim.catalog.sovereign_plan,
                    "sovereign_implement": self.shim.catalog.sovereign_implement,
                    "sovereign_ask": self.shim.catalog.sovereign_ask,
                    "plan": self.shim.catalog.plan,
                    "implement": self.shim.catalog.implement,
                    "ask": self.shim.catalog.ask,
                },
            },
            "cost": {"url": self.cost_url, "project": self.cost_project},
        }


class Orchestrator:
    def __init__(
        self,
        workspace_dir: Path,
        template: Path,
        gateway: GatewayClient,
        catalog: ModelCatalog,
        project_id: str = "app",
        opencode_cwd: Path | None = None,
        feedback: FeedbackRunner | None = None,
        assets: AssetProvider | None = None,
        resources: ResourceProvider | None = None,
        sensitivity_tag: str = DEFAULT_SENSITIVITY_TAG,
        domino_project_id: str | None = None,
        control_plane: ControlPlane | None = None,
        domino_project_name: str | None = None,
        workspace_id: str | None = None,
        domino_run_id: str | None = None,
        cost_project_label: str | None = None,
        gateway_ui_url: str | None = None,
        browser_gateway_base: str | None = None,
        opencode_client: OpenCodeClient | None = None,
    ) -> None:
        self._wm = WorkspaceManager(workspace_dir, template)
        self._project_id = project_id
        self._gateway = gateway
        self._catalog = catalog
        self._assets = assets or FakeAssetProvider()
        self._resources = resources or FakeResourceProvider()
        self._sensitivity_tag = sensitivity_tag
        # Total-size cap across all attached files (default 500 MiB). A file attach is a symlink,
        # not a copy, but the cap bounds what the agent/preview and the published dist/ pull in.
        self._attach_max_bytes = _env_int("SAGE_ATTACH_MAX_BYTES", 500 * 1024 * 1024)
        self._domino_project_id = domino_project_id
        # Domino control-plane wiring for Publish / Stop (None off-Domino / local runs -> the
        # endpoints report a clear "not available" instead of crashing).
        self._control_plane = control_plane
        self._domino_project_name = domino_project_name
        # Domino injects DOMINO_RUN_ID (the workspace SESSION's executionId), NOT the workspace id
        # stop_workspace() needs. We map run id -> workspace id by matching it against the project's
        # workspaces (mostRecentSession.executionId). `workspace_id` is a direct override (tests).
        self._workspace_id = workspace_id
        self._domino_run_id = domino_run_id
        # Cost attribution: the `sage-project` gateway tag, and the dashboard link the UI offers so
        # spend is read where it's authoritative rather than re-derived here. Both fall back to
        # nothing off-Domino, which hides the link instead of pointing it somewhere dead.
        self._cost_project_label = cost_project_label or project_id
        self._gateway_ui_url = gateway_ui_url
        # The LLM Gateway URL a PUBLISHED app's browser code calls (#7). The same URL Sage itself
        # routes through: a published app is served from the gateway's own host, so it is
        # same-origin there and the viewer's Domino session cookie authenticates the call. None
        # when Sage is not pointed at a Domino gateway, and then no app is given a model to call.
        self._browser_gateway_base = browser_gateway_base
        self._opencode_cwd = Path(opencode_cwd) if opencode_cwd else Path.cwd()
        self._feedback = feedback or FeedbackRunner()
        # One container hosts one project (D9): a single bound project, attached lazily on first
        # use (seeding the volume + rehydrating .sage/ from disk), memoized thereafter.
        self._project: Project | None = None
        self._oc_server: OpenCodeServer | None = None
        # Pre-supplied client (tests): _ensure_opencode already returns a non-None client untouched,
        # so injecting here means no server is ever started and the seam costs the production path
        # nothing. Same shape as the gateway/feedback/assets fakes above it.
        self._oc_client: OpenCodeClient | None = opencode_client
        self._oc_log_path: str | None = None  # OpenCode server stdout log, tailed into the stream on a no-call turn
        # Serializes read-modify-write of the workspace AGENTS.md: _write_agents_data_block (attach/
        # detach) and write_instructions both splice managed regions into the same file, and a
        # concurrent write could drop the other's region. Held around each full read-modify-write.
        self._agents_lock = threading.Lock()
        # Serializes build/approve turns: only one turn may stream at a time. A turn arms shared,
        # per-project state (read_only_turn, mode) and mutates one working tree; a second overlapping
        # turn would clear the first turn's read-only gate mid-flight (making the gated planner write
        # code, which then self-destructs as a "gate violation") and interleave edits on one tree.
        # The UI already queues composer messages behind a live turn, but uploads, an approve, or a
        # second client can still overlap — this is the backend backstop. Non-blocking: a would-be
        # overlap is refused with a clear event, not silently run. Stop stays lock-free (it only sets
        # stop_requested, which the running turn polls) so it can always interrupt the held turn.
        self._turn_lock = threading.Lock()

    def turn_busy(self) -> bool:
        """True while a build/approve turn holds the turn lock. The UI polls this to tell a dropped
        SSE connection (turn still running, keep showing Stop) from a finished turn — without it, a
        network blip makes the composer look idle and the next send hits _busy_refusal."""
        return self._turn_lock.locked()

    def project(self, start_preview: bool = True) -> Project:
        """Get-or-attach the single bound project. Idempotent: on first call it seeds the volume
        if empty, wires control/shim/supervisor, starts the preview, and rehydrates session/history/
        plan/model-overrides from .sage/; subsequent calls return the memoized Project (the preview
        is not restarted)."""
        if self._project is not None:
            return self._project
        workspace = self._wm.ensure(self._project_id)
        control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
        shim = EnforcementShim(control, self._effective_catalog(workspace), self._gateway,
                               project_name=self._cost_project_label)
        supervisor = ViteSupervisor(workspace.path, domino_base_prefix())
        if start_preview:
            supervisor.start()
        self._project = Project(self._project_id, workspace, supervisor, control, shim, TurnSnapshot(workspace.path),
                                cost_url=self._gateway_ui_url,
                                cost_project=self._cost_project_label if self._gateway_ui_url else None)
        self._rehydrate_attached(self._project)
        return self._project

    def _rehydrate_attached(self, project: Project) -> None:
        """Restore the attached-files list. The manifest (.sage/attachments.json) is the source of
        truth — it's committed, so it survives clones and orchestrator restarts (the in-memory list
        does not). Fall back to scanning public/data/ symlinks for older workspaces written before
        the manifest existed."""
        entries = project.workspace.read_attachments()
        if entries:
            project.attached[:] = entries
            # The sovereign lock is sticky but in-memory; re-fire it if any restored file is sensitive.
            if any(e.get("sensitive") for e in entries):
                project.control.on_assets_changed([True])
            return
        data_root = project.workspace.path / "public" / "data"
        if not data_root.is_dir():
            return
        for link in sorted(data_root.rglob("*")):
            if not link.is_symlink():
                continue
            rel = link.relative_to(project.workspace.path).as_posix()
            try:
                size = link.stat().st_size  # follows the symlink to the mount
            except OSError:
                size = 0
            project.attached.append(
                {"dataset_id": None, "dataset": link.parent.relative_to(data_root).as_posix() or link.parent.name,
                 "file": link.name, "path": rel, "size": size, "source": "dataset"}
            )

    def _ensure_opencode(self) -> OpenCodeClient:
        """Start the shared OpenCode server on first use (opencode.json in opencode_cwd points
        it at the shim). One server per container; sessions are scoped per workspace."""
        if self._oc_client is None:
            # Always capture OpenCode's server log (--print-logs) so a no-model-call turn can surface
            # its actual error (connection refused, provider/model load, auth) into the build stream —
            # the deployed workspace has no shell. Honor SAGE_OPENCODE_LOG if set, else a temp path.
            self._oc_log_path = os.environ.get("SAGE_OPENCODE_LOG") or str(
                Path(tempfile.gettempdir()) / "sage-opencode.log")
            self._oc_server = OpenCodeServer(cwd=self._opencode_cwd, log_path=self._oc_log_path)
            self._oc_client = OpenCodeClient(base_url=self._oc_server.start())
        return self._oc_client

    def resolved_agents(self) -> list[dict] | None:
        """The agents OpenCode resolved, for /api/diag. None when the server isn't up yet or the
        query failed — deliberately does NOT start it, so diag stays safe to hit mid-build."""
        if self._oc_client is None:
            return None
        try:
            return self._oc_client.agent_summaries()
        except Exception:
            return None

    def _opencode_log_tail(self, lines: int = 30) -> list[str]:
        """Last few lines of OpenCode's server log — surfaced when a turn makes no model call so the
        real reason (e.g. 'connect ECONNREFUSED 127.0.0.1:8080', a provider/auth error) is visible in
        the UI without shell access. Empty if logging isn't active or the file can't be read."""
        if not self._oc_log_path:
            return []
        try:
            with open(self._oc_log_path, encoding="utf-8", errors="replace") as f:
                return [ln.rstrip() for ln in f.readlines()[-lines:] if ln.strip()]
        except OSError:
            return []

    def _ensure_session(self, project: Project) -> str:
        client = self._ensure_opencode()
        if project.session_id is None:
            project.session_id = self._recover_session(project.workspace, client)
        if project.session_id is None:
            # No session-level model: use opencode.json's default; the shim's router enforces the
            # real model per request. (An explicit ModelRef at creation stalled turns.)
            project.session_id = client.create_session(directory=str(project.workspace.path))
            project.workspace.write_session_id(project.session_id)
        return project.session_id

    @staticmethod
    def _recover_session(workspace: Workspace, client: OpenCodeClient) -> str | None:
        """A session id persisted from a prior process may point at a session the current
        OpenCode server doesn't know about (e.g. its storage was reset); validate before reusing
        it so a stale id doesn't wedge every subsequent build call."""
        sid = workspace.read_session_id()
        if sid is None:
            return None
        try:
            client.messages(sid)
        except httpx.HTTPStatusError:
            return None
        return sid

    def build(self, prompt: str) -> dict:
        """Run one build to completion (non-streaming). Reuses the session, so repeated calls are
        follow-up turns with context. Requires gateway access."""
        # Serialize with the streaming turns: only one turn may run at a time (see _turn_lock). Refuse
        # rather than overlap another turn on the shared control + working tree.
        if not self._turn_lock.acquire(blocking=False):
            return {"ok": False, "error_count": 0, "decision": "busy",
                    "message": "A build is already running. Wait for it to finish or stop it first."}
        try:
            project = self.project()
            client = self._ensure_opencode()
            sid = self._ensure_session(project)

            def send_and_wait(text: str) -> None:
                project.last_gateway_error = None
                agent = _agent_for_mode(project.control.snapshot().mode)
                client.send_prompt(sid, text, agent=agent)
                client.wait_for_idle(sid)
                if project.last_gateway_error is not None:
                    err = project.last_gateway_error
                    raise RuntimeError(f"model call failed: {err['message']}")

            report, decision = run_feedback_loop(
                prompt,
                send_and_wait=send_and_wait,
                check=lambda: self._feedback.check(project.workspace.path),
                breaker=CircuitBreaker(),
            )
            return {"ok": report.ok, "error_count": len(report.errors), "decision": decision.reason, "message": report.as_agent_message()}
        finally:
            self._turn_lock.release()

    def _descriptor(self, project: Project, entry: dict) -> dict:
        """Typed shape summary (kind/summary/detail/size) for one attachment, cached in the manifest.

        The agent needs each file's SHAPE, never its content — the built app fetches the full file at
        runtime, so the raw bytes have no business in a prompt. Computing it here is deliberate:
        Python reads the /mnt/data mounts fine (the agent is the one that can't), and caching it in
        the committed manifest means each mount file is described once, not once per turn. Entries
        written before descriptors existed are backfilled on first use."""
        cached = entry.get("descriptor")
        if cached:
            return cached
        try:
            real = _safe_join(project.workspace.path, entry["path"]).resolve()
            d = describe(str(real))
            if d["kind"] == "image":
                # Whether the agent will actually SEE this image, settled once here so the Data
                # panel can say so. Without it the failure is invisible to the user: the agent
                # simply answers "unknown" and nothing explains why.
                d["shown"] = fit_image(str(real), _MAX_INLINE_IMAGE_BYTES) is not None
        except (ValueError, OSError) as e:  # describe() itself never raises; _safe_join can
            d = {"kind": "unavailable", "summary": f"not described ({type(e).__name__})",
                 "detail": "", "size": 0}
        entry["descriptor"] = d
        project.workspace.write_attachments(project.attached)
        return d

    def _resolve_mentions(self, project: Project, mentions: list[str] | None) -> list[dict] | None:
        """Map @-mentioned workspace paths to the attachment dicts send_prompt renders. Only paths
        actually in this project's attachment list are honored (never an arbitrary caller path).

        The agent is handed the WORKSPACE-RELATIVE path, never the resolved mount path: OpenCode's
        read tool hangs forever on absolute paths outside its project root (/mnt/data dataset
        mounts), while the in-root symlink at public/data/... reaches the same bytes and reads fine.
        We still resolve here — but only to confirm the symlink points at a real file and to describe
        it. None when nothing to attach."""
        if not mentions:
            return None
        known = {e["path"]: e for e in project.attached}
        out: list[dict] = []
        for m in mentions:
            entry = known.get(m)
            if entry is None:
                continue
            try:
                real = _safe_join(project.workspace.path, m).resolve()
            except (ValueError, OSError):
                continue
            if not real.is_file():
                continue
            d = self._descriptor(project, entry)
            item = {"path": m, "name": PurePosix(m).name,
                    "summary": d["summary"], "detail": d["detail"]}
            if d["kind"] == "image":
                item["image_uri"] = self._image_data_uri(real)
            out.append(item)
        return out or None

    def _image_data_uri(self, real: Path) -> str | None:
        """An image inlined as `data:<mime>;base64,...` for the agent's prompt, or None if it can't
        be. Images are the one type where a descriptor isn't enough — the pixels ARE the content.

        Oversized images are SHRUNK rather than refused (see describe.fit_image): a phone photo or
        hi-DPI screenshot is exactly what users attach, vision models downsample anyway, and the
        alternative is an agent that can't see the picture it was asked about. None only when the
        file won't decode at all — the caller then tells the agent it cannot see it.
        """
        fitted = fit_image(str(real), _MAX_INLINE_IMAGE_BYTES)
        if fitted is None:
            return None
        data, mime = fitted
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    def build_stream(self, prompt: str, mentions: list[str] | None = None):
        """Public entry: serialize this turn behind the per-project turn lock, then stream it.

        One turn at a time. If a turn is already streaming, refuse rather than run a second one
        concurrently (see _turn_lock) — overlapping turns corrupt the shared read-only gate and
        working tree. The refusal is a clean error + done(busy) so the UI surfaces it, not a hang.

        A bare approval typed while a plan is waiting ("ok build") means the same thing as clicking
        Approve, so it runs THAT plan instead of falling into the gate and proposing a second one."""
        if not self._turn_lock.acquire(blocking=False):
            yield from self._busy_refusal()
            return
        try:
            if _looks_like_approval(prompt) and (self.project().workspace.read_plan() or "").strip():
                yield {"type": "plan-stale", "note": "Approved in chat — building this plan."}
                yield from self._approve_locked(user_text=prompt)
                return
            if (self.project().control.snapshot().mode is Mode.ASK
                    and _looks_like_change_request(prompt)):
                yield from self._ask_mode_refusal(prompt)
                return
            yield from self._build_stream(prompt, mentions)
        finally:
            self._clear_turn_baseline()
            self._turn_lock.release()

    def _clear_turn_baseline(self) -> None:
        """Mark "no turn running" so _rebaseline_turn stops touching the baseline once the turn that
        owns it is over. Best-effort — a project we can't resolve has no baseline to clear.

        Also drops active_session_id. It's cleared here, at the one place that already means "the
        turn is over", rather than at each of _build_stream's many exits — a phased build reassigns
        it per phase, and the turn lock means no other turn can observe it mid-flight anyway."""
        try:
            self.project().turn_tree_baseline = ""
            self.project().active_session_id = None
        except Exception:
            pass

    def _ask_mode_refusal(self, prompt: str):
        """Events for a change request typed in Ask mode — refused up front, before any inference.

        Ask strips every write and shell tool out of each request (see shim.enforcement.handle), so an
        agent handed "remove the dataset from the UI" holds only read and grep. It doesn't stop; it
        scopes the edit it can't make — a live turn spent 153s and ~158 consecutive greps hunting the
        call sites it would have changed, and only said it had written nothing at the very end. There
        is nothing to learn from running that turn, so don't: name the rule and hand back the
        one-click way to actually run it (the UI turns `prompt` into a Build-in-Auto button)."""
        project = self.project()
        message = ("Ask mode answers questions and never changes files, so this turn was stopped "
                   "before it ran — nothing was searched, built, or spent. Build it in Auto, or "
                   "switch modes and send it again.")
        for ev in ({"type": "user", "text": prompt},
                   {"type": "ask-blocked", "prompt": prompt, "message": message},
                   {"type": "done", "ok": False, "decision": "ask mode (read-only)"}):
            project.workspace.append_history(ev)
            if ev["type"] != "user":  # the composer already rendered the user's own bubble
                yield ev

    def _busy_refusal(self):
        """Events yielded when a turn is refused because another is already streaming."""
        yield {"type": "error", "busy": True,
               "message": "A build is already running. Wait for it to finish or stop it first, "
                          "then resend."}
        yield {"type": "done", "ok": False, "decision": "busy"}

    def _seen_baseline(self, client, sid: str) -> set[tuple[str, object]]:
        """Keys of every assistant part already in the session, so a turn only emits its OWN parts.

        client.messages(sid) returns the ENTIRE session on every poll, and the emit-tracking `seen`
        set starts empty for each user turn. Without this baseline, a follow-up turn's first poll
        re-walks the previous turn's completed parts and re-emits them — the prior turn's summary
        reappearing at the top of the new turn (the "ordering" echo). Keys come from _part_key and
        must match the poll loop in _build_stream. Best-effort: on a poll error we return an empty
        baseline (worst case is the echo, not a broken build) and let the loop retry."""
        seen: set[tuple[str, object]] = set()
        try:
            for m in client.messages(sid):
                if m.get("type") == "assistant":
                    for i, part in enumerate(m.get("content", [])):
                        seen.add(_part_key(m, i, part))
        except httpx.HTTPError as e:
            log.warning("could not baseline session messages, prior-turn echo possible: %s", e)
        return seen

    def _build_stream(self, prompt: str, mentions: list[str] | None = None, *, is_approval: bool = False,
                      user_text: str | None = None, mode: Mode | None = None,
                      session_id: str | None = None, brief: PlanStep | None = None):
        """Same loop as build(), but yields progress events (dicts) as it goes: agent text/tool
        activity, typecheck results, iteration, and a final done event. Reuses the session so
        each call is a follow-up turn (modify/add features) with full context.

        Assumes the caller holds _turn_lock (build_stream / approve_stream acquire it).

        `session_id` + `brief` run this as ONE PHASE of a phased build (see _phased_approve) rather
        than a whole turn: a fresh session, and a step whose scope is the brief. Passing `brief`
        sets owns_turn False, which hands five turn-level responsibilities back to the caller — the
        user's chat bubble, the Stop revert, the failure flag, the git commit, and the single `done`.
        Everything else here (typecheck, circuit breaker, the implement-nudge and its model
        escalation, runtime-error and data-leak fixups) is exactly what one phase needs, which is why
        a phase reuses this loop instead of forking a second copy of it.

        `mentions` are workspace paths of attached files the user @-referenced; they're resolved to
        real files and attached to this turn's prompt (see _resolve_mentions)."""
        import time

        project = self.project()
        # Repair the warm node_modules before the turn, not only at attach — attach happens once per
        # process, and an agent-run `npm install` can destroy the symlink mid-session and leave the
        # workspace unable to build or preview (see WorkspaceManager.link_warm_deps).
        if self._wm.link_warm_deps():
            log.warning("workspace: restored the warm node_modules — an npm install had removed it")
        client = self._ensure_opencode()
        # A phase runs in the throwaway session its caller made; everything else reuses the project's.
        owns_turn = brief is None
        sid = session_id or self._ensure_session(project)
        project.active_session_id = sid
        breaker = CircuitBreaker()
        current = prompt
        # Attach the @-mentioned files to the user's turn only — not to the internal nudge/fix
        # follow-ups below, which carry no new user reference.
        mention_files = self._resolve_mentions(project, mentions)
        # What the turn actually carries. An attachment that silently arrives without its pixels
        # (image too large, wrong kind, unreadable) is indistinguishable from a normal descriptor
        # once it reaches the agent, so record it here where /api/diag can show it.
        log.info("turn attachments: requested=%s resolved=%s", len(mentions or []),
                 [(a["name"], a["summary"], len(a.get("image_uri") or "")) for a in (mention_files or [])])

        # Persist only the events the UI actually renders as a chat bubble/card/divider, so
        # replaying history reproduces the same transcript without ephemeral "active"/spinner noise.
        def persist(ev: dict) -> dict:
            # Failure-triggered replan, recording half (the reading half is the delimited block below).
            # Every terminal `done` of a turn passes through persist(), which is why the record lives
            # here rather than at the seven separate yield sites that can end a turn.
            #
            # What counts as a failure: `ok=False` on a turn that was an attempt to BUILD. That covers
            # the gateway/OpenCode errors, the typecheck breaker giving up, "couldn't get past
            # planning", and a gate violation whose edits were discarded. It deliberately does NOT
            # cover a user pressing stop (that yields `stopped`, never a `done`), a busy refusal (never
            # reaches this stream), or the two kinds of turn excluded below.
            #
            # `answer_only` and `arch` are assigned further down and read late — persist() only runs
            # once the stream is under way. Neither kind is a build attempt: a question answered in
            # prose and an architecture document say nothing about whether the app builds, so they
            # leave the recorded outcome exactly as they found it rather than clearing a real failure.
            #
            # A phase doesn't own the outcome: six phases would write the flag six times, and a build
            # that failed at phase 4 would be recorded as a success by phases 1-3. _phased_approve
            # sets it once for the whole build.
            if owns_turn and ev["type"] == "done" and not answer_only and not arch:
                project.workspace.set_last_turn_failed(not ev.get("ok"))
            # A phase's `done` is swallowed by _run_step so the UI sees exactly one per build; it
            # must not reach history either, or a reload would replay six "build is clean" dividers.
            if ev["type"] in _PERSISTED_EVENTS and (owns_turn or ev["type"] != "done"):
                project.workspace.append_history(ev)
            return ev

        # Refresh the agent-facing archive of earlier turns BEFORE the baseline below, so this write
        # is part of the pre-turn state. Written after it, the read-only gate would see a changed
        # working tree and fail an Ask/Plan turn that wrote nothing.
        project.workspace.render_history_md()
        # Snapshot before touching history/files so a stop mid-turn can restore exactly this
        # state, and remember how many history entries pre-date this turn so a stop can drop
        # everything appended since (the turn disappears from the transcript entirely).
        project.snapshot.commit_before_turn()
        history_baseline = project.workspace.history_len()

        # Plan gate (SPEC P6): in Plan mode (or on the first turn of a fresh project), run the
        # read-only planner and stop for the user to approve — this turn deliberately writes no code.
        # Pin the mode for the whole turn before anything reads it (token-scoped, exactly like the
        # read-only guarantee armed further down). The shim consults control.snapshot() on every
        # request, so unpinned, a mid-turn pick from the picker split one turn in half: its first
        # inferences ran as Implement with edit tools and the coder model, its later ones as Ask with
        # both stripped, the abandoned tool calls still sitting in context. The pick is not lost —
        # it's the user's standing choice now and runs their next turn. `mode` overrides the pick for
        # a turn Sage runs on the user's behalf (approving a plan from a read-only mode).
        mode_token = project.control.arm_turn_mode(mode or project.control.snapshot().mode)
        mode_at_start = project.control.snapshot().mode
        is_question = _looks_like_question(prompt)
        has_built = project.workspace.has_built()
        # An approval is the user saying "build this plan now" — never gate it (that would re-propose a
        # plan for an already-approved build and loop forever) and never treat it as a question.
        # An explicit request for an architecture (see _wants_architecture) produces a document, not a
        # build and not a build plan — so it overrides the mode in EVERY mode, including Ask. Without
        # this, Plan turned the request into a ten-step build plan and Implement just built it; Ask
        # answered in prose, which is right for a question and wrong for a request for a document.
        # Ask is where a design question is most naturally typed, so it gets the artifact too — the
        # turn is read-only either way, so nothing about Ask's contract changes.
        arch = not is_approval and _wants_architecture(prompt)
        # An explicit ask for a plan (see _wants_plan) is the same instruction as picking Plan mode,
        # typed instead of clicked, so it gates in every mode too. Ranked below arch: a prompt naming
        # both artifacts wants the heavier one, and that keeps the existing precedence untouched.
        wants_plan = not is_approval and not arch and _wants_plan(prompt)
        settings = project.workspace.read_settings()
        skip_planning = bool(settings.get("skip_planning"))
        # Only affects the SHAPE of a plan this turn writes; the phased execution itself happens on
        # the approve turn (_phased_approve). A plan written before the toggle was on simply won't
        # parse into steps, and falls back to a normal build.
        phased_build = bool(settings.get("phased_build"))
        gate = False if is_approval else arch or _should_gate(
            mode=mode_at_start,
            has_built=has_built,
            skip_planning=skip_planning,
            is_question=is_question,
            wants_plan=wants_plan,
        )
        # --- failure-triggered replan (case 3), reading half -------------------------------------
        # Read-and-consume, before the request goes out — the gate can only be applied here, because
        # read-only is enforced by stripping write/shell tools from the OUTGOING request (see
        # shim.enforcement) and cannot be applied retroactively.
        #
        # Consumed on any turn that isn't a question, whether or not it goes on to gate: that's what
        # makes this one-shot. Fail → plan → approve → fail again gives one gate per failure, never a
        # standing approval wall. A question is the deliberate exception, mirroring _should_gate's
        # is_question rule — asking "why did that break?" between the failure and the retry must not
        # spend the gate the failure earned.
        #
        # Ordered ahead of the scope classifier below on purpose: a failure has already decided this
        # turn should be planned, so there is nothing left for a model call to change and
        # _scope_gate_applies declines it on `gate`. The cheap deterministic signal shadows the paid one.
        prev_turn_failed = project.workspace.read_last_turn_failed()
        if not is_question:
            project.workspace.set_last_turn_failed(False)
        gate = gate or _failure_gate_applies(
            mode=mode_at_start,
            is_approval=is_approval,
            is_question=is_question,
            skip_planning=skip_planning,
            prev_turn_failed=prev_turn_failed,
        )
        # --- end failure-triggered replan --------------------------------------------------------
        # Nothing above gated this turn, and on a built project in Auto nothing ever will again: the
        # automatic gate is keyed on has_built, so from turn two on, "make the table sortable" and
        # "add auth, orgs and a billing page" take the same ungated path to code. Scope is the one
        # thing here a string can't answer, so this is the single decision in the turn path that asks
        # a model (see scope.wants_a_plan — biased to build, fails open, hard-bounded).
        #
        # Auto only. Implement is the user saying "just build it" and Plan already gates every turn;
        # overriding either would be second-guessing an explicit choice, and this exists precisely
        # because Auto is the mode with no explicit choice in it.
        answer_only = _is_answer_only(mode=mode_at_start, is_question=is_question,
                                      is_approval=is_approval, arch=arch, wants_plan=wants_plan)
        if _scope_gate_applies(mode=mode_at_start, has_built=has_built, gate=gate,
                               answer_only=answer_only, skip_planning=skip_planning):
            gate = scope.wants_a_plan(
                prompt,
                gateway=project.shim.gateway,
                catalog=project.shim.catalog,
                locked=project.control.snapshot().sensitivity_locked,
                root=project.workspace.path,
                session=project.session_id,
                version=project.shim.version,
            )
            if gate:
                log.info("scope: planning a substantial request on a built project")
        # A gated turn's prompt carries a planning-context preamble scoped to whether the app exists
        # yet. First build (fresh template): tell the planner it needn't read anything and can plan
        # straight from the request — this is what keeps a weak sovereign planner from read-looping
        # without ever producing plan text. Iteration (Plan mode on an already-built app): the plan
        # must fit the current code, so have it briefly read what the change touches first. The
        # preamble rides on `current` (what's sent to the agent), never on the persisted user bubble.
        #
        # Both branches also pin the plan's VOICE. Every write and shell tool is stripped from a
        # gated turn (see EnforcementShim), so the model can only describe — but left to itself it
        # narrates in the past tense ("I built a dataset explorer with…") and then hunts for a write
        # tool it doesn't have. The user reads that card as a finished build, which is the opposite
        # of what the approval gate is for: they approve without reading, or think the gate leaked.
        _PLAN_VOICE = ("Write the plan as a proposal for work not yet done: future tense, no claim "
                       "that anything has been built, changed, or verified. You have no write, edit, "
                       "or shell tools on this turn by design — don't look for them. Write each step "
                       "as the work itself, starting with a verb ('Define the sample data…', 'Add a "
                       "preview table…'). Never open a step with 'I will' or 'I'll' — the same "
                       "opener repeated down the list is what makes a plan unreadable.")
        # And its SHAPE. The plan lands in an approval card the user has to skim in a few seconds;
        # left to itself the planner writes one unbroken wall of prose (and sometimes restates it),
        # which is unreadable at any length. Long is fine — shapeless is not, so the structure is
        # spelled out here rather than left to the agent prompt alone.
        _PLAN_SHAPE = ("Format it exactly like this, in Markdown, and write nothing outside it:\n"
                       "- One short sentence saying what the app is.\n"
                       "- Then a '## Plan' heading and a numbered list. Each step is a single line: "
                       "a bolded 2-4 word label, then ' — ', then one sentence. No paragraph steps, "
                       "no sub-lists, no code.\n"
                       "- Then, ONLY if something genuinely needs the user to decide, an '## Open "
                       "questions' heading and short bullets. Nothing to ask: leave the heading out "
                       "entirely rather than writing 'None'.\n"
                       "Never repeat a sentence or restate a step you've already written.")
        # The phased variant. Same plan, but each step becomes a self-contained handoff brief,
        # because in a phased build the model that executes step 4 is a BRAND-NEW session: it never
        # read this plan, never saw steps 1-3, and can't ask. Every field below exists because a cold
        # executor fails without it — `Files` so its first act isn't a whole-tree grep that refills
        # the context the fresh session just bought us, `Done when` so verification travels with the
        # work instead of being inferred, `Don't touch` so a later step doesn't rewrite an earlier
        # one's output it has never seen. Kept separate from _PLAN_SHAPE rather than replacing it:
        # the single-context shape is what every non-phased build still uses.
        _PLAN_SHAPE_PHASED = (
            "Format it exactly like this, in Markdown, and write nothing outside it:\n"
            "- One short sentence saying what the app is.\n"
            "- Then a '## Plan' heading.\n"
            "- Then, for each step, a '### N. Label' heading (N is 1, 2, 3…; the label is 2-4 "
            "words), followed by exactly these bullets:\n"
            "  - Files — the workspace-relative files this step creates or edits, comma-separated. "
            "This is the step's allowlist, so it MUST include any earlier file the step has to edit "
            "to connect its work up — the table that needs a row-click handler, the parent that "
            "renders the new component. A step that cannot reach the file it needs cannot finish. "
            "But list the FEWEST files that does it: a step licensed to edit the main screen will "
            "rebuild the main screen, and a later step then throws that work away. A data or types "
            "step should not name the app's main component at all. "
            "Name them even if you are guessing; a wrong guess is cheaper than no guess.\n"
            "  - Do — one or two sentences of the work itself, starting with a verb.\n"
            "  - Done when — one sentence naming the observable result that proves this step is "
            "finished (a file exports something, the preview renders something, the app compiles).\n"
            "  - Don't touch — earlier files this step has no business editing at all, so it can't "
            "rewrite finished work it cannot see. Never list a file that also appears in this step's "
            "Files; that contradiction stops the step dead. Omit this bullet entirely when there are "
            "none; never write 'None'.\n"
            "Each step must be executable by someone who can see the code but has NOT read the "
            "other steps: no 'as above', no 'the same table', no pronouns pointing at another step. "
            "Aim for 3-7 steps; a step should be one coherent change, not a whole feature and not a "
            "one-line edit.\n"
            "- Then, ONLY if something genuinely needs the user to decide, an '## Open questions' "
            "heading and short bullets. Nothing to ask: leave the heading out entirely rather than "
            "writing 'None'.\n"
            "Write no code blocks. Never repeat a sentence or restate a step you've already written.")
        # The architecture deliverable — the same gated, read-only turn, but the artifact is a design
        # rather than a task list. The distinction is the whole point of the branch: a build plan
        # answers "what will you do", an architecture answers "what are the parts and how do they
        # talk". Asked for the latter, the planner produced the former ("Define queue model", "Add
        # queue panel"), which is a fine plan and not what was asked for.
        _ARCH_SHAPE = ("Format it exactly like this, in Markdown, and write nothing outside it:\n"
                       "- One short sentence saying what the design is for.\n"
                       "- Then a '## Diagram' heading and ONE ```mermaid code block — a `flowchart "
                       "TD` or `LR` of the components and the data flowing between them. Keep node "
                       "labels to a few words. This is the only code block you may write.\n"
                       "- Then a '## Components' heading and a bullet per part: a bolded name, "
                       "then ' — ', then one sentence on what it owns.\n"
                       "- Then a '## Data flow' heading and a short numbered list tracing one trip "
                       "through the system end to end.\n"
                       "- Then a '## Tradeoffs' heading and short bullets on the calls this design "
                       "makes and what it gives up.\n"
                       "Describe structure, not implementation steps: no file names, no function or "
                       "component signatures, no source code beyond the one mermaid block. Never "
                       "repeat a sentence you've already written.")
        if arch:
            current = ("Describe the ARCHITECTURE for the request below. This turn produces a design "
                       "document, not a build and not a build plan — do not write a numbered list of "
                       "implementation steps, and do not describe the work as something you are about "
                       "to start. If reading a file or two helps the design fit the app, keep it "
                       "brief, then write the document.\n\n" + _ARCH_SHAPE
                       + "\n\nThe request:\n" + current)
        elif gate:
            # Phased builds need the heavier per-step shape; everything else keeps today's prose plan.
            # Only in Auto: Plan/Implement are explicit user modes, and a phased build silently
            # changing what "Plan" produces there would be a surprise, not a feature.
            shape = _PLAN_SHAPE_PHASED if (phased_build and mode_at_start is Mode.AUTO) else _PLAN_SHAPE
            if has_built:
                current = ("Plan a change to this existing app. Briefly read the files your change "
                           "would touch so the plan fits the current code, then write the plan. "
                           + _PLAN_VOICE + "\n\n" + shape + "\n\n" + current)
            else:
                current = ("This is a brand-new app from a blank template — there are no existing "
                           "files worth reading, so plan straight from the request. "
                           + _PLAN_VOICE + "\n\n" + shape + "\n\n" + current)
        # Answer-only turn: answered directly and read-only, no plan card, no build (see _is_answer_only).
        # Read-only so answering a question can never quietly build or edit an app; and unlike a normal
        # Auto turn, a clean no-edit answer is the goal, so it must not be nudged to implement.
        # Pin the ANSWER's voice, for the same reason the gated turn pins the plan's (see _PLAN_VOICE):
        # the sage-ask agent prompt alone hasn't held. Asked "what tech stack will be used", the agent
        # answered and then announced the build it was about to start — "Next I'm replacing the starter
        # screen with the dataset explorer itself" — and opened a task list, on a turn that has no write
        # tools and returns without building. The user reads that as a build in progress and waits for
        # an app that is never coming. The agent prompt covers voice and forbids restating an earlier
        # plan, but says nothing about announcing future work; this preamble does, and it rides every
        # answer-only turn whichever agent or model got picked.
        # The no-edit-tools sentence is load-bearing, not a restatement of the first one: told only
        # that it isn't building, the agent still SCOPED the change call site by call site — reading
        # and grepping for three minutes for an edit it was never offered the tools to make. It has to
        # know the tools are absent, and that a couple of sentences is the whole job.
        if answer_only:
            current = ("Answer this question and stop. You are not building or changing the app on "
                       "this turn: don't announce work you're about to start ('Next I'll…'), don't "
                       "open a task list, and don't present the answer as a step towards a build. You "
                       "have NO edit, write, or shell tools this turn — they are withheld, so no "
                       "amount of searching will let you make a change. If the answer implies a "
                       "change, describe it in a sentence or two from what you already know; do not "
                       "go and find every call site you would have edited. The user will ask for it "
                       "if they want it.\n\n"
                       # Every answer-only turn may draw, and the model decides when. A keyword rule
                       # tried this first and couldn't work: "how does data get from the upload to the
                       # table" is diagrammable because of its SHAPE, not its vocabulary, so catching
                       # it by noun would have meant listing every noun in the app. The judgement is
                       # safe to hand over precisely here — an answer-only turn has no card, no file
                       # and no Build button, so a diagram nobody needed costs one block in a reply
                       # that was prose anyway. The when-NOT-to sentence is the load-bearing half: a
                       # model told it may draw will draw every time unless told when not to.
                       "Your answer may include ONE ```mermaid flowchart, and only when the question "
                       "is really about how parts connect or how something moves through the app — "
                       "skip it and answer in words when a diagram wouldn't add anything, which is "
                       "most of the time. Keep node labels to a few plain words, with no parentheses, "
                       "quotes, commas or colons inside a label, since those break the diagram. Write "
                       "no other code blocks.\n\n" + current)
        plan_text_parts: list[str] = []  # accumulates the planner's text to persist as plan.md

        # Tell the UI whether a plan card still waiting for approval survives this turn. It doesn't
        # once we're about to overwrite plan.md (a gated turn) or change the app under it; it does
        # across an answer-only turn, which touches neither — asking a question shouldn't cost the
        # user the plan they were reading.
        if not answer_only and not is_approval:
            yield {"type": "plan-stale",
                   "note": "Superseded by the architecture below." if arch
                           else "Superseded by a newer plan below." if gate
                           else "No longer current — the app changed after this plan."}

        # `user_text` is what the person actually typed, when that differs from the prompt we send the
        # agent (a typed approval is expanded into the full approve prompt) — the transcript should
        # replay their words, not ours. A phase writes none: the user approved once, so a phased
        # build is one bubble in the transcript, not six copies of their approval.
        if owns_turn:
            project.workspace.append_history({"type": "user", "text": user_text if user_text is not None else prompt})

        # The user's own model pick (None in Auto). Set when a planning stall forces us to pin the
        # strong model for the Implement retry (see the nudge branch); restored on exit so we never
        # leave the user's own pick clobbered.
        original_pick = project.control.snapshot().picked_model
        escalated_pick = False

        # Arm the read-only guarantee for a gated (plan) turn OR an answer-only turn (Ask mode / any
        # Auto question): the shim strips every write/shell tool from each request, which stops the
        # turn writing code (OpenCode's own per-agent permission block doesn't). Token-scoped to THIS
        # turn — disarm only clears our own arming, so nothing drops the guarantee out from under us.
        # The reason rides with the arming: both kinds withhold write and shell tools, but only an
        # answering turn also loses the task-list tool (see TODO_TOOLS). Also reported in the turn
        # summary, so a turn that wrote nothing can say which rule stopped it.
        read_only = _read_only_reason(mode=mode_at_start, answer_only=answer_only, gate=gate, arch=arch)
        ro_token = project.control.arm_read_only(read_only) if (gate or answer_only) else None

        # Internet access is default-denied; arm it for THIS turn only when the prompt asked for the
        # web (a URL or an intent verb). Token-scoped like read-only, disarmed on every exit.
        web_token = project.control.arm_web() if _wants_web(prompt) else None

        def agent_wrote() -> bool:
            """Did the AGENT change the app this turn? Its own edit-tool calls, plus the working
            tree as ground truth for writes no tool reported (the `printf > file` shell hole). The
            tree baseline moves when a concurrent attach/upload writes into the workspace, so a user
            uploading data mid-turn is not mistaken for the agent (see Project.turn_tree_baseline)."""
            return made_edits or project.snapshot.working_tree_hash() != project.turn_tree_baseline

        def restore_mode() -> None:
            # Dropping the pin is the whole restore: a mid-turn escalation moved the PINNED mode, so
            # the user's standing choice was never touched and there is nothing to put back. Whatever
            # they picked while this turn streamed is what runs next.
            project.control.disarm_turn_mode(mode_token)
            if escalated_pick:
                project.control.pick(original_pick)
            if ro_token is not None:
                project.control.disarm_read_only(ro_token)
            if web_token is not None:
                project.control.disarm_web(web_token)

        def handle_stop() -> dict:
            project.stop_requested = False
            # A phase reverts nothing: discard_changes() resets to HEAD, which after per-phase
            # checkpoints is only the CURRENT phase — it would leave phases 1..n-1 on disk while
            # erasing the transcript that explains them. _phased_approve reverts the whole build to
            # its own base instead (snapshot.discard_to), which is what the user asked for by
            # stopping. The bare "stopped" still flows out so the caller knows to do it.
            if owns_turn:
                project.snapshot.discard_changes()
                project.workspace.truncate_history(history_baseline)
            restore_mode()
            return {"type": "stopped"}

        # client.messages(sid) returns the ENTIRE session's messages on every poll, and `seen` starts
        # empty for each user turn (this is a fresh _build_stream call). So without a baseline, this
        # turn's first poll re-walks the PREVIOUS turn's already-completed assistant parts and re-emits
        # them — the prior turn's summary reappearing at the top of the new turn (the "ordering" echo).
        # Pre-seed `seen` with every part that already exists before we send this turn's prompt, so
        # only parts produced by THIS turn are emitted. Within the turn `seen` also persists across the
        # nudge/fix iterations of the loop below, so we never re-emit our own earlier parts either.
        seen: set[tuple[str, object]] = self._seen_baseline(client, sid)
        # Text already shown this turn, so a repeat is dropped rather than printed twice. Scoped to the
        # turn (not the session): a later turn restating something is usually answering a new question.
        emitted_text: set[str] = set()
        # A clean typecheck of the untouched template must NOT count as a finished build: track
        # whether the agent actually edited files, and if a turn ends clean with zero edits, nudge
        # it to implement instead of declaring success. Capped so a model that refuses to write
        # can't loop forever.
        made_edits = False
        nudges = 0
        MAX_NUDGES = _env_int("SAGE_MAX_NUDGES", 3)
        # When a turn routed to the cheap implement-tier coder writes nothing, pin the strong
        # plan-tier model for the retry (works from Auto or explicit Implement). On by default;
        # set SAGE_IMPLEMENT_STRONG_FALLBACK=0 to keep retries on the originally-routed model.
        strong_fallback = os.environ.get("SAGE_IMPLEMENT_STRONG_FALLBACK", "1").strip().lower() not in ("0", "false", "no")
        # Ports for the OpenCode->shim wiring check surfaced in each turn-summary: control_port is what
        # this process serves /v1 on; base_port is what opencode.json tells OpenCode to dial. If they
        # differ and a turn records 0 model calls, inference bypassed the shim (routing/sovereignty
        # never ran) — shown as a warning in-stream since the deployed workspace has no shell/logs.
        control_port = int(os.environ.get("SAGE_CONTROL_PORT", "8080"))
        base_port = _opencode_base_port(self._opencode_cwd)
        # Direct vendor keys OpenCode can auto-detect and use to reach a model WITHOUT going through
        # the shim's provider (localhost baseURL). If the shim is bypassed, their presence is the
        # likely reason inference still worked — and means sovereignty/routing were silently skipped.
        vendor_keys = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY") if os.environ.get(k)]
        # A clean typecheck doesn't mean the app runs: a render/runtime throw (e.g. calling a Date
        # method on a string) blanks the preview but passes tsc. The open preview reports such throws
        # to project.runtime_error; we feed them back to fix, bounded so a crash we can't fix can't loop.
        runtime_fixes = 0
        MAX_RUNTIME_FIXES = 3
        leak_fixes = 0
        MAX_LEAK_FIXES = 2
        LEAK_FIX_NUDGE = (
            "You copied attached data into the app's source, which leaks it into git — attached files "
            "live under public/data/ (gitignored on purpose) and must be READ from there at runtime, "
            "not duplicated into src/. Delete the copy you made and load the data by fetching its "
            "served path instead (see the 'Attached data' section in AGENTS.md for the exact URL)."
        )
        IMPLEMENT_NUDGE = (
            "You've explored and planned but haven't written any code yet. Now IMPLEMENT the "
            "request: edit the project files (start with src/App.tsx) so the app actually builds "
            "what was asked. Make the code changes now."
        )
        RUNTIME_FIX_NUDGE = (
            "The app compiled but threw a runtime error when it rendered in the browser, so the "
            "preview is blank. Fix the code so it renders without throwing. Do not just guard the "
            "symptom — find and fix the root cause.\n\nError: {message}\n\nStack:\n{stack}"
        )
        while True:
            if project.stop_requested:
                yield handle_stop()
                return
            yield {"type": "turn", "prompt": current[:120]}
            project.last_gateway_error = None
            # Reset per-turn model-call telemetry; the shim stream wrapper repopulates it as OpenCode
            # drives this turn's inferences (see Project.model_calls).
            project.model_calls = 0
            project.tool_call_responses = 0
            # Detect edits made by THIS turn, not cumulatively since build start. Reset the tool-based
            # flag and fingerprint the working tree now; compare after the turn. Without this, once any
            # turn writes a file every later (possibly no-op) turn reads as "wrote code".
            made_edits = False
            # Published on the project so a concurrent attach/upload/detach can move it forward as it
            # writes AGENTS.md / .gitignore / public/data/ — see Project.turn_tree_baseline.
            project.turn_tree_baseline = project.snapshot.working_tree_hash()
            # A gated turn is pinned to the read-only planner regardless of the user's mode; it
            # proposes a plan and never edits, so it always lands in the no-edit fork below. A first-
            # turn question uses the read-only Q&A agent — it answers, it doesn't plan or build.
            # An architecture request gates too, but not onto sage-plan: that agent's prompt hardcodes
            # a build plan and bans every code block, which would strip the one mermaid diagram the
            # document is built around.
            if arch:
                agent = "sage-architect"
            elif gate:
                agent = "sage-plan"
            elif answer_only:
                agent = "sage-ask"
            else:
                agent = _agent_for_mode(project.control.snapshot().mode)
            # Which agent this turn actually asked for, and whether the plan gate was armed. OpenCode
            # falls back to its default build agent when a name doesn't resolve, so a turn that ignores
            # a mode's read-only permission looks identical to one that honored it — log the intent so
            # /api/diag's log_tail can be compared against its `agents` list.
            log.info("turn: agent=%s gate=%s answer_only=%s arch=%s mode=%s", agent, gate,
                     answer_only, arch, project.control.snapshot().mode.value)
            # Boundary for the runtime-error check below: only a crash the preview reports AFTER this
            # send belongs to this turn's code (an earlier turn's render reported before send_ts).
            send_ts = time.monotonic()
            client.send_prompt(sid, current, agent=agent, attachments=mention_files)
            mention_files = None  # attach only on the first (user) turn, not the nudge/fix follow-ups
            appeared = False
            start = time.monotonic()
            # The shim classifies plan/implement per model call (phase_classifier). We only observe
            # the resulting phase here to keep the UI's live indicator in sync — routing is decided
            # in the shim, not here, so it stays per-step and race-free.
            last_phase = project.control.snapshot().phase.value
            last_active: str | None = None  # last "active" label emitted (dedup across 1s polls)
            poll_failures = 0
            while True:
                if project.stop_requested:
                    client.interrupt(sid)
                    yield handle_stop()
                    return
                # Poll OpenCode for turn status + new messages. A transient slow/unresponsive OpenCode
                # (e.g. CPU-bound serializing a huge context) must NOT hard-crash the build: a single
                # is_running/messages ReadTimeout used to escape and kill the whole SSE. Tolerate it —
                # assume still running and retry — and give up only after a sustained outage.
                try:
                    running = client.is_running(sid)
                    msgs = client.messages(sid)
                    poll_failures = 0
                except httpx.HTTPError as e:
                    poll_failures += 1
                    log.warning("opencode poll failed (%d/%d): %s", poll_failures, _MAX_POLL_FAILURES, e)
                    if poll_failures >= _MAX_POLL_FAILURES:
                        restore_mode()
                        yield persist({"type": "error", "message": (
                            "OpenCode stopped responding, so the build was halted. Try again — if it "
                            "keeps happening, the request may be pulling too much data into context.")})
                        yield persist({"type": "done", "ok": False, "decision": "opencode unresponsive"})
                        return
                    time.sleep(2.0)
                    continue
                appeared = appeared or running
                for m in msgs:
                    if m.get("type") != "assistant":
                        continue
                    for i, part in enumerate(m.get("content", [])):
                        key = _part_key(m, i, part)
                        if key in seen:
                            continue
                        pt = part.get("type", "")
                        if "tool" in pt:
                            # Wait until the call finishes before emitting the card: a tool's args
                            # stream in, so at first sight a large input (e.g. todowrite's `todos`) is
                            # still empty -> "0 steps". Don't mark it seen while in-progress; re-check
                            # next poll and emit once the completed state carries the full input.
                            status = (part.get("state") or {}).get("status")
                            tool = part.get("tool") or part.get("name") or pt
                            if status in ("pending", "running", "in_progress"):
                                # Live "active" hint so a long step names what it's doing instead of
                                # dead air. Only for tools whose streaming input already carries a
                                # useful detail (a file path, a command); this deliberately skips
                                # todowrite so the "0 steps" artifact never surfaces.
                                detail = _tool_detail(tool, part) if tool in ("edit", "write", "read", "bash", "grep") else ""
                                if detail:
                                    sig = f"{tool}:{detail}"
                                    if sig != last_active:
                                        last_active = sig
                                        # Also log it: if a tool stalls (e.g. read of a /mnt/data mount),
                                        # /api/diag's log ring shows exactly which tool is stuck.
                                        log.info("active tool: %s %s", tool, detail)
                                        if tool == "bash" and (pkgs := _install_attempt(detail)):
                                            # The data behind "curated stack, revisit after real
                                            # usage": what agents reach for and can't find baked in.
                                            # Also the warning that this turn may be about to lose
                                            # its node_modules (see link_warm_deps).
                                            log.warning("dependency wanted: %s — via `%s`",
                                                        ", ".join(pkgs), detail)
                                        yield {"type": "active", "tool": tool, "detail": detail}
                                continue
                            seen.add(key)
                            last_active = None  # completed: let the next running tool re-announce
                            log.info("tool done: %s %s", tool, _tool_detail(tool, part))
                            if tool in ("edit", "write"):
                                made_edits = True
                            yield persist({"type": "agent", "kind": "tool", "tool": tool, "detail": _tool_detail(tool, part)})
                        elif pt == "text" and part.get("text"):
                            seen.add(key)
                            # Second line of defence behind _part_key: parts with no id still key on a
                            # shifting index, and a model that restates itself verbatim produces a
                            # genuinely distinct part. Either way the same paragraph twice in the
                            # transcript is never what the user should read, so drop the repeat.
                            if part["text"].strip() in emitted_text:
                                continue
                            emitted_text.add(part["text"].strip())
                            if gate:
                                # Gate turns render this text once, in the plan card below — don't also
                                # stream it live, or the user sees the same prose twice (loose text + card).
                                plan_text_parts.append(part["text"])
                            else:
                                yield persist({"type": "agent", "kind": "text", "text": part["text"]})
                cur_phase = project.control.snapshot().phase.value
                if cur_phase != last_phase:
                    last_phase = cur_phase
                    yield {"type": "phase", "phase": cur_phase}
                if project.last_gateway_error is not None:
                    break
                if appeared and not running:
                    break
                if not appeared and time.monotonic() - start > 12:
                    break
                time.sleep(1.0)

            if project.last_gateway_error is not None:
                err = project.last_gateway_error
                restore_mode()
                yield persist({"type": "error", "message": f"model call failed: {err['message']}"})
                yield persist({"type": "done", "ok": False, "decision": "gateway error"})
                return

            # Answer-only turn (Ask mode, or any question in Auto): it answered read-only and changed
            # nothing, so there's nothing to typecheck, build, or nudge — finish
            # clean regardless of the workspace's pre-existing typecheck state. If read-only was somehow
            # bypassed and it DID edit, fall through to the normal path so those edits get reverted
            # (the gate/answer-only violation check below), never silently kept.
            if answer_only and not agent_wrote():
                restore_mode()
                yield persist({"type": "done", "ok": True, "decision": "answered"})
                return

            # Gated (plan) turn that wrote nothing — the designed outcome. Resolve the gate HERE,
            # ahead of the typecheck, for three reasons. Running tsc over a tree the turn never
            # touched is pure dead time (10-30s of the user staring at a spinner for a plan). Its
            # "Typecheck passed" line lands under the plan and reads as though Sage already built
            # and verified the app. And the old placement nested this inside the circuit breaker's
            # `stop` branch, so a workspace carrying pre-existing type errors sent a plan turn into
            # the fix-it nudge loop instead of proposing its plan. A gated turn that DID write falls
            # through to the violation check below and is reverted.
            if gate and not agent_wrote():
                plan_md = _tidy_plan("\n".join(plan_text_parts))
                restore_mode()
                # A weak planner (notably the small sovereign models a sensitivity lock forces) can
                # finish this read-only turn without emitting any plan text — leaving nothing to
                # approve. Don't persist a blank plan or present an approve card that would build
                # from an empty plan; report it as a failed planning turn, with the same diagnostics
                # a stalled build gets, since "no plan text" is usually "no inference reached us".
                if not plan_md:
                    log.warning("%s gate produced no text (model_calls=%d) — reporting empty plan",
                                "architecture" if arch else "plan", project.model_calls)
                    yield {"type": "turn-summary", "model_calls": project.model_calls,
                           "tool_call_responses": project.tool_call_responses, "wrote_code": False,
                           "shim_bypassed": (project.model_calls == 0 and base_port is not None
                                             and base_port != control_port),
                           "base_port": base_port, "control_port": control_port,
                           "vendor_keys": vendor_keys, "gate": True, "read_only": read_only}
                    if project.model_calls == 0:
                        tail = self._opencode_log_tail()
                        if tail:
                            yield {"type": "opencode-log", "lines": tail}
                    yield persist({"type": "error", "message": (
                        "Describing the architecture didn't produce anything this time. Send the "
                        "request again — naming the parts you care about can help."
                        if arch else
                        "Planning didn't produce a plan this time. Send the request again — adding "
                        "a bit more detail about what you want can help — or switch to Implement to "
                        "build it directly.")})
                    yield persist({"type": "done", "ok": False, "decision": "empty plan"})
                    return
                # An architecture is a reference document, not the one-shot plan→implement handoff, so
                # it goes to its own file: .sage/plan.md is archived the moment a build consumes it
                # (see archive_plan), and a design the user wants to keep reading must not vanish
                # because they later approved a build from it.
                if arch:
                    project.workspace.write_architecture(plan_md)
                else:
                    project.workspace.write_plan(plan_md)
                # `steps` is how the card says "Approve & build (6 phases)" — and, more usefully,
                # it's the user's chance to see BEFORE approving that a phased plan actually parsed.
                # A plan the parser can't read still builds, just in one context.
                steps = len(parse_steps(plan_md)) if (phased_build and not arch) else 0
                yield persist({"type": "plan-proposed", "plan": plan_md,
                               "kind": "architecture" if arch else "plan",
                               "steps": steps if steps >= MIN_STEPS else 0})
                yield persist({"type": "done", "ok": True,
                               "decision": "architecture ready" if arch else "awaiting approval"})
                return

            yield {"type": "typecheck-start"}
            report = self._feedback.check(project.workspace.path)
            yield persist({"type": "typecheck", "ok": report.ok, "errors": len(report.errors), "message": report.as_agent_message()})
            if project.stop_requested:
                yield handle_stop()
                return
            decision = breaker.record(report.signature(), report.ok)
            if decision.action == "stop":
                # A clean typecheck with no edits means the agent only planned — don't call that a
                # finished build. Nudge it to implement (once); if it still writes nothing, stop
                # with an honest, actionable message rather than a false "done — clean".
                # `made_edits` only trips on tools literally named edit/write this turn; the agent may write
                # via another (patch/str_replace/create). Confirm against the snapshot's ground truth
                # so a real edit is never misread as "planned but wrote no code". Compare the tree hash
                # to this turn's start (not the build-start baseline) so only edits made THIS turn count.
                wrote_code = agent_wrote()
                # Surface why a turn landed where it did — especially a no-edit turn. Reads apart the
                # three failure modes (see Project.model_calls); rendered as a status line in the UI.
                shim_bypassed = (project.model_calls == 0 and base_port is not None and base_port != control_port)
                yield {"type": "turn-summary", "model_calls": project.model_calls,
                       "tool_call_responses": project.tool_call_responses, "wrote_code": wrote_code,
                       "shim_bypassed": shim_bypassed, "base_port": base_port, "control_port": control_port,
                       "vendor_keys": vendor_keys, "gate": gate, "read_only": read_only}
                # No inference reached the shim this turn: surface OpenCode's own log tail so its actual
                # error (which port it dialed, provider/model/auth failure) is visible without a shell.
                if project.model_calls == 0:
                    tail = self._opencode_log_tail()
                    if tail:
                        yield {"type": "opencode-log", "lines": tail}
                # A gated turn that wrote code broke the guarantee it exists to provide: the user was
                # promised a plan to approve and got an unreviewed build instead. Don't fall through
                # to the ordinary build path (that's what silently swallowed the gate before the shim
                # enforced read-only) — revert to the pre-turn tree and say so.
                if (gate or answer_only) and wrote_code:
                    kind = "gated" if gate else "answer-only"
                    log.error("%s turn wrote code — reverting; read-only enforcement was bypassed", kind)
                    project.snapshot.discard_changes()
                    restore_mode()
                    msg = ("Planning was expected, but the agent edited files — nothing was applied. "
                           "Send the request again, or switch to Implement to build directly." if gate else
                           "That was a question, but the agent edited files — nothing was applied. Ask "
                           "again, or switch to Implement to build directly.")
                    yield persist({"type": "error", "message": msg})
                    yield persist({"type": "done", "ok": False,
                                   "decision": "gate violated" if gate else "answer only — edits discarded"})
                    return
                if report.ok and not wrote_code:
                    # Neither gated nor answer-only turns reach here — a no-edit plan turn resolved
                    # its gate, and a no-edit Q&A finished, before the typecheck ran.
                    if nudges < MAX_NUDGES:
                        nudges += 1
                        # The nudge is a fresh user turn, so the shim's per-step classifier resets to
                        # PLAN (it biases plan until the first write) — in Auto the model can just plan
                        # again and stall. Pin Implement for the retry so it actually writes: the "try
                        # Implement mode" advice, applied automatically instead of shown as a dead end.
                        mode_now = project.control.snapshot().mode
                        if mode_now is Mode.AUTO:
                            project.control.set_turn_mode(Mode.IMPLEMENT)
                            reason = "planned but wrote no code — switching to Implement"
                        else:
                            reason = "wrote no code — retrying"
                        # Whether we just switched out of Auto or the user is already in Implement,
                        # resolve() routes to catalog.implement — the cheap coder that just wrote
                        # nothing. With the fallback on, pin the strong plan-tier model for the retry
                        # so a model capable of calling the edit tool drives it. Restored to the user's
                        # own pick in restore_mode(); no-op under a sensitivity lock (sovereign forced).
                        if strong_fallback and not escalated_pick and mode_now in (Mode.AUTO, Mode.IMPLEMENT):
                            project.control.pick(project.shim.catalog.plan)
                            escalated_pick = True
                            reason += " with the strong model"
                        yield {"type": "iterate", "reason": reason}
                        current = IMPLEMENT_NUDGE
                        continue
                    restore_mode()
                    # "couldn't get past planning" only makes sense when the user let us plan (Auto/
                    # Plan). In explicit Implement mode the model simply replied without editing — say
                    # that instead, so the message doesn't contradict the mode the user picked.
                    stop_msg = ("the model replied but didn't change any files — try rephrasing or a smaller step"
                                if mode_at_start is Mode.IMPLEMENT
                                else "couldn't get past planning — try rephrasing or a smaller step")
                    yield persist({"type": "done", "ok": False, "decision": stop_msg})
                    return
                # Typecheck is clean and code was written — but tsc can't see a runtime crash that
                # blanks the preview. Wait briefly for the open preview to report one; if it does,
                # feed the error back so the agent fixes it before we call the build done.
                if report.ok and wrote_code and runtime_fixes < MAX_RUNTIME_FIXES:
                    yield {"type": "runtime-check"}
                    rt = self._await_runtime_error(project, since=send_ts)
                    if rt is not None:
                        runtime_fixes += 1
                        project.runtime_error = None  # consume so a later turn starts clean
                        first_line = (rt.get("message") or "runtime error").splitlines()[0][:140]
                        yield {"type": "iterate", "reason": f"app crashed at runtime — fixing ({first_line})"}
                        current = RUNTIME_FIX_NUDGE.format(message=rt.get("message", ""), stack=rt.get("stack", ""))
                        continue
                # The agent may have copied attached data into src/ — that leaks it into git
                # (public/data/ is gitignored on purpose) and is why deleting the attachment leaves the
                # dashboard still working. Treat it like a build error: nudge the agent to remove the
                # copy and fetch from data/ instead, bounded. If it won't, _save_to_git strips the copy
                # from the commit anyway (the bytes never reach git), so this loop is UX, not the guard.
                if report.ok and wrote_code and leak_fixes < MAX_LEAK_FIXES:
                    leaks = self._detect_leaks(project)
                    if leaks:
                        leak_fixes += 1
                        for name, where in leaks:
                            yield persist({"type": "data-leak", "file": name, "where": where[:3]})
                        yield {"type": "iterate", "reason": "copied attached data into source — moving it back to data/"}
                        current = LEAK_FIX_NUDGE
                        continue
                restore_mode()
                yield persist({"type": "done", "ok": report.ok, "decision": decision.reason})
                if report.ok and owns_turn:
                    # A clean code-writing build succeeded (a no-edit plan/answer turn returned earlier),
                    # so this project is now "built" — future turns gate on plan, not on this being done.
                    #
                    # A phase does neither: one approved plan is one commit, not six, and a build that
                    # dies at phase 4 must not have been marked "built" by phase 1.
                    project.workspace.mark_built()
                    saved = self._save_to_git(project, prompt)
                    if saved is not None:
                        yield persist(saved)
                return
            yield {"type": "iterate", "reason": decision.reason}
            current = report.as_agent_message()

    def approve_stream(self, answers: str = "", plan_edits: str | None = None):
        """Approve a gated plan and build it (SPEC P6). Feeds the approved plan into a normal
        build turn as context, then archives the plan so no live .sage/plan.md is left for a later
        turn to misread. Approval means "build it now", so if the user is in Plan or Ask mode we run
        this turn pinned to Implement, leaving their own mode alone. Both are read-only: Plan's gate
        would just re-plan, and Ask has every write and shell tool stripped from the request by the
        shim, so the agent would emit edits that never land on disk. An Auto/Implement approve already
        has history, so it's never re-gated regardless."""
        # Serialize like build_stream: approving while a turn already streams would overlap two turns
        # on one working tree and read-only gate. We hold the lock across the whole approve (plan
        # write + mode swap + build) and call _build_stream directly so it doesn't re-acquire.
        if not self._turn_lock.acquire(blocking=False):
            yield from self._busy_refusal()
            return
        try:
            yield from self._approve_locked(answers, plan_edits)
        finally:
            self._clear_turn_baseline()
            self._turn_lock.release()

    def _approve_locked(self, answers: str = "", plan_edits: str | None = None, user_text: str | None = None):
        """The approve turn itself. Assumes the caller holds _turn_lock — approval reaches here both
        from the card's Approve button and from a bare approval typed in the composer (build_stream)."""
        project = self.project()
        if plan_edits is not None:
            project.workspace.write_plan(plan_edits)
        # Fall back to the architecture when no plan is live: an architecture turn writes only
        # .sage/architecture.md (it isn't a one-shot handoff and must survive the build), so its card's
        # Build button would otherwise approve an empty plan and build nothing.
        plan_md = project.workspace.read_plan() or project.workspace.read_architecture() or ""
        prior_mode = project.control.snapshot().mode
        # Approval means "build it now", so a turn approved from a read-only mode RUNS as Implement —
        # pinned to this turn only (see arm_turn_mode), never written to the user's picker. The
        # earlier set_mode-then-restore did move their picker, which meant a mode they changed while
        # the build streamed was reverted underneath them when it finished.
        run_as = Mode.IMPLEMENT if prior_mode in (Mode.PLAN, Mode.ASK) else None
        # Phased only when the toggle is on AND the plan actually parsed into briefs. A plan written
        # before the toggle (or by a planner that ignored the format) builds the ordinary way rather
        # than half-phasing, which would be worse than not phasing at all.
        phased = bool(project.workspace.read_settings().get("phased_build")) and is_phasable(plan_md)
        try:
            if phased:
                yield from self._phased_approve(project, plan_md, answers, user_text)
            else:
                yield from self._build_stream(_approve_prompt(plan_md, answers), is_approval=True,
                                              user_text=user_text, mode=run_as)
            # Approving from Ask mode builds (that's deliberate — the user asked for this plan), but
            # the mode goes straight back to Ask below. The user has just watched Ask write an app, so
            # the next change they type reasonably looks like it will build too, and instead runs
            # read-only and writes nothing. Say so here, where it lands right under the build.
            if prior_mode is Mode.ASK:
                ev = {"type": "ask-active",
                      "message": "Approving built this plan, but the mode is still Ask — it answers "
                                 "questions and never changes files. Switch to Auto or Implement "
                                 "before asking for your next change."}
                project.workspace.append_history(ev)
                yield ev
        finally:
            # One-shot handoff: consumed, so move it out of the agent's live view (git keeps history).
            project.workspace.archive_plan()

    def _phased_approve(self, project: Project, plan_md: str, answers: str, user_text: str | None):
        """Build an approved plan one step at a time, each in a FRESH OpenCode session.

        The point is context, not parallelism: a cheap coder holds up in a clean 8k window and comes
        apart at 100k, so every phase starts from nothing but its own brief. That costs a session
        bootstrap per phase (OpenCode re-reads AGENTS.md and project context), which is exactly what
        the `sage-session` cost tag makes measurable — the tax is the thing this feature has to earn.

        Owns everything a turn owns and a phase doesn't: the user's chat bubble, the whole-build
        revert point, the failure flag, the git commit, and the single terminal `done`.
        """
        import time

        client = self._ensure_opencode()
        steps = parse_steps(plan_md)

        def persist(ev: dict) -> dict:
            if ev["type"] in _PERSISTED_EVENTS:
                project.workspace.append_history(ev)
            return ev

        # Same ordering rule as _build_stream: refresh the archive before the revert point below.
        project.workspace.render_history_md()
        project.workspace.append_history(
            {"type": "user", "text": user_text if user_text is not None else "Approved the plan."})
        # ONE revert point for the whole build. _build_stream still checkpoints per phase (which is
        # what gives a gate violation its correct, narrow scope), so undoing everything needs a ref
        # that reaches back past all of them — hence discard_to rather than discard_changes.
        base = project.snapshot.commit_before_turn()
        history_baseline = project.workspace.history_len()
        # Per-phase circuit breakers bound each phase, not the build: 6 × (15 iterations, 600s) is an
        # hour of wall clock that nobody asked for.
        deadline = time.monotonic() + _env_int("SAGE_PHASED_MAX_SECONDS", 1800)

        yield persist({"type": "build-plan",
                       "steps": [{"n": s.n, "label": s.label, "files": s.files} for s in steps]})

        failed: tuple[PlanStep, str] | None = None
        notes: list[str] = []  # each finished phase's closing summary, handed to the ones after it
        for step in steps:
            yield persist({"type": "step-start", "n": step.n, "total": len(steps),
                           "label": step.label, "files": step.files})
            outcome = yield from self._run_step(project, client, step, steps, answers, notes)
            if outcome == "stopped":
                # The user rejected the whole build, so leaving three of six phases on disk would
                # leave a state they never asked for and can't describe. Same semantics as Stop on a
                # normal turn: the turn vanishes, files and transcript both.
                project.snapshot.discard_to(base)
                project.workspace.truncate_history(history_baseline)
                yield {"type": "stopped"}
                return
            if outcome is not True:
                failed = (step, str(outcome))
                yield persist({"type": "step-done", "n": step.n, "total": len(steps),
                               "ok": False, "decision": str(outcome)})
                break
            yield persist({"type": "step-done", "n": step.n, "total": len(steps), "ok": True})
            if time.monotonic() > deadline:
                failed = (step, "the build ran out of time")
                break

        if failed is not None:
            step, why = failed
            # Deliberately NOT reverted. A failure is Sage's problem to recover from, and the
            # finished phases are real progress the user can see and build on — throwing away forty
            # minutes of good work because step 4 of 6 broke is the worst available behaviour. What
            # we do instead is skip the commit (the durable record stays clean) and flag the failure,
            # which makes the next turn plan first via the existing failure-replan gate.
            project.workspace.set_last_turn_failed(True)
            yield persist({"type": "done", "ok": False,
                           "decision": f"phase {step.n} of {len(steps)} failed — {why}"})
            return

        project.workspace.set_last_turn_failed(False)
        project.workspace.mark_built()
        yield persist({"type": "done", "ok": True, "decision": "typecheck clean"})
        saved = self._save_to_git(project, f"build plan ({len(steps)} phases)")
        if saved is not None:
            yield persist(saved)

    def _run_step(self, project: Project, client: OpenCodeClient, step: PlanStep,
                  steps: list[PlanStep], answers: str, notes: list[str] | None = None):
        """Run one phase, retrying once in another fresh session if it fails. Returns True, the
        string "stopped", or a failure reason; swallows the phase's own terminal `done` so the build
        emits exactly one."""
        strong_retry = os.environ.get("SAGE_PHASE_RETRY_STRONG", "1").strip().lower() not in ("0", "false", "no")
        original_pick = project.control.snapshot().picked_model
        escalated = False
        errors = ""
        reason = "the step did not complete"

        try:
            for attempt in (1, 2):
                if attempt == 2 and strong_retry:
                    # The cheap coder just demonstrated it can't do this step. Paying for one strong
                    # attempt beats failing the whole build, and the pick is restored below so the
                    # escalation lasts exactly one phase.
                    project.control.pick(project.shim.catalog.plan)
                    escalated = True
                    yield {"type": "active", "tool": "retry",
                           "detail": f"phase {step.n} failed — retrying on {project.shim.catalog.plan}"}
                # A brand-new session even on the retry: the first attempt's failure is now sitting in
                # that session's context, and it's the most misleading thing a retry could read.
                sid = client.create_session(directory=str(project.workspace.path))
                # Every phase is pinned to Implement, never the user's mode. A phase begins with a
                # fresh user message, so the per-inference classifier would read PLAN and route the
                # expensive plan-tier model on every phase — N times the cost this feature exists to
                # avoid, and N chances to stall having written nothing.
                outcome: dict | None = None
                summary = ""
                for ev in self._build_stream(_phase_prompt(step, steps, answers, notes, errors),
                                             is_approval=True, mode=Mode.IMPLEMENT,
                                             session_id=sid, brief=step):
                    if ev["type"] == "stopped":
                        return "stopped"
                    if ev["type"] == "done":
                        outcome = ev  # swallowed: one `done` per build, not one per phase
                        continue
                    if ev["type"] == "agent" and ev.get("kind") == "text" and ev.get("text"):
                        summary = str(ev["text"])  # last one wins: the agent's closing summary
                    if ev["type"] == "typecheck" and not ev.get("ok"):
                        # Carried into the retry's brief so the next attempt starts from the actual
                        # errors rather than rediscovering them.
                        errors = str(ev.get("message") or "")[:4000]
                    yield ev
                if outcome is not None and outcome.get("ok"):
                    if notes is not None and summary:
                        notes.append(f"{step.n}. {step.label} — {_phase_note(summary)}")
                    return True
                if outcome is not None:
                    reason = str(outcome.get("decision") or reason)
        finally:
            if escalated:
                project.control.pick(original_pick)
        return reason

    def record_runtime_error(self, message: str, stack: str = "") -> None:
        """Store a runtime error the live preview reported (via /api/preview/runtime-error), stamped
        so build_stream can tell this turn's crash from a stale one. Best-effort: a report that
        arrives with no active project is simply dropped."""
        import time

        if self._project is None:
            return
        self._project.runtime_error = {"message": message, "stack": stack, "ts": time.monotonic()}

    def _await_runtime_error(self, project: Project, since: float, timeout: float = 4.0) -> dict | None:
        """Poll up to `timeout`s for a preview-reported runtime error newer than `since` (this turn's
        send time). Returns it, or None if the preview stays clean. The HMR update -> re-render ->
        report round-trip usually lands within a second or two of the agent's last write."""
        import time

        deadline = time.monotonic() + timeout
        while True:
            rt = project.runtime_error
            if rt is not None and rt.get("ts", 0.0) >= since:
                return rt
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.5)

    def _save_to_git(self, project: Project, prompt: str) -> dict | None:
        """Commit + push the workspace after a clean build so the app and .sage/ transcript are
        durable. Returns None when the workspace isn't the root of its own git repo (local dev / the
        /tmp spike — no save line to show); otherwise a `saved` event. Never raises into the build."""
        from ..workspace import git

        path = project.workspace.path
        if not git.is_repo_root(path):
            return None
        message = f"sage: {prompt.splitlines()[0][:72]}" if prompt.strip() else "sage: build"
        # Hard backstop: never stage attached-data copies, even if the agent ignored the fix nudge.
        leaked = self._leaked_copy_paths(project)
        try:
            committed = git.commit_all(path, message, exclude=leaked)
            # Integrate any teammate changes before pushing, or the push is rejected as non-ff and
            # the build's work silently never reaches the repo.
            synced = self._integrate_remote(project)
            if synced is not None and synced.status in ("conflict-unresolved", "error"):
                return {"type": "saved", "ok": False, "pushed": False,
                        "detail": f"couldn't sync with the repo — {synced.detail}"}
            if not committed and (synced is None or synced.status == "up-to-date"):
                return {"type": "saved", "ok": True, "pushed": False, "detail": "no changes to commit"}
            result = git.push(path)
            detail = result.detail
            if leaked:
                detail += f" — kept {len(leaked)} copied data file(s) out of git; fetch attached data from data/ instead"
            return {"type": "saved", "ok": True, "pushed": result.pushed, "detail": detail}
        except Exception as e:
            log.exception("git save failed")
            return {"type": "saved", "ok": False, "pushed": False, "detail": f"{type(e).__name__}: {e}"}

    def _integrate_remote(self, project: Project):
        """Pull the remote into the (already-committed, clean) workspace, resolving merge conflicts
        with the agent. Returns a git.SyncResult, or None when there's no remote to pull from."""
        from ..workspace import git

        path = project.workspace.path
        if not git.has_remote(path):
            return None
        result = git.pull(path)
        if result.status == "conflict":
            result = self._resolve_conflicts(project, result.conflicts)
        return result

    def _resolve_conflicts(self, project: Project, conflicts: list[str]):
        """Hand the conflicted files to the agent to resolve the markers, then commit the merge.
        Rolls the merge back (leaving the pre-pull state) if the agent leaves markers or errors."""
        from ..workspace import git

        path = project.workspace.path
        client = self._ensure_opencode()
        sid = self._ensure_session(project)
        files = "\n".join(f"- {c}" for c in conflicts)
        prompt = (
            "A `git pull` brought in changes from a teammate that conflict with the current code. "
            f"These files have unresolved merge conflicts:\n{files}\n\n"
            "For each file, resolve every conflict: reconcile the code between the `<<<<<<<`, "
            "`=======`, and `>>>>>>>` markers so both sides' intent is kept where possible, then "
            "delete all three markers. Leave no conflict markers behind, and edit only the files "
            "listed above."
        )
        project.last_gateway_error = None
        client.send_prompt(sid, prompt)
        client.wait_for_idle(sid)
        if project.last_gateway_error is not None:
            git.abort_merge(path)
            return git.SyncResult("error", conflicts, f"model call failed: {project.last_gateway_error['message']}")
        remaining = git.files_with_conflict_markers(path, conflicts)
        if remaining:
            git.abort_merge(path)
            return git.SyncResult("conflict-unresolved", remaining,
                                  f"conflicts remain in {', '.join(remaining)} — pull was rolled back")
        git.finalize_merge(path, "sage: merge remote changes")
        return git.SyncResult("merged", conflicts, "merged teammate changes (conflicts resolved)")

    def sync(self) -> dict:
        """Manual "Pull latest": commit in-progress edits, pull + agent-resolve teammate changes,
        then push the result so the repo and workspace agree. Returns a UI result dict."""
        from ..workspace import git

        project = self.project()
        path = project.workspace.path
        if not git.is_repo_root(path) or not git.has_remote(path):
            return {"status": "no-remote", "conflicts": [], "pushed": False,
                    "detail": "this app has no git remote to pull from"}
        try:
            git.commit_all(path, "sage: save before pull", exclude=self._leaked_copy_paths(project))
            result = self._integrate_remote(project)
            if result is None or result.status in ("conflict-unresolved", "error"):
                detail = result.detail if result else "no remote to pull from"
                return {"status": result.status if result else "no-remote",
                        "conflicts": result.conflicts if result else [], "pushed": False, "detail": detail}
            pushed = git.push(path)
            return {"status": result.status, "conflicts": result.conflicts,
                    "pushed": pushed.pushed, "detail": result.detail}
        except Exception as e:
            log.exception("sync failed")
            return {"status": "error", "conflicts": [], "pushed": False, "detail": f"{type(e).__name__}: {e}"}

    def publish(self) -> dict:
        """Publish (or republish) THIS app's project as a live Domino App, deploying the latest
        committed code on the default branch. Mirrors HubService.publish_app: an existing App gets a
        new version (stable URL); otherwise a new App is created + launched. Best-effort saves the
        current work first so the deploy ships the newest code. Returns {published, app_id, url,
        manage_url, republished}."""
        if self._control_plane is None or not self._domino_project_id:
            raise RuntimeError(
                "Publish is only available when this builder runs on Domino (missing control-plane "
                "or DOMINO_PROJECT_ID)."
            )
        project = self.project()
        # Ship the CURRENT entry script. app.sh is committed to the app's repo at seed time, so an
        # app created from an older image would otherwise redeploy its original copy forever — and
        # keep hitting bugs fixed since (the Node-18 PATH order that crash-looped every build).
        # Best-effort: a refresh failure must not block publishing the committed copy.
        try:
            if self._wm.refresh_entry_script():
                log.info("publish: refreshed the deploy files from the template")
        except Exception:
            log.exception("publish: couldn't refresh the deploy files; publishing the committed copies")
        # The builder holds the working tree, so a fast local check beats the hub's GitHub-API probe.
        entry = project.workspace.path / _ENTRY_POINT
        if not entry.exists():
            raise RuntimeError(
                f"'{_ENTRY_POINT}' is missing from the workspace, so Domino has no entry script to "
                f"run. Add {_ENTRY_POINT} to the project root and rebuild, then publish again."
            )
        # The refresh above is best-effort, so app.sh can be the current one while the server it execs
        # is absent — a deploy that reports success and then crash-loops on "can't open file
        # 'serve.py'". Ask what THIS app.sh needs rather than demanding serve.py of an older app whose
        # entry script still serves the build with Node.
        if _SERVER_SCRIPT in entry.read_text() and not (project.workspace.path / _SERVER_SCRIPT).exists():
            raise RuntimeError(
                f"'{_SERVER_SCRIPT}' is missing from the workspace, but {_ENTRY_POINT} runs it to serve "
                f"the app, so the deploy would start and immediately fail. Restore {_SERVER_SCRIPT} to "
                "the project root and publish again."
            )
        # Deploy the newest code: commit + push before publishing. Best-effort — a save failure (no
        # remote, offline) must not block a publish of whatever is already committed.
        try:
            self._save_to_git(project, "save before publish")
        except Exception:
            log.exception("publish: pre-publish save failed; publishing the last committed code")

        cp = self._control_plane
        pid = self._domino_project_id
        name = self._domino_project_name or self._project_id
        existing = cp.find_project_app(pid)
        if existing and existing.id:  # already published — ship a new version, keep the URL
            app = cp.republish_app(existing.id)
            out = {"published": True, "app_id": app.id, "url": app.url or existing.url, "republished": True}
        else:
            app = cp.publish_app(pid, name=name)
            out = {"published": True, "app_id": app.id, "url": app.url, "republished": False}
        out["manage_url"] = cp.app_manage_url(app.id, name)
        return out

    def publish_status(self, app_id: str) -> dict:
        """Deploy status of a published app so the UI can poll after Publish. Maps the raw instance
        status to a phase: running (live) / failed / pending (still deploying)."""
        if self._control_plane is None:
            raise RuntimeError("Publish status is only available when this builder runs on Domino.")
        raw = self._control_plane.app_status(app_id)
        s = raw.lower()
        if s in _RUNNING_STATES:
            phase = "running"
        elif s in _FAILED_STATES:
            phase = "failed"
        else:
            phase = "pending"
        return {"app_id": app_id, "status": raw, "phase": phase}

    def stop(self) -> dict:
        """Stop THIS builder's workspace so it stops consuming compute. Saves in-progress work first
        (commit + pull/resolve + push), then stops the workspace if the workspace id is known.
        Returns {saved, stopped, workspace_id, detail}."""
        project = self.project()
        saved = self._save_to_git(project, "save before stop")
        saved_ok = saved is None or bool(saved.get("ok"))
        wid = self._resolve_workspace_id()
        if self._control_plane is None or not self._domino_project_id or not wid:
            # Off-Domino, or the workspace id wasn't discoverable from the env — we can't stop the
            # workspace ourselves, so at least the work is saved. Report clearly.
            return {"saved": saved_ok, "stopped": False, "workspace_id": wid,
                    "detail": "workspace id unavailable — saved work, but couldn't stop the workspace"}
        self._control_plane.stop_workspace(self._domino_project_id, wid)
        return {"saved": saved_ok, "stopped": True, "workspace_id": wid, "detail": "stopping workspace"}

    def _resolve_workspace_id(self) -> str | None:
        """This builder's own workspace id, needed to stop it. Prefer the explicit override; else map
        DOMINO_RUN_ID (the session executionId) to its workspace by scanning the project's workspaces
        for a matching mostRecentSession.executionId. Returns None when it can't be determined."""
        if self._workspace_id:
            return self._workspace_id
        if self._control_plane is None or not self._domino_project_id or not self._domino_run_id:
            return None
        try:
            workspaces = self._control_plane.list_workspaces(self._domino_project_id)
        except Exception:
            log.exception("stop: couldn't list workspaces to resolve this workspace's id")
            return None
        for ws in workspaces:
            if not isinstance(ws, dict):
                continue
            session = ws.get("mostRecentSession") or {}
            exec_id = session.get("executionId") or session.get("id") if isinstance(session, dict) else None
            if exec_id and str(exec_id) == str(self._domino_run_id) and ws.get("id"):
                self._workspace_id = str(ws["id"])  # cache for a subsequent call
                return self._workspace_id
        return None

    def stop_build(self) -> None:
        """Interrupt the in-flight build_stream() turn; it reverts files/history and stops."""
        project = self.project()
        project.stop_requested = True
        # The LIVE session, not the project's: during a phased build the project session is idle and
        # interrupting it would leave the running phase generating for another poll cycle or more,
        # which reads as a Stop button that doesn't work.
        sid = project.active_session_id or project.session_id
        if sid and self._oc_client is not None:
            try:
                self._oc_client.interrupt(sid)
            except httpx.HTTPError:
                pass

    def _effective_catalog(self, workspace: Workspace) -> ModelCatalog:
        overrides = workspace.read_catalog_overrides()
        return replace(self._catalog, **overrides) if overrides else self._catalog

    def set_catalog(self, **fields: str | None) -> ModelCatalog:
        """Override which model id fills a catalog slot (sovereign/plan/implement/default),
        persisted so it survives a restart. Only non-empty fields change; the rest keep their
        current value."""
        project = self.project()
        changes = {k: v for k, v in fields.items() if v}
        if not changes:
            return project.shim.catalog
        new_catalog = replace(project.shim.catalog, **changes)
        project.shim.set_catalog(new_catalog)
        overrides = project.workspace.read_catalog_overrides()
        overrides.update(changes)
        project.workspace.write_catalog_overrides(overrides)
        return new_catalog

    def history(self) -> list[dict]:
        """Reads straight from the workspace volume, so the transcript is available without
        starting the preview (attaching the project) — a plain GET must not spin up Vite."""
        return Workspace(self._project_id, self._wm.path).read_history()

    def list_assets(self) -> list[dict]:
        """Datasets mounted in this project (the ones whose files can actually be attached)."""
        return [
            {
                "id": a.id,
                "name": a.name,
                "tags": a.tags,
                "project": a.project,
                "sensitive": is_sensitive(a, self._sensitivity_tag),
                "writable": bool(a.mount_path and os.access(a.mount_path, os.W_OK)),
            }
            for a in self._assets.list_datasets(self._domino_project_id)
        ]

    def list_llm_aliases(self) -> list[dict]:
        """LLM Aliases this caller can actually call, shaped for the Resource Browser (#5).

        The provider has already intersected the accessible model ids with the registered aliases,
        so everything returned here is pickable — a row never needs a "you may not use this" state.
        """
        return [
            {
                "id": a.id,
                "name": a.name,
                "display_name": a.display_name,
                "description": a.description,
                "capabilities": a.capabilities,
                "costs": a.costs,
            }
            for a in self._resources.list_llm_aliases()
        ]

    def list_model_apis(self) -> list[dict]:
        """Model APIs deployed in THIS project, shaped for the Resource Browser (#8).

        Scoped to the project Sage is bound to, which is the only scope a normal user can ask for:
        the deployment-wide listing is an admin surface. `_domino_project_id` is the same id the
        dataset listing already runs on, so nothing new has to be configured for this to work.
        """
        return [
            {"id": m.id, "name": m.name, "description": m.description, "status": m.status}
            for m in self._resources.list_model_apis(self._domino_project_id)
        ]

    def list_data_sources(self) -> list[dict]:
        """Data Sources this caller has permission on, shaped for the Resource Browser (#10).

        Unscoped, unlike the two listings above, because a Data Source belongs to the person and not
        to the project: attaching one to a project is optional bookkeeping in Domino, and a listing
        keyed on it answered `200 []` live for a user with a working Snowflake source.

        The provider has already dropped connector kinds this panel cannot offer, so every row here
        is one a creator could go on to pick. `ready` is the only thing that varies: `False` means
        Domino says this caller cannot open it, and `None` means Domino would not say.
        """
        return [
            {
                "id": d.id,
                "name": d.name,
                "connector": d.connector,
                "credential_type": d.credential_type,
                "description": d.description,
                "ready": d.ready,
                # What the cascade can offer for this row (#11). `levels` is the panel's whole
                # instruction: three levels, two when the connector has nothing above a schema, and
                # none when Sage has no dialect for it — three different renderings that a connector
                # name could not be asked to imply. The defaults, when Domino's own config pins them,
                # are levels already answered and so levels not worth asking about.
                "levels": cascade_levels(d),
                "default_database": d.default_database,
                "default_schema": d.default_schema,
            }
            for d in self._resources.list_data_sources()
        ]

    # ---- What is inside one Data Source, a level at a time (#11) ----

    def _data_source(self, source_id: str) -> DataSource:
        """The row for one Data Source, resolved against the live listing.

        Resolved here rather than taken from the request, for the reason `bind_llm_alias` resolves an
        Alias against its listing: the listing is Domino's answer about what this caller may reach,
        and the name and connector type that come out of it are what the cascade builds SQL from. A
        request carrying its own connector type would be choosing which SQL Sage sends.

        The cost is one listing per level opened. That is deliberate rather than overlooked — the
        query behind a level takes seconds, so the listing beside it is noise, and caching the row
        would mean the cascade could keep walking into a Data Source Domino had stopped offering.
        """
        source = next((d for d in self._resources.list_data_sources() if d.id == source_id), None)
        if source is None:
            raise LookupError(source_id)
        return source

    def list_data_source_databases(self, source_id: str) -> list[str]:
        return self._resources.list_databases(self._data_source(source_id))

    def list_data_source_schemas(self, source_id: str, database: str) -> list[str]:
        return self._resources.list_schemas(self._data_source(source_id), _level(database))

    def list_data_source_tables(self, source_id: str, database: str, schema: str) -> list[str]:
        return self._resources.list_tables(
            self._data_source(source_id), _level(database), _level(schema))

    # ---- Bindings: the Resources this app is recorded as using (#6) ----

    def list_bindings(self) -> list[dict]:
        """Read from the manifest rather than the gateway, so this still answers "what does this app
        depend on" when the gateway is unreachable — which is when the question gets asked."""
        return [b.to_dict() for b in parse_bindings(self.project().workspace.read_bindings())]

    def bind_llm_alias(self, alias_id: str) -> list[dict]:
        """Record that this app uses one LLM Alias, and return the new Binding list.

        The Alias must be one the caller can actually call: `list_llm_aliases` has already
        intersected the grants, so anything absent from it is either not registered or not granted,
        and recording a dependency on it would be recording a build that cannot run. Binding twice
        is not an error — the second click just means the row is already in the group above.
        """
        alias = next((a for a in self.list_llm_aliases() if a["id"] == alias_id), None)
        if alias is None:
            raise LookupError(alias_id)
        return self._record(Binding(KIND_LLM_ALIAS, alias["id"], alias["name"], alias["display_name"]))

    def bind_model_api(self, model_api_id: str) -> list[dict]:
        """Record that this app uses one Model API, and return the new Binding list (#9).

        Validated against the project's own listing, for the reason the Alias is: a record naming
        something the creator cannot reach is a dependency on a call that cannot run. The listing is
        already scoped to this project and already permission-filtered by Domino, so anything absent
        from it is not this app's to depend on.

        A Model API has ONE name and no separate display name, so both fields carry it. That keeps
        the manifest one shape across kinds, and the row renders the name once rather than twice.

        Refuses without a stored credential, which is what makes a Model API Binding mean something
        an app can act on. Unlike an LLM Alias — where the viewer's own session is the credential —
        a Model API opens for nothing but its access token, so a Binding recorded without one would
        pin a model the app cannot call and report it as a dependency that works. The rail asks for
        the snippet before it gets here; this is the invariant behind that, not a second prompt.
        """
        api = next((m for m in self.list_model_apis() if m["id"] == model_api_id), None)
        if api is None:
            raise LookupError(model_api_id)
        if self._credentials(self.project()).get(model_api_id) is None:
            raise CredentialRequired(model_api_id)
        return self._record(Binding(KIND_MODEL_API, api["id"], api["name"], api["name"]))

    def bind_data_source(
        self, source_id: str, database: str = "", schema: str = "", table: str = "",
    ) -> list[dict]:
        """Record that the app uses one Data Source, and which part of it (#11).

        Validated against the project's own listing, as the two kinds above are. The scope is not
        validated against the store: proving a schema exists would cost two more queries and several
        more seconds, and these names did not come from a keyboard — they came from the cascade the
        creator has just walked, which is the listing.

        The scope may be empty at every level, and that is a record worth keeping rather than a
        half-finished one. A connector Sage has no dialect for cannot offer a scope at all, and "this
        app uses this Data Source" was already the whole of what a Binding meant in #6.

        Re-binding replaces in place, because `Binding.key` leaves the scope out. So changing the
        chosen schema is one more pass through the cascade, not a Remove and a re-pick.
        """
        source = self._data_source(source_id)
        # Charset-checked here as well as where the SQL is built. Not belt-and-braces for its own
        # sake: this is what keeps the manifest holding only names that can be sent, so the slice that
        # builds the app's query out of this record inherits the guarantee rather than re-earning it.
        parts = [safe_identifier(p) if p else None for p in (database, schema, table)]
        return self._record(Binding(KIND_DATA_SOURCE, source.id, source.name, source.name, *parts))

    # ---- Model access tokens, pasted once and remembered (#9) ----

    def _credentials(self, project: Project) -> CredentialStore:
        return CredentialStore(project.workspace.path)

    def model_api_credential_ids(self) -> list[str]:
        """Which Model APIs Sage already holds a token for. The rail reads this to know which Use
        buttons can act at once and which have to ask first."""
        return sorted(self._credentials(self.project()).ids())

    def save_model_api_credential(self, model_api_id: str, snippet: str) -> dict:
        """Take a pasted sample request, prove it opens the model, and remember it.

        Verified before it is stored, never after. An unverified token fails for the first person to
        open the published app — where there is no form, no Sage and nobody who can fix it — whereas
        a token checked here fails while the creator is still looking at the paste that produced it.

        The id in the pasted URL is checked against the Model API being bound. Two snippets look
        alike and a creator with several Overview tabs open can easily copy the wrong one; without
        this the app would call somebody else's model and report every mismatch as a bad body.
        """
        parsed = parse_snippet(snippet)
        if not parsed.complete:
            return {"ok": False, "error": parsed.missing()}
        if parsed.model_id and parsed.model_id != model_api_id:
            return {"ok": False, "error": (
                "That snippet is for a different Model API. Copy the sample request from the "
                "Overview page of the model you are adding."
            )}
        result = verify_credential(parsed.url, parsed.token)
        if not result.ok:
            return {"ok": False, "error": result.message, "detail": result.detail}
        self._credentials(self.project()).put(model_api_id, Credential(parsed.url, parsed.token))
        return {"ok": True, "url": parsed.url}

    def _record(self, new: Binding) -> list[dict]:
        """Write one Binding into the manifest, and re-derive what the app's source says about it."""
        def change(entries: list[dict]) -> list[dict]:
            # Re-binding replaces IN PLACE rather than moving to the end. Order decides which Alias
            # the app calls (#7), so appending an already-bound one would repin an app that already
            # works — from a call that was meant to be a no-op.
            current = parse_bindings(entries)
            if any(b.key == new.key for b in current):
                return [(new if b.key == new.key else b).to_dict() for b in current]
            return [b.to_dict() for b in [*current, new]]
        entries = self.project().workspace.update_bindings(change)
        self._write_app_resources(self.project())
        return entries

    def unbind(self, kind: str, resource_id: str) -> list[dict]:
        """Drop one Binding. Removing a record that is already gone is not an error: the creator
        wanted it gone, and it is."""
        def change(entries: list[dict]) -> list[dict]:
            return [b.to_dict() for b in parse_bindings(entries) if b.key != (kind, resource_id)]
        entries = self.project().workspace.update_bindings(change)
        self._write_app_resources(self.project())
        return entries

    # ---- Preflight: what a build will need, checked before it needs it (#17) ----

    def preflight_slots(self) -> dict:
        """Resolve every configured model slot against the gateway. One listing, at startup.

        The DEPLOYMENT catalog, not a project's: this is Sage checking its own configuration before
        any project is attached, so a maintainer learns about a missing Alias from a log line rather
        than from a user's failed build. A project's per-slot overrides are the user's own choice and
        are reported to them by the model panel, not here.

        A gateway that will not answer reports `unreachable`, not `problems`: we did not learn that
        a slot is broken, we learned that we could not check. Announcing the former on the strength
        of the latter would send a maintainer to edit a setting that was correct all along.
        """
        try:
            aliases = self._resources.list_llm_aliases()
        except ResourceUnavailable as e:
            return {"state": "unreachable", "error": str(e), "slots": []}
        problems = unresolved_slots(self._catalog, aliases)
        return {
            "state": "problems" if problems else "ok",
            "error": None,
            "slots": [p.to_dict() for p in problems],
        }

    def preflight_bindings(self) -> dict:
        """Check this project's recorded Bindings against the gateway. One listing, at session open.

        Reads the manifest directly rather than through `project()`, so this stays callable from
        inside the attach path without recursing through the memo it is being called from.
        """
        workspace = self._project.workspace if self._project is not None else Workspace(self._project_id, self._wm.path)
        recorded = parse_bindings(workspace.read_bindings())
        if not recorded:
            # Nothing to check, so nothing worth a gateway call: the overwhelmingly common case at
            # session open is an app with no Bindings at all, and that must cost nothing.
            return {"state": "ok", "error": None, "bindings": []}
        try:
            aliases = self._resources.list_llm_aliases()
        except ResourceUnavailable as e:
            return {"state": "unreachable", "error": str(e), "bindings": []}
        gone = stale_bindings(recorded, aliases)
        return {
            "state": "problems" if gone else "ok",
            "error": None,
            "bindings": [{**b.to_dict(), "message": stale_message(b)} for b in gone],
        }

    def _find_asset(self, dataset_id: str) -> Asset:
        asset = next((a for a in self._assets.list_datasets(self._domino_project_id) if a.id == dataset_id), None)
        if asset is None:
            raise LookupError(dataset_id)
        return asset

    def list_asset_files(self, dataset_id: str) -> list[dict]:
        """Files under a mounted dataset, each with its size and whether it's already attached."""
        asset = self._find_asset(dataset_id)
        attached = {e["path"] for e in self.project().attached}
        out = []
        for f in self._assets.list_files(asset):
            dest = _attach_dest(asset.name, f.path)
            out.append({"path": f.path, "size": f.size, "dest": dest, "attached": dest in attached})
        return out

    def attach_file(self, dataset_id: str, file_path: str) -> dict:
        """Symlink one dataset file into the workspace under public/data/ so OpenCode can @mention
        it and the (static) preview/published app can fetch it — no byte copy, the symlink points
        at the live Domino mount. A sensitivity-tagged dataset still fires the sticky sovereign lock.
        Enforces a configurable total-size cap across all attached files."""
        project = self.project()
        asset = self._find_asset(dataset_id)
        if not asset.mount_path:
            raise LookupError(dataset_id)  # not mounted here -> nothing to attach
        src = _safe_join(Path(asset.mount_path), file_path)
        if not src.is_file():
            raise FileNotFoundError(file_path)
        rel = _attach_dest(asset.name, file_path)  # workspace-relative posix path
        already = next((e for e in project.attached if e["path"] == rel), None)
        sensitive = is_sensitive(asset, self._sensitivity_tag)
        if already is None:
            size = src.stat().st_size
            total = sum(e["size"] for e in project.attached)
            if total + size > self._attach_max_bytes:
                raise AttachTooLarge(self._attach_max_bytes, total, size)
            dest = _safe_join(project.workspace.path, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            dest.symlink_to(src)
            # source="dataset": bytes belong to a pre-existing dataset — delete must never remove them.
            project.attached.append(
                {"dataset_id": dataset_id, "dataset": asset.name, "file": file_path, "path": rel,
                 "size": size, "sensitive": sensitive, "source": "dataset", "dataset_rel_path": file_path}
            )
            self._ensure_data_gitignored(project.workspace)
            self._write_agents_data_block(project)
            project.workspace.write_attachments(project.attached)
        project.control.on_assets_changed([sensitive])  # sticky lock if sensitive
        size = next((e["size"] for e in project.attached if e["path"] == rel), 0)
        entry = next((e for e in project.attached if e["path"] == rel), {})
        return {"attached": file_path, "dataset": asset.name, "path": rel, "size": size,
                "sensitive": sensitive, "descriptor": entry.get("descriptor"),
                "status": project.status()}

    def detach_file(self, path: str) -> dict:
        """Remove an attached file's symlink (keyed by its workspace path, so rehydrated entries
        with no dataset_id detach too) and forget it. Also deletes any standalone COPY of the file the
        agent leaked into the app tree (same basename under src/ etc.): once the entry leaves
        project.attached the commit backstop (_detect_leaks) stops covering it, so a leaked copy would
        otherwise get staged into the next save — pushing the (possibly sensitive) bytes into git.
        Inlined-into-code copies are left in place (deleting the source file would nuke app logic) and
        reported, alongside code that fetches the served path, as `refs` so the UI can warn and offer an
        agent cleanup. Keeps the dataset bytes. Does NOT clear the sovereign lock even for a
        sensitivity-tagged dataset — the asset-driven lock is sticky (ModelControl); unlock manually."""
        project = self.project()
        if not path.startswith("public/data/"):
            raise ValueError(path)
        entry = next((e for e in project.attached if e["path"] == path), None)
        usage = self._data_usage(project, entry) if entry else {"refs": [], "copies": []}
        name = PurePosix(path).name
        removed: list[str] = []
        for rel in usage["copies"]:
            if PurePosix(rel).name == name:  # the raw file copied in — leaked data, no app logic of its own
                cp = _safe_join(project.workspace.path, rel)
                if cp.is_symlink() or cp.is_file():
                    cp.unlink()
                    removed.append(rel)
        dest = _safe_join(project.workspace.path, path)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        _prune_empty_dirs(dest.parent, project.workspace.path / "public" / "data")
        project.attached[:] = [e for e in project.attached if e["path"] != path]
        self._write_agents_data_block(project)
        project.workspace.write_attachments(project.attached)
        still_used = sorted(set(usage["refs"] + [r for r in usage["copies"] if PurePosix(r).name != name]))
        return {"detached": path, "removed_copies": removed, "refs": still_used, "status": project.status()}

    def upload_file(self, filename: str, data: bytes, sensitive: bool = False,
                    dataset_id: str | None = None) -> dict:
        """Write an uploaded file into a writable dataset mount (persisted, and outside git), then
        attach it under public/data/ like any dataset file. Hybrid sensitivity:

        - No `dataset_id` -> the shared default project dataset. A sensitive upload goes to its
          `sensitive/` subfolder and is recorded sensitive in the manifest (which drives the
          sovereign lock); the dataset is NOT tagged (its non-sensitive data must stay unmarked).
        - A picked `dataset_id` -> a real Domino tag. If the dataset is already tagged `sensitive`
          the file is sensitive regardless; if it's untagged and the upload is marked sensitive we
          tag the whole dataset (best-effort) so the tag governs it and every future attachment.

        The committed manifest lets the published app rebuild public/data/ from the mount. Enforces
        the same total-size cap as attach."""
        project = self.project()
        if not filename or not filename.strip():
            raise ValueError("filename required")
        name = _slug(filename)
        target, effective_sensitive, subfolder = self._resolve_upload_target(sensitive, dataset_id)
        if target is None or not target.mount_path:
            raise UploadUnavailable(sensitive)
        size = len(data)
        total = sum(e["size"] for e in project.attached)
        if total + size > self._attach_max_bytes:
            raise AttachTooLarge(self._attach_max_bytes, total, size)
        rel_in_dataset = PurePosix(subfolder, name).as_posix()
        dest_bytes = _safe_join(Path(target.mount_path), rel_in_dataset)
        rel = _attach_dest(target.name, rel_in_dataset)
        link = _safe_join(project.workspace.path, rel)   # resolved BEFORE any write, so a rejected
        dest_bytes.parent.mkdir(parents=True, exist_ok=True)  # path fails without stranding bytes
        # The bytes land on the dataset mount, which is OUTSIDE git and outside the workspace, while
        # everything that RECORDS them (symlink, manifest, AGENTS.md) is inside it. A failure in
        # between therefore strands data on a shared mount with nothing pointing at it — invisible
        # to detach/delete, and for a sensitive upload, unlocked. So the write is undone on any
        # failure. `created` guards the one case we must not undo: overwriting a same-named
        # re-upload already destroyed the old bytes, and deleting the file would compound that.
        created = not dest_bytes.exists()
        dest_bytes.write_bytes(data)
        try:
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(dest_bytes)
            project.attached[:] = [e for e in project.attached if e["path"] != rel]
            project.attached.append(
                {"dataset_id": target.id, "dataset": target.name, "file": rel_in_dataset, "path": rel,
                 "size": size, "sensitive": effective_sensitive, "source": "upload",
                 "dataset_rel_path": rel_in_dataset}
            )
            self._ensure_data_gitignored(project.workspace)
            self._write_agents_data_block(project)
            project.workspace.write_attachments(project.attached)
        except Exception:
            project.attached[:] = [e for e in project.attached if e["path"] != rel]
            for undo in (lambda: link.unlink() if link.is_symlink() or link.exists() else None,
                         lambda: _prune_empty_dirs(link.parent,
                                                   project.workspace.path / "public" / "data"),
                         lambda: dest_bytes.unlink() if created else None,
                         lambda: project.workspace.write_attachments(project.attached)):
                try:
                    undo()
                except OSError:
                    pass  # best effort — rollback must never mask the failure that caused it
            raise
        if effective_sensitive:
            project.control.on_assets_changed([True])  # sticky sovereign lock
        # descriptor rides the response so the Data panel can flag an image the agent can't see
        # immediately, instead of only after the next page load refetches the attachment list.
        entry = next((e for e in project.attached if e["path"] == rel), {})
        return {"uploaded": name, "dataset": target.name, "dataset_id": target.id, "path": rel,
                "size": size, "sensitive": effective_sensitive,
                "descriptor": entry.get("descriptor"), "status": project.status()}

    def _resolve_upload_target(self, sensitive: bool, dataset_id: str | None) -> tuple[Asset | None, bool, str]:
        """Resolve (target dataset, effective sensitivity, subfolder) for an upload.

        Without a picked dataset the target is the shared default dataset and sensitivity uses the
        `sensitive/` subfolder without tagging. With a picked dataset, sensitivity is tag-driven:
        an already-tagged dataset forces sensitive; an untagged one gets tagged (best-effort) when
        the upload is marked sensitive. Picked datasets always write to `uploads/`."""
        if dataset_id:
            try:
                target = self._find_asset(dataset_id)
            except LookupError:
                return None, False, "uploads"
            if not target.mount_path or not os.access(target.mount_path, os.W_OK):
                return None, False, "uploads"
            already = is_sensitive(target, self._sensitivity_tag)
            effective = already or bool(sensitive)
            if effective and not already:
                self._tag_dataset_sensitive(target)  # best-effort governance tag
            return target, effective, "uploads"
        # Default (shared) project dataset: subfolder + manifest drive sensitivity, no tag.
        target = self._default_dataset()
        subfolder = "sensitive" if sensitive else "uploads"
        return target, bool(sensitive), subfolder

    def default_dataset_id(self) -> str | None:
        """Id of the dataset uploads land in when the user doesn't pick one — lets the UI label and
        pre-select it by its real name instead of a generic "Project data" option."""
        target = self._default_dataset()
        return target.id if target else None

    def _default_dataset(self) -> Asset | None:
        """The shared default project dataset to write uploads into: a writable, mounted dataset,
        preferring the project's own (named after / owned by the project, mounted under /mnt/data),
        falling back to the first writable non-sensitive dataset (covers the local fake harness)."""
        writable = [a for a in self._assets.list_datasets(self._domino_project_id)
                    if a.mount_path and os.access(a.mount_path, os.W_OK)]
        pname = self._domino_project_name
        if pname:
            for a in writable:
                if a.name == pname and str(a.mount_path).startswith("/mnt/data"):
                    return a
            for a in writable:
                if a.project == pname or a.name == pname:
                    return a
        for a in writable:
            if not is_sensitive(a, self._sensitivity_tag):
                return a
        return writable[0] if writable else None

    def _tag_dataset_sensitive(self, target: Asset) -> bool:
        """Tag a picked dataset `sensitive` via the control plane (best-effort). Reuses the snapshot
        id from the dataset's tag map when present, else lets the control plane fetch one. Returns
        False (without raising) when there's no control plane or the tag write fails — the upload
        still proceeds and the manifest carries the per-file sensitive flag."""
        if self._control_plane is None:
            log.warning("tag_dataset_sensitive skipped: no control plane (dataset %s)", target.id)
            return False
        snap = next(iter(target.tag_snapshots.values()), None) if target.tag_snapshots else None
        return bool(self._control_plane.tag_dataset_sensitive(target.id, snapshot_id=snap))

    def delete_file(self, path: str) -> dict:
        """Delete an UPLOADED file: remove its workspace symlink AND its bytes from the dataset mount,
        then forget it. Bytes are deleted only for Sage-managed uploads — files under a dataset's
        `uploads/` folder, which Sage always created (whether attached as source=='upload' or later
        re-attached from the dataset browser as source=='dataset'). A genuine pre-existing dataset
        file (not under uploads/) is detach-only here; its bytes are the user's data and never
        removed. Sensitivity is irrelevant to deletability — it only drives the sovereign lock.
        The sovereign lock stays sticky."""
        project = self.project()
        if not path.startswith("public/data/"):
            raise ValueError(path)
        entry = next((e for e in project.attached if e["path"] == path), None)
        # Refuse to delete data the app still uses — otherwise the code that fetches (or has copied)
        # it is left dangling, and a copied file keeps the dashboard "working" after the data is gone.
        # Detach stays available for a deliberate drop; here we protect the user's built app.
        if entry:
            usage = self._data_usage(project, entry)
            if usage["refs"] or usage["copies"]:
                raise DataReferenced(path, usage["refs"], usage["copies"])
        link = _safe_join(project.workspace.path, path)
        if link.is_symlink() or link.exists():
            link.unlink()
        _prune_empty_dirs(link.parent, project.workspace.path / "public" / "data")
        if entry and _is_sage_upload(entry):
            self._delete_upload_bytes(entry)
        project.attached[:] = [e for e in project.attached if e["path"] != path]
        self._write_agents_data_block(project)
        project.workspace.write_attachments(project.attached)
        return {"deleted": path, "status": project.status()}

    def _delete_upload_bytes(self, entry: dict) -> None:
        """Remove an uploaded file's bytes from its dataset mount. Guarded to only ever touch a path
        under a Sage upload folder (uploads/ or sensitive/) resolved within the dataset mount — so
        it can never delete pre-existing data."""
        rel = entry.get("dataset_rel_path") or ""
        if not rel.startswith(_SAGE_UPLOAD_PREFIXES):
            return
        asset = next((a for a in self._assets.list_datasets(self._domino_project_id)
                      if a.id == entry.get("dataset_id")), None)
        if asset is None or not asset.mount_path:
            return
        try:
            target = _safe_join(Path(asset.mount_path), rel)
        except ValueError:
            return
        try:
            if target.is_file():
                target.unlink()
                _prune_empty_dirs(target.parent, Path(asset.mount_path))
        except OSError:
            log.exception("delete_upload_bytes: failed to remove %s", rel)

    def _scan_app_sources(self, project: Project) -> list[tuple[str, str | None]]:
        """(workspace-relative posix path, text) for each file under the app tree — skips
        dependencies, build output, git, and public/ (the attached-data symlinks live there). Text is
        the file's contents for code files (see _SCAN_EXTS) and None otherwise, so a copied data file
        is still listed (matched by basename) without reading megabytes of CSV."""
        root = project.workspace.path
        out: list[tuple[str, str | None]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
            for fn in filenames:
                fp = Path(dirpath) / fn
                text: str | None = None
                if Path(fn).suffix.lower() in _SCAN_EXTS:
                    try:
                        text = fp.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                out.append((fp.relative_to(root).as_posix(), text))
        return out

    def _data_usage(self, project: Project, entry: dict,
                    sources: list[tuple[str, str | None]] | None = None) -> dict:
        """How the app's source uses an attached file, so delete can refuse to orphan code:
          refs   — source files that fetch it by its served path/name (the intended runtime dependency)
          copies — source files that ARE a copy of the data: same basename under the app tree, or its
                   bytes inlined. This is the leak we forbid (public/data/ is gitignored on purpose),
                   and it's why deleting the attachment leaves the dashboard still working.
        """
        if sources is None:
            sources = self._scan_app_sources(project)
        served = entry["path"][len("public/"):]        # data/<slug>/uploads/<name>
        name = PurePosix(entry["path"]).name
        refs: list[str] = []
        copies: list[str] = []
        raw: bytes | None = None
        for rel, text in sources:
            if PurePosix(rel).name == name:            # a file copied under the app tree (any type)
                copies.append(rel)
                continue
            if text is None:                            # non-code file, nothing more to inspect
                continue
            if raw is None:
                raw = self._attachment_bytes(project, entry)
            if raw is not None and _is_inlined_copy(raw, text):
                copies.append(rel)                      # data bytes inlined into source (full or sample)
            elif served in text or name in text:
                refs.append(rel)
        return {"refs": refs, "copies": copies}

    def _detect_leaks(self, project: Project) -> list[tuple[str, list[str]]]:
        """(attachment name, [source files that copy it]) for every attached file whose bytes were
        duplicated into the app tree. Empty when nothing was copied. One source scan for all files."""
        if not project.attached:
            return []
        sources = self._scan_app_sources(project)
        out: list[tuple[str, list[str]]] = []
        for e in project.attached:
            copies = self._data_usage(project, e, sources)["copies"]
            if copies:
                out.append((PurePosix(e["path"]).name, copies))
        return out

    def _leaked_copy_paths(self, project: Project) -> list[str]:
        """Flat list of workspace-relative source files that are copies of attached data — passed to
        commit_all(exclude=...) so the (possibly sensitive) bytes are never staged into a commit."""
        return [f for _, files in self._detect_leaks(project) for f in files]

    def _attachment_bytes(self, project: Project, entry: dict) -> bytes | None:
        """Read an attached file's bytes (follows the symlink to the dataset mount). None if absent."""
        try:
            p = project.workspace.path / entry["path"]
            return p.read_bytes() if p.is_file() else None
        except OSError:
            return None

    _AGENTS_BEGIN = "<!-- sage:attached-data:begin -->"
    _AGENTS_END = "<!-- sage:attached-data:end -->"

    def _write_agents_data_block(self, project: Project) -> None:
        """Maintain a managed block in the workspace AGENTS.md listing attached data files, so the
        agent knows they exist (and their served paths) even without an explicit @mention."""
        agents = project.workspace.path / "AGENTS.md"
        if project.attached:
            # Be prescriptive: give the EXACT disk path and the EXACT served URL per file. Agents
            # otherwise guess a flat `/data/<name>` (the files are nested under a dataset slug), hit
            # the SPA fallback (index.html) instead of the CSV, and "fix" it by copying the file into
            # src/ — which leaks the data into git (public/data/ is gitignored on purpose).
            lines = [
                "## Attached data", "",
                ("The user attached the files below. Each lives on disk at the path shown (read or "
                 "edit it there) and the running app serves it at the URL shown. Load one in app code "
                 "by fetching it RELATIVE TO THE APP BASE, so it resolves in both the dev preview and "
                 "the published app:"), "",
                "```js",
                "// import.meta.env.BASE_URL always ends in '/', so this string is a valid relative",
                "// URL in both the dev preview and the published app.",
                'const url = import.meta.env.BASE_URL + "data/<slug>/<name>";',
                "const text = await fetch(url).then((r) => r.text());",
                "```", "",
                ("Do NOT wrap it in `new URL(path, import.meta.env.BASE_URL)` — BASE_URL is a path "
                 "(e.g. `/`), not an absolute URL, so `new URL()` throws `Invalid base URL` and crashes "
                 "the app on load. Just concatenate as shown. "
                 "Do NOT fetch a leading-slash path like `/data/...` — it breaks under the app's base "
                 "prefix. Do NOT copy these files into `src/`: `public/data/` is gitignored on purpose, "
                 "so copying leaks the data into the app's git repo. @mention a file by its disk path."), "",
                # grep/ripgrep skips ignored paths AND does not follow symlinks; every attachment is
                # both. So a search over one silently returns nothing and the agent concludes the
                # value isn't there — a wrong answer, not an error. Reading the exact path works.
                ("To look INSIDE one of these files, use the read tool on its exact disk path. Do NOT "
                 "use grep/search: `public/data/` is gitignored and each file is a symlink, so search "
                 "skips them and returns no matches even when the value IS present. A search that "
                 "finds nothing here proves nothing — read the file instead."), "",
            ]
            for e in project.attached:
                path = e["path"]
                served = path.removeprefix("public/")
                # One-line shape only. This block is re-read every turn, so the full descriptor stays
                # out of it — that one is inlined by send_prompt for @mentioned files alone.
                shape = self._descriptor(project, e)["summary"]
                lines.append(f"- disk `{path}` — {shape} — fetch `{served}` (relative to base) "
                             f"— from dataset **{e['dataset']}**")
            block = f"{self._AGENTS_BEGIN}\n" + "\n".join(lines) + f"\n{self._AGENTS_END}"
        else:
            block = ""
        with self._agents_lock:  # serialize with write_instructions — same file, distinct regions
            existing = agents.read_text() if agents.exists() else ""
            b, e = existing.find(self._AGENTS_BEGIN), existing.find(self._AGENTS_END)
            if b != -1 and e != -1:
                existing = existing[:b] + block + existing[e + len(self._AGENTS_END):]
            elif block:
                existing = (existing.rstrip() + "\n\n" + block + "\n") if existing.strip() else block + "\n"
            agents.write_text(existing.strip("\n") + "\n" if existing.strip() else "")
        self._rebaseline_turn(project)

    _MODEL_BEGIN = "<!-- sage:app-model:begin -->"
    _MODEL_END = "<!-- sage:app-model:end -->"
    _MODEL_API_BEGIN = "<!-- sage:app-model-api:begin -->"
    _MODEL_API_END = "<!-- sage:app-model-api:end -->"

    def _write_app_model(self, project: Project) -> None:
        """Pin the app's model into its own source, and tell the agent it is there (#7).

        Called on every Binding change, and reads the manifest back rather than taking the new list,
        so the file on disk is a function of the record on disk and cannot drift from it.

        Helper BEFORE config, for the reason _DEPLOY_FILES is ordered: the config is imported by the
        helper, so a config that lands without one is an app that no longer compiles, while a helper
        without a config is inert.

        Written only when the text changes. This file is committed to the user's app repo, and a
        rewrite with identical content would still show up as a dirty file in the turn's tree
        comparison and in their git history.
        """
        alias = pinned_alias(parse_bindings(project.workspace.read_bindings()))
        if alias is not None:
            self._wm.ensure_llm_helper()
        self._write_generated(project.workspace.path / CONFIG_PATH,
                              render_config(alias, self._browser_gateway_base, self._cost_project_label))
        self._splice_agents(project, self._MODEL_BEGIN, self._MODEL_END, agents_block(alias))

    def _write_app_model_api(self, project: Project) -> None:
        """Pin the app's Model API into its own source, and tell the agent it is there (#9).

        The Model API twin of _write_app_model, and the same contract: reads the manifest back rather
        than taking a list, writes only on change, helper before config.

        The credential is read here rather than passed in for that same reason — the file on disk is
        a function of what is on disk. A Binding whose credential has gone renders as no Model API,
        which is what `render_config` documents and what bind refuses to create in the first place.
        """
        bindings = parse_bindings(project.workspace.read_bindings())
        api = pinned_model_api(bindings)
        credential = self._credentials(project).get(api.id) if api is not None else None
        if api is not None:
            self._wm.ensure_model_api_helper()
        self._write_generated(project.workspace.path / MODEL_API_CONFIG_PATH,
                              render_model_api_config(api, credential))
        self._splice_agents(project, self._MODEL_API_BEGIN, self._MODEL_API_END,
                            model_api_agents_block(api))

    def _write_app_resources(self, project: Project) -> None:
        """Both pinned-Resource writers, then one baseline move for the pair.

        One entry point because a Binding change can move either pin, and because rebaselining once
        per change keeps a mid-build bind from being counted as two separate writes by the agent.
        """
        self._write_app_model(project)
        self._write_app_model_api(project)
        self._rebaseline_turn(project)

    @staticmethod
    def _write_generated(path: Path, text: str) -> None:
        """A Sage-generated file, written only when its content changes.

        These are committed to the user's app repo, and a rewrite with identical content would still
        show up as a dirty file in the turn's tree comparison and in their git history.
        """
        if not path.is_file() or path.read_text() != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def _splice_agents(self, project: Project, begin: str, end: str, block: str) -> None:
        """Replace one managed region of AGENTS.md, or drop it when the block is empty."""
        if block:
            block = f"{begin}\n" + block + f"\n{end}"
        agents = project.workspace.path / "AGENTS.md"
        with self._agents_lock:  # serialize with the other managed regions in this file
            existing = agents.read_text() if agents.exists() else ""
            b, e = existing.find(begin), existing.find(end)
            if b != -1 and e != -1:
                existing = existing[:b] + block + existing[e + len(end):]
            elif block:
                # Appended, with no anchor among the other regions: this one describes what the app
                # has, like the attached-data block, so its position carries no meaning and staying
                # out of the ordering leaves write_instructions' own splice untouched.
                existing = (existing.rstrip() + "\n\n" + block + "\n") if existing.strip() else block + "\n"
            agents.write_text(existing.strip("\n") + "\n" if existing.strip() else "")

    def _rebaseline_turn(self, project: Project) -> None:
        """Move a running turn's working-tree baseline forward over writes WE just made on the
        user's behalf (attach / upload / detach / delete: AGENTS.md, .gitignore, the public/data/
        symlink). Those endpoints don't take the turn lock — uploading data mid-build is a normal
        thing to do — so without this the turn's end-of-run tree comparison sees changed files and
        blames the agent. On a read-only turn that means a bogus "gate violated" and a
        discard_changes() that deletes the file the user just uploaded. No-op when no turn is
        running. Best-effort: if the hash can't be taken we leave the old baseline, which fails the
        safe way (a false write report, never a missed one)."""
        if not project.turn_tree_baseline:
            return
        new = project.snapshot.working_tree_hash()
        if new:
            project.turn_tree_baseline = new

    _INSTR_BEGIN = "<!-- sage:instructions:begin -->"
    _INSTR_END = "<!-- sage:instructions:end -->"
    _INSTR_HEAD = "## User project instructions"
    _INSTR_FRAME = ("Apply the user's guidance below. It does NOT override the build, configuration, "
                    "or design-system rules above — on any conflict, the rules above win.")

    def read_instructions(self, project: Project) -> str:
        """Return the user's raw project-instructions body (the managed heading + frame stripped off),
        or "" if the block is absent or AGENTS.md is missing."""
        agents = project.workspace.path / "AGENTS.md"
        if not agents.exists():
            return ""
        existing = agents.read_text()
        b, e = existing.find(self._INSTR_BEGIN), existing.find(self._INSTR_END)
        if b == -1 or e == -1:
            return ""
        inner = existing[b + len(self._INSTR_BEGIN):e]
        # Strip the managed heading line and the frame paragraph, leaving only the user's body.
        prefix = f"\n{self._INSTR_HEAD}\n\n{self._INSTR_FRAME}\n\n"
        inner = inner.removeprefix(prefix)
        return inner.strip("\n")

    def write_instructions(self, project: Project, content: str) -> None:
        """Splice the user's project instructions into AGENTS.md as a managed block, preserving the
        template body and the attached-data block. Empty content removes the block."""
        agents = project.workspace.path / "AGENTS.md"
        content = content.strip()
        if content:
            block = (f"{self._INSTR_BEGIN}\n{self._INSTR_HEAD}\n\n{self._INSTR_FRAME}\n\n"
                     f"{content}\n{self._INSTR_END}")
        else:
            block = ""
        with self._agents_lock:  # serialize with _write_agents_data_block — same file, distinct regions
            existing = agents.read_text() if agents.exists() else ""
            b, e = existing.find(self._INSTR_BEGIN), existing.find(self._INSTR_END)
            if b != -1 and e != -1:
                existing = existing[:b] + block + existing[e + len(self._INSTR_END):]
            elif block:
                d = existing.find(self._AGENTS_BEGIN)
                if d != -1:  # keep file order: template body -> instructions -> attached-data
                    existing = existing[:d].rstrip() + "\n\n" + block + "\n\n" + existing[d:].lstrip()
                else:
                    existing = (existing.rstrip() + "\n\n" + block + "\n") if existing.strip() else block + "\n"
            agents.write_text(existing.strip("\n") + "\n" if existing.strip() else "")

    @staticmethod
    def _ensure_data_gitignored(workspace: Workspace) -> None:
        gi = workspace.path / ".gitignore"
        line = "public/data/"
        existing = gi.read_text() if gi.exists() else ""
        if line not in existing.split():
            gi.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n") + line + "\n")

    def shutdown(self) -> None:
        # Stop-safe backstop: on a graceful SIGTERM (Domino /stop, idle cull, or the hub button),
        # save any in-progress work first — commit + pull/resolve + push — so stopping never drops
        # uncommitted edits. Done before tearing down opencode, whose server the conflict-resolution
        # turn still needs. Best-effort: _save_to_git never raises, but guard the teardown regardless.
        if self._project is not None:
            try:
                self._save_to_git(self._project, "save before stop")
            except Exception:
                log.exception("shutdown: failed to save work to git")
        # Best-effort per resource: the preview failing to stop must not leave the shared
        # opencode server running as an orphan.
        if self._project is not None:
            try:
                self._project.supervisor.stop()
            except Exception:
                log.exception("shutdown: failed to stop preview")
        if self._oc_server:
            try:
                self._oc_server.stop()
            except Exception:
                log.exception("shutdown: failed to stop opencode server")
