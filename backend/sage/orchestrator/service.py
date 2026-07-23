"""Orchestrator service — assembles a project's parts into one lifecycle (SPEC C1).

A Project bundles: workspace (from the warm template), a Vite supervisor (live preview), a
ModelControl (per-project switching state), and an EnforcementShim (routes model calls through
the gateway with the sovereign override). Per D9 a container usually hosts one project, but the
registry supports more.

Deep module, narrow interface: create_project / get / active / shutdown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx

from ..assets.provider import Asset, AssetProvider, FakeAssetProvider, is_sensitive
from ..assets.provider import DEFAULT_SENSITIVITY_TAG
from ..driver.opencode import OpenCodeClient, run_feedback_loop
from ..driver.server import OpenCodeServer
from ..feedback.circuit_breaker import CircuitBreaker
from ..feedback.runner import FeedbackRunner
from ..gateway.client import GatewayClient
from ..router.model_control import ModelControl
from ..router.models import Mode, ModelCatalog, Phase
from ..preview.supervisor import ViteSupervisor
from ..shim.enforcement import EnforcementShim
from ..workspace.manager import Workspace, WorkspaceManager
from ..workspace.snapshot import TurnSnapshot

log = logging.getLogger("sage.orchestrator")

# Ask/Plan are the two read-only modes: routed to an opencode.json agent whose `permission`
# block OpenCode enforces natively (edit/bash denied), not just hidden from the model's tools
# list. Auto/Implement keep OpenCode's default agent (full permissions).
_READ_ONLY_AGENT = {Mode.ASK: "sage-ask", Mode.PLAN: "sage-plan"}


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
        workspaces_root: Path,
        template: Path,
        gateway: GatewayClient,
        catalog: ModelCatalog,
        opencode_cwd: Path | None = None,
        feedback: FeedbackRunner | None = None,
        force_model: bool = False,
        assets: AssetProvider | None = None,
        sensitivity_tag: str = DEFAULT_SENSITIVITY_TAG,
        domino_project_id: str | None = None,
    ) -> None:
        self._wm = WorkspaceManager(workspaces_root, template)
        self._gateway = gateway
        self._catalog = catalog
        self._force_model = force_model
        self._assets = assets or FakeAssetProvider()
        self._sensitivity_tag = sensitivity_tag
        self._domino_project_id = domino_project_id
        self._opencode_cwd = Path(opencode_cwd) if opencode_cwd else Path.cwd()
        self._feedback = feedback or FeedbackRunner()
        self._projects: dict[str, Project] = {}
        self._active: str | None = None
        self._oc_server: OpenCodeServer | None = None
        self._oc_client: OpenCodeClient | None = None

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

    def build(self, project_id: str, prompt: str) -> dict:
        """Run one build to completion (non-streaming). Reuses the session, so repeated calls are
        follow-up turns with context. Requires gateway access."""
        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
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

    def build_stream(self, project_id: str, prompt: str):
        """Same loop as build(), but yields progress events (dicts) as it goes: agent text/tool
        activity, typecheck results, iteration, and a final done event. Reuses the session so
        each call is a follow-up turn (modify/add features) with full context."""
        import time

        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
        client = self._ensure_opencode()
        sid = self._ensure_session(project)
        breaker = CircuitBreaker()
        current = prompt

        # Persist only the events the UI actually renders as a chat bubble/card/divider, so
        # replaying history reproduces the same transcript without ephemeral "active"/spinner noise.
        def persist(ev: dict) -> dict:
            if ev["type"] == "agent" or ev["type"] in ("typecheck", "done"):
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
                yield persist({"type": "done", "ok": report.ok, "decision": decision.reason})
                return
            yield {"type": "iterate", "reason": decision.reason}
            current = report.as_agent_message()

    def stop_build(self, project_id: str) -> None:
        """Interrupt the in-flight build_stream() turn; it reverts files/history and stops."""
        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
        project.stop_requested = True
        if project.session_id and self._oc_client is not None:
            try:
                self._oc_client.interrupt(project.session_id)
            except httpx.HTTPError:
                pass

    def _effective_catalog(self, workspace: Workspace) -> ModelCatalog:
        overrides = workspace.read_catalog_overrides()
        return replace(self._catalog, **overrides) if overrides else self._catalog

    def create_project(self, project_id: str, start_preview: bool = True) -> Project:
        workspace = self._wm.create(project_id)
        control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
        shim = EnforcementShim(control, self._effective_catalog(workspace), self._gateway, force_model=self._force_model)
        supervisor = ViteSupervisor(workspace.path)
        if start_preview:
            supervisor.start()
        project = Project(project_id, workspace, supervisor, control, shim, TurnSnapshot(workspace.path))
        self._projects[project_id] = project
        self._active = project_id
        return project

    def get(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def active(self) -> Project | None:
        return self._projects.get(self._active) if self._active else None

    def list_ids(self) -> list[str]:
        return list(self._projects)

    def list_all_ids(self) -> list[str]:
        """Registered projects plus any workspace left on disk from a prior process (e.g. a
        restart wiped the in-memory registry but the project's files and history survive)."""
        return sorted(set(self._projects) | set(self._wm.list_ids()))

    def open_project(self, project_id: str, start_preview: bool = True) -> Project:
        """Re-attach an on-disk workspace that isn't currently registered, instead of re-copying
        the template (which create_project would refuse, since the directory already exists).
        Idempotent: returns the existing Project if it's already registered."""
        existing = self.get(project_id)
        if existing is not None:
            self._active = project_id
            return existing
        workspace = self._wm.get(project_id)
        if workspace is None:
            raise FileNotFoundError(project_id)
        control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
        shim = EnforcementShim(control, self._effective_catalog(workspace), self._gateway, force_model=self._force_model)
        supervisor = ViteSupervisor(workspace.path)
        if start_preview:
            supervisor.start()
        project = Project(project_id, workspace, supervisor, control, shim, TurnSnapshot(workspace.path))
        self._projects[project_id] = project
        self._active = project_id
        return project

    def set_catalog(self, project_id: str, **fields: str | None) -> ModelCatalog:
        """Override which model id fills a catalog slot (sovereign/plan/implement/default) for
        this project, persisted so it survives a restart. Only non-empty fields change; the rest
        keep their current value."""
        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
        changes = {k: v for k, v in fields.items() if v}
        if not changes:
            return project.shim.catalog
        new_catalog = replace(project.shim.catalog, **changes)
        project.shim.set_catalog(new_catalog)
        overrides = project.workspace.read_catalog_overrides()
        overrides.update(changes)
        project.workspace.write_catalog_overrides(overrides)
        return new_catalog

    def history(self, project_id: str) -> list[dict]:
        """Reads straight from the workspace (not the in-memory registry) so history is available
        even for a dormant project that hasn't been re-attached via open_project yet."""
        workspace = self._wm.get(project_id)
        if workspace is None:
            raise FileNotFoundError(project_id)
        return workspace.read_history()

    def delete_project(self, project_id: str) -> None:
        """Stop the project's preview (if running) and remove its workspace from disk. Raises if
        the project is neither registered nor present on disk."""
        if project_id not in self.list_all_ids():
            raise FileNotFoundError(project_id)
        project = self._projects.pop(project_id, None)
        if project is not None:
            project.supervisor.stop()
        if self._active == project_id:
            self._active = None
        self._wm.delete(project_id)

    def list_assets(self) -> list[dict]:
        assets = self._assets.list_datasets(self._domino_project_id)
        return [
            {"id": a.id, "name": a.name, "tags": a.tags, "sensitive": is_sensitive(a, self._sensitivity_tag)}
            for a in assets
        ]

    def attach_asset(self, project_id: str, dataset_id: str) -> dict:
        """Attach a dataset to the project. If it carries the sensitivity tag, fire the sovereign
        lock (sticky). This is the real signal that replaces the manual lock toggle."""
        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
        asset = next((a for a in self._assets.list_datasets(self._domino_project_id) if a.id == dataset_id), None)
        if asset is None:
            raise LookupError(dataset_id)
        if dataset_id not in project.attached:
            project.attached.append(dataset_id)
        sensitive = is_sensitive(asset, self._sensitivity_tag)
        project.control.on_assets_changed([sensitive])  # sticky lock if sensitive
        return {"attached": asset.name, "sensitive": sensitive, "status": project.status()}

    def detach_asset(self, project_id: str, dataset_id: str) -> dict:
        """Detach a dataset from the project. Does NOT clear the sovereign lock even if the
        dataset was sensitivity-tagged — the asset-driven lock is sticky for the session
        (see ModelControl.on_assets_changed); use the manual lock toggle to unlock."""
        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
        asset = next((a for a in self._assets.list_datasets(self._domino_project_id) if a.id == dataset_id), None)
        if asset is None:
            raise LookupError(dataset_id)
        if dataset_id in project.attached:
            project.attached.remove(dataset_id)
        return {"detached": asset.name, "status": project.status()}

    def shutdown(self) -> None:
        # Best-effort per resource: one project's preview failing to stop must not leave the
        # rest (or the shared opencode server) running as orphans.
        for pid, p in self._projects.items():
            try:
                p.supervisor.stop()
            except Exception:
                log.exception("shutdown: failed to stop preview for project %s", pid)
        if self._oc_server:
            try:
                self._oc_server.stop()
            except Exception:
                log.exception("shutdown: failed to stop opencode server")
