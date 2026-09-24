"""A model call records whether a measured route proof matched it (#534).

A TFL Build on 2026-09-24 sent all 22 sonnet calls on `protocol: chat` — no prompt cache, no usage,
no eager tool streaming — and the downloaded diagnostics could not say why. `resolve` falls back to
CHAT whenever no evidence row matches the alias identity, `updated_at` included, so one re-save of
the alias upstream moves a Claude model off the native lane. The shim logs `ROUTE UNVERIFIED`, but
the log rolls; the call record is what a person downloads. A CHAT call that was MEASURED to be chat
and one that fell back to it read the same without this field.
"""
import copy

import pytest

from sage import build_diagnostics as diagnostics
from sage import timing
from sage.gateway.capabilities import evidence
from sage.gateway.protocol import Protocol
from sage.resources.provider import join_aliases

from .test_native_model_controls import (
    VerifiedResources,
    active,
    dispatch,
    running,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.usefixtures("ledger")

MODEL = "GLM 5.3 OR"  # measured on the chat lane, so both cases take the same wire


@pytest.fixture(autouse=True)
def enable_recorder(monkeypatch):
    monkeypatch.setenv("SAGE_TIMING", "1")


def _drift(orch):
    """The alias was re-saved upstream: same model, new `updated_at`, no proof matches."""
    rows = copy.deepcopy(evidence())
    for row in rows:
        row["capabilities"] = ["chat"]
        if row["name"] == MODEL:
            row["updated_at"] = "2026-09-24T00:00:00.000000"
    aliases = [a for row in rows for a in join_aliases({row["name"]}, [row], gateway_root=row["gateway"])]
    orch._resources = VerifiedResources(aliases)


def _one_call(client, orch):
    client.post("/api/project/model", json={"pick": MODEL, "mode": "plan"})
    timing.start_turn("build", turn_id="turn_route", app_id="app_route", conversation_id="thr_route")
    try:
        with active(orch) as headers:
            assert dispatch(client, headers, Protocol.CHAT, MODEL).status_code == 200
    finally:
        rec = timing.finish_turn()
    return rec


@pytest.mark.parametrize("drifted", [False, True])
def test_the_call_record_and_the_download_say_whether_the_route_was_measured(running, drifted):  # noqa: F811
    client, orch, _ = running
    if drifted:
        _drift(orch)
    rec = _one_call(client, orch)
    call = timing.as_dict(rec)["calls"][0]
    assert call["protocol"] == "chat"
    assert call["routeVerified"] is (not drifted)
    exported = diagnostics.snapshot(rec, {"turnId": "turn_route", "kind": "build"}, terminal=True)
    assert exported["timing"]["calls"][0]["routeVerified"] is (not drifted)


def test_the_download_admits_the_field_only_as_a_bool():
    assert diagnostics._metadata({"routeVerified": False}, diagnostics.CALL_FIELDS) == {"routeVerified": False}
    assert diagnostics._metadata({"routeVerified": "PRIVATE"}, diagnostics.CALL_FIELDS) == {}
