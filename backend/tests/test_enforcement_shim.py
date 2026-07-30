"""Integration tests for the enforcement shim against the FakeGatewayClient (DESIGN.md Seam 2).

Proves the policy half of the guarantee without a network: model override under lock + mandatory
tagging. The containment half (egress allowlist) is an infra test, not here (Step 1.4).
"""
from __future__ import annotations

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from dataclasses import replace as _replace

from sage.router.models import Mode, ModelCatalog, Phase, supports_vision
from sage.shim.enforcement import IMAGE_OMITTED, EnforcementShim

CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b",
    sovereign_implement="sovereign-8b",
    sovereign_ask="sovereign-8b",
    plan="strong-vendor",
    implement="cheap-vendor",
    ask="ask-vendor",
)


def _shim(control: ModelControl, gw: FakeGatewayClient) -> EnforcementShim:
    return EnforcementShim(control, CATALOG, gw)


def test_override_forces_sovereign_when_locked():
    control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
    control.pick("strong-vendor")
    control.on_assets_changed([True])  # sensitivity-tagged asset attached
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "strong-vendor", "messages": []}, project="p1"))

    sent_request, labels = gw.seen[-1]
    assert sent_request["model"] == "sovereign-8b"  # caller's model was overridden
    assert labels.mode == "sovereign"  # asset lock is surfaced as the sovereign cost dimension


def test_every_request_is_tagged_with_phase_and_component():
    # Implement mode: phase comes straight from the control (no per-step classification), so this
    # deterministically exercises tag propagation. Auto-mode classification has its own test.
    # Project is NOT tagged — the gateway captures it first-class (a `project` tag is dropped).
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"messages": []}, project="proj-x", session="ses_abc"))

    _, labels = gw.seen[-1]
    assert labels.phase == "implement"  # never empty -> avoids the gateway 'unknown' bucket
    assert labels.mode == "implement"
    assert labels.component == "builder"
    assert labels.session == "ses_abc"  # per-build cost rollup


def test_auto_mode_classifies_phase_per_request():
    # Auto mode picks the model per step from the message tail: writing code -> implement model.
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    gw = FakeGatewayClient()
    messages = [{"role": "assistant", "tool_calls": [{"function": {"name": "edit"}}]}]

    list(_shim(control, gw).handle({"messages": messages}, project="p"))

    sent_request, labels = gw.seen[-1]
    assert labels.phase == "implement"
    assert sent_request["model"] == "cheap-vendor"  # catalog.implement


def test_sticky_lock_survives_detach():
    control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
    control.on_assets_changed([True])   # attach tagged
    control.on_assets_changed([False])  # detach: must NOT clear the lock
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "strong-vendor", "messages": []}, project="p1"))

    assert gw.seen[-1][0]["model"] == "sovereign-8b"


def test_user_can_override_asset_lock():
    # The sticky asset lock is user-removable (with a UI warning); once cleared, routing is no
    # longer forced to sovereign — until another sensitivity-tagged asset is attached.
    control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
    control.on_assets_changed([True])   # sensitivity-tagged asset attached -> locked
    assert control.locked
    control.clear_asset_lock()          # user override
    assert not control.locked
    control.on_assets_changed([True])   # attaching another sensitive asset re-locks
    assert control.locked


def test_ask_mode_strips_write_tools_from_request():
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "edit"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    tool_names = [t["function"]["name"] for t in sent_request["tools"]]
    assert "edit" not in tool_names
    assert "read" in tool_names


def test_ask_mode_strips_shell_tools_too():
    """The hole that made Ask mode writable: `bash` was never in the strip set, and OpenCode's own
    `permission: {bash: deny}` is inert headless — so the model wrote files with `printf > file`."""
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "bash"}},
        {"type": "function", "function": {"name": "grep"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    tool_names = [t["function"]["name"] for t in sent_request["tools"]]
    assert "bash" not in tool_names
    assert "grep" in tool_names


def test_gated_plan_turn_strips_tools_even_outside_ask_mode():
    """The plan gate fires from Auto too, where `mode` alone can't express "this turn is read-only"."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.set_read_only_turn(True)
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "bash"}},
        {"type": "function", "function": {"name": "write"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert [t["function"]["name"] for t in sent_request["tools"]] == ["read"]


def test_read_only_turn_is_per_turn_not_sticky():
    """The orchestrator clears this on every exit from a turn; a stuck flag would silently make
    every later build read-only, which looks like the agent refusing to write."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    assert control.snapshot().read_only_turn is False
    control.set_read_only_turn(True)
    assert control.snapshot().read_only_turn is True
    control.set_read_only_turn(False)
    assert control.snapshot().read_only_turn is False


def test_write_tools_reach_an_ordinary_build_turn():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "bash"}},
        {"type": "function", "function": {"name": "write"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert [t["function"]["name"] for t in sent_request["tools"]] == ["bash", "write"]


def test_ask_mode_with_no_tools_key_is_untouched():
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"messages": []}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert "tools" not in sent_request


def _image_messages():
    return [
        {"role": "user", "content": [
            {"type": "text", "text": "make it look like this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]},
        {"role": "assistant", "content": "sure"},
    ]


def _vision_shim(control, gw):
    """Same catalog, but the implement model is one verified vision-capable on the live gateway."""
    return EnforcementShim(control, _replace(CATALOG, implement="sonnet"), gw)


def test_images_are_stripped_when_the_resolved_model_cannot_see_them():
    # catalog.implement here is "cheap-vendor" — unknown, therefore treated as non-vision. Without
    # this, bedrock-qwen3-coder (the real default) hard-400s and the whole build turn dies.
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"messages": _image_messages()}, project="p"))

    parts = gw.seen[-1][0]["messages"][0]["content"]
    assert not any(p.get("type") == "image_url" for p in parts)
    assert parts[0] == {"type": "text", "text": "make it look like this"}  # rest of the turn intact
    assert parts[1] == {"type": "text", "text": IMAGE_OMITTED}  # agent is told, never left guessing


def test_images_pass_through_untouched_to_a_vision_capable_model():
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    messages = _image_messages()

    list(_vision_shim(control, gw).handle({"messages": messages}, project="p"))

    assert gw.seen[-1][0]["model"] == "sonnet"
    assert gw.seen[-1][0]["messages"] == _image_messages()  # byte-for-byte, no marker injected


def test_plain_string_content_is_never_rewritten():
    for shim_factory in (_shim, _vision_shim):
        control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
        gw = FakeGatewayClient()
        messages = [{"role": "user", "content": "just text"}]

        list(shim_factory(control, gw).handle({"messages": messages}, project="p"))

        assert gw.seen[-1][0]["messages"] == [{"role": "user", "content": "just text"}]


def test_stripping_images_does_not_mutate_the_callers_request():
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    request = {"messages": _image_messages()}

    list(_shim(control, gw).handle(request, project="p"))

    assert request == {"messages": _image_messages()}


def test_every_image_across_every_message_is_replaced():
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "d1"}},
            {"type": "image_url", "image_url": {"url": "d2"}},
        ]},
        {"role": "user", "content": [{"type": "text", "text": "and this"}]},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "d3"}}]},
    ]

    list(_shim(control, gw).handle({"messages": messages}, project="p"))

    sent = gw.seen[-1][0]["messages"]
    assert [p["text"] for p in sent[0]["content"]] == [IMAGE_OMITTED, IMAGE_OMITTED]
    assert sent[1] == {"role": "user", "content": [{"type": "text", "text": "and this"}]}
    assert sent[2]["content"] == [{"type": "text", "text": IMAGE_OMITTED}]


def test_vision_capability_is_a_closed_list_with_unknown_models_failing_safe():
    assert supports_vision("sonnet") and supports_vision("domino/gpt-5.4")
    assert supports_vision("opus") and supports_vision("etan-opus-4.6")
    assert not supports_vision("bedrock-qwen3-coder")  # live 400
    assert not supports_vision("qwen-2-5")             # live 502
    assert not supports_vision("some-future-model")    # unknown -> no images


def test_ask_mode_respects_locked_sovereign_ask():
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    control.on_assets_changed([True])  # sensitivity-tagged asset attached
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "ask-vendor", "messages": []}, project="p"))

    assert gw.seen[-1][0]["model"] == "sovereign-8b"
