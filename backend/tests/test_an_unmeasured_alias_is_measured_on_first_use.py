"""An Alias nobody measured is measured by the builder the first time a turn runs on it (#646).

Before this, a model the gateway admin added reached the picker with no reasoning levels and stayed
that way until somebody ran `scripts/reasoning-evidence.py` and landed the file. Now the first turn
on it starts a measurement in the background, on the viewer's own gateway credential, and keeps the
answer in the Project's `.sage/` so the next turn — and the next builder start — offers its levels.
"""
from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
import threading

import pytest

from sage.gateway.capabilities import RouteCapability, RouteStatus
from sage.gateway.client import FakeGatewayClient
from sage.resources.provider import MEASURING_NOTE, DominoResourceProvider
from sage.router.model_control import ModelControl
from sage.router.models import Mode, Phase
from sage.shim.enforcement import EnforcementShim
from sage.workspace import manager
from tests.test_an_alias_is_measured_before_it_is_offered_a_level import ROOT, ROW, Gateway
from tests.test_an_unverified_route_says_so import CATALOG, _shim_with

ALIAS = {k: v for k, v in ROW.items() if k != "gateway"}


def _provider(tmp_path, gateway) -> DominoResourceProvider:
    provider = DominoResourceProvider(ROOT + "/v1", lambda: "token")
    provider._get = lambda path: {"data": [{"id": "model-x"}]} if path == "/v1/models" else [ALIAS]
    provider._gateway_call = gateway
    provider.use_local_evidence(tmp_path / ".sage" / "reasoning-evidence.json")
    return provider


def _settle(provider: DominoResourceProvider) -> None:
    worker = provider._worker
    if worker is not None:
        worker.join(timeout=5)
        assert not worker.is_alive(), "the measurement never finished"


def test_an_unmeasured_alias_gains_its_levels_after_its_first_turn(tmp_path):
    gateway = Gateway(validates=("chat",), accepts=("low", "high"))
    provider = _provider(tmp_path, gateway)
    assert provider.reasoning_capability("model-x").status is RouteStatus.UNMEASURED

    provider.measure_on_first_use("model-x")
    _settle(provider)

    capability = provider.reasoning_capability("model-x")
    assert capability.status is RouteStatus.VERIFIED
    assert capability.efforts == capability.efforts_with_tools == ("low", "high")


def test_the_picker_says_a_measurement_is_running_while_it_runs(tmp_path):
    release = threading.Event()
    gateway = Gateway(validates=("chat",), accepts=("low",))

    def slow(url, body):
        release.wait(timeout=5)
        return gateway(url, body)

    provider = _provider(tmp_path, slow)
    provider.measure_on_first_use("model-x")
    try:
        capability = provider.reasoning_capability("model-x")
        assert capability.status is RouteStatus.MEASURING
        assert capability.reason == MEASURING_NOTE
        assert capability.unverified_route, "a turn during the measurement is still unverified"
    finally:
        release.set()
        _settle(provider)


def test_a_measurement_survives_a_builder_restart_without_asking_again(tmp_path):
    provider = _provider(tmp_path, Gateway(validates=("chat",), accepts=("low",)))
    provider.measure_on_first_use("model-x")
    _settle(provider)

    restarted = _provider(tmp_path, Gateway())
    restarted.measure_on_first_use("model-x")
    _settle(restarted)

    assert restarted.reasoning_capability("model-x").status is RouteStatus.VERIFIED
    assert restarted._gateway_call.sent == [], "a kept measurement must not be taken again"


def test_a_measurement_the_gateway_refused_is_not_retried_on_every_turn(tmp_path):
    gateway = Gateway(control=400)
    provider = _provider(tmp_path, gateway)
    provider.measure_on_first_use("model-x")
    _settle(provider)
    asked = len(gateway.sent)

    provider.measure_on_first_use("model-x")
    _settle(provider)

    assert provider.reasoning_capability("model-x").status is RouteStatus.UNMEASURED
    assert len(gateway.sent) == asked, "a refused measurement waits before it costs quota again"
    assert not (tmp_path / ".sage" / "reasoning-evidence.json").exists()


def test_a_measurement_stays_out_of_the_projects_git(tmp_path):
    """Levels measured on THIS gateway are not true of a fork's gateway, so git must not carry them."""
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not on PATH")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    run = functools.partial(subprocess.run, cwd=tmp_path, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run([git, "init", "-q", "."], check=True)
    (tmp_path / ".gitignore").write_text("\n".join(manager._PROJECT_IGNORE) + "\n")
    for rel in (".sage/reasoning-evidence.json", ".sage/settings.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).touch()

    def ignored(rel: str) -> bool:
        return run([git, "-c", f"core.excludesFile={os.devnull}", "check-ignore", "-q", rel],
                   check=False).returncode == 0

    assert ignored(".sage/reasoning-evidence.json")
    assert not ignored(".sage/settings.json")


def test_the_turn_reports_the_model_it_runs_on_not_the_one_it_asked_for():
    """Dispatch is where the lock has already picked the model; a save could name a barred one."""
    shim = _shim_with(RouteCapability(status=RouteStatus.UNMEASURED))
    seen: list[str] = []
    shim.on_unmeasured_route = seen.append

    list(shim.handle({"model": "strong-vendor", "messages": []}, project="p"))

    assert seen == [CATALOG.implement]


def test_a_measured_route_starts_no_measurement():
    shim = _shim_with(RouteCapability(status=RouteStatus.VERIFIED))
    seen: list[str] = []
    shim.on_unmeasured_route = seen.append

    list(shim.handle({"model": "strong-vendor", "messages": []}, project="p"))

    assert seen == []


def test_a_measurement_that_cannot_start_never_ends_the_turn(caplog):
    shim = EnforcementShim(ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT),
                           CATALOG, FakeGatewayClient())
    shim.resolve_capability = lambda _model: RouteCapability(status=RouteStatus.UNMEASURED)

    def broken(_model):
        raise RuntimeError("listing unavailable")

    shim.on_unmeasured_route = broken
    with caplog.at_level(logging.ERROR, logger="sage.shim"):
        out = list(shim.handle({"model": "strong-vendor", "messages": []}, project="p"))

    assert out, "the turn still answered"
    assert any("could not start measuring" in r.getMessage() for r in caplog.records)
