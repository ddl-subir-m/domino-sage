"""Binding a Resource puts it in the Project's working set (#134).

Membership used to be a chore standing in front of the act with a reason behind it: a person
picked a Resource for an app, and the rail still had to be told separately. So every bind door now
records membership on the way through, and the rail becomes a record of what the Project uses
rather than a gate it has to pass first (ADR-0018).

The seam under test is the service, not the door: `_record` is the one place every bind arrives at
— the panel's "Use in {app}", the rail's tree, the Chat handoff — so a test per door would pin the
same write three times, and a test below `_record` would pin an internal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator, ResourceStillBound
from sage.resources.bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, Binding
from sage.resources.model_api_credentials import Credential, CredentialRequired, CredentialStore
from sage.router.models import ModelCatalog


def _orch(tmp_path: Path) -> Orchestrator:
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code", template=template,
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
    )
    orch.project(start_preview=False)
    return orch


def _credential(orch: Orchestrator, model_api_id: str) -> None:
    """The verified token a Model API Binding is refused without. Written straight into the store
    rather than through `save_model_api_credential`, which calls the model to check it first —
    membership is what this file is about, and the paste has its own."""
    CredentialStore(orch.project().workspace.path).put(
        model_api_id, Credential(f"https://dogfood.example/models/{model_api_id}/latest/model", "t" * 64))


def _member(orch: Orchestrator, resource_id: str) -> dict:
    return next(r for r in orch.list_project_resources() if r["id"] == resource_id)


# ---- Every kind joins, through whichever bind method records it ---------------------------------


def test_binding_an_llm_alias_puts_it_in_the_project(tmp_path: Path):
    orch = _orch(tmp_path)

    orch.bind_llm_alias("f-sonnet")

    row = _member(orch, "llm_alias:f-sonnet")
    assert row["kind"] == "llm_alias"
    assert row["name"] == "Claude Sonnet 4.6"
    # The gateway alias the model picker calls by. A row without it is an option drawn blank.
    assert row["alias"] == "sonnet"


def test_binding_a_model_api_puts_it_in_the_project(tmp_path: Path):
    orch = _orch(tmp_path)
    _credential(orch, "f-churn")

    orch.bind_model_api("f-churn")

    assert _member(orch, "model_api:f-churn")["name"] == "churn-risk"


def test_binding_a_data_source_puts_it_in_the_project(tmp_path: Path):
    orch = _orch(tmp_path)

    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "DIM_ACCOUNT")

    row = _member(orch, "data_source:ds-dwh")
    assert row["kind"] == "data_source"
    assert row["name"] == "Snowflake-Data-Warehouse"


def test_a_bind_writes_the_same_row_the_browse_button_writes(tmp_path: Path):
    """The three doors into the working set have to agree. The model picker reads `alias` and
    `reasoning_efforts` off these rows whenever the Alias listing is unavailable, so a bind that
    wrote only id, kind and name would put an option in the picker that it draws blank and cannot
    select — the same defect the mention join was fixed for."""
    orch = _orch(tmp_path)

    orch.bind_llm_alias("f-gpt54")

    row = _member(orch, "llm_alias:f-gpt54")
    assert row["alias"] == "gpt-5.4"
    assert row["reasoning_efforts"] == ["low", "medium", "high"]
    assert "vision" in row["capabilities"]
    assert row["description"] == "gpt-5.4"


def test_a_data_source_row_carries_the_connector_the_rail_draws(tmp_path: Path):
    """The row's second line. `connector` is the connector type's label; `connector_type` is the
    config-class string the Binding keeps, and writing that would put `SnowflakeConfig` under the
    name in the rail."""
    orch = _orch(tmp_path)

    orch.bind_data_source("ds-dwh", "DWH", "MARTS")

    assert _member(orch, "data_source:ds-dwh")["description"] == "Snowflake"


def test_a_model_api_nothing_describes_gets_no_invented_second_line(tmp_path: Path):
    """A Model API can reach a Binding on a verified credential alone, with Domino declining to
    describe it. The row is written; its description is left blank rather than filled in."""
    orch = _orch(tmp_path)
    _credential(orch, "f-fraud")

    orch.bind_model_api("f-fraud")

    assert not _member(orch, "model_api:f-fraud").get("description")


def test_the_id_written_is_the_one_the_rail_keys_a_resource_on(tmp_path: Path):
    """A Binding records the bare Domino id beside its kind; a membership row carries the kind as a
    prefix. Writing the bare id would draw a SECOND row for a Resource the Project already holds,
    and the removal guard would not join the two."""
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "data_source:ds-dwh", "kind": "datasource", "name": "Snowflake-Data-Warehouse",
    })

    orch.bind_data_source("ds-dwh", "DWH", "MARTS")

    assert [r["id"] for r in orch.list_project_resources()] == ["data_source:ds-dwh"]


def test_the_handoff_door_joins_through_the_same_place(tmp_path: Path):
    """Chat's confirmed handoff records Bindings of its own. It reaches the manifest through
    `_record` like every other door, so it needs no join of its own — and this pins that it has
    not grown one."""
    orch = _orch(tmp_path)

    orch._bind_from_handoff(Binding(KIND_LLM_ALIAS, "f-opus", "opus", "Claude Opus 4.6"))

    assert _member(orch, "llm_alias:f-opus")["name"] == "Claude Opus 4.6"


# ---- The join is idempotent --------------------------------------------------------------------


def test_a_second_bind_changes_nothing_about_the_membership_row(tmp_path: Path):
    """Re-binding is a no-op by design — it is how a Data Source's Scope is corrected — so it must
    not duplicate the row, move it, or rewrite it. Only the live `usedBy` enrichment may differ:
    it is computed from the app's own manifest on every read (#133), so the corrected Scope shows
    up there — that is the enrichment telling the truth, not the membership row moving."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    before = orch.list_project_resources()

    orch.bind_data_source("ds-dwh", "DWH", "MARTS_ARCHIVE")

    after = orch.list_project_resources()
    membership = [{k: v for k, v in r.items() if k != "usedBy"} for r in after]
    assert membership == [{k: v for k, v in r.items() if k != "usedBy"} for r in before]
    assert [u["scope"] for u in after[0]["usedBy"]] == ["DWH.MARTS_ARCHIVE"]


def test_a_resource_already_in_the_project_is_not_renamed_by_a_bind(tmp_path: Path):
    """The name on the row is the one this Project has. A bind is a reason to record use, never a
    reason to move a name the creator is already reading in the rail."""
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "llm_alias:f-sonnet", "kind": "model_llm", "name": "Sonnet, as we call it here",
    })

    orch.bind_llm_alias("f-sonnet")

    rows = orch.list_project_resources()
    assert len(rows) == 1
    assert rows[0]["name"] == "Sonnet, as we call it here"
    assert rows[0]["kind"] == "model_llm"


def test_the_bind_keeps_what_the_earlier_write_left_out(tmp_path: Path):
    """`add_project_resource` fills gaps on a re-add, and the bind is a re-add. A row that reached
    the working set without its alias is repaired here rather than staying unselectable."""
    orch = _orch(tmp_path)
    orch.add_project_resource({
        "id": "llm_alias:f-sonnet", "kind": "llm_alias", "name": "Claude Sonnet 4.6",
    })

    orch.bind_llm_alias("f-sonnet")

    assert _member(orch, "llm_alias:f-sonnet")["alias"] == "sonnet"


def test_two_apps_binding_one_resource_leave_one_membership_row(tmp_path: Path):
    """A Resource is picked once for the Project and bound by as many of its Built Apps as need it
    (ADR-0008). The second app's bind finds the row the first one wrote."""
    orch = _orch(tmp_path)
    first = orch.project(start_preview=False).workspace.app_id
    orch.bind_llm_alias("f-sonnet")

    second = orch._wm.create_app("Sage").app_id
    orch.select_app(second)
    orch.bind_llm_alias("f-sonnet")
    orch.select_app(first)

    assert [r["id"] for r in orch.list_project_resources()] == ["llm_alias:f-sonnet"]


# ---- What the join does not do -----------------------------------------------------------------


def test_unbinding_leaves_the_resource_in_the_project(tmp_path: Path):
    """Removal lives with the list that owns the scope (ADR-0011). Dropping a Binding says this app
    no longer uses the Resource; it does not say the Project is done with it."""
    orch = _orch(tmp_path)
    orch.bind_llm_alias("f-sonnet")

    orch.unbind(KIND_LLM_ALIAS, "f-sonnet")

    assert [r["id"] for r in orch.list_project_resources()] == ["llm_alias:f-sonnet"]


def test_a_bind_the_service_refuses_records_no_membership(tmp_path: Path):
    """Membership is a record of use, and a refused bind is not use. A Model API with no verified
    credential is refused by design, and a row written anyway would report a dependency on a model
    nothing can call."""
    orch = _orch(tmp_path)

    with pytest.raises(CredentialRequired):
        orch.bind_model_api("f-churn")

    assert orch.list_project_resources() == []


def test_the_membership_row_is_still_guarded_by_the_apps_that_bind_it(tmp_path: Path):
    """The join writes the row the removal guard reads. Auto-joining and then allowing the removal
    the guard exists to refuse would be worse than not joining at all."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS")

    with pytest.raises(ResourceStillBound, match="Snowflake-Data-Warehouse"):
        orch.remove_project_resource("data_source:ds-dwh")

    orch.unbind(KIND_DATA_SOURCE, "ds-dwh")
    assert orch.remove_project_resource("data_source:ds-dwh") is True
