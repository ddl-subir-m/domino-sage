"""Direct Build skips the automatic gates and keeps the ones the person asked for (ADR-0070).

Auto on an unbuilt app does not stop for a plan. Plan still does. Ask still cannot write.
A named table is still recorded. A build with no Data Source still asks which one.
"""
from __future__ import annotations

from pathlib import Path

from sage.gateway.client import FakeGatewayClient
from sage.router.models import Mode, Phase
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import Turn
from .test_a_mentioned_store_and_its_table_are_one_card import MENTION
from .test_a_mentioned_store_and_its_table_are_one_card import _orch as _store_orch
from .test_a_table_named_in_the_sentence_is_not_asked_for_again import BUILD_NAMED
from .test_plan_origin import _DESK, _orch
from .test_with_nothing_bound_sage_asks_which_data_source import PROMPT as SOURCE_PROMPT
from .test_with_nothing_bound_sage_asks_which_data_source import _orch as _source_orch


def test_auto_plus_direct_on_an_unbuilt_app_does_not_yield_a_plan_card(tmp_path: Path):
    orch, oc, _root = _orch(tmp_path, [Turn(text="The table is in.")])
    conversation = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    limits: list[int] = []
    inner = oc.send_prompt

    def send(*args, **kwargs):
        guard = project.pre_edit_guard
        if guard is not None:
            limits.append(guard._policy.pre_edit_model_call_limit)
        return inner(*args, **kwargs)

    oc.send_prompt = send
    events = list(orch.build_stream(
        "build me a desk exposure dashboard", conversation=conversation,
        how_sage_works="direct"))

    assert not [e for e in events if e.get("type") == "plan-proposed"]
    assert oc.prompts[0]["agent"] == "sage-implement-direct"
    assert project.control.snapshot().mode is Mode.AUTO
    assert project.control.snapshot().direct is False
    assert limits and limits[0] == 24


def test_plan_plus_direct_still_gates(tmp_path: Path):
    orch, oc, _root = _orch(tmp_path, [Turn(text=_DESK)])
    conversation = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.PLAN)

    events = list(orch.build_stream(
        "build me a desk exposure dashboard", conversation=conversation,
        how_sage_works="direct"))

    assert any(e.get("type") == "plan-proposed" for e in events)
    assert oc.prompts[0]["agent"] == "sage-plan"
    assert project.control.snapshot().mode is Mode.PLAN


def test_ask_plus_direct_strips_write_and_shell(tmp_path: Path):
    orch, oc, _root = _orch(tmp_path, [Turn(text="It lists the desks.")])
    conversation = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.ASK)
    upstream = FakeGatewayClient()
    shim = EnforcementShim(project.control, project.shim.catalog, upstream)
    request = {"model": "placeholder", "messages": [{"role": "user", "content": "hi"}],
               "tools": [{"type": "function", "function": {"name": name}}
                         for name in ("read", "bash", "write", "edit")]}
    inner = oc.send_prompt

    def send(*args, **kwargs):
        list(shim.handle(request, "p"))
        return inner(*args, **kwargs)

    oc.send_prompt = send
    list(orch.build_stream("what desks are on the page?", conversation=conversation,
                           how_sage_works="direct"))

    names = [t["function"]["name"] for t in upstream.seen[-1][0]["tools"]]
    assert "read" in names
    assert "bash" not in names and "write" not in names and "edit" not in names
    assert project.control.snapshot().mode is Mode.ASK


def test_a_named_table_is_recorded_and_no_table_card_is_yielded(tmp_path: Path):
    orch = _store_orch(tmp_path)
    orch.bind_data_source("ds-dwh")
    reached = []

    def _continue(*_args, **kwargs):
        reached.append(kwargs.get("how_sage_works"))
        return iter(())

    orch._build_stream = _continue  # type: ignore[method-assign]
    events = list(orch.build_stream(BUILD_NAMED, resources=MENTION, how_sage_works="direct"))

    assert not [e for e in events if e.get("type") == "table-candidates"]
    recorded = [b for b in orch.list_bindings() if b.get("kind") == "data_source"]
    assert recorded and recorded[0].get("table") == "GONG__CALLS"
    assert reached == ["direct"]


def test_no_bound_source_still_yields_the_source_card(tmp_path: Path):
    orch = _source_orch(tmp_path)
    events = list(orch.build_stream(SOURCE_PROMPT, how_sage_works="direct"))
    assert any(e.get("type") == "source-candidates" for e in events)


def test_a_direct_auto_request_resolves_the_implement_model_on_a_read_and_a_write():
    """The shim does not write the phase, so a read stays on PLAN in the control and still
    routes to the Implement slot. A write does not move the control either."""
    from sage.router.model_control import ModelControl
    from sage.router.models import ModelCatalog

    catalog = ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                           plan="strong-vendor", implement="cheap-vendor", ask="ask-vendor")
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_direct()
    upstream = FakeGatewayClient()
    shim = EnforcementShim(control, catalog, upstream)
    read = {"model": "placeholder", "messages": [{"role": "user", "content": "read the files"}]}
    write = {"model": "placeholder", "messages": [
        {"role": "user", "content": "read the files"},
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "edit", "arguments": "{}"}},
        ]},
    ]}
    for request in (read, write):
        list(shim.handle(request, "p"))
        assert upstream.seen[-1][0]["model"] == "cheap-vendor"
        assert control.snapshot().phase is Phase.PLAN
