"""Orchestrator service — assembles a project's parts into one lifecycle (SPEC C1).

A Project bundles: workspace (from the warm template), a Vite supervisor (live preview), a
ModelControl (per-project switching state), and an EnforcementShim (routes model calls through
the gateway). Per D9 a container hosts exactly one project, bound to
the Domino project's mounted volume and attached lazily on first use.

Deep module, narrow interface: project / build / build_stream / shutdown.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import re
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from pathlib import PurePosixPath as PurePosix
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ..provision.domino import ControlPlane

from ..assets.provider import Asset, AssetProvider, FakeAssetProvider
from ..driver.opencode import OpenCodeClient, run_feedback_loop
from ..driver.server import OpenCodeServer
from ..feedback.circuit_breaker import CircuitBreaker
from ..feedback.runner import FeedbackRunner
from ..gateway.client import GatewayClient
from ..preview.prefix import domino_base_prefix, publish_available
from ..preview.queries import PreviewQueries
from ..preview.supervisor import ViteSupervisor
from ..provision import naming

# The 404 the publish path has to tell from every other failure (#80). A runtime import, unlike the
# `ControlPlane` Protocol above, because it is caught rather than annotated with.
# `app_viewer_url` beside it for the rail row's `Open app` door (#89) — same module, and the same
# reason: the URL grammar is the control plane's, so only one file gets to know it.
from ..provision.domino import NotFound, app_viewer_url
from ..resources.bindings import (
    KIND_DATA_SOURCE,
    KIND_DATASET,
    KIND_LLM_ALIAS,
    KIND_MODEL_API,
    Binding,
    Mention,
    mention_note,
    parse_bindings,
)
from ..resources.bound_schema import (
    LEGACY_SOURCE,
    SAMPLES_PATH,
    SCHEMA_PATH,
    BoundSource,
    SharedSample,
    parse_samples,
    parse_schema,
    recorded_scope,
    render_samples,
    render_schema,
)
from ..resources.bound_schema import agents_block as data_agents_block
from ..resources.builtapp import catalog_problems, serve_module, stranded_levels
from ..resources.gateway_bypass import raw_gateway_calls, unbound_alias_notice
from ..resources.model_api_credentials import (
    Credential,
    CredentialRequired,
    CredentialStore,
    verify_credential,
)
from ..resources.model_api_snippet import parse_snippet
from ..resources.pinned_model import agents_block, bound_aliases, render_config
from ..resources.pinned_model_api import agents_block as model_api_agents_block
from ..resources.pinned_model_api import bound_model_apis
from ..resources.pinned_model_api import render_config as render_model_api_config
from ..resources.preflight import (
    SLOTS,
    alias_problem,
    bindings_on_dead_endpoints,
    credential_message,
    missing_credentials,
    slots_on_dead_endpoints,
    stale_bindings,
    stale_message,
    turn_refusal,
    turn_slots,
    unresolved_slots,
)
from ..resources.provider import (
    Column,
    DataSource,
    FakeResourceProvider,
    LlmAlias,
    ResourceProvider,
    ResourceUnavailable,
    alias_reasoning_efforts,
    cascade_levels,
    safe_identifier,
)
from ..resources.publish_egress import egress_notice, needs_listing
from ..resources.publish_guard import (
    PublishRefused,
    data_source_bindings,
    missing_app_problem,
    publish_problems,
)
from ..router.model_control import ModelControl
from ..router.models import ASSIGNABLE_SLOTS, Mode, ModelCatalog, Phase
from ..shim.enforcement import EnforcementShim
from ..workspace import plan_doc
from ..workspace.manager import ProjectRecord, Workspace, WorkspaceManager, ensure_ignore_line
from ..workspace.snapshot import TurnSnapshot
from ..workspace.threads import (
    ThreadStore,
    _ensure_dir_link,
    ensure_chat_workdir,
    new_artifact_paths,
    new_id,
    revert_denied_writes,
    snapshot_files,
    title_from_prompt,
)
from . import brand, chat_compact, scope
from . import handoff as chat_handoff
from . import recall
from .describe import describe, fit_image
from .plan_steps import MIN_STEPS, PlanStep, is_phasable, parse_steps, step_index

log = logging.getLogger("sage.orchestrator")

# Consecutive OpenCode poll (is_running/messages) failures tolerated before halting a build. Each poll
# can block up to its httpx timeout, so this is ~a minute of sustained unresponsiveness, not a blip.
_MAX_POLL_FAILURES = 4

# What ends a BUILD turn that will not end itself (#39). Silence, not wall clock: a turn writing a
# large file is legitimately quiet for a minute, and a phased build for far longer, so a deadline on
# the turn's total length kills healthy work. This one is measured from the last thing OpenCode
# produced — text, a tool call, a phase change — so any turn still making progress resets it and is
# never at risk however long it runs in total.
#
# This is the IDLE window: nothing is open, so what is being waited on is the model taking its next
# step. Two minutes is several times the longest of those gaps, and far below the 36 minutes the
# live incident sat wedged. It was five minutes until #98 split the slow-tool case out into
# _BUILD_TOOL_QUIET_TIMEOUT_S below — five was sized for a case this window no longer has, and
# leaving it there would have made the wedge it exists to catch wait three minutes longer than it
# has any reason to.
#
# The 12-second deadline in the poll loop is a different rule and stays as it is: it covers a turn
# that never appeared at all, and this one only starts to matter once one has.
_BUILD_QUIET_TIMEOUT_S = 120.0

# The same silence, while a tool call is still open (#98). One window over both cases had to be the
# worst of the two: a `npm run build` or a broad test run sends nothing between `called` and its
# result, so covering it meant a genuinely wedged turn — model stopped, nothing running — sat for
# the length of the slowest tool anyone might run. Splitting them lets each be sized for what it
# is. The idle window came DOWN from five minutes to two: with slow calls out of it, what is left
# is the gap between steps, and four times the longest of those is already generous. This one went
# up to ten minutes, which is the ceiling on a tool rather than on a wedge — a `task` sub-agent is
# one outstanding call for however long it runs, and killing that was the reported bug.
#
# Build has no wall-clock backstop the way Chat has _CHAT_TURN_MAX_S, deliberately (see above), so
# this is the only cap on a call that never returns.
_BUILD_TOOL_QUIET_TIMEOUT_S = 600.0

# How long we wait for a wedged session to confirm it actually stopped, after asking it to. Only
# a session that confirms lets the turn lock go — see _stop_wedged_session.
_BUILD_STOP_GRACE_S = 30.0

# What ends a Chat turn that will not end itself. Quiet time, not wall clock: a hung
# `DataSourceClient.query` (Arrow Flight from a published App) never goes idle, the UI stays on its
# last label, and the turn lock blocks the next send — and that hang looks exactly like this, a
# session that is running while nothing arrives. A turn that is streaming text, or starting and
# finishing tools, is alive however long it has been going, so only silence ends it. The wall clock
# this replaces could not tell the two apart: it killed long-but-working turns at 90s, which is what
# "convert this into an app" hit, and the live hang ran past 20 minutes because nothing capped it.
_CHAT_QUIET_TIMEOUT_S = 90.0
# The same silence, while a tool is in flight. A tool sends nothing between `called` and its result,
# so one window cannot tell a hung query from a Dataset file being fetched — and `download_file` on
# a real transactions CSV is legitimately silent for minutes. That is the turn this cap was killing:
# the agent did the one thing it was told to do, and Chat stopped it for doing it. Splitting the two
# keeps the hang capped without killing the work — a stalled MODEL still ends at 90s, a tool that
# never comes back still ends here, and _CHAT_TURN_MAX_S is the backstop under both.
_CHAT_TOOL_QUIET_TIMEOUT_S = 240.0
# Alive is not the same as getting somewhere: an agent that loops holds the turn lock for as long as
# it keeps talking. Generous, because by then the person can see the work and can press Stop — this
# is the backstop for a turn nobody is watching, not the cap for a turn that is going well.
_CHAT_TURN_MAX_S = 600.0
# How many of the newest messages a Chat poll reads. The whole transcript came back on every poll,
# once a second for the length of the turn, so the cost of asking a question grew with the length of
# the Thread rather than with the question — and it competed for CPU with the agent it was watching,
# on the same box. A turn writes one assistant message per step, so this is a turn many times over;
# anything older was already emitted and is already in `seen`.
_CHAT_POLL_MESSAGES = 20

# Largest image inlined into a prompt as a data: URI. Base64 inflates by ~4/3, and the result rides
# in the request body through OpenCode -> shim -> gateway -> provider; anything larger degrades to
# its descriptor instead. Provider limits sit around 5 MB per image, so this stays well under.
_MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024

# How long a turn-time slot check may reuse the gateway's Alias listing (#125). Sixty seconds, and
# the number is chosen against what each side costs when it is wrong. Too long and a maintainer who
# has just re-registered an Alias is refused for another minute; too short and a phased build pays a
# round trip per phase, plus one for the plan turn and one for the approve, for an answer that
# cannot have changed in between. What it is NOT protecting is a person fixing this from the
# Workbench: the remedy the refusal names changes the CATALOG, which is read live and never cached,
# so picking a different model takes effect on the very next turn.
#
# The endpoint half of the answer is the exception to that: "start that endpoint" is a remedy the
# person may take on the platform rather than in the catalog, and endpoint status IS in the cached
# pair. So a slot refused for a stopped endpoint can stay refused for one more window after the
# endpoint comes back. Held anyway, because the alternative is a per-turn round trip for a fault
# that takes minutes to appear and minutes to clear.
_TURN_SLOT_TTL_S = 60.0

# And a much longer one for "we could not check". A gateway that hangs rather than refuses costs
# this check its whole timeout — two requests at 20s each — at the FRONT of a turn, with nothing on
# screen to say why. Remembering the failure for a minute does not help: real builds are minutes
# apart, so every one of them would pay it again. Five minutes bounds that to one hang per five,
# and it is safe to hold for as long as we like, because this state never refuses anything.
_TURN_SLOT_UNCHECKED_TTL_S = 300.0

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
    "build-plan", "step-start", "step-done", "attachments-restored",
    "reset-offer", "app-reset", "incoming-changes", "mentions-unresolved",
    "app_change", "build-stalled", "gateway-call", "gateway-alias-unbound",
})

# How long the rail's reading of the remote stays good for. The check runs off the request path, so
# this is how stale a badge may be, not how long anything waits: a turn does its own check, because
# a turn is the moment the answer has to be right (#78).
_REMOTE_CHECK_SECONDS = 30.0
# How many incoming file names an offer carries. Enough to recognise what a teammate touched, and
# short of pasting a thousand-file merge into the transcript; the full count rides beside it.
_INCOMING_FILES_SHOWN = 20

# The entry script Domino runs to serve a published app (repo root). The builder has the working
# tree, so publish pre-checks it exists locally before deploying (a missing one fails opaquely).
_ENTRY_POINT = "app.sh"
# The Python server that entry script execs to serve the build (ADR-0002). Pre-checked too, but only
# when this app's app.sh actually calls it — an app still serving with Node doesn't need it.
_SERVER_SCRIPT = "serve.py"
# Published-app deploy status -> terminal phase. Matched case-insensitively; anything else means
# the deploy is still in progress.
_RUNNING_STATES = frozenset({"running"})
_FAILED_STATES = frozenset({"failed", "error"})


def turn_busy_message(wedged: bool, action: str = "resend") -> str:
    """The sentence a refused operation answers with — the one place both of them live.

    Two states, not one. An ordinary refusal is a turn somebody can wait out or stop, and `action`
    names what they were doing when it was refused: the only part worth varying per entry point.
    A wedged workspace (#39) has neither a turn to wait for nor one to stop, so there is no tail to
    add and every entry point says the same thing — restart, which is the only thing that clears it.

    One place rather than one per caller because the distinction is the hard part, not the wording:
    a seventh hand-written copy is a seventh chance to tell somebody to wait for a build that is
    never going to finish (#97).
    """
    if wedged:
        return brand.text("This workspace is stuck on a build that would not stop, so {assistantName} "
                          "cannot start another one here. Restart the workspace to clear it. "
                          "Everything already written to your apps is safe.")
    return f"A build is already running. Wait for it to finish or stop it first, then {action}."


class TurnBusy(RuntimeError):
    """A non-streaming operation asked for the turn lock while something else held it.

    Still a RuntimeError, because that is what these entry points have always raised and what their
    callers unwind on. The type and the `wedged` flag are what is new: the routes used to test
    `str(e) == "busy"` and write their own sentence, which left them unable to tell a turn that is
    running from a workspace that will never run one again (#97)."""

    def __init__(self, wedged: bool = False, action: str = "resend") -> None:
        super().__init__(turn_busy_message(wedged, action))
        self.wedged = wedged


class ResetBusy(Exception):
    """A reset was asked for while a turn was already streaming. Same rule as a build: one operation
    owns the working tree at a time, and a reset under a live turn would pull the files out from
    under it.

    Its own type rather than a `TurnBusy`, because it comes off a different acquire — the one that
    waits out a Stop (see _acquire_for_reset). The message and the `wedged` flag are the same, from
    the same place."""

    def __init__(self, wedged: bool = False) -> None:
        super().__init__(turn_busy_message(wedged, "reset"))
        self.wedged = wedged


class TurnWedged(Exception):
    """A turn was given up on and the session it ran in would not confirm that it stopped (#39).

    Raised out of the turn so the entry point that holds `_turn_lock` keeps holding it. Releasing a
    lock whose session may still be writing is the corruption the lock exists to prevent, arriving
    from the fix instead of from the bug — a wedged workspace someone can restart beats a corrupted
    working tree nobody can detect. The turn has already said so in its own stream before this is
    raised, so nothing above needs to report it a second time."""


def turn_pending_message(ahead: int) -> str:
    """The sentence a QUEUED turn answers with, beside the one a refused turn gets (#79).

    Here rather than at the call site for the same reason `turn_busy_message` is: the hard part is
    which of the three things happened to somebody's question — refused, queued, or dropped — and a
    hand-written copy per entry point is another chance to get that wrong.

    It has one job the busy sentence never had, which is to say a pending turn is an intention and
    not a commitment. "Nothing has run yet" is that promise in the only terms that matter: no file
    written, no model called, nothing to undo if they change their mind."""
    turns = "the turn that is running" if ahead <= 1 else f"{ahead} turns"
    return brand.text("Queued behind {turns}. {assistantName} runs one turn at a time and will start "
                      "this when they finish. Nothing has run yet, so you can cancel it.",
                      turns=turns)


def turn_context_changed_message() -> str:
    """Why a pending turn was dropped instead of run: what it was written against has moved (#79).

    Both alternatives produce a turn that quietly did something other than what was on screen.
    Running against the snapshot resurrects a chip the person deliberately removed; running against
    live context makes the transcript bubble a lie about what the turn was given. So the turn does
    not run and the text goes back where they typed it (ADR-0013)."""
    return brand.text("Your context changed since you asked this, so {assistantName} did not run it. "
                      "The text is back in the composer — send it again to run it against what is "
                      "there now.")


class _TurnTicket:
    """One turn's place in the queue, and what it was written against (#79).

    `snapshot` is taken when the turn joins the queue and compared to the live context when it
    reaches the front. A turn that never waited carries none: it was written against the context it
    is about to run in, and reading it twice to prove that would cost every turn in the Project a
    pair of file reads for a comparison that cannot fail.

    `kind` and `conversation` are what the turn IS, for the controls that have to be aimed at it
    (#126). `kind` is the screen you can stop it from — "build" or "chat" — and deliberately NOT the
    routing `Mode`, which is ask/plan/implement/auto and answers a different question. Both are set
    before the ticket is admitted, because a queue with nothing in front of it makes a turn running
    in the same call."""

    __slots__ = ("app", "conversation", "granted", "id", "kind", "outcome", "snapshot")

    def __init__(self, ticket_id: str) -> None:
        self.id = ticket_id
        self.snapshot: dict = {}
        self.outcome = ""       # "" while waiting, then "ready" | "cancelled" | "wedged"
        self.granted = False    # the verdict the entry point reads: did this turn get to run?
        self.kind = ""          # "build" | "chat" — the screen this turn can be stopped from
        self.conversation = ""
        # Which Built App a build turn writes into. The Conversation is not enough on its own: the
        # Build transcript is one app's log for one Conversation, and the rail is free to move while
        # a turn streams (`_pin_turn_app`, #77) — so a Stop gated on the Conversation alone is aimed
        # at a build you stopped being able to see the moment you switched apps. Empty for Chat,
        # which writes Artifacts under the Thread rather than into an app.
        self.app = ""


class _TurnQueue:
    """FIFO admission to the turn lock, for the three streaming turn entry points (#79).

    `threading.Lock` hands a freed lock to whichever waiter the OS picked, which was fine while the
    only waiters were about to be refused and is wrong the moment turns wait in line. Drain order is
    "in the order you asked", across the whole Project rather than round-robin per Conversation:
    there are no tenants here, every pending turn was asked by the same person, and any other rule
    reorders their own questions against each other for no benefit they can perceive (ADR-0013). So
    a ticket goes on a deque when the turn is asked for, and only the head may take the lock.

    The lock stays a plain `threading.Lock` and is NOT owned by this class, because most of what
    takes it does not queue: publish, reset, create and delete app, the non-streaming build and the
    coalesced Chat save all keep their immediate refusal, since a control that sat silently until a
    long build finished would look like a control that did nothing (#89). This sits in front of the
    lock for the callers that queue; it does not replace it.

    Which is also why the wait has a poll interval rather than trusting a notify. A holder that
    never learned about the queue — or a test holding the raw lock — releases it without waking
    anybody, so the head ticket re-tests it on a short timer. Every release that goes through
    `release()` wakes it at once, so the timer is the backstop and not the mechanism."""

    def __init__(self, lock: threading.Lock, poll_s: float = 0.05) -> None:
        self._lock = lock
        self._poll_s = poll_s
        self._cond = threading.Condition()
        self._waiting: deque[_TurnTicket] = deque()
        # The ticket holding the lock, or None. Not on the deque — `_take` pops it — so without
        # this the running turn is the one turn nobody can name (#126). None is a real answer and
        # not a gap: publish, reset and the other raw-lock callers never queue, so a busy Project
        # with no running turn is exactly what "busy, and not a turn you can Stop" looks like.
        self._running: _TurnTicket | None = None

    def admit(self, ticket: _TurnTicket) -> bool:
        """Join the queue, taking the lock straight away if nothing else is asking for it."""
        with self._cond:
            self._waiting.append(ticket)
            return self._take(ticket)

    def _take(self, ticket: _TurnTicket) -> bool:
        """Caller holds `_cond`. True once this ticket owns the turn lock."""
        if self._waiting[0] is not ticket or not self._lock.acquire(blocking=False):
            return False
        self._waiting.popleft()
        self._running = ticket
        ticket.outcome = "ready"
        return True

    def wait(self, ticket: _TurnTicket) -> str:
        """Block on the client's own connection until this ticket runs, is cancelled, or wedges."""
        with self._cond:
            while ticket.outcome == "":
                if self._take(ticket):
                    break
                self._cond.wait(self._poll_s)
            return ticket.outcome

    def cancel(self, ticket_id: str) -> bool:
        """Drop one pending turn. Cancel is not Stop: what is running is not touched (ADR-0013)."""
        with self._cond:
            for ticket in self._waiting:
                if ticket.id != ticket_id:
                    continue
                ticket.outcome = "cancelled"
                self._waiting.remove(ticket)
                self._cond.notify_all()
                return True
        return False

    def fail_pending(self) -> int:
        """Fail every waiting turn at once, for a lock that is never coming back (#39).

        A wedge is the one exit that keeps the lock for the life of the process, so the turns behind
        it are waiting on a release that will not happen. Left alone they are held connections and a
        spinner; failed, each one says what happened and names the restart."""
        with self._cond:
            failed = len(self._waiting)
            for ticket in self._waiting:
                ticket.outcome = "wedged"
            self._waiting.clear()
            self._cond.notify_all()
            return failed

    def release(self) -> None:
        """Hand the turn lock back and wake whoever is next in line."""
        with self._cond:
            self._running = None
            self._lock.release()
            self._cond.notify_all()

    def ahead_of(self, ticket: _TurnTicket) -> int:
        """How many turns have to finish before this one starts, for the sentence it answers with.

        The turn holding the lock is one of them and is not on the deque, so the head ticket is
        behind one turn rather than none."""
        with self._cond:
            try:
                return self._waiting.index(ticket) + 1
            except ValueError:
                return 1

    def running(self) -> _TurnTicket | None:
        """The ticket that holds the lock, or None when whatever holds it never queued (#126)."""
        with self._cond:
            return self._running

    def depth(self) -> int:
        with self._cond:
            return len(self._waiting)


class AttachTooLarge(Exception):
    """Attaching a file would push the total attached size over the configured cap."""

    def __init__(self, cap: int, current: int, incoming: int) -> None:
        self.cap, self.current, self.incoming = cap, current, incoming
        super().__init__(f"attach would exceed cap: {current + incoming} > {cap} bytes")


class UploadUnavailable(Exception):
    """No writable dataset is mounted to receive an upload."""


class ResourceStillBound(Exception):
    """A Built App still records a Binding for this Resource, so membership cannot drop it.

    Carries `refs` — the app source that still uses it — for the same reason `unbind` reports them:
    "an app still needs it" is a refusal, and a refusal a creator cannot act on is a dead end. The
    files are what turns it into a next step.

    Carries the `apps` for the same reason one level up. A Project holds many Built Apps (ADR-0008),
    so the app that refuses is often not the one on screen, and "which app" is the first thing the
    creator has to know before the files mean anything.
    """

    def __init__(self, name: str, apps: list[str], refs: list[str] | None = None) -> None:
        self.name = name
        self.apps = list(apps)
        self.refs = list(refs or [])
        # Plural verb for a plural subject: the panel puts this sentence in front of the reader as
        # it stands, so the agreement has to be right here rather than in the markup.
        needs = "still needs" if len(self.apps) == 1 else "still need"
        super().__init__(f"{', '.join(self.apps)} {needs} {name}")


class ResourceNotBound(Exception):
    """The app records no Binding for this Resource, so there is no Scope on it to set (#142).

    Its own type rather than the `LookupError` `bind_data_source` raises, because the two refusals
    are two different sentences: that one means the platform will not offer the Resource, this one
    means the app does not depend on it yet. Collapsed into one code they would have to share a
    sentence, and a message naming two causes tells a creator which act to take about neither.
    """

    def __init__(self, resource_id: str) -> None:
        self.resource_id = resource_id
        super().__init__(f"no Binding for {resource_id}")


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


# Subfolders Sage writes uploaded bytes into. `uploads/` is current; `sensitive/` is kept so a
# file written by an older Sage can still be deleted as a Sage-managed upload. Both are
# Sage-created, so both are safe to delete; a genuine pre-existing dataset file is neither.
_SAGE_UPLOAD_PREFIXES = ("uploads/", "sensitive/")


def _is_sage_upload(entry: dict) -> bool:
    """A Sage-managed upload: bytes Sage wrote under a dataset's `uploads/` or `sensitive/` folder.
    True for `source=='upload'` and for such a file later re-attached from the dataset browser
    (source becomes 'dataset' but its dataset_rel_path still lives under one of those folders).
    These are safe to delete; a genuine pre-existing dataset file is not."""
    rel = str(entry.get("dataset_rel_path") or "")
    return entry.get("source") == "upload" or rel.startswith(_SAGE_UPLOAD_PREFIXES)


_SCRATCH_PREFIX = ".sage/scratch/"
# Where a Dataset file fetched FOR A QUESTION lands. Not `public/data/`: that tree is the published
# app's, and attach_file also writes the file into the committed manifest, so asking "what is in
# this file?" enrolled the bytes in every later publish of an app that may never reference them.
# Scratch is gitignored and already linked into the chat workdir, so the path in the turn prompt
# resolves where the agent stands. A SUBdirectory, because `_list_scratch_files` reads the top level
# only: the chip already represents this file, and it must not also appear as a Chat upload the
# person can delete — the bytes on the other end may be the Dataset's own.
_CHAT_DATA_PREFIX = f"{_SCRATCH_PREFIX}datasets/"


def _chat_data_dest(dataset_name: str, file_path: str) -> str:
    """Workspace-relative POSIX path a Dataset file is fetched to for a Chat turn."""
    rel = PurePosix(file_path.replace("\\", "/"))
    parts = [p for p in rel.parts if p not in ("", ".", "..")]
    return PurePosix(_CHAT_DATA_PREFIX.rstrip("/"), _slug(dataset_name), *parts).as_posix()


def _links_at(workspace: Path, rel: str, target: Path) -> bool:
    """True when an attached path is a symlink standing on `target` — the shape a handoff leaves
    behind when it hands the scratch bytes over instead of fetching them again."""
    p = workspace / rel
    try:
        return p.is_symlink() and p.resolve() == target.resolve()
    except OSError:
        return False


def _copied_bytes(root: Path) -> int:
    """Disk actually used under `root`. A symlink into a mount costs nothing and is not counted:
    the cap is about what Sage copied here, not about how large the Dataset is."""
    if not root.is_dir():
        return 0
    total = 0
    for p in root.rglob("*"):
        if p.is_symlink() or not p.is_file():
            continue
        total += p.stat().st_size
    return total


# The Domino Resource kinds that have a membership row of their own. Everything else a Thread can
# name is either not a Resource (a file, an Artifact) or a LEAF hanging off one of these — see
# `Orchestrator._join_project_on_mention`.
#
# The same four kinds in the UI's own id space are `SW.util.MEMBERSHIP_PARENT_KINDS` in
# `sage/workbench/js/util.js`; `SW.util.uiKind` is the mapping between the two spellings.
_MEMBERSHIP_PARENT_KINDS = ("dataset", "data_source", "llm_alias", "model_api")

# The two leaf ids that arrive carrying their PARENT's kind, so the kind alone cannot spot them:
# `table:<sourceId>:<dotted>` posts as `data_source`, `dsfile:<datasetId>:<relPath>` as `file`.
# Every other leaf is already refused by its kind.
_LEAF_ID_PREFIXES = ("table:", "dsfile:")

# What a catalogue row carries for the sake of the membership row and nothing else: the model
# picker reads `alias` and `reasoning_efforts` off the project's own resources when the Alias
# listing is unavailable, so a join without them writes an option that cannot be selected. A chip
# has no use for any of them, so they ride in on the mention and are taken back off before it is
# stored.
_MEMBERSHIP_ONLY_FIELDS = ("description", "alias", "capabilities", "reasoning_efforts")

# Set once `_backfill_membership_from_bindings` has reconciled this Project's working set with the
# Bindings that predate membership-on-bind (#140). In the Project's settings rather than derived
# from the data, because "already done" is what makes the repair a migration instead of the union
# on read ADR-0020 rejected.
_MEMBERSHIP_BACKFILLED = "membershipBackfilled"


def _bare_kind_id(value: str, kind: str) -> str:
    """Strip a `kind:` prefix from a membership id. Dataset `dataset:ds_1` and Data Source
    `data_source:abc` / `datasource:abc` all store the live id after the first colon."""
    s = str(value or "").strip()
    prefix = f"{kind}:"
    if s.startswith(prefix):
        return s[len(prefix):]
    if kind == "data_source" and s.startswith("datasource:"):
        return s[len("datasource:"):]
    return s


def _dataset_unique_name(item: dict, name: str) -> str:
    """The `dataset-<name>-<id>` handle for a context chip, or "" when the chip carries no id.

    Both halves are needed: the Domino data library rejects a bare name and a bare id alike. A chip
    with no id is worth an honest sentence, not a call that cannot succeed.
    """
    ds_id = _bare_kind_id(str(item.get("id") or ""), "dataset")
    return f"dataset-{name}-{ds_id}" if (name and ds_id) else ""


def _dataset_pseudo_path(item: dict) -> bool:
    """True when a Dataset file's `path` is really its resource id, not somewhere on disk.

    A Dataset file's resource id is `dsfile:<datasetId>:<relPath>`, and the client used to recover a
    missing path by stripping the first prefix — which yields `<datasetId>:<relPath>`, a string that
    looks like a path to every `if path:` in this file and is not one. One fabricated value silenced
    four things at once: add_thread_context skipped the auto-attach (it only runs when there is no
    path), _chat_context_line skipped the Dataset-library route (same condition) and told the agent
    to read a path that cannot exist, and the composer built its @token from it, so `@<id>:<file>`
    never matched the file it named. Rows written before the client stopped sending it are still on
    disk, so this is checked where a path is READ, not only where one is stored.
    """
    path = str(item.get("path") or "")
    rel = str(item.get("datasetRelPath") or "")
    ds_id = str(item.get("datasetId") or "")
    return bool(path) and bool(ds_id) and path == f"{ds_id}:{rel}"


def _list_scratch_files(workspace: Path) -> list[dict]:
    """Chat-local uploads that live in this workspace, not in a Dataset and not in git."""
    root = Path(workspace) / ".sage" / "scratch"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(root.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            out.append({
                "path": f"{_SCRATCH_PREFIX}{p.name}",
                "name": p.name,
                "size": p.stat().st_size,
                "source": "scratch",
            })
    return out


def _normalize_pin(kind: str, pin: dict) -> dict:
    """One Dataset file or one Data Source table, as stored on a membership parent."""
    pin = pin if isinstance(pin, dict) else {}
    ui = str(kind or "")
    if ui in ("dataset",):
        path = str(pin.get("path") or "").replace("\\", "/").strip().lstrip("/")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("path required")
        return {"path": path, "name": str(pin.get("name") or path.rsplit("/", 1)[-1])}
    if ui in ("datasource", "data_source"):
        table = str(pin.get("table") or "").strip()
        if not table:
            raise ValueError("table required")
        return {
            "database": str(pin.get("database") or ""),
            "schema": str(pin.get("schema") or ""),
            "table": table,
            "name": str(pin.get("name") or table),
        }
    raise ValueError("this Resource cannot pin leaves")


def _pin_key(pin: dict) -> tuple:
    if pin.get("path"):
        return ("file", str(pin.get("path")))
    return (
        "table",
        str(pin.get("database") or ""),
        str(pin.get("schema") or ""),
        str(pin.get("table") or ""),
    )


def _describe_context_file(workspace: Path, item: dict) -> str:
    """Shape summary for a file chip. Empty when the bytes are not here to read."""
    path = item.get("path")
    if not path:
        return ""
    try:
        p = Path(str(path))
        real = p if p.is_absolute() else _safe_join(workspace, str(path))
        d = describe(str(real))
    except (ValueError, OSError, TypeError):
        return ""
    return str(d.get("summary") or "").strip()


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

# Conversational openers that carry no request of their own. Stripped before the lead rules read the
# prompt, because every one of those rules looks at the FIRST word or anchors at ^ — so a single "ok"
# in front pushed the real lead out of view. Observed live 2026-08-24: "ok what data is there in
# @BigQuery_Demo" has no question mark and leads with "ok", so it was classified a build, gated, and
# the answer was written into the user's app instead of said to them — #29's failure, another door.
#
# Curated, like the lead sets themselves. No entry here is a build verb or an interrogative lead, so
# stripping can only expose a lead that was already in the sentence; it can never invent one. Only the
# strict rules read the stripped text (_INFO_LEAD, _INFO_ASK, _PLAN_FIRST) — _looks_like_question's own
# weak fallback still reads the raw prompt, so "ok do that" stays a build rather than becoming a
# question on the strength of "do". Affirmatives are safe to list because a bare approval typed while a
# plan is pending is caught by _looks_like_approval before any of this runs (see build_stream).
_LEAD_FILLER = re.compile(
    r"^(?:(?:ok(?:ay)?|so|now|well|alright|all\s+right|right|anyway|actually|also|and|but|"
    r"hey|hi|hello|yes|yeah|yep|sure|cool|great|thanks|thank\s+you|"
    r"btw|by\s+the\s+way|quick\s+question)[\s,.:;!-]+)+",
    re.IGNORECASE,
)


def _strip_lead_filler(text: str) -> str:
    """Drop conversational openers so the lead rules see the first REAL word of the prompt."""
    return _LEAD_FILLER.sub("", text.strip())


def _asks_about_a_change(prompt: str) -> bool:
    """True when a prompt asks us to DESCRIBE work rather than do it — "give me an architecture to add
    a real time queue", "how would you fix the race?". Consulted by both classifiers below, ahead of
    their build-verb scan.

    Without this, a build verb anywhere in the sentence wins, so Ask mode refused the single most
    natural thing to type into it: a design question that happens to name the change it's about."""
    text = _strip_lead_filler(prompt.lower())
    words = re.findall(r"[a-z']+", text)
    # Apostrophes stripped so "what's the best way to add caching" leads with "whats", like "what".
    return bool(words) and (words[0].replace("'", "") in _INFO_LEAD
                            or _INFO_ASK.match(text) is not None)


# An interrogative CONTENT clause: an information verb handing off to a question word. "tell me what
# information the table has" is a question with no question mark and no interrogative lead, so
# neither rule in _looks_like_question can see it — and the clause such a prompt usually trails
# ("...we'll then use it to build a dashboard") loses it outright to the build-verb veto. That is
# #29, observed live 2026-08-21: the answer to the question was written into the user's app.
#
# Unanchored, unlike _INFO_ASK, because the clause rarely opens the sentence — "explore the
# clickstream table AND TELL ME WHAT information it has". That is only safe with the ordering rule
# in _leads_with_a_question below; on its own it would also match "build a dashboard and tell me
# what you did", which is plainly a request for the dashboard.
_INFO_CLAUSE = re.compile(
    r"\b(?:tell|show|explain|describe)\s+(?:me|us)?\s*(?:about\s+)?"
    r"(?:what|which|how|why|whether|where|when|who)\b",
)

# The same vocabulary as _BUILD_VERB, as a pattern, because the ordering rule needs WHERE the first
# build verb is and the word-set membership test can only say whether one is present.
_BUILD_VERB_RE = re.compile(r"\b(?:" + "|".join(sorted(_BUILD_VERB)) + r")\b")


def _leads_with_a_question(prompt: str) -> bool:
    """True when an interrogative content clause sits BEFORE the first build verb.

    Consulted by both classifiers below, alongside _asks_about_a_change, so the two stay
    complementary — a prompt this claims must not also read as a change request in Ask mode.

    Order is the whole rule, and it is doing real work rather than papering over one transcript:

        "explore the table and tell me what it has, then we'll build a dashboard"  -> question
        "build a dashboard and tell me what you did"                               -> change request

    Same two fragments, opposite requests. Which one the user leads with is what separates them, and
    a build verb mentioned downstream of the question is the job the answer is FOR, not this turn's
    instruction."""
    text = prompt.strip().lower()
    info = _INFO_CLAUSE.search(text)
    if info is None:
        return False
    build = _BUILD_VERB_RE.search(text)
    return build is None or info.start() < build.start()


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
    if _PLAN_FIRST.match(_strip_lead_filler(prompt or "")):
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
    if _asks_about_a_change(prompt) or _leads_with_a_question(prompt):
        return True
    if any(w in _BUILD_VERB for w in words):
        return False
    return words[0] in _QUESTION_LEAD or text.endswith("?")


# Asking to throw the app away and start over (#36). Two shapes, both requiring the WHOLE app as the
# object: "start over"/"start from scratch" as a standalone phrase, or a removal verb reaching a
# whole-app noun ("delete everything", "wipe the app", "remove everything you have built").
#
# The match does NOT reset anything. It replies with the control and stops the turn, because a reset
# is destructive and a heuristic is the wrong thing to put in front of one — that is the shape of #29,
# where a misread prompt wrote an answer into the user's app. Erring wide is therefore cheap here: the
# worst a false positive costs is one turn that says "there's a button for this", which is a sentence,
# not a deleted app. A false NEGATIVE costs nothing new either — the request falls through and builds
# exactly as it did before this existed.
_RESET_PHRASE = re.compile(
    r"\bstart(?:ing)?\s+(?:over|again|from\s+scratch)\b"
    r"|\b(?:rebuild|build|redo|start|do)\s+(?:it|this|the\s+app|everything)?\s*"
    r"(?:over\s+)?from\s+scratch\b"
    r"|\b(?:delete|remove|clear|wipe|scrap|throw\s+away|get\s+rid\s+of)\s+"
    r"(?:all\s+of\s+)?(?:the\s+|this\s+|my\s+|your\s+)?"
    r"(?:everything|the\s+whole\s+app|the\s+entire\s+app|the\s+app|it\s+all|all\s+of\s+it)\b"
    r"|\breset\s+(?:the\s+|this\s+|my\s+)?app\b",
    re.IGNORECASE,
)


# The same words REPORTING a reset that already happened, which is not a request for another one.
# "i reset the app, build me the dashboard again" is a build request whose first clause happens to
# name the button — and it arrives at the one moment the user is most likely to type those words,
# immediately after using it. Erring wide is cheap in general (see above) but not here: this fires
# exactly when they have already lost the app once and are asking for it back.
#
# The subject is what separates the two. A request is imperative or modal — "reset the app", "can
# you reset the app", "i want to reset the app" — and none of those put a first-person subject
# directly in front of the verb. A report does: "i reset", "i've reset", "we already reset".
_RESET_REPORT = re.compile(
    r"\b(?:i|we)(?:'ve|'d)?\s+(?:have\s+|had\s+|just\s+|already\s+|then\s+)*"
    r"reset\s+(?:the\s+|this\s+|my\s+)?app\b",
    re.IGNORECASE,
)


def _asks_to_reset(prompt: str) -> bool:
    """True when the prompt asks for the app to be thrown away and started over.

    The reported clause is cut out rather than the whole prompt being waved through, so a prompt that
    reports one reset AND asks for another ("i reset the app, now delete everything") still matches
    on the part that is actually a request."""
    return bool(_RESET_PHRASE.search(_RESET_REPORT.sub(" ", _strip_lead_filler(prompt or ""))))


def _looks_like_change_request(prompt: str) -> bool:
    """True when a prompt asks for the app to CHANGE ("remove the dataset from the UI") rather than
    for information. Used only in Ask mode, to refuse the turn before it runs (see _ask_mode_refusal).

    An explicit build verb anywhere wins, exactly as it does in _looks_like_question, and both defer
    to _asks_about_a_change and _leads_with_a_question first — so the two agree on every prompt and
    one is never both."""
    if _asks_about_a_change(prompt) or _leads_with_a_question(prompt):
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


# The whole prompt says nothing but "try again" — used only while an approved plan is still live
# because its build gave up (see _looks_like_retry). Anchored end to end for the same reason
# _APPROVAL_ONLY is: "try again" retries that build, "try again with a bar chart" is a new request.
_RETRY_ONLY = re.compile(
    r"^(?:ok(?:ay)?|yes|yep|yeah|sure|please)?[\s,.!]*"
    r"(?:(?:please|now|then|just|lets|let's)\s+)*"
    r"(?:try|run|do|build|go)?\s*(?:it|this|that)?\s*"
    r"(?:again|one more time|once more|retry)[\s,.!]*$",
    re.IGNORECASE,
)


def _looks_like_retry(prompt: str) -> bool:
    """True when the whole prompt is the user saying "run that again".

    Only consulted while a plan the user ALREADY approved is still live because its build gave up
    without consuming it (see Workspace.read_plan_retry_step). There, "try again" means the
    build, not the plan: re-planning it proposes a second plan for a request the user has already
    approved, and the next failure proposes a third — the loop this exists to break.

    Never consulted while a plan is merely awaiting approval. "Try again" typed at a plan card is
    the user asking for a different plan, and that must keep gating. Conservative by construction,
    like _looks_like_approval: anything carrying content beyond the retry itself builds normally."""
    text = prompt.strip()
    if not text or len(text) > 40:
        return False
    return bool(_RETRY_ONLY.match(text)) and bool(re.search(r"[a-z]", text, re.IGNORECASE))


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


_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)


def _urls_in_chat(prompt: str, history: list | None = None) -> list[str]:
    """http(s) URLs the person named in this Thread, current message last-wins-deduped.

    Listed in the sage-chat prompt so a follow-up that does not repeat the link still has it."""
    found: list[str] = []
    seen: set[str] = set()
    texts = [str(ev.get("text") or "") for ev in (history or []) if ev.get("type") == "user"]
    if prompt and prompt not in texts:
        texts.append(prompt)
    for text in texts:
        for raw in _URL.findall(text):
            url = raw.rstrip(".,);]>\"'")
            if url and url not in seen:
                seen.add(url)
                found.append(url)
    return found


def _chat_wants_web(prompt: str, history: list | None = None) -> bool:
    """Chat arms internet access when this turn asked for the web, or an earlier user message
    in the Thread already did — so 'summarise that page' can still read the URL from last turn."""
    if _wants_web(prompt):
        return True
    return any(
        ev.get("type") == "user" and _wants_web(ev.get("text") or "")
        for ev in (history or [])
    )


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
                        is_approval: bool, skip_planning: bool) -> bool:
    """Whether to spend a model call asking scope.wants_a_plan about this turn.

    Every deterministic signal gets to decide first and for free — this only runs when none of them
    did. The conditions are all "the classifier could change the outcome":

      * `gate` already True — the turn plans regardless, so there is nothing to ask.
      * `answer_only` — the turn answers and stops, so there is no build to gate.
      * `is_approval` — the user has ALREADY seen a plan and pressed the button. There is nothing
        left to infer, and inferring anyway is not a wasted call, it is a broken one: a gated
        approval runs on `sage-plan`, which is read-only, so the approved build reads its way
        through the turn, writes nothing, and — being a gated turn that wrote nothing — answers with
        a SECOND plan for the work it was just told to do. Live on 2026-08-24: an approval logged
        `agent=sage-plan gate=True`, made nineteen tool calls without a single write, and came back
        with a fresh plan card. This condition used to be folded into `answer_only`, which does not
        hold it — _is_answer_only returns False for an approval on purpose ("an approval is the user
        asking to build, never an answer"), so the exclusion documented here never actually ran.
      * not `has_built` — the first-build gate has this turn; the hole opens only after it.
      * `skip_planning` — the project opted out of the automatic gate, and this IS the automatic
        gate, one turn later. Honouring the flag here is the same promise.
      * Auto only. Plan gates every turn already; Implement is the user saying "just build it", and
        Ask never builds. Auto is the mode that carries no explicit instruction, which is the whole
        reason it needs one inferred."""
    return (mode is Mode.AUTO and has_built and not gate and not answer_only
            and not is_approval and not skip_planning)


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


class _EventTap:
    """A turn's live event stream, drained by the turn loop without ever blocking it.

    The stream is a socket read that can sit quiet for a minute while a model thinks, and the turn
    loop has to stay responsive to Stop and to its own timeout the whole time. So the read lives on
    its own thread and hands events over a queue; the loop takes whatever has arrived and moves on.

    `ok` goes False the moment the stream fails or ends. That is the loop's signal to go back to
    reading the transcript every second — the poll is not dead code, it is the fallback, and it is
    also what ends the turn. A stream that never connects costs the turn nothing but a log line.
    """

    def __init__(self, client, sid: str, directory: str | None = None) -> None:
        opener = getattr(client, "session_events", None)
        # A driver that cannot stream is not an error. `ok` False from the first tick leaves the
        # turn loop on exactly the path it took before any of this existed.
        self._stream = opener(sid, directory=directory) if opener is not None else None
        self.ok = self._stream is not None
        self._q: queue.Queue = queue.Queue()
        # Whether this stream has ever produced a frame. `ok` only says the socket is up, and a
        # stream that connects and stays silent is the failure that hides: the turn keeps the fast
        # path, never reads the transcript, and goes to the quiet cap blind to its own progress.
        self.seen_any = False
        if self._stream is not None:
            threading.Thread(target=self._read, daemon=True, name="sage-chat-events").start()

    def _read(self) -> None:
        try:
            for ev in self._stream:
                self._q.put(ev)
        except Exception as e:  # any failure means "poll instead", never "fail the turn"
            log.info("chat: event stream unavailable (%s: %s) - polling the transcript instead",
                     type(e).__name__, e)
        finally:
            self.ok = False

    def drain(self) -> list:
        """Everything that has arrived since the last call. Never blocks."""
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                self.seen_any = self.seen_any or bool(out)
                return out

    def close(self) -> None:
        self.ok = False
        if self._stream is not None:
            self._stream.close()


# What Chat says it is doing, while it does it. None of this is kept: the Thread keeps the chart
# and the answer, not a tool log (see _CHAT_SHOWN_TOOLS). This is the spinner's subject.
#
# Sage tells the agent exactly how to reach a Data Source and how to read a Dataset file, so both
# arrive as bash rather than as tools of their own — and both are the slow ones. A turn that spent
# two minutes on a 5.5M-row query looked identical to a turn that had hung.
_CHAT_QUERY = re.compile(r"""get_datasource\(\s*['"]([^'"]+)['"]""")
_CHAT_DATASET_READ = re.compile(r"""download_file\(\s*['"]([^'"]+)['"]""")


def _chat_activity(tool: str, subject: str) -> tuple[str, str]:
    """(the kind of work, the thing it is working on) for a tool call Chat should name."""
    if tool in ("read", "write"):
        return tool, subject
    if tool == "bash":
        found = _CHAT_QUERY.search(subject)
        if found:
            return "query", found.group(1)
        found = _CHAT_DATASET_READ.search(subject)
        if found:
            return "read", found.group(1)
        return "bash", ""
    return "", ""


# A step OpenCode refused, rendered short enough to say to a person. Long enough to carry a
# provider's own sentence ("context length exceeded", "model not found"), short enough that it does
# not become the Thread.
_CHAT_ERROR_MAX = 300

# The gateway's own words for a guardrail refusal. It arrives buried: the gateway answers 400, the
# shim wraps that in a 502 whose message quotes the body, OpenCode quotes the shim, and the whole
# nest reaches the Thread as one string. The name is the only part worth keeping.
_GUARDRAIL = re.compile(r"Blocked by guardrail:\s*([^\"'}\\]+)")


def _guardrail_sentence(text: str, attachments: list[dict] | None = None) -> str:
    """A refusal by a gateway guardrail, said plainly — or "" when this is some other failure.

    A guardrail block is not a fault, and it is not Sage's: the gateway was asked to police what
    passes through it and it did. What the raw text cannot say is the part people get wrong, which
    is WHERE it looked. Live, a Thread asked what was in an attached JSON file of sales forecasts,
    and a "Block phone numbers" guardrail refused the turn: three predictions ran over a million,
    and their seven-digit integer parts matched a pattern that accepts a decimal point as its right
    boundary. Nobody had typed a phone number, and the file contained none.

    The refusal then outlived the turn. The value reached the gateway as a tool result, so it was in
    the Conversation's Recall, and Recall is sent again on every turn — every later turn was refused
    too, on a value from an earlier one. Live, the next question attached a different file with
    nothing in it that could match, and it was refused; the same file in a new Conversation was
    answered.

    What this says is therefore only half the answer. It names the suspect, which is the half the
    person can act on and the half that was missing: the file is theirs, the values are theirs, and
    nobody could see which file had done it. The other half — the way out of a Conversation that
    will now refuse everything — is an offer, not a sentence, and it is `recall.offer` that decides
    when to make it. Naming a way out here as well would put it in front of someone whose first
    refusal may have been a blip.

    No resolution is offered beyond the file and the administrator, because Sage has no others: it
    cannot edit the gateway's policy and it will not quietly redact someone's data to get past one.
    """
    match = _GUARDRAIL.search(text)
    if not match:
        return ""
    name = " ".join(match.group(1).split()).strip(" .;:")
    read = _named_files(attachments)
    # The gateway's sentence verbatim, ours around it (ADR-0014). What is dropped is the transport
    # wrapper it arrived in — three levels of quoted JSON — not a word the gateway wrote.
    return (f'the gateway refused it: "Blocked by guardrail: {name}". Guardrails read everything a '
            "turn carries, including the contents of files it opened, not only what you typed. "
            + (f"This turn read {read}. Take the matching values out of it, "
               if read else "Take the matching values out, ")
            + "or ask your Domino administrator about the policy.")


def _named_files(attachments: list[dict] | None) -> str:
    """The turn's Attachments, said as prose — the suspects a refusal can point at.

    Attachments rather than every file the turn read, for two reasons that agree. They are already
    in the user's message, so the transcript already holds them and nothing new has to be recorded;
    tool inputs are deliberately not kept (`chat_summary` calls them "transcript furniture"). And
    they are the files the person CHOSE, so they are the ones they can go and change. A workspace
    file the agent opened on its own is noise in a sentence asking someone to act.
    """
    names = [str(a.get("name") or "").strip() for a in (attachments or []) if a.get("name")]
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _error_raw(err: object) -> str:
    """The failing frame's own words, before Sage says anything about them.

    Split out because two callers want it and they want different things from it: the sentence
    below is shown, while `recall.reason_key` reads this to decide whether two refusals are the
    same refusal. Keying that off the shown sentence would be wrong — it names the turn's
    Attachment, which is precisely what differs between the turns the ladder has to connect.
    """
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        data = err.get("data") if isinstance(err.get("data"), dict) else {}
        return str(data.get("message") or err.get("message") or err.get("name") or "")
    return ""


def _chat_error_text(err: object, attachments: list[dict] | None = None) -> str:
    """One plain line for a step that failed, or "" when the frame carries nothing to say.

    The frame carries whatever said no, in whatever shape it said it: a bare string, or a dict with
    the sentence under `data.message` (OpenCode's own wrapper) or `message`. `name` is the last
    resort — a class name is a poor sentence, but it beats reporting silence, which is what Chat
    did with these frames before.
    """
    text = _error_raw(err)
    # Before the clip, not after. The live nest put the name at character 200 of 302, so the clip
    # spared it — by 59 characters, on the shortest of the two gateway URL forms in use. A longer
    # host, app path or guardrail name eats that margin, and translating first costs nothing.
    return _guardrail_sentence(text, attachments) or " ".join(text.split())[:_CHAT_ERROR_MAX]


def _chat_live_event(ev) -> dict | None:
    """One AgentEvent off the live stream -> a Chat SSE event, or None to drop it.

    Text becomes `delta`, a type the transcript never contains: these are the turn as it happens,
    not the record of it. The record is still the single text event written at the end from the
    transcript, so history.jsonl replays exactly as it did before this existed and a client that
    ignores `delta` sees the turn it has always seen.

    `final` carries the WHOLE text rather than the last fragment, because /event cannot be replayed:
    a dropped frame leaves the live copy short, and this is the event that makes it whole again.

    bash becomes the tool event the poll loop already yields, so "Running Python…" shows up when the
    command starts instead of when it finishes. Everything else is dropped: the other tools stay off
    Chat's Thread (see _CHAT_SHOWN_TOOLS), and step and error frames are the poll loop's business.
    """
    if ev.kind == "message":
        if ev.payload.get("final"):
            # Marker out of the live copy too, not only out of what is kept: this event is what
            # repairs the streamed text, so leaving it in would paint NO_BUILD_MARKER under the
            # answer and hold it there until the transcript event replaces the block.
            text, _ = _take_no_build_marker(str(ev.payload.get("text") or ""))
            return {"type": "delta", "text": text, "final": True}
        text = str(ev.payload.get("delta") or "")
        return {"type": "delta", "text": text} if text else None
    if ev.kind == "tool_run":
        tool = str(ev.payload.get("tool") or "").lower()
        # A label has to stop being true when the work stops. "Reading clickstream.csv" left
        # standing through a minute of thinking names the wrong thing and never moves, which is
        # the thing that reads as a hang. So a finished call clears it, and so does a call whose
        # tool Chat does not name.
        if ev.payload.get("status") != "called":
            return {"type": "agent", "kind": "tool", "doing": "idle"}
        inp = ev.payload.get("input") if isinstance(ev.payload.get("input"), dict) else {}
        if tool == "bash":
            # Measured: bash arrives as tool.called with the command inside `input`, and the
            # shell.started frame that carries `command` at the top level never fired at all. Read
            # both rather than pick — the one that looked obvious is the one that was never sent.
            subject = str(ev.payload.get("command") or inp.get("command") or "")
        else:
            # Measured live: read and write both send `path`. `filePath` is kept because the
            # transcript's own parts use it (see _tool_detail) and this has to agree with them.
            subject = str(inp.get("path") or inp.get("filePath") or "")
        doing, subject = _chat_activity(tool, subject)
        if not doing:
            return {"type": "agent", "kind": "tool", "doing": "idle"}
        return {"type": "agent", "kind": "tool", "tool": tool, "doing": doing, "detail": subject}
    return None


# Chat's Thread shows the chart/table, not a tool log — this set stays empty on purpose. What the
# agent is doing is said while it is doing it and then let go of (see _chat_activity): a card
# reading "Ran write examples/revenue.png" above the chart it produced is the same event twice, and
# it would have to survive a reload to be worth keeping at all.
_CHAT_SHOWN_TOOLS = frozenset()
_CHAT_AT = re.compile(r"@([^\s@]+)")


def _scope_label(scope: dict) -> str:
    return ".".join(
        str(p) for p in (scope.get("database"), scope.get("schema"), scope.get("table")) if p
    )


def _at_token_hits(token: str, name: str, path: str) -> bool:
    """True when an @token from the user message names this context file."""
    t = token.lower().lstrip("@")
    if not t:
        return False
    names = {name.lower(), Path(path).name.lower(), Path(name).name.lower()}
    names.update(n.replace(" ", "_") for n in list(names))
    stems = {n.rsplit(".", 1)[0] for n in names if "." in n}
    return t in names or t in stems


def _chat_context_line(item: dict, *, file_note: str = "") -> str:
    """One Session-context row for sage-chat: identity, and how its contents can be reached.

    A Dataset without a mount is still readable — through the Domino data library, which is how
    every Dataset shared from another project is reached, since a mount only ever covers this one.
    What the row must not do is name a Dataset and leave the route to it unsaid: that is what let
    the agent grep this git repo for a similarly named folder and answer about the wrong thing.
    A table chip names Scope and columns, not rows.
    """
    kind = str(item.get("kind") or "resource")
    if kind == "table":
        kind = "data_source"
    # A table chip carries the TABLE in `name` — the panel pins a table, and the client sends it
    # under kind "data_source". `get_datasource()` takes the SOURCE, so `sourceName` (stamped by
    # add_thread_context from the chip's own parent id) is the only field that answers it. Reading
    # `name` here told the agent to call get_datasource('clickstream') for a source named
    # BigQuery_Demo, and Domino answered, correctly, that no Data Source goes by that name.
    source_name = str(item.get("sourceName") or item.get("subtitle") or "").strip()
    name = source_name or str(item.get("name") or item.get("id") or "unnamed")
    path = None if _dataset_pseudo_path(item) else item.get("path")
    project = item.get("project")
    scope = item.get("scope") if isinstance(item.get("scope"), dict) else None
    if kind == "dataset":
        where = f" (project {project})" if project else ""
        # The noun is ours and the name is theirs, in one sentence: the noun is a pack token, and
        # every name, path and identifier rides in as a value, which the helper does not scan again
        # (ADR-0014). A Dataset somebody called `{dataset}` therefore comes through as they wrote it.
        if path:
            return brand.text(
                "- {dataset} {name}{where}, files at {path}. Read those files. "
                "Do not search the rest of this workspace for a substitute.",
                name=name, where=where, path=path,
            )
        unique = _dataset_unique_name(item, name)
        if not unique:
            return brand.text(
                "- {dataset} {name}{where}. {assistantName} has no identifier for it, so its files "
                "cannot be read this turn. Tell the person that. Do not search this git repo or any "
                "other folder for a project of the same name — that is not this {dataset}.",
                name=name, where=where,
            )
        return brand.text(
            "- {dataset} {name}{where}. Not mounted here, so read it with the {platformName} data "
            'library: `from domino_data.datasets import DatasetClient` then '
            '`DatasetClient().get_dataset("{unique}")`. '
            '`.list_files()` names its files and `.download_file(<file>, "/tmp/<file>")` fetches '
            "one to read with pandas. Do not search this git repo or any other folder for a "
            "project of the same name — that is not this {dataset}.",
            name=name, where=where, unique=unique,
        )
    if kind in ("file", "artifact"):
        extra = f" at {path}" if path else ""
        at = f" (@{Path(path).name})" if path else (f" (@{name.replace(' ', '_')})" if name else "")
        extra = extra + at
        if not path and item.get("datasetId"):
            ds = item.get("datasetName") or item.get("datasetId")
            rel = item.get("datasetRelPath") or name
            unique = _dataset_unique_name(
                {"id": item.get("datasetId")}, str(item.get("datasetName") or "")
            )
            if not unique:
                return brand.text(
                    "- file {name} in {dataset} {ds} ({rel}). {assistantName} has no identifier for "
                    "that {dataset}, so this file cannot be read this turn. Tell the person that. "
                    "Do not search this git repo for a substitute.",
                    name=name, ds=ds, rel=rel,
                )
            return brand.text(
                "- file {rel} in {dataset} {ds}. Not mounted here, so fetch it with the "
                "{platformName} data library: `from domino_data.datasets import DatasetClient` then "
                '`DatasetClient().get_dataset("{unique}").download_file("{rel}", "/tmp/{name}")`, '
                "then read /tmp/{name} with pandas. Do not search this git repo for a substitute.",
                name=name, ds=ds, rel=rel, unique=unique,
            )
        line = f"- {kind}: {name}{extra}"
        if file_note:
            return f"{line}. {file_note}"
        if path:
            return (
                f"{line}. Read that file. Do not search the rest of this workspace for a substitute."
            )
        return line
    if kind in ("data_source", "datasource"):
        # No resolved source name, no recipe. When a table is scoped, `name` is the table, so
        # guessing with it sends the agent at a lookup that cannot succeed — and it comes back as
        # "no Data Source registered under that name", which reads like the person attached the
        # wrong thing. Saying "cannot query" is worse to read and far better to act on.
        if scope and scope.get("table") and source_name:
            dotted = _scope_label(scope)
            cols = item.get("columns") if isinstance(item.get("columns"), list) else []
            col_txt = ", ".join(
                " ".join(
                    str(p) for p in (
                        (c.get("name") if isinstance(c, dict) else None),
                        (c.get("type") if isinstance(c, dict) else None),
                    ) if p
                )
                for c in cols[:40]
            ).strip()
            extra = f" Columns: {col_txt}." if col_txt else ""
            return brand.text(
                "- {dataSource} {name}, table {dotted}.{extra} Query it with "
                "`from domino_data.data_sources import DataSourceClient` then "
                '`DataSourceClient().get_datasource({quoted}).query('
                '"SELECT * FROM {dotted} LIMIT 50").to_pandas()`. '
                "Do not search files, env, or /opt/sage for credentials. Do not invent rows. "
                "If the query errors, tell the person.",
                name=name, dotted=dotted, extra=extra, quoted=repr(name),
            )
        extra = f" at {path}" if path else ""
        return brand.text(
            "- {dataSource} {name}{extra}. This workspace cannot query it live. Do not invent rows. "
            "Say that you cannot open it.",
            name=name, extra=extra,
        )
    extra = f" at {path}" if path else ""
    return f"- {kind}: {name}{extra}"


# The one way an agent may end a turn without editing `src/`. AGENTS.md holds that rule absolutely —
# "a turn that produces no file edits has accomplished nothing" — because the failure it was written
# against is an agent that plans, stalls and calls that a turn. But an absolute rule has no room for
# a request that cannot be acted on at all, and an agent that may not decline and may not stop has
# only one move left: write its own explanation into the app. That is #29 — a creator ended up with
# a dashboard whose UI said the file they attached wasn't showing up.
#
# A marker is what lets both rules stand. The src/ requirement stays absolute and the exception is
# something the agent must CLAIM, out loud, in a token it would not emit by accident — so "nothing to
# build" and "stopped at a plan" stay distinguishable here, which they are not from the outside.
NO_BUILD_MARKER = "NOTHING_TO_BUILD"
# Its own line, per AGENTS.md, with the wrappers a model reaches for unprompted (backticks, bold)
# tolerated — a turn that ends correctly must not be re-classified as a stall over a pair of
# backticks. Not tolerated mid-sentence: that is where a model QUOTES the marker while explaining
# itself, and reading that as a claim would hand every agent an accidental way out of the rule.
_NO_BUILD_LINE = re.compile(
    rf"^[ \t]*[`*_]*{NO_BUILD_MARKER}[`*_]*[ \t]*$\n?", re.MULTILINE)


def _take_no_build_marker(text: str) -> tuple[str, bool]:
    """Split one assistant text part into the prose to show and whether it claimed nothing to build.

    Stripped rather than shown. It is a signal addressed to Sage, and the user reads every word the
    agent says — a bare NOTHING_TO_BUILD sitting under a friendly explanation reads as a leaked
    error code, which is a smaller version of the same defect this whole mechanism exists to fix."""
    stripped = _NO_BUILD_LINE.sub("", text)
    return stripped, stripped != text


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


_PLAN_HEADING = re.compile(r"^#{1,6}[ \t]*plan\b", re.IGNORECASE)
_PLAN_STEP = re.compile(r"^[ \t]*(?:\*\*[ \t]*)?\d{1,2}[.)]")


def _count_plan_steps(plan_md: str) -> int:
    """How many steps the `## Plan` section lists, for the pin's one-line subtitle.

    Not `plan_steps.parse_steps`, which is the phased-build parser and drops any step without a
    `Do`/`Done when` field — an ordinary plan has neither, so it would count zero. This counts what
    a reader counts: numbered lines under the Plan heading, stopping at the next heading so
    `## Open questions` bullets do not join in.
    """
    steps, inside = 0, False
    for line in (plan_md or "").splitlines():
        if line.lstrip().startswith("#"):
            inside = bool(_PLAN_HEADING.match(line.strip()))
            continue
        if inside and _PLAN_STEP.match(line):
            steps += 1
    return steps


def _viewer_id() -> str:
    """Who a plan document is authored by. A turn runs outside any request, so there is no viewer
    JWT to read here — this is the same container-identity fallback `/api/me` uses when extended
    identity forwards nothing, which is the Sage Builder case and so the usual one."""
    return os.environ.get("DOMINO_USER_ID") or "me"


def _thread_plan_id(record: ProjectRecord, thread_id: str) -> str:
    """The plan document this Conversation produced, or "" if it has produced none.

    Read off the document's own record of where it came from, not off the Thread's handoff row:
    both entry paths stamp the origin, but only one of them hands off (#54). A plan the Build gate
    wrote has no handoff behind it, and reading the handoff row left it unreachable from the very
    Conversation that asked for it.

    Newest first, because one Conversation may produce several — a Thread that hands off twice, a
    Build conversation the gate fires in again — and the plan card is about the newest. It also
    cannot name a document that is no longer there, which the handoff row could: a reset takes the
    documents and leaves the row pointing at one of them.
    """
    for doc in record.list_plan_docs():  # newest first
        if str(doc.get("originThreadId") or "") == thread_id:
            return str(doc.get("id") or "")
    return ""


def _approve_prompt(plan_md: str, answers: str, *, handoff_note: str = "") -> str:
    """The Implement-turn prompt built from an approved plan (SPEC P6): the plan is fed in as
    context so the build turn constructs exactly what the user signed off on."""
    parts = ["The user approved this plan. Build the app it describes now — implement it, don't re-plan.",
             "", "## Approved plan", plan_md]
    if answers.strip():
        parts += ["", "## Answers to the open questions", answers.strip()]
    if handoff_note.strip():
        parts += ["", handoff_note.strip()]
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
# The end-of-turn scan's answer about the Bindings (#93). Gitignored where it is written — see
# Workspace.usage_path for why this one is not a committed manifest like the two beside it.
_USAGE_PATH = ".sage/usage.json"
# Sage's own files inside the app tree name every bound Resource by definition, and Sage rewrites
# them itself whenever the Bindings change (_write_app_resources), so a Binding found in one is not
# a dangling reference anybody has to act on. Excluded from _resource_usage for that reason:
# reporting them would send the creator to edit files AGENTS.md tells the agent never to touch.
# WHICH files those are is the app's own answer rather than a constant here — an app seeded before
# #119 calls them `sage*` — so the set is `Workspace.helpers.owned`, resolved per app (#119).


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


def _workspace_relative(text: str) -> str:
    """A path as the user knows it — `src/App.tsx`, not the container's /workspaces/ prefix.
    Anything that is not a path comes back unchanged."""
    return text.split("/workspaces/", 1)[-1] if "/workspaces/" in text else text


# The input field that names what a call acted on, tried in order, for every tool that has no
# branch of its own below: glob and list carry a pattern or a path, skill a name, webfetch a url.
# A list of keys rather than a list of tools because the tools are OpenCode's to change — a
# version that adds one still names its subject here instead of rendering a card with nothing in it.
_SUBJECT_KEYS = ("pattern", "path", "filePath", "url", "name", "query", "description")


def _tool_detail(tool: str, part: dict) -> str:
    """A short, human label for a tool call (the file it touched, the command it ran) so the UI
    can render dyad-style action cards instead of a bare tool name. Best-effort; '' when unknown."""
    inp = (part.get("state") or {}).get("input") or {}
    if tool in ("edit", "write", "read"):
        path = inp.get("path") or inp.get("filePath") or ""
        return _workspace_relative(path)
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
    for key in _SUBJECT_KEYS:
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            return _workspace_relative(value.strip())
    return ""


def _tool_duration_ms(part: dict) -> int | None:
    """How long a tool call took, in ms, or None when OpenCode did not time it.

    A completed tool part carries `state.time = {start, end}` as epoch ms. None rather than 0 is
    the whole point: the card used to print a hardcoded "0.0s" on every row, which reads as "this
    took no time at all" when the truth is "nobody measured it".
    """
    t = (part.get("state") or {}).get("time") or {}
    start, end = t.get("start"), t.get("end")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return None
    ms = int(end - start)
    return ms if ms >= 0 else None


def _app_display_name(workspace: Workspace, fallback: str | None = None) -> str:
    """What to call one Built App.

    The name somebody gave it, else the title of the plan it was built from, else `fallback`: a
    rail row with no words on it is not a row anybody can pick. The plan title is what makes the
    name start as the plan's (ADR-0008) without a rename having to be written at birth.

    Publish passes the Domino project's name as `fallback`, because that is what it used to name
    every App and is the better answer on the deployment side for an app nobody has named.
    """
    stored = workspace.display_name()
    if stored:
        return stored
    # `plan_title` answers "App" for a plan it cannot read a title out of, which is a fine default
    # for a card about one plan and a poor name for a row you pick between several — so an app with
    # no plan at all is named for what it is instead of borrowing that. "Built App" in full, and
    # not "Untitled": CONTEXT.md keeps `App` for the Domino thing and `Untitled` away from names.
    plan = workspace.read_plan() or workspace.read_archived_plan() or ""
    if plan.strip():
        return chat_handoff.plan_title(plan)
    # Resolved here rather than in the signature: a default evaluated at import would freeze the
    # pack the process booted with, and a caller's own fallback is the Domino project's name — a
    # name somebody chose, which is never rewritten.
    return fallback if fallback is not None else brand.text("Unnamed {builtApp}")


def _app_change_event(workspace: Workspace) -> dict:
    """The record that a turn changed an app, attached to the app it changed (#56, #83).

    Written server-side and blind to the viewer's conversation view: the block belongs to the build
    turn, and both views render it. What one of them adds is the FOLDING — Chat's merged read
    collapses a run into one row whose face is built from these — and that is a view over these
    blocks, not a second kind of block beside them.

    The name is carried rather than looked up later, because it is a then-fact: a run from six weeks
    ago names the app as it was called then, and a rename since is not something that run did.
    Whether the app is published today is the opposite kind of question, so it is deliberately NOT
    here — the reader asks the rail for that.
    """
    return {"type": "app_change", "appId": workspace.app_id, "name": _app_display_name(workspace)}


def _crossing_minted_app(workspace: Workspace, conversation: str) -> bool:
    """Did this conversation's crossing MAKE this app, or find it already standing?

    The answer is the crossing's own receipt (#60) — `newApp` on the plan card the handoff wrote
    into this app's log under this conversation — read back rather than recomputed, because by the
    time a build turn asks, `apps/` holds the app either way. It is what separates the rail's
    "Built X" tag from "Changed X", and it does not change with later turns.
    """
    return any(
        isinstance(row.get("crossed"), dict) and row["crossed"].get("newApp")
        for row in workspace.read_history(conversation)
    )


@dataclass
class Project:
    id: str
    # The Built App on screen: `apps/<appId>/` on the volume, and everything inside it. The person
    # may point this at another app while a turn is running (#77), so a turn asks `app_for_turn()` for
    # the app it writes into rather than reading this.
    workspace: Workspace
    # The Project's own record — Threads, plan documents, settings, sessions — at the volume root,
    # which is also the git repo root. Two surfaces, two directories (ADR-0008): ask this one for
    # what the Project owns, `workspace` for what the app owns, and neither for the other's.
    record: ProjectRecord
    supervisor: ViteSupervisor
    queries: PreviewQueries
    control: ModelControl
    shim: EnforcementShim
    session_id: str | None = None
    # The session a turn is CURRENTLY streaming into. Normally session_id, but a phased build runs
    # each phase in its own throwaway session, and two things must follow the live one rather than
    # the project's: Stop (interrupting the idle project session would leave the phase generating),
    # and the `sage-session` cost tag (tagging every phase with the project session collapses their
    # spend into one bucket — which is exactly the per-phase breakdown a phased build exists to be
    # judged on). None between turns.
    active_session_id: str | None = None
    # The Build conversation the current turn belongs to. Build used to be one session and one
    # transcript per project; it is now per conversation, the way Chat already was, so that "New
    # conversation" in the rail means something (see docs/workbench/handoff.md). Set at the top of
    # every public build entry, read by the append_history calls that persist the turn. None means
    # an unscoped caller (CLI, tests) — it still builds, it just owns no conversation.
    build_conversation: str | None = None
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
    # The Built App a turn in flight is writing into, and the attachments linked into it, pinned at
    # the top of the turn and dropped at its end. Switching Built App does not take the turn lock
    # (#77), so `workspace` and `attached` follow the app the person SELECTED — that is what the
    # rail, the preview, the transcript and every panel show. These hold the app the turn began in,
    # so the build, its revert and its end-of-turn repairs all land in the tree they started in
    # rather than in the one that appeared under them. None between turns.
    turn_app: Workspace | None = None
    turn_attached: list[dict] | None = None
    # Where the UI sends a user to read what this project has cost, and the tag value to filter by
    # once they land. Both None off-Domino (or in fake/openai gateway mode), which hides the link —
    # a dead link to a dashboard that has no Sage data reads as a bug.
    cost_url: str | None = None
    cost_project: str | None = None
    # Where the platform bar sends a user to Manage — a Domino App of its own, outside this
    # Workbench. None off-Domino, which hides the link for the same reason `cost_url` does.
    manage_url: str | None = None

    # Workspace-relative prefixes the PROJECT owns rather than the app: Chat's Artifacts and
    # scratch, and the Threads and plan documents beside them. Everything else a caller names is
    # the app's, and resolves inside `apps/<appId>/`.
    _PROJECT_PREFIXES = ("examples/", _SCRATCH_PREFIX, ".sage/threads/", ".sage/plan-docs/")

    def app_for_turn(self) -> Workspace:
        """The Built App a turn writes into: the one it pinned at its start, else the one on screen.

        Ask this anywhere a turn acts — writing code, reverting it, appending to the log, repairing
        attachments afterwards. Ask `workspace` for what the person is looking at."""
        return self.turn_app or self.workspace

    def attachments_for_turn(self) -> list[dict]:
        """The attachment list that belongs to `app_for_turn()`, the same way round."""
        return self.attached if self.turn_attached is None else self.turn_attached

    @property
    def snapshot(self) -> TurnSnapshot:
        """Turn-scoped revert over the app being built. Derived rather than stored: a TurnSnapshot
        is two paths and a `git --git-dir` call, and a stored one is a third thing that has to be
        kept in step with the app a switch just changed."""
        return TurnSnapshot(self.app_for_turn().path)

    def root_for(self, rel: str) -> Path:
        """Which of the two directories a workspace-relative path is written against.

        The UI hands back paths it was given — a Chat Artifact, an attached data file, a source
        file in the code view — and they are all shaped the same. This is what tells them apart.
        """
        clean = str(rel or "").replace("\\", "/").lstrip("/")
        return self.record.path if clean.startswith(self._PROJECT_PREFIXES) else self.workspace.path

    def repo_rel(self, rel: str, app: Workspace | None = None) -> str:
        """An app-relative path, as the Project's git repo names it: `apps/<appId>/<rel>`.

        Git runs at the Project root, so every path handed to it — an exclude, an untrack — has to
        be written from there, while everything else in the orchestrator is app-relative. Called
        with an empty `rel` it names the app's directory itself.

        `app` names one other than the app on screen. Only the turn's own git excludes need it: a
        build that carried on after a switch (#77) is committing a tree nobody is looking at."""
        return f"{(app or self.workspace).path.relative_to(self.record.path).as_posix()}/{rel}"

    def status(self) -> dict:
        s = self.control.snapshot()
        try:
            upstream = self.supervisor.upstream()
        except RuntimeError:
            upstream = None
        return {
            "id": self.id,
            "name": self.record.display_name() or self.id,
            "untitled": self.record.is_untitled(),
            "workspace": str(self.workspace.path),
            "preview_upstream": upstream,
            "attached": list(self.attached),
            "scratch": _list_scratch_files(self.record.path),
            "model": {
                # `mode` is what routes right now — the pin, while a turn is running (see
                # arm_turn_mode). `selected_mode` is where the user's picker actually sits, which is
                # what the picker must render: otherwise a mode changed mid-turn snaps back to the
                # pinned one on the next poll and looks like the click was dropped.
                "mode": s.mode.value,
                "selected_mode": self.control.selected_mode.value,
                "phase": s.phase.value,
                "picked_model": s.picked_model,
                "chat_model": s.chat_model,
                "reasoning_effort": s.reasoning_effort,
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
            "manage": self.manage_url,
        }


def _warn_if_shapeless(where: str, plan_md: str) -> None:
    """Say so when a plan comes back with none of the headings its document is parsed from.

    Such a plan still builds — the text is all there — but parse_sections has nowhere to put it, so
    every section comes back empty and the plan page can only show prose. Three prompts now ask for
    the shape (the sage-plan agent, the gated turn, the handoff turn), so this should be rare. This
    line is how we find out whether it is, before anyone pays for a repair turn to fix it.
    """
    if not any(plan_doc.parse_sections(plan_md)["sections"].values()):
        log.warning("%s: the plan has none of the document's headings — its page can only show prose",
                    where)


# The plan's voice and shape. Module level because two turns write plans: the gated build turn
# (build_stream) and the Chat -> Build handoff (_draft_handoff_plan). Both must ask for the same
# headings, because both produce a plan document that is parsed out of them (plan_doc.SECTIONS).
# The architecture shape stays inside its turn — only one turn writes designs.
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
# Every plan shape opens the same way, and the plan document's sections sit between that
# opening sentence and '## Plan'. Composed rather than repeated so the step list — the part
# plan_steps.parse_steps and _count_plan_steps read — stays identical in both shapes.
# The heading is asked for FIRST because it is the app's name, and until it was asked for nothing
# ever wrote one. The opening sentence was read as the name instead, by the plan card, the plan
# document and the Built App's row in the rail — so an app went into the rail called "- This app
# will be an AI consumption dashboard for exploring daily usage, spend,". Deriving a name from that
# sentence is not something code can do; asking the writer for one is a line of prompt.
_PLAN_OPENER = ("Format it exactly like this, in Markdown, and write nothing outside it:\n"
                # The example carries no app-shape word on purpose. The Control rule below keys on
                # the data's shape and never on what an app is called, and a noun in an example is
                # the easiest way to teach the opposite (see test_a_dashboard_ships_with_a_control).
                "- A '# ' heading naming the app in 2-4 words. This is its NAME, the way a product "
                "has one — 'Support Ticket Explorer', not 'An app for looking through support "
                "tickets'. No leading 'A' or 'The', no trailing full stop, and never a sentence: it "
                "is shown as a title, in a list beside other apps, and in a header that "
                "capitalises it.\n"
                "- Then one short sentence saying what the app is.\n")
# The sections a colleague reads to decide whether the app is worth building, and the
# durable half of the plan document. Kept short on purpose: the plan still has to be
# skimmable in the approval card, so each section is a line or a few bullets, not an essay.
_PLAN_DOC_SECTIONS = (
    "- Then a '## Problem & outcome' heading and one or two sentences: what is wrong today, "
    "and what is true once the app exists.\n"
    "- Then a '## Who uses this' heading and one sentence naming the person who opens it.\n"
    "- Then a '## What it does' heading and short bullets, one capability each.\n"
    "- Then a '## Screens' heading and one bullet per screen: a bolded name, then ' — ', "
    "then one sentence on what it shows.\n"
    "- Then a '## Not doing' heading and short bullets naming what is deliberately out of "
    "scope. Leave the heading out entirely if nothing is.\n"
    "- Then a '## Done when' heading and short bullets, each one an observable result "
    "someone can check without reading the code.\n")
_PLAN_SHAPE = (_PLAN_OPENER + _PLAN_DOC_SECTIONS +
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
    _PLAN_OPENER + _PLAN_DOC_SECTIONS +
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

# What a Build turn is told to DO with the Chat half of its Conversation (#53). The transcript
# itself is rendered by chat_compact.chat_summary; this is the framing, which lives here with the
# turn's other preambles.
#
# The last sentence is the load-bearing one. Handed a conversation with no instruction about it, a
# model reads the questions in it as a backlog and starts answering them — which on a build turn
# means building things nobody asked for on this turn. The background is here to resolve pronouns,
# not to be worked through.
_CHAT_CONTEXT_PREAMBLE = (
    "Earlier in this same conversation the person was talking to you in Chat, and this is the tail "
    "of what you both said. It is background for the request above: when the request points at "
    "something without naming it — \"that chart\", \"the table we looked at\" — this is what it "
    "points at. It is not a list of work to do. Build only what the request above asks for.")

# What the agent is told about an @mention this turn dropped (#130). `{said}` is filled with the
# sentence `_unusable_mentions` already put on the screen, so the person and the agent are never
# told two different things about the same gap.
#
# Before this, only the screen was told. The agent received the request with the mention silently
# removed, which leaves an ordinary-looking hole — and a model fills one of those: it invents a
# file, quietly uses a Resource this app IS bound to, or writes placeholder data around the gap and
# reports a clean build. The person then reads a green turn built on nothing they named.
#
# The two prohibitions are the load-bearing half. Attaching a file to an app and recording a
# Binding are both a person's act (ADR-0010), so there is nothing here for the agent to go and fix
# — being told about a gap it cannot close is exactly the shape of instruction a model answers by
# trying anyway. Name the gap and stop is the whole of the wanted behaviour.
_DROPPED_MENTION_NOTE = (
    "The person @mentioned something this turn cannot use, and it was removed from their request "
    "above before you saw it. This is what they were told about it:\n\n"
    "{said}\n\n"
    "You cannot repair this yourself — attaching a file to an app and recording a Resource for it "
    "are both acts only a person can do. So do not attach or record anything, do not offer to, do "
    "not substitute a different file or Resource, and do not write placeholder, mock or example "
    "data in place of what is missing. Say which mention you could not use. If the rest of the "
    "request stands on its own, build that part and nothing more; if it does not, stop there and "
    "say so.")


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
        domino_project_id: str | None = None,
        control_plane: ControlPlane | None = None,
        domino_project_name: str | None = None,
        workspace_id: str | None = None,
        domino_run_id: str | None = None,
        cost_project_label: str | None = None,
        gateway_ui_url: str | None = None,
        manage_url: str | None = None,
        browser_gateway_base: str | None = None,
        opencode_client: OpenCodeClient | None = None,
        gateway_mode: str = "fake",
    ) -> None:
        self._wm = WorkspaceManager(workspace_dir, template)
        self._project_id = project_id
        self._gateway = gateway
        self._catalog = catalog
        self._assets = assets or FakeAssetProvider()
        self._resources = resources or FakeResourceProvider()
        # Which authority a model id resolves against, so the turn-time slot check (#125) knows
        # whether an Alias listing is evidence about this turn at all. Only `domino` puts the models
        # and the listing behind one gateway: in `openai` each model routes to its own vendor and in
        # `fake` there is no gateway, so a slot missing from the listing there says nothing about
        # the turn — and refusing on it would be the "we could not check" mistake in its worst form.
        # The same gate `_run_slot_preflight` already applies at boot, for the same reason.
        self._gateway_mode = gateway_mode
        # The listing that check runs on, and when it was taken. See _TURN_SLOT_TTL_S.
        self._slot_listings_at: float = 0.0
        self._slot_listings: tuple[list | None, list | None] = (None, None)
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
        # Manage is its own Domino App, so the platform bar links out to it. Same fallback rule as
        # the dashboard link: nothing to point at means no link at all.
        self._manage_url = manage_url
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
        # Turns still run ONE AT A TIME under the queue below (#79): what changed is what happens to
        # the second one, not how many run. A streaming turn waits its turn; everything else is
        # refused on the spot, because a control with no composer behind it that sat silently until
        # a long build finished would look like a control that did nothing (#89). Stop stays
        # lock-free (it only sets stop_requested, which the running turn polls) so it can always
        # interrupt the held turn.
        self._turn_lock = threading.Lock()
        # The order the streaming turns take that lock in. Only build_stream, chat_stream and
        # approve_stream go through it — see _TurnQueue for why the lock stays a plain Lock.
        self._turns = _TurnQueue(self._turn_lock)
        # Set when a turn was given up on and its OpenCode session would not confirm it stopped
        # (#39). The lock above is then deliberately never released, so every later turn is refused
        # rather than run over a session that may still be writing. Nothing clears this: restarting
        # the workspace is the remedy, and the refusal says so.
        self._turn_wedged = False
        # Set when a turn was given up on, whether or not its session then confirmed it stopped —
        # so it is true on the clean stall path as well, where `_turn_wedged` stays false because
        # the lock IS handed back. What it means is narrower and more useful: this turn did not
        # build anything. Cleanup that only makes sense after a build that happened reads this one.
        self._turn_gave_up = False
        # Serializes the four operations that change WHICH Built App is bound, or what is in it:
        # select, New app, Delete and Reset. Switching stopped taking the turn lock (#77), so it is
        # no longer serialized against the other three by holding that — and each of them reads the
        # selected app and then acts on it, which a switch landing in between would make a lie.
        # Held around the rebind alone, never around a network call or a turn: a rail click must
        # not wait on Domino (see delete_app).
        self._app_lock = threading.Lock()
        # What the remote has that this workspace does not (#78). Refreshed off the request path so
        # the rail can badge it without anyone clicking, and read again by a turn on its way in.
        # `None` until the first check lands, which is why the rail badges nothing before then.
        self._incoming = None
        self._incoming_at = 0.0
        self._incoming_checking = False
        self._incoming_lock = threading.Lock()
        # The remote commit a person chose to build past, per Built App. A decision made once stands
        # until the remote moves on: re-asking every turn is a wall, not a choice.
        self._incoming_dismissed: dict[str, str] = {}
        # Chat files are written every turn; git save is coalesced (docs/workbench/chat.md).
        self._chat_dirty = False
        self._chat_dirty_thread: str | None = None
        self._chat_save_timer: threading.Timer | None = None
        self._chat_save_idle_s = 30.0

    def turn_busy(self) -> bool:
        """True while a build/approve turn holds the turn lock. The UI polls this to tell a dropped
        SSE connection (turn still running, keep showing Stop) from a finished turn — without it, a
        network blip makes the composer look idle and the next send offers Stop for nothing."""
        # A wedged workspace holds the lock for good (#39) and has no turn in it. Reading that as
        # "running" would leave the header spinning on a build nobody can stop and the composer
        # disabled against it — the exact screen this fixes, put back by the fix. It is refused at
        # the lock either way; what changes is that the refusal can be reached and reads true.
        return self._turn_lock.locked() and not self._turn_wedged

    def turn_state(self) -> dict:
        """What the turn lock is doing, for the one route the UI polls (#79).

        `running` on its own was enough while a refusal was immediate: a client that saw "not
        running" and could not see why still got a sentence the moment it sent anything. A queue
        turns that blind spot into a spinner nobody can resolve — the pending turns behind a wedged
        lock are waiting on a release that is never coming (#39) — so the wedge is reported here
        rather than inferred from a refusal that no longer arrives.

        `pending` is how many turns are waiting in line. Without it the composer's own queued rows
        are the only account of the queue, and a second tab's are invisible.

        `running_turn` is WHICH turn, as `{kind, conversation}` (#126). `running` alone was enough
        while there was one control for one turn; under a queue a Stop bar has to know whether the
        turn holding the lock is the one on screen, and a Chat turn, a Build turn and another tab's
        turn all set `running` alike. It is None for the two holders that cannot be Stopped: a wedge,
        which has its own sentence naming the restart, and a raw-lock caller like publish or reset,
        which never queued and has no ticket."""
        running = self._turns.running()
        return {"running": self.turn_busy(), "wedged": self._turn_wedged,
                "pending": self._turns.depth(),
                "running_turn": None if (self._turn_wedged or running is None) else
                                {"kind": running.kind, "conversation": running.conversation,
                                 "app": running.app}}

    def cancel_pending_turn(self, ticket_id: str) -> bool:
        """Drop a turn that is still waiting in line. False when there is no such turn (#79).

        A separate control from Stop, because they answer different questions. Stop is "I have seen
        enough of this answer"; Cancel is "I have changed my mind about asking". Pressing one where
        the other was meant is the mistake worth making impossible: a Stop that also emptied the
        queue would throw away questions somebody still wants answered, and a Cancel that
        interrupted the running turn would throw away work in progress."""
        return self._turns.cancel(ticket_id)

    def _release_turn(self) -> None:
        """Hand the turn lock back, waking whatever queued behind it (#79).

        Every release goes through here rather than through the lock directly, including the ones no
        queued turn is waiting on. The queue polls as a backstop, so a missed wake-up costs latency
        rather than correctness — but the sites that hold the lock longest are exactly the ones a
        queue waits behind, and half of them are not the streaming turns (publish, reset, the Chat
        save), so picking which ones to wake would be a rule to maintain and this is not."""
        self._turns.release()

    def read_plan_pin(self) -> dict:
        """The plan this app is being built from, for the rail's plan pin. `{}` when there is none.

        Two states, because a plan is a one-shot handoff: `.sage/plan.md` while it is waiting to be
        approved, and the newest `.sage/plans/NNN.md` once a build has consumed it. Both are the
        same document and both are worth showing — before, so the pin and the transcript card agree
        on what is pending; after, so the panel can say what the app in the preview was built from.

        Reads the workspace WITHOUT starting the preview (`project(start_preview=False)`): the panel
        asks for this on every load, and a pin is not a reason to boot Vite.
        """
        project = self.project(start_preview=False, seed_app=False)
        live = (project.workspace.read_plan() or "").strip()
        markdown = live or (project.workspace.read_archived_plan() or "").strip()
        if not markdown:
            return {}
        # The document this plan.md was written alongside, so the pin can open the plan page rather
        # than a modal of the raw text. Newest first, and the newest is the one plan.md belongs to.
        # Empty for a workspace whose plan predates plan documents — the pin falls back to the text.
        # The copy is the app's and the document is the Project's, which is why this reads two
        # surfaces to answer one question — and why it asks for this app's documents rather than
        # the Project's, which now include other apps'.
        docs = self._app_plan_docs(project)
        # The DOCUMENT's title, falling back to plan.md's first line. The two are the same words
        # until somebody renames the document, and after that only one of them is a name anybody
        # chose: plan.md's first line is what the model wrote and nothing can edit it. The fallback
        # is for a workspace whose plan predates plan documents, which has no title but that one.
        #
        # Renaming the Built App is deliberately NOT one of the things that reaches here: an app is
        # not its plan, and the same title is the plan page's heading and the transcript's plan
        # card, on a document that carries comments and approvals (ADR-0008).
        doc_title = str(docs[0].get("title") or "").strip() if docs else ""
        return {
            "title": doc_title or chat_handoff.plan_title(markdown),
            "markdown": markdown,
            "status": "awaiting" if live else "built",
            "steps": _count_plan_steps(markdown),
            "planId": docs[0]["id"] if docs else "",
        }

    # ---- Plan documents ----
    #
    # The pin above reads plan.md, the transient copy. These read the document, which outlives it
    # and belongs to the Project rather than to any one app. All of them take the record the same
    # cheap way the pin does: a plan page is not a reason to boot Vite or seed an app.

    def list_members(self) -> dict:
        """Who can be named as a reviewer, and whose id a comment resolves to. `directory` is the
        wider set the Workbench looks names up in; here they are the same list, because Sage only
        ever learns about the people on this project."""
        people = [
            {"id": p.id, "name": p.name, "title": p.title, "avatar": p.avatar}
            for p in self._resources.list_collaborators(self._domino_project_id)
        ]
        return {"members": people, "directory": people}

    def _plan_docs_record(self) -> ProjectRecord:
        return self.project(start_preview=False, seed_app=False).record

    def list_plan_docs(self) -> list[dict]:
        return self._plan_docs_record().list_plan_docs()

    def read_plan_doc(self, plan_id: str) -> dict | None:
        return self._plan_docs_record().read_plan_doc(plan_id)

    def read_plan_doc_markdown(self, plan_id: str) -> dict | None:
        return self._plan_docs_record().read_plan_doc_markdown(plan_id)

    def create_plan_doc(self, body: dict | None = None) -> dict:
        """An empty document somebody fills in by hand. The planner's own documents are created in
        the gate, where there is a plan to put in them."""
        body = body or {}
        return self._plan_docs_record().create_plan_doc(
            "",
            title=str(body.get("title") or "Untitled plan"),
            author=_viewer_id(),
            origin_thread_id=str(body.get("threadId") or ""),
        )

    def patch_plan_doc(self, plan_id: str, body: dict) -> dict | None:
        """Edit the document. Sections are rendered back to markdown and stored as a new version,
        so the text stays the source of truth and the previous draft survives the edit."""
        project = self.project(start_preview=False, seed_app=False)
        current = project.record.read_plan_doc(plan_id)
        if current is None:
            return None
        body = body or {}
        if "sections" not in body and "summary" not in body:
            # Nothing about the body changed — a rename is metadata, not a new draft.
            return project.record.patch_plan_doc_meta(
                plan_id, **{k: v for k, v in body.items() if k in ("title", "status", "appId")})
        summary = body.get("summary", current.get("summary", ""))
        sections = {**current.get("sections", {}), **(body.get("sections") or {})}
        meta = {k: v for k, v in body.items() if k in ("title", "status", "appId")}
        # The document's own name, from the rename if this edit carries one and from the document
        # otherwise — an edit to Screens must not cost the plan its title.
        doc = project.record.write_plan_doc_version(
            plan_id,
            plan_doc.render(summary, sections, meta.get("title") or current.get("title", "")),
            **meta)

        # An edit to the document that a live plan.md was copied from has to reach that copy, or the
        # build runs the plan as it was before the edit — and the rail's pin goes on counting the old
        # steps. Only while a handoff is actually live, and only from the document it belongs to:
        # editing an older plan after its build must not resurrect it as the thing being built.
        # The document is the Project's and the copy is the app's, so this is the one place the two
        # surfaces meet — deliberately, because copying between them is what it is for.
        if doc and project.workspace.read_plan() is not None:
            newest = self._app_plan_docs(project)
            if newest and newest[0]["id"] == plan_id:
                project.workspace.write_plan(doc["markdown"], plan_id)
        return doc

    def review_plan_doc(self, plan_id: str, body: dict) -> dict | None:
        """Reviewers, comments and approvals. None of it touches the body, so none of it makes a
        version: a comment on a plan is not a new draft of that plan."""
        record = self._plan_docs_record()
        doc = record.read_plan_doc(plan_id)
        if doc is None:
            return None
        body = body or {}
        action = str(body.get("action") or "")
        comments = list(doc.get("comments") or [])
        approvals = list(doc.get("approvals") or [])

        if action == "request":
            reviewers = [str(r) for r in (body.get("reviewers") or []) if r]
            return record.patch_plan_doc_meta(
                plan_id, reviewers=reviewers, status="in_review",
                reviewNote=str(body.get("note") or ""))
        if action == "comment":
            comments.append({
                "id": f"c{len(comments) + 1}",
                "section": str(body.get("section") or ""),
                "user": _viewer_id(),
                "text": str(body.get("text") or ""),
                "at": plan_doc.now(),
                "resolved": False,
            })
            return record.patch_plan_doc_meta(plan_id, comments=comments)
        if action == "resolve":
            target = str(body.get("commentId") or "")
            for comment in comments:
                if comment.get("id") == target:
                    comment["resolved"] = True
            return record.patch_plan_doc_meta(plan_id, comments=comments)
        if action == "approve":
            user = str(body.get("user") or _viewer_id())
            if not any(a.get("user") == user for a in approvals):
                approvals.append({"user": user, "at": plan_doc.now()})
            reviewers = [str(r) for r in (doc.get("reviewers") or [])]
            # Approved once every named reviewer has signed off. With nobody named, one approval is
            # the whole review.
            done = all(r in [a["user"] for a in approvals] for r in reviewers) if reviewers else True
            return record.patch_plan_doc_meta(
                plan_id, approvals=approvals, status="approved" if done else "in_review")
        return doc

    def project(self, start_preview: bool = True, seed_app: bool = True) -> Project:
        """Get-or-attach the single bound project. Idempotent.

        Chat uses `start_preview=False, seed_app=False` so opening a Thread does not clone the
        React template or start Vite. Later `project()` calls return that attach as-is. Build and
        the preview proxy call `_ensure_seeded` / `_ensure_preview_running` when they need the app.
        """
        if self._project is not None:
            return self._project
        workspace = self._wm.ensure(self._project_id, seed_app=seed_app)
        record = self._wm.project_record(self._project_id)
        self._hydrate_untitled(record)
        if seed_app:
            self._prepare_app_files()
        control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
        shim = EnforcementShim(control, self._effective_catalog(record), self._gateway,
                               project_name=self._cost_project_label)
        supervisor = ViteSupervisor(workspace.path, domino_base_prefix())
        queries = PreviewQueries(workspace.path, self._wm.template)
        if start_preview:
            supervisor.start()
            queries.start()
        self._project = Project(self._project_id, workspace, record, supervisor, queries, control, shim,
                                cost_url=self._gateway_ui_url,
                                cost_project=self._cost_project_label if self._gateway_ui_url else None,
                                manage_url=self._manage_url)
        if seed_app:
            # A freshly seeded AGENTS.md is the template's, so it is voiced in the pack's words
            # (#114) and then the Project's instructions have to be rendered back into it — they are
            # kept on the record, not in the file (ADR-0008).
            self._voice_agents_md(self._project)
            self._splice_instructions(self._project)
        self._rehydrate_attached(self._project)
        # Once per Project, on the way in rather than on the way to the rail: a migration belongs to
        # opening the thing it repairs, and `list_project_resources` promises it never writes to the
        # membership file (#140).
        self._backfill_membership_from_bindings(record)
        return self._project

    def _chat_project(self) -> Project:
        return self.project(start_preview=False, seed_app=False)

    def _ensure_seeded(self) -> Project:
        """Chat may have attached an empty volume. Handoff / Build need the app template.

        The SELECTED app, always: this is get-or-seed and never mints a second one. Adding to
        `apps/` is `_open_app`'s job, off a confirmed handoff.
        """
        if self._project is None:
            return self.project(start_preview=False, seed_app=True)
        self._wm.ensure(self._project_id, seed_app=True)
        self._prepare_app_files()
        # The app may have been seeded just now, from a template that carries the pack's tokens and
        # no instructions block.
        self._voice_agents_md(self._project)
        self._splice_instructions(self._project)
        return self._project

    # ---- Built Apps ----
    #
    # A Project holds many (ADR-0008) and this Builder shows one at a time. The list is a directory
    # scan: an index is one file with many writers, and two viewers in one Project are two Sage
    # Builders, which is to say two processes — the same bug one `meta.json` per Thread exists to
    # avoid. Each app's own record is written only inside its own directory.

    def list_apps(self) -> list[dict]:
        """Every Built App in this Project, oldest first, for the Build rail."""
        project = self.project(start_preview=False, seed_app=False)
        # Off the request path on purpose: the rail is the first thing that draws, and it must not
        # wait on a network round trip to do it. The badge appears on the poll after the check.
        self._remote_check_due(project)
        # Newest document wins the app it names, which is the one its plan pin already trusts.
        plans = {str(d.get("appId") or ""): d["id"]
                 for d in reversed(project.record.list_plan_docs())}
        return [self._app_row(app_id, project.workspace.app_id, plans, self._building_app_id())
                for app_id in self._wm.app_ids()]

    # ---- what the remote has that we don't (#78) ----
    #
    # Git is the PROJECT's: one repo holds every Built App, so one reading of the remote answers for
    # all of them and the rows split it by directory. Two moments, two rules — a person decides
    # while they are here, and the save path decides for them once they have gone
    # (see _integrate_remote).

    def _check_remote(self, project: Project, *, fetch: bool = True):
        """Fetch, then read what the remote has that this workspace does not. Caches the answer for
        the rail and returns it. `fetch=False` re-reads the refs a caller just fetched for itself.

        Never raises. This sits at the top of a turn, and a remote that can't be reached has to
        leave the turn exactly as an up-to-date one would — refusing to build because a network was
        down would be a worse failure than the stale code it was guarding against."""
        import time

        from ..workspace import git

        found = git.Incoming("", [])
        try:
            root = project.record.path
            if git.is_repo_root(root):
                if fetch:
                    git.fetch(root)
                found = git.incoming(root)
        except Exception:
            log.exception("could not check the remote for incoming changes")
            found = git.Incoming("", [])
        with self._incoming_lock:
            self._incoming = found
            self._incoming_at = time.monotonic()
        return found

    def _remote_check_due(self, project: Project) -> None:
        """Start a background check when the cached one has gone stale, and return immediately.

        One at a time: `/api/apps` is polled while a build runs, and a fetch per poll would spend a
        network round trip every two seconds to answer a question that changes far more slowly."""
        import time

        with self._incoming_lock:
            if self._incoming_checking:
                return
            if self._incoming_at and time.monotonic() - self._incoming_at < _REMOTE_CHECK_SECONDS:
                return
            self._incoming_checking = True

        def run() -> None:
            try:
                self._check_remote(project)
            finally:
                with self._incoming_lock:
                    self._incoming_checking = False

        threading.Thread(target=run, name="sage-remote-check", daemon=True).start()

    def _incoming_now(self):
        """The last reading of the remote, or an empty one before the first has landed."""
        from ..workspace import git

        with self._incoming_lock:
            return self._incoming or git.Incoming("", [])

    def _incoming_files(self, workspace: Workspace, found=None) -> list[str]:
        """Of an incoming reading, the files that land inside one Built App, named as that app
        names them.

        The repo is the Project, so a teammate's commit can touch another Built App, a Thread, or
        the Project's own record. None of those is this app's code, and this app's code is the only
        thing a turn here would be building on top of."""
        found = self._incoming_now() if found is None else found
        prefix = f"{workspace.path.relative_to(self._wm.path).as_posix()}/"
        return [f[len(prefix):] for f in found.files if f.startswith(prefix)]

    def _building_app_id(self) -> str:
        """The Built App a turn is streaming into right now, or "" when none is.

        Asked of the pin rather than of the lock alone: a Chat turn holds the same lock and builds
        no app, and marking a rail row for it would say a build is running when none is."""
        pinned = self._project.turn_app if self._project is not None else None
        return pinned.app_id if pinned is not None else ""

    def _app_row(self, app_id: str, selected: str, plans: dict[str, str], building: str = "") -> dict:
        """One rail row. `plans` is read once by the caller rather than per app: the documents are
        the Project's, so asking for them inside the loop would re-read all of them per app."""
        workspace = self._wm.app_workspace(self._project_id, app_id)
        return {
            "id": app_id,
            "name": _app_display_name(workspace),
            "built": workspace.has_built(),
            "builtAt": workspace.built_at(),
            "planId": plans.get(app_id, ""),
            # Whether there is a deployment behind this app, which is what makes Delete offer to
            # take the Domino App with it (#76). The id itself stays here — the rail has no use for
            # it, and a Domino App is deleted by the app that owns it, never by one named over HTTP.
            "published": bool(workspace.domino_app_id()),
            # Where `Open app` goes (#89), and "" until the first publish — the same answer
            # `published` gives, from the same recorded id.
            #
            # A destination, not a field to read an id out of. The id is IN this string, which the
            # line above does not pretend otherwise about: what stays true is that no route takes a
            # Domino App id from a caller, so nothing the UI can do with the one it can see here
            # reaches an App. What the row is not is a `dominoAppId` for the browser to build URLs
            # from — the rewrite that turns one into a page (`/apps-internal/{id}` 404s in a
            # browser, `/modelproducts/{id}` does not) is control-plane knowledge that lives in one
            # place and has already been re-learned from live Domino once. Handing over the id
            # would make the UI its second author. `/api/gallery` hands its cards a URL for the
            # same reason.
            "url": app_viewer_url(workspace.domino_app_id()),
            # When the code behind that URL last moved. The transcript's app card reads publish
            # state off this row rather than out of the block it renders, because whether an app is
            # published today is a now-question and a six-week-old build run cannot answer it (#56).
            "publishedAt": workspace.published_at(),
            "selected": app_id == selected,
            # A build is streaming into this one. Switching away no longer stops it (#77), so the
            # row is where "a build is running, and this is where" gets said — the composer can
            # only say the first half, and it says it on whichever app you happen to be reading.
            "building": app_id == building,
            # Somebody else has pushed changes to this app's code (#78). A background check keeps
            # this current, so the rail says which app it is without anyone opening one to find out.
            "behind": bool(self._incoming_files(workspace)),
        }

    def _one_app(self, app_id: str) -> dict:
        """The rail row for one app, for the two callers that just changed it."""
        project = self.project(start_preview=False, seed_app=False)
        plans = {str(d.get("appId") or ""): d["id"]
                 for d in reversed(project.record.list_plan_docs())}
        return self._app_row(app_id, project.workspace.app_id, plans, self._building_app_id())

    def create_app(self) -> dict:
        """Start a Built App from the Build rail: minted, seeded and selected, with no Thread and
        no plan behind it (#74).

        No new gate is needed. `_should_gate` fires on the first BUILD of an app that has not been
        built, so a fresh app lands on the plan gate by itself — the same review a handoff earns on
        the way out of Chat, reached from the other side.

        Refused while a turn is streaming, unlike a plain switch (#77): switching only changes what
        is on screen, and this seeds a directory and points Build at it. The lock is taken BEFORE
        the app is minted rather than around the select, so a refusal leaves no half-born app
        sitting in the rail with nothing pointed at it.
        """
        project = self.project(start_preview=False, seed_app=False)
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self._turn_wedged, "start a new app")
        try:
            with self._app_lock:
                born = self._wm.create_app(self._project_id)
                self._bind_app(project, born)
        finally:
            self._release_turn()
        return self._one_app(born.app_id)

    def select_app(self, app_id: str) -> dict:
        """Point Build at another Built App. Raises KeyError for one that is not there.

        Never refused for a running build (#77). A switch takes no working tree and writes no code:
        it changes the preview, the transcript and the panels, all of which describe the app on
        screen. The turn lock stays one per Project and still refuses a second turn — this just
        stops being one of the things that takes it, so a build carries on in the app it started in
        while the person reads another. `_app_lock` is what a switch takes instead, so that a click
        in the rail cannot land in the middle of a New app, a Delete or a Reset.
        """
        project = self.project(start_preview=False, seed_app=False)
        # Checked before the equality guard, not inside it: on a Project with no apps the selected
        # id is one this process minted for a directory that does not exist yet, so an id equal to
        # it names nothing and must 404 rather than fall through to a row that cannot be built.
        if app_id not in self._wm.app_ids():
            raise KeyError(app_id)
        with self._app_lock:
            if app_id != project.workspace.app_id:
                self._wm.select(app_id)
                self._bind_app(project, self._wm.ensure(self._project_id, seed_app=True))
        return self._one_app(app_id)

    def rename_app(self, app_id: str, name: str) -> dict:
        """Change what an app is called. Its ID is not touched and cannot be: the directory is
        named for it, and Domino fixes a published App's `entryPoint` when the App is created, so a
        rename that moved the directory would strand the deployment (ADR-0008)."""
        if app_id not in self._wm.app_ids():
            raise KeyError(app_id)
        name = (name or "").strip()
        if not name:
            raise ValueError("a name is required")
        self._wm.app_workspace(self._project_id, app_id).set_display_name(name)
        # The rail's tags name this app from OUTSIDE its directory, so the new name has to be
        # carried to them here — the same one-place-outside problem a delete solves with
        # `forget_app`. Left alone, the chip on every conversation that changed this app keeps the
        # old name until some later build turn happens to rewrite it, while the app rail, the
        # header and the preview have all already moved.
        ThreadStore(self.project(start_preview=False, seed_app=False).record.path).rename_app(
            app_id, name)
        return self._one_app(app_id)

    def delete_app(self, app_id: str, *, delete_domino_app: bool = False) -> dict:
        """Take a Built App out of the Project, so a rail of abandoned experiments can be cleared
        (#76). Raises KeyError for an app that is not there.

        The mirror of `create_app`, and deliberately NOT of Reset: Reset empties an app and keeps
        it, this takes the app away. Everything the app owns goes — its code, its Bindings, its
        queries and its log, all of which live in its directory — and so do the plan documents that
        name it, which is the same rule Reset applies to the documents it clears. A document naming
        no app is a plan drafted in Chat and nobody's to take.

        A published app is offered its Domino App, because a live URL nobody can find or fix is the
        same stranding in a different costume: the Domino App's id is kept in the app's own
        settings, so once the directory goes Sage cannot update or delete that App either. The offer
        is the caller's to make — `delete_domino_app` is what the person answered — and an app that
        was never published makes no control-plane call at all.

        The control plane is asked FIRST and its failure stops the delete. The Built App is still
        there to try again with, which is the only outcome that leaves a way out; the other order
        loses the app and strands the Domino App in one step.

        Refused rather than queued while a turn is streaming, as a New app is — and unlike a plain
        switch (#77), which takes no tree away. Reset waits out a stop it can see landing; there is
        nothing to wait for here, because the app being deleted may not be the app the turn is
        running in.
        """
        if app_id not in self._wm.app_ids():
            raise KeyError(app_id)
        project = self.project(start_preview=False, seed_app=False)
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self._turn_wedged, "delete the app")
        try:
            # Read before the directory goes: the id that reaches the Domino App is in the app's
            # own settings, and afterwards there is nothing left to ask.
            deployed = self._wm.app_workspace(self._project_id, app_id).domino_app_id()
            deleted_domino_app = False
            if deployed and delete_domino_app:
                if self._control_plane is None:
                    raise RuntimeError(brand.text(
                        "This app has a published {platformName} App, but this builder has no "
                        "connection to {platformName} to delete it with. Delete the App in "
                        "{platformName}, then delete this app."))
                try:
                    self._control_plane.delete_app_deployment(deployed)
                except Exception as e:
                    log.exception("delete_app: couldn't delete Domino App %s", deployed)
                    # `reason` is the platform's own words, so it rides in as a value and is left
                    # exactly as it arrived — only the sentence around it is ours to re-brand.
                    raise RuntimeError(brand.text(
                        "{assistantName} couldn't delete this app's {platformName} App "
                        "({deployed}): {reason}. The {builtApp} is still here, so nothing is "
                        "stranded — try again, or delete the App in {platformName} first and then "
                        "delete this one.",
                        deployed=deployed, reason=e)) from e
                deleted_domino_app = True
            # From here down is the part a switch must not interleave with. The control-plane call
            # above is deliberately OUTSIDE it: deleting a Domino App takes the best part of a
            # minute, and holding the rail's own lock across that would make every click in the
            # rail wait for Domino.
            with self._app_lock:
                self._wm.delete_app(app_id)
                project.record.clear_plan_docs(app_id)
                # And the rail's tags, on the same rule as the plan documents: they name an app that
                # is gone, and they are the one place that names it from OUTSIDE the directory the
                # delete just took away, so nothing else clears them.
                ThreadStore(project.record.path).forget_app(app_id)
                # Build was looking at the app that just went, so it has to be looking at something.
                # `ensure` answers with the newest app left, or seeds one for a Project that now has
                # none — the same not-yet-built app a Project starts with, rather than a Build mode
                # pointed at a directory that is not there.
                if project.workspace.app_id == app_id:
                    self._bind_app(project, self._wm.ensure(self._project_id, seed_app=True))
        finally:
            self._release_turn()
        return {
            "ok": True,
            "id": app_id,
            # Three answers, not two: an app that was never published has no Domino App to speak
            # of, and saying "running" of one would be a sentence about something that never was.
            "dominoApp": "deleted" if deleted_domino_app else ("running" if deployed else "none"),
            "selected": self._wm.selected_app_id(),
        }

    def _bind_app(self, project: Project, workspace: Workspace) -> Project:
        """Point the attached Project at a different Built App.

        What is replaced is what belongs to an app: its code, the preview serving it and the
        attachments linked into it. The Project's own record, its Threads and the model picker are
        untouched, because switching app is not switching Project.

        Safe to call while a turn is streaming, which is the point (#77): nothing here is read by a
        turn. A turn asks `project.app_for_turn()` for its app and pinned its attachments at its start,
        so what changes here is only what the person is looking at.

        The preview is STOPPED rather than restarted here. It serves whichever directory it was
        started in, so one left running would go on serving the app the person just left;
        `_preview_upstream` starts it again in the new directory the next time a preview is asked
        for. The Build session goes with it — a session is opened on one directory, and the one
        cached here belongs to the app being left.
        """
        if project.workspace.path != workspace.path:
            project.supervisor.stop()
            project.queries.stop()
            project.supervisor = ViteSupervisor(workspace.path, domino_base_prefix())
            project.queries = PreviewQueries(workspace.path, self._wm.template)
        project.workspace = workspace
        project.session_id = None
        # A NEW list rather than a clear: a turn in flight pinned the one it started with, and
        # emptying that one under it would leave its end-of-turn repairs with nothing to restore
        # from (see Project.turn_attached and _restore_attachments).
        project.attached = []
        self._prepare_app_files()
        self._voice_agents_md(project)   # the app being bound to may have been seeded just now
        self._splice_instructions(project)
        self._rehydrate_attached(project)
        return project

    @staticmethod
    def _app_plan_docs(project: Project) -> list[dict]:
        """The plan documents that belong to the app in front of us, newest first.

        A document is the Project's and names the app it bound to, so once a Project holds several
        apps "the newest document" stopped being an answer to "what is THIS app built from".

        A document naming no app is a FALLBACK, not a peer: it is either one drafted in Chat and
        not yet confirmed, or one from a Project written before documents carried the reference.
        Mixed into one newest-first list they would outrank the real answer — a plan drafted in
        Chat after this app was built is newer than the app's own document, and would become what
        the plan pin names and what a bare "yes, build it" approves.

        A superseded document is not a candidate at all. It named this app and it lost its live
        copy to a newer plan (#59), so answering "what is this app's plan" with it would pin the
        rail to one document beside another one's markdown, and would copy an edit made to the
        plan nobody is building over the plan somebody is.
        """
        app_id = project.workspace.app_id
        docs = project.record.list_plan_docs()
        mine = [d for d in docs if str(d.get("appId") or "") == app_id
                and str(d.get("status") or "") != "superseded"]
        return mine or [d for d in docs if not str(d.get("appId") or "")]

    @staticmethod
    def _supersede_live_plan(project: Project, workspace: Workspace, new_plan_id: str,
                             conversation: str) -> None:
        """Step the plan that is awaiting approval in this app aside, instead of writing over it.

        Called from both doors into a Built App just before its `plan.md` is written: the BUILD
        gate and a confirmed Chat handoff. With no live plan there is nothing to move, which is
        every ordinary first plan, and this does nothing at all.

        Nothing is deleted (#59). The live copy is archived by the path that already keeps that
        history, and the document behind it keeps every version and every comment — it only gains
        a note saying what became of it. That holds even for a plan.md with no document behind it
        at all, which is what an upgraded project has: it is archived, there is simply nothing to
        note it on.

        Which document lost its live copy is read off the app (`live_plan_doc_id`) rather than
        guessed from the document list, because "the newest document for this app" is a different
        question with a different answer — see `write_plan`.
        """
        if workspace.read_plan() is None:
            return
        live_doc_id = workspace.live_plan_doc_id()
        if live_doc_id and live_doc_id == new_plan_id:
            # Confirming one sheet twice writes the same document's plan back over itself. There
            # is no earlier plan standing here, so nothing steps aside.
            return
        workspace.archive_plan(superseded=True)
        # The document the live copy was WRITTEN FROM, not the newest one for this app. A plan
        # drafted in Chat is confirmed long after it was drafted, so the two are different
        # documents and only the first is the one that just lost its live copy.
        earlier = project.record.read_plan_doc(live_doc_id) if live_doc_id else None
        if earlier is None:
            return
        origin = str(earlier.get("originThreadId") or "")
        project.record.patch_plan_doc_meta(earlier["id"], status="superseded",
                                           supersededBy=new_plan_id,
                                           supersededByThreadId=conversation)
        # Told in the earlier Conversation's own transcript, which is what its plan card is
        # rebuilt from. A plan with no Conversation behind it (the CLI path) has no card to
        # correct, and an untagged entry would later be adopted by whoever switched next. A
        # conversation replanning over its own plan is not told either — the newer card is already
        # right there under the older one — but its document still says what became of it.
        if origin and origin != conversation:
            workspace.append_history(
                {"type": "plan-superseded", "planId": earlier["id"], "by": new_plan_id,
                 "byConversation": conversation}, origin)

    def _prepare_app_files(self) -> None:
        self._wm.refresh_preview_config()
        self._wm.ensure_llm_helper()
        self._wm.refresh_owned_sources()

    def _ensure_preview_running(self, project: Project) -> None:
        try:
            project.supervisor.upstream()
        except RuntimeError:
            project.supervisor.start()
        if project.queries.port is None:
            project.queries.start()

    def set_chat_pick(self, model: str | None, effort: str | None) -> None:
        """Standing Chat alias + reasoning_effort. `auto`/empty is Sage's default (catalog.ask)."""
        project = self._chat_project()
        if model in (None, "", "auto"):
            project.control.pick_chat(None, None)
            return
        alias = next((a for a in self.list_llm_aliases() if a["name"] == model), None)
        if alias is None:
            raise ValueError(f"unknown model {model!r}")
        caps = alias.get("capabilities") or []
        if caps and "embeddings" in caps and "chat" not in caps:
            raise ValueError(f"{model!r} is not a chat model")
        efforts = alias.get("reasoning_efforts") or []
        if effort in ("", None, "default"):
            effort = None
        elif effort not in efforts:
            raise ValueError(f"invalid reasoning_effort {effort!r}")
        project.control.pick_chat(model, effort)

    def _hydrate_untitled(self, record: ProjectRecord) -> None:
        """First boot of a scratch project: set untitled so the chip can lie. Once settings
        already has the key (true or false), never flip it from the Domino slug."""
        if "untitled" in record.read_settings():
            return
        name = (
            self._domino_project_name
            or self._project_id
            or os.environ.get("DOMINO_PROJECT_NAME")
        )
        if name == naming.UNTITLED_DISPLAY:
            record.mark_untitled(True)
            return
        username = (
            os.environ.get("DOMINO_USER_NAME")
            or os.environ.get("DOMINO_STARTING_USERNAME")
            or ""
        )
        user_id = os.environ.get("DOMINO_USER_ID") or ""
        expected = naming.default_project_name(username, user_id)
        if naming.is_default_name(name, expected):
            record.mark_untitled(True)

    def _rehydrate_attached(self, project: Project) -> None:
        """Restore the attached-files list. The manifest (.sage/attachments.json) is the source of
        truth — it's committed, so it survives clones and orchestrator restarts (the in-memory list
        does not). Fall back to scanning public/data/ symlinks for older workspaces written before
        the manifest existed."""
        entries = project.workspace.read_attachments()
        if entries:
            project.attached[:] = entries
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

    @staticmethod
    def _switch_conversation(project: Project, conversation: str | None) -> None:
        """A different conversation is a different session. project.session_id caches only the one
        in play, so switching conversations drops the cache and forces a re-read from disk."""
        if conversation != project.build_conversation:
            project.build_conversation = conversation
            project.session_id = None

    def _begin_conversation(self, conversation: str | None) -> None:
        """Pin a streaming turn to its Build conversation before any of it is persisted: the
        append_history calls below read project.build_conversation, and _ensure_session opens that
        conversation's own session."""
        project = self.project()
        self._switch_conversation(project, conversation)
        self._adopt_legacy_build_history(project.workspace, project.record)

    @staticmethod
    def _adopt_legacy_build_history(workspace: Workspace, record: ProjectRecord) -> None:
        """Build history predates conversation tagging (Workspace.adopt_history). Hand every
        untagged entry to the project's OLDEST conversation: an upgraded project keeps its
        transcript, and a conversation created after the upgrade — the "New conversation" the rail
        offers — still opens empty. With no conversations yet nothing can own them, so leave them
        for the first one that builds."""
        if not workspace.has_untagged_history():
            return
        rows = ThreadStore(record.path).list()
        if not rows:
            return
        # createdAt has one-second resolution, so two Threads made in the same second tie. The id
        # carries epoch-ms and is strictly increasing, so it breaks the tie by creation order —
        # the list itself no longer carries one, being a scan (ADR-0008).
        oldest = min(rows, key=lambda r: (str(r.get("createdAt") or ""), str(r.get("id") or "")))
        if oldest.get("id"):
            workspace.adopt_history(str(oldest["id"]))

    def _ensure_session(self, project: Project, conversation: str | None = None) -> str:
        client = self._ensure_opencode()
        self._switch_conversation(project, conversation)
        app_id = project.app_for_turn().app_id
        if project.session_id is None:
            project.session_id = self._recover_session(project.record, client, conversation, app_id)
        if project.session_id is None:
            # No session-level model: use opencode.json's default; the shim's router enforces the
            # real model per request. (An explicit ModelRef at creation stalled turns.)
            project.session_id = client.create_session(directory=str(project.app_for_turn().path))
            project.record.write_session_id(project.session_id, conversation, app_id)
        return project.session_id

    @staticmethod
    def _recover_session(record: ProjectRecord, client: OpenCodeClient,
                         conversation: str | None = None, app_id: str = "") -> str | None:
        """A session id persisted from a prior process may point at a session the current
        OpenCode server doesn't know about (e.g. its storage was reset); validate before reusing
        it so a stale id doesn't wedge every subsequent build call.

        Read per app as well as per conversation: a session is opened on one directory, so one
        recovered for another Built App would stand the agent in the wrong tree (ADR-0008)."""
        sid = record.read_session_id(conversation, app_id)
        if sid is None:
            return None
        try:
            client.messages(sid)
        except httpx.HTTPStatusError:
            return None
        return sid

    def build(self, prompt: str, conversation: str | None = None) -> dict:
        """Run one build to completion (non-streaming). Reuses the session, so repeated calls are
        follow-up turns with context. Requires gateway access."""
        # Serialize with the streaming turns: only one turn may run at a time (see _turn_lock). Refuse
        # rather than overlap another turn on the shared control + working tree.
        if not self._turn_lock.acquire(blocking=False):
            # Answers in a dict rather than by raising, but from the same two sentences: a wedged
            # workspace refuses everything the same way, whatever shape the caller reads (#97).
            return {"ok": False, "error_count": 0,
                    "decision": "wedged" if self._turn_wedged else "busy",
                    "message": turn_busy_message(self._turn_wedged)}
        try:
            project = self._ensure_seeded()
            self._pin_turn_app(project)
            self._adopt_legacy_build_history(project.app_for_turn(), project.record)
            # Same reason as the streaming turns: neither the archive nor the Artifacts link is
            # committed, so a fresh clone reaching the agent through this route would hand it
            # files that aren't there.
            self._refresh_agent_inputs(project)
            client = self._ensure_opencode()
            sid = self._ensure_session(project, conversation)

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
                check=lambda: self._feedback.check(project.app_for_turn().path),
                breaker=CircuitBreaker(),
            )
            return {"ok": report.ok, "error_count": len(report.errors), "decision": decision.reason, "message": report.as_agent_message()}
        finally:
            self._clear_turn_baseline()
            self._release_turn()

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
                real = _safe_join(project.app_for_turn().path, m).resolve()
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

    def _resource_mention_note(self, project: Project, resources: list[dict] | None) -> str:
        """The block a turn carries for the Resources the creator @mentioned (#31), or "" for none.

        A ref is `{"kind", "id"}` — a Binding's identity rather than a name — because the name is
        exactly what cannot survive the trip: two kinds can carry the same one, and the creator picked
        a row out of a list. Attachments resolve to a path; a Resource has none, so this is its own
        channel and not another entry in `mentions`. An optional `table` names one place inside a
        Data Source, which is as deep as a mention goes: everything below the Binding's scope was
        already enumerated when the Scope was bound, and everything outside it is not on disk.

        Only Resources this app holds a Binding for are honored, the same rule `_resolve_mentions`
        applies to attachments: mentioning one it is not bound to would ask the agent to use a
        Resource with no schema, credential or config behind it. A table is held to the same rule
        against the recorded schema — one that is not in it is one the agent has no columns for.
        """
        if not resources:
            return ""
        recorded = parse_bindings(project.app_for_turn().read_bindings())
        known = {b.key: b for b in recorded}
        # The tables of each bound Data Source, keyed by the Binding they belong to — so a table
        # mention is honored against the Resource it was offered under, and an app reading a
        # warehouse and an app database can be pointed at either one's tables (#33).
        in_schema = {rid: {c.table for c in columns} for rid, columns
                     in parse_schema(self._read_json(project.app_for_turn().path / SCHEMA_PATH)).items()}
        # Grouped by Binding, in the order they were mentioned: "@Snowflake-Data-Warehouse and
        # @FCT_USAGE_DAILY" names one Resource once, not twice, and the tables belong on that line.
        order: list[tuple[str, str]] = []
        tables: dict[tuple[str, str], list[str]] = {}
        for ref in resources:
            if not isinstance(ref, dict):
                continue
            key = (str(ref.get("kind") or ""), str(ref.get("id") or ""))
            if key not in known:
                continue
            if key not in tables:
                order.append(key)
                tables[key] = []
            table = str(ref.get("table") or "")
            if table and table in in_schema.get(key[1], ()) and table not in tables[key]:
                tables[key].append(table)
        return mention_note([Mention(known[k], tuple(tables[k])) for k in order], recorded)

    def _unusable_mentions(self, project: Project, resolved: list[dict] | None,
                           mentions: list[str] | None,
                           resources: list[dict] | None) -> tuple[str, list[dict]]:
        """What this turn was @-mentioned and cannot use: one line for the transcript, and the rows
        the refusal card hangs its buttons on. `("", [])` when the turn dropped nothing.

        The picker offers more than a build can honor: Chat's own uploads live at the Project root,
        outside every app, and a Resource in the rail is usable only by the app holding a Binding for
        it. Both were dropped in silence — which is how a turn builds from the wrong file while the
        person watches the right one sit in the panel. The two rules are not restated here; the
        answer is read back off `_resolve_mentions` and the same Binding list `_resource_mention_note`
        honors, so what is reported can never drift from what was used.

        The rows are the sentence's other half rather than a second opinion of it: both are built in
        this one pass, so the card can never offer to fix something the sentence did not report, and
        neither can go on saying a Resource is refused after the other stops (#135). A row is
        `{kind, id, name, app, appId}` — the Binding identity rather than the name, because a name is
        unique only within a kind; the label the creator picked off a list, so the card can quote it
        back; and the app the fix would act on, because a Project holds many Built Apps (ADR-0008)
        and "this app" is the one word that cannot say which of them lacks the record. `kind` is the
        Binding kind for a Resource and `file` for an attachment.

        The app is carried TWICE, as the name the card prints and as the id it checks. Switching
        Built App mid-build is allowed and the turn carries on (#77), so a card offering "Use in Gong
        sentiment" can still be on screen after the rail has moved — and every act behind it resolves
        whatever is selected now. The id is what lets the card notice and stand down rather than bind
        the app it did not name.

        Only a drop somebody can close in ONE act gets a row. A Chat file can — promoting it onto a
        Dataset attaches it to the app — and so can an unbound Resource. A workspace path that
        resolves to nothing cannot: there is no act to offer it, and a button that cannot complete is
        the dead end this sentence exists to remove. That drop keeps the prose and gets no row.
        """
        kept = {a["path"] for a in (resolved or [])}
        missing = [m for m in (mentions or []) if m not in kept]
        bound = {b.key for b in parse_bindings(project.app_for_turn().read_bindings())}
        unbound = [r for r in (resources or []) if isinstance(r, dict)
                   and (str(r.get("kind") or ""), str(r.get("id") or "")) not in bound]
        if not missing and not unbound:
            return "", []

        def named(paths: list[str]) -> str:
            return ", ".join("@" + PurePosix(p).name for p in paths)

        # Named, because a Project holds many apps (ADR-0008) and "this app" is the one word that
        # cannot say which of them is missing the Binding. Through `_app_display_name` and not
        # `display_name`, which is "" until somebody renames an app: this sentence quotes a menu
        # label back at the reader, so it has to call the app what the rail calls it — plan title, or
        # "Unnamed Built App" — or it sends them looking for a row of that name.
        where = _app_display_name(project.app_for_turn())
        whose = project.app_for_turn().app_id
        # Chat's uploads, named apart from the rest: "not attached" is true of both, but only this one
        # has a file the person can point at, and telling them to attach a file they can see beats
        # telling them a file they can see isn't there.
        chat_files = [m for m in missing if m.startswith(_SCRATCH_PREFIX)]
        others = [m for m in missing if m not in chat_files]
        lines: list[str] = []
        entries: list[dict] = []
        if chat_files:
            lines.append(f"Couldn't use {named(chat_files)} — a Chat file lives outside this app. "
                         "Attach it to the app in the Data panel, then ask again.")
            entries += [{"kind": "file", "id": m, "name": PurePosix(m).name,
                         "app": where, "appId": whose} for m in chat_files]
        if others:
            lines.append(f"Couldn't use {named(others)} — not attached to this app. "
                         "Attach it in the Data panel, then ask again.")
        if unbound:
            shown = ", ".join("@" + str(r.get("name") or r.get("id") or "") for r in unbound)
            # The act, spelled the way the door spells it. The sentence this replaces sent people to
            # the Resources panel for a control that was not there (#127) and called it "connecting",
            # a word `CONTEXT.md` bans for exactly the confusion it caused here. The panel then grew
            # the control and lost it again: the act is on the Built App's own surface now (ADR-0021,
            # #144), so naming that panel would re-make the original bug word for word.
            #
            # The destination is named by the heading the reader will actually see over it — "{app}
            # ships", in the Build header — which is the shape every other pointer in this product
            # takes (`modes/builder.js`, and the receipt in `store.bindToApp`). A direction on the
            # screen would be a second thing to keep in step with a layout.
            lines.append(f"Couldn't use {shown} — {where} doesn't use it yet. "
                         f"Choose Use in {where} in the list of what it ships, then ask again.")
            # One row per Resource, not per mention. "@Warehouse and @FCT_USAGE_DAILY" names one
            # Data Source at one table, and two identical buttons would offer the same bind twice.
            seen: set[tuple[str, str]] = set()
            for r in unbound:
                key = (str(r.get("kind") or ""), str(r.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                entries.append({"kind": key[0], "id": key[1],
                                "name": str(r.get("name") or r.get("id") or ""),
                                "app": where, "appId": whose})
        return " ".join(lines), entries

    def _chat_context_note(self, project: Project) -> str:
        """The Chat half of THIS turn's Conversation, rebuilt for this turn. "" when there is none.

        Attribution is the whole subtlety here. A Build turn belongs to a pair — a Conversation and
        a Built App (`app_for_turn`) — and two Conversations can drive the same app (#73). So this
        is keyed on the Conversation that drove the turn, never on the app: keyed on the app, the
        second Conversation to build would be handed the first one's Chat and would resolve "that
        chart" against a chart it had never seen.

        Read from the Thread's own `history.jsonl`, which is never rewritten, rather than from the
        Chat OpenCode session — that session is compacted as it grows (see chat_compact), so what
        Build hears would otherwise depend on whether Chat had compacted yet.

        Empty for a caller with no conversation (the CLI, tests) and for a Conversation that has
        never been in Chat. The caller then writes no section at all rather than an empty heading.
        """
        conversation = project.build_conversation
        if not conversation:
            return ""
        try:
            history = ThreadStore(project.record.path).read_history(conversation)
        except (OSError, ValueError):
            # A transcript we cannot read is background, not the turn: build without it.
            log.exception("chat context: could not read conversation %s", conversation)
            return ""
        summary = chat_compact.chat_summary(history)
        return f"{_CHAT_CONTEXT_PREAMBLE}\n\n{summary}" if summary else ""

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

    def build_stream(self, prompt: str, mentions: list[str] | None = None,
                     resources: list[dict] | None = None, conversation: str | None = None,
                     skip_reset_gate: bool = False, skip_incoming_gate: bool = False):
        """Public entry: serialize this turn behind the per-project turn lock, then stream it.

        One turn at a time still. If a turn is already streaming, this one WAITS in line rather than
        running concurrently (see _TurnQueue) — overlapping turns corrupt the shared read-only gate
        and working tree, and nothing here changes that. What changed is that the second turn is
        accepted and pending instead of refused (#79): the wait is held on this request's own
        connection, and everything below it runs exactly as it did when it won the lock outright.

        A bare approval typed while a plan is waiting ("ok build") means the same thing as clicking
        Approve, so it runs THAT plan instead of falling into the gate and proposing a second one.

        `skip_reset_gate` says the reset offer already ran for this exact prompt and the user answered
        it with a button (see _reset_offer). Without it, replaying the prompt after a reset would match
        _asks_to_reset again and re-offer the same thing, forever. `skip_incoming_gate` says the same
        of the incoming-changes offer (see _incoming_offer), and both of its buttons set it: pulling
        answers that offer as much as building past it does."""
        # `app=True`: a build is written for the Built App on the rail, so the rail moving under a
        # pending one is a context change like any other (see _turn_snapshot).
        ticket = _TurnTicket(new_id("turn"))
        yield from self._acquire_turn(ticket, kind="build", conversation=conversation,
                                      prompt=prompt, app=True)
        if not ticket.granted:
            return
        try:
            self._turn_gave_up = False
            self._begin_conversation(conversation)
            project = self.project()
            self._pin_turn_app(project)
            plan_app = project.app_for_turn()
            live_plan = (plan_app.read_plan() or "").strip()
            # And a bare "try again" means the same thing again, when the live plan is one an
            # approve turn already started building and gave up on (a gateway error, a session that
            # went quiet). That plan has been approved; sending it back through the gate proposes a
            # second plan for the same request, and the next failure proposes a third. Read off the
            # workspace's resume point rather than the words alone, because "try again" at a plan still
            # WAITING for approval is a request for a different plan and must keep gating.
            retry = bool(live_plan) and plan_app.read_plan_retry_step() > 0 and _looks_like_retry(prompt)
            if live_plan and (retry or _looks_like_approval(prompt)):
                yield {"type": "plan-stale",
                       "note": ("Building the approved plan again." if retry
                                else "Approved in chat — building this plan.")}
                yield from self._approve_locked(user_text=prompt)
                return
            # Before the Ask check and the gate: "remove everything you have built" is a change
            # request and a build request by every rule below, which is exactly how it used to reach
            # the build agent and come back as a page ABOUT starting over.
            # `skip_reset_gate` is the offer being ANSWERED, not the gate being bypassed: it only
            # arrives from a button the offer itself drew, so the confirmation the gate exists to
            # get has already happened. Re-gating there would loop the same prompt forever.
            if _asks_to_reset(prompt) and not skip_reset_gate:
                yield from self._reset_offer(prompt, mentions, resources)
                return
            if (project.control.snapshot().mode is Mode.ASK
                    and _looks_like_change_request(prompt)):
                yield from self._ask_mode_refusal(prompt)
                return
            # What this turn would be built on top of (#78). Checked here rather than read from the
            # rail's cache: the badge is a background reading and may be half a minute old, and a
            # turn is the moment the answer has to be right. Last of the gates, so a turn that was
            # never going to run doesn't stop to discuss the remote first.
            app = project.app_for_turn()
            if skip_incoming_gate:
                # The offer ANSWERED, not the gate bypassed — the check the offer just ran is what
                # `_incoming_now` holds. Remembered against the remote commit it was about, so a
                # person who chose to build on stands built on rather than being asked every turn.
                self._incoming_dismissed[app.app_id] = self._incoming_now().head
            else:
                found = self._check_remote(project)
                changed = self._incoming_files(app, found)
                if changed and self._incoming_dismissed.get(app.app_id) != found.head:
                    yield from self._incoming_offer(prompt, changed)
                    return
            # Last of the gates, and the only one that costs a gateway call: the model this turn
            # would route to, resolved against what the gateway will actually serve (#125). Here
            # rather than at the top of the turn because the approve fork above runs on a different
            # mode and checks itself — and because the two free gates should get their answer in
            # first. Still before a session is opened and before a single prompt goes out.
            refusal = self._slot_refusal_events(
                project, project.control.snapshot().mode, user_text=prompt)
            if refusal:
                yield from refusal
                return
            # A button answering the offer is a click, not a second typing of the request — the
            # prompt is already a bubble in the transcript, put there by _reset_offer. So the turn
            # gets the short line the click deserves, the way an Approve click does, instead of
            # echoing the same sentence twice. Whether they reset first is already on the record
            # above it as an `app-reset` marker.
            yield from self._build_stream(
                prompt, mentions, resources,
                user_text="Build it." if skip_reset_gate or skip_incoming_gate else None)
        except TurnWedged:
            # Swallowed, not re-reported: the turn already said what happened in its own stream, and
            # a traceback on top of it would only be a second, worse version of the same sentence.
            log.error("turn wedged and would not stop — keeping the turn lock; restart to clear")
        finally:
            # Everything here is skipped for a wedged turn — the lock, and the two cleanups that read
            # and write the working tree. The session would not confirm it stopped, so it may still be
            # writing there, and both letting the next turn in and healing the tree under it are the
            # exact collision `_turn_lock` exists to prevent (#39). Any OTHER failure still unwinds
            # normally: an exception is not evidence that OpenCode is loose in the tree.
            #
            # Read off the orchestrator rather than a local, so it holds however this generator
            # unwinds — a caller that walks away mid-stream raises GeneratorExit here, not
            # TurnWedged, and that must not hand the lock back either.
            if not self._turn_wedged:
                self._restore_attachments()   # before _recheck_app_data: it reads the tree this heals
                self._recheck_app_data()
                self._record_resource_usage()
                self._clear_turn_baseline()
                self._release_turn()

    def create_thread(self) -> dict:
        """A new Chat Thread in this project. Does not provision a Domino project."""
        self._flush_chat_save("leave")
        return ThreadStore(self._chat_project().record.path).create()

    def get_thread(self, thread_id: str) -> dict:
        if self._chat_dirty_thread and self._chat_dirty_thread != thread_id:
            self._flush_chat_save("leave")
        record = self._chat_project().record
        store = ThreadStore(record.path)
        row = store.get(thread_id)
        if row is None:
            raise KeyError(thread_id)
        handoffs = store.read_handoffs(thread_id)
        return {
            **row,
            "history": store.read_history(thread_id),
            "context": store.read_context(thread_id),
            "artifacts": store.read_artifacts(thread_id),
            "handoff": handoffs[-1] if handoffs else None,
            "planId": _thread_plan_id(record, thread_id),
        }

    def patch_thread(self, thread_id: str, body: dict) -> dict:
        store = ThreadStore(self._chat_project().record.path)
        if store.get(thread_id) is None:
            raise KeyError(thread_id)
        if isinstance(body, dict) and body.get("handoff") == "suppress":
            store.suppress_handoff(thread_id)
        row = store.update(
            thread_id,
            title=body.get("title") if isinstance(body, dict) else None,
            pinned=body.get("pinned") if isinstance(body, dict) else None,
        )
        if row is None:
            raise KeyError(thread_id)
        return row

    def delete_thread(self, thread_id: str) -> None:
        project = self._chat_project()
        store = ThreadStore(project.record.path)
        # Read the chips before the Thread goes: afterwards there is nothing left to say what it
        # fetched, and the files would sit in scratch for the life of the project.
        paths = [str(i.get("path") or "") for i in store.read_context(thread_id).get("items") or []]
        if not store.delete(thread_id):
            raise KeyError(thread_id)
        for path in paths:
            self._release_chat_file(project, path)

    def list_threads(self) -> list[dict]:
        """Every Thread, each carrying the Built App it bound last.

        ADR-0009 says a Build link naming no app resolves the Conversation's newest bound handoff.
        That answer is already on the Thread's own record, so the list carries it and the rail can
        move the whole of Build on the click rather than a network round trip later (#139) — the
        round trip is what draws a Conversation beside the app it did not bind.

        The newest BOUND entry, not the newest entry: a Thread may hand off more than once, and the
        one on top may still be an offer nobody has answered. Same read and same guard as
        `_recross_handoff` — an entry naming an app that has since been deleted names nothing.
        """
        store = ThreadStore(self._chat_project().record.path)
        rows = store.list()
        live = set(self._wm.app_ids())
        for row in rows:
            bound = [r for r in store.read_handoffs(str(row.get("id") or ""))
                     if r.get("status") == "bound" and str(r.get("appId") or "") in live]
            row["boundAppId"] = str(bound[-1]["appId"]) if bound else None
        return rows

    def thread_history(self, thread_id: str) -> list[dict]:
        return ThreadStore(self._chat_project().record.path).read_history(thread_id)

    def conversation_history(self, thread_id: str) -> list[dict]:
        """One Conversation's whole record — what was asked in Chat and what was done in Build —
        merged into the order it happened, every row labelled with the half it came from (#56).

        A SCAN, not a read of one file. The Build log is per Built App (#68) and one Thread can hand
        off more than once (#72), so a Conversation's build turns are spread over every app it
        drove. `history()` reads the SELECTED app's log, and a merge built on that would hide every
        other app this Conversation changed — whichever app happens to be on screen would decide
        what the Conversation appears to have done. Every app in the Project is read instead and
        filtered on the conversation tag. Rows already carry `app`, so a reader can still say which
        one each turn built.

        Order comes from the `at` stamp both writers apply (#51). Entries written before the stamp
        existed carry none: they sort first, Chat before Build. Two halves with no clock between
        them cannot be interleaved honestly, and Chat before Build is the order a Conversation with
        both actually had — Build only ever started after a handoff out of Chat.
        """
        record = self._chat_project().record
        rows = [{**row, "half": "chat"} for row in ThreadStore(record.path).read_history(thread_id)]
        for app_id in self._wm.app_ids():
            workspace = self._wm.app_workspace(self._project_id, app_id)
            # The same adoption `history()` does before it reads, and for the same reason: build
            # history predates conversation tagging, and `read_history` filters on the tag. Without
            # it an upgraded Project's merged view would be strictly emptier than the split view it
            # replaces — Build's whole transcript missing, and only under unified.
            self._adopt_legacy_build_history(workspace, record)
            rows += [{**row, "half": "build"} for row in workspace.read_history(thread_id)]
        # Stable, so rows sharing a stamp — a whole turn is written inside one second — keep the
        # order the log they came from has them in. The half is only ever the tiebreak for the
        # stampless rows above; for two stamped rows it agrees with what stability already gives,
        # because a second is the finest this clock reads and no finer order exists to recover.
        rows.sort(key=lambda row: (str(row.get("at") or ""), 0 if row["half"] == "chat" else 1))
        return rows

    def thread_context(self, thread_id: str) -> dict:
        return ThreadStore(self._chat_project().record.path).read_context(thread_id)

    def add_thread_context(self, thread_id: str, item: dict) -> dict:
        store = ThreadStore(self._chat_project().record.path)
        if store.get(thread_id) is None:
            raise KeyError(thread_id)
        row = dict(item or {})
        if _dataset_pseudo_path(row):
            row["path"] = None
        dataset_id = row.get("datasetId")
        rel = row.get("datasetRelPath")
        if str(row.get("kind") or "") == "file" and dataset_id and rel and not row.get("path"):
            try:
                # Fetched for the question, not for the app. `_confirm_handoff` is where a Thread
                # says it is becoming an app, and where these bytes reach `public/data/`.
                fetched = self.fetch_dataset_file_for_chat(str(dataset_id), str(rel))
                row["path"] = fetched.get("path")
            except (LookupError, FileNotFoundError, ValueError, AttachTooLarge,
                    ResourceUnavailable):
                # The chip is still worth adding: with no path, the turn prompt routes the agent
                # to the Domino data library instead. A fetch Domino refused must not take the
                # whole chip down with it.
                pass
        scope = row.get("scope") if isinstance(row.get("scope"), dict) else None
        # "table" belongs here as much as "data_source": the panel pins a table, and the client
        # flattens that to "data_source" before posting — but a stored row may carry either.
        if str(row.get("kind") or "") in ("data_source", "datasource", "table") and scope and scope.get("table"):
            source = self._context_source(row)
            if source is not None:
                # The chip's `name` is the table. Stamp the SOURCE's own Domino name, because that
                # is what `get_datasource()` takes and nothing else in the row answers it.
                row["sourceName"] = source.name
                cols = self._columns_for_context(source, scope)
                if cols:
                    row["columns"] = cols
        joined = self._join_project_on_mention(row)
        stored = store.add_context(
            thread_id, {k: v for k, v in row.items() if k not in _MEMBERSHIP_ONLY_FIELDS})
        # On the answer, not in the row: the flag says "membership changed just now", which is true
        # once. Persisted, it would say it again on every reload and the panel would re-announce a
        # join the user made days ago.
        return {**stored, "joinedProject": True} if joined else stored

    def _join_project_on_mention(self, row: dict) -> bool:
        """Put a catalogue Resource in the project because a Thread named it. True when this
        mention is what made it a member.

        Membership is the provisioning step. It exists for a machine reason, not a user one, so it
        happens as a side effect of the act that does have a reason behind it — naming the thing in
        a sentence — rather than standing in front of it as a gate.

        Parents only. A Dataset file or a warehouse table is reached by expanding a parent in the
        rail, which already means that parent is a member; and a leaf's own id is not one the rail
        can render a membership row for.
        """
        kind = str(row.get("kind") or "")
        rid = str(row.get("resourceId") or "").strip()
        name = str(row.get("name") or "").strip()
        if kind not in _MEMBERSHIP_PARENT_KINDS or not name:
            return False
        if not rid or rid.startswith(_LEAF_ID_PREFIXES):
            return False
        try:
            # Idempotent on id, so an existing member reports `added: False` and draws no flag.
            return bool(self.add_project_resource({
                "id": rid,
                "kind": kind,
                "name": name,
                "project": row.get("project"),
                "path": row.get("path"),
                "bindingKey": row.get("bindingKey"),
                # Everything `add_project_resource` keeps, so a mention writes the same row the
                # Browse Domino button writes. Six fields wrote a model with no alias, which is an
                # option the picker draws blank and cannot select.
                **{k: row[k] for k in _MEMBERSHIP_ONLY_FIELDS if row.get(k) is not None},
            })["added"])
        except (ValueError, OSError):
            # The chip is worth having either way. A membership row the disk refused must not take
            # the mention down with it.
            return False

    @staticmethod
    def _context_source_id(item: dict) -> str:
        """The Data Source id behind a table chip, from whichever field carried it."""
        bk = item.get("bindingKey")
        if isinstance(bk, (list, tuple)) and len(bk) >= 2 and bk[1]:
            return str(bk[1])
        parent = _bare_kind_id(str(item.get("parentId") or ""), "data_source")
        if parent:
            return parent
        candidate = _bare_kind_id(str(item.get("resourceId") or ""), "data_source")
        if candidate and not candidate.startswith(("table:", "dsfile:", "file:", "pin:")):
            return candidate
        return ""

    def _context_source(self, item: dict):
        """The Data Source a table chip belongs to, or None when it cannot be resolved."""
        source_id = self._context_source_id(item)
        if not source_id:
            return None
        try:
            return self._data_source(source_id)
        except (LookupError, ValueError, ResourceUnavailable):
            return None

    def _columns_for_context(self, source, scope: dict) -> list[dict]:
        """Column names and types for a table chip. Empty when the store will not answer."""
        try:
            columns = self._resources.list_columns(
                source,
                _level(str(scope.get("database") or "")),
                _level(str(scope.get("schema") or "")),
                _level(str(scope.get("table") or "")),
            )
        except (LookupError, ValueError, ResourceUnavailable):
            return []
        return [{"name": c.name, "type": c.type, "table": c.table} for c in columns]

    def remove_thread_context(self, thread_id: str, item_id: str) -> bool:
        project = self._chat_project()
        store = ThreadStore(project.record.path)
        row = next((i for i in store.read_context(thread_id).get("items") or []
                    if i.get("id") == item_id), None)
        removed = store.remove_context(thread_id, item_id)
        if removed and row is not None:
            self._release_chat_file(project, str(row.get("path") or ""))
        return removed

    def _release_chat_file(self, project: Project, path: str) -> bool:
        """Delete a file fetched for a chip that is gone. True when the bytes were released.

        A chip closed is the only signal a Chat fetch is finished with, and `_copied_bytes` counts
        every fetch against the cap — so without this, a long-lived project fills up scratch and
        then quietly refuses new fetches, falling back to the data-library route with nothing on
        screen to say why.

        Two things hold a file back. Another Thread still naming it: a fetch is shared, and the
        person closing one chip is not speaking for the other conversation. And a handoff that
        linked the app's data path at these very bytes: deleting them would leave the app pointing
        at nothing. Only what Sage fetched is deleted — a mounted Dataset is a symlink here, so the
        Dataset's own bytes are never what goes.
        """
        if not path.startswith(_CHAT_DATA_PREFIX):
            return False
        store = ThreadStore(project.record.path)
        for thread in store.list():
            for item in store.read_context(thread["id"]).get("items") or []:
                if str(item.get("path") or "") == path:
                    return False
        try:
            dest = _safe_join(project.record.path, path)
        except ValueError:
            return False
        if not dest.is_symlink() and not dest.is_file():
            return False
        if any(_links_at(project.workspace.path, e["path"], dest) for e in project.attached):
            return False
        try:
            dest.unlink()
        except OSError:
            log.warning("chat: could not release the fetched copy of %s", path)
            return False
        _prune_empty_dirs(
            dest.parent, _safe_join(project.record.path, _CHAT_DATA_PREFIX.rstrip("/")))
        return True

    def chat_stream(self, thread_id: str, prompt: str, *, timeout_s: float | None = None,
                    already_asked: bool = False):
        """A Chat turn: sage-chat, no plan gate, no typecheck. History goes on the Thread.

        `already_asked` means this question is on the Thread and was offered Build rather than an
        answer, and the person declined the offer (`decline_handoff_stream`). The turn runs; the two
        things that belong to asking do not. Recording it again would print the person's sentence
        twice, which is exactly the transcript they produced by hand when declining did nothing.
        """
        # Waits its turn rather than refusing (#79). `app=False`: Chat writes Artifacts under the
        # Thread's own `examples/`, so which Built App the rail points at is not something this turn
        # was written against.
        ticket = _TurnTicket(new_id("turn"))
        yield from self._acquire_turn(ticket, kind="chat", conversation=thread_id, prompt=prompt,
                                      app=False)
        if not ticket.granted:
            return
        # The lock goes at `done`, not at the end of this generator. What comes after `done` is
        # aftercare — classify the turn for a Build offer, compact the session, commit and push —
        # and it used to run with the lock still held, so the next question was refused as busy for
        # as long as the aftercare took. The answer is on screen by then, which is exactly when
        # someone types again: the wait they felt was the previous turn tidying up.
        #
        # Not "the aftercare is safe to race". Each piece that touches something the next turn also
        # touches takes the lock back for itself — _maybe_compact_chat, because it rewrites the
        # OpenCode session the next prompt runs in, and _flush_chat_save, because it commits the
        # working tree. The classifier needs neither: it calls the gateway directly and writes only
        # this Thread's handoff.json, so it runs off the lock as it is.
        holding = True
        try:
            for ev in self._chat_stream(thread_id, prompt, timeout_s=timeout_s,
                                        already_asked=already_asked):
                if holding and ev.get("type") == "done":
                    # Released before the yield rather than after, so a client that hangs up on
                    # `done` still frees it here. Baseline first: it means "no turn running", and a
                    # turn that started after the release would have set its own for us to wipe.
                    self._clear_turn_baseline()
                    self._release_turn()
                    holding = False
                yield ev
        finally:
            if holding:
                self._clear_turn_baseline()
                self._release_turn()

    def flush_chat_save(self) -> dict | None:
        """Push dirty Chat files now (leaving Chat, switching Thread). No-op if nothing is dirty."""
        return self._flush_chat_save("leave")

    def _cancel_chat_idle_save(self) -> None:
        timer = self._chat_save_timer
        self._chat_save_timer = None
        if timer is not None:
            timer.cancel()

    def _arm_chat_idle_save(self) -> None:
        self._cancel_chat_idle_save()
        timer = threading.Timer(self._chat_save_idle_s, self._on_chat_save_idle)
        timer.daemon = True
        self._chat_save_timer = timer
        timer.start()

    def _on_chat_save_idle(self) -> None:
        if self._turn_wedged:
            # The lock is held for good (#39) and this would re-arm against it every 30 seconds for
            # the life of the process. The Chat files stay on disk unharmed; the save that commits
            # them runs after the restart the wedge card asks for.
            log.warning("chat: save deferred to the restart — the workspace is wedged")
            return
        if self._turn_lock.locked():
            self._arm_chat_idle_save()
            return
        self._flush_chat_save("idle")

    def _flush_chat_save(self, reason: str, *, holding_turn: bool = False) -> dict | None:
        """Commit + push if Chat has unsaved files. Returns the `saved` event, or None."""
        self._cancel_chat_idle_save()
        if not self._chat_dirty:
            return None
        if holding_turn:
            return self._chat_save_now(reason)
        # Hold the lock for the save rather than test it and let go. This walks the tree, commits,
        # pulls, and can run the conflict-resolution turn; a turn that starts partway through gets
        # its half-written files committed. Testing left that window open, and the post-turn save
        # now runs off the lock (see chat_stream), so the window is the moment someone is most
        # likely to send the next thing. Losing the race only defers the commit.
        if not self._turn_lock.acquire(blocking=False):
            # Not re-armed on a wedge: the lock never comes back, so this would only queue a timer
            # per attempt against a workspace that has to be restarted anyway (see _on_chat_save_idle).
            if not self._turn_wedged:
                self._arm_chat_idle_save()
            return None
        try:
            return self._chat_save_now(reason)
        finally:
            self._release_turn()

    def _chat_save_now(self, reason: str) -> dict | None:
        """The save itself. The caller owns the turn lock; this decides what to do with the result."""
        try:
            project = self.project(start_preview=False)
            result = self._save_to_git(project, f"chat ({reason})")
        except Exception:
            log.exception("chat save failed")
            self._arm_chat_idle_save()
            return {"type": "saved", "ok": False, "pushed": False, "detail": "chat save failed"}
        if result is None or result.get("ok"):
            self._chat_dirty = False
            self._chat_dirty_thread = None
        else:
            self._arm_chat_idle_save()
        return result

    def _after_chat_turn(self, thread_id: str, *, immediate: str | None) -> dict | None:
        self._chat_dirty = True
        self._chat_dirty_thread = thread_id
        if immediate:
            # No `holding_turn`: the turn let the lock go at `done`, so this save takes it itself.
            # If the next turn got there first the commit waits for the idle timer, which is what
            # an ordinary text turn already does — later is fine for a commit.
            return self._flush_chat_save(immediate)
        self._arm_chat_idle_save()
        return None

    def decline_handoff_stream(self, thread_id: str):
        """`Not now` on a Build offer: stop offering, and answer the question if one is waiting.

        Suppression stays permanent, and deliberately so — it is the person saying stop, and the spec
        says as much (docs/workbench/handoff.md §2, criterion 10). What was wrong was not that the
        offer never came back. It was that the question underneath it never got answered by anything:
        the explicit-build path offers Build INSTEAD of running a turn, so declining left a question
        on the Thread with no reply, and the only way forward was to type it again.

        Which is worse than it sounds, because the suppression this same click wrote is what let the
        retyped copy through — so the person's own workaround silently changed what Sage did with it.

        The pending question is read from the Thread here rather than taken from the caller. The
        browser has the text on screen and could send it, but then a stale tab could put a turn under
        a question it does not match, and the transcript is the only place this can be settled.
        """
        store = ThreadStore(self._chat_project().record.path)
        if store.get(thread_id) is None:
            yield {"type": "error", "message": "Unknown thread"}
            yield {"type": "done", "ok": False, "decision": "unknown thread"}
            return
        store.suppress_handoff(thread_id)
        pending = chat_handoff.unanswered_ask(store.read_history(thread_id))
        if not pending:
            # An offer the classifier raised, and the turn that raised it already answered. There is
            # nothing to run, and running the last question again would answer it twice.
            yield {"type": "done", "ok": True, "decision": "suppressed"}
            return
        yield from self.chat_stream(thread_id, pending, already_asked=True)

    def _explicit_handoff(self, store: ThreadStore, thread_id: str, prompt: str) -> dict | None:
        """The regex half of handoff detection. No model call, so it is safe to run BEFORE a turn.

        Silent while this Thread's newest handoff is unresolved, and for good once one was
        declined — someone who chose to stay in Chat keeps asking in Chat.
        """
        try:
            if not chat_handoff.should_classify(store.read_handoffs(thread_id)):
                return None
            if not chat_handoff.looks_like_build_request(prompt):
                return None
            store.mark_handoff_suggested(thread_id)
            return {"type": "handoff-suggest", "reason": "explicit"}
        except Exception:
            log.exception("handoff: explicit detect failed")
            return None

    def _maybe_suggest_handoff(self, store: ThreadStore, project: Project,
                               thread_id: str, prompt: str) -> dict | None:
        """Detect once: persist handoff.json and emit a callout, or stay silent. Never raises."""
        try:
            explicit = self._explicit_handoff(store, thread_id, prompt)
            if explicit:
                return explicit
            if not chat_handoff.should_classify(store.read_handoffs(thread_id)):
                return None
            thread = store.get(thread_id) or {}
            hit = chat_handoff.wants_an_app(
                title=thread.get("title") or "",
                user=prompt,
                assistant=chat_handoff.last_assistant_text(store.read_history(thread_id)),
                gateway=project.shim.gateway,
                catalog=project.shim.catalog,
                thread=thread_id,
                session=project.session_id,
                version=project.shim.version,
            )
            if not hit:
                return None
            store.mark_handoff_suggested(thread_id)
            return {"type": "handoff-suggest", "reason": "classifier"}
        except Exception:
            log.exception("handoff: detect failed")
            return None

    def draft_handoff_plan(self, thread_id: str) -> dict:
        """Write a plan document for this Thread by running sage-plan in its own session, and open
        the sheet payload. Creates no app: that is what confirming does (ADR-0008).

        Idempotent once the Thread's handoff names a plan document. Does not teleport into Build."""
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self._turn_wedged, "try again")
        try:
            return self._draft_handoff_plan(thread_id)
        finally:
            self._release_turn()

    def confirm_handoff(self, thread_id: str, include: dict | None = None,
                        target: dict | None = None) -> dict:
        """Write the confirm files, upsert Bindings, mark bound. Does not run implement.

        `target` says which Built App this handoff is for: `{"appId": "app_..."}` names one that
        already exists, and anything else — absent, empty, `{"appId": ""}` — means a new one. The
        default lives HERE rather than in the sheet's markup, so a caller that says nothing gets a
        new app and never someone else's (docs/workbench/handoff.md §4, #73)."""
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self._turn_wedged, "try again")
        try:
            # `_app_lock` as well, because confirming BINDS an app and then keeps reading the one it
            # bound — the plan, the handoff note, the `bound` mark, the plan document's `appId`. A
            # switch is no longer refused while a turn holds the lock (#77), so without this a click
            # in the rail could land between the bind and those writes and send them elsewhere.
            with self._app_lock:
                return self._confirm_handoff(thread_id, include or {}, target or {})
        finally:
            self._release_turn()

    def recross_handoff(self, thread_id: str, include: dict | None = None,
                        plan_id: str = "") -> dict:
        """Redo a confirmed handoff's crossing with different answers (#60).

        Deliberately NOT a second confirm. Confirming writes the plan card, and that card is the
        handoff's one receipt — running the confirm again would hang a second card off the same
        crossing, which is the thing the card exists to avoid. So this rewrites what crosses,
        leaves the plan and the app alone, and appends a row the card folds onto the receipt it
        already has.

        The target is not a parameter and never becomes one: which Built App a handoff lands in is
        decided once, on the sheet, per handoff (ADR-0008). `plan_id` is not a target — it names
        WHICH of this Conversation's handoffs the card belongs to, and one Conversation may have
        made several, into several apps. Without it the newest would answer for all of them, and
        pressing Change on the first card would rewrite the second app's crossing.
        """
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self._turn_wedged, "try again")
        try:
            # `_app_lock` for the same reason confirming takes it: this selects an app and then
            # writes into the one it selected.
            with self._app_lock:
                return self._recross_handoff(thread_id, include or {}, plan_id)
        finally:
            self._release_turn()

    def _recross_handoff(self, thread_id: str, include: dict, plan_id: str) -> dict:
        chat = self._chat_project()
        store = ThreadStore(chat.record.path)
        if store.get(thread_id) is None:
            raise KeyError(thread_id)
        bound = [r for r in store.read_handoffs(thread_id) if r.get("status") == "bound"]
        if plan_id:
            bound = [r for r in bound if str(r.get("planId") or "") == plan_id]
        if not bound:
            raise ValueError("no crossing")
        handoff_row = bound[-1]
        app_id = str(handoff_row.get("appId") or "")
        if not app_id or app_id not in self._wm.app_ids():
            raise ValueError("unknown app")
        self._wm.select(app_id)
        project = self._bind_app(chat, self._wm.ensure(self._project_id, seed_app=True))
        crossed = self._write_crossing(project, store, thread_id, include)
        # Only what crossed. Where it went rides on the row the confirm wrote and is merged back on
        # by the reader, so a Change can never quietly re-answer the question it does not ask.
        project.workspace.append_history(
            {"type": "handoff-recrossed", "planId": str(handoff_row.get("planId") or ""),
             "crossed": crossed}, thread_id)
        self._flush_chat_save("handoff", holding_turn=True)
        return {"ok": True, "threadId": thread_id, "appId": app_id,
                "planId": str(handoff_row.get("planId") or ""), "crossed": crossed}

    def cancel_plan(self, conversation: str = "", plan_id: str = "") -> dict:
        """Archive the plan awaiting approval. Idempotent: with no live plan this does nothing.

        `conversation` is the Conversation whose card pressed it, and it is what makes Undo
        readable after a reload (#60): the card is rebuilt from the transcript, so a cancel that
        left no row there would say nothing the next time the person opened it. Recorded only when
        a plan was actually archived, so pressing twice leaves one row rather than two — and the
        gate's own plan card, which names no Conversation, archives exactly as it always did.

        `plan_id` is the document the card is showing, and a card that names one it is no longer
        holding cancels nothing. A tab left open on a plan another Conversation has since
        superseded (#59) still draws it as pending, and without this that Undo would archive the
        NEWER plan — dismissing a plan the person never looked at, on behalf of a card that was
        already out of date.
        """
        workspace = self.project().workspace
        live_doc_id = workspace.live_plan_doc_id()
        if plan_id and plan_id != live_doc_id:
            return {"cancelled": True, "archived": False}
        archived = workspace.archive_plan(cancelled=True)
        if archived is not None and conversation:
            workspace.append_history(
                {"type": "plan-cancelled", "planId": plan_id or live_doc_id}, conversation)
        return {"cancelled": True, "archived": archived is not None}

    def _handoff_sheet_payload(self, store: ThreadStore, thread_id: str, project: Project,
                               plan_md: str, handoff: dict) -> dict:
        thread = store.get(thread_id) or {}
        return {
            "ok": True,
            "threadId": thread_id,
            "plan": plan_md,
            "title": chat_handoff.plan_title(plan_md) or thread.get("title") or "App",
            "handoff": handoff,
            "untitled": project.record.is_untitled(),
            "artifacts": store.read_artifacts(thread_id),
            "context": store.read_context(thread_id).get("items") or [],
            # The apps this handoff could build into, so the sheet can offer them (#73). The rail's
            # `selected` flag is dropped on the way out: the only default is New app, and a payload
            # that named one of these would give the markup something to preselect — which is the
            # silent overwrite this row exists to prevent (docs/workbench/handoff.md §4).
            "apps": [{k: v for k, v in row.items() if k != "selected"} for row in self.list_apps()],
        }

    @staticmethod
    def _handoff_plan_markdown(record: ProjectRecord, handoff: dict) -> str:
        """The plan a Thread's handoff is holding, read from the document it lives in.

        Not `.sage/plan.md`: that is the copy the BUILDER consumes, and it does not exist until the
        handoff is confirmed, because until then there is no app to put it in (ADR-0008). The
        document is the Project's and was written the moment the sheet was drafted, so it is the
        only place to ask in between — and it is the copy an edit on the plan page lands in, so
        confirming after an edit builds what the person actually approved.
        """
        plan_id = str((handoff or {}).get("planId") or "")
        doc = record.read_plan_doc(plan_id) if plan_id else None
        return str((doc or {}).get("markdown") or "").strip()

    def _draft_handoff_plan(self, thread_id: str) -> dict:
        # Chat's project, deliberately unseeded: drafting a plan must not create an app. Somebody
        # who opens the sheet and closes it again has asked for nothing, and an app minted here
        # would be one nobody asked for — a Built App is born when a handoff is CONFIRMED
        # (ADR-0008). Everything written here is the Project's: the plan document, and the handoff
        # row on the Thread.
        project = self._chat_project()
        store = ThreadStore(project.record.path)
        if store.get(thread_id) is None:
            raise KeyError(thread_id)
        existing = store.read_handoff(thread_id) or {}
        plan_md = self._handoff_plan_markdown(project.record, existing)
        if existing.get("status") in ("planned", "bound") and plan_md:
            return self._handoff_sheet_payload(store, thread_id, project, plan_md, existing)

        thread = store.get(thread_id) or {}
        history = store.read_history(thread_id)
        context = store.read_context(thread_id).get("items") or []
        artifacts = store.read_artifacts(thread_id)
        digest = chat_handoff.draft_digest(
            title=thread.get("title") or "",
            asked=chat_handoff.user_texts(history),
            context=context,
            artifacts=artifacts,
        )
        # The digest rides in the prompt rather than through a file: `plan_prompt` embeds it, and
        # the app that would hold `.sage/handoff.md` does not exist yet. The confirm writes that
        # file, for the implement turn that reads it (chat_handoff.implement_note).
        prompt = chat_handoff.plan_prompt(thread_id, digest,
                                          voice=_PLAN_VOICE, shape=_PLAN_SHAPE)
        client = self._ensure_opencode()
        plan_md = self._run_sage_plan(
            project, prompt, self._ensure_thread_session(store, thread_id, project, client))
        if not plan_md:
            raise ValueError("empty plan")
        # Same document the gate creates, and it records its Thread the same way. No `app_id`: the
        # app does not exist until the handoff is confirmed, and that is what stamps it.
        _warn_if_shapeless("chat handoff", plan_md)
        plan_id = project.record.create_plan_doc(
            plan_md,
            title=chat_handoff.plan_title(plan_md) or thread.get("title") or "App",
            author=_viewer_id(),
            origin_thread_id=thread_id,
        )["id"]
        handoff = store.mark_handoff_planned(thread_id, plan_id)
        self._flush_chat_save("plan", holding_turn=True)
        return self._handoff_sheet_payload(store, thread_id, project, plan_md, handoff)

    def _run_sage_plan(self, project: Project, prompt: str, session_id: str) -> str:
        """sage-plan on a session the caller picked. No typecheck. Read-only arming so src/ stays put.

        The session is the caller's because the two callers stand in different places: a gated build
        turn plans in the app, and a Chat handoff plans in the Thread — before an app exists, which
        is a directory OpenCode could not have opened.
        """
        client = self._ensure_opencode()
        sid = session_id
        project.active_session_id = sid
        token = project.control.arm_read_only("plan")
        try:
            seen = self._seen_baseline(client, sid)
            client.send_prompt(sid, prompt, agent="sage-plan")
            client.wait_for_idle(sid)
            parts: list[str] = []
            for m in client.messages(sid):
                if m.get("type") != "assistant":
                    continue
                for i, part in enumerate(m.get("content", [])):
                    if _part_key(m, i, part) in seen:
                        continue
                    if part.get("type") == "text" and part.get("text"):
                        parts.append(part["text"])
            return _tidy_plan("\n".join(parts))
        finally:
            project.control.disarm_read_only(token)
            project.active_session_id = None

    def _confirm_handoff(self, thread_id: str, include: dict, target: dict) -> dict:
        # Read and refuse BEFORE anything is created: this is where a Built App is born (ADR-0008),
        # and a confirm that cannot find its plan must leave no app behind.
        chat = self._chat_project()
        store = ThreadStore(chat.record.path)
        if store.get(thread_id) is None:
            raise KeyError(thread_id)
        handoff_row = store.read_handoff(thread_id) or {}
        plan_md = self._handoff_plan_markdown(chat.record, handoff_row)
        if not plan_md:
            raise ValueError("no plan")
        # A named app that is not there is refused rather than quietly turned into a new one: the
        # person picked a target, and building somewhere else is the surprise this row prevents.
        chosen = str(target.get("appId") or "").strip()
        if chosen and chosen not in self._wm.app_ids():
            raise ValueError("unknown app")
        # Read before the app is opened, because opening is where one is born. "New or one you
        # already had" is a fact the card reports (#60), and afterwards there is nothing left to
        # tell the two apart — a minted app and a reselected one look identical on disk.
        existing_before = set(self._wm.app_ids())
        # The app: the one the sheet named, or a directory named for a newly minted id.
        project = self._open_app(chat, handoff_row, chosen)
        # The builder's own copies, which only have somewhere to live now. `plan.md` is the one-shot
        # handoff the implement turn consumes and archives; the plan card is what Build opens on.
        # An existing app may already hold a plan awaiting approval, which the sheet warns about
        # and this steps aside rather than overwrites (#59).
        self._supersede_live_plan(project, project.workspace,
                                  str(handoff_row.get("planId") or ""), thread_id)
        project.workspace.write_plan(plan_md, str(handoff_row.get("planId") or ""))
        # The crossing happens before the card is written, because the card carries the receipt for
        # it (#60). One row, not two: a second row is a second card, and only one card appears for
        # a handoff.
        crossed = self._write_crossing(project, store, thread_id, include)
        crossed.update({
            "conversation": thread_id,
            "appId": project.workspace.app_id,
            "appName": project.workspace.display_name(),
            "newApp": project.workspace.app_id not in existing_before,
        })
        project.workspace.append_history(
            {"type": "plan-proposed", "plan": plan_md, "kind": "plan",
             "planId": str(handoff_row.get("planId") or ""), "steps": 0,
             "crossed": crossed}, thread_id)
        project.workspace.append_history(
            {"type": "done", "ok": True, "decision": "awaiting approval"}, thread_id)
        handoff = store.mark_handoff_bound(thread_id, project.workspace.app_id)
        # A plan is drafted in a Thread, before the app exists, so it cannot be born inside one —
        # it stays with the Project and gains its app reference here, at the moment it binds
        # (ADR-0008). This is what lets the plan page offer "Open in Builder" instead of "Build this".
        plan_id = str((handoff or {}).get("planId") or "")
        if plan_id:
            project.record.patch_plan_doc_meta(plan_id, appId=project.workspace.app_id)
        self._flush_chat_save("handoff", holding_turn=True)
        return {
            "ok": True,
            "threadId": thread_id,
            "handoff": handoff,
            "untitled": project.record.is_untitled(),
            "title": chat_handoff.plan_title(plan_md),
        }

    def _write_crossing(self, project: Project, store: ThreadStore, thread_id: str,
                        include: dict) -> dict:
        """Write what this Conversation carries into a Built App, and report what went.

        Two doors call it: the confirm that makes the crossing, and Change on the plan card, which
        redoes it with different answers (#60). The receipt it returns is what that card reads, so
        it names real files and real rows rather than repeating the answers it was handed — a
        handoff nobody can inspect is the magic docs/workbench/handoff.md §1 forbids.

        What it does NOT touch is the plan and the app. The plan is not one of the answers (a
        handoff without one is not this flow), and the target is a per-handoff decision the sheet
        asks every time (ADR-0008).
        """
        include_resources = include.get("resources", True)
        include_artifacts = include.get("artifacts", True)
        include_transcript = include.get("transcript", False)
        context = store.read_context(thread_id).get("items") or []
        artifacts = store.read_artifacts(thread_id)
        thread = store.get(thread_id) or {}
        digest = chat_handoff.confirm_digest(
            chat_handoff.draft_digest(
                title=thread.get("title") or "",
                asked=chat_handoff.user_texts(store.read_history(thread_id)),
                context=context if include_resources else [],
                artifacts=artifacts if include_artifacts else [],
            ),
            artifacts=artifacts,
            context=context,
            include_artifacts=include_artifacts,
            include_resources=include_resources,
        )
        (project.workspace.path / ".sage" / "handoff.md").write_text(digest)
        transcript_path = project.workspace.path / ".sage" / "handoff-transcript.md"
        if include_transcript:
            transcript_path.write_text(chat_handoff.transcript_markdown(store.read_history(thread_id)))
        else:
            transcript_path.unlink(missing_ok=True)
        if include_resources:
            for item in context:
                binding = chat_handoff.binding_from_context(item)
                if binding is not None:
                    self._bind_from_handoff(binding)
                self._promote_chat_file(item)
        charts = [{"title": str(a.get("title") or a.get("name") or ""),
                   "path": str(a.get("path") or "")}
                  for a in artifacts] if include_artifacts else []
        # The same list the sheet showed before confirming: where to go and look. Bindings are
        # named whenever the file is there rather than only when this crossing added a row —
        # turning Resources off does not withdraw a Binding already made (the sheet says so), and
        # a receipt that stopped naming the file would read as an app connected to nothing while
        # it goes on reading those Resources.
        files = [
            ".sage/plan.md",
            ".sage/handoff.md",
            include_artifacts and charts and f"examples/{thread_id}/",
            (project.workspace.path / ".sage" / "bindings.json").is_file()
            and ".sage/bindings.json",
            include_transcript and ".sage/handoff-transcript.md",
        ]
        return {
            "resources": include_resources,
            "artifacts": include_artifacts,
            "transcript": include_transcript,
            "charts": charts,
            "context": [str(i.get("name") or "") for i in context] if include_resources else [],
            "files": [f for f in files if f],
        }

    def _open_app(self, project: Project, handoff_row: dict, chosen: str) -> Project:
        """The Built App a confirmed handoff builds into, selected and ready.

        A NEW one unless the sheet named one that already exists, because a Project holds many and
        confirming is where one is born (ADR-0008): a second conversation that wants a dashboard
        gets a dashboard, rather than writing over the one the first conversation is still using.
        `chosen` is the sheet's answer, and is empty unless a person picked a row — New app is its
        default, and this is that default (docs/workbench/handoff.md §4, #73).

        A handoff that already bound stamped its app on the plan document, and that app is the
        fallback: confirming the same sheet twice reopens it instead of minting a twin nobody asked
        for and nothing points at. A FALLBACK and not an override, because the sheet is served again
        on a bound entry (`_draft_handoff_plan`) — so a bound app that won would quietly swallow the
        answer to the question this row asks, which is criterion 11's failure from the other side.
        Saying nothing is the only thing that reaches the old app, and a double-confirm says nothing.
        """
        plan_id = str((handoff_row or {}).get("planId") or "")
        doc = project.record.read_plan_doc(plan_id) if plan_id else None
        # The entry names its own app from the moment it binds. The plan document is stamped at that
        # same moment and answers for entries written before the record became a list.
        bound = str((handoff_row or {}).get("appId") or (doc or {}).get("appId") or "")
        # In order, not `chosen or bound`: an app that has since been deleted must not swallow the
        # answer either, so each candidate has to survive the membership test on its own.
        for candidate in (chosen, bound):
            if candidate and candidate in self._wm.app_ids():
                self._wm.select(candidate)
                return self._bind_app(project, self._wm.ensure(self._project_id, seed_app=True))
        opened = self._bind_app(project, self._wm.create_app(self._project_id))
        # The name starts as the plan's title, and is the person's to change from there.
        title = str((doc or {}).get("title") or "")
        if title:
            opened.workspace.set_display_name(title)
        return opened

    def _promote_chat_file(self, item: dict) -> None:
        """Move a Dataset file fetched for a question into the app's own data tree.

        Chat fetches into scratch because a question has no app to serve bytes to. A confirmed
        handoff is the moment that stops being true: the app this Thread becomes is a static build
        that fetches `data/<slug>/<name>` over HTTP, and only attach_file writes the manifest entry
        that rehydrates the file at publish. A Dataset file chip is not a Binding — its id is a
        `dsfile:` leaf — so it is not covered by the loop this sits in.

        The scratch bytes are handed over rather than fetched again, and stay where they are: the
        Thread's chip still names that path, and Chat goes on working after the handoff.
        """
        if str(item.get("kind") or "") != "file":
            return
        dataset_id = str(item.get("datasetId") or "")
        rel = str(item.get("datasetRelPath") or "")
        if not dataset_id or not rel:
            return
        path = str(item.get("path") or "")
        local = None
        if path.startswith(_CHAT_DATA_PREFIX):
            fetched = _safe_join(self.project().record.path, path)
            # A symlink there points at the mount, and attach_file makes that link itself.
            local = fetched if fetched.is_file() and not fetched.is_symlink() else None
        try:
            self.attach_file(dataset_id, rel, local_source=local)
        except (LookupError, FileNotFoundError, ValueError, AttachTooLarge, ResourceUnavailable):
            # The handoff is worth more than one file. The app is built from the plan either way,
            # and the Data panel still offers the attach by hand.
            log.warning("handoff: could not attach %s from Dataset %s", rel, dataset_id)

    def _bind_from_handoff(self, binding: Binding) -> None:
        """Record one Chat context row as a Binding, resolving a Data Source the way the rail does.

        `binding_from_context` is a parser with no listing to ask, so the only name it has for a
        Data Source row is the chip's — and for a TABLE chip that is the table's name, not the
        source's. `Binding.name` is what a published app calls `get_datasource` with (see the
        template's `serve.py`), so a handoff that bound a table chip shipped an app that could
        never open its own store: `get_datasource("clickstream")` when the source is
        `BigQuery_Demo`. It also left `connector_type` empty, which is what decides whether the
        Scope can travel as configuration, and skipped the column read the build agent writes SQL
        from. `bind_data_source` does all three, off the same live listing the cascade used.

        A source that is no longer in the listing still gets recorded, unresolved. The creator did
        pick it, the app names it in `IN THIS APP`, and the alternative — losing the whole handoff
        because one source was renamed — is worse than an app that says it cannot open its data.
        The other two kinds keep the plain record: `bind_model_api` raises when a credential is
        missing, and a handoff is not the moment to refuse over one.
        """
        if binding.kind != KIND_DATA_SOURCE:
            self._record(binding)
            return
        try:
            self.bind_data_source(binding.id, binding.database or "", binding.schema or "",
                                  binding.table or "")
        except (LookupError, ResourceUnavailable) as e:
            log.warning("handoff: Data Source %s did not resolve (%s) — recording it unresolved, "
                        "the app will not be able to open it until it is re-bound", binding.id, e)
            self._record(binding)

    def _chat_agents_md(self) -> str:
        """The stub AGENTS.md for the Chat workdir. The rules are NOT here.

        `template/chat/AGENTS.md` is the source of truth for what sage-chat is told, and it is
        inlined into `opencode.json` as that agent's prompt — the copy OpenCode is guaranteed to
        send. Writing the same body here as well put every rule in front of the model twice on
        every turn, roughly 1,700 tokens the question had to be read past before an answer could
        start; a one-word greeting paid for all of it. OpenCode loads AGENTS.md from the session
        directory, and the session directory is this one.

        A stub rather than no file at all: an absent AGENTS.md lets OpenCode walk up for one, and
        an AGENTS.md added at the Project root later would then reach a Thread. Build's rules are
        not this Thread's.
        """
        from .brand import apply_voice
        return apply_voice(
            "# This Thread\n\n"
            "You are Sage's chat agent. Your instructions arrive with the turn — follow those. "
            "There is nothing further to read in this directory.\n"
        )

    def _chat_mention_files(self, prompt: str, items: list[dict], workspace: Path) -> list[dict] | None:
        """Descriptors for files the user @named, so OpenCode sees the path not just a chip."""
        tokens = {m.group(1).lower() for m in _CHAT_AT.finditer(prompt or "")}
        if not tokens:
            return None
        out: list[dict] = []
        seen: set[str] = set()
        for it in items:
            if str(it.get("kind") or "") not in ("file", "artifact"):
                continue
            path = str(it.get("path") or "")
            if not path:
                continue
            name = str(it.get("name") or Path(path).name)
            if not any(_at_token_hits(t, name, path) for t in tokens):
                continue
            if path in seen:
                continue
            try:
                real = Path(path) if Path(path).is_absolute() else _safe_join(workspace, path)
                d = describe(str(real))
            except (ValueError, OSError, TypeError):
                continue
            seen.add(path)
            out.append({
                "path": path,
                "name": name,
                "summary": str(d.get("summary") or ""),
                "detail": str(d.get("detail") or ""),
            })
        return out or None

    def _ensure_thread_session(self, store: ThreadStore, thread_id: str, project: Project,
                               client: OpenCodeClient) -> str:
        # Chat stands at the Project root, where its Threads, Artifacts and scratch live. The one
        # thing it borrows from the app is `public/data/`, and only once an app exists to borrow
        # from: linking it would otherwise create the app directory a confirmed handoff is what
        # creates (ADR-0008).
        has_app = project.workspace.exists()
        work = str(ensure_chat_workdir(
            project.record.path, self._chat_agents_md(),
            data_dir=project.workspace.path / "public" / "data" if has_app else None))
        # That link creates `public/data/` in order to point at it, so the tree can now exist
        # before anything has been attached. It must be out of git either way: the gitignore line
        # is what keeps Dataset bytes from ever reaching the app's repo.
        if has_app:
            self._ensure_gitignored(project.workspace.path, "public/data/")
        rec = store.read_session(thread_id) or {}
        sid = rec.get("session_id")
        if sid and rec.get("directory") == work:
            try:
                client.messages(sid)
                return sid
            except httpx.HTTPStatusError:
                sid = None
        sid = client.create_session(directory=work)
        store.write_session_id(thread_id, sid, directory=work)
        return sid

    @staticmethod
    def _plan_state_note(entries: list[dict] | None) -> str:
        """What is true about this Conversation's plan, said to the turn that would otherwise guess.

        sage-chat cannot find this out and has every reason to invent it. Asked to move the work to
        Build, the helpful-sounding reply is that it is already waiting there — and live, that is the
        reply: "Head over to the Build tab in this project and the plan is already waiting there",
        followed by a confident list of the sections it contained. There was no plan. There was no
        app. The Build tab the person opened said "No plan yet".

        A prohibition alone would not hold. "Never claim a plan exists" asks the model to weigh a
        rule against a sentence that reads as helpful, about a fact it has no way to check. So it is
        given the fact instead, on every turn, which makes the true answer also the easy one.

        Two sentences, because this is read on every Chat turn and most of them are about data.
        """
        if chat_handoff.has_plan(entries):
            return ("This conversation has a plan and it is in Build. You did not write it — do not "
                    "restate what it contains, and do not describe it as yours.")
        return ("This conversation has no plan and no app, and this turn cannot write either. "
                "If the person asks to build, or to move this to Build, say plainly that nothing "
                "has been planned yet — never that a plan is waiting, was drafted, or has been "
                "handed over.")

    def _maybe_offer_recall(self, store: ThreadStore, thread_id: str):
        """The offer a twice-refused Conversation is owed, or nothing (ADR-0022).

        Read back out of the store rather than tracked through the turn: the error was appended a
        moment ago, and the transcript is the thing `recall.offer` reasons over. Tracking it in
        locals would mean two accounts of the same ladder, and the stored one is the one that
        survives a restart.
        """
        scope = recall.offer(store.read_history(thread_id))
        if not scope:
            return
        ev = {"type": recall.SUGGEST, "scope": scope}
        store.append_history(thread_id, ev)
        yield ev

    def clear_recall(self, thread_id: str, scope: str) -> dict:
        """Start the model over on this Conversation, keeping everything the person can see.

        Recall lives in the OpenCode session, so dropping the stored session id IS the clear:
        `_ensure_session` finds nothing to recover on the next turn and opens a fresh one. Nothing
        else rides on that id — `session.json` holds it and a directory and nothing more — so the
        transcript, the Artifacts, the plan and the Built App links are all untouched, which is
        what the offer promised.

        Chat holds no live handle to undo alongside it: `_ensure_chat_session` reads `session.json`
        on every turn rather than caching an id on the Project the way Build does, so the file IS
        the state. The old OpenCode session is left where it is — OpenCode owns it, it is already
        unreachable from here, and a Conversation that has just failed repeatedly is the worst
        possible moment to start deleting things on its behalf.
        """
        if scope not in (recall.SUMMARY, recall.EMPTY):
            raise ValueError(f"unknown scope {scope!r}")
        store = ThreadStore(self._chat_project().record.path)
        if store.get(thread_id) is None:
            raise KeyError(thread_id)
        store.clear_session_id(thread_id)
        ev = {"type": recall.CLEARED, "scope": scope}
        store.append_history(thread_id, ev)
        return ev

    def _chat_prompt(self, thread_id: str, prompt: str, ctx: dict,
                     urls: list[str] | None = None, workspace: Path | None = None,
                     artifacts: list[dict] | None = None,
                     handoffs: list[dict] | None = None,
                     history: list[dict] | None = None) -> str:
        lines = [
            f"Thread id: {thread_id}",
            f"Write Artifacts under examples/{thread_id}/.",
            self._plan_state_note(handoffs),
            "",
        ]
        # The first turn after a summary-scoped clear keeps the promise the offer made: the model
        # starts over, but is told what was said. Empty on every other turn, including the first
        # turn after a complete clear, where being told nothing is the whole point.
        carried = recall.seed(history or [])
        if carried:
            lines += ["What was said in this Conversation before the model was started over:",
                      carried, ""]
        items = ctx.get("items") or []
        urls = [u for u in (urls or []) if u]
        if items or urls:
            lines.append("Session context:")
            for it in items:
                note = ""
                if workspace is not None and str(it.get("kind") or "") in ("file", "artifact"):
                    note = _describe_context_file(workspace, it)
                lines.append(_chat_context_line(it, file_note=note))
            for url in urls:
                lines.append(
                    f"- URL {url}. Read this page and answer from what it contains. "
                    "Do not guess the contents."
                )
            lines.append("")
        if artifacts:
            lines.append(
                "Already written this Thread (on screen; change one only if asked):"
            )
            for art in artifacts:
                path = str(art.get("path") or "")
                title = str(art.get("title") or art.get("name") or Path(path).name).strip()
                kind = str(art.get("kind") or "file")
                if path:
                    lines.append(f"- {kind}: {title} at {path}")
                elif title:
                    lines.append(f"- {kind}: {title}")
            lines.append("")
        lines.append(
            "This turn answers a question about data. Do not greet by asking what to build, "
            "and do not offer an app unless the person asked to make one that other people would use. "
            "If a chart or table would help, write it without being asked — a PNG and/or "
            f".table.json at examples/{thread_id}/. A matrix is a heatmap PNG plus the table. "
            "Never tell the user whether a chart or table was needed. "
            "That folder already exists, not a React file, not src/. Write the file there; "
            "do not list directories. "
            "@name in the user's message is the file listed above; read that path."
        )
        lines.append("")
        lines.append(prompt)
        return "\n".join(lines)

    def _chat_stream(self, thread_id: str, prompt: str, *, timeout_s: float | None = None,
                     already_asked: bool = False):
        import time

        project = self._chat_project()
        store = ThreadStore(project.record.path)
        thread = store.get(thread_id)
        if thread is None:
            yield {"type": "error", "message": "Unknown thread"}
            yield {"type": "done", "ok": False, "decision": "unknown thread"}
            return
        self._cancel_chat_idle_save()
        store.examples_dir(thread_id).mkdir(parents=True, exist_ok=True)
        was_first = thread.get("title") in ("", "New conversation")
        if was_first:
            store.touch(thread_id, title=title_from_prompt(prompt))
        else:
            store.touch(thread_id)

        ctx = store.read_context(thread_id)
        items = [i for i in (ctx.get("items") or []) if i.get("id")]
        context_ids = [i["id"] for i in items]
        user_ev = {
            "type": "user",
            "text": prompt,
            "contextIds": context_ids,
            # Names live on the event so a later chip-remove still paints this message.
            "context": [{"id": i["id"], "name": i.get("name"), "kind": i.get("kind")} for i in items],
        }
        if not already_asked:
            store.append_history(thread_id, user_ev)
            yield user_ev

            # "Build me an app" is answered by Build, so offer it now rather than after a turn.
            # sage-chat writes an Artifact under examples/, never an app, so running the turn first
            # spends a whole turn and ends exactly where this starts — which is how a build request
            # became 90 seconds of spinner and "ask again with a smaller question".
            #
            # Only the regex short-circuits. The model classifier still runs after a turn, because
            # it judges the assistant's reply as well as the ask, and it cannot do that before one
            # exists.
            #
            # Declining that offer is what brings a turn back here with `already_asked` — so this
            # is skipped on the way through, or the decline would meet the same offer it declined.
            early = self._explicit_handoff(store, thread_id, prompt)
            if early:
                done = {"type": "done", "ok": True, "decision": "handoff"}
                store.append_history(thread_id, early)
                store.append_history(thread_id, done)
                yield early
                yield done
                return

        immediate = "first" if was_first else None
        artifacts: list[dict] = []
        history = store.read_history(thread_id)
        urls = _urls_in_chat(prompt, history)
        chat_token = project.control.arm_chat(thread_id)
        web_token = project.control.arm_web() if _chat_wants_web(prompt, history) else None
        tap: _EventTap | None = None
        try:
            client = self._ensure_opencode()
            sid = self._ensure_thread_session(store, thread_id, project, client)
            work = str((store.read_session(thread_id) or {}).get("directory")
                       or project.record.path)
            project.active_session_id = sid
            before = snapshot_files(project.record.path)
            seen = self._seen_baseline(client, sid, limit=_CHAT_POLL_MESSAGES)
            # Resolved against the chat workdir, which is where the agent stands and the only place
            # every path in the prompt resolves: `examples/` and `.sage/scratch/` are the Project's
            # and `public/data/` is the app's, and all three are linked in there.
            mentioned = self._chat_mention_files(prompt, items, Path(work))
            # Opened BEFORE the prompt: the stream has no `?after=`, so anything emitted before the
            # reader connects is gone. The window is a local connect and text.ended repairs whatever
            # falls in it, which is the whole reason the end event is treated as authoritative.
            #
            # The directory is the SESSION's, not the workspace's. /event delivers only the events of
            # the directory the connection asks for (see SessionEvents.__iter__, which measured it),
            # and a Chat session is created in `.sage/chat-work` — so asking for the workspace root
            # subscribed to a project this turn was not running in. The connection succeeded, stayed
            # open, and carried nothing. Build never saw it because Build's session directory IS the
            # workspace root; Chat inherited the value and not the reason for it.
            tap = _EventTap(client, sid, directory=work)
            client.send_prompt(
                sid, self._chat_prompt(thread_id, prompt, ctx, urls,
                                       workspace=Path(work),
                                       artifacts=store.read_artifacts(thread_id),
                                       handoffs=store.read_handoffs(thread_id),
                                       history=store.read_history(thread_id)),
                agent="sage-chat",
                attachments=mentioned,
                chat=True)
            appeared = False
            poll_failures = 0
            started = time.monotonic()
            # The last moment this turn showed it was moving. Everything OpenCode sends counts,
            # not only what Chat shows: `map_session_event` already drops the stream's noise, so a
            # frame arriving means the session did something. A stuck tool sends nothing between
            # `called` and its result, which is why the window still expires on it — on the longer
            # one, since a slow tool and a stuck tool look identical from here and only one of them
            # deserves to be killed at 90 seconds.
            last_activity = started
            last_text = ""
            # The last thing that said no, and whether anything was ever said back to the person.
            # A step OpenCode refused (a provider 400, a model that is not there) ends the turn
            # without a word, and Chat used to let the quiet cap have it — so a turn that WAS told
            # why reported that Sage "stopped making progress", and sent the person to shrink a
            # question that was never the problem.
            step_error = ""
            step_reason = ""
            answered = False
            idle_quiet = _CHAT_QUIET_TIMEOUT_S if timeout_s is None else timeout_s
            tool_quiet = _CHAT_TOOL_QUIET_TIMEOUT_S if timeout_s is None else timeout_s
            # Calls that started and have not come back, by call id: `called` opens one and
            # success/failed closes it. A caller-supplied timeout_s makes both windows that number,
            # so a test forcing the cap keeps forcing it and this only picks the message.
            running_tools: set[str] = set()
            while True:
                if project.stop_requested:
                    # Cleared by the turn that consumes it. Build clears it in its own handle_stop;
                    # Chat only ever read it, so the flag outlived the turn and the NEXT question
                    # died on this line before it ran a step.
                    project.stop_requested = False
                    try:
                        client.interrupt(sid)
                    except Exception:
                        log.exception("chat: interrupt on stop failed")
                    # Chat reverts nothing. A Build stop takes the turn's file changes with it
                    # because half an app is worse than none; a Chat turn writes charts and tables
                    # under examples/, and one already written is an answer someone can still use.
                    stopped = {"type": "stopped",
                               "message": brand.text(
                                   "Stopped. Anything {assistantName} had already written is kept.")}
                    done = {"type": "done", "ok": False, "decision": "stopped"}
                    store.append_history(thread_id, stopped)
                    store.append_history(thread_id, done)
                    yield stopped
                    yield done
                    return
                now = time.monotonic()
                quiet_limit = tool_quiet if running_tools else idle_quiet
                quiet = now - last_activity >= quiet_limit
                if quiet or now - started >= _CHAT_TURN_MAX_S:
                    log.warning("chat: turn stopped after %.0fs — %s", now - started,
                                f"quiet for {now - last_activity:.0f}s" if quiet
                                else f"hit the {_CHAT_TURN_MAX_S:.0f}s ceiling")
                    try:
                        client.interrupt(sid)
                    except Exception:
                        log.exception("chat: interrupt after timeout failed")
                    # The turn stopped, but the charts and tables it already wrote are on disk and
                    # are still an answer someone can use. Only the end-of-turn path recorded them,
                    # so a timed-out turn left them unlisted in the Thread — and `examples/` crosses
                    # into Build by that list (handoff.md §1), which made the nudge below an offer to
                    # start again with none of the work the person had just waited minutes for.
                    revert_denied_writes(project.record.path, thread_id, before)
                    timed_out = [
                        store.record_artifact(thread_id, path=rel)
                        for rel in new_artifact_paths(project.record.path, thread_id, before)
                    ]
                    if timed_out:
                        immediate = immediate or "artifacts"
                    # Detect here too, not only after a turn that finished. Asking Chat to build an
                    # app is exactly what runs long — sage-chat writes an Artifact, not an app — so
                    # the turn the person most needs the nudge on is the one that never reaches the
                    # end of this loop. Without it the timeout is a dead end they retype into.
                    suggestion = self._maybe_suggest_handoff(store, project, thread_id, prompt)
                    if suggestion:
                        message = (
                            "This turn took too long, so it was stopped. Building an app is Build's "
                            "job rather than Chat's — open it in Build below."
                        )
                    elif step_error:
                        # It did not go quiet on its own — it was refused, and then there was
                        # nothing left to say. The refusal is the reason; the silence only followed.
                        # The refusal is the provider's own sentence, so it rides in as a value:
                        # ours is the half around it, and theirs is left as it arrived.
                        message = brand.text(
                            "{assistantName} could not finish this turn — {reason}", reason=step_error)
                    elif quiet and running_tools:
                        # A step was still open when the window closed. Blaming the turn for
                        # stopping would be wrong twice over: it did not stop, and the person
                        # would go looking for the wrong thing to make smaller.
                        message = brand.text(
                            "The step {assistantName} was running did not finish, so the turn was "
                            "stopped. A large {dataset} file or a broad query can take longer than "
                            "one Chat turn allows — try a narrower query."
                        )
                    elif quiet:
                        # Say which of the two happened. The turn did not run out of time doing
                        # work — it stopped doing any, with nothing of its own left running.
                        message = brand.text(
                            "{assistantName} stopped making progress, so the turn was stopped. If "
                            "you were querying a {dataSource}, it may be too slow to answer here — "
                            "try a narrower query."
                        )
                    else:
                        message = (
                            "This turn worked for too long to finish, so it was stopped. Ask again "
                            "with a smaller question, or ask for one step at a time."
                        )
                    err = {"type": "error", "message": message}
                    done = {"type": "done", "ok": False, "decision": "timeout"}
                    # Before the error, as on the path that finishes: what the turn produced, then
                    # why it stopped. `done` still carries them, so a client that reads only the
                    # terminal event sees them too.
                    if timed_out:
                        art_ev = {"type": "artifacts", "items": timed_out}
                        store.append_history(thread_id, art_ev)
                        yield art_ev
                        done["artifacts"] = timed_out
                    store.append_history(thread_id, err)
                    store.append_history(thread_id, done)
                    yield err
                    yield done
                    if suggestion:
                        store.append_history(thread_id, suggestion)
                        yield suggestion
                    return
                for ev in tap.drain():
                    last_activity = time.monotonic()
                    if ev.kind == "error":
                        # Not shown as it happens — a step that fails may still be retried, and the
                        # answer is what the Thread is for. Kept, so the end of the turn can say it,
                        # and logged, because nothing else in Sage records this frame at all.
                        raw = _error_raw(ev.payload.get("error"))
                        said = _chat_error_text(ev.payload.get("error"), mentioned)
                        if said:
                            step_error, step_reason = said, recall.reason_key(raw)
                        log.warning("chat: step failed — %s", ev.payload.get("error"))
                        # A turn refused at its first step may never register as running, and
                        # `finished` needs to have seen it run. Without this the loop cannot end on
                        # anything but the cap, which is the 90 seconds of nothing this fixes.
                        appeared = True
                        continue
                    if ev.kind == "tool_run":
                        # `shell.started` repeats the call id of the bash `tool.called`, so a set
                        # makes the second one a no-op. An event with no id falls back to one
                        # shared key, which a completion with no id then clears.
                        call = str(ev.payload.get("call_id") or "") or "?"
                        if str(ev.payload.get("status") or "") == "called":
                            running_tools.add(call)
                        else:
                            running_tools.discard(call)
                    live = _chat_live_event(ev)
                    if live is not None:
                        answered = answered or bool(live.get("text"))
                        yield live
                # A stream that has said nothing is not a stream. The transcript fallback exists
                # for a tap that failed to open, and a tap that opened onto silence needs it just as
                # much — without it the turn cannot see its own progress and the quiet cap ends work
                # that is going fine. One frame is enough to earn the fast path back.
                streaming = tap.ok and tap.seen_any
                try:
                    running = client.is_running(sid)
                    appeared = appeared or running
                    finished = appeared and not running
                    # While the stream is healthy the transcript is read ONCE, at the end. The
                    # answer is already arriving on the stream, and re-reading the newest messages
                    # every second was the cost that grew with the length of the Thread rather than
                    # with the question — on the same box the agent is working on.
                    msgs = (client.messages(sid, limit=_CHAT_POLL_MESSAGES)
                            if finished or not streaming else ())
                    poll_failures = 0
                except httpx.HTTPError as e:
                    poll_failures += 1
                    log.warning("opencode poll failed (%d/%d): %s", poll_failures, _MAX_POLL_FAILURES, e)
                    if poll_failures >= _MAX_POLL_FAILURES:
                        yield {"type": "error", "message": (
                            "OpenCode stopped responding, so the turn was halted.")}
                        yield {"type": "done", "ok": False, "decision": "opencode unresponsive"}
                        return
                    time.sleep(2.0)
                    continue
                pending_text = ""
                polled_running = False
                for m in msgs:
                    if m.get("type") != "assistant":
                        continue
                    for i, part in enumerate(m.get("content", [])):
                        key = _part_key(m, i, part)
                        pt = part.get("type", "")
                        # Intermediate "let me save there" text is not the answer. Keep only the
                        # latest text part; persist it once the turn is idle.
                        if pt == "text" and part.get("text"):
                            if key in seen:
                                continue
                            pending_text = part["text"]
                            continue
                        if key in seen:
                            continue
                        if "tool" in pt:
                            status = (part.get("state") or {}).get("status")
                            if status in ("pending", "running", "in_progress"):
                                polled_running = True
                                continue
                            seen.add(key)
                            last_activity = time.monotonic()
                            tool = part.get("tool") or part.get("name") or pt
                            ev = {"type": "agent", "kind": "tool", "tool": tool,
                                  "detail": _tool_detail(tool, part)}
                            if str(tool).lower() in _CHAT_SHOWN_TOOLS:
                                store.append_history(thread_id, ev)
                                yield ev
                            elif str(tool).lower() == "bash" and not tap.ok:
                                # Only when the transcript IS the source. With the stream up this
                                # line already ran when the command started, and replaying it from
                                # the final read would flash "Running Python…" on a finished turn.
                                yield ev
                if not streaming:
                    # No stream to open and close calls on, so the transcript answers instead: a
                    # part still pending IS a call in flight. Read fresh every poll, since nothing
                    # here reports the end of one.
                    running_tools = {"transcript"} if polled_running else set()
                else:
                    # The stream took over. Its own call ids run the set from here, and the key the
                    # transcript left behind would otherwise hold the turn on the long window.
                    running_tools.discard("transcript")
                # Proof of life when the transcript is the only source. The same text part comes
                # back on every poll, so it is the change that counts, not the presence.
                if pending_text and pending_text != last_text:
                    last_text = pending_text
                    last_activity = time.monotonic()
                if finished:
                    # NO_BUILD_MARKER is Build's signal to Sage — a Chat turn has nothing to build
                    # by definition. It reaches here because OpenCode reads the app's AGENTS.md as
                    # well as this session's, so the chat agent is told the rule it belongs to; the
                    # Thread must not show it either way. Stripped here, where the reply is both
                    # persisted and replayed, so a reload does not bring it back.
                    body = _take_no_build_marker(pending_text)[0] if pending_text else ""
                    if body.strip():
                        answered = True
                        ev = {"type": "agent", "kind": "text", "text": body}
                        store.append_history(thread_id, ev)
                        yield ev
                    break
                time.sleep(1.0)

            revert_denied_writes(project.record.path, thread_id, before)
            artifacts = [
                store.record_artifact(thread_id, path=rel)
                for rel in new_artifact_paths(project.record.path, thread_id, before)
            ]
            if artifacts:
                immediate = immediate or "artifacts"
                art_ev = {"type": "artifacts", "items": artifacts}
                store.append_history(thread_id, art_ev)
                yield art_ev
            done = {"type": "done", "ok": True, "decision": "answered"}
            if step_error and not answered:
                # The turn ended, and the person has nothing. `done ok:True` with an empty Thread is
                # the shape that sends someone looking for a Sage bug when the provider had already
                # said what was wrong. Artifacts written before the failing step are still theirs.
                err = {"type": "error",
                       "reason": step_reason,
                       "message": brand.text(
                           "{assistantName} could not finish this turn — {reason}",
                           reason=step_error)}
                store.append_history(thread_id, err)
                yield err
                done = {"type": "done", "ok": False, "decision": "step failed"}
                # After the error and before `done`, so a client reading the stream in order sees
                # what failed before it is offered a way out of it.
                for ev_out in self._maybe_offer_recall(store, thread_id):
                    yield ev_out
            if artifacts:
                done["artifacts"] = artifacts
            store.append_history(thread_id, done)
            yield done
            suggestion = self._maybe_suggest_handoff(store, project, thread_id, prompt)
            if suggestion:
                store.append_history(thread_id, suggestion)
                yield suggestion
            self._maybe_compact_chat(client, sid, project)
        finally:
            if tap is not None:
                tap.close()
            project.control.disarm_chat(chat_token)
            if web_token is not None:
                project.control.disarm_web(web_token)
            saved = self._after_chat_turn(thread_id, immediate=immediate)
            if saved:
                yield saved

    def _maybe_compact_chat(self, client, sid: str, project: Project) -> None:
        """After a Chat turn, compact the OpenCode session if it has grown too large.

        Sage `history.jsonl` is the UI replay and is left alone. Compact only what the next
        `sage-chat` prompt will see. Chat arming must still be live so the summary call routes
        as this Thread's Chat model (the shim rewrites every sage-gateway request). Fail open.
        """
        summarize = getattr(client, "summarize", None)
        if summarize is None:
            return
        # The turn let the lock go at `done` (see chat_stream), and this is the piece of aftercare
        # that touches what the next prompt runs in: summarising a session mid-turn rewrites that
        # turn's context, and wait_for_idle would then sit out the whole turn. So take the lock back
        # rather than race it, and read the arming inside it — outside, the thread_id and the Chat
        # model could be the next turn's. Skipping is safe: the trigger is a context size, so the
        # turn that beat us here ends over the threshold too.
        if not self._turn_lock.acquire(blocking=False):
            log.info("chat compact: another turn started — leaving it to that one")
            return
        try:
            state = project.control.snapshot()
            if not state.chat_thread_id:
                return
            messages = client.messages(sid)
            provider, model = chat_compact.compact_model(state, project.shim.catalog)
            if not chat_compact.should_compact(messages, model):
                return
            log.info("chat compact: session=%s model=%s/%s", sid, provider, model)
            summarize(sid, provider, model, auto=False)
            if client.is_running(sid):
                client.wait_for_idle(sid, appear_grace_s=2.0)
        except Exception:
            log.exception("chat compact failed; leaving the OpenCode session as-is")
        finally:
            self._release_turn()

    def _recheck_app_data(self) -> None:
        """Re-derive what the agent is told about the app's data, now that the turn is over (#15).

        This is where a catalog the agent just wrote gets checked. `serve.py` refuses a broken query
        at app startup, which is after a publish and a cold start — running the same check here puts
        the app's own sentence in front of the agent on its next turn, while the creator is still in
        the conversation that produced it.

        End of turn rather than during: the agent may write `.sage/queries.json` in several edits, and
        a half-written catalog is not a problem to report. Best-effort — a project that cannot be
        resolved has nothing to check, and this must never be what fails a build that worked.
        """
        try:
            project = self.project()
            # Skipped when the person switched Built App mid-build (#77). This writes the app's
            # AGENTS.md and reads its Bindings, and both of those follow the app on screen — the
            # same ones Bind and Unbind write. Writing the turn's answer into another app's file is
            # worse than waiting: the next turn in the app that was built ends here too, and
            # re-derives the same thing then.
            if project.turn_app is not None and project.turn_app.app_id != project.workspace.app_id:
                return
            self._write_app_data(project)
        except Exception:
            log.exception("app data: could not re-check the query catalog")

    def _acquire_for_reset(self, wait: float = 15.0) -> bool:
        """Take the turn lock for a reset, waiting only while a Stop is still unwinding the turn.

        Stop is asynchronous and reset was not, which is the whole bug. stop_build() sets the flag,
        interrupts the session and returns immediately; the turn releases this lock several seconds
        later, after its poll loop notices, reverts the files and finishes its git work. A reset that
        failed instantly in that window answered "a build is running — stop it, then reset" to
        someone who had just pressed Stop and watched it take effect.

        `stop_requested` is what makes the wait bounded and honest: it is set by stop_build and
        cleared by the turn's own handle_stop, so it is true for exactly the window where the lock is
        about to free itself. A build nobody stopped still fails at once, with the sentence that
        tells them what to do about it — waiting there would only be a slower way to say the same
        thing. A turn that never unwinds falls out at the deadline and fails the same way.
        """
        if self._turn_lock.acquire(blocking=False):
            return True
        # A wedged workspace can hold both the lock and the flag: pressing Stop during the thirty
        # seconds Sage spends asking the session to stop sets `stop_requested`, and the turn that
        # would have cleared it is the one that never came back. Nothing else does. Waiting there
        # is fifteen seconds spent on a lock that is never released, before the wrong sentence
        # (#97, #39).
        if self._turn_wedged:
            return False
        if self._project is None or not self._project.stop_requested:
            return False
        # Read once, as a decision, then wait on the LOCK — not polled as a loop condition. The turn
        # clears this flag in handle_stop and only releases the lock afterwards, once it has reverted
        # the files and finished its git work, so a loop that re-checked it would give up inside the
        # very window it exists to cover.
        return self._turn_lock.acquire(timeout=wait)

    def reset_app(self) -> dict:
        """Put the selected app's code back to the starter template, keeping the user's setup (#36).

        One Built App, the one in front of them (#75). A Project holds many, so starting one over
        does not take the rest of the Project with it: another app's code, plan document, Bindings
        and log are outside this operation's reach entirely — its directory is never touched, and
        the documents that survive at the root are the ones naming some other app.

        Serialized on the turn lock like any build: this rewrites the working tree, and doing that
        under a streaming turn would pull the files out from under it.

        Everything the user set up survives — attachments, Bindings, the transcript, and their project
        instructions, which are re-spliced because AGENTS.md itself is re-seeded from the template.
        The `built` flag is cleared with it, so the next build request is gated and planned the way a
        first build is: the app really is new again, and approving a plan for it is the point.

        The transcript survives on purpose (that was the call), so it gets a line saying the reset
        happened. Without one, the history the agent greps still describes an app that is gone, and
        the next turn would build from a record of code it can no longer read."""
        if not self._acquire_for_reset():
            raise ResetBusy(self._turn_wedged)
        try:
            with self._app_lock:
                project = self.project()
                self._wm.reset()
                # The plan documents describe the app that was just taken away, and they live with the
                # Project rather than inside the app — so clearing them is a second call, through the
                # surface that owns them, and it names the app so the other apps' documents stay.
                project.record.clear_plan_docs(project.workspace.app_id)
                # AGENTS.md came back from the template, so it is voiced again and the Project's
                # instructions are rendered into it. They were never kept in that file, so Reset had
                # nothing to lose.
                self._voice_agents_md(project)
                self._splice_instructions(project)
                self._write_agents_data_block(project)   # AGENTS.md is new; the attachments are not
                project.workspace.clear_built()
                # The usage label is derived from the source Reset just put back to the template, so
                # it goes with it (#93). Unlike the Bindings themselves, which are setup and survive.
                project.workspace.clear_resource_usage()
                project.workspace.append_history({"type": "app-reset"}, project.build_conversation)
                # Reset took the Artifacts link with the rest of the app, and `examples` is not in
                # _RESET_KEEP because this seam is what puts it back — the same way it puts back
                # `.sage/history.md`. One place encodes the invariant.
                self._refresh_agent_inputs(project)
                return {"ok": True, "status": project.status()}
        finally:
            self._release_turn()

    def _restore_attachments(self) -> None:
        """Put back any attachment the turn just deleted (#37).

        Told to "remove everything you have built", a live agent took the user's uploaded CSV with it:
        the file left the @ menu and they had to attach it again to say the same sentence. AGENTS.md
        now says not to, but an instruction is not a guarantee, and neither of the two obvious
        enforcement points can carry this one:

        - The shim gates by tool NAME (READ_ONLY_DENIED, WEB_TOOLS strip a tool out of the request).
          It never reads arguments, and it is an LLM proxy — the tool runs in OpenCode, not through
          it. A `rm` in a bash call would never look like a write tool in the first place.
        - The turn snapshot cannot restore these either: commit_before_turn stages with `add -A`,
          which honours the workspace .gitignore, and attach_file puts `public/data/` there
          (_ensure_gitignored) so those symlinks were never in the snapshot at all.

        What the agent cannot reach is process memory. `project.attached` is the live list and no tool
        touches it, so it is the thing to rebuild from: rewrite the manifest it should have produced,
        and re-link anything whose symlink went missing. Both attach paths write the same entry shape
        (see attach_file and the upload path), so one re-link covers a dataset file and an upload.

        Best-effort and end-of-turn, like _recheck_app_data beside it: this must never be the thing
        that fails a build that otherwise worked."""
        try:
            project = self.project()
        except Exception:
            return
        # The turn's app and the turn's list, not the ones on screen: a switch mid-build (#77)
        # leaves `project.attached` describing another app entirely, and restoring THAT here would
        # link one app's files into another's tree.
        app = project.app_for_turn()
        attached = project.attachments_for_turn()
        restored: list[str] = []
        for entry in list(attached):
            try:
                dest = _safe_join(app.path, entry["path"])
                if dest.is_symlink() or dest.exists():
                    continue
                # Raises LookupError for a rehydrated entry with no dataset_id, which the except
                # below treats like any other unrestorable one.
                asset = self._find_asset(entry.get("dataset_id"))
                rel_path = entry.get("dataset_rel_path") or entry.get("file") or ""
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not asset.mount_path:
                    # No mount to re-link to, which is not the dead end it used to be: the same
                    # download that made this attachment can make it again.
                    self._assets.download_file(asset, rel_path, dest)
                else:
                    src = _safe_join(Path(asset.mount_path), rel_path)
                    if not src.is_file():
                        continue
                    dest.symlink_to(src)
                restored.append(entry["path"])
            except (ValueError, OSError, LookupError):
                continue            # one unrestorable attachment must not strand the others
        try:
            # Unconditional, not only when `restored` is non-empty: the entry can survive on disk
            # while the manifest that carries it into the next session does not, and rewriting a
            # file that already says this is free.
            app.write_attachments(attached)
        except OSError:
            log.exception("attachments: could not rewrite the manifest")
        if restored:
            log.warning("attachments: the turn deleted %d attachment(s); restored %s",
                        len(restored), ", ".join(restored))
            try:
                app.append_history({"type": "attachments-restored", "paths": restored},
                                   project.build_conversation)
            except OSError:
                pass

    @staticmethod
    def _pin_turn_app(project: Project) -> None:
        """Name the Built App this turn writes into, for as long as it runs (#77).

        Taken at the top of every turn, under the turn lock. The person may point the rail at
        another app while the turn streams, and everything after this — the session's directory,
        the log, the revert, the `built` latch, the end-of-turn repairs — has to mean the app the
        turn began in rather than whichever one is on screen when it gets there."""
        project.turn_app = project.workspace
        project.turn_attached = project.attached

    def _clear_turn_baseline(self) -> None:
        """Mark "no turn running" so _rebaseline_turn stops touching the baseline once the turn that
        owns it is over. Best-effort — a project we can't resolve has no baseline to clear.

        Also drops active_session_id and the app the turn pinned. They're cleared here, at the one
        place that already means "the turn is over", rather than at each of _build_stream's many
        exits — a phased build reassigns the session per phase, and the turn lock means no other
        turn can observe either mid-flight anyway."""
        try:
            if self._project is None:
                return
            self._project.turn_tree_baseline = ""
            self._project.active_session_id = None
            self._project.turn_app = None
            self._project.turn_attached = None
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
            project.workspace.append_history(ev, project.build_conversation)
            if ev["type"] != "user":  # the composer already rendered the user's own bubble
                yield ev

    def _reset_offer(self, prompt: str, mentions: list[str] | None = None,
                     resources: list[dict] | None = None):
        """Events for "start over" — the control, not the reset (#36).

        Deliberately does not act. A reset throws the app away, and putting a destructive action
        behind a phrase heuristic is the shape of #29: one misread prompt and the user loses work they
        never asked to lose. So the turn stops before any inference and hands back the button, which
        confirms before it runs.

        Stopping it here is already most of the fix. This request used to reach the build agent, and a
        build agent builds — asked to remove everything it had built, it wrote a landing page saying
        "Ready to rebuild from scratch", which is the most literal thing those words describe."""
        project = self.project()
        message = ("Starting over is its own action, not a build — a build agent asked to remove "
                   "everything writes you a page about removing everything. Resetting puts this "
                   "app's code back to the starter template. Your attached files, Resources, this "
                   "conversation and your other apps all stay.")
        for ev in ({"type": "user", "text": prompt},
                   # The whole turn rides along, not just the prompt: "clear everything and build X
                   # from @clickstream" is one request, and the button that answers this offer has to
                   # replay it intact — a reset that drops the @-mentions makes the user retype.
                   {"type": "reset-offer", "prompt": prompt, "message": message,
                    "mentions": mentions or [], "resources": resources or []},
                   {"type": "done", "ok": False, "decision": "reset offered"}):
            project.workspace.append_history(ev, project.build_conversation)
            if ev["type"] != "user":
                yield ev

    def _incoming_offer(self, prompt: str, files: list[str]):
        """Events for a turn that would build on top of somebody else's changes (#78).

        Stops before any inference, the way the reset offer does and for the same reason: the answer
        belongs to the person, and they are here to give it. Once they have gone the save path
        decides for them — commit, pull, resolve, push — which is the other half of the rule.

        Building anyway is a real answer, not a mistake to be talked out of. Two people editing one
        Built App will conflict, and a conflict is what the merge is for."""
        project = self.project()
        shown = files[:_INCOMING_FILES_SHOWN]
        message = ("Somebody else has pushed changes to this app. Building now works from your copy "
                   "of the code, so their changes and yours have to be merged before either can be "
                   "published. Pull first to build on top of their work, or keep building and merge "
                   "when this app saves.")
        for ev in ({"type": "user", "text": prompt},
                   # The prompt rides along so a button can replay the request rather than making
                   # the person retype it. `count` is the whole truth and `files` the readable part
                   # of it, so a merge of a thousand files is still a card and not a wall of paths.
                   {"type": "incoming-changes", "prompt": prompt, "message": message,
                    "files": shown, "count": len(files)},
                   {"type": "done", "ok": False, "decision": "incoming changes"}):
            project.workspace.append_history(ev, project.build_conversation)
            if ev["type"] != "user":
                yield ev

    def _wedged_refusal(self):
        """Events yielded when a streaming turn cannot run because the workspace is wedged (#39).

        The only refusal a streaming turn still has. "A build is already running — wait for it or
        stop it first" used to be the other one, and a queue is what retired it: a turn behind a
        turn now waits instead of being told to come back (#79). A turn behind a WEDGE cannot,
        because the lock it would wait on is held for the life of the process, and there is nothing
        left to wait for and nothing left to stop. The restart is the whole answer.

        No `busy` flag, because the UI drops every event carrying one and substitutes its own line
        about a build still running — true of the lock, and the one thing that is useless here."""
        yield {"type": "error", "message": turn_busy_message(wedged=True)}
        yield {"type": "done", "ok": False, "decision": "wedged"}

    def _turn_app_id(self) -> str:
        """Which Built App a turn is aimed at, for the identity a Stop is checked against (#126).

        Best-effort like `_turn_snapshot`, and for the same reason: an app that cannot be read gives
        an empty id, which matches any app rather than none. Failing open means a build whose app we
        could not name is still stoppable from the screen it is running on; failing closed would
        make it stoppable from nowhere."""
        try:
            return self.project(start_preview=False, seed_app=False).workspace.app_id or ""
        except Exception:
            log.exception("turn identity: could not read the app this turn is aimed at")
            return ""

    def _turn_snapshot(self, conversation: str | None, *, app: bool) -> dict:
        """What a pending turn was written against, to be compared with the live context when it
        reaches the front of the queue (#79).

        Two things move under a waiting turn and each makes its answer a lie in its own way. The
        chips are the Conversation's context: read server-side when the turn RUNS
        (`read_context`), echoed into the transcript bubble client-side when it was SENT. Queue
        those apart and the bubble describes a turn that was given something else. The Built App is
        the rail, which a person is free to move while a turn streams (#77) — a build that resolved
        its target on the way out of the queue would land in whatever app the rail had drifted to,
        which is the failure #77 fixed for a running turn arriving through the queue instead.

        Best-effort, and deliberately so. A snapshot that could not be read compares equal to the
        next one that could not be read either, which lands on the old behaviour — the turn runs —
        rather than on a refusal nobody can act on."""
        snapshot: dict = {}
        try:
            if conversation:
                store = ThreadStore(self._chat_project().record.path)
                snapshot["context"] = [str(item.get("id") or "")
                                       for item in store.read_context(conversation).get("items") or []]
            if app:
                # Not `_ensure_seeded`: a turn waiting in line must not mint the app it is waiting
                # to build. An unseeded Project answers "" here and again at the front of the queue,
                # which compares equal and lets the turn seed it the way it always did.
                snapshot["app"] = self.project(start_preview=False, seed_app=False).workspace.app_id
        except Exception:
            log.exception("turn queue: could not read the context this turn was written against")
        return snapshot

    def _acquire_turn(self, ticket: _TurnTicket, *, kind: str, conversation: str | None,
                      prompt: str, app: bool):
        """Take the turn lock for a streaming turn, waiting in line rather than refusing (#79).

        Yields what the wait owes the client — a `pending` row the moment the turn joins the queue,
        and the refusal if it never gets to run — and sets `ticket.granted`, which is the verdict
        the entry point reads. The wait is held on the client's own connection: a turn IS its HTTP
        request here, so a queued turn that nobody was connected to would have nowhere to stream.

        A wedged workspace is refused at the door instead of queued. The lock is held for good
        there (#39), so a queue behind it is a spinner that never resolves."""
        if self._turn_wedged:
            yield from self._wedged_refusal()
            return
        # Before admit, not after: a queue with nothing in front of it hands over the lock inside
        # `admit()`, so a ticket named afterwards would be running and anonymous in between (#126).
        ticket.kind = kind
        ticket.conversation = conversation or ""
        ticket.app = self._turn_app_id() if app else ""
        if self._turns.admit(ticket):
            ticket.granted = True
            return
        ticket.snapshot = self._turn_snapshot(conversation, app=app)
        try:
            yield {"type": "pending", "ticket": ticket.id, "prompt": prompt,
                   "message": turn_pending_message(self._turns.ahead_of(ticket))}
            outcome = self._turns.wait(ticket)
            if outcome == "cancelled":
                yield {"type": "done", "ok": False, "decision": "cancelled"}
                return
            if outcome == "wedged":
                yield from self._wedged_refusal()
                return
            if self._turn_snapshot(conversation, app=app) != ticket.snapshot:
                # Hand the lock straight back: this turn is not running, and the next one in line has
                # been waiting for it. The prompt rides along so the composer can put the text back
                # rather than making somebody retype what they already typed.
                self._release_turn()
                # `contextChanged` is a machine-readable marker, the way the old busy refusal carried
                # one: this refusal belongs in the composer beside the text it hands back, not in the
                # transcript as something Sage answered, and the UI should not have to match on prose
                # to tell the difference.
                yield {"type": "error", "contextChanged": True, "prompt": prompt,
                       "message": turn_context_changed_message()}
                yield {"type": "done", "ok": False, "decision": "context changed"}
                return
            ticket.granted = True
        finally:
            # A generator abandoned at one of those yields — a client that hung up, a caller that
            # stopped reading — is a place in the queue nobody is standing in. Left on the deque it
            # would sit at the head for ever and every turn behind it would wait on it. A no-op on
            # every path that reached the front of the queue under its own steam.
            if not ticket.granted:
                self._turns.cancel(ticket.id)

    def _stop_wedged_session(self, client, sid: str) -> bool:
        """Ask a wedged session to stop, and report whether it confirmed that it did (#39).

        Order matters and only runs one way: stop OpenCode, THEN release the turn lock. A lock
        released under a session that is still generating gives you two turns on one working tree,
        which is the thing the lock exists to prevent — the abandoned turn goes on writing while the
        next one starts, and nothing downstream can tell which file came from which.

        Confirmation is the session going idle, not the interrupt call returning. `interrupt` posts
        and returns without waiting, and a session wedged badly enough to need this is exactly the
        one that may ignore it. Anything short of a clean idle reading — a refused interrupt, a poll
        that will not answer, a session still running at the grace deadline — is reported as NOT
        stopped, because the only safe reading of "we cannot tell" is that it is still writing.
        """
        import time

        try:
            client.interrupt(sid)
        except Exception:
            # Tolerated rather than fatal, because it is not the answer: the polling below is, and a
            # session that was already finishing reads idle whether or not this call landed.
            log.exception("wedged turn: interrupt failed")
        # Started HERE, after the call returns. `interrupt` is an httpx POST with a 30-second
        # timeout against the server that just stopped answering, so it is exactly the call likely
        # to hang — and a deadline set before it would be spent by the time the session got its
        # first chance to go idle. That would brick a workspace over a slow POST rather than over a
        # session that actually refused to stop, which is the harshest outcome here.
        deadline = time.monotonic() + _BUILD_STOP_GRACE_S
        while True:
            try:
                if not client.is_running(sid):
                    return True
            except Exception as e:
                # A failed poll is not a refusal. This is the same OpenCode the main loop tolerates
                # four consecutive timeouts from — briefly CPU-bound is normal, and a session busy
                # enough to need an interrupt is exactly the one likely to miss the first probe.
                # Giving up here would condemn the workspace to a restart over one slow read.
                log.warning("wedged turn: session state unreadable after interrupt: %s", e)
            if time.monotonic() >= deadline:
                log.error("wedged turn: no idle reading %.0fs after interrupt", _BUILD_STOP_GRACE_S)
                return False
            time.sleep(1.0)

    def _seen_baseline(self, client, sid: str, *, limit: int | None = None) -> set[tuple[str, object]]:
        """Keys of every assistant part already in the session, so a turn only emits its OWN parts.

        client.messages(sid) returns the ENTIRE session on every poll, and the emit-tracking `seen`
        set starts empty for each user turn. Without this baseline, a follow-up turn's first poll
        re-walks the previous turn's completed parts and re-emits them — the prior turn's summary
        reappearing at the top of the new turn (the "ordering" echo). Keys come from _part_key and
        must match the poll loop in _build_stream. Best-effort: on a poll error we return an empty
        baseline (worst case is the echo, not a broken build) and let the loop retry."""
        seen: set[tuple[str, object]] = set()
        try:
            for m in client.messages(sid, limit=limit) if limit else client.messages(sid):
                if m.get("type") == "assistant":
                    for i, part in enumerate(m.get("content", [])):
                        seen.add(_part_key(m, i, part))
        except httpx.HTTPError as e:
            log.warning("could not baseline session messages, prior-turn echo possible: %s", e)
        return seen

    def _tag_conversation(self, project: Project, ev: dict) -> None:
        """Name the app on the conversation's own record, so the rail can tag, filter and search it.

        Off the `app_change` receipt rather than off the turn: the tag and the card in the
        transcript then say the same appId and the same name by construction, and there is one
        place that decides a turn changed an app instead of two that can disagree.
        """
        thread_id = str(project.build_conversation or "")
        app_id = str(ev.get("appId") or "")
        if not thread_id or not app_id:
            return
        ThreadStore(project.record.path).record_touch(
            thread_id,
            app_id=app_id,
            app_name=str(ev.get("name") or ""),
            kind="built" if _crossing_minted_app(project.app_for_turn(), thread_id) else "changed",
        )

    def _build_stream(self, prompt: str, mentions: list[str] | None = None,
                      resources: list[dict] | None = None, *, is_approval: bool = False,
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
        real files and attached to this turn's prompt (see _resolve_mentions). `resources` are the
        Bindings they @-referenced, which ride the prompt text instead (see _resource_mention_note)."""
        import time

        project = self._ensure_seeded()
        # Repair the warm node_modules before the turn, not only at attach — attach happens once per
        # process, and an agent-run `npm install` can destroy the symlink mid-session and leave the
        # workspace unable to build or preview (see WorkspaceManager.link_warm_deps).
        if self._wm.link_warm_deps():
            log.warning("workspace: restored the warm node_modules — an npm install had removed it")
        client = self._ensure_opencode()
        # A phase runs in the throwaway session its caller made; everything else reuses the project's.
        owns_turn = brief is None
        sid = session_id or self._ensure_session(project, project.build_conversation)
        project.active_session_id = sid
        breaker = CircuitBreaker()
        current = prompt
        # Attach the @-mentioned files to the user's turn only — not to the internal nudge/fix
        # follow-ups below, which carry no new user reference.
        mention_files = self._resolve_mentions(project, mentions)
        # Rides the prompt TEXT, appended at the send below rather than here: the gate/answer/plan
        # forks wrap `current` in their own preamble, and the block has to stay at the end of what
        # the agent reads — beside the attachment listing, which is rendered the same way.
        resource_note = self._resource_mention_note(project, resources)
        # What this turn was @-mentioned and cannot use — computed ONCE, here, and spent twice: on
        # the screen below as a `mentions-unresolved` bubble, and in the prompt as a note to the
        # agent. One value because two would drift, and the whole defect (#130) was the screen being
        # told something the agent never heard.
        #
        # Deliberately outside the `owns_turn` gate the screen half sits behind. That gate is about
        # the TRANSCRIPT — a phase must not add a second user bubble — and says nothing about what
        # the agent should be able to read. A phase is `""` here anyway, since only `build_stream`
        # ever passes `mentions` or `resources`, so the gate would not be doing any work: it would
        # only be a second rule to keep in step with this one.
        #
        # Read at the same point as `resource_note`, off the same Bindings, so the two can never
        # report a Resource as both used and refused.
        # `unusable_rows` is the third reader of that one value, and the only one that is not prose:
        # it rides the same event as the sentence so the card's buttons and the sentence's claim are
        # the same answer (#135). The agent is deliberately NOT given the rows — the note it reads
        # already forbids the repair, and a machine-readable list of fixes is exactly the invitation
        # `_DROPPED_MENTION_NOTE` spends its length taking away.
        unusable, unusable_rows = self._unusable_mentions(project, mention_files, mentions, resources)
        unusable_note = _DROPPED_MENTION_NOTE.format(said=unusable) if unusable else ""
        # What was said in the Chat half of this Conversation (#53). Rides the prompt text beside
        # `resource_note`, and for the same reason: the gate/answer/plan forks below wrap `current`
        # in their own preamble, so a block that has to sit at the END of what the agent reads
        # cannot be glued on here. Built now rather than at the send so it is this turn's answer —
        # a Chat turn arriving mid-build belongs to the next Build turn, not this one.
        chat_note = self._chat_context_note(project)
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
                project.app_for_turn().set_last_turn_failed(not ev.get("ok"))
            # A phase's `done` is swallowed by _run_step so the UI sees exactly one per build; it
            # must not reach history either, or a reload would replay six "build is clean" dividers.
            # The rail's app tag, recording half. Here rather than at the four yield sites that
            # emit the receipt, for the reason the replan record above gives: every one of them
            # passes through persist(), and a fact with four writers has four chances to be
            # forgotten. A phase is not excluded — a phase never emits `app_change`.
            if ev["type"] == "app_change":
                self._tag_conversation(project, ev)
            if ev["type"] in _PERSISTED_EVENTS and (owns_turn or ev["type"] != "done"):
                project.app_for_turn().append_history(ev, project.build_conversation)
            return ev

        # Refresh what the agent reads BEFORE the baseline below, so these writes are part of the
        # pre-turn state. Written after it, they would sit outside the revert point: a stop would
        # roll them back mid-turn, and the read-only gate would be reading a working tree Sage had
        # changed on the user's behalf rather than one the agent wrote.
        self._refresh_agent_inputs(project)
        # Snapshot before touching history/files so a stop mid-turn can restore exactly this
        # state, and remember how many history entries pre-date this turn so a stop can drop
        # everything appended since (the turn disappears from the transcript entirely).
        project.snapshot.commit_before_turn()
        history_baseline = project.app_for_turn().history_len()

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
        has_built = project.app_for_turn().has_built()
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
        settings = project.record.read_settings()
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
        prev_turn_failed = project.app_for_turn().read_last_turn_failed()
        if not is_question:
            project.app_for_turn().set_last_turn_failed(False)
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
                               answer_only=answer_only, is_approval=is_approval,
                               skip_planning=skip_planning):
            gate = scope.wants_a_plan(
                prompt,
                gateway=project.shim.gateway,
                catalog=project.shim.catalog,
                root=project.app_for_turn().path,
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
            project.app_for_turn().append_history(
                {"type": "user", "text": user_text if user_text is not None else prompt},
                project.build_conversation)
            # What they @-mentioned and this turn cannot use, said out loud. It goes here, right after
            # their own bubble, because it answers what they just typed — and before the agent runs,
            # because the whole point is to be read while the turn is still worth stopping. The
            # sentence itself was built above, with the other prompt riders — the agent is told the
            # same thing (see `unusable_note`).
            # `entries` rides beside the prose rather than replacing it, and no new event type is
            # coined for it: a transcript written before this shipped has the sentence and no rows,
            # and renders as the sentence — which is also what a replayed row must render as, so the
            # client needs one branch rather than two (#135).
            if unusable:
                yield persist({"type": "mentions-unresolved", "message": unusable,
                               "entries": unusable_rows})

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
                project.app_for_turn().truncate_history(history_baseline)
            restore_mode()
            return {"type": "stopped"}

        def stalled_offer(quiet_for: float, *, in_tool: bool):
            """The transcript's half of giving up on a wedged turn (#39).

            An offer, not a bare error row, on the precedent of the reset offer (#36) and the
            incoming-changes offer (#78): something needs the person's decision, and here the only
            decision left is whether to ask again. A plain `error` is a dead end — the screen stops
            meaning anything and there is nowhere to go from it.

            It says the two things they cannot see for themselves: that Sage waited rather than
            missed something, and that whatever the turn had already written is still on disk.
            Nothing is discarded. A wedged turn's files are exactly as legitimate as any other
            turn's — the person asked for them and the agent wrote them, and only the reporting
            failed — and deleting them silently would be indistinguishable from the turn never
            having run. If they are wrong, that at least is something the person can see.

            The button replays `prompt`, what was asked for, rather than `current`, which may be an
            internal nudge by now. An approve turn and a phase of a phased build have no such
            sentence of the person's to replay, so they get the message and no button.
            """
            # Measured, not the constant: there are two windows now (#98) and naming the wrong
            # one would put a number in front of the person that nothing they can see produced.
            waited = (f"{quiet_for / 60:.0f} minutes" if quiet_for >= 90
                      else f"{quiet_for:.0f} seconds")
            kept = agent_wrote()
            if kept and (gate or answer_only):
                # A read-only turn that wrote broke the guarantee it exists to give, and stalling
                # does not make those edits legitimate. The ordinary exit reverts them and says so
                # (the gate/answer-only check further down); this exit has to reach the same answer,
                # or going quiet becomes the way around the gate.
                log.error("%s turn wrote code and then stalled — reverting; read-only was bypassed",
                          "gated" if gate else "answer-only")
                project.snapshot.discard_changes()
                kept = False
                fate = ("It had also edited files, which a planning turn is not allowed to do, so "
                        "those edits were undone.")
            elif kept:
                fate = "What it had already written to your app is kept, so you can see how far it got."
            else:
                fate = "It hadn't written anything to your app yet."
            # A phase is not a turn, and gets no card. _run_step retries a failed phase in a fresh
            # session, so a build that stalls at phase 3 and finishes on the retry would otherwise
            # carry "the build stopped responding, so Sage stopped it" permanently in the middle of
            # a build that worked. The phase machinery already has words for this: the `done` below
            # is swallowed into the step's own outcome, and the step-done card names it.
            if owns_turn:
                yield persist({
                    "type": "build-stalled",
                    # Which silence it was, in the person's terms. "It went quiet" is the wrong
                    # sentence for a step that was running the whole time — it sends them looking
                    # at the model when the thing that hung was their build command, and a step
                    # that never returns is the one case where asking again unchanged may not be
                    # the move. Chat draws the same line for the same reason.
                    "message": (brand.text(
                        "The step {assistantName} was running didn't finish. It waited {waited} and "
                        "then stopped the build. {fate}", waited=waited, fate=fate) if in_tool else
                        brand.text(
                        "The build stopped responding. It went quiet for {waited}, so "
                        "{assistantName} stopped it. {fate}", waited=waited, fate=fate)),
                    "prompt": "" if is_approval else prompt,
                    "quietForS": round(quiet_for),
                    "kept": kept,
                })
                if kept:
                    # The app really did change, so it owes the same receipt every other turn that
                    # changes it leaves — without one, "you can see how far it got" points at nothing.
                    yield persist(_app_change_event(project.app_for_turn()))
            yield persist({"type": "done", "ok": False, "decision": "stalled"})

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
        # Set when the agent claims this turn's request cannot be acted on at all (NO_BUILD_MARKER).
        # Not reset between nudge iterations, and it doesn't need to be: a claimed turn returns
        # before the nudge loop can run, and the two nudges that follow a WRITING turn (runtime,
        # leak) can't reach a turn that wrote nothing.
        nothing_to_build = False
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
        # An app that declares a model has a live gateway URL in its own source, and nothing stops
        # the agent fetching it instead of calling `askModel` (#94). Bounded like the leak fix
        # beside it, and for the same reason: a defect we can describe but cannot make the agent fix.
        gateway_fixes = 0
        MAX_GATEWAY_FIXES = 2
        GATEWAY_FIX_NUDGE = (
            # Self-contained rather than pointing at a heading in AGENTS.md: `agents_block` writes
            # no model section at all for an app with no Alias bound, and titles it in the plural for
            # an app with several, so a quoted heading is wrong in two of the three cases.
            # `{files}` and `{helper}` are not pack keys, so the helper leaves them for the
            # brand.text call at the fix site to fill from the turn (see below).
            "You called {platformName}'s LLM Gateway with your own fetch in {files}. Rewrite those "
            "calls to use `askModel` from `{helper}` — import it and pass it the messages; it "
            "already knows the model, the URL and the headers. A "
            "raw call drops the X-LLM-Tag-sage-* cost tags that attribute this app's spend, the "
            "error messages written for the viewer, the expired-session check and streaming — and "
            "`/api/llm` is {assistantName}'s PREVIEW proxy, which is not there once the app is "
            "published, so a call written against it works while you build and breaks the moment "
            "it ships."
        )
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
            client.send_prompt(sid,
                               "\n\n".join(p for p in (current, chat_note, resource_note,
                                                       unusable_note) if p),
                               agent=agent, attachments=mention_files)
            # All four ride the first (user) turn only, not the nudge/fix follow-ups: those carry
            # no new user reference, and a repeated block reads as a second request for the same
            # Resource. The Chat background goes with them — a nudge is Sage talking to itself
            # about the code it just failed to write, and the conversation behind it hasn't moved.
            # The dropped-mention note goes with them for the same reason: it answers what the
            # person typed, and a nudge mentions nothing.
            mention_files = None
            resource_note = ""
            chat_note = ""
            unusable_note = ""
            appeared = False
            start = time.monotonic()
            # When OpenCode last produced anything, and so what the quiet deadline below is measured
            # from (#39). Seeded at the send rather than at zero: a turn that says nothing at all from
            # the moment it is asked is as wedged as one that stops halfway.
            last_event = start
            # The shim classifies plan/implement per model call (phase_classifier). We only observe
            # the resulting phase here to keep the UI's live indicator in sync — routing is decided
            # in the shim, not here, so it stays per-step and race-free.
            last_phase = project.control.snapshot().phase.value
            last_active: str | None = None  # last "active" label emitted (dedup across 1s polls)
            # Tool calls seen in flight, by part key. Only five tools carry a printable detail and
            # only some of those change it, so `last_active` alone would miss a `task`, a `webfetch`
            # or a `glob` starting — and a step starting IS a new OpenCode message, whatever the
            # transcript can show for it. Movement, for the quiet deadline; nothing renders from it.
            in_flight: set[tuple[str, object]] = set()
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
                # What "OpenCode produced something" means for the quiet deadline below: a part this
                # poll had not seen before (a finished tool call, a paragraph of text), or a running
                # tool whose streaming detail moved. A tool sitting in-progress with the same detail
                # poll after poll is not progress — it is the shape a wedged turn has.
                seen_before = len(seen)
                active_before = last_active
                in_flight_before = len(in_flight)
                # Whether a call is open RIGHT NOW, which decides the quiet window below. Read
                # fresh from the transcript every poll, the way Chat reads it when no event stream
                # is up (`running_tools = {"transcript"} if polled_running else set()`) — Build has
                # no stream at all, so the transcript is the only thing that could answer. It can:
                # an in-progress part is never marked `seen`, so it comes back around on every poll
                # until the state that closes it arrives. `in_flight` cannot answer instead; it
                # only ever grows, because all it is asked is whether this poll added to it.
                tool_open = False
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
                                in_flight.add(key)
                                tool_open = True
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
                            ev = {"type": "agent", "kind": "tool", "tool": tool,
                                  "detail": _tool_detail(tool, part)}
                            ms = _tool_duration_ms(part)
                            if ms is not None:
                                ev["durationMs"] = ms
                            yield persist(ev)
                        elif pt == "text" and part.get("text"):
                            seen.add(key)
                            # Take the marker out before anything else looks at this text — the
                            # dedupe below, the plan card, the transcript. Stripped on EVERY turn,
                            # including gated ones: the claim is only honoured on a build turn (see
                            # the exit below), but a marker left in the text on a plan turn would be
                            # persisted into plan.md and shown on the approval card.
                            body, claimed = _take_no_build_marker(part["text"])
                            nothing_to_build = nothing_to_build or claimed
                            if not body.strip():
                                continue  # the marker was the whole part; there is no prose to show
                            # Second line of defence behind _part_key: parts with no id still key on a
                            # shifting index, and a model that restates itself verbatim produces a
                            # genuinely distinct part. Either way the same paragraph twice in the
                            # transcript is never what the user should read, so drop the repeat.
                            if body.strip() in emitted_text:
                                continue
                            emitted_text.add(body.strip())
                            if gate:
                                # Gate turns render this text once, in the plan card below — don't also
                                # stream it live, or the user sees the same prose twice (loose text + card).
                                plan_text_parts.append(body)
                            else:
                                yield persist({"type": "agent", "kind": "text", "text": body})
                cur_phase = project.control.snapshot().phase.value
                moved = (len(seen) > seen_before or last_active != active_before
                         or len(in_flight) > in_flight_before)
                if cur_phase != last_phase:
                    last_phase = cur_phase
                    # A phase change is the shim having classified another model call, so inference
                    # is flowing even when nothing has reached the transcript yet.
                    moved = True
                    yield {"type": "phase", "phase": cur_phase}
                if moved:
                    last_event = time.monotonic()
                if project.last_gateway_error is not None:
                    break
                if appeared and not running:
                    break
                if not appeared and time.monotonic() - start > 12:
                    break
                # Last of the exits, and only once the turn has appeared. A turn that never
                # registered as running has its own deadline twelve lines up and its own answer;
                # this one is for the case that had none — a session that says it is running and
                # has stopped saying anything else (#39).
                # Which of the two silences this is (#98). A call still open is a step that has
                # not come back; nothing open is a turn that stopped taking them.
                quiet_limit = _BUILD_TOOL_QUIET_TIMEOUT_S if tool_open else _BUILD_QUIET_TIMEOUT_S
                if appeared and time.monotonic() - last_event >= quiet_limit:
                    quiet_for = time.monotonic() - last_event
                    log.error("build turn wedged: no OpenCode output for %.0fs (%s) — giving up",
                              quiet_for, "a call was still open" if tool_open else "nothing open")
                    stopped = self._stop_wedged_session(client, sid)
                    # Before the branch, because it is true of both: nothing was built either way.
                    self._turn_gave_up = True
                    if not stopped:
                        # Two failures, and the second is the one that decides what happens next. We
                        # cannot show that the session let go of the working tree, so the turn lock
                        # stays held and this workspace takes no more turns until it is restarted.
                        # Saying that is the whole of what is left to do for the person.
                        self._turn_wedged = True
                        # Everything queued behind this turn is waiting on a release that is never
                        # coming, so fail it here rather than leave held connections and spinners
                        # (#79). Loudly: each one answers with the restart sentence of its own.
                        failed = self._turns.fail_pending()
                        if failed:
                            log.error("turn wedged: failed %d pending turn(s) — restart to clear",
                                      failed)
                        # The same persisted card the clean give-up leaves, not an `error` frame:
                        # `error` is not in _PERSISTED_EVENTS, and this is the one outcome
                        # guaranteed to outlive the tab — it ends in a restart. No prompt rides
                        # along, because there is nothing a retry could reach until then.
                        #
                        # No restore_mode() either, and that is the point rather than an omission:
                        # the read-only and web pins are what the shim strips a request against, so
                        # leaving them armed is the last thing still standing between a session
                        # that would not stop and the working tree. The user's model pick rides
                        # along with them, unrestored — in memory only, so the restart clears it.
                        # The two persists below still append history and set the failure flag;
                        # what is skipped is everything that reverts or rewrites the app's code.
                        yield persist({
                            "type": "build-stalled", "stuck": True, "prompt": "",
                            "quietForS": round(time.monotonic() - last_event), "kept": True,
                            # Which silence it was, on the same line the offer next door draws
                            # (#98). The action is the same either way — restart — but the sentence
                            # is what someone reads to know whether their build command was the
                            # thing that hung, and a card that says "stopped responding" over a
                            # step that ran the whole time sends them to look at the model.
                            "message": (
                                (brand.text(
                                    "The step {assistantName} was running didn't finish, and the "
                                    "build would not stop when {assistantName} asked it to, so this "
                                    "workspace cannot run another build. ") if tool_open else
                                 brand.text(
                                    "The build stopped responding and would not stop when "
                                    "{assistantName} asked it to, so this workspace cannot run "
                                    "another build. "))
                                + "Restart the workspace to clear it. Everything already written "
                                  "to your app is still there.")})
                        yield persist({"type": "done", "ok": False, "decision": "wedged"})
                        raise TurnWedged()
                    # It stopped, so the tree is ours again: put the mode pins back and hand the
                    # person an offer they can act on rather than an error row they cannot.
                    #
                    # The stop flag goes with it. A person watching a build hang for five minutes
                    # presses Stop, and `stop_build` sets this while the workspace still reads busy
                    # — `_turn_wedged` is false until the stop actually fails. The turn that would
                    # have cleared it in `handle_stop` is the one that never came back, and this
                    # exit hands the lock straight back, so the flag would be sitting there for the
                    # NEXT turn to trip over: the Try again button on the card below would return
                    # "stopped" without running a step. The stop has been honoured — the session is
                    # idle — so the flag has done its job.
                    project.stop_requested = False
                    restore_mode()
                    yield from stalled_offer(quiet_for, in_tool=tool_open)
                    return
                time.sleep(1.0)

            if project.last_gateway_error is not None:
                err = project.last_gateway_error
                # This turn gave up, exactly as a wedged one does: the gateway never answered, so
                # whatever the turn was asked to do did not happen. Said here so an approve turn's
                # `finally` keeps the plan instead of archiving one it never built from — otherwise
                # the person's "try again" is met with a fresh plan for the request they approved a
                # minute ago. A phase says nothing: _phased_approve owns that build's outcome, and
                # its failed phases are kept on disk rather than retried from the top.
                if owns_turn:
                    self._turn_gave_up = True
                restore_mode()
                message = f"model call failed: {err['message']}"
                if owns_turn and is_approval:
                    # The kept plan is invisible: the card's Approve button was spent the moment
                    # this turn started, and what is left on screen is a gateway's 404. Say the one
                    # sentence that gets the person out of here, on the row that stopped them.
                    message += brand.text(
                        '\n\nThe plan you approved is still here — say "try again" and '
                        "{assistantName} will build it without planning it again.")
                yield persist({"type": "error", "message": message})
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
                # A weak planner can finish this read-only turn without emitting any plan text —
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
                plan_id = ""
                if arch:
                    project.app_for_turn().write_architecture(plan_md)
                else:
                    # The durable half of the same plan, written first because superseding an
                    # earlier one below has to be able to name it. plan.md is the copy the builder
                    # consumes and archive_plan() moves aside the moment it does; this document is
                    # what people open, edit and comment on, and it has to outlive that build.
                    # Architecture keeps its own file instead and gets no document — it is already
                    # a reference that nothing archives.
                    _warn_if_shapeless("plan gate", plan_md)
                    plan_id = project.record.create_plan_doc(
                        plan_md,
                        title=chat_handoff.plan_title(plan_md) or "App",
                        author=_viewer_id(),
                        # This gate ran inside an app, so the document knows which one from the
                        # start — unlike a Chat handoff, which is planned before any app exists
                        # and gains its app reference only when the handoff is confirmed.
                        app_id=project.app_for_turn().app_id,
                        # And it knows the Conversation, which is the Build conversation this turn
                        # was pinned to (_begin_conversation). Both ends of the back-link, where
                        # the Chat handoff can only fill in one of them yet (#54). Empty for a
                        # turn driven with no conversation at all — a caller the Workbench does
                        # not have, since typing in Build opens one first.
                        origin_thread_id=str(project.build_conversation or ""),
                    )["id"]
                    # A plan another Conversation left awaiting approval in this same app steps
                    # aside rather than being written over (#59).
                    self._supersede_live_plan(project, project.app_for_turn(), plan_id,
                                              str(project.build_conversation or ""))
                    project.app_for_turn().write_plan(plan_md, plan_id)
                # `steps` is how the card says "Approve & build (6 phases)" — and, more usefully,
                # it's the user's chance to see BEFORE approving that a phased plan actually parsed.
                # A plan the parser can't read still builds, just in one context.
                steps = len(parse_steps(plan_md)) if (phased_build and not arch) else 0
                yield persist({"type": "plan-proposed", "plan": plan_md,
                               "kind": "architecture" if arch else "plan",
                               "planId": plan_id,
                               "steps": steps if steps >= MIN_STEPS else 0})
                yield persist({"type": "done", "ok": True,
                               "decision": "architecture ready" if arch else "awaiting approval"})
                return

            # The agent said this request cannot be acted on (NO_BUILD_MARKER) and, true to that,
            # wrote nothing. Finish here. Two things are being skipped, and the second is the point:
            # the typecheck, which would be 10-30s of tsc over a tree nobody touched, and the
            # implement-nudge below. That nudge exists to break an agent stalled at a plan; pointed
            # at an agent that correctly declined, it force-switches the turn to Implement, pins the
            # strong model, and pushes until something gets written into src/App.tsx — which is
            # exactly how the user's dashboard ended up displaying "the file isn't showing up" (#29).
            #
            # Never trusted over the filesystem. An agent that claims this AND edits files falls
            # through to the normal build path, where its edits are typechecked and kept like any
            # other build's: the marker excuses a turn from editing, it can't unmake edits. The same
            # ordering also means a gated turn resolved its plan above before we got here, so a
            # marker on a plan turn is stripped from the card and otherwise ignored.
            if nothing_to_build and not agent_wrote():
                restore_mode()
                yield persist({"type": "done", "ok": True, "decision": "nothing to build"})
                return

            yield {"type": "typecheck-start"}
            report = self._feedback.check(project.app_for_turn().path)
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
                        # own pick in restore_mode().
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
                # The agent may have called the LLM Gateway itself rather than through `askModel`,
                # which loses the cost tags, the viewer's error messages, session expiry and
                # streaming — and a call written against the preview's `/api/llm` proxy works while
                # it is built and breaks once it ships (#94). Its own block rather than folded into
                # the leak one above: two defects want two nudges, and the `continue` means only one
                # fires per iteration, so a turn that did both fixes the leak and catches this on
                # the next pass. Nothing gates — the nudge is bounded and the turn completes either
                # way, exactly as the leak fix does.
                if report.ok and wrote_code and gateway_fixes < MAX_GATEWAY_FIXES:
                    raw_calls = self._detect_raw_gateway_calls(project)
                    if raw_calls:
                        gateway_fixes += 1
                        for name, _ in raw_calls:
                            yield persist({"type": "gateway-call", "file": name})
                        # The half the agent cannot finish: rewriting the call to `askModel` makes an
                        # Alias this app never declared throw, and only a person can bind one
                        # (ADR-0010). Said once for the turn, not once per file.
                        notice = unbound_alias_notice(raw_calls)
                        if notice:
                            yield persist({"type": "gateway-alias-unbound", "message": notice})
                        yield {"type": "iterate", "reason": "called the LLM Gateway directly — routing it through askModel"}
                        current = brand.text(
                            GATEWAY_FIX_NUDGE,
                            files=", ".join(n for n, _ in raw_calls),
                            helper=project.app_for_turn().helpers.llm_path)
                        continue
                restore_mode()
                # The receipt for what this turn changed, before the line that closes the turn.
                # `owns_turn` because a phase is not a turn: one approved plan changed one app, and
                # six phases would put six identical cards in the transcript (#56).
                if wrote_code and owns_turn:
                    yield persist(_app_change_event(project.app_for_turn()))
                yield persist({"type": "done", "ok": report.ok, "decision": decision.reason})
                if report.ok and owns_turn:
                    # A clean code-writing build succeeded (a no-edit plan/answer turn returned earlier),
                    # so this project is now "built" — future turns gate on plan, not on this being done.
                    #
                    # A phase does neither: one approved plan is one commit, not six, and a build that
                    # dies at phase 4 must not have been marked "built" by phase 1.
                    project.app_for_turn().mark_built()
                    saved = self._save_to_git(project, prompt)
                    if saved is not None:
                        yield persist(saved)
                return
            yield {"type": "iterate", "reason": decision.reason}
            current = report.as_agent_message()

    def approve_stream(self, answers: str = "", plan_edits: str | None = None,
                       conversation: str | None = None, plan_id: str = ""):
        """Approve a gated plan and build it (SPEC P6). Feeds the approved plan into a normal
        build turn as context, then archives the plan so no live .sage/plan.md is left for a later
        turn to misread. Approval means "build it now", so if the user is in Plan or Ask mode we run
        this turn pinned to Implement, leaving their own mode alone. Both are read-only: Plan's gate
        would just re-plan, and Ask has every write and shell tool stripped from the request by the
        shim, so the agent would emit edits that never land on disk. An Auto/Implement approve already
        has history, so it's never re-gated regardless."""
        # Serialize like build_stream: approving while a turn already streams would overlap two turns
        # on one working tree and read-only gate. We hold the lock across the whole approve (plan
        # write + mode swap + build) and call _build_stream directly so it doesn't re-acquire — and
        # an approve asked for mid-turn queues behind it (#79) rather than being refused, because an
        # approve IS a build turn and a Workbench that queued one and refused the other is a rule
        # people would have to learn instead of guess.
        ticket = _TurnTicket(new_id("turn"))
        yield from self._acquire_turn(ticket, kind="build", conversation=conversation, prompt="",
                                      app=True)
        if not ticket.granted:
            return
        try:
            self._turn_gave_up = False
            self._begin_conversation(conversation)
            self._pin_turn_app(self.project())
            yield from self._approve_locked(answers, plan_edits, plan_id=plan_id)
        except TurnWedged:
            # Swallowed, not re-reported: the turn already said what happened in its own stream, and
            # a traceback on top of it would only be a second, worse version of the same sentence.
            log.error("turn wedged and would not stop — keeping the turn lock; restart to clear")
        finally:
            # Everything here is skipped for a wedged turn — the lock, and the two cleanups that read
            # and write the working tree. The session would not confirm it stopped, so it may still be
            # writing there, and both letting the next turn in and healing the tree under it are the
            # exact collision `_turn_lock` exists to prevent (#39). Any OTHER failure still unwinds
            # normally: an exception is not evidence that OpenCode is loose in the tree.
            #
            # Read off the orchestrator rather than a local, so it holds however this generator
            # unwinds — a caller that walks away mid-stream raises GeneratorExit here, not
            # TurnWedged, and that must not hand the lock back either.
            if not self._turn_wedged:
                self._restore_attachments()   # before _recheck_app_data: it reads the tree this heals
                self._recheck_app_data()
                self._record_resource_usage()
                self._clear_turn_baseline()
                self._release_turn()

    def _approve_locked(self, answers: str = "", plan_edits: str | None = None,
                        user_text: str | None = None, plan_id: str = ""):
        """The approve turn itself. Assumes the caller holds _turn_lock — approval reaches here both
        from the card's Approve button and from a bare approval typed in the composer (build_stream)."""
        project = self.project()
        if plan_edits is not None:
            project.app_for_turn().write_plan(plan_edits)
        # Fall back to the architecture when no plan is live: an architecture turn writes only
        # .sage/architecture.md (it isn't a one-shot handoff and must survive the build), so its card's
        # Build button would otherwise approve an empty plan and build nothing.
        live_plan = project.app_for_turn().read_plan() or ""
        plan_md = live_plan or project.app_for_turn().read_architecture() or ""
        # Nothing live to approve. A plan is a one-shot handoff — the `finally` below archives it the
        # moment a build consumes it — so a second click on a card that already built finds an empty
        # string here. Before this guard that string still went out as an approve prompt, and the
        # agent answered the only way it could: "there isn't a real change described in that approved
        # plan yet". A turn's worth of inference to be told the plan is blank, and a plan card in the
        # transcript that nobody can act on. build_stream's chat-approval path has always checked this
        # (it requires a non-empty read_plan before it approves); the Approve button never did.
        if not plan_md.strip():
            yield {"type": "error", "message": brand.text(
                "That plan was already built. Type the next change you want and {assistantName} "
                "will plan it.")}
            yield {"type": "done", "ok": False, "decision": "no plan to approve"}
            return
        # What was approved reaches the document. Two things were going missing here. The text: an
        # edit made in the card is written to plan.md above and built, but the document it came from
        # kept the pre-edit draft, so the record of what got built was wrong. And the decision: plan.md
        # is archived the moment the build consumes it (the `finally` below), so nothing else was ever
        # going to move the document off "Draft · Waiting for approval" — it said that long after
        # somebody approved it and watched it build. An architecture has no document, so only a live
        # plan marks one. What approval MEANS stays the review flow's own rule: named reviewers who
        # have not signed off keep the plan in review, because building was never their sign-off.
        if live_plan.strip():
            doc = self._approved_plan_doc(project, plan_id)
            if doc:
                # A version, not an overwrite, for the same reason a document edit makes one: the
                # draft people commented on has to survive the edit that built over it.
                if live_plan.strip() != (doc.get("markdown") or "").strip():
                    project.record.write_plan_doc_version(doc["id"], live_plan)
                self.review_plan_doc(doc["id"], {"action": "approve"})
        prior_mode = project.control.snapshot().mode
        # Approval means "build it now", so a turn approved from a read-only mode RUNS as Implement —
        # pinned to this turn only (see arm_turn_mode), never written to the user's picker. The
        # earlier set_mode-then-restore did move their picker, which meant a mode they changed while
        # the build streamed was reverted underneath them when it finished.
        run_as = Mode.IMPLEMENT if prior_mode in (Mode.PLAN, Mode.ASK) else None
        # The same check `build_stream` makes, on the mode this turn will really run as (#125).
        # Before `set_plan_retry_step(0)` and before the `finally` that archives the plan, so an
        # approve refused for its model leaves the card, the plan and a phased build's resume point
        # exactly as it found them — the person changes the model and presses Approve again.
        refusal = self._slot_refusal_events(project, run_as or prior_mode)
        if refusal:
            yield from refusal
            return
        # Phased only when the toggle is on AND the plan actually parsed into briefs. A plan written
        # before the toggle (or by a planner that ignored the format) builds the ordinary way rather
        # than half-phasing, which would be worse than not phasing at all.
        phased = bool(project.record.read_settings().get("phased_build")) and is_phasable(plan_md)
        # Where an earlier attempt at this plan stopped, or 0 for a plan nobody has built from yet.
        # Read here, before the build overwrites it, and only phasing can use it — an unphased build
        # has no seam to resume at, so it runs the plan whole however far the last attempt got.
        # A plan EDITED in the card resets it above (write_plan), which is the right answer: edited
        # steps are different steps, and resuming into them would build a plan nobody approved.
        resume_from = project.app_for_turn().read_plan_retry_step()
        # And cleared straight away: the resume point belongs to the attempt that wrote it, and this
        # is now a new one. Left standing, the number the LAST attempt died at would outlive a clean
        # build and keep its plan alive for ever. What this turn owes is written by the two things
        # that can know it — the give-up flag below, and a phase as it starts.
        project.app_for_turn().set_plan_retry_step(0)
        try:
            if phased:
                yield from self._phased_approve(project, plan_md, answers, user_text,
                                                start_step=resume_from)
            else:
                # The bubble is what the person did, not what we sent. Approving from the card passes
                # no `user_text`, and _build_stream's fallback is the prompt itself — so the whole
                # approve prompt (the plan, then the handoff digest) was landing in the transcript as
                # if the user had typed it. _phased_approve has always written "Approved the plan."
                # here; this is the same sentence on the path that runs when phasing is off.
                yield from self._build_stream(
                    _approve_prompt(plan_md, answers,
                                   handoff_note=chat_handoff.implement_note(project.app_for_turn().path)),
                    is_approval=True, mode=run_as,
                    user_text=user_text if user_text is not None else "Approved the plan.")
            # Approving from Ask mode builds (that's deliberate — the user asked for this plan), but
            # the mode goes straight back to Ask below. The user has just watched Ask write an app, so
            # the next change they type reasonably looks like it will build too, and instead runs
            # read-only and writes nothing. Say so here, where it lands right under the build.
            if prior_mode is Mode.ASK:
                ev = {"type": "ask-active",
                      "message": "Approving built this plan, but the mode is still Ask — it answers "
                                 "questions and never changes files. Switch to Auto or Implement "
                                 "before asking for your next change."}
                project.app_for_turn().append_history(ev, project.build_conversation)
                yield ev
        finally:
            # One-shot handoff: consumed, so move it out of the agent's live view (git keeps history).
            # Not when the turn gave up (#39): on the wedged path that writes to the tree the session
            # may still be holding, and on BOTH paths it would retire a plan for a build that never
            # happened. `_turn_wedged` alone was too narrow — it is false after a stall the session
            # confirmed, which is exactly the case where the person is holding a Try again button and
            # the plan they would be retrying has just been archived out from under it.
            #
            # One rule, two writers: a plan that still owes a build is not consumed. `_turn_gave_up`
            # is the whole-turn version — nothing was built, so the retry starts from the top. A
            # phased build writes its own step number instead (see _phased_approve), because it got
            # somewhere before it died and the retry resumes there. Either way the plan stays live
            # AND says so, which is what lets the next "try again" build it rather than re-plan it:
            # a plan waiting for its first approval looks identical on disk.
            app = project.app_for_turn()
            if self._turn_gave_up and app.read_plan_retry_step() == 0:
                app.set_plan_retry_step(1)
            if app.read_plan_retry_step() == 0:
                app.archive_plan()

    def _approved_plan_doc(self, project: Project, plan_id: str) -> dict | None:
        """The document the approved plan.md belongs to, or None.

        The card sends the id it was given when the plan was proposed, which is the only answer that
        cannot be wrong. A bare "yes, build it" typed in the composer has no card and sends nothing,
        and there the newest document is the best available answer — the same one the plan pin and
        patch_plan_doc already trust for the same question. A workspace whose plan predates plan
        documents has none at all, and gets None."""
        if plan_id:
            return project.record.read_plan_doc(plan_id)
        docs = self._app_plan_docs(project)
        return docs[0] if docs else None

    def _phased_approve(self, project: Project, plan_md: str, answers: str, user_text: str | None,
                        start_step: int = 0):
        """Build an approved plan one step at a time, each in a FRESH OpenCode session.

        The point is context, not parallelism: a cheap coder holds up in a clean 8k window and comes
        apart at 100k, so every phase starts from nothing but its own brief. That costs a session
        bootstrap per phase (OpenCode re-reads AGENTS.md and project context), which is exactly what
        the `sage-session` cost tag makes measurable — the tax is the thing this feature has to earn.

        Owns everything a turn owns and a phase doesn't: the user's chat bubble, the whole-build
        revert point, the failure flag, the git commit, and the single terminal `done`.

        `start_step` resumes an earlier attempt at this same plan rather than repeating it. A build
        that died at phase 4 of 6 keeps phases 1-3 on disk (see the failure path below), so starting
        over would buy a session per phase to redo work that is already there — and each redone
        phase would be editing the files the first attempt wrote. 0 and 1 both mean "from the top".
        """
        import time

        client = self._ensure_opencode()
        steps = parse_steps(plan_md)

        def persist(ev: dict) -> dict:
            # Same argument as _build_stream's persist(): the receipt has two yield sites here and
            # the tag has one writer.
            if ev["type"] == "app_change":
                self._tag_conversation(project, ev)
            if ev["type"] in _PERSISTED_EVENTS:
                project.app_for_turn().append_history(ev, project.build_conversation)
            return ev

        # Same ordering rule as _build_stream: refresh what the agent reads before the revert
        # point below.
        self._refresh_agent_inputs(project)
        project.app_for_turn().append_history(
            {"type": "user", "text": user_text if user_text is not None else "Approved the plan."},
            project.build_conversation)
        # ONE revert point for the whole build. _build_stream still checkpoints per phase (which is
        # what gives a gate violation its correct, narrow scope), so undoing everything needs a ref
        # that reaches back past all of them — hence discard_to rather than discard_changes.
        base = project.snapshot.commit_before_turn()
        history_baseline = project.app_for_turn().history_len()
        # What the app's code looked like before any phase ran, so a build that dies halfway can
        # still say whether it changed anything — see the failure path below (#56).
        tree_before = project.snapshot.working_tree_hash()
        # Per-phase circuit breakers bound each phase, not the build: 6 × (15 iterations, 600s) is an
        # hour of wall clock that nobody asked for.
        deadline = time.monotonic() + _env_int("SAGE_PHASED_MAX_SECONDS", 1800)

        yield persist({"type": "build-plan",
                       "steps": [{"n": s.n, "label": s.label, "files": s.files} for s in steps]})

        failed: tuple[PlanStep, str] | None = None
        notes: list[str] = []  # each finished phase's closing summary, handed to the ones after it
        for step in steps:
            if step.n < start_step:
                # Built by the attempt this one resumes, and still on disk. It gets its row so the
                # transcript shows a whole plan rather than a build that opens at "step 4 of 6" with
                # no account of the three before it — `kept` is what separates a phase this turn ran
                # from one it inherited. No note for it: the notes are the phases' own closing
                # summaries, they died with that turn's memory, and writing one on their behalf
                # would put words in their mouths. The code is on disk and the next phase's brief
                # already points at it ("the earlier steps are already done... read it if you need
                # it"), which is the same standing this phase would have had anyway.
                yield persist({"type": "step-done", "n": step.n, "total": len(steps),
                               "ok": True, "kept": True})
                continue
            # Where a retry picks up, written BEFORE the phase runs rather than after it fails. A
            # phase can end in ways that never come back here — a wedged session raises past this
            # loop entirely — and the resume point has to survive all of them, not just the tidy
            # failure below. Cleared on the success path; a plan that still owes a step is not
            # archived (see _approve_locked).
            project.app_for_turn().set_plan_retry_step(step.n)
            yield persist({"type": "step-start", "n": step.n, "total": len(steps),
                           "label": step.label, "files": step.files})
            outcome = yield from self._run_step(project, client, step, steps, answers, notes)
            if outcome == "stopped":
                # The user rejected the whole build, so leaving three of six phases on disk would
                # leave a state they never asked for and can't describe. Same semantics as Stop on a
                # normal turn: the turn vanishes, files and transcript both.
                project.snapshot.discard_to(base)
                project.app_for_turn().truncate_history(history_baseline)
                # Stop retires the plan, exactly as it does on an unphased build: the person said
                # they don't want this, so nothing is owed a retry and _approve_locked archives it.
                project.app_for_turn().set_plan_retry_step(0)
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
            #
            # Unless the next turn is "try again", which the resume point written before this phase
            # ran now answers directly: the plan stays live, and the retry opens at the phase that
            # broke rather than at a second plan card for a plan that is already approved.
            project.app_for_turn().set_last_turn_failed(True)
            # The finished phases are kept on purpose (above), so the app IS changed and still owes
            # the receipt for it — "which app now holds forty minutes of work" is exactly what this
            # card exists to stop people going to look for. The working tree rather than the phase
            # count, because a phase can finish without writing anything.
            if project.snapshot.working_tree_hash() != tree_before:
                yield persist(_app_change_event(project.app_for_turn()))
            yield persist({"type": "done", "ok": False,
                           "decision": f"phase {step.n} of {len(steps)} failed — {why}"})
            return

        project.app_for_turn().set_last_turn_failed(False)
        # Every phase ran, so the plan owes nothing and _approve_locked archives it as usual.
        project.app_for_turn().set_plan_retry_step(0)
        project.app_for_turn().mark_built()
        # One card for the whole phased build, here rather than per phase — the phases swallow their
        # own terminal events for the same reason (#56).
        yield persist(_app_change_event(project.app_for_turn()))
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

        outcome: dict | None = None
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
                sid = client.create_session(directory=str(project.app_for_turn().path))
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
        except TurnWedged:
            # A phase's `done` is swallowed above so one build ends once, not once per phase — but a
            # wedge never reaches the ending in _phased_approve that would emit the build's own, so
            # this is the last place it can be said at all. Without it the stream simply stops: no
            # terminal event, and no record that the build failed for the next turn to replan from.
            project.app_for_turn().set_last_turn_failed(True)
            if outcome is not None:
                yield outcome
            raise
        finally:
            if escalated:
                project.control.pick(original_pick)
        return reason

    def record_runtime_error(self, message: str, stack: str = "") -> None:
        """Store a runtime error the live preview reported (via /api/preview/runtime-error), stamped
        so build_stream can tell this turn's crash from a stale one. Best-effort: a report that
        arrives with no active project is simply dropped.

        Stamped with the app too, because the preview serves whichever app is ON SCREEN and the
        person may have switched away from the one being built (#77)."""
        import time

        if self._project is None:
            return
        self._project.runtime_error = {"message": message, "stack": stack, "ts": time.monotonic(),
                                       "app": self._project.workspace.app_id}

    def _await_runtime_error(self, project: Project, since: float, timeout: float = 4.0) -> dict | None:
        """Poll up to `timeout`s for a preview-reported runtime error newer than `since` (this turn's
        send time), from the app this turn is building. Returns it, or None if the preview stays
        clean. The HMR update -> re-render -> report round-trip usually lands within a second or two
        of the agent's last write.

        A crash from another app is not this turn's to fix: the person may have pointed the preview
        at a different Built App mid-build (#77), and feeding its stack to the agent would send it
        hunting a file that is not in the tree it can see."""
        import time

        app_id = project.app_for_turn().app_id
        deadline = time.monotonic() + timeout
        while True:
            rt = project.runtime_error
            if rt is not None and rt.get("ts", 0.0) >= since and rt.get("app", app_id) == app_id:
                return rt
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.5)

    def _save_to_git(self, project: Project, prompt: str) -> dict | None:
        """Commit + push the Project after a clean build so the app and .sage/ transcript are
        durable. Returns None when the volume isn't the root of its own git repo (local dev / the
        /tmp spike — no save line to show); otherwise a `saved` event. Never raises into the build.

        The Project root, not the app: one repo holds every Built App and the Project's own record,
        so this is the only directory git has ever been runnable in."""
        from ..workspace import git

        path = project.record.path
        if not git.is_repo_root(path):
            return None
        message = f"build: {prompt.splitlines()[0][:72]}" if prompt.strip() else "build: no prompt"
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
        """Pull the remote into the (already-committed, clean) Project, resolving merge conflicts
        with the agent. Returns a git.SyncResult, or None when there's no remote to pull from."""
        from ..workspace import git

        path = project.record.path
        if not git.has_remote(path):
            return None
        result = git.pull(path)
        if result.status == "conflict":
            result = self._resolve_conflicts(project, result.conflicts)
        # The pull already fetched, so this is a local re-read. Whatever it merged has stopped being
        # incoming, and the rail's badge goes out with it rather than at the next check (#78).
        self._check_remote(project, fetch=False)
        return result

    def _resolve_conflicts(self, project: Project, conflicts: list[str]):
        """Hand the conflicted files to the agent to resolve the markers, then commit the merge.
        Rolls the merge back (leaving the pre-pull state) if the agent leaves markers or errors."""
        from ..workspace import git

        path = project.record.path
        client = self._ensure_opencode()
        # A session at the PROJECT root, not the build session.
        #
        # Git names conflicts from the repo root, which is the volume: a conflict in a Built App
        # arrives as `apps/<appId>/src/App.tsx`. The build session is opened at
        # `app_for_turn().path`, so that same name resolved to `apps/<appId>/apps/<appId>/…` and
        # the agent edited nothing — `files_with_conflict_markers` then still found markers at the
        # root and the pull was rolled back. There was no input for which that could succeed.
        #
        # Rebasing the paths onto the app directory was the other way out, and it only covers the
        # conflicts that happen to be inside the selected app. A merge is the Project's: it can
        # land in a second Built App, or in the Project's own files at the root, and neither is
        # nameable from inside `apps/<appId>/`. The turn is Project-level, so the session is.
        #
        # Its own session rather than a re-rooted build one, because a build session belongs to a
        # Built App and to a conversation (ADR-0008) and this turn is neither — putting a merge in
        # it would file a Project-wide repair under whichever app happened to be on screen.
        sid = client.create_session(directory=str(path))
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
        git.finalize_merge(path, "build: merge remote changes")
        return git.SyncResult("merged", conflicts, "merged teammate changes (conflicts resolved)")

    def sync(self) -> dict:
        """Manual "Pull latest": commit in-progress edits, pull + agent-resolve teammate changes,
        then push the result so the repo and workspace agree. Returns a UI result dict."""
        from ..workspace import git

        project = self.project()
        path = project.record.path
        if not git.is_repo_root(path) or not git.has_remote(path):
            return {"status": "no-remote", "conflicts": [], "pushed": False,
                    "detail": "this app has no git remote to pull from"}
        # The turn lock, for the reasons publish takes it: `commit_all` commits the PROJECT ROOT —
        # one repo holding every Built App (ADR-0008) — so under a streaming build it commits half a
        # turn's writes, and `_integrate_remote` then runs an AGENT over that tree to resolve
        # conflicts. Two agents in one working tree is the collision #39 exists to prevent.
        #
        # Project-wide, so `buildRunning` in another app is still a reason to refuse: a build
        # streaming into app A is stopped by nothing when app B is selected, and this commit takes
        # A's half-written tree with it.
        #
        # Non-blocking, like publish's and Delete's: there is nothing to wait out, and a Pull latest
        # that sat silently until a long build finished would look like a control that did nothing.
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self._turn_wedged, "pull the latest changes")
        try:
            git.commit_all(path, "build: save before pull", exclude=self._leaked_copy_paths(project))
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
        finally:
            self._release_turn()

    def publish(self, *, new_app: bool = False) -> dict:
        """Publish (or republish) THIS app's project as a live Domino App, deploying the latest
        committed code on the default branch. An existing App gets a new version (stable URL);
        otherwise a new App is created + launched. Best-effort saves the current work first so the
        deploy ships the newest code. Returns {published, app_id, url, manage_url, republished}.

        `new_app` creates a fresh Domino App instead of re-publishing to the recorded one — the
        answer to a `MISSING_APP` refusal, and the only way out of an App deleted on its own
        settings page in Domino (#80). It is a thing a person asks for, never something a failed
        re-publish decides for itself: a publish that forgot whatever it could not reach would
        deploy a second copy of an app whose first copy is alive and shared, every time Domino had
        a bad minute.

        It is refused unless the App really is gone, which is the same rule read from the other
        side. `record_domino_app` OVERWRITES the one id Sage holds, so publishing beside a live App
        would put it beyond both Publish and Delete while it went on serving old code at a URL
        people already have. Nothing here clears the record either: it is replaced by the publish
        that succeeds, never cleared by one that then refuses.
        """
        if not publish_available(self._wm.path):
            raise RuntimeError(brand.text(
                "Publish is only available in a {assistantName} Builder workspace whose app repo is "
                "/mnt/code. This {productName} App is {assistantName} itself, not a {builtApp}."
            ))
        if self._control_plane is None or not self._domino_project_id:
            # DOMINO_PROJECT_ID is an env var name, so it is named, not renamed.
            raise RuntimeError(brand.text(
                "Publish is only available when this builder runs on {platformName} (missing "
                "control-plane or DOMINO_PROJECT_ID)."
            ))
        # One operation owns the working tree at a time (see `_turn_lock`). Publishing is not a
        # read: `_save_to_git` commits the PROJECT ROOT — one repo holds every Built App — then
        # pulls, and may run an agent turn to resolve conflicts. Under a streaming build that
        # commits half of whatever the turn is in the middle of writing and merges on top of it,
        # which is the exact collision the lock exists to prevent (#39).
        #
        # Project-wide rather than per app, which is why the UI's own guard is not enough on its
        # own: a build streaming into app A is stopped by nothing when app B is the one selected,
        # and the commit takes A's half-written tree with it.
        #
        # Non-blocking, like Delete's: there is nothing to wait out here, and a Publish that sat
        # silently until a long build finished would look like a control that did nothing.
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self._turn_wedged, "publish")
        try:
            project = self.project()
            # Which Domino App this Built App deploys to, read from the app itself: a Project holds many
            # and its Domino project holds one App per app, so "the project's App" names none of them
            # in particular (ADR-0008). Settled here rather than after the save because the guard needs
            # it — an app's sharing setting is a property of the deployed App. Before anything is
            # written, pushed or deployed, too: a refused publish must leave nothing behind, so a
            # creator who fixes the Binding and publishes again publishes the code they were looking at.
            deployed_app_id = project.workspace.domino_app_id()
            if deployed_app_id:
                gone = self._target_is_gone(deployed_app_id)
                if not new_app:
                    # An ordinary re-publish. Only a 404 stops it — see `_target_is_gone` for why an
                    # unreachable check carries on rather than refusing.
                    if gone:
                        raise self._missing_app(project)
                elif gone:
                    # The creator answered the refusal, so everything after here runs the first-publish
                    # path. Dropped from the LOCAL only: the record on disk is replaced by
                    # `record_domino_app` once a new App exists, and not before, so a
                    # `_refuse_unsafe_publish` refusal below cannot leave this app having forgotten an
                    # App that is still serving (#76's stranding, self-inflicted).
                    deployed_app_id = ""
                else:
                    # `new_app` is the answer to one question, and this is not that question. Creating a
                    # second App while the first is alive strands it: `record_domino_app` overwrites the
                    # only id Sage has, so neither Publish nor Delete could reach the old App again, and
                    # it would go on serving old code at a URL people already hold.
                    raise RuntimeError(brand.text(
                        "This app's published App is still there, so {assistantName} won't publish a "
                        "second one beside it — that would leave the first serving at a URL nothing "
                        "here could reach again. Publish normally to ship a new version to it. If "
                        "you want a fresh App, delete that one in {platformName} first."
                        if gone is False else
                        "{assistantName} couldn't reach {platformName} to confirm that this app's "
                        "published App is really gone, and it won't create a second one on a guess. "
                        "Try again in a moment."
                    ))
            self._refuse_unsafe_publish(project, deployed_app_id)
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
                raise RuntimeError(brand.text(
                    "'{entry}' is missing from this app, so {platformName} has no entry script to "
                    "run. Add {entry} to {where} and rebuild, then publish again.",
                    entry=_ENTRY_POINT, where=project.repo_rel(''))
                )
            # The refresh above is best-effort, so app.sh can be the current one while the server it execs
            # is absent — a deploy that reports success and then crash-loops on "can't open file
            # 'serve.py'". Ask what THIS app.sh needs rather than demanding serve.py of an older app whose
            # entry script still serves the build with Node.
            if _SERVER_SCRIPT in entry.read_text() and not (project.workspace.path / _SERVER_SCRIPT).exists():
                raise RuntimeError(
                    f"'{_SERVER_SCRIPT}' is missing from this app, but {_ENTRY_POINT} runs it to serve "
                    f"the app, so the deploy would start and immediately fail. Restore {_SERVER_SCRIPT} to "
                    f"{project.repo_rel('')} and publish again."
                )
            # Deploy the newest code: commit + push before publishing. Best-effort — a save failure (no
            # remote, offline) must not block a publish of whatever is already committed.
            try:
                self._save_to_git(project, "save before publish")
            except Exception:
                log.exception("publish: pre-publish save failed; publishing the last committed code")

            cp = self._control_plane
            pid = self._domino_project_id
            project_name = self._domino_project_name or self._project_id
            if deployed_app_id:  # already published — ship a new version, keep the URL
                try:
                    app = cp.republish_app(deployed_app_id)
                except NotFound as e:
                    # The preflight said the App was there and the version POST says 404. Usually that
                    # is the window between them — a git push, seconds long and room enough for somebody
                    # to delete it — and then this is the same refusal, not the raw 502 naming an app id
                    # (#80). But `versions` is a sub-resource, and a deployment that does not route it
                    # would 404 every re-publish: telling every creator their App was deleted and
                    # inviting each to publish a fresh one is how one broken route becomes a deployment
                    # full of duplicates. So ASK, on the error path where a second call costs nothing,
                    # and only say "deleted" when the App itself is what is missing.
                    if self._target_is_gone(deployed_app_id):
                        raise self._missing_app(project) from e
                    raise
                out = {"published": True, "app_id": app.id, "url": app.url, "republished": True}
            else:
                # The entry point is the app's own directory, and Domino fixes it when the App is
                # created — which is why the directory is named for an id that never changes (ADR-0008).
                # The App is named for the Built App: several of them share this Domino project, and the
                # project's name would list them as identical rows nobody can tell apart.
                app = cp.publish_app(pid, name=_app_display_name(project.workspace, project_name),
                                     entry_point=project.repo_rel(_ENTRY_POINT))
                project.workspace.record_domino_app(app.id)
                out = {"published": True, "app_id": app.id, "url": app.url, "republished": False}
            # Both branches, because both moved the code behind the URL. `record_domino_app` above runs
            # on the first publish only, so it cannot be where the time is written (#56).
            project.workspace.mark_published()
            # The deep link is /u/{owner}/{project}/apps/… — the Domino project's name, not the app's.
            out["manage_url"] = cp.app_manage_url(app.id, project_name)
            return out
        finally:
            self._release_turn()

    def publish_check(self) -> dict:
        """What the published app is going to refuse to answer, asked BEFORE it is published (#26).

        #15 already runs this check at the end of every build turn and splices the sentences into
        AGENTS.md, which closes the loop as long as there is a next turn. A creator who reads the
        plan, likes it and publishes never gets one: the app deploys, and several minutes later the
        first viewer meets a 503 carrying a sentence the creator could have read before the deploy
        started. So the same check runs once more, on the way out.

        Not a guard, and deliberately not shaped like one. A broken query is one screen of an app
        that may be fine everywhere else, and it re-exports nothing — which is the only thing
        `_refuse_unsafe_publish` exists to stop. This tells the creator; the creator decides. It is
        therefore a separate read rather than a step inside `publish`, and `publish` is unchanged.

        Local and pure: two JSON files off the workspace disk and no network, so the common answer —
        an app with no queries, or with queries that hold together — costs the publish flow nothing.

        `checked` is false when Sage could not run the check at all (a template carrying no
        `serve.py`). That is not the same answer as "no problems", and must not be reported as one.
        """
        project = self.project()
        problems = catalog_problems(self._wm.template, project.workspace.path)
        return {"checked": problems is not None, "queries": problems or []}

    def publish_egress(self) -> dict:
        """What of this app's data leaves Domino when it calls a model, asked before publishing (#35).

        BESIDE `publish_check`, not inside it, and the two routes are fired in parallel. Telling a
        vendor-backed Alias from a Domino-hosted one needs the gateway's Alias listing, and
        `publish_check`'s "local and pure ... no network" is load-bearing — it is why the common
        case costs the publish flow nothing. Folding a listing into it would make every publish wait
        on the network for a sentence most apps do not earn, and a slow listing would hold up the
        query warnings that were already sitting on the disk.

        The join is checked on the manifest FIRST, so an app with no store bound, or no Alias bound,
        makes no request at all. That is most apps, and they pay nothing for this.

        Never a refusal. ADR-0012 settled that no combination is refused, so this returns prose or
        nothing and `publish` neither calls it nor cares whether the UI did.

        `checked` is false when the listing could not be fetched, and `notice` is None either way:
        "checked and nothing to say" and "could not check" are different answers and must not
        collapse, even though a creator sees the same silence. The silence is deliberate — see
        `egress_notice`.
        """
        project = self.project()
        bindings = parse_bindings(project.workspace.read_bindings())
        if not needs_listing(bindings):
            return {"checked": True, "notice": None}
        try:
            aliases: list[LlmAlias] | None = self._resources.list_llm_aliases()
        except Exception:
            log.exception("publish-egress: couldn't list LLM Aliases to say where this app's data goes")
            aliases = None
        return {"checked": aliases is not None, "notice": egress_notice(bindings, aliases)}

    def publish_status(self, app_id: str) -> dict:
        """Deploy status of a published app so the UI can poll after Publish. Maps the raw instance
        status to a phase: running (live) / failed / pending (still deploying)."""
        if self._control_plane is None:
            raise RuntimeError(brand.text(
                "Publish status is only available when this builder runs on {platformName}."))
        raw = self._control_plane.app_status(app_id)
        s = raw.lower()
        if s in _RUNNING_STATES:
            phase = "running"
        elif s in _FAILED_STATES:
            phase = "failed"
        else:
            phase = "pending"
        return {"app_id": app_id, "status": raw, "phase": phase}

    def _target_is_gone(self, deployed_app_id: str) -> bool | None:
        """Whether the Domino App this Built App publishes to has been deleted (#80).

        Three answers, and the third is the one the ticket turns on: True the App 404s, False it
        answered, None Sage could not get an answer at all. Only True is evidence. "Domino is
        having a bad minute" must never be read as "the App is gone", because acting on that
        creates a duplicate deployment — the failure #70 exists to prevent, arriving by a new road.

        The tri-state is here rather than a refusal because the two callers act on None in OPPOSITE
        directions, and both are right:

        - an ordinary re-publish carries on. It is posting a version to an id it already had, which
          can deploy nothing new whatever the answer, so an unreachable check must not take away a
          publish that worked before this preflight existed. It also must not take one away
          PERMANENTLY: `settings.json` is committed, so a teammate re-publishing an App they hold
          no grant on reads 403 here, not 404, and a refusal on 403 would wedge them forever —
          the very shape of bug this is fixing.
        - `new_app` refuses. That one CREATES an App, so an unconfirmed answer is exactly the guess
          that strands the App still serving.
        """
        try:
            return not self._control_plane.app_exists(deployed_app_id)
        except Exception:
            log.exception("publish: couldn't check whether Domino App %s still exists", deployed_app_id)
            return None

    def _missing_app(self, project: Project) -> PublishRefused:
        # Same fallback chain `publish` uses to NAME an App it creates, for the same reason: on a
        # builder that never got DOMINO_PROJECT_NAME, an unnamed app would otherwise leave a hole
        # where the refusal's subject should be.
        return PublishRefused([missing_app_problem(_app_display_name(
            project.workspace, self._domino_project_name or self._project_id))])

    def _refuse_unsafe_publish(self, project: Project, deployed_app_id: str) -> None:
        """Refuse a publish that would re-export a Data Source (#12). No-op for an app that reads
        none, which is every app Sage built before #11.

        The two questions are asked here rather than carried in the manifest. A Binding records
        which Data Source an app reads, and deliberately not what kind of credential it had at the
        time: a credential can be changed from individual to shared, or the other way, long after
        the pick, and the answer that matters is the one true at the moment the app is shared.
        Sharing is read from the deployed App for the same reason — Sage sets it once, at create,
        and cannot set it again on a re-publish.

        Both reads are best-effort in the sense that a failure is caught here; what a failure MEANS
        is `publish_problems`'s decision, and the two differ (a missing listing refuses, an
        unreadable visibility does not).

        One meaning is no longer among them: `_refuse_missing_target` has already established that
        the App is there, so `UNCHECKED_APP` is now only ever the transient it reads as (#80).
        """
        recorded = parse_bindings(project.workspace.read_bindings())
        bindings = data_source_bindings(recorded)
        if not bindings:
            return
        try:
            sources: list[DataSource] | None = self._resources.list_data_sources()
        except Exception:
            log.exception("publish: couldn't list Data Sources to check the credential guard")
            sources = None
        visibility: str | None = ""   # "" = nothing published yet, so nothing to read
        if deployed_app_id:
            try:
                visibility = self._control_plane.app_visibility(deployed_app_id)
            except Exception:
                log.exception("publish: couldn't read the app's visibility")
                visibility = None
        problems = publish_problems(bindings, sources, visibility)
        if problems:
            raise PublishRefused(problems)

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

    def stop_build(self, kind: str = "", conversation: str = "", app: str = "") -> bool:
        """Interrupt the turn in flight — Build or Chat. Both poll `stop_requested` and
        both clear it as they unwind; Build also reverts the files and history the turn wrote,
        Chat keeps what it wrote (see _chat_stream, _build_stream's handle_stop).

        A Stop with nothing running is a no-op rather than a flag left lying about. It used to set
        `stop_requested` regardless, and nothing cleared it until a turn tripped over it — so a
        Stop pressed twice, or pressed in the second a turn was already ending, silently killed the
        NEXT question before it ran a step.

        `kind` and `conversation` name the turn the Stop was AIMED at, and it is a no-op when that
        is no longer the turn running (#126). The queue made this necessary: Stop ends one turn and
        the queue advances (ADR-0013), so between the press and the POST the lock can change hands
        and the Stop lands on a question the person never aimed at. Naming nothing keeps the old
        meaning — stop whatever is running — which is what a caller with only one turn to mean has
        always sent. A raw-lock holder has no ticket, so an aimed Stop never matches one: you cannot
        Stop a publish, and that is the answer rather than an omission.

        Returns whether it actually interrupted anything, so the route stops answering
        `{"stopped": true}` to a Stop it declined to fire — the same class of lie as the button
        this fixes.
        """
        if not self.turn_busy():
            return False
        if kind or conversation:
            running = self._turns.running()
            if running is None:
                return False
            if kind and running.kind != kind:
                return False
            if conversation and running.conversation != conversation:
                return False
            # An empty `running.app` matches anything: a Chat turn has no app, and a build whose app
            # could not be read must not become unstoppable over a field nobody could fill.
            if app and running.app and running.app != app:
                return False
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
        return True

    def _effective_catalog(self, record: ProjectRecord) -> ModelCatalog:
        overrides = record.read_catalog_overrides()
        return replace(self._catalog, **overrides) if overrides else self._catalog

    def model_assignments(self) -> dict:
        """What the model panel is drawn from: the three assignable slots, and the Aliases a person
        may put in them with whether each will actually answer (ADR-0017).

        Project-scoped, which is why it is not `preflight_slots`. That one answers about the
        DEPLOYMENT catalog before any project is attached, and its own docstring hands this job here;
        the value it caches is a module global computed once at startup, over a catalog no project
        has touched.

        A slot carries the model it runs AND the default it would revert to. The panel cannot derive
        the second from the first: once a slot is assigned, the catalog it is showing no longer holds
        what "Use the default" goes back to.

        A gateway that will not answer costs the Alias list, not the slots. The panel opens read-only
        with the reason rather than falling back to the models already assigned — a list that can
        only offer what is already chosen cannot express a change, which is the defect being fixed.
        """
        defaults = self._catalog
        project = self.project()
        live = project.shim.catalog
        # Key presence, not `live != default`. Assigning a slot to the model that happens to BE the
        # deployment default writes an override all the same, and reporting that as "following the
        # default" is a lie with a consequence: the day the deployment default moves, this project
        # will not follow it, and the panel said it would.
        overrides = project.record.read_catalog_overrides()
        slots = [
            {
                "slot": slot,
                "model": getattr(live, slot),
                "default": getattr(defaults, slot),
                "assigned": slot in overrides,
                "problem": None,
            }
            for slot in ASSIGNABLE_SLOTS
        ]
        try:
            aliases = self._resources.list_llm_aliases()
        except ResourceUnavailable as e:
            return {"slots": slots, "aliases": [], "error": str(e)}
        # One listing for every row, on the same `wanted` skip preflight uses: an all-vendor gateway
        # has no endpoint to join to and pays nothing for the check.
        endpoints, errors = self._endpoint_listing(aliases, {a.name for a in aliases})
        # Preflight's own verdict on the slots as they stand, which is what makes the read after a
        # save a re-check rather than a redraw: greying a menu row says the model is bad, and only
        # this says the SLOT is. Both of preflight's questions, in preflight's own words — a second
        # set of sentences for the same two faults is how they come to disagree.
        verdicts = {p.slot: p.message for p in
                    list(unresolved_slots(live, aliases))
                    + list(slots_on_dead_endpoints(live, aliases, endpoints))}
        for row in slots:
            row["problem"] = verdicts.get(row["slot"])
        return {
            "slots": slots,
            "aliases": [
                {
                    "name": a.name,
                    "display_name": a.display_name,
                    # Carried so the panel can reuse the chat-capable filter the Chat picker already
                    # applies, rather than growing a second copy of that rule on this side.
                    "capabilities": a.capabilities,
                    "serving": (problem := alias_problem(a.name, aliases, endpoints)) is None,
                    "problem": problem,
                }
                for a in aliases
            ],
            # A failed endpoint listing is not a failed panel: every Alias is still offered, and
            # `endpoint_status` already reads "not checked" as "learned nothing".
            "error": " ".join(errors) or None,
        }

    def set_catalog(self, **fields: str | None) -> ModelCatalog:
        """Assign the model a Build mode runs on, or take an assignment back (ADR-0017).

        Three cases, and telling the second from the third is the whole point. A field carrying a
        model id ASSIGNS that slot. A field carrying `None` or `""` CLEARS it, so the slot falls
        back to the deployment default `_effective_catalog` layers over — without this an
        assignment, once made, could never be undone, and the "Use the default" row had nothing to
        call. A field that is ABSENT is left alone: the drawer saves one row at a time and must not
        silently revert the other two.

        The new catalog is rebuilt through `_effective_catalog` rather than by patching the live
        one, because that is the only expression that knows what a cleared slot reverts TO.
        """
        unknown = sorted(k for k in fields if k not in SLOTS)
        if unknown:
            # Not defensive padding: the write below lands BEFORE the catalog is rebuilt, so an
            # unknown key would be persisted to `model_overrides.json` and then raise a TypeError
            # out of `replace()` on every read of this project's catalog afterwards. A transient 400
            # in place of a durable brick. Checked against `SLOTS` rather than `ASSIGNABLE_SLOTS`
            # because the sovereign slots have always been settable here and narrowing that would
            # take away a capability nobody asked to lose.
            raise ValueError(f"not a model slot: {', '.join(unknown)}")
        project = self.project()
        if not fields:
            return project.shim.catalog
        # Nothing pins the catalog for the duration of a turn, unlike `arm_turn_mode`, so a change
        # accepted here would move the rest of a running build onto another model with the first
        # half's tool calls in context. Same guard the override chip already closes against.
        if not self._turn_lock.acquire(blocking=False):
            raise TurnBusy(self._turn_wedged, "change the model")
        try:
            overrides = project.record.read_catalog_overrides()
            for slot, model in fields.items():
                if model:
                    overrides[slot] = model
                else:
                    overrides.pop(slot, None)
            project.record.write_catalog_overrides(overrides)
            new_catalog = self._effective_catalog(project.record)
            project.shim.set_catalog(new_catalog)
            # An assignment that saves and visibly does nothing is the worst outcome available
            # here, and a standing override shadowing the slot would do exactly that. It clears on
            # ANY assignment rather than on the one being shadowed: `picked_model` is a single
            # mode-independent field, so a narrower clear is not expressible without reshaping it.
            project.control.pick(None)
            return new_catalog
        finally:
            self._turn_lock.release()

    def history(self, conversation: str | None = None) -> list[dict]:
        """Reads straight from the app's directory on the volume, so the transcript is available
        without starting the preview (attaching the project) — a plain GET must not spin up Vite."""
        workspace = self._wm.app_workspace(self._project_id)
        self._adopt_legacy_build_history(workspace, self._wm.project_record(self._project_id))
        return workspace.read_history(conversation)

    def list_project_resources(self) -> list[dict]:
        """Domino Resources the creator added to this project — the rail, not the catalogue.

        Every row carries `usedBy`: each Built App that records a Binding for it, with the app's id,
        the name it goes by, and the Scope that Binding recorded (#133). One look answers where a
        Resource is used, and the rail's `Used by N apps` subtitle finally has data behind it.

        Computed on read, never written to the membership file. The answer lives in the apps' own
        manifests (ADR-0010) and a copy here would go stale the moment any app bound or unbound —
        including one nobody was looking at. It is the scan `remove_project_resource` refuses on, so
        the drawer names exactly the apps the refusal would.
        """
        rows = self.project(start_preview=False).record.read_project_resources()
        scanned = self._app_bindings() if rows else []
        return [
            {**row, "usedBy": [
                {"appId": ws.app_id, "name": _app_display_name(ws), "scope": b.scope}
                for ws, b in self._apps_that_bind(str(row.get("id") or ""), scanned)
            ]}
            for row in rows
        ]

    def _backfill_membership_from_bindings(self, record: ProjectRecord) -> None:
        """Give every Binding recorded before membership-on-bind existed its membership row (#140).

        `_join_project_on_bind` landed on 2026-09-01 in `29d4930` with no backfill, so a Project
        whose Data Source was bound the week before opens on a rail claiming it uses nothing, beside
        an app that plainly uses it. Under ADR-0020 the working set's job is orientation, which
        makes completeness a correctness requirement rather than a nicety: the section is true or it
        is wrong.

        Written through `_join_project_on_bind`, so a migrated row is the row a live bind writes —
        same prefixed id, same name, same `alias` for the one kind whose picker needs it. The
        catalogue half a door hands `_record` is the listing that door had just read, and a
        migration has no door and no listing; it fetches none of its own, exactly as the join does
        not (ADR-0018). What it leaves out is filled in by the next Add or bind for that Resource,
        because `add_project_resource` fills a row's gaps rather than replacing it.

        ONE-SHOT, and the flag is what makes it so. Recomputing the missing rows on every read was
        the cheap alternative and it is the union on read wearing a different hat: it would keep the
        file reconciled to the Bindings for ever, so a future door that binds without joining would
        be repaired behind itself instead of showing up as the correctness bug ADR-0020 says it is.
        Once done, the file is the single source of truth again.

        A write the disk refuses never fails the open — the join swallows it, as it does for a live
        bind. What it does do is leave the flag down, so the next open tries again: a migration that
        marked itself done over a row that never landed would leave the section permanently short,
        and this is the one place that can still tell.
        """
        settings = record.read_settings()
        if settings.get(_MEMBERSHIP_BACKFILLED):
            return
        members: set[str] = set()
        for row in record.read_project_resources():
            rid = str(row.get("id") or "").strip()
            if not rid:
                continue
            members.add(rid)
            # The tolerance `_apps_that_bind` extends, for the reason it extends it: an older row
            # may key a Data Source under `datasource:`, and writing a second row under
            # `data_source:` is exactly the split the removal guard then cannot join.
            kind, _, rest = rid.partition(":")
            if kind == "datasource":
                members.add(f"{KIND_DATA_SOURCE}:{rest}")
        joined: set[str] = set()
        try:
            for _workspace, bindings in self._app_bindings():
                for binding in bindings:
                    rid = f"{binding.kind}:{binding.id}"
                    if rid in members:
                        continue
                    self._join_project_on_bind(binding, {})
                    members.add(rid)
                    joined.add(rid)
        except OSError:
            log.warning("membership: could not read the app manifests to backfill", exc_info=True)
            return
        if joined and not joined <= {
                str(row.get("id") or "").strip() for row in record.read_project_resources()}:
            log.warning("membership: backfill did not land; leaving it for the next open")
            return
        settings[_MEMBERSHIP_BACKFILLED] = True
        record.write_settings(settings)

    def add_project_resource(self, item: dict) -> dict:
        """Put one Resource in this project's working set. Idempotent on id."""
        import time

        rid = str((item or {}).get("id") or "").strip()
        if not rid:
            raise ValueError("id required")
        kind = str(item.get("kind") or "").strip()
        name = str(item.get("name") or "").strip()
        if not kind or not name:
            raise ValueError("kind and name required")
        added = {"added": False, "item": None}

        keep = ("id", "kind", "name", "description", "project", "path", "bindingKey",
                "alias", "capabilities", "reasoning_efforts")

        def change(items: list[dict]) -> list[dict]:
            for row in items:
                if row.get("id") == rid:
                    # Still not added, and still not a rename: what an earlier, thinner write left
                    # out is filled in, and what the row already says is left exactly as it is. A
                    # row that reached the working set missing its alias could otherwise never be
                    # repaired, because adding it again lands right here.
                    for k in keep:
                        if k not in row and item.get(k) is not None:
                            row[k] = item[k]
                    added["item"] = row
                    return items
            row = {k: item[k] for k in keep if k in item and item[k] is not None}
            row["id"] = rid
            row["kind"] = kind
            row["name"] = name
            row["addedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            added["added"] = True
            added["item"] = row
            return items + [row]

        self.project(start_preview=False).record.update_project_resources(change)
        if (item or {}).get("pin"):
            self.pin_project_resource(rid, item["pin"])
            added["item"] = next(
                (r for r in self.list_project_resources() if r.get("id") == rid),
                added["item"],
            )
        return {"added": added["added"], "item": added["item"]}

    def remove_project_resource(self, resource_id: str) -> bool:
        """Drop a Resource from this project's working set. False if it was not there.

        Refuses when ANY Built App in this Project still records a Binding for it — membership is
        not a back door to unbind. A Resource is picked once for the Project; a Binding names
        exactly one app (ADR-0008).
        """
        rid = str(resource_id or "").strip()
        if not rid:
            return False
        bound = self._apps_that_bind(rid)
        if bound:
            _, first = bound[0]
            apps = [_app_display_name(ws) for ws, _ in bound]
            # Each file is named with its app once more than one app binds. Every Built App is
            # seeded from the same template, so `src/App.tsx` in two of them is two files and two
            # edits — a bare path would say neither which one nor how many. With a single app the
            # sentence above already says which, and repeating it on every line is noise.
            many = len(bound) > 1
            refs = [f"{app} — {ref}" if many else ref
                    for (workspace, binding), app in zip(bound, apps)
                    for ref in self._resource_usage(workspace, binding)]
            raise ResourceStillBound(first.display_name or first.name or rid, apps, refs)
        found = {"ok": False}

        def change(items: list[dict]) -> list[dict]:
            kept = [row for row in items if row.get("id") != rid]
            found["ok"] = len(kept) != len(items)
            return kept

        self.project(start_preview=False).record.update_project_resources(change)
        return found["ok"]

    def _app_bindings(self) -> list[tuple[Workspace, list[Binding]]]:
        """Every Built App's recorded Bindings, oldest app first, one manifest read per app.

        Read once and filtered many times. The rail asks where each of its rows is used, and a scan
        per row would re-read every manifest once per Resource on every refresh of a panel that
        redraws on each app switch.
        """
        out: list[tuple[Workspace, list[Binding]]] = []
        for app_id in self._wm.app_ids():
            workspace = self._wm.app_workspace(self._project_id, app_id)
            out.append((workspace, parse_bindings(workspace.read_bindings())))
        return out

    def _apps_that_bind(
        self,
        resource_id: str,
        scanned: list[tuple[Workspace, list[Binding]]] | None = None,
    ) -> list[tuple[Workspace, Binding]]:
        """Every Built App that still records a Binding for this membership id, oldest first.

        Every app rather than the selected one: a Binding belongs to one app and a Project holds
        many (ADR-0008), so a guard that reads the manifest in front of you lets a tidy-up break an
        app nobody was looking at. The apps come from the same directory scan the rail is built
        from, so none can be missed by being absent from an index nobody wrote to.

        `scanned` lets a caller with many ids to answer pay for that directory scan once. Left out,
        this reads the manifests itself — a removal is one deliberate act and can afford it.
        """
        rid = str(resource_id or "").strip()
        if not rid:
            return []
        aliases = {rid}
        if ":" in rid:
            kind, _, rest = rid.partition(":")
            aliases.add(rest)
            if kind == "datasource":
                aliases.add(f"data_source:{rest}")
            elif kind == "data_source":
                aliases.add(f"datasource:{rest}")
        out: list[tuple[Workspace, Binding]] = []
        for workspace, bindings in (self._app_bindings() if scanned is None else scanned):
            for b in bindings:
                if f"{b.kind}:{b.id}" in aliases or b.id in aliases:
                    out.append((workspace, b))
                    break
        return out

    def pin_project_resource(self, resource_id: str, pin: dict) -> dict:
        """Pin one file or table on a membership parent. Parent must already be in the project."""
        rid = str(resource_id or "").strip()
        if not rid:
            raise ValueError("id required")
        found = {"item": None}

        def change(items: list[dict]) -> list[dict]:
            out: list[dict] = []
            for row in items:
                if row.get("id") != rid:
                    out.append(row)
                    continue
                leaf = _normalize_pin(str(row.get("kind") or ""), pin)
                pins = [p for p in (row.get("pins") or []) if isinstance(p, dict)]
                key = _pin_key(leaf)
                if any(_pin_key(p) == key for p in pins):
                    found["item"] = row
                    out.append(row)
                    continue
                updated = {**row, "pins": pins + [leaf]}
                found["item"] = updated
                out.append(updated)
            return out

        self.project(start_preview=False).record.update_project_resources(change)
        if found["item"] is None:
            raise KeyError(rid)
        return found["item"]

    def unpin_project_resource(self, resource_id: str, pin: dict) -> bool:
        """Drop one pin from a membership parent. Does not drop the parent."""
        rid = str(resource_id or "").strip()
        if not rid:
            return False
        try:
            leaf = _normalize_pin(
                "dataset" if pin.get("path") else "data_source", pin)
        except ValueError:
            return False
        key = _pin_key(leaf)
        found = {"ok": False}

        def change(items: list[dict]) -> list[dict]:
            out: list[dict] = []
            for row in items:
                if row.get("id") != rid:
                    out.append(row)
                    continue
                pins = [p for p in (row.get("pins") or []) if isinstance(p, dict)]
                kept = [p for p in pins if _pin_key(p) != key]
                found["ok"] = len(kept) != len(pins)
                updated = {**row, "pins": kept}
                if not kept:
                    updated.pop("pins", None)
                    updated["pins"] = []
                out.append(updated)
            return out

        self.project(start_preview=False).record.update_project_resources(change)
        return found["ok"]

    def list_assets(self) -> list[dict]:
        """Every Dataset this caller can read. `mount_path` says which are also on this disk —
        useful for uploads, which need a writable mount, and no longer a condition of reading."""
        return [
            {
                "id": a.id,
                "name": a.name,
                "tags": a.tags,
                "project": a.project,
                "writable": bool(a.mount_path and os.access(a.mount_path, os.W_OK)),
                "mount_path": a.mount_path,
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
                "reasoning_efforts": a.reasoning_efforts or alias_reasoning_efforts(a.name),
            }
            for a in self._resources.list_llm_aliases()
        ]

    def list_model_apis(self) -> list[dict]:
        """Model APIs this creator can compose with, shaped for the Resource Browser (#8, #42).

        No longer only this project's. The deployment-wide listing is still an admin surface, so the
        provider asks once per project the creator belongs to and unions the answers;
        `_domino_project_id` is now the home project of that fan-out rather than the whole of it.

        `project` is empty for a Model API deployed here, and names the project otherwise. The rail
        renders it as a row's second line, so it has to be blank rather than "this project" — a label
        repeated on every row would say nothing while hiding the rows where it says something.
        """
        return [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "status": m.status,
                "project": m.project_name,
            }
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
        return self._labelled_bindings(
            [b.to_dict() for b in parse_bindings(self.project().workspace.read_bindings())])

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
        return self._record(
            Binding(KIND_LLM_ALIAS, alias["id"], alias["name"], alias["display_name"]),
            {k: alias.get(k) for k in ("description", "capabilities", "reasoning_efforts")})

    def bind_model_api(self, model_api_id: str) -> list[dict]:
        """Record that this app uses one Model API, and return the new Binding list (#9).

        Reach is established per model, not per project (#42). This used to look the id up in the
        project's own listing, which made a model deployed in any other project impossible to bind —
        and impossible to paste a token for either, since the paste form only ever opened under a row
        the listing had drawn. A creator could call the model from a terminal and still had no way to
        tell Sage about it.

        Two ways to establish it, and either is enough:

        - Domino describes the model. `get_model_api` answers wherever the caller has access,
          whatever project it lives in, and its answer also carries the name.
        - The creator holds a verified access token for it. `save_model_api_credential` calls the
          model before it stores anything, so a stored credential IS a demonstrated call — a stronger
          proof of reach than any listing, and the only one available for a model in a project the
          creator is not a member of.

        With neither, this is still a LookupError, and it still means what it meant: nothing Sage can
        see says this model is yours to depend on.

        A Model API has ONE name and no separate display name, so both fields carry it. When only the
        token proved reach, the id stands in — an unlovely row, but a truthful one, and it stops
        being unlovely the moment Domino will describe the model.

        Refuses without a stored credential, which is what makes a Model API Binding mean something
        an app can act on. Unlike an LLM Alias — where the viewer's own session is the credential —
        a Model API opens for nothing but its access token, so a Binding recorded without one would
        pin a model the app cannot call and report it as a dependency that works.
        """
        found = self._resources.get_model_api(model_api_id)
        credential = self._credentials(self.project()).get(model_api_id)
        if found is None and credential is None:
            raise LookupError(model_api_id)
        if credential is None:
            raise CredentialRequired(model_api_id)
        name = found.name if found else model_api_id
        # Nothing when Domino would not describe the model — the credential alone got it here, and
        # the rail draws the second line blank rather than inventing one.
        return self._record(
            Binding(KIND_MODEL_API, model_api_id, name, name),
            {"description": found.description, "project": found.project_name} if found else {})

    def bind_dataset(self, dataset_id: str) -> list[dict]:
        """Record that this app uses one Dataset, and return the new Binding list (#141).

        Resolved against `list_assets` for the reason `bind_llm_alias` resolves an Alias against its
        listing: that listing is Domino's answer about what this caller can reach, and the name that
        comes out of it is the label the manifest keeps for the moment the gateway is unreachable.

        A Dataset has one name and no separate display name, so both fields carry it — the shape
        `bind_model_api` already writes. No scope: the Scope fields belong to a Data Source, and a
        Dataset is reached whole.

        This is not an Attachment and does not write one. An Attachment is a file copied into the
        app's tree and rebuilt at deploy time (`attach_file`); this says which Dataset the app is
        recorded as reading. The two are the app's two named things, and they stay two.
        """
        asset = next((a for a in self.list_assets() if a["id"] == dataset_id), None)
        if asset is None:
            raise LookupError(dataset_id)
        name = asset["name"] or dataset_id
        return self._record(
            Binding(KIND_DATASET, asset["id"], name, name),
            {"project": asset.get("project"), "path": asset.get("mount_path")})

    def bind_data_source(
        self, source_id: str, database: str = "", schema: str = "", table: str = "",
    ) -> list[dict]:
        """Record that the app uses one Data Source, and which part of it (#11, #142).

        Validated against the project's own listing, as the two kinds above are. The scope is not
        validated against the store: proving a schema exists would cost two more queries and several
        more seconds, and these names did not come from a keyboard — they came from a list Sage
        enumerated.

        THE SCOPE IS OPTIONAL AND USUALLY ABSENT HERE (#142, ADR-0021). Binding and scoping are two
        acts now: the door on the Built App's own surface has no cascade position to pass, so it
        calls this with no scope at all and `scope_data_source` answers the narrower question
        afterwards. A scopeless record is the named, unfinished state the glossary already had a
        word for — "the Resource is recorded but the part of it the app reads is not" — and not a
        half-finished one. It is also all a connector Sage has no dialect for can ever offer.

        Two callers still arrive with a scope in hand, and both are right to: the handoff replays a
        chip that carries one, and `scope_data_source` delegates here so that one writer holds the
        charset check, the column read and the replace-in-place rule.

        Re-binding replaces in place, because `Binding.key` leaves the scope out.
        """
        source = self._data_source(source_id)
        # Charset-checked here as well as where the SQL is built. Not belt-and-braces for its own
        # sake: this is what keeps the manifest holding only names that can be sent, so the slice that
        # builds the app's query out of this record inherits the guarantee rather than re-earning it.
        parts = [safe_identifier(p) if p else None for p in (database, schema, table)]
        # The connector type travels with the scope, because the published app cannot ask for it: what
        # a Data Source will accept as a configuration override differs per connector, and that is what
        # decides whether the scope recorded here reaches the store or has to be written into the SQL
        # (#14). Domino's own string, not the label — `connector` says "Snowflake" for every Snowflake
        # source, and only the type string keys a table.
        binding = Binding(KIND_DATA_SOURCE, source.id, source.name, source.name, *parts,
                          source.connector_type)
        # The schema BEFORE the record, because recording is what re-renders what the agent is told
        # and that render reads this file. One extra query at the end of a cascade the creator has
        # just spent three on, and the last one they wait for.
        self._write_bound_schema(source, binding)
        # `connector` is the connector type's LABEL ("Snowflake"), which is what the rail draws as
        # the row's second line — not `connector_type`, the config-class string the Binding keeps.
        return self._record(binding, {"description": source.connector})

    def scope_data_source(
        self, source_id: str, database: str = "", schema: str = "", table: str = "",
    ) -> list[dict]:
        """Say which part of a Data Source the app reads, against a Binding it already holds (#142).

        The second of the two acts ADR-0021 split the bind into, and the only one of them that walks
        databases, schemas and tables. It is the cheaper act on purpose: the dependency is already
        recorded, so this narrows a decision rather than making one, and it can be called again
        whenever the choice moves — which is what "change the Scope without re-binding" means.

        WHAT IT ADDS OVER `bind_data_source` WITH A SCOPE is the refusal. A Scope is a part of a
        dependency, so writing one where there is no Binding would record the dependency itself as a
        side effect of narrowing it — a bind arriving through the door that exists because binding
        and scoping came apart.

        Everything after the guard is `bind_data_source`'s, deliberately. One writer for one record:
        the charset check, the column re-read and the replace-in-place rule cannot drift between the
        act that creates the Binding and the act that scopes it.

        An empty scope at every level is a legal argument here too, and it means the same thing it
        means anywhere else — the Binding stands and the part of it the app reads is not chosen.
        """
        recorded = parse_bindings(self.project().workspace.read_bindings())
        if not any(b.kind == KIND_DATA_SOURCE and b.id == source_id for b in recorded):
            raise ResourceNotBound(source_id)
        return self.bind_data_source(source_id, database, schema, table)

    def _write_bound_schema(self, source: DataSource, binding: Binding) -> None:
        """Read what the bound tables hold, once, and record it for the agent (#15).

        Written even when nothing came back, and written on every re-bind. This file describes the
        Binding beside it, so a Scope that moved to another schema must not leave the previous
        schema's columns behind for the agent to write queries against.

        A store that will not answer is not a failed bind. The Binding is the creator's decision and
        it stands; what they lose is the column names, and the agent is told that in as many words
        rather than left to guess at names. Same reason readiness degrades rather than blocking the
        listing (#10).
        """
        columns: list[Column] = []
        if binding.schema:      # columns live under a schema; a Scope that stopped above one has none
            try:
                columns = self._resources.list_columns(
                    source, binding.database or "", binding.schema, binding.table or "")
            except ResourceUnavailable as e:
                log.info("bound schema: %s did not answer for columns — %s", source.name, e)
            except Exception:
                log.exception("bound schema: could not read columns for %s", source.name)
        self._write_schema_entries(self.project(), {binding.id: (binding, columns)})

    def _write_schema_entries(self, project: Project, fresh: dict[str, tuple[Binding, list[Column]]],
                              ) -> None:
        """Re-render `.sage/schema.json` for every recorded Data Source, in Binding order (#33).

        `fresh` carries the entries just read from a store; every other Binding keeps the columns
        already on file. That is what makes a bind cost ONE query rather than one per bound source —
        the others were read when they were bound and nothing about them has moved.

        Bindings no longer recorded fall out by construction: the file is rebuilt from the manifest,
        so an unbound Data Source cannot leave its tables behind for the agent to write queries
        against.
        """
        recorded = [b for b in parse_bindings(project.workspace.read_bindings())
                    if b.kind == KIND_DATA_SOURCE]
        # A bind reads the store BEFORE it records the Binding, because recording is what re-renders
        # what the agent is told and that render reads this file. So a freshly-read source may not be
        # in the manifest yet — appended here, where `_record` is about to append it, or the entry is
        # dropped and the next render asks the store a second time for what was just read.
        recorded += [b for b, _ in fresh.values() if all(b.id != r.id for r in recorded)]
        raw = self._read_json(project.workspace.path / SCHEMA_PATH)
        on_file = parse_schema(raw)
        entries: list[tuple[Binding, list[Column]]] = []
        for b in recorded:
            if b.id in fresh:
                entries.append(fresh[b.id])
                continue
            # A pre-#33 file named one source and no id, and the Binding it described was the first
            # one recorded — so its columns belong to whichever Binding is first here.
            legacy = on_file.get(LEGACY_SOURCE) if b is recorded[0] else None
            entries.append((b, on_file.get(b.id, legacy or [])))
        self._write_generated(project.workspace.path / SCHEMA_PATH, render_schema(entries))

    # ---- Sample rows, only ever because someone asked (#16) ----

    def recorded_tables(self) -> list[dict]:
        """What the recorded schema holds, per bound Data Source, in Binding order (#33).

        Read from `.sage/schema.json` rather than from any store, so asking costs nothing — every
        table here was enumerated when its Scope was bound. Three callers want it and want it
        differently: the sample picker offers one source's tables and needs that source's own choice
        ticked, the rail labels each store's row with what it is showing, and the builder's `@` menu
        offers every table, each labelled with the store it is inside.

        `shared` rides along for that reason — one read of two files answers all three, where a
        request per store would mean one per row on every project open.
        """
        project = self.project()
        columns = parse_schema(self._read_json(project.workspace.path / SCHEMA_PATH))
        shared = self._shared(project)
        return [
            {"id": b.id, "name": b.name, "display_name": b.display_name, "scope": b.scope,
             "tables": list(dict.fromkeys(c.table for c in columns.get(b.id, []))),
             "shared": [x.rows.table for x in shared if x.binding == b.id]}
            for b in parse_bindings(project.workspace.read_bindings()) if b.kind == KIND_DATA_SOURCE
        ]

    def sample_candidates(self) -> dict:
        """The tables the creator could show the agent, and what is already shared.

        The picker's data. Still one Data Source — the first — because sharing rows is a choice made
        per source in the rail, and the picker is opened from a source's own row.
        """
        project = self.project()
        binding = self._data_source_binding(project)
        sources = self.recorded_tables()
        if binding is None:
            return {"bindable": False, "source": "", "tables": [], "shared": [],
                    "sources": sources}
        first = next((s for s in sources if s["id"] == binding.id), None) or {}
        return {
            "bindable": True,
            "scope": binding.scope,
            "source": binding.name,
            # The first store's own answer at the top level, where a caller written before several
            # sources were bindable expects to find it.
            "tables": first.get("tables", []),
            "shared": first.get("shared", []),
            # Every bound source, for the picker, the rail and the builder's `@` menu. Same read —
            # a route per store would mean a request per row on every project open.
            "sources": sources,
        }

    def share_sample_rows(self, source_id: str, tables: list[str],
                          limit: int = 5) -> dict:
        """Show the agent a few real rows from the tables the creator picked (#16).

        Every part of this is the creator's: whether to share at all, and which tables. Sage infers
        none of it.

        Replaces rather than adds. The picker shows what is currently shared, so the list that comes
        back IS the choice, and a table unticked is a table the creator wants the agent to stop
        seeing.

        Read at the moment of sharing, not per turn. Rows go stale, which is why re-sharing exists —
        but re-reading the store on every turn would mean production data crossing to a model
        continuously on the strength of one click.
        """
        project = self.project()
        binding = self._binding_for(project, source_id)
        wanted = [t for t in dict.fromkeys(tables) if t]
        if not wanted:
            return self.clear_sample_rows(source_id)
        source = self._data_source(binding.id)
        fresh = [
            SharedSample(binding.id,
                         self._resources.sample_rows(source, binding.database or "",
                                                     binding.schema or "", table, limit))
            for table in wanted
        ]
        # This source's choice replaces this source's rows and leaves every other store's alone. The
        # picker is opened from one Data Source's row and shows one store's tables, so a save that
        # replaced the whole file would silently un-share the rows chosen from the store next to it.
        kept = [s for s in self._shared(project) if s.binding != binding.id]
        self._ensure_gitignored(project.workspace.path, SAMPLES_PATH)
        self._write_generated(project.workspace.path / SAMPLES_PATH, render_samples(kept + fresh))
        self._write_app_data(project)
        self._rebaseline_turn(project)
        return {"shared": [s.rows.table for s in fresh],
                "rows": sum(len(s.rows.rows) for s in fresh)}

    def clear_sample_rows(self, source_id: str = "") -> dict:
        """Stop showing the agent rows from one Data Source, or from all of them.

        One source by default of the picker, which is opened from a store's own row: "stop showing
        these" there means that store's rows, not the ones chosen next to another store. No id clears
        the file, which is what a caller with no store in mind means.
        """
        project = self.project()
        path = project.workspace.path / SAMPLES_PATH
        kept = [s for s in self._shared(project) if s.binding != source_id] if source_id else []
        if kept:
            self._write_generated(path, render_samples(kept))
        else:
            path.unlink(missing_ok=True)
        self._write_app_data(project)
        self._rebaseline_turn(project)
        return {"shared": [s.rows.table for s in kept], "rows": 0}

    def _data_source_binding(self, project: Project) -> Binding | None:
        recorded = parse_bindings(project.workspace.read_bindings())
        return next((b for b in recorded if b.kind == KIND_DATA_SOURCE), None)

    def _shared(self, project: Project) -> list[SharedSample]:
        """The rows currently shared, with every entry attributed to a Data Source Binding.

        An entry written before #33 names none; it can only have come from the first Data Source,
        which is the one the picker read from when there was no other. Resolved here rather than in
        `parse_samples`, which has no Binding list to resolve it against.
        """
        shared = parse_samples(self._read_json(project.workspace.path / SAMPLES_PATH))
        first = self._data_source_binding(project)
        if first is None:
            return shared
        return [s if s.binding else replace(s, binding=first.id) for s in shared]

    def _shared_samples(self, project: Project) -> list[tuple[str, list[str]]]:
        """The shared tables per store, for the AGENTS.md region.

        Grouped in Binding order rather than in file order, so the section reads in the same order as
        the store sections above it.
        """
        shared = self._shared(project)
        bindings = [b for b in parse_bindings(project.workspace.read_bindings())
                    if b.kind == KIND_DATA_SOURCE]
        groups = [(b.display_name, [s.rows.table for s in shared if s.binding == b.id])
                  for b in bindings]
        return [g for g in groups if g[1]]

    def _binding_for(self, project: Project, source_id: str) -> Binding:
        """The Data Source Binding a request names, or the first one when it names none.

        Falling back keeps a caller that predates several sources working, and raises rather than
        guessing when the id names something this app does not use — sharing rows out of a store the
        app has no Binding for is exactly the thing the creator did not agree to.
        """
        recorded = [b for b in parse_bindings(project.workspace.read_bindings())
                    if b.kind == KIND_DATA_SOURCE]
        if not recorded:
            raise LookupError(brand.text("This app is not recorded as using a {dataSource}."))
        if not source_id:
            return recorded[0]
        found = next((b for b in recorded if b.id == source_id), None)
        if found is None:
            raise LookupError(brand.text("This app is not recorded as using that {dataSource}."))
        return found

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

        An EMPTY id means the paste did not come from a row (#42) — the creator is adding a Model API
        the rail could not list, so the snippet's own URL is the only thing that says which model this
        is. Nothing is relaxed by that. The mismatch check below is between two ids the creator
        supplied, and with only one supplied there is no second one to disagree with; a paste naming
        no model at all is already refused above, because the URL and the id come out of one match and
        a snippet without the URL is not `complete`.
        """
        parsed = parse_snippet(snippet)
        if not parsed.complete:
            return {"ok": False, "error": parsed.missing()}
        model_api_id = model_api_id or (parsed.model_id or "")
        if parsed.model_id and parsed.model_id != model_api_id:
            return {"ok": False, "error": brand.text(
                "That snippet is for a different {modelApi}. Copy the sample request from the "
                "Overview page of the model you are adding."
            )}
        result = verify_credential(parsed.url, parsed.token)
        if not result.ok:
            return {"ok": False, "error": result.message, "detail": result.detail}
        self._credentials(self.project()).put(model_api_id, Credential(parsed.url, parsed.token))
        # The id goes back because the caller may not have had one to send: a paste that did not come
        # from a row learns which Model API it just added only from here, and it has to bind next.
        return {"ok": True, "url": parsed.url, "id": model_api_id}

    def _record(self, new: Binding, catalogue: dict | None = None) -> list[dict]:
        """Write one Binding into the manifest, and re-derive what the app's source says about it.

        `catalogue` is what the door that resolved this Resource already read about it — the fields
        a membership row keeps and a Binding does not carry. Passed in rather than fetched here so
        the join costs no second listing call, and optional because the Chat handoff records a
        Binding for a Resource whose listing row it may no longer be able to fetch.
        """
        def change(entries: list[dict]) -> list[dict]:
            # Re-binding replaces IN PLACE rather than moving to the end. Order decides which Alias
            # the app calls (#7), so appending an already-bound one would repin an app that already
            # works — from a call that was meant to be a no-op.
            current = parse_bindings(entries)
            if any(b.key == new.key for b in current):
                return [(new if b.key == new.key else b).to_dict() for b in current]
            return [b.to_dict() for b in [*current, new]]
        entries = self.project().workspace.update_bindings(change)
        self._join_project_on_bind(new, catalogue or {})
        self._write_app_resources(self.project())
        return self._labelled_bindings(entries)

    def _join_project_on_bind(self, new: Binding, catalogue: dict) -> None:
        """Put the bound Resource in the project's working set, because binding it is using it.

        Membership is a record of use, not a gate (ADR-0018). The bind is the act with a reason
        behind it — a person picked this Resource for this app — so the membership row follows it
        rather than standing in front of it. Every door that binds arrives here, so the join is
        written once: the panel, the rail's tree, the Chat handoff, and anything later that records
        a Binding all pass through `_record`.

        Idempotent: `add_project_resource` keys on id, and a row already in the working set is left
        where it is — not duplicated, not renamed, and not thinned back to what this call knows.
        """
        # The id space every other surface keys a Resource on — `SW.util.bindingId` in the client
        # writes the same string. A Binding records the bare Domino id beside its kind, so the
        # prefix has to be put back or the rail draws a second row for a Resource it already holds.
        rid = f"{new.kind}:{new.id}"
        try:
            self.add_project_resource({
                "id": rid,
                "kind": new.kind,
                "name": new.display_name or new.name,
                "bindingKey": [new.kind, new.id],
                # The gateway alias the model picker calls by. It is `Binding.name` for this one
                # kind, and a row written without it is an option the picker draws blank and
                # cannot select.
                **({"alias": new.name} if new.kind == KIND_LLM_ALIAS else {}),
                # Everything else `add_project_resource` keeps, from the listing the bind already
                # read. A bind has to write the same row the Browse Domino button writes, for the
                # reason the mention join writes it too: the model picker reads `reasoning_efforts`
                # off these rows whenever the Alias listing is unavailable, so a row born without
                # them is an option nobody can select.
                **{k: v for k, v in catalogue.items() if v not in (None, [], "")},
            })
        except (ValueError, OSError):
            # The Binding is the record that matters and it is already on disk. A membership row
            # the disk refused must not fail the bind the creator asked for.
            log.warning("membership: could not record %s in the project", rid, exc_info=True)

    def unbind(self, kind: str, resource_id: str) -> dict:
        """Drop one Binding. Removing a record that is already gone is not an error: the creator
        wanted it gone, and it is.

        Reports the app source that still uses it as `refs`, exactly as detach_file does, so the UI
        can offer the cleanup instead of leaving a dead button to be found later in the preview.
        Read BEFORE the record goes: a Data Source's queries are found THROUGH the record, and
        _write_app_resources rewrites Sage's own files on the way out."""
        current = parse_bindings(self.project().workspace.read_bindings())
        gone = next((b for b in current if b.key == (kind, resource_id)), None)
        refs = self._resource_usage(self.project().workspace, gone) if gone else []
        def change(entries: list[dict]) -> list[dict]:
            return [b.to_dict() for b in parse_bindings(entries) if b.key != (kind, resource_id)]
        entries = self.project().workspace.update_bindings(change)
        self._write_app_resources(self.project())
        return {"bindings": self._labelled_bindings(entries), "refs": refs, "kind": kind,
                "name": gone.name if gone else resource_id}

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
        slot_aliases = {(getattr(self._catalog, slot, "") or "").rsplit("/", 1)[-1] for slot in SLOTS}
        endpoints, errors = self._endpoint_listing(aliases, slot_aliases)
        # Sorted back into SLOTS order across BOTH checks. Each returns its own findings in that
        # order, but concatenating them does not preserve it, and a reader met with `implement`
        # above `sovereign_plan` has to work out that the list is in no order at all.
        found = (list(unresolved_slots(self._catalog, aliases))
                 + list(slots_on_dead_endpoints(self._catalog, aliases, endpoints)))
        problems = [p.to_dict() for p in sorted(found, key=lambda p: SLOTS.index(p.slot))]
        return {
            # Same precedence as `preflight_bindings`: a listing that failed does not unlearn what
            # the other one answered, so real problems outrank "could not check" while `error` still
            # carries what went unchecked.
            "state": "problems" if problems else "unreachable" if errors else "ok",
            "error": " ".join(errors) or None,
            "slots": problems,
        }

    def _slot_listings_now(self) -> tuple[list | None, list | None]:
        """The Aliases this gateway offers and the endpoints behind them, remembered briefly (#125).

        `(None, None)` means the gateway would not answer — the same value a caller must read as
        "we could not check", never as "the model is broken".

        The endpoint listing is fetched for EVERY Alias rather than for the ones a particular turn
        names, unlike `_endpoint_listing`'s callers. One cached pair then answers any turn, whatever
        it routes to; keyed on one turn's slots it would be a cache that misses whenever the mode
        changes, which is most of the time. The skip inside `_endpoint_listing` still applies, so an
        all-vendor gateway pays nothing for the second call.
        """
        import time

        ttl = _TURN_SLOT_TTL_S if self._slot_listings[0] else _TURN_SLOT_UNCHECKED_TTL_S
        if self._slot_listings_at and time.monotonic() - self._slot_listings_at < ttl:
            return self._slot_listings
        answer: tuple[list | None, list | None] = (None, None)
        try:
            aliases = self._resources.list_llm_aliases()
            endpoints, errors = self._endpoint_listing(aliases, {a.name for a in aliases})
            for error in errors:
                # Said out loud, because silence here reads as "no endpoint is stopped". A failed
                # endpoint listing means every hosted slot goes unchecked, and nothing else would
                # record that it did.
                log.warning("turn preflight: could not check the endpoints behind this turn's "
                            "model — %s", error)
            if aliases:
                answer = (aliases, endpoints)
            else:
                # A listing that ARRIVED and offered nothing is not evidence that a slot is wrong.
                # `/v1/models` answering 200 with no usable `data` — a permission-cache blip, a token
                # that momentarily resolves to no grants — reaches here as `[]`, not as an exception,
                # and reading it as "every Alias is missing" would refuse every turn on this gateway
                # with a remedy that cannot work: there is no model left to pick. Same rule as an
                # unreachable gateway, because it is the same amount of knowledge.
                log.warning("turn preflight: the gateway offered no models at all, so the model "
                            "this turn will use went unchecked")
        except ResourceUnavailable as e:
            # Logged, not raised and not reported: a turn goes ahead on an unchecked slot, exactly
            # as it did before this check existed.
            log.warning("turn preflight: could not check the model this turn will use — %s", e)
        self._slot_listings_at = time.monotonic()
        self._slot_listings = answer
        return answer

    def _turn_slot_refusal(self, project: Project, mode: Mode) -> str | None:
        """The sentence that stops this turn before it opens a session, or None to let it run (#125).

        The boot check answers about a moment that has passed and about the DEPLOYMENT catalog, which
        is not necessarily what a turn routes to: a project may have assigned its own model to a slot,
        and the composer may be overriding one. This resolves what THIS turn would actually run on —
        `_effective_catalog` through the shim, plus the standing pick, read through the same
        precedence `llm_router` applies — and asks the gateway about that.

        Every reason to say nothing is a reason to let the turn run. A gateway that will not answer,
        a mode with no gateway behind it, a slot left blank: none of them is evidence that a model is
        broken, and #125's fourth criterion turns on keeping "we could not check" a state rather than
        a refusal.
        """
        if self._gateway_mode != "domino":
            return None
        wanted = turn_slots(project.shim.catalog, mode, project.control.snapshot().picked_model)
        if not wanted:
            return None
        aliases, endpoints = self._slot_listings_now()
        if aliases is None:
            return None
        # Every dead slot this turn would touch, not the first: an Auto turn routes to two, and
        # naming one of them sends the person back to fix the other on the next attempt.
        found = [m for slot, alias, picked in wanted
                 if (m := turn_refusal(slot, alias, aliases, endpoints, picked=picked))]
        for message in found:
            log.error("turn preflight: %s", message)
        return "\n\n".join(found) or None

    def _slot_refusal_events(self, project: Project, mode: Mode,
                             user_text: str | None = None) -> list[dict]:
        """What a turn refused for its model owes the stream, or [] when it is cleared to run.

        Written to the transcript as well as streamed, the way every other pre-turn refusal is
        (`_ask_mode_refusal`, `_reset_offer`, `_incoming_offer`): the composer renders the person's
        prompt optimistically, so a refusal the server never recorded leaves a reload showing neither
        the question nor the answer. `user_text` is the prompt to keep — None on the approve path,
        which has no typed prompt and whose "Approved the plan." bubble belongs to an approval that
        did not happen.

        `model unavailable` is a gate decision on the UI side, so the plan card keeps its Approve
        button — otherwise the remedy this names has no button left to take it.
        """
        message = self._turn_slot_refusal(project, mode)
        if message is None:
            return []
        out = [{"type": "error", "message": message},
               {"type": "done", "ok": False, "decision": "model unavailable"}]
        # The user's own bubble goes to history but not to the stream: the composer already put it
        # on screen, and yielding it again would draw the prompt twice.
        for ev in ([{"type": "user", "text": user_text}] if user_text else []) + out:
            project.workspace.append_history(ev, project.build_conversation)
        return out

    def preflight_bindings(self) -> dict:
        """Check this project's recorded Bindings against the gateway. One listing, at session open.

        Reads the manifest directly rather than through `project()`, so this stays callable from
        inside the attach path without recursing through the memo it is being called from.
        """
        workspace = self._project.workspace if self._project is not None else self._wm.app_workspace(self._project_id)
        recorded = parse_bindings(workspace.read_bindings())
        if not recorded:
            # Nothing to check, so nothing worth a call: the overwhelmingly common case at session
            # open is an app with no Bindings at all, and that must cost nothing.
            return {"state": "ok", "error": None, "bindings": []}
        listings, errors = self._binding_listings({b.kind for b in recorded})
        gone = stale_bindings(recorded, listings)
        # A Model API that has gone is not also reported as having no token: it is the same Binding
        # to remove, and the more useful half of why.
        held = self._held_tokens(workspace, {b.kind for b in recorded})
        tokenless = [b for b in missing_credentials(recorded, held) if b not in gone]
        # An Alias that has gone is not also judged on the endpoint behind it: there is no alias
        # record left to carry an endpoint_url, so this returns nothing for it anyway — but saying so
        # here is cheaper than making a reader work that out from two files.
        aliases = listings.get(KIND_LLM_ALIAS) or []
        endpoints, endpoint_errors = self._endpoint_listing(
            aliases, {b.name for b in recorded if b.kind == KIND_LLM_ALIAS})
        errors += endpoint_errors
        stalled = [(b, m, st) for b, m, st in bindings_on_dead_endpoints(recorded, aliases, endpoints)
                   if b not in gone]
        # The third element is the endpoint's status, or None for the two problems that are not about
        # an endpoint. It exists for the rail's chip: see `bindings_on_dead_endpoints`.
        problems = ([(b, stale_message(b), None) for b in gone]
                    + [(b, credential_message(b), None) for b in tokenless]
                    + stalled)
        return {
            # `problems` outranks `unreachable`, because one listing failing does not unlearn what
            # another one answered. `error` still carries what could not be checked, so a caller is
            # never told that a partial answer was the whole one.
            "state": "problems" if problems else "unreachable" if errors else "ok",
            "error": " ".join(errors) or None,
            "bindings": [{**b.to_dict(), "message": message,
                          **({"status": status} if status else {})}
                         for b, message, status in problems],
        }

    # Which listing is authoritative for which kind, and how to fetch it. An LLM Alias comes off the
    # gateway; a Model API off the Domino API scoped to this project, which is the only scope a
    # non-admin can ask for; a Data Source off the caller's own permission listing, unscoped, because
    # a Data Source is granted to a person rather than to a project.
    def _binding_listings(self, kinds: set[str]) -> tuple[dict[str, list | None], list[str]]:
        """The listings needed to judge these Binding kinds, and what could not be fetched.

        A kind with no Bindings costs no call — an app that uses one Alias and nothing else makes the
        same single request it made before this. A listing that fails maps to None rather than being
        omitted, which is the same answer to `stale_bindings` and a different one to the caller: the
        `error` it produces is how "we could not check" stays distinguishable from "nothing is wrong".
        """
        fetchers = (
            (KIND_LLM_ALIAS, self._resources.list_llm_aliases),
            (KIND_MODEL_API, lambda: self._resources.list_model_apis(self._domino_project_id)),
            (KIND_DATA_SOURCE, self._resources.list_data_sources),
        )
        listings: dict[str, list | None] = {}
        errors: list[str] = []
        for kind, fetch in fetchers:
            if kind not in kinds:
                continue
            try:
                listings[kind] = fetch()
            except ResourceUnavailable as e:
                listings[kind] = None
                errors.append(str(e))
        return listings, errors

    def _endpoint_listing(self, aliases: list, wanted: set[str]) -> tuple[list | None, list[str]]:
        """Hosted GenAI Endpoints, but only when something being checked actually points at one.

        The skip is the point, and `wanted` is what makes it real. Keying it on "does this gateway
        offer any hosted Alias" looked equivalent and is not: cloud-dogfood offers two of nine, so
        that test is always true there and every session open paid a ~1.5s round trip for an answer
        that could not apply to it. Keyed on the Aliases actually named by the slots or the Bindings
        in hand, an app using only vendor models pays nothing — the same rule `_binding_listings`
        applies per kind and `_held_tokens` applies to the token store.

        None means "not checked", which is what `endpoint_status` reads as "learned nothing". That is
        the same value a failed listing produces, deliberately: neither is evidence that a model is
        broken, and only the returned `error` tells the two apart for the caller.
        """
        if not any(getattr(a, "endpoint_url", None) for a in aliases if a.name in wanted):
            return None, []
        try:
            return self._resources.list_hosted_endpoints(), []
        except ResourceUnavailable as e:
            return None, [str(e)]

    @staticmethod
    def _held_tokens(workspace: Workspace, kinds: set[str]) -> set[str] | None:
        """The Model APIs Sage still holds an access token for, or None when it did not look.

        Local, so it costs no call — but it is still skipped for an app with no Model API Binding,
        because a read that can answer nothing is a read worth not making.
        """
        if KIND_MODEL_API not in kinds:
            return None
        try:
            return set(CredentialStore(workspace.path).ids())
        except Exception:
            log.exception("preflight: could not read the Model API token store")
            return None

    def _find_asset(self, dataset_id: str) -> Asset:
        asset = next((a for a in self._assets.list_datasets(self._domino_project_id)
                      if a.id == _bare_kind_id(dataset_id, "dataset")), None)
        if asset is None:
            raise LookupError(dataset_id)
        return asset

    def list_asset_files(self, dataset_id: str) -> list[dict]:
        """Files in a Dataset, each with its size and whether it's already attached. Size is 0 for
        a Dataset with no mount here — the API listing names files without measuring them."""
        asset = self._find_asset(dataset_id)
        attached = {e["path"] for e in self.project(start_preview=False).attached}
        out = []
        for f in self._assets.list_files(asset):
            dest = _attach_dest(asset.name, f.path)
            out.append({"path": f.path, "size": f.size, "dest": dest, "attached": dest in attached})
        return out

    def _download_attachment(self, asset: Asset, file_path: str, dest: Path, total: int,
                             prune_root: Path) -> int:
        """Fetch one file from a Dataset this container has no mount for. Returns its size.

        The API listing carries no sizes, so the cap can only be enforced against real bytes: the
        download lands beside its destination and is moved into place once it fits, and is deleted
        when it does not. A partial or over-cap download never becomes an attachment — and never
        leaves the directories it needed behind either, so a refused attach is invisible in the
        tree the preview serves.
        """
        tmp = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        placed = False
        try:
            size = self._assets.download_file(asset, file_path, tmp)
            if total + size > self._attach_max_bytes:
                raise AttachTooLarge(self._attach_max_bytes, total, size)
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            os.replace(tmp, dest)
            placed = True
        finally:
            if tmp.is_symlink() or tmp.exists():
                tmp.unlink()          # before the prune, or the leftover keeps the dir alive
            if not placed:
                _prune_empty_dirs(dest.parent, prune_root)
        return size

    def attach_file(self, dataset_id: str, file_path: str, *,
                    local_source: Path | None = None) -> dict:
        """Put one dataset file into the workspace under public/data/ so OpenCode can @mention it
        and the (static) preview/published app can fetch it.

        A mounted Dataset is symlinked — no byte copy, and the link points at the live Domino mount.
        A Dataset this container has no mount for is downloaded through the Domino data library,
        which is how a Dataset shared from another project becomes attachable at all: mounts are
        fixed when the execution starts, and waiting for a restart was the old answer.
        Enforces a configurable total-size cap across all attached files.

        `local_source` names bytes already fetched into this workspace — the scratch copy a Chat
        turn read (see fetch_dataset_file_for_chat) — and links the app's data path at them instead
        of asking Domino for the same file twice. The cap does not apply to it: the link adds no
        disk, the bytes passed a cap when they were fetched, and refusing here would drop a file out
        of a handoff the person already confirmed.
        """
        project = self.project()
        asset = self._find_asset(dataset_id)
        rel = _attach_dest(asset.name, file_path)  # workspace-relative posix path
        already = next((e for e in project.attached if e["path"] == rel), None)
        if already is None:
            total = sum(e["size"] for e in project.attached)
            dest = _safe_join(project.workspace.path, rel)
            if local_source is not None:
                size = local_source.stat().st_size
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                dest.symlink_to(local_source)
            elif not asset.mount_path:
                size = self._download_attachment(
                    asset, file_path, dest, total,
                    project.workspace.path / "public" / "data",
                )
            else:
                src = _safe_join(Path(asset.mount_path), file_path)
                if not src.is_file():
                    raise FileNotFoundError(file_path)
                size = src.stat().st_size
                if total + size > self._attach_max_bytes:
                    raise AttachTooLarge(self._attach_max_bytes, total, size)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                dest.symlink_to(src)
            # source="dataset": the Dataset's own bytes are never Sage's to delete. Detach removes
            # only what sits in the workspace — the symlink, or the copy downloaded in its place.
            project.attached.append(
                {"dataset_id": dataset_id, "dataset": asset.name, "file": file_path, "path": rel,
                 "size": size, "source": "dataset", "dataset_rel_path": file_path}
            )
            self._ensure_gitignored(project.workspace.path, "public/data/")
            self._write_agents_data_block(project)
            project.workspace.write_attachments(project.attached)
        size = next((e["size"] for e in project.attached if e["path"] == rel), 0)
        entry = next((e for e in project.attached if e["path"] == rel), {})
        return {"attached": file_path, "dataset": asset.name, "path": rel, "size": size,
                "descriptor": entry.get("descriptor"),
                "status": project.status()}

    def fetch_dataset_file_for_chat(self, dataset_id: str, file_path: str) -> dict:
        """One Dataset file, put where a Chat turn can read it and nowhere else.

        attach_file is the APP's route: it lands the bytes in `public/data/` because a published app
        is a static build that fetches them over HTTP, and it records them in the committed manifest
        so a publish can rehydrate them. A question has no app. Adding a chip to a Thread went down
        that route anyway, so asking what was in a file wrote it into the app's asset tree and into
        every later publish. This is the same fetch without either consequence.

        Mounted stays the fast path — a symlink, no copy. A Dataset with no mount here is
        downloaded, because `download_file` is the only content API a Dataset has: there is no
        server-side read to push a question down to, the way a Data Source takes SQL.

        Idempotent: a chip re-added, or two chips naming the same file, fetch once.
        """
        project = self._chat_project()
        asset = self._find_asset(dataset_id)
        rel = _chat_data_dest(asset.name, file_path)
        dest = _safe_join(project.record.path, rel)
        self._ensure_gitignored(project.record.path, _SCRATCH_PREFIX)
        if dest.is_symlink() or dest.is_file():
            return {"path": rel, "dataset": asset.name, "size": dest.stat().st_size}
        if asset.mount_path:
            src = _safe_join(Path(asset.mount_path), file_path)
            if not src.is_file():
                raise FileNotFoundError(file_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(src)
            size = src.stat().st_size
        else:
            root = _safe_join(project.record.path, _CHAT_DATA_PREFIX.rstrip("/"))
            size = self._download_attachment(asset, file_path, dest, _copied_bytes(root), root)
        return {"path": rel, "dataset": asset.name, "size": size}

    def detach_file(self, path: str) -> dict:
        """Remove an attached file's symlink (keyed by its workspace path, so rehydrated entries
        with no dataset_id detach too) and forget it. Also deletes any standalone COPY of the file the
        agent leaked into the app tree (same basename under src/ etc.): once the entry leaves
        project.attached the commit backstop (_leaked_copy_paths) stops covering it, so a leaked copy
        would otherwise get staged into the next save — pushing the bytes into git.
        Inlined-into-code copies are left in place (deleting the source file would nuke app logic) and
        reported, alongside code that fetches the served path, as `refs` so the UI can warn and offer an
        agent cleanup. Keeps the dataset bytes."""
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

    def upload_file(self, filename: str, data: bytes, dataset_id: str | None = None) -> dict:
        """Write an uploaded file into a writable dataset mount (persisted, and outside git), then
        attach it under public/data/ like any dataset file.

        - No `dataset_id` -> the shared default project dataset, under `uploads/`.
        - A picked `dataset_id` -> that dataset, also under `uploads/`.

        The committed manifest lets the published app rebuild public/data/ from the mount. Enforces
        the same total-size cap as attach."""
        project = self.project()
        if not filename or not filename.strip():
            raise ValueError("filename required")
        name = _slug(filename)
        target = self._resolve_upload_target(dataset_id)
        if target is None or not target.mount_path:
            raise UploadUnavailable()
        size = len(data)
        total = sum(e["size"] for e in project.attached)
        if total + size > self._attach_max_bytes:
            raise AttachTooLarge(self._attach_max_bytes, total, size)
        rel_in_dataset = PurePosix("uploads", name).as_posix()
        dest_bytes = _safe_join(Path(target.mount_path), rel_in_dataset)
        rel = _attach_dest(target.name, rel_in_dataset)
        link = _safe_join(project.workspace.path, rel)   # resolved BEFORE any write, so a rejected
        dest_bytes.parent.mkdir(parents=True, exist_ok=True)  # path fails without stranding bytes
        # The bytes land on the dataset mount, which is OUTSIDE git and outside the workspace, while
        # everything that RECORDS them (symlink, manifest, AGENTS.md) is inside it. A failure in
        # between therefore strands data on a shared mount with nothing pointing at it — invisible
        # to detach/delete. So the write is undone on any failure. `created` guards the one case we
        # must not undo: overwriting a same-named re-upload already destroyed the old bytes, and
        # deleting the file would compound that.
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
                 "size": size, "source": "upload",
                 "dataset_rel_path": rel_in_dataset}
            )
            self._ensure_gitignored(project.workspace.path, "public/data/")
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
        # descriptor rides the response so the Data panel can flag an image the agent can't see
        # immediately, instead of only after the next page load refetches the attachment list.
        entry = next((e for e in project.attached if e["path"] == rel), {})
        return {"uploaded": name, "dataset": target.name, "dataset_id": target.id, "path": rel,
                "size": size,
                "descriptor": entry.get("descriptor"), "status": project.status()}

    def upload_scratch(self, filename: str, data: bytes) -> dict:
        """Write a Chat-local file into gitignored `.sage/scratch/`. No Dataset required."""
        project = self._chat_project()
        if not filename or not filename.strip():
            raise ValueError("filename required")
        name = _slug(filename)
        rel = f"{_SCRATCH_PREFIX}{name}"
        dest = _safe_join(project.record.path, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self._ensure_gitignored(project.record.path, _SCRATCH_PREFIX)
        return {"uploaded": name, "path": rel, "size": len(data), "source": "scratch",
                "status": project.status()}

    def promote_scratch_to_dataset(self, path: str, dataset_id: str) -> dict:
        """Copy a scratch file onto a writable Dataset, then drop the scratch copy."""
        rel = str(path or "").replace("\\", "/")
        rel = rel.removeprefix("./")
        if not rel.startswith(_SCRATCH_PREFIX):
            raise ValueError("not a scratch file")
        src = _safe_join(self._chat_project().record.path, rel)
        if not src.is_file():
            raise FileNotFoundError(path)
        data = src.read_bytes()
        result = self.upload_file(src.name, data, dataset_id)
        try:
            src.unlink()
        except OSError:
            pass
        result["scratch"] = rel
        return result

    def _resolve_upload_target(self, dataset_id: str | None) -> Asset | None:
        """The dataset an upload writes into: a picked one if it is mounted and writable, else the
        shared default project dataset."""
        if dataset_id:
            try:
                target = self._find_asset(dataset_id)
            except LookupError:
                return None
            if not target.mount_path or not os.access(target.mount_path, os.W_OK):
                return None
            return target
        return self._default_dataset()

    def default_dataset_id(self) -> str | None:
        """Id of the dataset uploads land in when the user doesn't pick one — lets the UI label and
        pre-select it by its real name instead of a generic "Project data" option."""
        target = self._default_dataset()
        return target.id if target else None

    def _default_dataset(self) -> Asset | None:
        """The shared default project dataset to write uploads into: a writable, mounted dataset,
        preferring the project's own (named after / owned by the project, mounted under /mnt/data),
        falling back to the first writable dataset (covers the local fake harness)."""
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
        return writable[0] if writable else None

    def delete_file(self, path: str) -> dict:
        """Delete an UPLOADED file: remove its workspace symlink AND its bytes from the dataset mount,
        then forget it. Bytes are deleted only for Sage-managed uploads — files under a dataset's
        `uploads/` folder, which Sage always created (whether attached as source=='upload' or later
        re-attached from the dataset browser as source=='dataset'). A genuine pre-existing dataset
        file (not under uploads/) is detach-only here; its bytes are the user's data and never
        removed."""
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

    def _scan_app_sources(self, workspace: Workspace) -> list[tuple[str, str | None]]:
        """(workspace-relative posix path, text) for each file under the app tree — skips
        dependencies, build output, git, and public/ (the attached-data symlinks live there). Text is
        the file's contents for code files (see _SCAN_EXTS) and None otherwise, so a copied data file
        is still listed (matched by basename) without reading megabytes of CSV.

        Takes the Workspace rather than the attached Project because a Built App is what it reads,
        and the guard on Resource removal has to read apps that are not the attached one."""
        root = workspace.path
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
                    sources: list[tuple[str, str | None]] | None = None,
                    app: Workspace | None = None) -> dict:
        """How the app's source uses an attached file, so delete can refuse to orphan code:
          refs   — source files that fetch it by its served path/name (the intended runtime dependency)
          copies — source files that ARE a copy of the data: same basename under the app tree, or its
                   bytes inlined. This is the leak we forbid (public/data/ is gitignored on purpose),
                   and it's why deleting the attachment leaves the dashboard still working.

        `app` names a Built App other than the one on screen. Detach never passes it — its answer is
        about the app the person is deleting from — and the commit backstop always does, because the
        commit it guards covers every app in the Project (#81).
        """
        app = app or project.workspace
        if sources is None:
            sources = self._scan_app_sources(app)
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
                raw = self._attachment_bytes(app, entry)
            if raw is not None and _is_inlined_copy(raw, text):
                copies.append(rel)                      # data bytes inlined into source (full or sample)
            elif served in text or name in text:
                refs.append(rel)
        return {"refs": refs, "copies": copies}

    def _resource_usage(self, workspace: Workspace, binding: Binding,
                        sources: list[tuple[str, str | None]] | None = None) -> list[str]:
        """App source that still uses a Binding, so unbind can offer to clean up after itself.

        The affordance an attached file already has, for the other half of the composer row.
        Removing the record is not removing the code: an app whose Summarise button calls an Alias
        the app no longer has keeps the button, and the creator finds out in the preview with
        nothing on screen saying which removal did it.

        What identifies a Binding in code differs by kind, which is why this cannot be one scan. An
        Alias and a Model API are named directly — `askModel(msgs, { alias: "mimo-v2.5" })` — so the
        name is the token. A Data Source is never named in the app at all: the app calls queries by
        name and the SQL lives in `.sage/queries.json`, so the tokens are the names of the queries
        recorded against THIS Data Source, and the catalog itself is reported alongside them.
        """
        if sources is None:
            sources = self._scan_app_sources(workspace)
        if binding.kind == KIND_DATA_SOURCE:
            names = self._query_names_for(workspace, binding.id)
            # The catalog is a ref in its own right: its statements now run against a store this app
            # no longer records, and the agent owns that file, so the cleanup can actually fix it.
            catalog = [".sage/queries.json"] if names else []
        else:
            names, catalog = [binding.name], []
        tokens = [t for t in names if t]
        if not tokens:
            return []
        owned = workspace.helpers.owned
        refs = [rel for rel, text in sources
                if text is not None and rel not in owned
                and any(t in text for t in tokens)]
        return catalog + sorted(set(refs))

    def _record_resource_usage(self) -> None:
        """Write which of the app's Bindings its own source uses, for the header row to read (#93).

        End of turn, never on render. `_scan_app_sources` walks the whole app tree and reads every
        code file into memory, and the row it feeds redraws on every app switch — so the row reads
        this answer off the disk instead, on `publish_check`'s discipline: local, pure, no network
        (ADR-0010). Unbind keeps its live scan; that is one deliberate act and can afford one.

        The turn's app, not the one on screen, and with no #77 skip like `_recheck_app_data`'s
        beside it: this writes into the tree it scanned and reads that same tree's Bindings, so a
        switch mid-build leaves both halves describing one app. The app switched TO keeps whatever
        its own last turn wrote, which is the answer for it.

        `_resource_usage` is the scanner — an LLM Alias by its name in the source, a Data Source by
        the names of the queries recorded against it. NOT `_data_usage`, which scans for uses of an
        attached FILE. Its answer counts as used whenever it is non-empty, including the
        `.sage/queries.json` entry it reports for a Data Source that has queries written against it
        but no component calling them yet: that errs towards "used", which is the quiet direction
        for a label that must never gate anything.

        Staleness errs true. A Binding bound since this last ran is absent from the list and reads
        as unused, which it is — nothing has been written against it yet.

        Best-effort, like the two cleanups it stands beside: this must never fail a build that
        otherwise worked.
        """
        try:
            app = self.project().app_for_turn()
            bindings = parse_bindings(app.read_bindings())
            # No Binding, no walk — it is the expensive half and it could label nothing. The empty
            # answer is still WRITTEN, because "checked, and nothing is used" is what makes the
            # first Binding bound after this turn read as unused rather than as unknown.
            sources = self._scan_app_sources(app) if bindings else []
            self._ensure_gitignored(app.path, _USAGE_PATH)
            app.write_resource_usage(
                [f"{b.kind}:{b.id}" for b in bindings if self._resource_usage(app, b, sources)])
        except Exception:
            log.exception("bindings: could not record what the app's source uses")

    def _labelled_bindings(self, entries: list[dict]) -> list[dict]:
        """Manifest entries plus the advisory `used` label the header row draws (#93).

        `used` is None — not False — when no build turn has left an answer for this app, so the row
        can draw no label at all rather than call every Binding unused. The label is added here and
        never in `Binding.to_dict`, which is the manifest entry as well as the HTTP row: a derived
        answer written into `.sage/bindings.json` would outlive the scan that produced it.

        Every route that hands the list back goes through this — bind and unbind as much as the
        plain read — because a list arriving with no labels would blank the ones on screen until the
        next refresh put them back.
        """
        used = self.project().workspace.read_resource_usage()
        return [{**e, "used": None if used is None
                 else f"{e.get('kind')}:{e.get('id')}" in used} for e in entries]

    def _query_names_for(self, workspace: Workspace, resource_id: str) -> list[str]:
        """Names of the queries in `.sage/queries.json` recorded against one Data Source.

        Best-effort. The agent writes this file, and a catalog that will not parse already has its
        own check (_recheck_app_data); here it only means we cannot name the queries, and an unbind
        that warns about nothing is a better outcome than one that fails."""
        try:
            catalog = json.loads((workspace.path / ".sage" / "queries.json")
                                 .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(catalog, list):
            return []
        return [q["name"] for q in catalog
                if isinstance(q, dict) and q.get("binding") == resource_id
                and isinstance(q.get("name"), str) and q["name"]]

    def _copies_in_app(self, project: Project, app: Workspace,
                     attached: list[dict]) -> list[tuple[str, list[str]]]:
        """(attachment name, [source files that copy it]) for one Built App. One source scan for all
        of its files, and none at all for an app with nothing attached."""
        if not attached:
            return []
        sources = self._scan_app_sources(app)
        out: list[tuple[str, list[str]]] = []
        for e in attached:
            copies = self._data_usage(project, e, sources, app)["copies"]
            if copies:
                out.append((PurePosix(e["path"]).name, copies))
        return out

    def _detect_leaks(self, project: Project) -> list[tuple[str, list[str]]]:
        """(attachment name, [source files that copy it]) for the app a turn is BUILDING.

        What the build loop nudges the agent about, so it is scoped to the tree the agent just
        wrote — not the whole Project, whose idle apps hold copies this agent did not make and
        cannot be asked to move. `_leaked_copy_paths` is the one that has to cover them.

        The app being built, not the one on screen: after a switch mid-build (#77) the on-screen app
        is neither the tree the agent copied into nor the list it copied from — so asking it would
        report no leak."""
        return self._copies_in_app(project, project.app_for_turn(), project.attachments_for_turn())

    def _detect_raw_gateway_calls(self, project: Project) -> list[tuple[str, list[str]]]:
        """(source file, [Alias names it asks for that this app does not declare]) for the app a
        turn is BUILDING — the source that calls the LLM Gateway around `askModel` (#94).

        The I/O half of `gateway_bypass`, which is pure: this reads the tree and the manifest and
        hands both in. `_scan_app_sources`'s existing walk, not a second one — the leak scan above
        does its own because it needs the attachment bytes as well, and this needs nothing the walk
        does not already return.

        The app being built rather than the one on screen, for `_detect_leaks`'s reason: after a
        switch mid-build (#77) the on-screen app is not the tree the agent wrote into, and its
        manifest is not the record the flagged call should be judged against.
        """
        app = project.app_for_turn()
        declared = [b.name for b in bound_aliases(parse_bindings(app.read_bindings()))]
        return raw_gateway_calls(self._scan_app_sources(app), declared,
                                 self._browser_gateway_base, app.helpers.owned)

    def _attached_per_app(self, project: Project) -> list[tuple[Workspace, list[dict]]]:
        """Every Built App on the volume, paired with the attachment list to judge it by.

        The manifest on disk answers for an app nobody is holding. The two apps that have a live
        list in memory are read from memory instead: the app on screen, and the app a turn pinned
        when the person switched away from it (#77)."""
        live = {project.workspace.app_id: project.attached}
        if project.turn_app is not None:
            live[project.turn_app.app_id] = project.attachments_for_turn()
        out: list[tuple[Workspace, list[dict]]] = []
        for app_id in self._wm.app_ids():
            app = self._wm.app_workspace(project.id, app_id)
            out.append((app, live[app_id] if app_id in live else app.read_attachments()))
        return out

    def _leaked_copy_paths(self, project: Project) -> list[str]:
        """Flat list of the source files that are copies of attached data — passed to
        commit_all(exclude=...) so the bytes are never staged into a commit.

        Every Built App in the Project, because the commit is `git add -A` at the Project root and
        stages every one of them (#81). An exclude list drawn from the app being built guards the
        narrower thing: a copy sitting in an idle app rides out on a commit driven from another.

        Written from the repo root, because that is where git runs: the scan names files the way
        each app does, and an exclude git cannot resolve excludes nothing."""
        return [project.repo_rel(f, app)
                for app, attached in self._attached_per_app(project)
                for _, files in self._copies_in_app(project, app, attached)
                for f in files]

    def _attachment_bytes(self, app: Workspace, entry: dict) -> bytes | None:
        """Read an attached file's bytes (follows the symlink to the dataset mount). None if absent.

        The app is named rather than defaulted: the symlink lives under the app the file is attached
        to, and the path is the same in every app while pointing at a file in only one."""
        try:
            p = app.path / entry["path"]
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
        recorded = parse_bindings(project.workspace.read_bindings())
        aliases = bound_aliases(recorded)
        if aliases:
            self._wm.ensure_llm_helper()
        # AFTER the helper: an app seeded before #7 has no helper at all, so what it is called is
        # only settled once one has landed.
        names = project.workspace.helpers
        self._write_generated(project.workspace.path / names.llm_config_path,
                              render_config(aliases, self._browser_gateway_base,
                                            self._cost_project_label, names))
        # The stores go in beside the Aliases only to decide the closing paragraph (#35): what the
        # agent is told about where the app's data goes is a fact about the JOIN, and the join is
        # readable off this one manifest.
        self._splice_agents(project, self._MODEL_BEGIN, self._MODEL_END,
                            agents_block(aliases, data_source_bindings(recorded), names))

    def _write_app_model_api(self, project: Project) -> None:
        """Pin the app's Model API into its own source, and tell the agent it is there (#9).

        The Model API twin of _write_app_model, and the same contract: reads the manifest back rather
        than taking a list, writes only on change, helper before config.

        The credential is read here rather than passed in for that same reason — the file on disk is
        a function of what is on disk. A Binding whose credential has gone renders as no Model API,
        which is what `render_config` documents and what bind refuses to create in the first place.
        """
        apis = bound_model_apis(parse_bindings(project.workspace.read_bindings()))
        store = self._credentials(project)
        credentials = {a.id: c for a in apis if (c := store.get(a.id)) is not None}
        if apis:
            self._wm.ensure_model_api_helper()
        names = project.workspace.helpers
        self._write_generated(project.workspace.path / names.model_api_config_path,
                              render_model_api_config(apis, credentials, names))
        self._splice_agents(project, self._MODEL_API_BEGIN, self._MODEL_API_END,
                            model_api_agents_block(apis, credentials, names))

    def _reconcile_bound_schema(self, project: Project, bindings: list[Binding]) -> None:
        """Give every recorded Data Source an entry read at the Scope it currently records (#33).

        Three things move the file out of step without touching it: a Binding removed (its tables
        must go), an app upgraded from the pre-#33 shape (one source, no id), and — the one that
        costs — a Binding whose entry was never read, which is how a source bound while the store
        was unreachable gets a second chance.

        Costs a query only for a source that has no entry at its recorded Scope. An entry that came
        back EMPTY still counts as read, so a store that will not answer is asked once at bind time
        rather than again at the end of every turn.
        """
        raw = self._read_json(project.workspace.path / SCHEMA_PATH)
        legacy = LEGACY_SOURCE in parse_schema(raw)
        missing = [b for b in bindings if recorded_scope(raw, b.id) != b.scope]
        # The first Binding of a pre-#33 file already has its columns; moving them onto its id is
        # what `_write_schema_entries` does, and it costs nothing.
        if legacy and bindings and missing and missing[0] is bindings[0]:
            missing = missing[1:]
        if not missing and not legacy and len(parse_schema(raw)) == len(bindings):
            return
        fresh: dict[str, tuple[Binding, list[Column]]] = {}
        for b in missing:
            try:
                self._read_columns_into(fresh, b)
            except (LookupError, ResourceUnavailable) as e:
                log.info("bound schema: could not read %s — %s", b.name, e)
                fresh[b.id] = (b, [])
        self._write_schema_entries(project, fresh)

    def _read_columns_into(self, fresh: dict, binding: Binding) -> None:
        """One source's columns, straight into the map `_write_schema_entries` takes."""
        source = self._data_source(binding.id)
        columns: list[Column] = []
        if binding.schema:
            columns = self._resources.list_columns(
                source, binding.database or "", binding.schema, binding.table or "")
        fresh[binding.id] = (binding, columns)

    _DATA_BEGIN = "<!-- sage:app-data:begin -->"
    _DATA_END = "<!-- sage:app-data:end -->"

    def _write_app_data(self, project: Project) -> None:
        """Tell the agent what the app's bound tables hold, and how to ask for them (#15).

        Reads the schema back from `.sage/schema.json` rather than taking it as an argument, for the
        same reason the two writers above re-read the manifest: what the agent is told has to be a
        function of what is on disk. It is also what makes this callable at the end of every turn,
        where re-reading the store would mean a warehouse round trip per turn.

        The two things this cannot answer for itself — whether the Scope travels as configuration,
        and which queries the app will refuse — come from the Built App's own `serve.py`, so Sage
        cannot promise something the published app then rejects.
        """
        bindings = [b for b in parse_bindings(project.workspace.read_bindings())
                    if b.kind == KIND_DATA_SOURCE]
        schema_file = project.workspace.path / SCHEMA_PATH
        if not bindings:
            # An unbound Data Source leaves no columns behind. A schema describing a store this app no
            # longer records would go on telling the agent to write queries against it.
            schema_file.unlink(missing_ok=True)
        else:
            self._wm.ensure_query_helper()
            self._reconcile_bound_schema(project, bindings)
        template = self._wm.template
        module = serve_module(template)
        columns = parse_schema(self._read_json(schema_file))
        block = data_agents_block(
            [BoundSource(b, columns.get(b.id, []), stranded_levels(template, b)) for b in bindings],
            catalog_problems(template, project.workspace.path),
            getattr(module, "_DEFAULT_MAX_ROWS", 5000),
            samples=self._shared_samples(project),
            names=project.workspace.helpers,
            # Only asked when nothing is bound, which is the only case it changes.
            reaching=not bindings and self._reaches_for_a_store(project, module),
        )
        self._splice_agents(project, self._DATA_BEGIN, self._DATA_END, block)

    @staticmethod
    def _reaches_for_a_store(project: Project, module: object) -> bool:
        """Whether this app reads a Data Source, judged from the app rather than from its Bindings.

        The two are supposed to agree. When they do not — code that queries, nothing bound — the app
        is broken in a way neither manifest records, and `agents_block` has to say so instead of
        going quiet (see its `reaching`).

        Two signals, because the failure arrives as either. The catalog is the declaration and the
        import is the call, and an agent that wrote one without the other has still decided this app
        reads a store. The `src/` walk skips the helpers Sage owns: `runQuery` is DEFINED in one of
        them, so counting it would make every app look like it were reaching.

        Cheap by construction — a stat and a walk of `src/`, once at the end of a build turn, against
        a tree of tens of files. Errors read as "not reaching": this decides what to say, never what
        to allow, and an unreadable file is not grounds to start shouting at an app that is fine.
        """
        root = project.workspace.path
        catalog = getattr(module, "_QUERIES_REL", ".sage/queries.json")
        if (root / catalog).is_file():
            return True
        owned = project.workspace.helpers.owned
        try:
            for path in (root / "src").rglob("*.ts*"):
                # Short-circuits left to right, so a helper Sage owns is never read at all.
                if (path.is_file() and str(path.relative_to(root)) not in owned
                        and "runQuery" in path.read_text(errors="ignore")):
                    return True
        except OSError:
            return False
        return False

    def _write_app_resources(self, project: Project) -> None:
        """Every pinned-Resource writer, then one baseline move for the set.

        One entry point because a Binding change can move any of the pins, and because rebaselining
        once per change keeps a mid-build bind from being counted as several separate writes by the
        agent.
        """
        self._write_app_model(project)
        self._write_app_model_api(project)
        self._write_app_data(project)
        self._rebaseline_turn(project)

    @staticmethod
    def _read_json(path: Path) -> object:
        """A manifest's contents, or None when it is absent or unreadable. Unreadable reads as absent
        on purpose: a stray comma in a generated file should cost the agent its column names, not the
        whole turn."""
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

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
        """The user's standing guidance for this Project, or "" if they have written none.

        Read from the Project's record rather than parsed back out of the app's AGENTS.md. The
        block in AGENTS.md is a RENDERING of this, and it is gone every time the app is re-seeded
        from the template (ADR-0008) — a Project with no app yet has instructions and no file to
        read them out of.
        """
        return project.record.read_instructions()

    def write_instructions(self, project: Project, content: str) -> None:
        """Record the user's project instructions, and render them where the agent reads them."""
        project.record.write_instructions(content)
        self._splice_instructions(project)

    def _voice_agents_md(self, project: Project) -> None:
        """Resolve the template's brand tokens in a freshly seeded AGENTS.md (#114).

        The Built App's repo is a surface — a partner's own customer can read it (ADR-0014) — and
        AGENTS.md is generated per Project, so it re-brands like every other prompt. The template
        ships the tokens; this is where they are read.

        Called only where the file has just come back from the template, and always before the
        managed blocks go in. Those carry a Resource name the creator chose and the user's own
        project instructions, and text Sage did not write is never rewritten — so this must not
        become a pass over the whole file on every attach.

        Nothing is written when nothing resolved: an app seeded before this change carries no
        tokens, and a rewrite with identical content would show up as a dirty file in the turn's
        tree comparison and in the user's git history. A `{` the template uses for something else
        (`basename={appBase}`) is not a pack key, so the helper leaves it exactly as written.
        """
        agents = project.workspace.path / "AGENTS.md"
        if not agents.exists():
            return
        with self._agents_lock:   # same file as the two managed regions below
            body = agents.read_text()
            voiced = brand.text(body)
            if voiced != body:
                agents.write_text(voiced)

    def _splice_instructions(self, project: Project) -> None:
        """Render the Project's instructions into the app's AGENTS.md as a managed block, preserving
        the template body and the attached-data block. No instructions removes the block.

        Runs whenever an app is seeded or reset as well as on an edit, because a freshly seeded
        AGENTS.md is the template's and carries no block: without this, the app a handoff creates
        would silently ignore guidance the person wrote before it existed.
        """
        agents = project.workspace.path / "AGENTS.md"
        if not agents.exists():
            return  # no app yet, or one with no AGENTS.md — nothing to render into
        content = project.record.read_instructions()
        if content:
            block = (f"{self._INSTR_BEGIN}\n{self._INSTR_HEAD}\n\n{self._INSTR_FRAME}\n\n"
                     f"{content}\n{self._INSTR_END}")
        else:
            block = ""
        with self._agents_lock:  # serialize with _write_agents_data_block — same file, distinct regions
            before = agents.read_text()
            existing = before
            b, e = existing.find(self._INSTR_BEGIN), existing.find(self._INSTR_END)
            if b != -1 and e != -1:
                existing = existing[:b] + block + existing[e + len(self._INSTR_END):]
            elif block:
                d = existing.find(self._AGENTS_BEGIN)
                if d != -1:  # keep file order: template body -> instructions -> attached-data
                    existing = existing[:d].rstrip() + "\n\n" + block + "\n\n" + existing[d:].lstrip()
                else:
                    existing = (existing.rstrip() + "\n\n" + block + "\n") if existing.strip() else block + "\n"
            updated = existing.strip("\n") + "\n" if existing.strip() else ""
            if updated != before:  # this runs on every attach; an unchanged render must not churn
                agents.write_text(updated)

    @staticmethod
    def _ensure_gitignored(root: Path, line: str) -> None:
        """Keep one path out of git, in the .gitignore of the directory that owns it. `root` is the
        app for what the app owns and the Project for what the Project owns (Chat's scratch): git
        reads a nested .gitignore, and a rule in the app's would not cover a tree above it."""
        ensure_ignore_line(root / ".gitignore", line)

    def _refresh_history_archive(self, project: Project) -> None:
        """Rebuild `.sage/history.md` for this turn, and keep it out of git while doing it (#65).

        The archive is rendered whole from the log beside it every turn, so two Sage Builders in one
        Project conflicted on it every turn over data either one could rebuild. Ignoring it is only
        half the job: the agent finds this file by grepping, OpenCode's grep is ripgrep, and ripgrep
        honours `.gitignore` — a plain ignore rule would make a project-wide grep silently return
        nothing. `.ignore` is read by ripgrep ahead of `.gitignore` and never read by git, so the
        negation there leaves the archive greppable and still uncommitted. The template ships both
        rules; these two calls are what fixes a project seeded before they existed."""
        from ..workspace import git

        ws = project.app_for_turn()
        rel = ws.history_md_path.relative_to(ws.path).as_posix()
        self._ensure_gitignored(ws.path, rel)
        ensure_ignore_line(ws.path / ".ignore", f"!{rel}")
        # The two ignore files are the app's, so their lines are app-relative. git is the Project's
        # and runs at the root, so the path it is handed has to be written from there.
        root = project.record.path
        if git.is_repo_root(root):
            git.untrack(root, project.repo_rel(rel))
        ws.render_history_md()

    def _refresh_agent_inputs(self, project: Project) -> None:
        """Rebuild what Sage maintains for the agent to read, before the agent reads it.

        Two things, and the point of this seam is that they cannot come apart: the rendered history
        archive, and the link that puts the Project's Chat Artifacts inside the app the agent stands
        in. They belong in one call because they share every constraint that matters — each is a
        write Sage makes on the user's behalf rather than the agent's, each has to land before the
        turn's baseline, and each has to be restored at the tail of Reset. Left as two calls at four
        sites, a fifth turn entrypoint that remembered one and forgot the other would reopen
        whichever bug it forgot, silently and in the same way as the first time.

        Call at a turn entry and at the tail of Reset, always BEFORE
        `project.snapshot.commit_before_turn()`. Each half documents what that ordering buys it."""
        self._refresh_history_archive(project)
        self._ensure_examples_link(project)

    def _ensure_examples_link(self, project: Project) -> None:
        """Link the Project's Chat Artifacts into the app a turn runs in, and keep the link out of
        git while doing it.

        The handoff prompt hands the implement turn `examples/<threadId>/…` (handoff._HANDOFF_LINE),
        but the build agent's cwd is `apps/<appId>/` and the Artifacts live one level above it — so
        every path the digest named resolved to nothing, and silently: the build still worked,
        because the plan is the spec and the Artifacts are background. Chat's own workdir has had
        this link since it was written, for the same reason and with the same shape
        (`ensure_chat_workdir`); the app directory never got it.

        The ignore rule is anchored and carries NO trailing slash. Git does not follow a symlink, so
        it records this one as a symlink and not as a directory — `examples/`, which matches
        directories only, would not cover it. Unignored, `git add -A` commits a link pointing
        outside the app tree and a fresh clone gets a dangling one. The template ships the rule;
        this call is what fixes an app seeded before it existed.

        No `.ignore` negation, unlike `_refresh_history_archive` above. That one needs it because
        the agent FINDS the archive by grepping, and ripgrep honours `.gitignore`. Nothing here is
        found by grepping: ripgrep does not follow symlinks anyway, so the rule costs no reach the
        link ever had. The digest names its files by exact path, and a direct read resolves through
        a symlink — which is the access this exists to provide.

        Called beside `_refresh_history_archive` and, like it, BEFORE the turn's baseline: creating
        the link and adding that rule are both writes, and after the baseline the read-only gate
        would read them as an Ask/Plan turn that changed the working tree."""
        ws = project.app_for_turn()
        self._ensure_gitignored(ws.path, "/examples")
        _ensure_dir_link(ws.path / "examples", project.record.path / "examples")

    def shutdown(self) -> None:
        # Stop-safe backstop: on a graceful SIGTERM (Domino /stop, idle cull, or the hub button),
        # save any in-progress work first — commit + pull/resolve + push — so stopping never drops
        # uncommitted edits. Done before tearing down opencode, whose server the conflict-resolution
        # turn still needs. Best-effort: _save_to_git never raises, but guard the teardown regardless.
        self._cancel_chat_idle_save()
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
            try:
                self._project.queries.stop()
            except Exception:
                log.exception("shutdown: failed to stop the preview query server")
        if self._oc_server:
            try:
                self._oc_server.stop()
            except Exception:
                log.exception("shutdown: failed to stop opencode server")
