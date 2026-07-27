"""ModelControl — the state owner for the model-policy seam (DESIGN.md Seam 1).

Owns the mutable SessionState and its transitions. The router only reads a snapshot.
Single serialized writer, so the manual-toggle-vs-auto-phase race has one arbiter.

Sticky lock rule (SPEC.md): once a sensitivity-tagged asset is attached, the lock stays
on for the session; detaching does NOT clear it.
"""
from __future__ import annotations

from collections.abc import Iterable

from .models import Mode, ModelId, Phase, SessionState


class ModelControl:
    def __init__(self, mode: Mode = Mode.AUTO, phase: Phase = Phase.PLAN) -> None:
        self._mode = mode
        self._phase = phase
        self._picked_model: ModelId | None = None
        self._asset_locked = False   # sticky once True: set by an attached sensitivity-tagged asset
        self._manual_locked = False  # user-toggled via the "Force sovereign" button; freely reversible

    def set_mode(self, mode: Mode) -> None:
        self._mode = mode
        # Keep the phase indicator honest for pinned modes so the UI spinner matches what routes
        # (the router ignores phase in these modes; only the displayed phase changes). Auto is left
        # alone — the shim's per-step classifier drives its phase.
        if mode is Mode.IMPLEMENT:
            self._phase = Phase.IMPLEMENT
        elif mode is Mode.PLAN:
            self._phase = Phase.PLAN

    def set_phase(self, phase: Phase) -> None:
        self._phase = phase

    def pick(self, model: ModelId | None) -> None:
        self._picked_model = model

    def on_assets_changed(self, asset_sensitivity_tags: Iterable[bool]) -> None:
        """Recompute the asset-driven lock from currently-attached assets. Sticky: never clears."""
        if any(asset_sensitivity_tags):
            self._asset_locked = True

    def set_manual_lock(self, on: bool) -> None:
        """User-initiated lock/unlock, independent of the sticky asset-driven lock."""
        self._manual_locked = on

    @property
    def locked(self) -> bool:
        return self._asset_locked or self._manual_locked

    @property
    def asset_locked(self) -> bool:
        return self._asset_locked

    @property
    def manual_locked(self) -> bool:
        return self._manual_locked

    def snapshot(self) -> SessionState:
        return SessionState(
            sensitivity_locked=self.locked,
            mode=self._mode,
            phase=self._phase,
            picked_model=self._picked_model,
        )
