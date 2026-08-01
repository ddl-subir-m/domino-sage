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
        # Why the live arming is read-only ("ask"/"question"/"plan"); see SessionState.read_only_reason.
        # Set and cleared with the token, so it can never outlive the arming it describes.
        self._read_only_reason = ""
        # Same token discipline as read-only: a per-turn arming for internet access, so an
        # overlapping/crashed turn can never drop another turn's guarantee. See arm_web().
        self._web_token: object | None = None
        # And again for the mode a running turn is pinned to. The shim reads snapshot() per REQUEST,
        # so without a pin the picker lands mid-turn: the later inferences of a build lose their edit
        # tools and swap to another model while the earlier tool calls are still in their context.
        # `_mode` stays the user's standing choice — changing it while a turn streams is recorded and
        # takes effect on the next turn, rather than half-applying to this one. See arm_turn_mode().
        self._turn_mode: Mode | None = None
        self._turn_mode_token: object | None = None

    def set_mode(self, mode: Mode) -> None:
        """The user's standing mode choice — what the next turn runs as. While a turn is pinned
        (arm_turn_mode) this only records the choice; it does not change what that turn routes to."""
        self._mode = mode
        if self._turn_mode_token is None:
            self._sync_phase(mode)

    def _sync_phase(self, mode: Mode) -> None:
        """Keep the phase indicator honest for pinned modes so the UI spinner matches what routes
        (the router ignores phase in these modes; only the displayed phase changes). Auto is left
        alone — the shim's per-step classifier drives its phase."""
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

    def arm_read_only(self, reason: str = "") -> object:
        """Arm the read-only guarantee for a gated turn and return its token. The caller keeps the
        token and passes it back to disarm_read_only() on exit. Minting a new token supersedes any
        prior arming, so a turn always owns the live guarantee for its own duration.

        `reason` says which kind of read-only turn this is (see SessionState.read_only_reason); it
        rides with the token so a superseding arming replaces it and a stale disarm can't strand it."""
        token = object()
        self._read_only_token = token
        self._read_only_reason = reason
        return token

    def disarm_read_only(self, token: object) -> None:
        """Clear the read-only guarantee, but only if `token` is still the live one. A disarm from a
        turn that no longer owns the guarantee (superseded, or an out-of-order exit) is a no-op — so
        one turn can never drop another turn's read-only mid-flight."""
        if self._read_only_token is token:
            self._read_only_token = None
            self._read_only_reason = ""

    def arm_web(self) -> object:
        """Arm internet access for THIS turn and return a token, mirroring arm_read_only(). The caller
        keeps the token and passes it to disarm_web() on exit. Minting a new token supersedes any prior
        arming, so a turn always owns the live guarantee for its own duration."""
        token = object()
        self._web_token = token
        return token

    def disarm_web(self, token: object) -> None:
        """Clear internet access, but only if `token` is still the live one — a disarm from a turn that
        no longer owns the guarantee (superseded, or an out-of-order exit) is a no-op."""
        if self._web_token is token:
            self._web_token = None

    def arm_turn_mode(self, mode: Mode) -> object:
        """Pin `mode` for THIS turn and return its token, mirroring arm_read_only(). Every request the
        turn makes then resolves against one mode, whatever the user does to the picker while it
        streams. The caller passes the token back to disarm_turn_mode() on exit."""
        token = object()
        self._turn_mode = mode
        self._turn_mode_token = token
        self._sync_phase(mode)
        return token

    def set_turn_mode(self, mode: Mode) -> None:
        """Re-pin the running turn — Sage escalating a stalled Auto build to Implement, not the user
        picking a mode. Deliberately does NOT touch the user's standing choice: the old
        set_mode-then-restore-on-exit dance moved the picker somewhere the user never put it, and
        then reverted whatever they had changed it to while the turn streamed. A no-op with no pin."""
        if self._turn_mode_token is not None:
            self._turn_mode = mode
            self._sync_phase(mode)

    def disarm_turn_mode(self, token: object) -> None:
        """Drop the pin, but only if `token` is still the live one — a superseded or out-of-order exit
        is a no-op, so one turn can never unpin another. The standing choice takes over again."""
        if self._turn_mode_token is token:
            self._turn_mode_token = None
            self._turn_mode = None
            self._sync_phase(self._mode)

    @property
    def selected_mode(self) -> Mode:
        """The user's standing choice from the picker — what the NEXT turn runs as. Differs from
        snapshot().mode only while a turn is pinned to something else."""
        return self._mode

    def snapshot(self) -> SessionState:
        return SessionState(
            sensitivity_locked=self.locked,
            mode=self._turn_mode if self._turn_mode_token is not None else self._mode,
            phase=self._phase,
            picked_model=self._picked_model,
            web_allowed=self._web_token is not None,
            read_only_turn=self._read_only_token is not None,
            read_only_reason=self._read_only_reason,
        )
