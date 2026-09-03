"""Preflight — Sage's own model slots at startup, and an app's Bindings at session open (#17).

Both failures otherwise look the same to a user: an opaque error partway through a build. So the
tests are about what gets *reported*, and about the one distinction that decides whether a report
is trustworthy — "this slot is broken" versus "we could not check". Nothing reaches the network:
the checks are pure functions over an alias list, and the orchestrator path runs on the injected
fake, subclassed to force the gateway failure.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE, KIND_MODEL_API, Binding
from sage.resources.preflight import (
    bindings_on_dead_endpoints,
    endpoint_binding_fix,
    missing_credentials,
    slots_on_dead_endpoints,
    stale_bindings,
    stale_fault,
    unresolved_slots,
)
from sage.resources.provider import (
    FakeResourceProvider,
    HostedEndpoint,
    LlmAlias,
    ResourceUnavailable,
)
from sage.router.models import ModelCatalog

ALIASES = [
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
    LlmAlias("id-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)", None, ["chat"], {}),
]

# Every slot filled by an Alias in ALIASES, so a test can break exactly one and know that is the
# only reason anything was reported.
GOOD_CATALOG = ModelCatalog(
    sovereign_plan="qwen-2-5", sovereign_implement="qwen-2-5", sovereign_ask="qwen-2-5",
    plan="sonnet", implement="sonnet", ask="sonnet",
)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp_path: Path, catalog: ModelCatalog = GOOD_CATALOG, resources=None) -> Orchestrator:
    """A real workspace on disk — the Bindings manifest is a thing under test, not faked."""
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=catalog,
        project_id="Sage",
        resources=resources if resources is not None else FakeResourceProvider(list(ALIASES)),
    )
    orch.project(start_preview=False)  # no method under test starts the dev server
    return orch


class _DeadGateway(FakeResourceProvider):
    """The gateway will not answer. The house pattern: subclass the fake to force the failure."""

    def list_llm_aliases(self) -> list[LlmAlias]:
        raise ResourceUnavailable("The LLM Gateway did not answer (ConnectError).")


# ---- model slots: the check itself ---------------------------------------------------------------


def test_a_catalog_whose_every_slot_resolves_reports_nothing():
    assert unresolved_slots(GOOD_CATALOG, ALIASES) == []


def test_a_slot_set_to_an_alias_that_does_not_exist_is_reported():
    catalog = replace(GOOD_CATALOG, implement="bedrock-qwen3-coder")
    (p,) = unresolved_slots(catalog, ALIASES)
    assert (p.slot, p.alias) == ("implement", "bedrock-qwen3-coder")


def test_the_report_names_both_the_slot_and_the_alias():
    # The slot alone does not say what to change it from; the alias alone does not say what broke.
    catalog = replace(GOOD_CATALOG, sovereign_implement="ghost-model")
    (p,) = unresolved_slots(catalog, ALIASES)
    assert "sovereign_implement" in p.message and "ghost-model" in p.message
    assert p.to_dict() == {"slot": "sovereign_implement", "alias": "ghost-model",
                           "message": p.message, "fault": p.fault, "fix": p.fix}
    # `message` is the two halves joined, so the log line and the builder read as they always did
    # while ADR-0027's payload can put the remedy in a field of its own.
    assert p.message == f"{p.fault} {p.fix}"


def test_every_broken_slot_is_reported_not_just_the_first():
    catalog = ModelCatalog(sovereign_plan="gone-a", sovereign_implement="gone-b", sovereign_ask="qwen-2-5",
                           plan="sonnet", implement="gone-c", ask="sonnet")
    assert [p.slot for p in unresolved_slots(catalog, ALIASES)] == [
        "sovereign_plan", "sovereign_implement", "implement"]


def test_a_slot_resolves_on_alias_name_not_on_id():
    # `name` is what a request's `model` field carries, so it is the identity a slot must resolve to.
    # Setting a slot to the gateway's internal id would 404 mid-turn, and must be reported.
    catalog = replace(GOOD_CATALOG, ask="id-sonnet")
    assert [p.alias for p in unresolved_slots(catalog, ALIASES)] == ["id-sonnet"]


def test_a_provider_prefixed_slot_resolves_on_its_bare_id():
    catalog = replace(GOOD_CATALOG, ask="domino/sonnet")
    assert unresolved_slots(catalog, ALIASES) == []


# Aliases whose own `name` contains a slash. Measured live against cloud-dogfood: `/v1/models`
# offers `domino/gemini-3.7-flash` and `domino-gcp/claude-sonnet-5`, and the whole string — slash and
# all — is the name a request's `model` field has to carry.
SLASH_ALIASES = [
    LlmAlias("id-gemini", "domino/gemini-3.7-flash", "Gemini 3.7 Flash", None, ["chat"], {}),
    LlmAlias("id-sonnet-5", "domino-gcp/claude-sonnet-5", "Claude Sonnet 5", None, ["chat"], {}),
]


def _every_slot(model: str) -> ModelCatalog:
    return ModelCatalog(sovereign_plan=model, sovereign_implement=model, sovereign_ask=model,
                        plan=model, implement=model, ask=model)


def test_an_alias_whose_own_name_contains_a_slash_is_offered_not_missing():
    """The live regression, in one line: every slot set to `domino/gemini-3.7-flash`, an Alias this
    gateway really offers, and all six came back "which this LLM Gateway does not offer" — because
    the slot was reduced to its bare id before the comparison was made."""
    assert unresolved_slots(_every_slot("domino/gemini-3.7-flash"), SLASH_ALIASES) == []


def test_the_bare_half_of_a_slash_named_alias_is_still_a_missing_alias():
    """The reduction is a fallback and never runs in reverse. `gemini-3.7-flash` on its own is not a
    name this gateway answers to, so a slot set to it would 404 mid-turn and must be reported."""
    (p,) = unresolved_slots(replace(GOOD_CATALOG, ask="gemini-3.7-flash"), SLASH_ALIASES + ALIASES)
    assert (p.slot, p.alias) == ("ask", "gemini-3.7-flash")


def test_a_slot_that_resolves_neither_way_is_reported_as_it_was_configured():
    """Not shortened. The remedy names this string twice — pick another model, or register it — and a
    name trimmed by a rule that has just failed to match would send an administrator to register
    something nobody configured."""
    (p,) = unresolved_slots(replace(GOOD_CATALOG, ask="domino/ghost-model"), ALIASES)
    assert p.alias == "domino/ghost-model"
    assert "domino/ghost-model" in p.message


def test_an_empty_gateway_reports_every_slot():
    # No aliases at all is the shape of a gateway the caller has no grants on. It must not read as
    # "everything is fine because there was nothing to compare against".
    assert len(unresolved_slots(GOOD_CATALOG, [])) == 6


# ---- model slots: through the orchestrator, which is what startup calls --------------------------


def test_startup_reports_ok_when_the_configured_models_all_exist(tmp_path):
    assert _orch(tmp_path).preflight_slots() == {
        "state": "ok", "error": None, "slots": [], "reached": True}


def test_startup_reports_the_configured_alias_that_does_not_exist(tmp_path):
    catalog = replace(GOOD_CATALOG, plan="gpt-5.4")
    result = _orch(tmp_path, catalog=catalog).preflight_slots()
    assert result["state"] == "problems"
    assert [(s["slot"], s["alias"]) for s in result["slots"]] == [("plan", "gpt-5.4")]


def test_a_gateway_that_will_not_answer_is_unreachable_not_broken(tmp_path):
    # The distinction the whole report rests on. Announcing "problems" here would send a maintainer
    # to edit six settings that were all correct.
    result = _orch(tmp_path, resources=_DeadGateway()).preflight_slots()
    assert result["state"] == "unreachable"
    assert result["slots"] == []
    assert "LLM Gateway" in result["error"]


# ---- Bindings: the check itself ------------------------------------------------------------------


def _binding(rid: str, name: str, display: str = "") -> Binding:
    return Binding("llm_alias", rid, name, display or name)


ALIAS_LISTING = {"llm_alias": ALIASES}
# The fake's own Model APIs and Data Sources, so the "still there" cases are checked against the same
# rows the orchestrator would fetch rather than against a second set invented here.
FAKE = FakeResourceProvider()
MODEL_APIS = list(FAKE.model_apis)
DATA_SOURCES = list(FAKE.data_sources)


def test_a_binding_whose_alias_is_still_offered_is_not_stale():
    assert stale_bindings([_binding("id-sonnet", "sonnet")], ALIAS_LISTING) == []


def test_a_binding_whose_resource_has_gone_is_stale():
    gone = _binding("id-gpt", "gpt-5.4", "gpt-5.4")
    assert stale_bindings([gone, _binding("id-sonnet", "sonnet")], ALIAS_LISTING) == [gone]


def test_a_binding_still_resolves_when_only_its_id_has_changed():
    # The manifest keeps id AND name; the control plane keys on id, a model call keys on name. An
    # Alias re-registered under a new id is the same Resource, and calling it stale would send a
    # creator to remove something that works.
    assert stale_bindings([_binding("stale-id", "sonnet")], ALIAS_LISTING) == []


def test_a_binding_of_a_kind_this_sage_cannot_check_is_left_alone():
    # A newer Sage's record: a kind with no listing in this call at all. Not being able to check
    # something is not evidence that it is gone.
    other = Binding("nothing_sage_knows", "x-1", "thing", "Thing")
    assert stale_bindings([other], ALIAS_LISTING) == []


def test_a_kind_is_only_ever_judged_against_its_own_listing(tmp_path):
    # #23's whole point. A Model API comes off the Domino API and a Data Source off the caller's
    # permission listing, so judging either against the alias listing would call every one of them
    # gone — and send a creator to remove a Model API that is deployed and running.
    api = Binding(KIND_MODEL_API, "f-churn", "churn-risk", "churn-risk")
    source = Binding(KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse",
                     "Snowflake-Data-Warehouse")
    assert stale_bindings([api, source], ALIAS_LISTING) == []
    assert stale_bindings([api, source],
                          {**ALIAS_LISTING, "model_api": MODEL_APIS,
                           "data_source": DATA_SOURCES}) == []


def test_each_kind_is_reported_when_its_own_listing_has_lost_it():
    api = Binding(KIND_MODEL_API, "id-gone", "gone-api", "gone-api")
    source = Binding(KIND_DATA_SOURCE, "ds-gone", "gone-source", "gone-source")
    listings = {"llm_alias": ALIASES, "model_api": MODEL_APIS, "data_source": []}
    assert stale_bindings([api, source], listings) == [api, source]


def test_a_listing_that_did_not_arrive_judges_nothing():
    # None is "we could not check", which is a different answer from an empty listing — only a
    # listing that ARRIVED can prove that something missing from it is gone.
    api = Binding(KIND_MODEL_API, "f-churn", "churn-risk", "churn-risk")
    assert stale_bindings([api], {"model_api": None}) == []
    assert stale_bindings([api], {"model_api": []}) == [api]


def test_one_listing_failing_does_not_suppress_another():
    # A gateway that is down says nothing about whether a Data Source still exists, and withholding
    # that because a different call failed would lose the one thing Sage did learn.
    alias = _binding("id-gone", "gone-alias")
    source = Binding(KIND_DATA_SOURCE, "ds-gone", "gone-source", "gone-source")
    assert stale_bindings([alias, source], {"llm_alias": None, "data_source": []}) == [source]


def test_each_kind_gets_the_sentence_that_leads_where_its_fix_is():
    # Three reasons, three screens. One message for all of them would send two thirds of the people
    # who read it to the wrong place.
    assert "LLM Gateway" in stale_fault(_binding("id", "a"))
    assert "deployed in this project" in stale_fault(
        Binding(KIND_MODEL_API, "id", "churn", "churn"))
    assert "permission on" in stale_fault(Binding(KIND_DATA_SOURCE, "id", "dwh", "dwh"))


def test_a_model_api_whose_token_has_gone_is_reported_too():
    # The failure the other two kinds have no equivalent for: an Alias is called with the viewer's
    # own session and a Data Source through the sidecar, but a Model API opens for nothing except a
    # token someone pasted (#9).
    api = Binding(KIND_MODEL_API, "f-churn", "churn-risk", "churn-risk")
    assert missing_credentials([api], held=set()) == [api]
    assert missing_credentials([api], held={"f-churn"}) == []
    assert missing_credentials([api], held=None) == []      # not looked at is not "gone"


# ---- Bindings: through the orchestrator, which is what session open calls ------------------------


def test_session_open_reports_nothing_for_an_app_with_no_bindings(tmp_path):
    assert _orch(tmp_path).preflight_bindings() == {"state": "ok", "error": None, "bindings": []}


def test_an_app_with_no_bindings_makes_no_gateway_call(tmp_path):
    # The common case at session open, and it must cost nothing: a dead gateway is still "ok" here
    # because there was nothing to ask about.
    orch = _orch(tmp_path, resources=_DeadGateway())
    assert orch.preflight_bindings()["state"] == "ok"


def test_session_open_reports_a_binding_whose_resource_has_gone(tmp_path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    # The Alias is de-registered, or the grant is withdrawn — both leave the listing without it.
    orch._resources = FakeResourceProvider([ALIASES[1]])
    result = orch.preflight_bindings()
    assert result["state"] == "problems"
    (b,) = result["bindings"]
    assert b["id"] == "id-sonnet"
    # The label the row showed when the choice was made — the only version the creator ever saw.
    assert "Claude Sonnet 4.6" in b["message"]


def test_session_open_leaves_a_still_valid_binding_alone(tmp_path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    assert orch.preflight_bindings() == {"state": "ok", "error": None, "bindings": []}


def test_a_dead_gateway_does_not_call_a_recorded_binding_stale(tmp_path):
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    orch._resources = _DeadGateway()
    result = orch.preflight_bindings()
    assert result["state"] == "unreachable"
    assert result["bindings"] == []


def test_the_binding_check_survives_an_orchestrator_restart(tmp_path):
    # The manifest is committed, the in-memory list is not. A stale Binding must still be caught by
    # the next session, which is the whole point of checking at session open.
    orch = _orch(tmp_path)
    orch.bind_llm_alias("id-sonnet")
    reopened = _orch(tmp_path, resources=FakeResourceProvider([ALIASES[1]]))
    assert [b["id"] for b in reopened.preflight_bindings()["bindings"]] == ["id-sonnet"]


# ---- through the routes --------------------------------------------------------------------------


def _client(tmp_path: Path, monkeypatch, **kw):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod, "orchestrator", _orch(tmp_path, **kw))
    # The startup verdict is process-wide state that _run_slot_preflight reassigns. Through
    # monkeypatch so a test that fires the check cannot leave its verdict behind for another one.
    monkeypatch.setattr(appmod, "PREFLIGHT_SLOTS", appmod.PREFLIGHT_SLOTS)
    return appmod, TestClient(appmod.control_app)


def test_healthz_carries_the_startup_slot_verdict(tmp_path: Path, monkeypatch):
    # /healthz is already the one call that answers "is this builder correctly wired", and the UI
    # already makes it on load, so the slot verdict rides along rather than earning its own route.
    appmod, client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "GATEWAY_MODE", "domino")
    appmod._run_slot_preflight()
    assert client.get("/healthz").json()["preflight_slots"] == {
        "state": "ok", "error": None, "slots": [], "reached": True}


def test_startup_logs_an_error_naming_the_slot_and_the_alias(tmp_path: Path, monkeypatch, caplog):
    # Loud, and loud in the place a maintainer looks: an ERROR line, before any build has run.
    appmod, client = _client(tmp_path, monkeypatch, catalog=replace(GOOD_CATALOG, ask="ghost-model"))
    monkeypatch.setattr(appmod, "GATEWAY_MODE", "domino")
    with caplog.at_level("ERROR", logger="sage.orchestrator"):
        appmod._run_slot_preflight()
    assert "ask" in caplog.text and "ghost-model" in caplog.text
    assert client.get("/healthz").json()["preflight_slots"]["state"] == "problems"


def test_openai_gateway_mode_skips_the_slot_check(tmp_path: Path, monkeypatch):
    # Each model routes to its own vendor there, so there is no LLM Gateway holding an Alias list.
    # Checking anyway would report all six slots missing — a loud check that is loudly wrong.
    appmod, client = _client(tmp_path, monkeypatch, catalog=replace(GOOD_CATALOG, ask="ghost-model"))
    monkeypatch.setattr(appmod, "GATEWAY_MODE", "openai")
    appmod._run_slot_preflight()
    assert client.get("/healthz").json()["preflight_slots"]["state"] == "skipped"


# `/api/preflight` is gone (ADR-0027): it had no caller, and `preflight_bindings` is one of the six
# reads `/api/health` composes. Both halves of what it proved are asserted there —
# `test_a_problem_says_what_broke_and_who_owns_it.py`.


# ---- Bindings of every kind, through the orchestrator (#23) ---------------------------------------


def _bound(tmp_path, *, model_api=False, data_source=False, resources=None):
    """An orchestrator with the asked-for Binding kinds already recorded."""
    from sage.resources.model_api_credentials import Credential, CredentialStore

    orch = _orch(tmp_path, resources=resources)
    orch.bind_llm_alias("id-sonnet")
    if model_api:
        # Straight into the store, as test_bindings.py does: `save_model_api_credential` calls the
        # model to verify the token, and the paste has its own tests.
        CredentialStore(orch.project().workspace.path).put(
            "f-churn", Credential("https://dogfood.example/models/id-churn/latest/model", "t" * 64))
        orch.bind_model_api("f-churn")
    if data_source:
        orch.bind_data_source("ds-dwh", "DWH", "MARTS")
    return orch


def test_a_data_source_that_has_gone_is_reported_at_session_open(tmp_path):
    # Today this is caught only at publish, by #12's guard — the right refusal in the wrong place,
    # because the creator has already built against it.
    orch = _bound(tmp_path, data_source=True)
    orch._resources.data_sources = [s for s in orch._resources.data_sources if s.id != "ds-dwh"]
    result = orch.preflight_bindings()
    assert result["state"] == "problems"
    (row,) = result["bindings"]
    assert row["kind"] == KIND_DATA_SOURCE
    assert "permission on" in row["message"]


def test_a_model_api_that_is_no_longer_deployed_is_reported_at_session_open(tmp_path):
    orch = _bound(tmp_path, model_api=True)
    orch._resources.model_apis = [m for m in orch._resources.model_apis if m.id != "f-churn"]
    result = orch.preflight_bindings()
    assert [b["kind"] for b in result["bindings"]] == [KIND_MODEL_API]
    assert "deployed in this project" in result["bindings"][0]["message"]


def test_bindings_of_every_kind_that_are_all_fine_report_nothing(tmp_path):
    orch = _bound(tmp_path, model_api=True, data_source=True)
    assert orch.preflight_bindings() == {"state": "ok", "error": None, "bindings": []}


def test_a_kind_whose_listing_could_not_be_fetched_is_not_called_gone(tmp_path):
    orch = _bound(tmp_path, data_source=True)

    def refuse():
        raise ResourceUnavailable("Domino did not answer.")

    orch._resources.list_data_sources = refuse
    result = orch.preflight_bindings()
    assert result["state"] == "unreachable"
    assert result["bindings"] == []
    assert "Domino did not answer." in result["error"]


def test_one_listing_failing_still_reports_what_another_answered(tmp_path):
    # Criterion 4. A dead gateway says nothing about whether a Data Source still exists.
    orch = _bound(tmp_path, data_source=True)
    orch._resources.data_sources = [s for s in orch._resources.data_sources if s.id != "ds-dwh"]

    def refuse():
        raise ResourceUnavailable("The LLM Gateway did not answer.")

    orch._resources.list_llm_aliases = refuse
    result = orch.preflight_bindings()
    assert result["state"] == "problems"            # what we learned outranks what we could not
    assert [b["kind"] for b in result["bindings"]] == [KIND_DATA_SOURCE]
    assert "LLM Gateway did not answer." in result["error"]


def test_a_kind_with_no_bindings_is_never_listed(tmp_path):
    # Criterion 5. An app that uses one Alias and nothing else makes the same single request it made
    # before #23.
    orch = _bound(tmp_path)
    asked = []
    for kind, name in (("model_api", "list_model_apis"), ("data_source", "list_data_sources")):
        setattr(orch._resources, name, lambda *a, _k=kind, **k: asked.append(_k) or [])
    orch.preflight_bindings()
    assert asked == []


def _drop_tokens(orch) -> None:
    """Lose the stored tokens the way a real app does: the Binding manifest is committed and the
    token store is gitignored, so a fresh clone has the record and none of the credentials."""
    from sage.resources.model_api_credentials import CredentialStore

    CredentialStore(orch.project().workspace.path).path.unlink()


def test_a_model_api_whose_token_has_gone_is_reported(tmp_path):
    orch = _bound(tmp_path, model_api=True)
    _drop_tokens(orch)
    result = orch.preflight_bindings()
    assert [b["kind"] for b in result["bindings"]] == [KIND_MODEL_API]
    assert "access token" in result["bindings"][0]["message"]


def test_a_model_api_that_is_gone_is_reported_once_not_twice(tmp_path):
    # It is the same Binding to remove, and "no longer deployed" is the more useful half of why.
    orch = _bound(tmp_path, model_api=True)
    orch._resources.model_apis = [m for m in orch._resources.model_apis if m.id != "f-churn"]
    _drop_tokens(orch)
    assert len(orch.preflight_bindings()["bindings"]) == 1


# ---- endpoints: an Alias that resolves, behind a model that will not answer (#21) ----------------
#
# The gap these close is narrow and was measured rather than assumed. `/v1/models` filters on
# permission alone, so a granted Alias whose Hosted GenAI Endpoint is stopped is STILL offered —
# `unresolved_slots` sees nothing wrong, preflight passes, and the build dies partway through on a
# gateway error. Everything below is about saying that earlier, and about the four different ways of
# knowing nothing, which must stay silent rather than become "this is broken".

HOSTED = [
    LlmAlias("id-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)", None, ["chat"], {},
             "https://apps.example.tech/endpoints/308f788c/v1"),
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0}),
]


def _endpoint(status: str | None, url: str = "https://apps.example.tech/endpoints/308f788c",
              name: str = "qwen-2-5") -> HostedEndpoint:
    return HostedEndpoint("308f788c", name, url, status)


def _hosted_catalog(**over) -> ModelCatalog:
    return replace(ModelCatalog(sovereign_plan="qwen-2-5", sovereign_implement="sonnet",
                                sovereign_ask="sonnet", plan="sonnet", implement="sonnet",
                                ask="sonnet"), **over)


def test_a_slot_whose_endpoint_is_stopped_is_reported():
    (p,) = slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint("Stopped")])
    assert (p.slot, p.alias, p.endpoint, p.status) == (
        "sovereign_plan", "qwen-2-5", "qwen-2-5", "Stopped")


def test_a_slot_whose_endpoint_is_running_is_not_reported():
    assert slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint("Running")]) == []


def test_the_remedy_for_a_stopped_endpoint_is_to_start_it():
    (p,) = slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint("Stopped")])
    assert "Start that endpoint" in p.message
    assert "sovereign_plan" in p.message and "qwen-2-5" in p.message


def test_the_remedy_for_a_broken_endpoint_is_to_pick_another_not_to_start_it():
    # The distinction #21's second criterion turns on: telling someone to start an endpoint that
    # failed to build sends them somewhere nothing can be done.
    for status in ("Failed", "BuildFailed"):
        (p,) = slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint(status)])
        assert "needs its owner to fix it" in p.message
        assert "Start that endpoint" not in p.message


def test_the_remedy_for_an_endpoint_mid_transition_is_to_wait():
    for status in ("Building", "Starting", "Stopping"):
        (p,) = slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint(status)])
        assert "wait for it" in p.message


def test_an_unknown_status_is_never_reported_as_stopped():
    # Domino's own word for "we do not know". Reporting it would turn "could not check" into "this
    # is broken", which is exactly what the fourth criterion forbids.
    assert slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint("Unknown")]) == []


def test_an_endpoint_with_no_current_version_has_no_status_and_is_not_reported():
    assert slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint(None)]) == []


def test_a_status_this_sage_does_not_recognise_stays_silent():
    # A newer platform adding a status must not make every slot read as broken.
    assert slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint("Hibernating")]) == []


def test_an_alias_with_no_endpoint_url_is_never_judged_on_an_endpoint():
    # The common case, not an edge one: 12 of 14 aliases on cloud-dogfood are vendor models with
    # nothing on Domino behind them. A vendor model must never read as "stopped".
    catalog = _hosted_catalog(sovereign_plan="sonnet")
    assert slots_on_dead_endpoints(catalog, HOSTED, [_endpoint("Stopped")]) == []


def test_a_listing_that_did_not_arrive_reports_nothing():
    # Same rule `stale_bindings` applies to a listing that is None. Not being able to check is not
    # evidence that anything is wrong.
    assert slots_on_dead_endpoints(_hosted_catalog(), HOSTED, None) == []


def test_an_alias_pointing_at_an_endpoint_that_is_not_in_the_listing_reports_nothing():
    assert slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint("Stopped", url="https://x/other")]) == []


def test_the_join_ignores_a_trailing_slash_on_either_side():
    aliases = [replace(HOSTED[0], endpoint_url="https://apps.example.tech/endpoints/308f788c/v1/")]
    ends = [_endpoint("Stopped", url="https://apps.example.tech/endpoints/308f788c/")]
    assert len(slots_on_dead_endpoints(_hosted_catalog(), aliases, ends)) == 1


def test_a_missing_alias_is_reported_once_as_missing_and_not_also_as_stopped():
    # The two checks share the UI's per-slot warning key, so a slot reported by both would leak one
    # of the two warnings. They cannot collide: a missing Alias has no record to carry an endpoint.
    catalog = _hosted_catalog(sovereign_plan="ghost-model")
    assert [p.slot for p in unresolved_slots(catalog, HOSTED)] == ["sovereign_plan"]
    assert slots_on_dead_endpoints(catalog, HOSTED, [_endpoint("Stopped")]) == []


def test_every_slot_on_a_dead_endpoint_is_reported_not_just_the_first():
    catalog = _hosted_catalog(sovereign_implement="qwen-2-5", ask="qwen-2-5")
    assert [p.slot for p in slots_on_dead_endpoints(catalog, HOSTED, [_endpoint("Stopped")])] == [
        "sovereign_plan", "sovereign_implement", "ask"]


def test_the_slot_report_carries_the_status_verbatim_for_the_log():
    (p,) = slots_on_dead_endpoints(_hosted_catalog(), HOSTED, [_endpoint("BuildFailed")])
    assert p.to_dict()["status"] == "BuildFailed"
    assert p.to_dict()["endpoint"] == "qwen-2-5"


# ---- endpoints: the same question asked of a Binding ---------------------------------------------


def test_a_binding_whose_endpoint_is_stopped_is_reported():
    b = _binding("id-qwen", "qwen-2-5", "Qwen 2.5 (Domino-hosted)")
    ((got, fault, status),) = bindings_on_dead_endpoints([b], HOSTED, [_endpoint("Stopped")])
    assert got is b
    # The status travels beside the sentence, for the rail's chip. A chip reading "Gone" here would
    # send the creator to remove an Alias that is registered, granted and offered.
    assert status == "Stopped"
    assert "Qwen 2.5 (Domino-hosted)" in fault and "is Stopped" in fault
    # The remedy is the other half, composed from the status the caller already holds. A Binding is
    # changed in the Resources rail, so its fallback is an Alias, not a "model".
    assert endpoint_binding_fix(status) == "Start that endpoint, or pick a different Alias, " \
                                           "before you build on it."


def test_a_binding_whose_endpoint_is_running_is_not_reported():
    b = _binding("id-qwen", "qwen-2-5")
    assert bindings_on_dead_endpoints([b], HOSTED, [_endpoint("Running")]) == []


def test_a_binding_of_another_kind_is_never_judged_on_an_endpoint():
    b = Binding(KIND_DATA_SOURCE, "ds-1", "Snowflake-Data-Warehouse", "Snowflake")
    assert bindings_on_dead_endpoints([b], HOSTED, [_endpoint("Stopped")]) == []


# ---- endpoints: through the orchestrator, which is what startup and session open call -------------


class _HostedProvider(FakeResourceProvider):
    """A gateway whose one alias is Domino-hosted, so the endpoint listing is actually consulted."""

    def list_llm_aliases(self) -> list[LlmAlias]:
        return list(HOSTED)


class _CountingProvider(_HostedProvider):
    """Counts the endpoint call, because "no call per slot" is an acceptance criterion, and the
    ordinary gateway must pay nothing at all."""

    calls = 0

    def list_hosted_endpoints(self) -> list[HostedEndpoint]:
        type(self).calls += 1
        return [_endpoint("Stopped")]


class _VendorOnlyCounting(_CountingProvider):
    def list_llm_aliases(self) -> list[LlmAlias]:
        return [HOSTED[1]]  # sonnet, no endpoint_url


class _DeadEndpointListing(_HostedProvider):
    def list_hosted_endpoints(self) -> list[HostedEndpoint]:
        raise ResourceUnavailable("The Domino API did not answer (ConnectError).")


def test_startup_reports_a_slot_whose_endpoint_is_stopped(tmp_path):
    orch = _orch(tmp_path, catalog=_hosted_catalog(), resources=_CountingProvider())
    result = orch.preflight_slots()
    assert result["state"] == "problems"
    assert [(s["slot"], s["status"]) for s in result["slots"]] == [("sovereign_plan", "Stopped")]


# The same slash-bearing name, but Domino-hosted, so the endpoint join has to survive it too.
SLASH_HOSTED = [
    LlmAlias("id-qwen", "domino/qwen-2-5", "Qwen 2.5 (Domino-hosted)", None, ["chat"], {},
             "https://apps.example.tech/endpoints/308f788c/v1"),
    HOSTED[1],
]


class _SlashNamedHosted(_CountingProvider):
    """A gateway whose hosted Alias has a slash in its own name, which is cloud-dogfood's shape."""

    def list_llm_aliases(self) -> list[LlmAlias]:
        return list(SLASH_HOSTED)


def test_a_slash_named_alias_is_still_joined_to_the_endpoint_behind_it():
    (p,) = slots_on_dead_endpoints(_hosted_catalog(sovereign_plan="domino/qwen-2-5"),
                                   SLASH_HOSTED, [_endpoint("Stopped")])
    assert (p.slot, p.alias, p.status) == ("sovereign_plan", "domino/qwen-2-5", "Stopped")


def test_startup_still_asks_about_the_endpoint_behind_a_slash_named_alias(tmp_path):
    """The endpoint call is keyed on the Alias names the slots actually name, so a slot reduced to a
    bare id skipped the call altogether and a stopped endpoint went unreported."""
    result = _orch(tmp_path, catalog=_hosted_catalog(sovereign_plan="domino/qwen-2-5"),
                   resources=_SlashNamedHosted()).preflight_slots()
    assert result["state"] == "problems"
    assert [(s["slot"], s["status"]) for s in result["slots"]] == [("sovereign_plan", "Stopped")]


def test_startup_makes_one_endpoint_call_for_six_slots(tmp_path):
    # Three of the six slots point at the hosted alias; the criterion is no call per slot.
    _CountingProvider.calls = 0
    catalog = _hosted_catalog(sovereign_implement="qwen-2-5", ask="qwen-2-5")
    _orch(tmp_path, catalog=catalog, resources=_CountingProvider()).preflight_slots()
    assert _CountingProvider.calls == 1


def test_a_gateway_with_no_hosted_alias_makes_no_endpoint_call_at_all(tmp_path):
    # The ordinary case. 12 of 14 aliases on cloud-dogfood are vendor models, and a check that can
    # answer nothing is a call worth not making.
    _VendorOnlyCounting.calls = 0
    catalog = _hosted_catalog(sovereign_plan="sonnet")
    result = _orch(tmp_path, catalog=catalog, resources=_VendorOnlyCounting()).preflight_slots()
    assert _VendorOnlyCounting.calls == 0
    assert result == {"state": "ok", "error": None, "slots": [], "reached": True}


def test_an_endpoint_listing_that_will_not_answer_is_unreachable_not_a_broken_slot(tmp_path):
    result = _orch(tmp_path, catalog=_hosted_catalog(),
                   resources=_DeadEndpointListing()).preflight_slots()
    assert result["state"] == "unreachable"
    assert result["slots"] == []
    assert "Domino API" in result["error"]


def test_a_broken_slot_still_outranks_an_endpoint_listing_that_failed(tmp_path):
    # One listing failing does not unlearn what the other answered.
    catalog = _hosted_catalog(plan="ghost-model")
    result = _orch(tmp_path, catalog=catalog, resources=_DeadEndpointListing()).preflight_slots()
    assert result["state"] == "problems"
    assert [s["slot"] for s in result["slots"]] == ["plan"]
    assert "Domino API" in result["error"]


def test_session_open_reports_a_binding_whose_endpoint_is_stopped(tmp_path):
    orch = _orch(tmp_path, catalog=_hosted_catalog(), resources=_CountingProvider())
    orch.bind_llm_alias("id-qwen")
    result = orch.preflight_bindings()
    assert result["state"] == "problems"
    ((one,),) = ([b for b in result["bindings"]],)
    assert one["id"] == "id-qwen" and "is Stopped" in one["message"]


def test_the_status_reaches_the_rail_so_the_chip_does_not_read_gone(tmp_path: Path):
    # Found by QA-ing the real UI, not by a unit test: the rail badges a stale Binding "Gone", and an
    # Alias on a stopped endpoint rode the same channel and inherited that word. It is registered,
    # granted and still offered — "Gone" points at the wrong remedy, which is the one thing #21's
    # second criterion exists to stop.
    orch = _orch(tmp_path, catalog=_hosted_catalog(), resources=_CountingProvider())
    orch.bind_llm_alias("id-qwen")

    (row,) = orch.preflight_bindings()["bindings"]

    assert row["status"] == "Stopped"


def test_a_binding_that_has_gone_carries_no_status_to_confuse_the_chip(tmp_path: Path):
    # The other half: a Resource that really is gone must not gain a status field, or the chip would
    # stop saying the one word that IS right for it.
    orch = _orch(tmp_path, resources=FakeResourceProvider(list(ALIASES)))
    orch.bind_llm_alias("id-sonnet")
    orch._resources = FakeResourceProvider([])   # the gateway now offers nothing

    (row,) = orch.preflight_bindings()["bindings"]

    assert "status" not in row


def test_both_slot_checks_report_in_slots_order(tmp_path: Path):
    # Also found in the UI: `implement` rendered above `sovereign_plan`, because the two checks were
    # concatenated rather than merged. Each is ordered on its own; the pair was not.
    catalog = _hosted_catalog(sovereign_plan="qwen-2-5", implement="ghost-model")
    result = _orch(tmp_path, catalog=catalog, resources=_CountingProvider()).preflight_slots()

    assert [s["slot"] for s in result["slots"]] == ["sovereign_plan", "implement"]


class _MixedGateway(_CountingProvider):
    """A gateway that offers hosted Aliases the app does not use — cloud-dogfood's real shape, and
    the one the all-vendor fake could not produce."""

    def list_llm_aliases(self) -> list[LlmAlias]:
        return list(HOSTED)   # qwen-2-5 is hosted; sonnet is not


def test_a_gateway_with_hosted_aliases_the_app_does_not_use_still_pays_nothing(tmp_path: Path):
    # Found against the real gateway, not by a unit test: keying the skip on "does this gateway offer
    # any hosted Alias" is always true on cloud-dogfood, so every session open paid a round trip for
    # an answer that could not apply. It has to key on what is actually being checked.
    _MixedGateway.calls = 0
    catalog = _hosted_catalog(sovereign_plan="sonnet")   # no slot names the hosted alias
    result = _orch(tmp_path, catalog=catalog, resources=_MixedGateway()).preflight_slots()

    assert _MixedGateway.calls == 0
    assert result == {"state": "ok", "error": None, "slots": [], "reached": True}


def test_a_binding_on_a_vendor_alias_pays_nothing_either(tmp_path: Path):
    _MixedGateway.calls = 0
    orch = _orch(tmp_path, catalog=_hosted_catalog(sovereign_plan="sonnet"), resources=_MixedGateway())
    orch.bind_llm_alias("id-sonnet")

    assert orch.preflight_bindings()["state"] == "ok"
    assert _MixedGateway.calls == 0
