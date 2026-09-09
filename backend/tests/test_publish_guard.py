"""Publish guards — the two refusals a Data Source Binding earns an app (#12).

ADR-0001's consequence, made true: a published app queries as its publisher, for whoever is looking
at it, so an `Individual` credential and an app anyone can open are both re-exports. What is worth
pinning here is the pair of decisions, because they are opposites and the code is easy to make
symmetrical by accident — an unlistable Data Source must refuse, and an unrecognised visibility must
not. And the case that must NOT break: building against an `Individual` source is exactly what the
creator's own session is for, so nothing before publish may take it away.

Both publish routes used to be covered, the builder's and the hub's. The hub is gone; the builder
reads the workspace's manifest. Nothing here reaches a network — the fakes stand in for Domino
throughout.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane, PublishedApp
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.provider import DataSource, FakeResourceProvider, LlmAlias
from sage.resources.publish_guard import (
    INDIVIDUAL_CREDENTIAL,
    OPEN_APP,
    UNCHECKED_APP,
    UNCHECKED_SOURCE,
    UNLISTED_SOURCE,
    PublishRefused,
    publish_problems,
)
from sage.router.models import ModelCatalog

# The two rows that decide everything below: one service-account credential, one person's own.
SHARED_SOURCE = DataSource("ds-dwh", "Snowflake-Data-Warehouse", "Snowflake", "Shared",
                           None, True, connector_type="SnowflakeConfig")
INDIVIDUAL_SOURCE = DataSource("ds-test", "test", "Snowflake", "Individual",
                               None, True, connector_type="SnowflakeConfig")
SOURCES = [SHARED_SOURCE, INDIVIDUAL_SOURCE]

SHARED_BINDING = Binding(KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse",
                         "Snowflake-Data-Warehouse", "ANALYTICS", "MARTS")
INDIVIDUAL_BINDING = Binding(KIND_DATA_SOURCE, "ds-test", "test", "test", "ANALYTICS", "PUBLIC")


# ---- the judgement ------------------------------------------------------------------------------


def test_a_shared_credential_publishes():
    assert publish_problems([SHARED_BINDING], SOURCES, "GRANT_BASED") == []


def test_an_individual_credential_refuses_and_says_which_source():
    (problem,) = publish_problems([INDIVIDUAL_BINDING], SOURCES, "GRANT_BASED")
    assert problem.reason == INDIVIDUAL_CREDENTIAL
    assert problem.id == "ds-test" and problem.kind == KIND_DATA_SOURCE  # the row to take them to
    assert "test" in problem.message
    assert "personal credential" in problem.message  # a refusal without a remedy is a dead end


def test_a_source_that_is_no_longer_listed_refuses_rather_than_assuming():
    # Not in the listing means the credential cannot be read, and "cannot read" is not "shared".
    (problem,) = publish_problems([SHARED_BINDING], [INDIVIDUAL_SOURCE], "GRANT_BASED")
    assert problem.reason == UNLISTED_SOURCE


def test_a_listing_that_could_not_be_fetched_refuses():
    (problem,) = publish_problems([SHARED_BINDING], None, "GRANT_BASED")
    assert problem.reason == UNCHECKED_SOURCE
    assert "again" in problem.message  # transient: say so, rather than send them to fix nothing


def test_a_binding_whose_source_was_re_registered_still_matches_by_name():
    # `stale_bindings` matches id OR name for this reason; refusing on a changed id would refuse a
    # publish for a Binding that works.
    moved = Binding(KIND_DATA_SOURCE, "ds-new-id", "Snowflake-Data-Warehouse",
                    "Snowflake-Data-Warehouse")
    assert publish_problems([moved], SOURCES, "GRANT_BASED") == []


def test_an_app_open_to_people_who_never_signed_in_refuses_even_on_a_shared_credential():
    (problem,) = publish_problems([SHARED_BINDING], SOURCES, "PUBLIC")
    assert problem.reason == OPEN_APP
    assert "Snowflake-Data-Warehouse" in problem.message
    assert "aren't signed in" in problem.message


@pytest.mark.parametrize("allowed", ["GRANT_BASED", "AUTHENTICATED", "PRIVATE", "grant-based"])
def test_a_visibility_that_still_requires_signing_in_publishes(allowed: str):
    # Both settings Domino's sharing dropdown actually offers, verified live on cloud-dogfood:
    # GRANT_BASED is "Restricted (project collaborators)" and what Sage sets at create,
    # AUTHENTICATED is "Anyone in Domino".
    assert publish_problems([SHARED_BINDING], SOURCES, allowed) == []


def test_anyone_in_domino_is_allowed_because_every_viewer_signed_in():
    # The line ADR-0001's research draws — "never PUBLIC, authenticated at minimum" — and since #13
    # a viewer can only run the named queries the creator declared, not the warehouse.
    assert publish_problems([SHARED_BINDING], SOURCES, "AUTHENTICATED") == []


def test_a_visibility_this_list_has_never_met_refuses():
    # Fails closed. The value is quoted in the message, so a deployment spelling one of the allowed
    # settings differently costs one report and one entry, not a hole.
    (problem,) = publish_problems([SHARED_BINDING], SOURCES, "SOME_FUTURE_SETTING")
    assert problem.reason == OPEN_APP


def test_an_app_that_is_not_published_yet_has_no_visibility_to_object_to():
    # "" is not an unknown value: there is no App, so there is nothing to read, and the first
    # publish is the one Sage sets GRANT_BASED on itself.
    assert publish_problems([SHARED_BINDING], SOURCES, "") == []


def test_a_visibility_that_could_not_be_read_refuses():
    (problem,) = publish_problems([SHARED_BINDING], SOURCES, None)
    assert problem.reason == UNCHECKED_APP
    assert "again" in problem.message


def test_visibility_only_matters_to_an_app_that_reads_a_store():
    assert publish_problems([], SOURCES, "PUBLIC") == []
    assert publish_problems([], SOURCES, None) == []


def test_every_problem_is_reported_at_once():
    # Fixing one Binding, publishing, and being told about the next is learning your own app one
    # refusal at a time.
    second = DataSource("ds-mssql", "AWS_MSSQL", "SQL Server", "Individual", None, True)
    bindings = [INDIVIDUAL_BINDING, Binding(KIND_DATA_SOURCE, "ds-mssql", "AWS_MSSQL", "AWS_MSSQL")]
    problems = publish_problems(bindings, [*SOURCES, second], "PUBLIC")
    assert [p.reason for p in problems] == [INDIVIDUAL_CREDENTIAL, INDIVIDUAL_CREDENTIAL, OPEN_APP]


# ---- the builder's publish ----------------------------------------------------------------------


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")  # entry script, no serve.py
    return t


def _orch(tmp: Path, cp: FakeControlPlane, *, resources: FakeResourceProvider | None = None) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=resources or FakeResourceProvider(data_sources=list(SOURCES)),
        control_plane=cp,
        domino_project_id="proj-1",
        domino_project_name="Sales dashboard",
    )
    orch.project(start_preview=False)  # attach + seed the workspace without starting Vite
    return orch


def test_the_builder_refuses_an_individual_credential_and_deploys_nothing(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    orch.bind_data_source("ds-test", "ANALYTICS", "PUBLIC")

    with pytest.raises(PublishRefused) as ei:
        orch.publish()

    assert [p.reason for p in ei.value.problems] == [INDIVIDUAL_CREDENTIAL]
    assert not cp.published  # never reached the control plane


def test_the_builder_publishes_a_shared_credential(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")

    assert orch.publish()["published"] is True


def test_the_builder_republishes_an_app_shared_with_every_domino_user(tmp_path: Path):
    # Not a refusal: every viewer signed in, and #13 bounds them to the creator's named queries.
    cp = FakeControlPlane()
    cp.published["app-8"] = PublishedApp(id="app-8", url="https://fake.domino/app/app-8")
    cp.app_visibilities["app-8"] = "AUTHENTICATED"
    orch = _orch(tmp_path, cp)
    orch.project(start_preview=False).workspace.record_domino_app("app-8")
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")

    assert orch.publish()["republished"] is True


def test_the_builder_refuses_to_republish_an_app_that_was_opened_up(tmp_path: Path):
    # The case the read-back exists for: Sage published this app grant-based, and somebody changed
    # it afterwards on the settings page Publish itself links to.
    cp = FakeControlPlane()
    cp.published["app-9"] = PublishedApp(id="app-9", url="https://fake.domino/app/app-9")
    cp.app_visibilities["app-9"] = "PUBLIC"
    orch = _orch(tmp_path, cp)
    orch.project(start_preview=False).workspace.record_domino_app("app-9")
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")

    with pytest.raises(PublishRefused) as ei:
        orch.publish()
    assert [p.reason for p in ei.value.problems] == [OPEN_APP]


def test_an_app_that_reads_no_store_publishes_without_asking_anything(tmp_path: Path):
    # The guard must cost nothing for every app built before #11: no Data Source listing, no
    # visibility read, and nothing new that can fail. A provider that raises proves it is not asked.
    class Refuses(FakeResourceProvider):
        def list_data_sources(self) -> list[DataSource]:
            raise AssertionError("the guard asked for Data Sources for an app that reads none")

    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp, resources=Refuses())
    orch.bind_llm_alias(FakeResourceProvider().list_llm_aliases()[0].id)

    assert orch.publish()["published"] is True


def test_building_against_an_individual_credential_stays_allowed(tmp_path: Path):
    # The guard is at publish and nowhere earlier. In their own session the creator queries with
    # their own access, which is what a Data Source is for — picking one must not be taken away.
    orch = _orch(tmp_path, FakeControlPlane())

    bindings = orch.bind_data_source("ds-test", "ANALYTICS", "PUBLIC")

    assert [(b["kind"], b["id"]) for b in bindings] == [(KIND_DATA_SOURCE, "ds-test")]
    assert any(d["id"] == "ds-test" for d in orch.list_data_sources())  # still offered, too


VENDOR = LlmAlias("f-gpt54", "gpt-5.4", "gpt-5.4")
ALIASES = [VENDOR]


def test_the_builder_publishes_a_store_bound_to_a_vendor_model(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp, resources=FakeResourceProvider(data_sources=list(SOURCES),
                                                             aliases=list(ALIASES)))
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")
    orch.bind_llm_alias("f-gpt54")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"])

    orch.publish()

    assert cp.published


def test_publish_never_asks_the_gateway_for_aliases(tmp_path: Path):
    # Publish only asks the Data Source listing. An ordinary publish cannot be blocked by a gateway
    # that is having a bad minute.
    class NoAliases(FakeResourceProvider):
        def list_llm_aliases(self):
            raise AssertionError("publish asked for the Alias listing")

    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp, resources=NoAliases(data_sources=list(SOURCES)))
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")

    orch.publish()

    assert cp.published


def test_the_guard_reads_the_bindings_of_the_app_being_published(tmp_path: Path):
    # A Project holds many Built Apps and each records its own Bindings (ADR-0008), so the refusal
    # follows the app in front of you. The second app reads nothing and publishes; the first still
    # refuses, and neither answer leaks into the other.
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)
    bound = orch.project(start_preview=False).workspace.app_id
    orch.bind_data_source("ds-test", "ANALYTICS", "PUBLIC")

    orch.select_app(orch._wm.create_app("Sage").app_id)
    assert orch.publish()["published"] is True

    orch.select_app(bound)
    with pytest.raises(PublishRefused) as ei:
        orch.publish()
    assert [p.reason for p in ei.value.problems] == [INDIVIDUAL_CREDENTIAL]


def test_publish_from_the_workbench_app_is_refused(tmp_path: Path, monkeypatch):
    # DOMINO_PROJECT_ID on the Workbench App is Sage itself. Publishing would ship this repo.
    monkeypatch.setenv("SAGE_PROXY_MODE", "app")
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp)

    with pytest.raises(RuntimeError, match="Workbench App"):
        orch.publish()
    assert not cp.published


# --- The sensitivity refusal (ADR-0043) ----------------------------------------------------------

from sage.resources.bindings import KIND_DATASET, KIND_LLM_ALIAS
from sage.resources.provider import ApprovedModels
from sage.resources.publish_guard import (
    EMPTY_MODEL_GROUP,
    MISSING_MODEL_GROUP,
    NO_APPROVED_MODEL_ACCESS,
    SENSITIVE_TO_VENDOR,
    UNCHECKED_ALIAS,
    sensitive_model_problems,
)

_SENSITIVE = [Binding(kind=KIND_DATASET, id="d1", name="customer_pii", display_name="customer_pii")]
_APPROVED = ApprovedModels(names=frozenset({"qwen-2-5"}), group_name="sensitive-approved", members=1)


def _alias_binding(name):
    return Binding(kind=KIND_LLM_ALIAS, id=f"a-{name}", name=name, display_name=name)


def test_an_unapproved_alias_beside_a_declared_dataset_refuses():
    problems = sensitive_model_problems([_alias_binding("gpt-5.4")], _SENSITIVE, _APPROVED)
    assert [p.reason for p in problems] == [SENSITIVE_TO_VENDOR]
    assert "gpt-5.4" in problems[0].message and "customer_pii" in problems[0].message
    assert problems[0].id == "a-gpt-5.4"  # the UI can take the creator to the row


def test_an_approved_alias_publishes():
    assert sensitive_model_problems([_alias_binding("qwen-2-5")], _SENSITIVE, _APPROVED) == []


def test_an_ordinary_app_never_reaches_the_check():
    """No declared Dataset, or no model bound: the guard costs nothing and cannot block a publish."""
    assert sensitive_model_problems([_alias_binding("gpt-5.4")], [], _APPROVED) == []
    assert sensitive_model_problems([], _SENSITIVE, _APPROVED) == []


def test_the_feature_is_off_when_no_group_is_configured():
    """approved=None is the opt-out. A tag somebody applied for their own reasons must not brick."""
    assert sensitive_model_problems([_alias_binding("gpt-5.4")], _SENSITIVE, None) == []


@pytest.mark.parametrize(
    "approved,expected",
    [
        (ApprovedModels(group_name="g", reachable=False), UNCHECKED_ALIAS),
        (ApprovedModels(group_name="g", group_found=False), MISSING_MODEL_GROUP),
        (ApprovedModels(group_name="g", members=0), EMPTY_MODEL_GROUP),
        (ApprovedModels(group_name="g", members=3), NO_APPROVED_MODEL_ACCESS),
    ],
)
def test_the_four_unusable_shapes_each_get_their_own_sentence(approved, expected):
    problems = sensitive_model_problems([_alias_binding("gpt-5.4")], _SENSITIVE, approved)
    assert [p.reason for p in problems] == [expected]
    # Every one of them names the Dataset it is about and ends with a next step — "publish again"
    # for the three a person can fix, "publishing again in a moment" for the transient one.
    assert "customer_pii" in problems[0].message
    assert "again" in problems[0].message


def test_the_four_sentences_are_all_different():
    """They fail the same way and are fixed differently, which is the whole reason they are four."""
    shapes = [
        ApprovedModels(group_name="g", reachable=False),
        ApprovedModels(group_name="g", group_found=False),
        ApprovedModels(group_name="g", members=0),
        ApprovedModels(group_name="g", members=3),
    ]
    messages = {
        sensitive_model_problems([_alias_binding("gpt-5.4")], _SENSITIVE, a)[0].message
        for a in shapes
    }
    assert len(messages) == 4
