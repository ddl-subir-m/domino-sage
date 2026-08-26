"""The Workbench door: land this viewer in their own Sage Builder (ADR-0004, #45).

The published Workbench App is a door, not a place. The first call finds or creates the viewer's
**Default** Project — Domino project and git repo both `sage-<user-slug>-<id>` — starts or resumes
**their** Sage Builder, and hands back the URL to send the browser to. A second call finds the same
Project and reuses the builder; nobody ever gets a second Default.

The Default is looked up by its Domino name, never by the chip. Naming the chip writes a Sage
display overlay and leaves the Domino/git name alone (there is no Control Plane rename API), so a
renamed Default is still the one this door lands on.

Identity is the sidecar token's user: on the App that is the viewer, via Domino's extended identity.
There is deliberately no inbound-JWT prefer-viewer path here — the token that provisions is the
token that acts.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from . import naming
from .domino import ProjectRef, UserRef
from .service import AppCreated, ProvisionService, workspace_is_running

log = logging.getLogger("sage.provision.door")


@dataclass(frozen=True)
class DoorTarget:
    """Where the door is sending this viewer, and what it had to do to get there."""

    project: ProjectRef
    open_url: str | None
    running: bool             # the builder's session is up, so the URL is safe to open now
    launched: bool            # a builder was started or resumed by this call (the viewer waits)
    created: bool             # this call created the Default Project itself (first ever open)
    workspace_id: str | None  # the builder to poll while it boots


class Door:
    def __init__(self, service: ProvisionService, viewer: Callable[[], UserRef]) -> None:
        self._service = service
        self._viewer = viewer

    def ensure_default(self) -> DoorTarget:
        """Find or create this viewer's Default Project and open their Sage Builder in it."""
        who = self._viewer()
        expected = naming.default_project_name(who.name, who.id)
        existing = self._find_default(expected)
        if existing is not None:
            opened = self._service.open_app(existing.id)
            return DoorTarget(
                project=existing,
                open_url=opened["open_url"],
                running=opened["running"],
                launched=opened["launched"],
                created=False,
                workspace_id=(opened["workspace"] or {}).get("id"),
            )
        log.info("door: creating Default Project %s for %s", expected, who.name or who.id)
        return _created_target(self._service.create_app("Default", name=expected))

    def status(self, project_id: str, workspace_id: str | None = None) -> dict:
        """Is that builder up yet, and where is it?

        A launched or resumed workspace reports `Started` while its session is still booting, and
        the browser lands on a not-ready page if it is sent in then. The door page polls this until
        `running`, so the wait happens on a page that says what it is waiting for.
        """
        return self._service.workspace_status(project_id, workspace_id)

    def _find_default(self, expected: str) -> ProjectRef | None:
        """This viewer's Default among their Sage Projects, or None.

        Matches on the Domino project name — `expected`, or an `expected-N` collision suffix. The
        exact name wins so a collision suffix can never shadow the real one and split the viewer
        across two Defaults.
        """
        mine = [p for p in self._service.list_apps() if naming.is_default_name(p.name, expected)]
        if not mine:
            return None
        return next((p for p in mine if p.name == expected), mine[0])


def _created_target(created: AppCreated) -> DoorTarget:
    return DoorTarget(
        project=created.project,
        open_url=created.open_url,
        running=workspace_is_running(created.workspace),
        launched=True,
        created=True,
        workspace_id=(created.workspace or {}).get("id"),
    )
