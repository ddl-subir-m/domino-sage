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
        # Read-only guarantee is scoped to the turn that armed it, not a shared on/off flag. arm_
        # read_only() mints a fresh token and returns it; disarm only clears if the live token is
        # still that same one. So a turn can never clear a *different* turn's arming (a stale disarm
        # from a crashed/overlapping turn is a no-op), and read-only can't be silently dropped
        # mid-flight. `read_only_turn` (the bool the shim reads) is simply "a token is live".
        self._read_only_token: object | None = None

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
        """Recompute the asset-driven lock from currently-attached assets. Sticky: attaching a
        sensitivity-tagged asset locks and detaching never clears it. A later user override
        (clear_asset_lock) can drop the lock, but attaching another sensitive asset re-locks."""
        if any(asset_sensitivity_tags):
            self._asset_locked = True

    def clear_asset_lock(self) -> None:
        """User override of the sticky asset-driven lock. The sovereign guarantee no longer holds
        for the session unless another sensitivity-tagged asset is attached."""
        self._asset_locked = False

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

    def arm_read_only(self) -> object:
        """Arm the read-only guarantee for a gated turn and return its token. The caller keeps the
        token and passes it back to disarm_read_only() on exit. Minting a new token supersedes any
        prior arming, so a turn always owns the live guarantee for its own duration."""
        token = object()
        self._read_only_token = token
        return token

    def disarm_read_only(self, token: object) -> None:
        """Clear the read-only guarantee, but only if `token` is still the live one. A disarm from a
        turn that no longer owns the guarantee (superseded, or an out-of-order exit) is a no-op — so
        one turn can never drop another turn's read-only mid-flight."""
        if self._read_only_token is token:
            self._read_only_token = None

    def snapshot(self) -> SessionState:
        return SessionState(
            sensitivity_locked=self.locked,
            mode=self._mode,
            phase=self._phase,
            picked_model=self._picked_model,
            read_only_turn=self._read_only_token is not None,
        )
