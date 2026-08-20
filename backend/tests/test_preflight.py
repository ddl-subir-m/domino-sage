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
from sage.resources.bindings import Binding
from sage.resources.preflight import stale_bindings, unresolved_slots
from sage.resources.provider import FakeResourceProvider, LlmAlias, ResourceUnavailable
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
    assert p.to_dict() == {"slot": "sovereign_implement", "alias": "ghost-model", "message": p.message}


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


def test_an_empty_gateway_reports_every_slot():
    # No aliases at all is the shape of a gateway the caller has no grants on. It must not read as
    # "everything is fine because there was nothing to compare against".
    assert len(unresolved_slots(GOOD_CATALOG, [])) == 6


# ---- model slots: through the orchestrator, which is what startup calls --------------------------


def test_startup_reports_ok_when_the_configured_models_all_exist(tmp_path):
    assert _orch(tmp_path).preflight_slots() == {"state": "ok", "error": None, "slots": []}


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


def test_a_binding_whose_alias_is_still_offered_is_not_stale():
    assert stale_bindings([_binding("id-sonnet", "sonnet")], ALIASES) == []


def test_a_binding_whose_resource_has_gone_is_stale():
    gone = _binding("id-gpt", "gpt-5.4", "gpt-5.4")
    assert stale_bindings([gone, _binding("id-sonnet", "sonnet")], ALIASES) == [gone]


def test_a_binding_still_resolves_when_only_its_id_has_changed():
    # The manifest keeps id AND name; the control plane keys on id, a model call keys on name. An
    # Alias re-registered under a new id is the same Resource, and calling it stale would send a
    # creator to remove something that works.
    assert stale_bindings([_binding("stale-id", "sonnet")], ALIASES) == []


def test_a_binding_of_a_kind_this_sage_cannot_check_is_left_alone():
    # A newer Sage's record. Not being able to check something is not evidence that it is gone.
    other = Binding("data_source", "ds-1", "warehouse", "Warehouse")
    assert stale_bindings([other], ALIASES) == []


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
        "state": "ok", "error": None, "slots": []}


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


def test_the_preflight_route_reports_a_stale_binding(tmp_path: Path, monkeypatch):
    appmod, client = _client(tmp_path, monkeypatch)
    appmod.orchestrator.bind_llm_alias("id-sonnet")
    appmod.orchestrator._resources = FakeResourceProvider([ALIASES[1]])
    body = client.get("/api/preflight").json()["bindings"]
    assert body["state"] == "problems"
    assert [b["id"] for b in body["bindings"]] == ["id-sonnet"]


def test_the_preflight_route_is_200_even_when_the_gateway_will_not_answer(tmp_path: Path, monkeypatch):
    # "We could not check" is a state, not a failed request. A 502 here would read to the UI exactly
    # like the rail's, where it means "you have no models".
    appmod, client = _client(tmp_path, monkeypatch)
    appmod.orchestrator.bind_llm_alias("id-sonnet")
    appmod.orchestrator._resources = _DeadGateway()
    r = client.get("/api/preflight")
    assert r.status_code == 200
    assert r.json()["bindings"]["state"] == "unreachable"
