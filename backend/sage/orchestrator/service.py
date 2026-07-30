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

from ..assets.provider import Asset, AssetProvider, FakeAssetProvider, is_sensitive
from ..assets.provider import DEFAULT_SENSITIVITY_TAG
from ..driver.opencode import OpenCodeClient, run_feedback_loop
from ..driver.server import OpenCodeServer
from ..feedback.circuit_breaker import CircuitBreaker
from ..feedback.runner import FeedbackRunner
from ..gateway.client import GatewayClient
from ..router.model_control import ModelControl
from ..router.models import Mode, ModelCatalog, Phase
from ..preview.prefix import domino_base_prefix
from ..preview.supervisor import ViteSupervisor
from ..shim.enforcement import EnforcementShim
from ..workspace.manager import Workspace, WorkspaceManager
from ..workspace.snapshot import TurnSnapshot
from .describe import describe, image_mime

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

# The entry script Domino runs to serve a published app (repo root). The builder has the working
# tree, so publish pre-checks it exists locally before deploying (a missing one fails opaquely).
_ENTRY_POINT = "app.sh"
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


def _looks_like_question(prompt: str) -> bool:
    """True when a prompt asks for information ("what colour is this?") rather than asking us to build
    something ("build a file upload UI"). Used to tell a first-turn question from a build request so
    we answer it directly instead of proposing a build plan.

    Deliberately conservative — only a CLEAR question counts; anything ambiguous returns False and
    falls through to the plan gate, because a wrongly-skipped gate silently builds without approval
    (the worse failure). Pure and deterministic, no model call — matches the phase classifier's style.
    An explicit build verb anywhere wins, so "can you build me a dashboard?" is a build, not a question."""
    text = prompt.strip().lower()
    words = re.findall(r"[a-z']+", text)
    if not words:
        return False
    if any(w in _BUILD_VERB for w in words):
        return False
    return words[0] in _QUESTION_LEAD or text.endswith("?")


def _should_gate(*, mode: Mode, has_built: bool, skip_planning: bool, is_question: bool = False) -> bool:
    """Plan gate (SPEC P6): run the read-only planner and stop for the user to approve before any
    code is written. Fires in Plan mode, or automatically on the first BUILD of a project that hasn't
    been built yet — unless the project opted out. Never gates Ask (read-only Q&A).

    Keyed on has_built, not "first turn": a question asked before the first build (answered read-only,
    see answer_only in build_stream) must not consume the gate — the first real build request still
    gates. A *question* is not a build to be planned, so it skips the gate. Plan mode always gates:
    it's an explicit ask to plan. Once built, iteration turns don't gate."""
    if skip_planning or mode is Mode.ASK:
        return False
    if mode is Mode.PLAN:
        return True
    return not has_built and not is_question


def _is_answer_only(*, mode: Mode, is_question: bool, is_approval: bool) -> bool:
    """A turn that answers read-only instead of building — no plan card, no edits, no implement-nudge.
    Two cases: Ask mode (always read-only Q&A), and any question in Auto mode (whether or not the app
    is built — a question about a built app should be answered, not turned into edits). An approval is
    the user asking to build, never an answer. Build requests and Plan/Implement mode fall through to
    the normal build path. Mutually exclusive with the plan gate (a question is never gated)."""
    if is_approval:
        return False
    return mode is Mode.ASK or (mode is Mode.AUTO and is_question)


def _approve_prompt(plan_md: str, answers: str) -> str:
    """The Implement-turn prompt built from an approved plan (SPEC P6): the plan is fed in as
    context so the build turn constructs exactly what the user signed off on."""
    parts = ["The user approved this plan. Build the app it describes now — implement it, don't re-plan.",
             "", "## Approved plan", plan_md]
    if answers.strip():
        parts += ["", "## Answers to the open questions", answers.strip()]
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
                "mode": s.mode.value,
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
        force_model: bool = False,
        assets: AssetProvider | None = None,
        sensitivity_tag: str = DEFAULT_SENSITIVITY_TAG,
        domino_project_id: str | None = None,
        control_plane: ControlPlane | None = None,
        domino_project_name: str | None = None,
        workspace_id: str | None = None,
        domino_run_id: str | None = None,
    ) -> None:
        self._wm = WorkspaceManager(workspace_dir, template)
        self._project_id = project_id
        self._gateway = gateway
        self._catalog = catalog
        self._force_model = force_model
        self._assets = assets or FakeAssetProvider()
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
        self._opencode_cwd = Path(opencode_cwd) if opencode_cwd else Path.cwd()
        self._feedback = feedback or FeedbackRunner()
        # One container hosts one project (D9): a single bound project, attached lazily on first
        # use (seeding the volume + rehydrating .sage/ from disk), memoized thereafter.
        self._project: Project | None = None
        self._oc_server: OpenCodeServer | None = None
        self._oc_client: OpenCodeClient | None = None
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

    def project(self, start_preview: bool = True) -> Project:
        """Get-or-attach the single bound project. Idempotent: on first call it seeds the volume
        if empty, wires control/shim/supervisor, starts the preview, and rehydrates session/history/
        plan/model-overrides from .sage/; subsequent calls return the memoized Project (the preview
        is not restarted)."""
        if self._project is not None:
            return self._project
        workspace = self._wm.ensure(self._project_id)
        control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
        shim = EnforcementShim(control, self._effective_catalog(workspace), self._gateway, force_model=self._force_model)
        supervisor = ViteSupervisor(workspace.path, domino_base_prefix())
        if start_preview:
            supervisor.start()
        self._project = Project(self._project_id, workspace, supervisor, control, shim, TurnSnapshot(workspace.path))
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
            # No session-level model: use opencode.json's default; the shim's force_model + router
            # enforce the real model per request. (An explicit ModelRef at creation stalled turns.)
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
                item["image_uri"] = self._image_data_uri(real, d["size"])
            out.append(item)
        return out or None

    def _image_data_uri(self, real: Path, size: int) -> str | None:
        """An image inlined as `data:<mime>;base64,...` for the agent's prompt, or None if it can't
        be. Images are the one type where a descriptor isn't enough — the pixels ARE the content.

        Capped because base64 inflates by ~4/3 and the whole thing rides in the prompt body; an
        oversized image degrades to its descriptor (dimensions/format) rather than failing the turn.
        """
        if size > _MAX_INLINE_IMAGE_BYTES:
            return None
        mime = image_mime(str(real))
        if mime is None:
            return None
        try:
            return f"data:{mime};base64,{base64.b64encode(real.read_bytes()).decode()}"
        except OSError:
            return None

    def build_stream(self, prompt: str, mentions: list[str] | None = None):
        """Public entry: serialize this turn behind the per-project turn lock, then stream it.

        One turn at a time. If a turn is already streaming, refuse rather than run a second one
        concurrently (see _turn_lock) — overlapping turns corrupt the shared read-only gate and
        working tree. The refusal is a clean error + done(busy) so the UI surfaces it, not a hang."""
        if not self._turn_lock.acquire(blocking=False):
            yield from self._busy_refusal()
            return
        try:
            yield from self._build_stream(prompt, mentions)
        finally:
            self._turn_lock.release()

    def _busy_refusal(self):
        """Events yielded when a turn is refused because another is already streaming."""
        yield {"type": "error", "message": "A build is already running. Wait for it to finish or "
               "stop it first, then resend."}
        yield {"type": "done", "ok": False, "decision": "busy"}

    def _seen_baseline(self, client, sid: str) -> set[tuple[str, int]]:
        """Keys of every assistant part already in the session, so a turn only emits its OWN parts.

        client.messages(sid) returns the ENTIRE session on every poll, and the emit-tracking `seen`
        set starts empty for each user turn. Without this baseline, a follow-up turn's first poll
        re-walks the previous turn's completed parts and re-emits them — the prior turn's summary
        reappearing at the top of the new turn (the "ordering" echo). Key format `(message id, part
        index)` must match the poll loop in _build_stream. Best-effort: on a poll error we return an
        empty baseline (worst case is the echo, not a broken build) and let the loop retry."""
        seen: set[tuple[str, int]] = set()
        try:
            for m in client.messages(sid):
                if m.get("type") == "assistant":
                    for i in range(len(m.get("content", []))):
                        seen.add((m["id"], i))
        except httpx.HTTPError as e:
            log.warning("could not baseline session messages, prior-turn echo possible: %s", e)
        return seen

    def _build_stream(self, prompt: str, mentions: list[str] | None = None, *, is_approval: bool = False):
        """Same loop as build(), but yields progress events (dicts) as it goes: agent text/tool
        activity, typecheck results, iteration, and a final done event. Reuses the session so
        each call is a follow-up turn (modify/add features) with full context.

        Assumes the caller holds _turn_lock (build_stream / approve_stream acquire it).

        `mentions` are workspace paths of attached files the user @-referenced; they're resolved to
        real files and attached to this turn's prompt (see _resolve_mentions)."""
        import time

        project = self.project()
        client = self._ensure_opencode()
        sid = self._ensure_session(project)
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
            if ev["type"] == "agent" or ev["type"] in ("typecheck", "done", "saved", "data-leak", "plan-proposed"):
                project.workspace.append_history(ev)
            return ev

        # Snapshot before touching history/files so a stop mid-turn can restore exactly this
        # state, and remember how many history entries pre-date this turn so a stop can drop
        # everything appended since (the turn disappears from the transcript entirely).
        project.snapshot.commit_before_turn()
        history_baseline = project.workspace.history_len()

        # Plan gate (SPEC P6): in Plan mode (or on the first turn of a fresh project), run the
        # read-only planner and stop for the user to approve — this turn deliberately writes no code.
        mode_at_start = project.control.snapshot().mode
        is_question = _looks_like_question(prompt)
        has_built = project.workspace.has_built()
        # An approval is the user saying "build this plan now" — never gate it (that would re-propose a
        # plan for an already-approved build and loop forever) and never treat it as a question.
        gate = False if is_approval else _should_gate(
            mode=mode_at_start,
            has_built=has_built,
            skip_planning=bool(project.workspace.read_settings().get("skip_planning")),
            is_question=is_question,
        )
        # Answer-only turn: answered directly and read-only, no plan card, no build (see _is_answer_only).
        # Read-only so answering a question can never quietly build or edit an app; and unlike a normal
        # Auto turn, a clean no-edit answer is the goal, so it must not be nudged to implement.
        answer_only = _is_answer_only(mode=mode_at_start, is_question=is_question, is_approval=is_approval)
        plan_text_parts: list[str] = []  # accumulates the planner's text to persist as plan.md

        project.workspace.append_history({"type": "user", "text": prompt})

        # Auto may be escalated to Implement mid-stream to force a stalled build to actually write
        # code (see the nudge branch below). Restore the user's mode on every exit from the stream.
        original_mode = project.control.snapshot().mode
        # The user's own model pick (None in Auto). Set when a planning stall forces us to pin the
        # strong model for the Implement retry (see the nudge branch); restored on exit so we never
        # leave the user's own pick clobbered.
        original_pick = project.control.snapshot().picked_model
        escalated_pick = False

        # Arm the read-only guarantee for a gated (plan) turn OR an answer-only turn (Ask mode / any
        # Auto question): the shim strips every write/shell tool from each request, which stops the
        # turn writing code (OpenCode's own per-agent permission block doesn't). Token-scoped to THIS
        # turn — disarm only clears our own arming, so nothing drops the guarantee out from under us.
        ro_token = project.control.arm_read_only() if (gate or answer_only) else None

        def restore_mode() -> None:
            if project.control.snapshot().mode is not original_mode:
                project.control.set_mode(original_mode)
            if escalated_pick:
                project.control.pick(original_pick)
            if ro_token is not None:
                project.control.disarm_read_only(ro_token)

        def handle_stop() -> dict:
            project.stop_requested = False
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
        seen: set[tuple[str, int]] = self._seen_baseline(client, sid)
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
            turn_start_tree = project.snapshot.working_tree_hash()
            # A gated turn is pinned to the read-only planner regardless of the user's mode; it
            # proposes a plan and never edits, so it always lands in the no-edit fork below. A first-
            # turn question uses the read-only Q&A agent — it answers, it doesn't plan or build.
            if gate:
                agent = "sage-plan"
            elif answer_only:
                agent = "sage-ask"
            else:
                agent = _agent_for_mode(project.control.snapshot().mode)
            # Which agent this turn actually asked for, and whether the plan gate was armed. OpenCode
            # falls back to its default build agent when a name doesn't resolve, so a turn that ignores
            # a mode's read-only permission looks identical to one that honored it — log the intent so
            # /api/diag's log_tail can be compared against its `agents` list.
            log.info("turn: agent=%s gate=%s answer_only=%s mode=%s", agent, gate, answer_only,
                     project.control.snapshot().mode.value)
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
                        key = (m["id"], i)
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
                            if gate:
                                plan_text_parts.append(part["text"])
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
            if answer_only and not (made_edits or project.snapshot.working_tree_hash() != turn_start_tree):
                restore_mode()
                yield persist({"type": "done", "ok": True, "decision": "answered"})
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
                wrote_code = made_edits or project.snapshot.working_tree_hash() != turn_start_tree
                # Surface why a turn landed where it did — especially a no-edit turn. Reads apart the
                # three failure modes (see Project.model_calls); rendered as a status line in the UI.
                shim_bypassed = (project.model_calls == 0 and base_port is not None and base_port != control_port)
                yield {"type": "turn-summary", "model_calls": project.model_calls,
                       "tool_call_responses": project.tool_call_responses, "wrote_code": wrote_code,
                       "shim_bypassed": shim_bypassed, "base_port": base_port, "control_port": control_port,
                       "vendor_keys": vendor_keys}
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
                    if gate:
                        # First-build gate (SPEC P6): the planner proposed a plan and wrote no code —
                        # that's success here, not a stall, so short-circuit the nudge loop. Persist
                        # the plan as the handoff artifact and stop for the user to approve.
                        plan_md = "\n".join(plan_text_parts).strip()
                        project.workspace.write_plan(plan_md)
                        restore_mode()
                        yield persist({"type": "plan-proposed", "plan": plan_md})
                        yield persist({"type": "done", "ok": True, "decision": "awaiting approval"})
                        return
                    # answer_only turns never reach here — a no-edit Q&A already finished before typecheck.
                    if nudges < MAX_NUDGES:
                        nudges += 1
                        # The nudge is a fresh user turn, so the shim's per-step classifier resets to
                        # PLAN (it biases plan until the first write) — in Auto the model can just plan
                        # again and stall. Pin Implement for the retry so it actually writes: the "try
                        # Implement mode" advice, applied automatically instead of shown as a dead end.
                        mode_now = project.control.snapshot().mode
                        if mode_now is Mode.AUTO:
                            project.control.set_mode(Mode.IMPLEMENT)
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
                                if original_mode is Mode.IMPLEMENT
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
                if report.ok:
                    # A clean code-writing build succeeded (a no-edit plan/answer turn returned earlier),
                    # so this project is now "built" — future turns gate on plan, not on this being done.
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
        turn to misread. Approval means "build it now", so if the user is in Plan mode we run this
        turn in Implement mode — Plan mode's agent is read-only and its gate would just re-plan — and
        restore their mode afterwards. An Auto/Implement approve already has history, so it's never
        re-gated regardless."""
        # Serialize like build_stream: approving while a turn already streams would overlap two turns
        # on one working tree and read-only gate. We hold the lock across the whole approve (plan
        # write + mode swap + build) and call _build_stream directly so it doesn't re-acquire.
        if not self._turn_lock.acquire(blocking=False):
            yield from self._busy_refusal()
            return
        try:
            project = self.project()
            if plan_edits is not None:
                project.workspace.write_plan(plan_edits)
            plan_md = project.workspace.read_plan() or ""
            prior_mode = project.control.snapshot().mode
            if prior_mode is Mode.PLAN:
                project.control.set_mode(Mode.IMPLEMENT)
            try:
                yield from self._build_stream(_approve_prompt(plan_md, answers), is_approval=True)
            finally:
                if project.control.snapshot().mode is not prior_mode:
                    project.control.set_mode(prior_mode)
                # One-shot handoff: consumed, so move it out of the agent's live view (git keeps history).
                project.workspace.archive_plan()
        finally:
            self._turn_lock.release()

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
        durable. Returns None when the workspace isn't a git repo (local dev / the /tmp spike — no
        save line to show); otherwise a `saved` event. Never raises into the build."""
        from ..workspace import git

        path = project.workspace.path
        if not git.is_repo(path):
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
        except Exception as e:  # noqa: BLE001
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
        if not git.is_repo(path) or not git.has_remote(path):
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
        except Exception as e:  # noqa: BLE001
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
        # The builder holds the working tree, so a fast local check beats the hub's GitHub-API probe.
        if not (project.workspace.path / _ENTRY_POINT).exists():
            raise RuntimeError(
                f"'{_ENTRY_POINT}' is missing from the workspace, so Domino has no entry script to "
                f"run. Add {_ENTRY_POINT} to the project root and rebuild, then publish again."
            )
        # Deploy the newest code: commit + push before publishing. Best-effort — a save failure (no
        # remote, offline) must not block a publish of whatever is already committed.
        try:
            self._save_to_git(project, "save before publish")
        except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001 — best-effort discovery; a failure just means "unknown"
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
        if project.session_id and self._oc_client is not None:
            try:
                self._oc_client.interrupt(project.session_id)
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
        return {"attached": file_path, "dataset": asset.name, "path": rel, "size": size, "sensitive": sensitive, "status": project.status()}

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
        return {"uploaded": name, "dataset": target.name, "dataset_id": target.id, "path": rel,
                "size": size, "sensitive": effective_sensitive, "status": project.status()}

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
                "The user attached the files below. Each lives on disk at the path shown (read or "
                "edit it there) and the running app serves it at the URL shown. Load one in app code "
                "by fetching it RELATIVE TO THE APP BASE, so it resolves in both the dev preview and "
                "the published app:", "",
                "```js",
                "// import.meta.env.BASE_URL always ends in '/', so this string is a valid relative",
                "// URL in both the dev preview and the published app.",
                'const url = import.meta.env.BASE_URL + "data/<slug>/<name>";',
                "const text = await fetch(url).then((r) => r.text());",
                "```", "",
                "Do NOT wrap it in `new URL(path, import.meta.env.BASE_URL)` — BASE_URL is a path "
                "(e.g. `/`), not an absolute URL, so `new URL()` throws `Invalid base URL` and crashes "
                "the app on load. Just concatenate as shown. "
                "Do NOT fetch a leading-slash path like `/data/...` — it breaks under the app's base "
                "prefix. Do NOT copy these files into `src/`: `public/data/` is gitignored on purpose, "
                "so copying leaks the data into the app's git repo. @mention a file by its disk path.", "",
            ]
            for e in project.attached:
                path = e["path"]
                served = path[len("public/"):] if path.startswith("public/") else path
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
        if inner.startswith(prefix):
            inner = inner[len(prefix):]
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
