"""The lock's per-slot sentence has to name the model the turn actually runs on (#285).

Two functions were answering one question and disagreeing. `resolve` applies the signing pin under
the lock (`_lock_sensitivity(_pin_signing(...))`, ADR-0032/ADR-0043), so when a signing assignment
holds and the signing model is itself approved, every Build turn runs it whatever the mode says.
`nearest_approved` never applied the pin — which is right for the question it answers, "where does
the lock MOVE a barred turn", and wrong for the model panel's, "what will this row's turn run".

So the panel said `gpt-5.4 isn't approved, so runs sov-plan` on every row holding an unapproved
model, while every one of those turns ran `gemini-3.7-flash`.

The agreement assertion is the point of most of these: whatever the fix, the panel's answer and the
router's must not be able to drift apart again, so each case asserts both.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sage.assets.provider import Asset, FakeAssetProvider
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.llm_router import locked_runs_on, nearest_approved, resolve
from sage.router.models import ASSIGNABLE_SLOTS, Mode, ModelCatalog, Phase, SessionState

SIGNING = "gemini-3.7-flash"   # the one member of SIGNS_TOOL_CALLS
GROUP = "sensitive-approved"

# The measured shape from #285: a signing model in the Implement slot, two unapproved vendor models
# in the other two, and three sovereign slots the lock would otherwise fall back to.
CATALOG = ModelCatalog(
    sovereign_plan="sov-plan",
    sovereign_implement="sov-imp",
    sovereign_ask="sov-ask",
    plan="gpt-5.4",
    implement=SIGNING,
    ask="sonnet",
)
ORDER = ("sov-plan", "sov-imp", "sov-ask", SIGNING)
APPROVED = frozenset(ORDER)


def _slot_state(slot: str, approved: frozenset[str] = APPROVED, **over) -> SessionState:
    """Exactly as `_locked_slot_models` builds it: the mode forced to the slot, the phase with it."""
    return SessionState(
        Mode(slot),
        Phase.IMPLEMENT if slot == "implement" else Phase.PLAN,
        approved_models=approved,
        approved_order=ORDER,
        **over,
    )


# --- the router's own answer -----------------------------------------------------------------

def test_the_pin_decides_every_row_while_its_own_model_is_approved():
    """The reported defect, at the router. All three rows, not one: the pin reads every assignable
    slot, so one signing assignment takes the whole Build session (ADR-0032)."""
    for slot in ASSIGNABLE_SLOTS:
        state = _slot_state(slot)
        assert locked_runs_on(state, CATALOG) == SIGNING
        assert locked_runs_on(state, CATALOG) == resolve(state, CATALOG).model
        # And the narrower question still answers narrowly, which is why both functions stay.
        assert nearest_approved(state, CATALOG) != SIGNING


def test_the_lock_still_outranks_the_pin_when_the_signing_model_is_barred():
    """ADR-0043 over ADR-0032: a 400 is an outage and a leak is not recoverable. Drop the signing
    model out of the approved set and the answer goes back to the sovereign slot for the mode."""
    approved = APPROVED - {SIGNING}
    for slot, expected in (("plan", "sov-plan"), ("implement", "sov-imp"), ("ask", "sov-ask")):
        state = _slot_state(slot, approved=approved)
        assert locked_runs_on(state, CATALOG) == expected
        assert locked_runs_on(state, CATALOG) == resolve(state, CATALOG).model


def test_it_reads_no_pick_so_one_answer_can_be_held_across_one():
    """The hazard `nearest_approved`'s docstring names, and the reason this is not simply `resolve`:
    the browser holds this answer across pick changes (`store.js` does not re-read on one), so an
    answer that could BE the pick would go stale the moment somebody picked a barred model."""
    for slot in ASSIGNABLE_SLOTS:
        answer = locked_runs_on(_slot_state(slot), CATALOG)
        for pick in (None, "sov-ask", "gpt-5.4", SIGNING):
            assert locked_runs_on(_slot_state(slot, picked_model=pick), CATALOG) == answer
            assert locked_runs_on(
                _slot_state(slot, chat_model=pick), CATALOG) == answer


def test_a_chat_turn_has_no_pin_to_apply():
    """The pin is a Build rule — Chat has no phases to hold still — so the fork is mirrored here
    rather than flattened. Without that, Chat's row would name the signing model it never runs."""
    state = replace(_slot_state("ask"), chat_thread_id="t1")
    assert locked_runs_on(state, CATALOG) == "sov-ask"
    assert locked_runs_on(state, CATALOG) == resolve(state, CATALOG).model


def test_nothing_approved_is_a_refusal_rather_than_a_model():
    """Same edge `_lock_sensitivity` and `nearest_approved` keep: the orchestrator refuses the turn
    before it starts, and a router that answered here would fail open at the one point that exists
    to fail closed."""
    for approved in (frozenset(), None):
        with pytest.raises(ValueError, match="empty approved set"):
            locked_runs_on(_slot_state("plan", approved=approved), CATALOG)


# --- and the panel's, end to end -------------------------------------------------------------

def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _assets(tmp: Path) -> FakeAssetProvider:
    mount = tmp / "mnt" / "data"
    provider = FakeAssetProvider(root=mount)
    provider.assets = [
        Asset("ds_claims", "claims", tags=["Sensitive"], project="Revenue",
              mount_path=str(mount / "claims")),
    ]
    return provider


class _ApprovesTheSigningModel(FakeResourceProvider):
    """A gateway group holding the three sovereign slots AND the signing model — the shape where
    the pin survives the lock, and so the only one where the two answers differ."""

    def list_llm_aliases(self):
        return [LlmAlias(id=f"id-{name}", name=name, display_name=name) for name in ORDER]

    def list_alias_groups(self):
        return [{"name": GROUP, "aliases": [{"id": f"id-{name}"} for name in ORDER]}]


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=CATALOG,
        project_id="Sage",
        assets=_assets(tmp),
        resources=_ApprovesTheSigningModel(),
        control_plane=None,
        domino_project_name="Revenue",
    )


def test_the_ask_row_is_answered_for_chat_because_the_pin_never_reaches_it(tmp_path, monkeypatch):
    """The one row with two turns behind it. `SLOTS` labels it "Ask and Chat" and `_resolve_chat`
    returns `catalog.ask`, so the row drives Build's Ask mode AND every Chat turn — and the pin takes
    only the first of those (ADR-0032). Answered pin-aware like its neighbours, the row read
    `sonnet isn't approved, so this runs gemini-3.7-flash` while the Chat chip an inch away read
    `sov-ask` off `chat_model`: two visible controls disagreeing about one row.

    Chat's half wins the select because it is the half this row's own assignment still decides. Under
    a held pin the row has stopped governing Build at all, which is what `ShadowedSlot`'s sentence
    for `ask` says out loud ("this model only runs in Chat"), and Build's own account is complete two
    controls over — the composer chip names the holder and `pinWhy` says why. Answer the row for
    Build instead and Chat is contradicted with nothing on screen to correct it.

    Costs nothing when no pin holds: `_lock_preferences` prefers `sovereign_ask` for an Ask turn and
    for a Chat turn alike, so the two answers differ only where the pin is what separates them.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.project(start_preview=False).workspace.update_bindings(
        lambda _: [{"kind": "dataset", "id": "ds_claims", "name": "claims",
                    "display_name": "claims"}])

    state = orch.sensitivity_state()

    assert state["slot_models"]["ask"] == state["chat_model"] == "sov-ask"
    # And the two rows the pin really does take are unaffected by the exception made for this one.
    assert state["slot_models"]["plan"] == state["slot_models"]["implement"] == SIGNING


def test_the_panel_is_told_the_model_the_pin_will_run_on_every_row(tmp_path, monkeypatch):
    """The whole read surface of the defect. `slot_models` is what the drawer substitutes into
    "<model> isn't approved, so runs <this>", and before the fix it named the sovereign slot on the
    Plan and Ask rows while all three turns ran the signing model."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch.project(start_preview=False).workspace.update_bindings(
        lambda _: [{"kind": "dataset", "id": "ds_claims", "name": "claims",
                    "display_name": "claims"}])

    state = orch.sensitivity_state()

    assert state["locked"] is True
    # `ask` is the exception and has its own test below — the pin does not reach Chat.
    assert state["slot_models"]["plan"] == SIGNING
    assert state["slot_models"]["implement"] == SIGNING
    assert set(state["slot_models"]) == set(ASSIGNABLE_SLOTS)
