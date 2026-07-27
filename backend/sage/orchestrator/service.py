"""Orchestrator service — assembles a project's parts into one lifecycle (SPEC C1).

A Project bundles: workspace (from the warm template), a Vite supervisor (live preview), a
ModelControl (per-project switching state), and an EnforcementShim (routes model calls through
the gateway with the sovereign override). Per D9 a container hosts exactly one project, bound to
the Domino project's mounted volume and attached lazily on first use.

Deep module, narrow interface: project / build / build_stream / shutdown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
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

log = logging.getLogger("sage.orchestrator")

# Ask/Plan are the two read-only modes: routed to an opencode.json agent whose `permission`
# block OpenCode enforces natively (edit/bash denied), not just hidden from the model's tools
# list. Auto/Implement keep OpenCode's default agent (full permissions).
_READ_ONLY_AGENT = {Mode.ASK: "sage-ask", Mode.PLAN: "sage-plan"}

# The entry script Domino runs to serve a published app (repo root). The builder has the working
# tree, so publish pre-checks it exists locally before deploying (a missing one fails opaquely).
_ENTRY_POINT = "app.sh"
# Published-app deploy status -> terminal phase (mirrors HubService.publish_status). Matched
# case-insensitively; anything else means the deploy is still in progress.
_RUNNING_STATES = frozenset({"running"})
_FAILED_STATES = frozenset({"failed", "error"})


def _agent_for_mode(mode: Mode) -> str | None:
    return _READ_ONLY_AGENT.get(mode)


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
    attached: list[str] = field(default_factory=list)
    # Set by the /v1/chat/completions handler when a model call the agent made this turn fails
    # upstream (bad model id, gateway auth, etc). build()/build_stream() check + clear this so a
    # failed turn is reported as an error instead of silently falling through to "typecheck clean"
    # on an unmodified workspace (the turn never touched any files).
    last_gateway_error: dict | None = None
    # Set by the /build/stop endpoint; build_stream() polls it to revert and stop early.
    stop_requested: bool = False

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
        return self._project

    def _ensure_opencode(self) -> OpenCodeClient:
        """Start the shared OpenCode server on first use (opencode.json in opencode_cwd points
        it at the shim). One server per container; sessions are scoped per workspace."""
        if self._oc_client is None:
            self._oc_server = OpenCodeServer(cwd=self._opencode_cwd)
            self._oc_client = OpenCodeClient(base_url=self._oc_server.start())
        return self._oc_client

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

    def build_stream(self, prompt: str):
        """Same loop as build(), but yields progress events (dicts) as it goes: agent text/tool
        activity, typecheck results, iteration, and a final done event. Reuses the session so
        each call is a follow-up turn (modify/add features) with full context."""
        import time

        project = self.project()
        client = self._ensure_opencode()
        sid = self._ensure_session(project)
        breaker = CircuitBreaker()
        current = prompt

        # Persist only the events the UI actually renders as a chat bubble/card/divider, so
        # replaying history reproduces the same transcript without ephemeral "active"/spinner noise.
        def persist(ev: dict) -> dict:
            if ev["type"] == "agent" or ev["type"] in ("typecheck", "done", "saved"):
                project.workspace.append_history(ev)
            return ev

        # Snapshot before touching history/files so a stop mid-turn can restore exactly this
        # state, and remember how many history entries pre-date this turn so a stop can drop
        # everything appended since (the turn disappears from the transcript entirely).
        project.snapshot.commit_before_turn()
        history_baseline = project.workspace.history_len()

        project.workspace.append_history({"type": "user", "text": prompt})

        def handle_stop() -> dict:
            project.stop_requested = False
            project.snapshot.discard_changes()
            project.workspace.truncate_history(history_baseline)
            return {"type": "stopped"}

        # Scoped to the whole build_stream call (not per turn): client.messages(sid) returns the
        # entire session's messages on every poll, so a per-turn `seen` would let a follow-up
        # turn's first poll re-walk the previous turn's already-completed parts and re-emit/
        # re-persist them out of order (duplicate cards appended after the newer turn began).
        seen: set[tuple[str, int]] = set()
        # A clean typecheck of the untouched template must NOT count as a finished build: track
        # whether the agent actually edited files, and if a turn ends clean with zero edits, nudge
        # it to implement instead of declaring success. Capped so a model that refuses to write
        # can't loop forever.
        made_edits = False
        nudges = 0
        MAX_NUDGES = 2
        # Set when a recovery nudge temporarily escalates routing (stronger model + write perms);
        # both the mode and the model pick are restored right after that turn's inferences finish so
        # a later prompt routes normally.
        escalated = False
        saved_mode: Mode | None = None
        saved_pick: str | None = None
        IMPLEMENT_NUDGE = (
            "You've explored and planned but haven't written any code yet. Now IMPLEMENT the "
            "request: edit the project files (start with src/App.tsx) so the app actually builds "
            "what was asked. Make the code changes now."
        )
        while True:
            if project.stop_requested:
                yield handle_stop()
                return
            yield {"type": "turn", "prompt": current[:120]}
            project.last_gateway_error = None
            agent = _agent_for_mode(project.control.snapshot().mode)
            client.send_prompt(sid, current, agent=agent)
            appeared = False
            start = time.monotonic()
            # The shim classifies plan/implement per model call (phase_classifier). We only observe
            # the resulting phase here to keep the UI's live indicator in sync — routing is decided
            # in the shim, not here, so it stays per-step and race-free.
            last_phase = project.control.snapshot().phase.value
            last_active: str | None = None  # last "active" label emitted (dedup across 1s polls)
            while True:
                if project.stop_requested:
                    client.interrupt(sid)
                    yield handle_stop()
                    return
                running = client.is_running(sid)
                appeared = appeared or running
                for m in client.messages(sid):
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
                                        yield {"type": "active", "tool": tool, "detail": detail}
                                continue
                            seen.add(key)
                            last_active = None  # completed: let the next running tool re-announce
                            if tool in ("edit", "write"):
                                made_edits = True
                            yield persist({"type": "agent", "kind": "tool", "tool": tool, "detail": _tool_detail(tool, part)})
                        elif pt == "text" and part.get("text"):
                            seen.add(key)
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

            # A recovery nudge (below) may have escalated to the stronger model with write perms;
            # its inferences are done now, so restore normal routing before any decision or early
            # return, so the override never leaks into a later prompt.
            if escalated:
                project.control.set_mode(saved_mode)
                project.control.pick(saved_pick)
                escalated = False

            if project.last_gateway_error is not None:
                err = project.last_gateway_error
                yield persist({"type": "error", "message": f"model call failed: {err['message']}"})
                yield persist({"type": "done", "ok": False, "decision": "gateway error"})
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
                # `made_edits` only trips on tools literally named edit/write; the agent may write
                # via another (patch/str_replace/create). Confirm against the snapshot's ground truth
                # so a real edit is never misread as "planned but wrote no code".
                wrote_code = made_edits or project.snapshot.changed_since_pre_turn()
                if report.ok and not wrote_code:
                    if nudges < MAX_NUDGES:
                        nudges += 1
                        # Recovery: the turn stalled at planning. Both tiers can stall here (the
                        # strong plan model in Auto, the cheap implement model in Implement), so retry
                        # on the STRONGER model with full write permission — pin Implement (no
                        # per-step reclassification, writes allowed) and override the pick to the
                        # strong `plan` model. Both restored after the turn (see above). Skipped under
                        # a sensitivity lock, which must keep choosing sovereign models only.
                        snap = project.control.snapshot()
                        if not snap.sensitivity_locked:
                            saved_mode = snap.mode
                            saved_pick = snap.picked_model
                            project.control.set_mode(Mode.IMPLEMENT)
                            project.control.pick(project.shim.catalog.plan)
                            escalated = True
                        yield {"type": "iterate", "reason": "planned but wrote no code — retrying on the stronger model"}
                        current = IMPLEMENT_NUDGE
                        continue
                    yield persist({"type": "done", "ok": False,
                                   "decision": "planned but wrote no code — try Implement mode"})
                    return
                yield persist({"type": "done", "ok": report.ok, "decision": decision.reason})
                if report.ok:
                    saved = self._save_to_git(project, prompt)
                    if saved is not None:
                        yield persist(saved)
                return
            yield {"type": "iterate", "reason": decision.reason}
            current = report.as_agent_message()

    def _save_to_git(self, project: Project, prompt: str) -> dict | None:
        """Commit + push the workspace after a clean build so the app and .sage/ transcript are
        durable. Returns None when the workspace isn't a git repo (local dev / the /tmp spike — no
        save line to show); otherwise a `saved` event. Never raises into the build."""
        from ..workspace import git

        path = project.workspace.path
        if not git.is_repo(path):
            return None
        message = f"sage: {prompt.splitlines()[0][:72]}" if prompt.strip() else "sage: build"
        try:
            committed = git.commit_all(path, message)
            # Integrate any teammate changes before pushing, or the push is rejected as non-ff and
            # the build's work silently never reaches the repo.
            synced = self._integrate_remote(project)
            if synced is not None and synced.status in ("conflict-unresolved", "error"):
                return {"type": "saved", "ok": False, "pushed": False,
                        "detail": f"couldn't sync with the repo — {synced.detail}"}
            if not committed and (synced is None or synced.status == "up-to-date"):
                return {"type": "saved", "ok": True, "pushed": False, "detail": "no changes to commit"}
            result = git.push(path)
            return {"type": "saved", "ok": True, "pushed": result.pushed, "detail": result.detail}
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
            git.commit_all(path, "sage: save before pull")
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
        out["manage_url"] = cp.app_manage_url(pid, app.id, name)
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
        assets = self._assets.list_datasets(self._domino_project_id)
        return [
            {"id": a.id, "name": a.name, "tags": a.tags, "sensitive": is_sensitive(a, self._sensitivity_tag)}
            for a in assets
        ]

    def attach_asset(self, dataset_id: str) -> dict:
        """Attach a dataset to the project. If it carries the sensitivity tag, fire the sovereign
        lock (sticky). This is the real signal that replaces the manual lock toggle."""
        project = self.project()
        asset = next((a for a in self._assets.list_datasets(self._domino_project_id) if a.id == dataset_id), None)
        if asset is None:
            raise LookupError(dataset_id)
        if dataset_id not in project.attached:
            project.attached.append(dataset_id)
        sensitive = is_sensitive(asset, self._sensitivity_tag)
        project.control.on_assets_changed([sensitive])  # sticky lock if sensitive
        return {"attached": asset.name, "sensitive": sensitive, "status": project.status()}

    def detach_asset(self, dataset_id: str) -> dict:
        """Detach a dataset from the project. Does NOT clear the sovereign lock even if the
        dataset was sensitivity-tagged — the asset-driven lock is sticky for the session
        (see ModelControl.on_assets_changed); use the manual lock toggle to unlock."""
        project = self.project()
        asset = next((a for a in self._assets.list_datasets(self._domino_project_id) if a.id == dataset_id), None)
        if asset is None:
            raise LookupError(dataset_id)
        if dataset_id in project.attached:
            project.attached.remove(dataset_id)
        return {"detached": asset.name, "status": project.status()}

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
