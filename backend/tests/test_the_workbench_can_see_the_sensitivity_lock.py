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

from sage.assets.provider import Asset, FakeAssetProvider
from sage.orchestrator.service import NotThisProjectsDataset, Orchestrator
from sage.provision.domino import FakeControlPlane
from sage.resources.provider import FakeResourceProvider
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


def _orch(tmp: Path, control=None) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=_catalog(),
        project_id="Sage",
        assets=_assets(tmp),
        resources=FakeResourceProvider(),
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
        "approved": [], "datasets": [], "refusal": None,
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
