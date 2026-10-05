"""A model's reasoning levels can be measured again from the Model assignments drawer (#646).

Measuring on first use covers a new model and a repointed one. What it cannot cover is a
measurement that failed (it waits an hour), a gateway that changed behaviour without moving
`updated_at`, and a checked-in row that is wrong. Re-check is the door for those, and because it
sends the model ~30 test requests, it is closed on any model the sensitivity lock bars.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage.gateway.capabilities import RouteCapability, RouteStatus, evidence
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import MEASURING_NOTE, DominoResourceProvider, FakeResourceProvider
from tests.test_a_person_picks_the_model_a_mode_runs_on import ALIASES, CATALOG, _template
from tests.test_an_alias_is_measured_before_it_is_offered_a_level import Gateway
from tests.test_the_model_panel_lets_a_person_choose import _drawn, _lock

# ---- the drawer -----------------------------------------------------------------------------------


def test_a_measured_and_an_unmeasured_row_both_offer_a_recheck():
    drawn = _drawn([{}])[0]
    assert [r["id"] for r in drawn["rechecks"]] == ["recheck-plan", "recheck-implement",
                                                    "recheck-ask"]


def test_a_row_on_a_model_with_no_gateway_route_offers_none():
    drawn = _drawn([{"seed": {"plan": {"model": "opus"}}}])[0]
    assert "recheck-plan" not in [r["id"] for r in drawn["rechecks"]]


def test_a_recheck_measures_that_model_and_the_row_follows_it_until_it_lands():
    drawn = _drawn([{"recheck": "implement", "recheckLands": {
        "reasoning_status": "verified", "reasoning_efforts_with_tools": ["low", "high"]}}])[0]

    assert drawn["wrote"] == [{"recheck": "coder"}]
    assert drawn["recheckReads"][:2] == ["measuring", "measuring"]
    assert drawn["recheckReads"][-1] == "verified", "the drawer stopped reading before it landed"
    assert [o["value"] for o in drawn["afterEfforts"]][-2:] == ["low", "high"]


def test_while_it_measures_the_row_says_so_and_offers_no_second_recheck():
    drawn = _drawn([{"recheck": "implement", "measuringReads": 99, "polls": 1}])[0]

    assert "recheck-implement" not in drawn["afterRechecks"]
    assert any(n.startswith("Checking which reasoning settings") for n in drawn["afterEffortLimits"])


def test_a_refused_recheck_says_why_on_the_row():
    refusal = "coder isn't approved while the sensitivity lock is on, so Sage won't send it test requests."
    drawn = _drawn([{"recheck": "implement", "recheckRefused": refusal}])[0]

    assert drawn["afterRecheckErrors"] == [refusal]


def test_a_model_the_lock_bars_is_not_offered_a_recheck():
    drawn = _drawn([{"sensitivity": _lock()}])[0]
    # gpt-5.4 holds plan and ask and is barred; coder holds implement and is approved.
    assert [r["id"] for r in drawn["rechecks"]] == ["recheck-implement"]


# ---- the provider ---------------------------------------------------------------------------------

PROOF = next(r for r in evidence() if r["efforts"] and not r.get("fallback_chain"))
ALIAS = {k: v for k, v in PROOF.items() if k in (
    "id", "name", "provider_id", "provider_type", "provider_model", "updated_at")}
NAME = PROOF["name"]


def _provider(tmp_path, gateway) -> DominoResourceProvider:
    provider = DominoResourceProvider(PROOF["gateway"] + "/v1", lambda: "token")
    provider._get = lambda path: {"data": [{"id": NAME}]} if path == "/v1/models" else [ALIAS]
    provider._gateway_call = gateway
    provider.use_local_evidence(tmp_path / ".sage" / "reasoning-evidence.json")
    return provider


def _settle(provider: DominoResourceProvider) -> None:
    if provider._worker is not None:
        provider._worker.join(timeout=5)


def _gateway_for(proof: dict, accepts: tuple[str, ...]) -> Gateway:
    """A gateway that answers on the proof's own wire, so the re-measured row keeps its protocol."""
    wire = proof["protocol"]
    return Gateway(native=wire == "responses", validates=(wire,), accepts=accepts)


def test_a_recheck_replaces_a_checked_in_row_with_what_the_gateway_says_now(tmp_path):
    provider = _provider(tmp_path, _gateway_for(PROOF, ("low",)))
    assert provider.reasoning_capability(NAME).status is RouteStatus.VERIFIED

    provider.recheck_reasoning(NAME)
    _settle(provider)

    capability = provider.reasoning_capability(NAME)
    assert capability.status is RouteStatus.VERIFIED
    assert capability.efforts == ("low",), "the checked-in row still won over a fresh measurement"


def test_a_measured_row_keeps_its_levels_while_it_is_rechecked(tmp_path):
    import threading
    release = threading.Event()
    gateway = _gateway_for(PROOF, ("low",))

    def slow(url, body):
        release.wait(timeout=5)
        return gateway(url, body)

    provider = _provider(tmp_path, slow)
    provider.recheck_reasoning(NAME)
    try:
        capability = provider.reasoning_capability(NAME)
        assert capability.status is RouteStatus.VERIFIED, "turns meanwhile run on the old row"
        assert capability.efforts == tuple(PROOF["efforts"])
        assert capability.reason == MEASURING_NOTE
        assert provider.is_measuring(capability.identity)
    finally:
        release.set()
        _settle(provider)


def test_a_recheck_that_fails_keeps_the_old_levels_and_says_why(tmp_path):
    provider = _provider(tmp_path, Gateway(control=400))
    provider.recheck_reasoning(NAME)
    _settle(provider)

    capability = provider.reasoning_capability(NAME)
    assert capability.efforts == tuple(PROOF["efforts"])
    assert "last re-check didn't finish" in capability.reason
    assert "quota spent until 2026-11-01" in capability.reason


def test_a_recheck_does_not_wait_out_the_hour_a_failure_earns(tmp_path):
    gateway = Gateway(control=400)
    provider = _provider(tmp_path, gateway)
    provider.recheck_reasoning(NAME)
    _settle(provider)
    asked = len(gateway.sent)

    provider.recheck_reasoning(NAME)
    _settle(provider)

    assert len(gateway.sent) > asked


def test_a_fallback_route_cannot_be_rechecked(tmp_path):
    provider = _provider(tmp_path, Gateway())
    provider._get = lambda path: ({"data": [{"id": NAME}]} if path == "/v1/models"
                                  else [{**ALIAS, "fallback_chain": ["other"]}])
    with pytest.raises(ValueError, match="fallback"):
        provider.recheck_reasoning(NAME)


# ---- the service ----------------------------------------------------------------------------------

IDENTITY = ("https://gw.example", "id-opus", "opus", "", "", "", "")


class _Rechecking(FakeResourceProvider):
    def __init__(self, aliases):
        super().__init__(aliases)
        self.rechecked: list[str] = []
        self.measuring: set = set()

    def recheck_reasoning(self, model: str) -> None:
        self.rechecked.append(model)

    def is_measuring(self, identity) -> bool:
        return identity in self.measuring


def _orch(tmp_path: Path) -> tuple[Orchestrator, _Rechecking]:
    aliases = [a if a.name != "opus" else replace(a, route_capability=RouteCapability(
        efforts=("low",), efforts_with_tools=("low",), identity=IDENTITY,
        status=RouteStatus.VERIFIED)) for a in ALIASES]
    resources = _Rechecking(aliases)
    orch = Orchestrator(workspace_dir=tmp_path / "mnt" / "code", template=_template(tmp_path),
                        gateway=FakeGatewayClient(), catalog=CATALOG, project_id="Sage",
                        resources=resources, cost_project_label="my-app")
    orch.project(start_preview=False)
    return orch, resources


def test_the_lock_refuses_a_recheck_of_a_model_it_bars(tmp_path):
    orch, resources = _orch(tmp_path)
    orch._sensitivity_for_turn = lambda *_a, **_k: (SimpleNamespace(names={"opus"}), "")

    with pytest.raises(ValueError, match="isn't approved"):
        orch.recheck_reasoning("sonnet")
    orch.recheck_reasoning("opus")

    assert resources.rechecked == ["opus"]


def test_a_lock_that_resolves_to_nothing_refuses_every_recheck(tmp_path):
    orch, resources = _orch(tmp_path)
    orch._sensitivity_for_turn = lambda *_a, **_k: (None, "No approved model is available.")

    with pytest.raises(ValueError, match="No approved model"):
        orch.recheck_reasoning("opus")
    assert resources.rechecked == []


def test_the_route_answers_at_once_and_carries_the_refusal(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    orch, resources = _orch(tmp_path)
    orch._sensitivity_for_turn = lambda *_a, **_k: (SimpleNamespace(names={"opus"}), "")
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    assert client.post("/api/project/model/recheck", json={"model": "opus"}).status_code == 202
    barred = client.post("/api/project/model/recheck", json={"model": "sonnet"})
    assert barred.status_code == 400 and "isn't approved" in barred.json()["error"]
    assert client.post("/api/project/model/recheck", json={}).status_code == 400
    assert resources.rechecked == ["opus"]


def test_the_drawer_is_told_which_rows_can_be_rechecked_and_which_are_measuring(tmp_path):
    orch, resources = _orch(tmp_path)
    rows = {a["name"]: a for a in orch.model_assignments()["aliases"]}
    assert rows["opus"]["reasoning_recheck"] is True
    assert rows["opus"]["reasoning_status"] == "verified"
    assert rows["sonnet"]["reasoning_recheck"] is False, "no gateway route, nothing to measure"

    resources.measuring.add(IDENTITY)
    rows = {a["name"]: a for a in orch.model_assignments()["aliases"]}
    assert rows["opus"]["reasoning_status"] == "measuring"
