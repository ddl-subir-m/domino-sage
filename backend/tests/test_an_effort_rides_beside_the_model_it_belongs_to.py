"""A model assignment carries an effort beside the model (ADR-0049, #281).

An effort is half of an assignment, not a Chat setting and not one Build-wide level: a single
Build-wide effort cannot say "think hard while planning, cheaply while implementing", which is the
entire reason the slots are separate. So it is carried per slot, persisted per slot, and reported
per slot.

The two rules these tests exist to hold, because both are the kind that reads as an implementation
detail right up until a turn 400s:

- **A stored effort is always one the slot's model accepts**, measured per alias (#280) rather than
  guessed from its name. Two aliases on this gateway validate the field and answer 400 to a value
  they do not list, so a bad effort saved here is not a cosmetic wrong — the next build dies on its
  first turn.
- **An effort can be cleared without its model**, or it is a setting that can never be undone. That
  is the same defect the model's own clear path (`test_clearing_a_slot_puts_the_deployment_default_back`)
  exists to prevent, one field further in.

Sending the effort on a Build turn is #282's and drawing the control is #283's. Nothing here asserts
about either.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import ModelCatalog

# `gpt-5.4` and `gemini-3.7-flash` are the two aliases the probe found that validate the field, and
# they do not take the same levels — `max` is Gemini's and gpt-5.4 refuses it. `sonnet` accepts none.
#
# `reasoning_efforts` is passed rather than left to default, because provider.py fills it on both of
# its LlmAlias construction sites and a fixture that leaves it empty is a record no gateway produces
# — which is what let a fallback that only fires on the empty list look harmless.
ALIASES = [
    LlmAlias("id-gpt", "gpt-5.4", "GPT-5.4", None, ["chat"], {},
             reasoning_efforts=["none", "low", "medium", "high", "xhigh"]),
    LlmAlias("id-gemini", "gemini-3.7-flash", "Gemini 3.7 Flash", None, ["chat"], {},
             reasoning_efforts=["low", "medium", "high", "max"]),
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {}),
]

CATALOG = ModelCatalog(
    sovereign_plan="sonnet", sovereign_implement="sonnet", sovereign_ask="sonnet",
    plan="gpt-5.4", implement="sonnet", ask="sonnet",
)


def _template(tmp_path: Path) -> Path:
    t = tmp_path / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Template rules\n")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES)),
    )
    orch.project(start_preview=False)
    return orch


def _saved(orch: Orchestrator) -> dict:
    return orch.project().record.read_catalog_overrides()


def _slot(orch: Orchestrator, name: str) -> dict:
    return next(r for r in orch.model_assignments()["slots"] if r["slot"] == name)


# ---- the effort reaches the catalog, and the file --------------------------------------------


def test_an_effort_is_saved_beside_the_model_it_was_picked_for(tmp_path):
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "max"})
    assert orch.project().shim.catalog.plan == "gemini-3.7-flash"
    assert orch.project().shim.catalog.plan_effort == "max"
    assert _saved(orch) == {"plan": {"model": "gemini-3.7-flash", "effort": "max"}}


def test_an_effort_survives_a_restart(tmp_path):
    # The assignment is the Project's and is shared with everyone in it, so it has to reach the file
    # and not only the live catalog — the same reason clearing does.
    orch = _orch(tmp_path)
    orch.set_catalog(implement={"model": "gpt-5.4", "effort": "low"})
    assert _orch(tmp_path).project().shim.catalog.implement_effort == "low"


def test_each_slot_carries_its_own_effort(tmp_path):
    """The whole reason it is per slot: plan thinks hard, implement runs cheap. One Build-wide
    level cannot express this, which is what ADR-0049 rejected."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "max"},
                     implement={"model": "gemini-3.7-flash", "effort": "low"})
    catalog = orch.project().shim.catalog
    assert (catalog.plan_effort, catalog.implement_effort) == ("max", "low")


# ---- the three cases, one field further in ----------------------------------------------------


def test_an_effort_can_be_cleared_without_clearing_its_model(tmp_path):
    """Present-and-empty takes the effort back and leaves the model. Without this the effort is a
    setting that can never be undone — the defect the model's own clear path exists to prevent."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "max"})
    orch.set_catalog(plan={"effort": None})
    catalog = orch.project().shim.catalog
    assert (catalog.plan, catalog.plan_effort) == ("gemini-3.7-flash", None)
    assert _saved(orch) == {"plan": {"model": "gemini-3.7-flash", "effort": None}}


def test_a_key_nobody_mentioned_is_not_a_key_somebody_cleared(tmp_path):
    """The row has two controls, and saving one must not revert the other — the same distinction
    the drawer already needs between its three rows, now inside one of them."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "high"})
    orch.set_catalog(plan={"effort": "low"})          # model absent
    assert orch.project().shim.catalog.plan == "gemini-3.7-flash"
    orch.set_catalog(plan={"model": "gpt-5.4"})       # effort absent
    assert orch.project().shim.catalog.plan_effort == "low"


def test_clearing_the_slot_takes_the_effort_with_it(tmp_path):
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "max"})
    orch.set_catalog(plan=None)
    catalog = orch.project().shim.catalog
    assert (catalog.plan, catalog.plan_effort) == ("gpt-5.4", None)
    assert _saved(orch) == {}


def test_an_effort_alone_leaves_the_model_following_the_default(tmp_path):
    """A row may carry an effort while its model still follows the deployment default, so
    `assigned` — which has only ever been the model's word — must stay false for it."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"effort": "low"})
    row = _slot(orch, "plan")
    assert (row["model"], row["assigned"]) == ("gpt-5.4", False)
    assert row["effort"] == "low"


# ---- validity is per alias, and is measured ---------------------------------------------------


def test_an_effort_the_slots_model_does_not_accept_is_refused_on_save(tmp_path):
    """`max` is Gemini's top level and gpt-5.4 answers 400 to it. Refused on the way in rather than
    tolerated on the way out: the write lands before the catalog is rebuilt, so a bad effort saved
    here is a durable brick that kills the first turn of every later build."""
    orch = _orch(tmp_path)
    with pytest.raises(ValueError, match="max"):
        orch.set_catalog(plan={"model": "gpt-5.4", "effort": "max"})
    assert _saved(orch) == {}


def test_the_refusal_names_the_levels_the_model_does_take(tmp_path):
    orch = _orch(tmp_path)
    with pytest.raises(ValueError) as e:
        orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "xhigh"})
    assert "low, medium, high, max" in str(e.value)


def test_an_alias_that_discards_the_field_accepts_no_effort_at_all(tmp_path):
    """Not a name match: sonnet is excluded because the probe watched it swallow a nonsense value,
    and gemini — which the old `gpt-5`-in-the-name heuristic also excluded — is not."""
    orch = _orch(tmp_path)
    with pytest.raises(ValueError, match="no effort"):
        orch.set_catalog(ask={"model": "sonnet", "effort": "low"})


def test_the_effort_is_checked_against_the_model_the_slot_will_actually_run(tmp_path):
    """With the model absent the slot falls back to the deployment default, and the effort is legal
    against THAT — not against whatever the row used to hold. `CATALOG.implement` is sonnet."""
    orch = _orch(tmp_path)
    with pytest.raises(ValueError, match="sonnet"):
        orch.set_catalog(implement={"effort": "low"})


def test_changing_the_model_drops_an_effort_the_new_one_will_not_take(tmp_path):
    """The other half of the invariant. Refusing here would make a slot that carries an effort
    impossible to retarget, so the effort goes instead — it belonged to the model that just left."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "max"})
    orch.set_catalog(plan="sonnet")
    catalog = orch.project().shim.catalog
    assert (catalog.plan, catalog.plan_effort) == ("sonnet", None)


def test_an_effort_the_new_model_does_take_rides_along(tmp_path):
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "low"})
    orch.set_catalog(plan="gpt-5.4")
    assert orch.project().shim.catalog.plan_effort == "low"


def test_a_sovereign_slot_carries_no_effort_and_the_refusal_says_why(tmp_path):
    """The panel draws its rows `for slot in ASSIGNABLE_SLOTS`, so nothing in the UI can reach this
    and the only caller who can is working the API — with no panel to learn the rule from. The
    sentence is the whole value of the refusal, so it carries the fact and not just the verdict."""
    orch = _orch(tmp_path)
    with pytest.raises(ValueError) as e:
        orch.set_catalog(sovereign_plan={"model": "gemini-3.7-flash", "effort": "low"})
    assert "sovereign_plan" in str(e.value)
    assert "the router reads no sovereign slot" in str(e.value)


def test_a_malformed_assignment_is_refused_rather_than_written(tmp_path):
    orch = _orch(tmp_path)
    with pytest.raises(ValueError):
        orch.set_catalog(plan={"model": "gpt-5.4", "reasoning": "high"})
    with pytest.raises(ValueError):
        orch.set_catalog(plan=["gpt-5.4"])
    assert _saved(orch) == {}


# ---- migration: a bare string is an assignment written before efforts shipped ------------------


def test_a_bare_string_on_disk_means_this_model_no_effort(tmp_path):
    """The shape every existing `model_overrides.json` is in. It means what that assignment has
    always done, so the migration preserves the behaviour rather than guessing at it."""
    orch = _orch(tmp_path)
    record = orch.project().record
    record.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    record.catalog_overrides_path.write_text('{"plan": "gemini-3.7-flash"}')
    assert record.read_catalog_overrides() == {"plan": {"model": "gemini-3.7-flash",
                                                        "effort": None}}
    catalog = _orch(tmp_path).project().shim.catalog
    assert (catalog.plan, catalog.plan_effort) == ("gemini-3.7-flash", None)


def test_nothing_rewrites_the_file_on_read(tmp_path):
    """Migration is a read-side concern only: no pass is run over the volume, and the old shape
    stays on disk until the next save of that row."""
    orch = _orch(tmp_path)
    record = orch.project().record
    record.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    record.catalog_overrides_path.write_text('{"plan": "gemini-3.7-flash"}')
    record.read_catalog_overrides()
    assert record.catalog_overrides_path.read_text() == '{"plan": "gemini-3.7-flash"}'


def test_the_new_shape_lands_the_next_time_that_row_is_saved(tmp_path):
    orch = _orch(tmp_path)
    record = orch.project().record
    record.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    record.catalog_overrides_path.write_text('{"plan": "gemini-3.7-flash"}')
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"effort": "max"})
    assert _saved(orch) == {"plan": {"model": "gemini-3.7-flash", "effort": "max"}}


# ---- what the surfaces report -----------------------------------------------------------------


def test_a_slot_row_reports_its_effort_and_the_default_it_reverts_to(tmp_path):
    """The panel cannot derive the second from the first, for the same reason it cannot for the
    model: once a row is assigned, the catalog it is showing no longer holds what "Use the default"
    goes back to."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "max"})
    row = _slot(orch, "plan")
    assert (row["effort"], row["default_effort"]) == ("max", None)


def test_an_alias_row_carries_the_efforts_it_accepts(tmp_path):
    """The join lives here or it is a second copy of the per-alias table on the panel's side."""
    orch = _orch(tmp_path)
    by_name = {a["name"]: a for a in orch.model_assignments()["aliases"]}
    assert by_name["gemini-3.7-flash"]["reasoning_efforts"] == ["low", "medium", "high", "max"]
    assert by_name["sonnet"]["reasoning_efforts"] == []


def test_an_empty_effort_list_is_a_verdict_and_is_not_recomputed(tmp_path):
    """Empty does not mean "nobody filled it in" — `alias_reasoning_efforts` returns it when the
    gateway's published enum and the measured table have nothing in common, which is the exact case
    it exists for. A fallback behind this field would answer the full measured list there and put
    back the levels the narrowing just refused."""
    aliases = [LlmAlias("id-gemini", "gemini-3.7-flash", "Gemini 3.7 Flash", None, ["chat"], {},
                        reasoning_efforts=[])]
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code", template=_template(tmp_path),
        gateway=FakeGatewayClient(), catalog=CATALOG, project_id="Sage",
        resources=FakeResourceProvider(aliases),
    )
    orch.project(start_preview=False)
    row = next(a for a in orch.model_assignments()["aliases"] if a["name"] == "gemini-3.7-flash")
    assert row["reasoning_efforts"] == []


def test_the_status_carries_each_slots_effort_beside_its_model(tmp_path):
    """Flat `<slot>_effort` keys, so every reader already keyed on `catalog.plan` keeps reading it."""
    orch = _orch(tmp_path)
    orch.set_catalog(implement={"model": "gpt-5.4", "effort": "low"})
    catalog = orch.project().status()["model"]["catalog"]
    assert catalog["implement"] == "gpt-5.4"
    assert catalog["implement_effort"] == "low"
    assert catalog["plan_effort"] is None


def test_a_slot_holding_neither_shape_is_dropped_rather_than_raised(tmp_path):
    """`model_overrides.json` is committed and shared with everyone in the Project, so a hand-edit
    or a bad merge is reachable. A read that raises takes out every resolve of this project's
    catalog; dropping the row costs one assignment and leaves the deployment default in place."""
    orch = _orch(tmp_path)
    record = orch.project().record
    record.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    record.catalog_overrides_path.write_text('{"plan": null, "implement": "gpt-5.4"}')
    assert record.read_catalog_overrides() == {"implement": {"model": "gpt-5.4", "effort": None}}
    catalog = _orch(tmp_path).project().shim.catalog
    assert (catalog.plan, catalog.implement) == ("gpt-5.4", "gpt-5.4")


def test_a_model_that_is_not_text_is_refused_rather_than_crashing(tmp_path):
    """`{"model": 123}` is reachable over the wire. Without this it reaches `reasoning_efforts_for`,
    which calls `.rsplit` on it, and the AttributeError is a 500 — the answer this path exists to
    keep off the wire."""
    orch = _orch(tmp_path)
    with pytest.raises(ValueError, match="must be text"):
        orch.set_catalog(plan={"model": 123})
    with pytest.raises(ValueError, match="must be text"):
        orch.set_catalog(plan={"model": "gpt-5.4", "effort": ["low"]})
    assert _saved(orch) == {}


def test_an_effort_on_a_sovereign_row_in_the_file_is_ignored_not_raised(tmp_path):
    """`set_catalog` refuses to write one, but this file is committed and shared — a hand-edit or a
    bad merge reaches it. Only the three assignable slots have an `<slot>_effort` on ModelCatalog,
    so applying one would raise TypeError out of `replace()` on EVERY resolve of this project's
    catalog: the same durable brick the unknown-slot guard is written against, reached through the
    file instead of through the route."""
    orch = _orch(tmp_path)
    record = orch.project().record
    record.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    record.catalog_overrides_path.write_text(
        '{"sovereign_plan": {"model": "sonnet", "effort": "low"}}')
    catalog = _orch(tmp_path).project().shim.catalog
    assert catalog.sovereign_plan == "sonnet"
    assert not hasattr(catalog, "sovereign_plan_effort")


def test_an_assignment_missing_a_half_is_filled_in_on_read(tmp_path):
    """`{"model": "gpt-5.4"}` with no effort key is the most natural thing a person hand-editing
    this file writes, and it is the shape the docstring describes. `_merge_assignment` subscripts
    both halves, so a missing key was a KeyError — a 500 out of the save route that exists to
    answer 400."""
    orch = _orch(tmp_path)
    record = orch.project().record
    record.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    record.catalog_overrides_path.write_text('{"plan": {"model": "gpt-5.4"}}')
    assert record.read_catalog_overrides() == {"plan": {"model": "gpt-5.4", "effort": None}}
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash"})
    assert orch.project().shim.catalog.plan == "gemini-3.7-flash"


def test_a_key_that_is_not_a_slot_is_ignored_rather_than_bricking_the_project(tmp_path):
    """`replace()` raises TypeError on an unknown field, and this read runs on every resolve of the
    project's catalog — so one bad key in a committed file makes the Project unopenable. The same
    refusal `set_catalog` makes on the route, reached through the file."""
    orch = _orch(tmp_path)
    record = orch.project().record
    record.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    record.catalog_overrides_path.write_text(
        '{"sovereign_plna": "sonnet", "implement": "gpt-5.4"}')
    catalog = _orch(tmp_path).project().shim.catalog
    assert catalog.implement == "gpt-5.4"


def test_an_effort_stranded_on_a_sovereign_row_is_not_written_back(tmp_path):
    """The refusal fires on an effort that ARRIVES. One already on the row would otherwise ride
    through a model-only save, so the path that refuses the setting would be the path re-persisting
    it."""
    orch = _orch(tmp_path)
    record = orch.project().record
    record.catalog_overrides_path.parent.mkdir(parents=True, exist_ok=True)
    record.catalog_overrides_path.write_text(
        '{"sovereign_plan": {"model": "sonnet", "effort": "low"}}')
    orch = _orch(tmp_path)
    orch.set_catalog(sovereign_plan={"model": "gemini-3.7-flash"})
    assert _saved(orch) == {"sovereign_plan": {"model": "gemini-3.7-flash", "effort": None}}


def test_a_whole_row_put_that_echoes_the_effort_drops_it_rather_than_refusing(tmp_path):
    """What #283's drawer will send on a model change: the new model plus the effort the row was
    already showing. That is the person changing the model, not asking for a level, so the rule is
    the documented drop — and keying on the key's presence would answer it with a hard 400 and make
    the contract depend on the client sending `effort` only when it changed."""
    orch = _orch(tmp_path)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "max"})
    orch.set_catalog(plan={"model": "sonnet", "effort": "max"})
    catalog = orch.project().shim.catalog
    assert (catalog.plan, catalog.plan_effort) == ("sonnet", None)
    # A CHANGED effort the new model refuses is still the person asking, and still a 400.
    with pytest.raises(ValueError, match="no effort"):
        orch.set_catalog(plan={"model": "sonnet", "effort": "high"})
