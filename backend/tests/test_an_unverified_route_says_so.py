"""A route with no matching measurement is said out loud, to the log and to the person (#509).

`capabilities.resolve` answers only from `reasoning-evidence.json`, and a row is keyed on the
Alias identity INCLUDING `updated_at`. Any repoint upstream silently stops the row matching, the
capability falls back to protocol CHAT with no levels, and a tool-carrying turn goes to a wire the
model may refuse. Measured on gpt-5.4 (#505): every new workspace died on its first Build turn in
about 730ms and nothing anywhere named the cause.

The fallback itself is correct and is not what changed here. What changed is that it is no longer
SILENT, and that the two states which used to be indistinguishable now differ in a field:

    measured, and it offers no levels     efforts == ()   verified is True
    no measurement matches this route     efforts == ()   verified is False

Capabilities are built through the real `resolve` against the real evidence file rather than
hand-assembled, so these cannot drift from what production computes.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace

from sage.gateway.capabilities import RouteCapability, evidence, resolve
from sage.gateway.client import FakeGatewayClient
from sage.gateway.protocol import Protocol
from sage.orchestrator.app import _unverified_route_note
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

CATALOG = ModelCatalog(sovereign_plan="sovereign-8b", sovereign_implement="sovereign-8b",
                       sovereign_ask="sovereign-8b", plan="strong-vendor",
                       implement="cheap-vendor", ask="ask-vendor")


def _measured_row_offering_nothing() -> dict:
    """A REAL row that was measured and offers no levels. This is the whole reason `verified`
    exists: without it, `not efforts` answers True for this row and for an unmeasured one alike."""
    row = next((r for r in evidence() if not r["efforts"]), None)
    assert row is not None, "the evidence file no longer contains a measured row with no levels"
    return row


def _repointed(row: dict) -> dict:
    """The same alias after an upstream repoint. `updated_at` is part of `route_identity`, so this
    is a row that can no longer match its own proof — exactly what a repoint does."""
    return {**row, "updated_at": "2099-01-01T00:00:00"}


def test_a_measured_row_that_offers_nothing_is_not_read_as_an_unmeasured_one():
    rows = _measured_row_offering_nothing(), None
    row = rows[0]
    measured = resolve(row["gateway"], row, evidence())
    unmeasured = resolve(row["gateway"], _repointed(row), evidence())

    assert measured.efforts == unmeasured.efforts == ()
    assert not measured.efforts, "this row is the point: it was measured and offers nothing"
    assert measured.verified is True and unmeasured.verified is False, (
        "no other field separates these two, and they need opposite responses")


def _shim_with(capability) -> EnforcementShim:
    shim = EnforcementShim(ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT),
                           CATALOG, FakeGatewayClient())
    shim.resolve_capability = lambda _model: capability
    return shim


def _policy_lines(caplog):
    return [r for r in caplog.records if r.name == "sage.shim"
            and r.getMessage().startswith("model policy: requested=")]


def test_the_shim_says_an_unverified_route_out_loud_and_says_it_as_a_warning(caplog):
    """A turn that runs on an unverified route leaves a line somebody can find AFTER it died.
    `warning` and not `info` because the warn tail is what the Workspace Logs panel surfaces."""
    row = _measured_row_offering_nothing()
    capability = resolve(row["gateway"], _repointed(row), evidence())

    with caplog.at_level(logging.INFO, logger="sage.shim"):
        list(_shim_with(capability).handle({"model": "strong-vendor", "messages": []}, project="p"))

    line = _policy_lines(caplog)[-1]
    assert "ROUTE UNVERIFIED" in line.getMessage()
    assert line.levelno == logging.WARNING, "an INFO line does not reach the warn tail"


def test_a_verified_route_adds_nothing_to_the_line_and_stays_at_info(caplog):
    """The no-false-alarm half. A model that was measured and offers no levels must not be
    reported as unmeasured — that is the case this whole change had to be careful about."""
    row = _measured_row_offering_nothing()
    # Its real protocol is MESSAGES, which `handle` would take to the native parser and away from
    # the line under test. Only the wire is swapped: `verified` and the empty levels — the two
    # things this asserts on — are the ones `resolve` actually computed for this row.
    capability = replace(resolve(row["gateway"], row, evidence()), protocol=Protocol.CHAT)
    assert capability.verified is True and capability.efforts == ()

    with caplog.at_level(logging.INFO, logger="sage.shim"):
        list(_shim_with(capability).handle({"model": "strong-vendor", "messages": []}, project="p"))

    line = _policy_lines(caplog)[-1]
    assert "ROUTE UNVERIFIED" not in line.getMessage()
    assert line.levelno == logging.INFO


def test_a_local_run_is_never_told_its_route_is_unverified(caplog):
    """`legacy` — the development contract and the shim's own default — is unverified by
    construction and carries no identity. Keying on `verified` alone would make every local run
    shout about a gateway it is not talking to."""
    with caplog.at_level(logging.INFO, logger="sage.shim"):
        list(_shim_with(RouteCapability()).handle({"model": "strong-vendor", "messages": []},
                                                  project="p"))

    line = _policy_lines(caplog)[-1]
    assert "ROUTE UNVERIFIED" not in line.getMessage() and line.levelno == logging.INFO


def _project(model, capability):
    return SimpleNamespace(resolved_model=SimpleNamespace(model=model),
                           shim=SimpleNamespace(resolve_capability=lambda _m: capability))


def test_the_person_is_told_why_the_refusal_happened_and_how_to_repair_it():
    row = _measured_row_offering_nothing()
    capability = resolve(row["gateway"], _repointed(row), evidence())

    note = _unverified_route_note(_project(row["name"], capability))

    assert "No measurement matches" in note
    assert f"scripts/reasoning-evidence.py '{row['name']}' --write" in note, (
        "a person told only that something is unverified has been told nothing they can act on")
    assert _unverified_route_note(_project(row["name"],
                                           resolve(row["gateway"], row, evidence()))) == "", (
        "a verified route must add nothing to the gateway's own message")


def test_a_fallback_route_is_not_offered_a_repair_that_cannot_help_it():
    """`resolve` returns the fallback capability WITHOUT consulting evidence, so measuring the
    alias can never satisfy it. A reader that appended one repair line to every unverified
    capability would send somebody to run a command that cannot work."""
    row = _measured_row_offering_nothing()
    capability = resolve(row["gateway"], {**row, "fallback_chain": ["other-alias"]}, evidence())

    note = _unverified_route_note(_project(row["name"], capability))

    assert capability.verified is False and "fallback" in note
    assert "reasoning-evidence.py" not in note


def test_a_diagnostic_never_replaces_the_error_it_explains():
    """`route_capability` raises when the gateway listing cannot be reached — which is exactly the
    situation this note is written for. It must go quiet, not bury the refusal under its own."""
    def angry(_model):
        raise ValueError("the gateway route cannot be checked now")

    project = SimpleNamespace(resolved_model=SimpleNamespace(model="anything"),
                              shim=SimpleNamespace(resolve_capability=angry))

    assert _unverified_route_note(project) == ""
    assert _unverified_route_note(SimpleNamespace()) == "", "no model resolved yet, nothing to say"
