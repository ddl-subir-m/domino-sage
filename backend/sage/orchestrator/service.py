"""Orchestrator service — assembles a project's parts into one lifecycle (SPEC C1).

A Project bundles: workspace (from the warm template), a Vite supervisor (live preview), a
ModelControl (per-project switching state), and an EnforcementShim (routes model calls through
the gateway with the sovereign override). Per D9 a container usually hosts one project, but the
registry supports more.

Deep module, narrow interface: create_project / get / active / shutdown.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


@dataclass
class Project:
    id: str
    workspace: Workspace
    supervisor: ViteSupervisor
    control: ModelControl
    shim: EnforcementShim
    session_id: str | None = None

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
            "model": {
                "mode": s.mode.value,
                "phase": s.phase.value,
                "picked_model": s.picked_model,
                "sensitivity_locked": s.sensitivity_locked,
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
    ) -> None:
        self._wm = WorkspaceManager(workspaces_root, template)
        self._gateway = gateway
        self._catalog = catalog
        self._force_model = force_model
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

    def build(self, project_id: str, prompt: str) -> dict:
        """Run one build: prompt the agent, then loop typecheck->feed-errors-back until clean or
        the circuit breaker stops. Requires gateway access (real model calls)."""
        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
        client = self._ensure_opencode()
        if project.session_id is None:
            # No session-level model: use opencode.json's default provider/model. The shim's
            # force_model + router still enforce the real model per request. (Passing an explicit
            # ModelRef at session creation was observed to stall the turn.)
            project.session_id = client.create_session(directory=str(project.workspace.path))
        sid = project.session_id

        def send_and_wait(text: str) -> None:
            client.send_prompt(sid, text)
            client.wait_for_idle(sid)  # wait for the full multi-step turn to finish

        report, decision = run_feedback_loop(
            prompt,
            send_and_wait=send_and_wait,
            check=lambda: self._feedback.check(project.workspace.path),
            breaker=CircuitBreaker(),
        )
        return {
            "ok": report.ok,
            "error_count": len(report.errors),
            "decision": decision.reason,
            "message": report.as_agent_message(),
        }

    def create_project(self, project_id: str, start_preview: bool = True) -> Project:
        workspace = self._wm.create(project_id)
        control = ModelControl(mode=Mode.MANUAL, phase=Phase.PLAN)
        shim = EnforcementShim(control, self._catalog, self._gateway, force_model=self._force_model)
        supervisor = ViteSupervisor(workspace.path)
        if start_preview:
            supervisor.start()
        project = Project(project_id, workspace, supervisor, control, shim)
        self._projects[project_id] = project
        self._active = project_id
        return project

    def get(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def active(self) -> Project | None:
        return self._projects.get(self._active) if self._active else None

    def list_ids(self) -> list[str]:
        return list(self._projects)

    def shutdown(self) -> None:
        for p in self._projects.values():
            p.supervisor.stop()
        if self._oc_server:
            self._oc_server.stop()
