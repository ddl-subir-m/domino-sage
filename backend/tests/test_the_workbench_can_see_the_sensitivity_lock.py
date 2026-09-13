"""What the Workbench is allowed to draw about the sensitivity lock (ADR-0043).

The lock itself is enforced in three places that have nothing to do with these tests — the router,
the preview proxy and the publish guard — and `test_sensitivity_gate.py` covers the gate they all
read. This file covers the READ SURFACE the panel, the pickers and the promote confirm are built
from, because a badge that says one thing while the router does another is its own kind of failure:
the person is told a promise that is not the one being kept.

Three claims, and each has a case that would be easy to get wrong:

  - a Dataset is `declared` only when the deployment opted IN, because Domino tags are freeform and
    a customer may have tagged something `sensitive` years ago for their own reasons;
  - `project_owned` says whether the tick may be offered, and it is FALSE for a Dataset shared in;
  - the declaration is refused server-side for one this Project does not own, so the guard survives
    a caller that never drew the form.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sage.orchestrator.service as service_module
from sage.assets.provider import Asset, FakeAssetProvider
from sage.orchestrator.service import NotThisProjectsDataset, Orchestrator
from sage.provision.domino import FakeControlPlane
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import ModelCatalog

# The group the FakeResourceProvider's own `list_alias_groups` answers for, holding `qwen-2-5`.
GROUP = "sensitive-approved"
APPROVED = "qwen-2-5"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan=APPROVED, sovereign_implement=APPROVED,
                        sovereign_ask=APPROVED, plan="gpt-5.4", implement="gpt-5.4", ask="gpt-5.4")


def _assets(root: Path) -> FakeAssetProvider:
    """Three Datasets: one declared and ours, one ours and undeclared, one somebody else's.

    Written out rather than taken from the fake's own seeds because the third row is the whole point
    of `project_owned`, and a fixture where every Dataset belongs to this Project could not fail.
    """
    mount = root / "mounts"
    (mount / "claims").mkdir(parents=True)
    (mount / "logs").mkdir(parents=True)
    (mount / "shared").mkdir(parents=True)
    provider = FakeAssetProvider(root=mount)
    # Assigned AFTER construction, because `__post_init__` seeds its own three Datasets over
    # whatever was handed in — the seeds are for a local demo run, and this fixture is not one.
    provider.assets = [
        Asset("ds_claims", "claims", tags=["Sensitive"], project="Revenue",
              mount_path=str(mount / "claims")),
        Asset("ds_logs", "logs", tags=["curated"], project="Revenue",
              mount_path=str(mount / "logs")),
        Asset("ds_shared", "shared", tags=["sensitive"], project="Platform",
              mount_path=str(mount / "shared")),
    ]
    return provider


def _orch(tmp: Path, control=None, resources=None, catalog=None) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=catalog or _catalog(),
        project_id="Sage",
        assets=_assets(tmp),
        resources=resources or FakeResourceProvider(),
        control_plane=control,
        domino_project_name="Revenue",
    )


def _bind(orch: Orchestrator, entries: list[dict]) -> None:
    """Write the app's Binding manifest directly. `bind_dataset` would arrive at the same file
    through three more layers, and none of them is what these tests are about."""
    orch.project(start_preview=False).workspace.update_bindings(lambda _: entries)


def _dataset_binding(dataset_id: str, name: str) -> dict:
    return {"kind": "dataset", "id": dataset_id, "name": name, "display_name": name}


# --- What a Dataset row carries -------------------------------------------------------------------

def test_no_dataset_is_declared_while_the_deployment_has_not_opted_in(tmp_path, monkeypatch):
    """The `sensitive` tag is pre-existing and freeform. A customer who applied it years ago, for
    their own reasons, must not open Sage and find their Datasets badged by a feature nobody turned
    on — and the badge promises a narrowing that is not happening."""
    monkeypatch.delenv("SAGE_SENSITIVE_MODEL_GROUP", raising=False)
    rows = _orch(tmp_path).list_assets()

    assert [r["name"] for r in rows if r["declared"]] == []


def test_the_tag_is_read_case_insensitively_once_the_group_is_configured(tmp_path, monkeypatch):
    """`Sensitive` on the claims Dataset. Somebody typed it into a free field, and case deciding
    governance would be a trap with no upside."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    rows = {r["name"]: r for r in _orch(tmp_path).list_assets()}

    assert rows["claims"]["declared"] is True
    assert rows["shared"]["declared"] is True
    assert rows["logs"]["declared"] is False


def test_a_taxonomy_tagged_dataset_is_badged_though_the_old_tag_map_is_empty(tmp_path, monkeypatch):
    """The Tags panel on a Dataset's own page writes Domino's Taxonomy API, not the datasetrw
    snapshot-tag map this listing reads, so `tags` stays empty while the chip is plainly on screen.

    LIVE 2026-09-11: `drug-analysis-cbv23` tagged `sensitive: sensitive` came back here with
    `tags: []` and drew no chip, while the lock behind it read the other system and fired. A badge
    that disagrees with the lock is the failure this file exists to catch.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    orch._assets.taxonomy = {"ds_logs": ["sensitive"]}

    rows = {r["name"]: r for r in orch.list_assets()}

    assert rows["logs"]["declared"] is True
    assert rows["claims"]["declared"] is True


def test_a_row_says_whether_this_project_owns_the_dataset(tmp_path, monkeypatch):
    """The one condition on offering the tick. A Domino tag marks a whole Dataset snapshot, so
    ticking a box on a Dataset shared in from `Platform` would lock that Project's work from inside
    a form that never named it."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    rows = {r["name"]: r for r in _orch(tmp_path).list_assets()}

    assert rows["claims"]["project_owned"] is True
    assert rows["logs"]["project_owned"] is True
    assert rows["shared"]["project_owned"] is False


def test_ownership_is_answered_even_with_the_feature_off(tmp_path, monkeypatch):
    """`declared` is gated on the opt-in and `project_owned` is not: it is a fact about the Dataset
    rather than a claim about what Sage will do, and the two are read by different callers."""
    monkeypatch.delenv("SAGE_SENSITIVE_MODEL_GROUP", raising=False)
    rows = {r["name"]: r for r in _orch(tmp_path).list_assets()}

    assert rows["claims"]["project_owned"] is True
    assert rows["shared"]["project_owned"] is False


# --- What the lock state answers -------------------------------------------------------------------

def test_the_lock_state_is_off_and_reads_nothing_without_the_group(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGE_SENSITIVE_MODEL_GROUP", raising=False)
    orch = _orch(tmp_path)
    _bind(orch, [_dataset_binding("ds_claims", "claims")])

    assert orch.sensitivity_state() == {
        "enabled": False, "locked": False, "group": "",
        "approved": [], "datasets": [], "refusal": None, "model": None, "chat_model": None,
        "slot_models": {}, "reason": "",
    }


def test_the_lock_is_enabled_but_not_holding_without_a_declared_binding(tmp_path, monkeypatch):
    """The ordinary state of an opted-in deployment. `enabled` and `locked` are separate because the
    picker draws nothing for the first and everything for the second."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind(orch, [_dataset_binding("ds_logs", "logs")])

    state = orch.sensitivity_state()

    assert state["enabled"] is True
    assert state["locked"] is False
    assert state["datasets"] == []


def test_a_bound_declared_dataset_names_itself_and_the_approved_models(tmp_path, monkeypatch):
    """What the picker and the notice are drawn from: which models survive, and which Dataset is the
    reason. The Dataset is named because an explanation that cannot be checked is not one."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind(orch, [_dataset_binding("ds_claims", "claims"),
                 {"kind": "llm_alias", "id": "f-gpt54", "name": "gpt-5.4",
                  "display_name": "gpt-5.4"}])

    state = orch.sensitivity_state()

    assert state["locked"] is True
    assert state["group"] == GROUP
    assert state["approved"] == [APPROVED]
    assert state["datasets"] == ["claims"]
    assert state["refusal"] is None
    # And WHERE the lock moves a barred turn to, worked out by the router rather than guessed at in
    # the browser: the chip and the notice both name it, and a second copy of `nearest_approved` in
    # JavaScript is how a label comes to promise a model that will not run (ADR-0043). Both slots
    # are the same alias in this catalog, which is the ordinary shape.
    assert state["model"] == APPROVED
    assert state["chat_model"] == APPROVED


def test_the_named_model_follows_the_administrators_ordering(tmp_path, monkeypatch):
    """The end of the chain the ordering travels: /api/alias-groups -> ApprovedModels.order ->
    SessionState -> `_nearest_approved` -> the name the chip reads.

    Sovereign slots deliberately unapproved, which is the only shape where the ordering decides.
    Naming a model without it would have been naming an arbitrary one out loud — `min()` would
    answer `alpha` here, and nobody chose `alpha`.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)

    class TwoApproved(FakeResourceProvider):
        def list_llm_aliases(self):
            return [LlmAlias(id="id-zeta", name="zeta", display_name="zeta"),
                    LlmAlias(id="id-alpha", name="alpha", display_name="alpha")]

        def list_alias_groups(self):
            return [{"name": GROUP, "aliases": [{"id": "id-zeta"}, {"id": "id-alpha"}]}]

    vendor = ModelCatalog(sovereign_plan="", sovereign_implement="", sovereign_ask="",
                          plan="gpt-5.4", implement="gpt-5.4", ask="gpt-5.4")
    orch = _orch(tmp_path, resources=TwoApproved(), catalog=vendor)
    _bind(orch, [_dataset_binding("ds_claims", "claims")])

    state = orch.sensitivity_state()

    assert state["approved"] == ["alpha", "zeta"]   # the list stays sorted; the pick does not
    assert state["model"] == "zeta"
    assert state["chat_model"] == "zeta"


def test_a_router_that_cannot_name_the_model_still_reports_the_lock(tmp_path, monkeypatch):
    """The name is for a chip; the lock governs what runs. The route answers a failed read with
    `enabled: False`, which would put every non-approved model back in the picker — so a label that
    could not be worked out must not travel on the same failure."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    monkeypatch.setattr(service_module.llm_router, "nearest_approved",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    orch = _orch(tmp_path)
    _bind(orch, [_dataset_binding("ds_claims", "claims")])

    state = orch.sensitivity_state()

    assert state["locked"] is True
    assert state["approved"] == [APPROVED]
    assert state["model"] is None and state["chat_model"] is None


def test_chat_and_build_are_answered_separately(tmp_path, monkeypatch):
    """Two composers, two turns, two sovereign slots. Chat is pinned to the sovereign Ask slot and
    Build follows its mode, so one field would make whichever surface it was not computed for name
    a model it will not run — which is the defect the field was added to fix."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)

    class BothApproved(FakeResourceProvider):
        def list_llm_aliases(self):
            return [LlmAlias(id="id-plan", name="plan-approved", display_name="plan-approved"),
                    LlmAlias(id="id-ask", name="ask-approved", display_name="ask-approved")]

        def list_alias_groups(self):
            return [{"name": GROUP, "aliases": [{"id": "id-plan"}, {"id": "id-ask"}]}]

    split = ModelCatalog(sovereign_plan="plan-approved", sovereign_implement="plan-approved",
                         sovereign_ask="ask-approved", plan="gpt-5.4", implement="gpt-5.4",
                         ask="gpt-5.4")
    orch = _orch(tmp_path, resources=BothApproved(), catalog=split)
    _bind(orch, [_dataset_binding("ds_claims", "claims")])

    state = orch.sensitivity_state()

    assert state["model"] == "plan-approved"        # Build, in its standing Auto/Plan state
    assert state["chat_model"] == "ask-approved"    # Chat, pinned to the sovereign Ask slot


def test_every_assignable_slot_is_answered_separately(tmp_path, monkeypatch):
    """The model panel draws Plan, Implement and Ask at once, whatever mode the session is in, so
    `model` answers only one of its three rows. Same split as Chat and Build and for the same
    reason: `llm_router._lock_preferences` prefers the sovereign slot of the MODE, and the other two
    rows would name a model they do not get."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)

    class BothApproved(FakeResourceProvider):
        def list_llm_aliases(self):
            return [LlmAlias(id="id-plan", name="plan-approved", display_name="plan-approved"),
                    LlmAlias(id="id-ask", name="ask-approved", display_name="ask-approved")]

        def list_alias_groups(self):
            return [{"name": GROUP, "aliases": [{"id": "id-plan"}, {"id": "id-ask"}]}]

    split = ModelCatalog(sovereign_plan="plan-approved", sovereign_implement="plan-approved",
                         sovereign_ask="ask-approved", plan="gpt-5.4", implement="gpt-5.4",
                         ask="gpt-5.4")
    orch = _orch(tmp_path, resources=BothApproved(), catalog=split)
    _bind(orch, [_dataset_binding("ds_claims", "claims")])

    assert orch.sensitivity_state()["slot_models"] == {
        "plan": "plan-approved", "implement": "plan-approved", "ask": "ask-approved",
    }


def test_a_router_that_cannot_answer_a_slot_leaves_it_out(tmp_path, monkeypatch):
    """Absent rather than null: the panel substitutes a row's model only where this names one, and a
    key holding None would make "could not work it out" and "nothing moves" the same read.

    `locked_runs_on` and not `nearest_approved`, because the slots ask a different question from the
    chip since #285 — the pin sits between a slot and the lock, and the chip's readers have already
    applied it. Patching the chip's function here would have proved nothing about the loop below it,
    which is the shape of mistake this whole file exists to catch.
    """
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path)
    _bind(orch, [_dataset_binding("ds_claims", "claims")])
    monkeypatch.setattr(service_module.llm_router, "locked_runs_on",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("no")))

    state = orch.sensitivity_state()

    assert state["locked"] is True
    assert state["slot_models"] == {}
    # And the chip is untouched by the slots' failure: it reads its own function, which still works.
    assert state["model"] == APPROVED


def test_an_unusable_approved_set_carries_the_refusal_rather_than_an_empty_lock(tmp_path, monkeypatch):
    """The dead end, and the one thing the picker must not do with it. An empty `approved` with no
    sentence would draw every model disabled with no reason — a screen that refuses and will not say
    why. The sentence is the server's own, the same one the turn would be refused with, so the
    person reads one account of the state rather than two."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", "a-group-nobody-made")
    orch = _orch(tmp_path)
    _bind(orch, [_dataset_binding("ds_claims", "claims")])

    state = orch.sensitivity_state()

    assert state["locked"] is True
    assert state["approved"] == []
    assert "a-group-nobody-made" in state["refusal"]
    assert "claims" in state["refusal"]


# --- Declaring a Dataset from the promote confirm ---------------------------------------------------

def test_declaring_a_dataset_this_project_does_not_own_is_refused(tmp_path, monkeypatch):
    """The guard is on the SERVER and not only on the form. The condition is about somebody who is
    not in the room — everyone else who reads that Dataset — and a rule that lives only in the
    control it is drawn on is one caller away from being gone."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    control = FakeControlPlane()
    orch = _orch(tmp_path, control=control)

    with pytest.raises(NotThisProjectsDataset) as refused:
        orch.declare_dataset_sensitive("ds_shared")

    assert refused.value.name == "shared"
    assert control.tagged_sensitive == {}


def test_declaring_this_projects_own_dataset_tags_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    control = FakeControlPlane()
    orch = _orch(tmp_path, control=control)

    assert orch.declare_dataset_sensitive("ds_logs") == {"tagged": True, "dataset": "logs"}
    assert "ds_logs" in control.tagged_sensitive


def test_a_local_run_with_no_control_plane_says_it_did_not_tag(tmp_path, monkeypatch):
    """Off Domino there is nothing to write the tag to. It answers False rather than raising, for
    the reason `tag_dataset_sensitive` is best-effort everywhere else: the upload has already
    landed, and losing the bytes to a governance tag would be the wrong trade. The caller says so
    plainly instead of reporting a declaration that was never made."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path, control=None)

    assert orch.declare_dataset_sensitive("ds_logs") == {"tagged": False, "dataset": "logs"}


def test_a_dataset_that_is_not_there_is_a_lookup_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", GROUP)
    orch = _orch(tmp_path, control=FakeControlPlane())

    with pytest.raises(LookupError):
        orch.declare_dataset_sensitive("ds_nope")
