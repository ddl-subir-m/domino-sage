"""Publish guards — the two refusals a Data Source Binding earns an app (#12).

ADR-0001's consequence, made true: a published app queries as its publisher, for whoever is looking
at it, so an `Individual` credential and an app anyone can open are both re-exports. What is worth
pinning here is the pair of decisions, because they are opposites and the code is easy to make
symmetrical by accident — an unlistable Data Source must refuse, and an unrecognised visibility must
not. And the case that must NOT break: building against an `Individual` source is exactly what the
creator's own session is for, so nothing before publish may take it away.

Both publish routes are covered, the builder's and the hub's, because they refuse by different
means: the builder reads the workspace's manifest, the hub reads the committed one over the repo
provider. Nothing here reaches a network — the fakes stand in for Domino throughout.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.provision.domino import FakeControlPlane, PublishedApp
from sage.provision.github import FakeRepoProvider
from sage.provision.service import HubService
from sage.resources.bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, Binding
from sage.resources.provider import DataSource, FakeResourceProvider, LlmAlias
from sage.resources.publish_guard import (
    INDIVIDUAL_CREDENTIAL,
    OPEN_APP,
    SENSITIVE_TO_VENDOR,
    UNCHECKED_ALIAS,
    UNCHECKED_APP,
    UNCHECKED_SOURCE,
    UNLISTED_SOURCE,
    PublishRefused,
    publish_problems,
    vendor_model_problems,
    vendor_model_warning,
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
    assert "publish again" in problem.message  # a refusal without a remedy is a dead end


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
    assert "PUBLIC" in problem.message   # quoted, so a wrong refusal is one report to fix


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
    cp.app_projects["app-8"] = "proj-1"
    cp.app_visibilities["app-8"] = "AUTHENTICATED"
    orch = _orch(tmp_path, cp)
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")

    assert orch.publish()["republished"] is True


def test_the_builder_refuses_to_republish_an_app_that_was_opened_up(tmp_path: Path):
    # The case the read-back exists for: Sage published this app grant-based, and somebody changed
    # it afterwards on the settings page Publish itself links to.
    cp = FakeControlPlane()
    cp.published["app-9"] = PublishedApp(id="app-9", url="https://fake.domino/app/app-9")
    cp.app_projects["app-9"] = "proj-1"
    cp.app_visibilities["app-9"] = "PUBLIC"
    orch = _orch(tmp_path, cp)
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


# ---- the hub's publish --------------------------------------------------------------------------

_GIT_URL = "https://github.com/me/sage-sales-app.git"
_FULL = "me/sage-sales-app"


def _hub(tmp_path: Path, cp: FakeControlPlane, repo: FakeRepoProvider, *,
         resources: FakeResourceProvider | None = None) -> HubService:
    return HubService(cp, repo, tmp_path, seed=lambda *a, **k: None,
                      resources=resources or FakeResourceProvider(data_sources=list(SOURCES)))


def _repo_with(bindings: list[dict]) -> FakeRepoProvider:
    repo = FakeRepoProvider()
    repo.files[(_FULL, ".sage/bindings.json")] = json.dumps(bindings)
    return repo


def test_the_hub_refuses_an_individual_credential_from_the_committed_manifest(tmp_path: Path):
    # The hub publishes without a builder, so the only copy of the Resource list it can reach is the
    # one in the repo. The guard has to be the same one, or it is a door left open beside a lock.
    cp = FakeControlPlane()
    ref = cp.create_project("Sales App", git_url=_GIT_URL)
    repo = _repo_with([{"kind": "data_source", "id": "ds-test", "name": "test",
                        "display_name": "test", "database": "ANALYTICS"}])

    with pytest.raises(PublishRefused) as ei:
        _hub(tmp_path, cp, repo).publish_app(ref.id)

    assert [p.reason for p in ei.value.problems] == [INDIVIDUAL_CREDENTIAL]
    assert not cp.published


def test_the_hub_publishes_a_shared_credential(tmp_path: Path):
    cp = FakeControlPlane()
    ref = cp.create_project("Sales App", git_url=_GIT_URL)
    repo = _repo_with([{"kind": "data_source", "id": "ds-dwh", "name": "Snowflake-Data-Warehouse",
                        "display_name": "Snowflake-Data-Warehouse", "database": "ANALYTICS"}])

    assert _hub(tmp_path, cp, repo).publish_app(ref.id)["published"] is True


def test_the_hub_publishes_an_app_with_no_manifest_unchanged(tmp_path: Path):
    # Every app built before #11 has no manifest at all, and a repo read that answers nothing is not
    # evidence the app reads a store.
    cp = FakeControlPlane()
    ref = cp.create_project("Sales App", git_url=_GIT_URL)

    assert _hub(tmp_path, cp, FakeRepoProvider()).publish_app(ref.id)["published"] is True


def test_the_hub_ignores_a_manifest_that_is_not_valid_json(tmp_path: Path):
    cp = FakeControlPlane()
    ref = cp.create_project("Sales App", git_url=_GIT_URL)
    repo = FakeRepoProvider()
    repo.files[(_FULL, ".sage/bindings.json")] = "{not json"

    assert _hub(tmp_path, cp, repo).publish_app(ref.id)["published"] is True


def test_the_hub_leaves_an_llm_alias_binding_alone(tmp_path: Path):
    # These guards are about stores, and an app that pins an Alias reads none.
    cp = FakeControlPlane()
    ref = cp.create_project("Sales App", git_url=_GIT_URL)
    repo = _repo_with([{"kind": KIND_LLM_ALIAS, "id": "id-sonnet", "name": "sonnet",
                        "display_name": "Claude Sonnet 4.6"}])

    assert _hub(tmp_path, cp, repo).publish_app(ref.id)["published"] is True


# --- Where the rows go (#35) -------------------------------------------------------------------
#
# A published app can read a store and call a model in one page load, and the model may be a vendor's
# rather than one hosted in Domino. The creator has already answered the only question that matters —
# they said whether the rows were sensitive when they shared samples — and until this that answer
# governed Sage's own conversation and nothing about the app Sage built.
#
# The pairs worth pinning are the asymmetries, as above: a store nobody called sensitive WARNS and
# must not refuse, an unreachable Alias listing REFUSES beside sensitive rows and must not warn, and
# an app that binds a model but reads no store must gain nothing at all.

HOSTED = LlmAlias("f-qwen25", "qwen-2-5", "Qwen 2.5 (Domino-hosted)",
                  endpoint_url="https://apps.example.domino.tech/qwen/v1")
VENDOR = LlmAlias("f-gpt54", "gpt-5.4", "gpt-5.4")
ALIASES = [VENDOR, HOSTED]

SENSITIVE_BINDING = Binding(KIND_DATA_SOURCE, "ds-dwh", "ANALYTICS", "MARTS", sensitive=True)
VENDOR_BINDING = Binding(KIND_LLM_ALIAS, "f-gpt54", "gpt-5.4", "gpt-5.4")
HOSTED_BINDING = Binding(KIND_LLM_ALIAS, "f-qwen25", "qwen-2-5", "Qwen 2.5 (Domino-hosted)")


def test_sensitive_rows_and_a_vendor_model_refuse_and_name_both():
    problems = vendor_model_problems([SENSITIVE_BINDING, VENDOR_BINDING], ALIASES)

    assert [p.reason for p in problems] == [SENSITIVE_TO_VENDOR]
    # A refusal a creator can act on names the two Resources that caused it, not the rule.
    assert "MARTS" in problems[0].message and "gpt-5.4" in problems[0].message
    assert problems[0].id == "f-gpt54"


def test_sensitive_rows_and_a_domino_hosted_model_publish():
    # The whole point of the choice. Option C refuses a destination, not a capability: an app that
    # reasons over sensitive rows inside Domino is exactly what the LLM Gateway is for.
    assert vendor_model_problems([SENSITIVE_BINDING, HOSTED_BINDING], ALIASES) == []


def test_rows_nobody_called_sensitive_warn_and_do_not_refuse():
    bindings = [SHARED_BINDING, VENDOR_BINDING]

    assert vendor_model_problems(bindings, ALIASES) == []
    warning = vendor_model_warning(bindings, ALIASES)
    assert warning and "gpt-5.4" in warning and "outside Domino" in warning


def test_the_warning_is_silent_whenever_the_refusal_speaks():
    # One pair of Resources produces one sentence. A creator who is being refused must not also read
    # a warning inviting them to publish past it.
    bindings = [SENSITIVE_BINDING, VENDOR_BINDING]

    assert vendor_model_problems(bindings, ALIASES) != []
    assert vendor_model_warning(bindings, ALIASES) is None


def test_an_alias_listing_sage_could_not_read_refuses_beside_sensitive_rows():
    # Fails CLOSED, like an unreadable Data Source listing and unlike an unreadable visibility. The
    # creator called these rows sensitive; "Sage could not check where they would go" is not a reason
    # to send them.
    problems = vendor_model_problems([SENSITIVE_BINDING, VENDOR_BINDING], None)

    assert [p.reason for p in problems] == [UNCHECKED_ALIAS]


def test_an_alias_listing_sage_could_not_read_says_nothing_when_no_rows_are_sensitive():
    # And fails OPEN on the same unknown, because this is the hint rather than the guard. Do not
    # symmetrise these two: a refusal one route skips is a hole, a hint one route skips is a nudge.
    assert vendor_model_warning([SHARED_BINDING, VENDOR_BINDING], None) is None


def test_an_alias_that_is_not_in_the_listing_refuses_rather_than_reads_as_hosted():
    gone = Binding(KIND_LLM_ALIAS, "f-retired", "retired-model", "retired-model")

    problems = vendor_model_problems([SENSITIVE_BINDING, gone], ALIASES)

    assert [p.reason for p in problems] == [UNCHECKED_ALIAS]


def test_an_app_that_calls_a_vendor_model_and_reads_no_store_gains_nothing():
    # The line #12 already draws. A model call that reads nothing re-exports nothing, so binding an
    # Alias on its own must cost a publish exactly what it cost before.
    assert vendor_model_problems([VENDOR_BINDING], ALIASES) == []
    assert vendor_model_warning([VENDOR_BINDING], ALIASES) is None


def test_every_vendor_model_beside_sensitive_rows_is_named_at_once():
    # The reason PublishRefused carries every problem: a creator who removes the one Alias they were
    # told about and is then told about the next has been made to discover their own app one refusal
    # at a time.
    second = Binding(KIND_LLM_ALIAS, "f-sonnet", "sonnet", "Claude Sonnet 4.6")

    problems = vendor_model_problems([SENSITIVE_BINDING, VENDOR_BINDING, second],
                                     [*ALIASES, LlmAlias("f-sonnet", "sonnet", "Claude Sonnet 4.6")])

    assert [p.id for p in problems] == ["f-gpt54", "f-sonnet"]


def test_the_builder_refuses_sensitive_rows_bound_to_a_vendor_model_and_deploys_nothing(tmp_path: Path):
    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp, resources=FakeResourceProvider(data_sources=list(SOURCES),
                                                             aliases=list(ALIASES)))
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")
    orch.bind_llm_alias("f-gpt54")
    orch.share_sample_rows("ds-dwh", ["FCT_USAGE_DAILY"], sensitive=True)

    with pytest.raises(PublishRefused) as ei:
        orch.publish()

    assert [p.reason for p in ei.value.problems] == [SENSITIVE_TO_VENDOR]
    assert not cp.published


def test_the_hub_refuses_the_same_app_the_builder_refuses(tmp_path: Path):
    # The door beside the lock. The hub publishes from the repo and never sees `.sage/samples.json`,
    # so this passes only because the creator's judgement rides in the committed Bindings manifest.
    cp = FakeControlPlane()
    ref = cp.create_project("Sales App", git_url=_GIT_URL)
    repo = _repo_with([
        {"kind": KIND_DATA_SOURCE, "id": "ds-dwh", "name": "Snowflake-Data-Warehouse",
         "display_name": "Snowflake-Data-Warehouse", "database": "ANALYTICS", "schema": "MARTS",
         "sensitive": True},
        {"kind": KIND_LLM_ALIAS, "id": "f-gpt54", "name": "gpt-5.4", "display_name": "gpt-5.4"},
    ])
    hub = _hub(tmp_path, cp, repo, resources=FakeResourceProvider(data_sources=list(SOURCES),
                                                                 aliases=list(ALIASES)))

    with pytest.raises(PublishRefused) as ei:
        hub.publish_app(ref.id)

    assert [p.reason for p in ei.value.problems] == [SENSITIVE_TO_VENDOR]
    assert not cp.published


def test_an_app_with_nothing_sensitive_never_asks_the_gateway(tmp_path: Path):
    # The cost guarantee. An ordinary publish must not gain a gateway call it can be blocked by, so
    # the listing is fetched only once a bound store's rows are marked sensitive.
    class NoAliases(FakeResourceProvider):
        def list_llm_aliases(self):
            raise AssertionError("publish asked for the Alias listing with nothing sensitive bound")

    cp = FakeControlPlane()
    orch = _orch(tmp_path, cp, resources=NoAliases(data_sources=list(SOURCES)))
    orch.bind_data_source("ds-dwh", "ANALYTICS", "MARTS")

    orch.publish()

    assert cp.published
