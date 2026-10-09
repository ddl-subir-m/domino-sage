"""The first turn on an unmeasured Alias waits for its measurement instead of failing (#724).

Dogfood of #714, prompt 7: "Write a plan" on sonnet at a saved reasoning effort of `medium` failed
with "sonnet cannot use the saved reasoning setting 'medium'… Repair: scripts/reasoning-evidence.py
'sonnet' --write". The failed turn had itself started the measurement (#646), a moment later the
model assignments showed sonnet measured with `medium` accepted, and a plain retry worked. So the
first turn after an Alias became unmeasured always failed, and told a person to run a script.

Now a turn whose saved level is not yet known waits, bounded, for the measurement it started and
then decides on the answer. Only a MEASURED refusal fails, and it names the setting to change.
"""
from __future__ import annotations

import asyncio
import copy
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from sage.gateway.capabilities import RouteCapability, RouteStatus, evidence
from sage.gateway.protocol import Protocol
from sage.orchestrator import app as appmod
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import join_aliases
from sage.router.model_control import ModelControl
from sage.router.models import Mode, Phase
from sage.shim import enforcement
from sage.shim.enforcement import EnforcementShim
from sage.gateway.client import FakeGatewayClient

from .test_a_build_pick_carries_its_own_effort import CATALOG as BUILD_CATALOG
from .test_a_build_pick_carries_its_own_effort import _template
from .test_an_unmeasured_alias_is_measured_on_first_use import Gateway, _provider, _settle
from .test_an_unverified_route_says_so import CATALOG
from .test_native_model_controls import RecordingGateway, VerifiedResources, active

UNMEASURED = RouteCapability(
    identity=("gw", "id"), status=RouteStatus.UNMEASURED,
    reason="No measurement matches this model's route. Repair: scripts/reasoning-evidence.py "
           "'cheap-vendor' --write")


def _on_the_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class MeasuredOnWait(VerifiedResources):
    """The live provider's three hooks, with the measurement's verdict chosen by the test.

    `wait_for_measurement` is where the verdict lands: before it, every listed model reads
    UNMEASURED; after it, `verdict` decides whether the evidence row is back. Nothing here starts a
    thread, so a test that waits leaves nothing behind.
    """

    def __init__(self, aliases, unmeasured: set[str], verdict: str = "measured"):
        super().__init__(aliases)
        self.unmeasured = set(unmeasured)
        self.verdict = verdict
        self.started: list[str] = []
        self.waits: list[tuple[str, float, bool]] = []

    def reasoning_capability(self, model):
        measured = super().reasoning_capability(model)
        if model in self.unmeasured:
            return replace(UNMEASURED, identity=measured.identity)
        return measured

    def use_local_evidence(self, path):
        pass

    def measure_on_first_use(self, model):
        self.started.append(model)

    def wait_for_measurement(self, model, timeout_s):
        self.waits.append((model, timeout_s, _on_the_event_loop()))
        if self.verdict == "measured":
            self.unmeasured.discard(model)


@pytest.fixture
def served(tmp_path, monkeypatch):
    rows = copy.deepcopy(evidence())
    for row in rows:
        row["capabilities"] = ["chat"]
    aliases = [a for row in rows for a in join_aliases({row["name"]}, [row], gateway_root=row["gateway"])]
    resources = MeasuredOnWait(aliases, unmeasured=set())
    orch = Orchestrator(workspace_dir=tmp_path / "mnt" / "code", template=_template(tmp_path),
                        gateway=FakeGatewayClient(), catalog=BUILD_CATALOG, project_id="Sage",
                        resources=resources)
    orch.project(start_preview=False)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    gateway = RecordingGateway()
    orch._project.shim._gateway = gateway
    client = TestClient(appmod.control_app)
    try:
        yield client, orch, gateway, resources
    finally:
        client.close()


def test_the_first_plan_turn_on_an_unmeasured_sonnet_runs_at_its_saved_medium(served):
    """The instance: sonnet saved at `medium` for planning, then its route stops matching any
    measurement. The turn's first call is the native resolve, which decides the wire as well as the
    level, so the measured answer has to be in hand before it returns."""
    client, orch, _gateway, resources = served
    saved = client.post("/api/project/model",
                        json={"catalog": {"plan": {"model": "sonnet", "effort": "medium"}}})
    assert saved.status_code == 200, saved.text
    resources.unmeasured.add("sonnet")

    with active(orch) as headers:
        chosen = client.post("/v1/sage/resolve", headers=headers,
                             json={"prompt": [{"role": "user", "content": "Write a plan"}],
                                   "tools": []})

    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["model"] == "sonnet"
    assert chosen.json()["effort"] == "medium"
    assert chosen.json()["protocol"] == Protocol.MESSAGES.value, "the measured wire, not the default"
    assert resources.started == ["sonnet"]
    assert [(m, t) for m, t, _ in resources.waits] == [("sonnet", enforcement.MEASURE_WAIT_S)]
    assert resources.waits[0][2] is False, "a wait on the event loop stalls every other request"


def test_a_chat_turn_on_the_shared_route_waits_too_and_sends_the_level(served):
    """Sibling: the Chat surface, on the control app's `/v1/chat/completions` rather than the native
    resolve, with tools on the request so the level is read from the with-tools column."""
    client, orch, gateway, resources = served
    saved = client.post("/api/project/model",
                        json={"chat_model": "GLM 5.3 OR", "reasoning_effort": "high"})
    assert saved.status_code == 200, saved.text
    resources.unmeasured.add("GLM 5.3 OR")

    with active(orch, chat=True) as headers:
        answer = client.post("/v1/chat/completions", headers=headers, json={
            "model": "GLM 5.3 OR", "stream": True,
            "messages": [{"role": "user", "content": "how many rows?"}],
            "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}]})

    assert answer.status_code == 200, answer.text
    outbound, _labels = gateway.seen[-1]
    assert outbound["reasoning_effort"] == "high"
    assert resources.waits and resources.waits[0][2] is False, (
        "a wait on the event loop stalls every other request")


def _shim(capabilities: list[RouteCapability], effort: str | None = "medium") -> EnforcementShim:
    """A shim whose route reads `capabilities` in turn, the last one for good."""
    shim = EnforcementShim(ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT),
                           replace(CATALOG, implement_effort=effort), FakeGatewayClient())
    answers = list(capabilities)
    shim.resolve_capability = lambda _model: answers.pop(0) if len(answers) > 1 else answers[0]
    return shim


MEASURED = RouteCapability(efforts=("low", "high"), efforts_with_tools=("low", "high"),
                           reason="", identity=("gw", "id"), status=RouteStatus.VERIFIED)


@pytest.mark.parametrize("before", [RouteStatus.UNMEASURED, RouteStatus.MEASURING])
def test_a_measured_refusal_names_the_setting_and_no_script(before):
    shim = _shim([replace(UNMEASURED, status=before), MEASURED])
    waited: list[str] = []
    shim.await_measurement = lambda model, _timeout: waited.append(model)

    with pytest.raises(ValueError) as refused:
        shim.prepare({"model": "x", "messages": []}, "p")

    message = str(refused.value)
    assert waited == [CATALOG.implement], "the verdict came from the measurement, not before it"
    assert "saved reasoning setting 'medium'" in message
    assert "Reasoning effort" in message and "Model assignments" in message
    assert "low, high" in message, "a person needs the levels it does accept"
    assert "scripts/" not in message and ".py" not in message


def test_a_measurement_that_does_not_finish_in_time_still_ends_the_turn():
    """Pinned: the bound expiring is today's behaviour — the turn fails rather than running at a
    level nobody chose — and it still never points a person at a script."""
    shim = _shim([UNMEASURED])
    timeouts: list[float] = []
    shim.await_measurement = lambda _model, timeout: timeouts.append(timeout)

    with pytest.raises(ValueError, match="saved reasoning setting 'medium'") as refused:
        shim.prepare({"model": "x", "messages": []}, "p")

    assert timeouts == [enforcement.MEASURE_WAIT_S]
    assert "scripts/" not in str(refused.value) and ".py" not in str(refused.value)


@pytest.mark.parametrize("effort,capability", [
    (None, UNMEASURED),          # nothing saved: nothing can fail, so nothing waits
    ("medium", replace(MEASURED, efforts=("medium",), efforts_with_tools=("medium",))),
])
def test_a_turn_that_cannot_fail_on_the_answer_does_not_wait_for_it(effort, capability):
    shim = _shim([capability], effort=effort)
    waited: list[str] = []
    shim.await_measurement = lambda model, _timeout: waited.append(model)

    shim.prepare({"model": "x", "messages": []}, "p")

    assert waited == []


def test_the_provider_wait_returns_when_the_measurement_lands(tmp_path):
    provider = _provider(tmp_path, Gateway(validates=("chat",), accepts=("low", "medium")))
    provider.measure_on_first_use("model-x")
    started = time.monotonic()
    try:
        provider.wait_for_measurement("model-x", 5)
        assert time.monotonic() - started < 4, "the wait sat out its bound after the answer landed"
        assert provider.reasoning_capability("model-x").status is RouteStatus.VERIFIED
    finally:
        _settle(provider)


def test_the_provider_wait_is_bounded_while_the_measurement_runs(tmp_path):
    release = threading.Event()
    gateway = Gateway(validates=("chat",), accepts=("low",))

    def slow(url, body):
        release.wait(timeout=5)
        return gateway(url, body)

    provider = _provider(tmp_path, slow)
    provider.measure_on_first_use("model-x")
    try:
        provider.wait_for_measurement("model-x", 0.05)
        assert provider.reasoning_capability("model-x").status is RouteStatus.MEASURING
    finally:
        release.set()
        _settle(provider)


def test_the_provider_wait_returns_at_once_when_nothing_is_measuring(tmp_path):
    provider = _provider(tmp_path, Gateway())
    started = time.monotonic()

    provider.wait_for_measurement("model-x", 2)

    assert time.monotonic() - started < 1, "a wait on no measurement must not sit out its bound"
