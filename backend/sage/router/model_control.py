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
    def __init__(self, mode: Mode = Mode.MANUAL, phase: Phase = Phase.PLAN) -> None:
        self._mode = mode
        self._phase = phase
        self._picked_model: ModelId | None = None
        self._sensitivity_locked = False  # sticky once set True

    def set_mode(self, mode: Mode) -> None:
        self._mode = mode

    def set_phase(self, phase: Phase) -> None:
        self._phase = phase

    def pick(self, model: ModelId | None) -> None:
        self._picked_model = model

    def on_assets_changed(self, asset_sensitivity_tags: Iterable[bool]) -> None:
        """Recompute the lock from currently-attached assets. Sticky: never clears."""
        if any(asset_sensitivity_tags):
            self._sensitivity_locked = True

    @property
    def locked(self) -> bool:
        return self._sensitivity_locked

    def snapshot(self) -> SessionState:
        return SessionState(
            sensitivity_locked=self._sensitivity_locked,
            mode=self._mode,
            phase=self._phase,
            picked_model=self._picked_model,
        )
