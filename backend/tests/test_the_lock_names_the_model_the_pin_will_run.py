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


# The three pick cases (#286). Dropping the pick made all three the first one, and the panel then
# named the pin's model on a session the pick was deciding.
_BARRED_PICK = "gpt-5.4"        # a real alias, and not in APPROVED
# One per slot, because no single approved alias tells all three cases apart on every row: the
# barred case moves to the sovereign slot OF THE MODE, so an approved pick equal to that slot's
# sovereign is the barred answer wearing a different reason.
_APPROVED_PICK = {"plan": "sov-imp", "implement": "sov-plan", "ask": "sov-imp"}

# Every (slot, pick) the panel can draw, with the model that slot's turn actually runs. Read off
# CATALOG by hand: `plan` and `implement` are pinned to the signing model with no pick, both move to
# their own mode's sovereign slot under a barred one, and an approved pick runs itself.
#
# `ask` answers the same for no pick and for a barred one, and that is a property rather than a gap:
# `catalog.ask` is barred too, so the move follows the mode either way.
_PANEL = {
    ("plan", None): SIGNING,
    ("plan", _BARRED_PICK): "sov-plan",
    ("plan", "sov-imp"): "sov-imp",
    ("implement", None): SIGNING,
    ("implement", _BARRED_PICK): "sov-imp",
    ("implement", "sov-plan"): "sov-plan",
    ("ask", None): "sov-ask",
    ("ask", _BARRED_PICK): "sov-ask",
    ("ask", "sov-imp"): "sov-imp",
}


def _panel_state(slot: str, pick: str | None, approved: frozenset[str] = APPROVED) -> SessionState:
    """`_slot_state` with the `ask` fork `_locked_slot_models` forces, and the pick on the side of it
    the router reads. The Chat fork is where the pick's own field forks too — `_resolve_chat` reads
    `chat_model` and `_resolve_build` reads `picked_model` — which is why the "Ask and Chat" row
    needs no fork of its own."""
    if slot == "ask":
        return _slot_state(slot, approved=approved, chat_thread_id="unarmed", chat_model=pick)
    return _slot_state(slot, approved=approved, picked_model=pick)


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


def test_it_reads_the_pick_because_the_drawer_cannot_change_one_behind_it():
    """The inverse of what this file asserted until #286, and the rename carries the new invariant.

    The old version held that a pick-free answer was the safe one, because the browser keeps this
    answer across pick changes. It bought that by being wrong: an in-session act outranks the signing
    pin one layer below the lock (`_pin_signing` returns an OVERRIDE untouched), so while a pick was
    live the panel named the pin's model and the turn ran somewhere else.

    What makes holding the answer safe now is the drawer rather than the answer. It is the only
    reader, it re-reads on open, its mask puts the picker out of reach while it is open, and
    `set_catalog` clears the pick on every save. #294 is the window that leaves.
    """
    for slot in ASSIGNABLE_SLOTS:
        # One answer per row across the three pick cases is the old invariant exactly. More than one
        # is the new one, and it is read as a set rather than pair by pair because `ask` genuinely
        # answers the same with no pick and a barred one — see `_PANEL`.
        answers = {locked_runs_on(_panel_state(slot, pick), CATALOG)
                   for pick in (None, _BARRED_PICK, _APPROVED_PICK[slot])}
        assert len(answers) > 1, (slot, answers)


def test_the_panel_answer_is_the_turn_the_slot_would_actually_run():
    """The identity, over the whole matrix: three pick cases, three slots, both sides of the Chat
    fork, and an approved set with and without the signing model in it.

    This is the assertion that would have caught #285 and #286 both. `locked_runs_on` is `resolve`
    asked of a slot rather than a turn, so once the caller has forced the mode there is nothing left
    for the two to disagree about — and every defect this file records has been the router quietly
    answering a narrower question than the panel was asking.
    """
    for approved in (APPROVED, APPROVED - {SIGNING}):
        for slot in ASSIGNABLE_SLOTS:
            for pick in (None, _BARRED_PICK, _APPROVED_PICK[slot]):
                state = _panel_state(slot, pick, approved=approved)
                assert locked_runs_on(state, CATALOG) == resolve(state, CATALOG).model, (slot, pick)


def test_each_of_the_three_pick_cases_names_its_own_model():
    """Beside the identity rather than folded into it: the identity alone passes when both sides are
    wrong together, which is exactly the shape #285 had — one function copied into the other's
    caller. These are the literal models, read off the catalog by hand."""
    for (slot, pick), expected in _PANEL.items():
        assert locked_runs_on(_panel_state(slot, pick), CATALOG) == expected, (slot, pick)


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
    provider.assets = [Asset("ds_claims", "claims", tags=["Sensitive"], project="Revenue")]
    provider.roots["ds_claims"] = mount / "claims"
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


def _bind_sensitive(orch: Orchestrator) -> None:
    orch.project(start_preview=False).workspace.update_bindings(
        lambda _: [{"kind": "dataset", "id": "ds_claims", "name": "claims",
                    "display_name": "claims"}])


def test_a_classify_runs_where_the_lock_moves_every_other_turn_of_its_shape(tmp_path, monkeypatch):
    """FOUND IN REVIEW of #373. `_classify_lock` names the model the handoff classifier sends a
    Conversation's digest to, and it has to be the answer `nearest_approved` already gives — the
    sovereign slot for the phase FIRST, then the administrator's group order.

    A rule of its own reading `approved.order[0]` picks `sov-plan` here, while every other locked
    Chat turn and the "runs on" chip an inch away both say `sov-ask`. That is #285 again wearing the
    classifier: two visible answers disagreeing about one lock, and this one disagrees by sending
    rows somewhere the deployment's own sovereign assignment says they should not go.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    project = orch.project(start_preview=False)

    model, refusal = orch._classify_lock(project, "thr_a")

    assert refusal == ""
    assert model == CATALOG.sovereign_ask
    # The premise, asserted rather than assumed: the group's own order would have said otherwise, so
    # a classifier carrying its own rule fails this and nothing else would have caught it.
    assert ORDER[0] != CATALOG.sovereign_ask


def test_a_pick_the_standing_mode_will_not_honour_is_not_reported_on_any_row(tmp_path, monkeypatch):
    """FOUND IN REVIEW of #286. `_resolve_build` reads `picked_model` in Plan and Implement modes
    only, and `ModelControl.set_mode` does not clear a pick — so one made in Plan survives a switch
    to Auto and goes inert. These rows force the mode PER SLOT, so a pick-reading answer reported
    that dead pick on both Build rows while every Auto turn ran the assignments.

    Which is #285's defect wearing the pick instead of the pin: a row naming a model no turn of that
    mode will use. Gated on `selected_mode` at the caller, where the standing mode is known, rather
    than inside `locked_runs_on` — the router answers the question it is asked, and the slot is what
    the caller forced.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    control = orch.project(start_preview=False).control

    # Both modes that honour a pick, because half an allow-set is the half that rots: a gate written
    # `is Mode.PLAN` passes every test that only ever stands in Plan.
    for mode in (Mode.PLAN, Mode.IMPLEMENT):
        control.set_mode(mode)
        control.pick("sov-imp")
        live = orch.sensitivity_state()["slot_models"]
        assert live["plan"] == live["implement"] == "sov-imp", mode

    # The pick is still set — nothing clears it — but no turn of this mode will honour it.
    for mode in (Mode.AUTO, Mode.ASK):
        control.set_mode(mode)
        assert control.snapshot().picked_model == "sov-imp", "the premise: the pick survives"
        inert = orch.sensitivity_state()["slot_models"]
        assert inert["plan"] == inert["implement"] == SIGNING, mode


def test_the_standing_choice_decides_not_the_mode_the_running_turn_is_pinned_to(tmp_path,
                                                                               monkeypatch):
    """`arm_turn_mode` pins `snapshot().mode` to whatever the RUNNING turn routes as, and a mode
    changed mid-turn is recorded against the next one. These rows predict the next turn, so the gate
    reads `selected_mode`.

    Without this the two fields agree everywhere the suite goes and the choice is documentation:
    swapping the gate to `snapshot().mode` passes every other test in this file. The scenario that
    separates them is a person switching to Auto while a Plan turn streams — the rows would go on
    naming a pick the mode they have just left was the only one to honour.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    control = orch.project(start_preview=False).control

    control.set_mode(Mode.PLAN)
    control.pick("sov-imp")
    token = control.arm_turn_mode(Mode.PLAN)
    control.set_mode(Mode.AUTO)

    assert control.snapshot().mode is Mode.PLAN, "the premise: the running turn is still pinned"
    assert control.selected_mode is Mode.AUTO, "and the standing choice has already moved"
    assert orch.sensitivity_state()["slot_models"]["plan"] == SIGNING

    control.disarm_turn_mode(token)


def test_the_chat_pick_is_not_gated_with_the_build_one(tmp_path, monkeypatch):
    """Chat has no modes to make a pick inert, so the gate above would be a rule with no case behind
    it — and applied to `chat_model` it would blank the "Ask and Chat" row for anyone whose standing
    Build mode happened to be Auto. The same asymmetry `set_catalog` already has: it clears
    `picked_model` on a save and leaves `chat_model` standing."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    control = orch.project(start_preview=False).control

    control.set_mode(Mode.AUTO)
    control.pick_chat("sov-imp")
    assert orch.sensitivity_state()["slot_models"]["ask"] == "sov-imp"


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


def test_the_payload_says_whether_a_pick_is_live_for_each_turn_a_row_drives(tmp_path, monkeypatch):
    """FOUND IN REVIEW of #294. `slot_models` says what a slot RUNS; this says whether a PICK is why.

    The panel draws the per-slot answer only where a rule that could have caused the difference is on
    the row (#287), and a live pick is one of them (#286). That gate used to read the browser's own
    mirror of the pick, which `applyModelStatus` writes on user acts and loads — true of every pick a
    person makes, and false of the one #294 is about, where the orchestrator escalates a stalled
    build turn with no human act. So the fact is served here, on the read that surface already takes.

    Booleans rather than model names: `slot_models` already carries what runs, and this payload
    already has `model` and `chat_model` meaning where the lock MOVES a barred turn. Two more model
    names in it would be read as two more of those.

    Not gated on whether the standing mode honours the pick — that is spent inside `slot_models`,
    and asking it twice would be one rule in two places.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    control = orch.project(start_preview=False).control

    quiet = orch.sensitivity_state()
    assert quiet["picked"] is False and quiet["chat_picked"] is False

    control.pick("sov-imp")
    live = orch.sensitivity_state()
    assert live["picked"] is True, "the Build pick, which is the one an escalation moves"
    assert live["chat_picked"] is False, "and it is not the Chat one — two turns, two facts"

    control.pick(None)
    assert orch.sensitivity_state()["picked"] is False


def test_assignment_rows_say_whether_the_pin_decided_this_turn(tmp_path, monkeypatch):
    """`shadowed` is catalog-derived. This field is the server's turn answer: the pin may be present
    and still lose to a pick."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.PLAN)

    quiet = {r["slot"]: r["pin_decided"] for r in orch.model_assignments()["slots"]}
    assert quiet["plan"] is True
    assert quiet["implement"] is False

    project.control.pick("sov-imp")
    picked = {r["slot"]: r["pin_decided"] for r in orch.model_assignments()["slots"]}
    assert picked["plan"] is False
    assert picked["implement"] is False
    # Ask's sentence describes Build Ask, even though its locked select describes Chat.
    assert picked["ask"] is True


@pytest.mark.parametrize("pick", [None, "sov-plan"])
def test_a_pick_on_the_rows_own_approved_model_is_not_a_pin_decision(tmp_path, monkeypatch, pick):
    """The #302 trigger has no model substitution to expose the defeated pin."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    orch.set_catalog(plan="sov-plan")
    control = orch.project(start_preview=False).control
    control.set_mode(Mode.PLAN)
    if pick:
        control.pick(pick)
    row = next(r for r in orch.model_assignments()["slots"] if r["slot"] == "plan")
    assert row["shadowed"] is True
    assert row["model"] == "sov-plan"
    assert row["pin_decided"] is (pick is None)
    assert orch.sensitivity_state()["slot_models"]["plan"] == (pick or SIGNING)


@pytest.mark.parametrize("approved", [APPROVED - {SIGNING}, frozenset()])
def test_a_lock_that_defeats_or_refuses_the_pin_reports_no_pin_decision(tmp_path, monkeypatch, approved):
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    monkeypatch.setattr(orch._resources, "list_alias_groups", lambda: [
        {"name": GROUP, "aliases": [{"id": f"id-{name}"} for name in sorted(approved)]},
    ])
    rows = orch.model_assignments()["slots"]
    assert next(r for r in rows if r["slot"] == "plan")["shadowed"] is True
    assert all(r["pin_decided"] is False for r in rows)
    lock = orch.sensitivity_state()
    assert bool(lock["refusal"]) is (not approved)
    if approved:
        assert lock["slot_models"]["plan"] == "sov-plan"


def test_assignment_pin_decision_uses_the_conversations_sticky_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.record.mark_session_locked("thr_locked")
    monkeypatch.setattr(orch._resources, "list_alias_groups", lambda: [
        {"name": GROUP, "aliases": [{"id": "id-sov-plan"}]},
    ])
    locked = {r["slot"]: r for r in orch.model_assignments("thr_locked")["slots"]}
    fresh = {r["slot"]: r for r in orch.model_assignments("thr_fresh")["slots"]}
    assert locked["plan"]["shadowed"] is fresh["plan"]["shadowed"] is True
    assert locked["plan"]["pin_decided"] is False
    assert fresh["plan"]["pin_decided"] is True


def test_the_slot_answer_reads_the_snapshot_it_is_handed_and_not_a_fresh_one(tmp_path, monkeypatch):
    """FOUND IN REVIEW. One payload used to take three separate `control.snapshot()` reads.

    `sensitivity_state` reports `picked`/`chat_picked` beside `slot_models`, and the drawer's row
    draws that answer only where the flag says a pick is why (#294). Read apart, an escalation
    landing between them ships `picked: False` over a `slot_models` that has already moved — the
    gate shut over a moved answer, which is #294 itself in a one-tick window. The payload has to be
    a photograph of one moment, so the snapshot is taken once and handed down.

    Asserted by taking the snapshot away: the live one is made to RAISE for the duration, so any
    read of its own is a red rather than a silently different answer. Handing it a pick the control
    does not hold is not enough on its own — the loop `replace`s almost every field it reads, so a
    re-read there changes nothing observable, and a test written that way passes over it. This
    covers both the pick line, where a raise propagates, and the loop, where the method's own
    `except` would turn one into a missing slot.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind_sensitive(orch)
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.PLAN)
    # The approved set alone is wanted here, for a panel read and not a turn — so it says what the
    # panel says rather than falling through the refusal a nameless turn now gets (ADR-0057).
    approved, _ = orch._sensitivity_for_turn(project, None, is_a_turn=False)

    assert project.control.snapshot().picked_model is None, "the premise: nothing is picked live"
    handed = replace(project.control.snapshot(), picked_model="sov-imp")

    def gone():
        raise AssertionError("read its own snapshot instead of the one it was handed")

    monkeypatch.setattr(project.control, "snapshot", gone)
    answered = orch._locked_slot_models(project, approved, handed)

    assert sorted(answered) == sorted(ASSIGNABLE_SLOTS), "a slot that raised would simply be absent"
    assert answered["plan"] == answered["implement"] == "sov-imp"
