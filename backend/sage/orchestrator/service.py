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
    def __init__(self, workspaces_root: Path, template: Path, gateway: GatewayClient, catalog: ModelCatalog) -> None:
        self._wm = WorkspaceManager(workspaces_root, template)
        self._gateway = gateway
        self._catalog = catalog
        self._projects: dict[str, Project] = {}
        self._active: str | None = None

    def create_project(self, project_id: str, start_preview: bool = True) -> Project:
        workspace = self._wm.create(project_id)
        control = ModelControl(mode=Mode.MANUAL, phase=Phase.PLAN)
        shim = EnforcementShim(control, self._catalog, self._gateway)
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
