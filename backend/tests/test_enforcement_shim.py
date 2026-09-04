"""Integration tests for the enforcement shim against the FakeGatewayClient (DESIGN.md Seam 2).

Proves the policy half of the guarantee without a network: the shim overwrites `model` with
the router decision and tags every request. The containment half (egress allowlist) is an infra
test, not here (Step 1.4).
"""
from __future__ import annotations

from dataclasses import replace as _replace

from sage.gateway.client import FakeGatewayClient
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase, supports_vision
import sage.shim.keepalive as ka
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


def test_router_overrides_the_caller_model():
    """The shape of every real request: OpenCode sending its configured model.

    This is the case that used to slip through. The override was conditional on `decision.locked or
    force_model or "model" not in request`, and in domino mode all three are false — so the router
    ran, logged its decision, and the caller's model went upstream untouched. Sending a model
    the router disagrees with is what pins the guarantee.
    """
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()

    list(_shim(control, gw).handle({"model": "strong-vendor", "messages": []}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert sent_request["model"] == "cheap-vendor"  # catalog.implement, not what the caller asked


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


def test_chat_turn_keeps_write_and_bash_tools():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_chat("thr_01abc")
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "write"}},
        {"type": "function", "function": {"name": "bash"}},
        {"type": "function", "function": {"name": "edit"}},
        {"type": "function", "function": {"name": "read"}},
        {"type": "function", "function": {"name": "webfetch"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    tool_names = [t["function"]["name"] for t in sent_request["tools"]]
    assert set(tool_names) == {"write", "bash", "edit", "read"}


def test_chat_turn_keeps_web_tools_when_web_is_armed():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_chat("thr_01abc")
    control.arm_web()
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "webfetch"}},
        {"type": "function", "function": {"name": "bash"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert [t["function"]["name"] for t in sent_request["tools"]] == ["webfetch", "bash", "read"]


def test_chat_turn_rejects_src_writes_instead_of_dropping_them():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_chat("thr_01abc")
    gw = FakeGatewayClient()
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "id": "w1",
            "function": {"name": "write", "arguments": '{"filePath": "src/App.tsx"}'},
        }],
    }]

    list(_shim(control, gw).handle({"messages": messages, "tools": []}, project="p"))

    sent = gw.seen[-1][0]["messages"]
    assert sent[0]["tool_calls"][0]["id"] == "w1"
    assert sent[1]["role"] == "tool"
    assert "examples/thr_01abc/" in sent[1]["content"]


def test_gated_plan_turn_strips_tools_even_outside_ask_mode():
    """The plan gate fires from Auto too, where `mode` alone can't express "this turn is read-only"."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_read_only()
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "bash"}},
        {"type": "function", "function": {"name": "write"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert [t["function"]["name"] for t in sent_request["tools"]] == ["read"]


def test_an_answering_turn_loses_the_task_list_tool_but_a_plan_turn_keeps_it():
    """An answering turn returns without building, so a task list on it is a build the user waits for
    that never comes. A gated plan turn is the opposite — tracking the steps is its job."""
    tools = [
        {"type": "function", "function": {"name": "todowrite"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    def names_after(reason: str, mode: Mode = Mode.AUTO) -> list[str]:
        control = ModelControl(mode=mode, phase=Phase.PLAN)
        control.arm_read_only(reason)
        gw = FakeGatewayClient()
        list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))
        return [t["function"]["name"] for t in gw.seen[-1][0]["tools"]]

    assert names_after("question") == ["read"]     # a question in Auto
    assert names_after("ask", Mode.ASK) == ["read"]
    assert names_after("plan") == ["todowrite", "read"]   # the gate keeps it


def test_an_answering_turn_can_still_read_an_earlier_builds_task_list():
    """Only the write side is stripped. "What's left to do?" is a fair question for an answering turn,
    so a read-side todo tool (1.18.4 has none; a future driver might) must survive."""
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    control.arm_read_only("ask")
    gw = FakeGatewayClient()
    tools = [{"type": "function", "function": {"name": "todoread"}}]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    assert [t["function"]["name"] for t in gw.seen[-1][0]["tools"]] == ["todoread"]


def test_ask_mode_loses_the_task_list_tool_even_with_nothing_armed():
    """Ask is read-only by mode, with no arming — the guarantee can't rest on the reason alone."""
    control = ModelControl(mode=Mode.ASK, phase=Phase.PLAN)
    gw = FakeGatewayClient()
    tools = [{"type": "function", "function": {"name": "todowrite"}},
             {"type": "function", "function": {"name": "read"}}]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    assert [t["function"]["name"] for t in gw.seen[-1][0]["tools"]] == ["read"]


def test_an_ordinary_build_turn_keeps_the_task_list_tool():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    tools = [{"type": "function", "function": {"name": "todowrite"}}]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    assert [t["function"]["name"] for t in gw.seen[-1][0]["tools"]] == ["todowrite"]


def test_the_read_only_reason_clears_with_the_arming_it_describes():
    """A reason left behind after disarm would strip the task list from later ordinary builds."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    token = control.arm_read_only("question")
    assert control.snapshot().read_only_reason == "question"
    control.disarm_read_only(token)
    assert control.snapshot().read_only_reason == ""
    # A stale disarm must not strand a newer turn's reason either.
    old = control.arm_read_only("question")
    control.arm_read_only("plan")
    control.disarm_read_only(old)
    assert control.snapshot().read_only_reason == "plan"


def test_read_only_turn_is_per_turn_not_sticky():
    """The orchestrator disarms on every exit from a turn; a stuck guarantee would silently make
    every later build read-only, which looks like the agent refusing to write."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    assert control.snapshot().read_only_turn is False
    token = control.arm_read_only()
    assert control.snapshot().read_only_turn is True
    control.disarm_read_only(token)
    assert control.snapshot().read_only_turn is False


def test_a_stale_disarm_cannot_drop_a_newer_turns_read_only():
    """Token-scoped guarantee: an older turn's disarm must not clear a newer turn's arming. This is
    the hardening behind the turn lock — even if two turns' arm/disarm interleave, read-only holds
    for whichever turn armed last, so a gated planner is never silently un-gated mid-flight."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    old = control.arm_read_only()   # turn A arms
    new = control.arm_read_only()   # turn B arms, superseding A
    control.disarm_read_only(old)   # A exits late — must be a no-op
    assert control.snapshot().read_only_turn is True
    control.disarm_read_only(new)   # B exits — now it clears
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


def test_web_tools_are_stripped_by_default_when_web_is_not_armed():
    """The planning-spiral fix: with no web arming, the agent is never offered webfetch, so it can't
    wander off to fetch Storybook/CDN URLs mid-plan. Holds in an ordinary build turn, not just Ask."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "webfetch"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert [t["function"]["name"] for t in sent_request["tools"]] == ["read"]


def test_web_tools_survive_when_the_turn_armed_web():
    """When the prompt asked for the web, the orchestrator arms web_allowed and the tool passes."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.IMPLEMENT)
    control.arm_web()
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "webfetch"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert [t["function"]["name"] for t in sent_request["tools"]] == ["webfetch", "read"]


def test_web_arming_is_per_turn_not_sticky():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    assert control.snapshot().web_allowed is False
    token = control.arm_web()
    assert control.snapshot().web_allowed is True
    control.disarm_web(token)
    assert control.snapshot().web_allowed is False


def test_a_stale_disarm_cannot_drop_a_newer_turns_web():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    old = control.arm_web()
    new = control.arm_web()
    control.disarm_web(old)   # late exit of the superseded turn — no-op
    assert control.snapshot().web_allowed is True
    control.disarm_web(new)
    assert control.snapshot().web_allowed is False


def test_gated_plan_turn_still_strips_web_along_with_write_and_shell():
    """A gated plan turn is read-only AND web-denied: read survives, webfetch/write/bash do not."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.arm_read_only()
    gw = FakeGatewayClient()
    tools = [
        {"type": "function", "function": {"name": "webfetch"}},
        {"type": "function", "function": {"name": "write"}},
        {"type": "function", "function": {"name": "read"}},
    ]

    list(_shim(control, gw).handle({"messages": [], "tools": tools}, project="p"))

    sent_request, _ = gw.seen[-1]
    assert [t["function"]["name"] for t in sent_request["tools"]] == ["read"]


def test_wants_web_detects_urls_and_intent_verbs_but_not_plain_builds():
    from sage.orchestrator.service import _wants_web

    # URLs and clear intent phrases
    assert _wants_web("fetch https://ant.design/components/upload")
    assert _wants_web("look up the antd Upload docs online")
    assert _wants_web("search the web for a good dropzone pattern")
    assert _wants_web("scrape the pricing page")
    # Widened vocabulary: standalone web words + more verb/noun pairs
    assert _wants_web("google the recommended dropzone library")
    assert _wants_web("curl the latest release")
    assert _wants_web("grab the changelog from the repo")
    assert _wants_web("pull the README from github")
    assert _wants_web("check the antd api reference")
    assert _wants_web("find the upload example in their wiki")
    # Plain build/edit requests must still fall through to deny
    assert not _wants_web("build a UI that lets users upload datasets")
    assert not _wants_web("add a delete button to the dataset card")
    assert not _wants_web("add an API endpoint that returns the dataset rows")
    assert not _wants_web("")


def test_vision_capability_is_a_closed_list_with_unknown_models_failing_safe():
    assert supports_vision("sonnet") and supports_vision("domino/gpt-5.4")
    assert supports_vision("opus") and supports_vision("etan-opus-4.6")
    assert not supports_vision("bedrock-qwen3-coder")  # live 400
    assert not supports_vision("qwen-2-5")             # live 502
    assert not supports_vision("some-future-model")    # unknown -> no images


def test_gemini_is_vision_capable_under_the_name_the_gateway_offers():
    """cloud-dogfood offers Gemini as `domino/gemini-3.7-flash`, and it answered "Red" to an 8x8
    red PNG sent as an image_url part on 2026-09-03. The list holds the bare id because
    supports_vision reduces the provider prefix away first; both forms must pass."""
    assert supports_vision("domino/gemini-3.7-flash")
    assert supports_vision("gemini-3.7-flash")


def test_the_broken_gcp_sonnet_alias_is_not_treated_as_vision_capable():
    """`domino-gcp/claude-sonnet-5` is listed by the gateway but 404s upstream from GCP, so no
    image was ever seen through it. It also must not inherit `sonnet`: the reduction is exact-match
    on the bare id, not a substring test."""
    assert not supports_vision("domino-gcp/claude-sonnet-5")


def test_chat_pick_and_effort_go_to_the_gateway():
    control = ModelControl()
    control.pick_chat("gpt-5.4", "high")
    token = control.arm_chat("thr_1")
    gw = FakeGatewayClient()
    list(_shim(control, gw).handle({"model": "opencode-default", "messages": []}, project="p"))
    sent = gw.seen[-1][0]
    assert sent["model"] == "gpt-5.4"
    assert sent["reasoning_effort"] == "high"
    control.disarm_chat(token)


def test_a_tool_carrying_turn_is_sent_without_an_effort():
    """gpt-5.4 refuses function tools and `reasoning_effort` together on chat/completions.

    Live: "Function tools with reasoning_effort are not supported for gpt-5.4 in
    /v1/chat/completions." Chat turns always carry tools, so with gpt-5.4 as the Chat alias every
    turn failed, down to "hi". The same turn succeeded on sonnet, which advertises no efforts and
    so was never given one.
    """
    control = ModelControl()
    control.pick_chat("gpt-5.4", "high")
    token = control.arm_chat("thr_1")
    gw = FakeGatewayClient()
    tools = [{"function": {"name": "read"}}]
    list(_shim(control, gw).handle({"model": "opencode-default", "messages": [], "tools": tools},
                                   project="p"))
    sent = gw.seen[-1][0]
    assert sent["model"] == "gpt-5.4"          # the pick still routes
    assert "reasoning_effort" not in sent      # only the effort is dropped
    control.disarm_chat(token)


def test_the_auto_low_effort_is_dropped_for_a_tool_carrying_turn_too():
    """The cost floor takes the same exit. Both branches send the field the gateway refuses."""
    control = ModelControl()
    token = control.arm_chat("thr_1")
    gw = FakeGatewayClient()
    # Auto routes Chat to the ask slot, so that is the slot that has to be a GPT-5 for the floor
    # to apply at all.
    handler = EnforcementShim(control, _replace(CATALOG, ask="gpt-5.4"), gw)
    list(handler.handle({"model": "opencode-default", "messages": []}, project="p"))
    assert gw.seen[-1][0]["reasoning_effort"] == "low"   # no tools: the floor still applies
    list(handler.handle({"model": "opencode-default", "messages": [],
                         "tools": [{"function": {"name": "read"}}]}, project="p"))
    assert "reasoning_effort" not in gw.seen[-1][0]
    control.disarm_chat(token)


def test_chat_default_uses_the_ask_model_not_the_build_mode():
    control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
    token = control.arm_chat("thr_1")
    gw = FakeGatewayClient()
    list(_shim(control, gw).handle({"model": "opencode-default", "messages": []}, project="p"))
    assert gw.seen[-1][0]["model"] == "ask-vendor"
    control.disarm_chat(token)


def test_chat_default_does_not_run_the_build_phase_classifier():
    # The same edit-tool tail that flips Build Auto to catalog.implement must stay on catalog.ask.
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    token = control.arm_chat("thr_1")
    gw = FakeGatewayClient()
    messages = [{"role": "assistant", "tool_calls": [{"function": {"name": "edit"}}]}]
    list(_shim(control, gw).handle({"messages": messages}, project="p"))
    assert gw.seen[-1][0]["model"] == "ask-vendor"
    control.disarm_chat(token)


# --- the turn-mode pin -------------------------------------------------------------------------
# The shim reads control.snapshot() per REQUEST, and the mode picker is live while a turn streams.
# Unpinned, changing it mid-build split one turn in half: the first inferences ran as Implement with
# edit tools and the coder model, the later ones as Ask with both stripped — and the tool calls the
# agent could no longer make were still sitting in its context. See ModelControl.arm_turn_mode.

def _tools_sent(gw) -> list[str]:
    return [t["function"]["name"] for t in gw.seen[-1][0]["tools"]]


_MIXED_TOOLS = [{"type": "function", "function": {"name": n}} for n in ("read", "edit", "bash")]


def _req() -> dict:
    return {"messages": [], "tools": list(_MIXED_TOOLS)}


def test_a_pinned_turn_ignores_a_mode_change_made_while_it_runs():
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    shim = _shim(control, gw)
    token = control.arm_turn_mode(control.snapshot().mode)

    list(shim.handle(_req(), project="p"))
    first_model = gw.seen[-1][0]["model"]
    control.set_mode(Mode.ASK)  # the user picks Ask while this turn is still streaming
    list(shim.handle(_req(), project="p"))

    assert "edit" in _tools_sent(gw) and "bash" in _tools_sent(gw)  # same turn, same tools
    assert gw.seen[-1][0]["model"] == first_model                   # and the same model
    control.disarm_turn_mode(token)


def test_the_mode_picked_mid_turn_is_what_runs_once_the_pin_drops():
    """The pick isn't discarded, just deferred — the whole point of pinning over ignoring."""
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    token = control.arm_turn_mode(control.snapshot().mode)
    control.set_mode(Mode.ASK)
    assert control.snapshot().mode is Mode.IMPLEMENT   # this turn
    assert control.selected_mode is Mode.ASK           # the next one

    control.disarm_turn_mode(token)
    assert control.snapshot().mode is Mode.ASK
    list(_shim(control, gw).handle(_req(), project="p"))
    assert "edit" not in _tools_sent(gw)


def test_an_escalation_repins_the_turn_without_moving_the_users_choice():
    """The stalled-Auto nudge switches to Implement. It used to do that with set_mode + restore, which
    moved the user's own picker and then reverted anything they had changed it to meanwhile."""
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    token = control.arm_turn_mode(Mode.AUTO)
    control.set_turn_mode(Mode.IMPLEMENT)

    assert control.snapshot().mode is Mode.IMPLEMENT
    assert control.snapshot().phase is Phase.IMPLEMENT  # the spinner follows what routes
    assert control.selected_mode is Mode.AUTO

    control.disarm_turn_mode(token)
    assert control.snapshot().mode is Mode.AUTO  # dropping the pin is the whole restore


def test_a_stale_disarm_cannot_unpin_another_turn():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    stale = control.arm_turn_mode(Mode.AUTO)
    live = control.arm_turn_mode(Mode.ASK)   # the next turn supersedes it
    control.disarm_turn_mode(stale)          # a crashed/out-of-order exit
    assert control.snapshot().mode is Mode.ASK
    control.disarm_turn_mode(live)


def test_set_turn_mode_is_a_no_op_with_no_turn_running():
    control = ModelControl(mode=Mode.AUTO, phase=Phase.PLAN)
    control.set_turn_mode(Mode.IMPLEMENT)
    assert control.snapshot().mode is Mode.AUTO


# --- Mid-turn rescue routing (flipped from observe-only 2026-08-13) --------------------------
# One landed write, then two failing shell results with no write between: the scorer's rescue
# condition. Exit footers are OpenCode's own, verbatim from a live build.
_RESCUE_MESSAGES = [
    {"role": "user", "content": "build it"},
    {"role": "assistant", "tool_calls": [{"id": "w1", "function": {"name": "write"}}]},
    {"role": "tool", "tool_call_id": "w1", "content": "Wrote file successfully: src/App.tsx"},
    {"role": "assistant", "tool_calls": [{"id": "b1", "function": {"name": "bash"}}]},
    {"role": "tool", "tool_call_id": "b1", "content": "boom\nCommand exited with code 1."},
    {"role": "assistant", "tool_calls": [{"id": "b2", "function": {"name": "bash"}}]},
    {"role": "tool", "tool_call_id": "b2", "content": "boom\nCommand exited with code 2."},
]


def _handled(control: ModelControl, messages: list) -> tuple[dict, object]:
    gw = FakeGatewayClient()
    list(_shim(control, gw).handle({"model": "cheap-vendor", "messages": messages}, project="p1"))
    return gw.seen[-1]


def test_a_failing_turn_is_rescued_onto_the_plan_model():
    # The whole feature. A write already landed, so the write-flip rule alone would pin this step
    # to the cheap coder for the rest of the turn — which is what it did before the flip.
    sent, labels = _handled(ModelControl(mode=Mode.AUTO), _RESCUE_MESSAGES)
    assert sent["model"] == "strong-vendor"
    assert labels.phase == "plan"
    assert labels.route_reason == "rescue-errors"  # priced separately in the gateway dashboard


def test_the_rescued_step_is_told_why_it_was_called_in():
    # Without this the strong model inherits the transcript but no account of the failure, and
    # re-attempts the edit that just failed. `system`, never `user` — a user message is a turn
    # boundary to the scorer and would reset the error window that triggered the rescue.
    sent, _ = _handled(ModelControl(mode=Mode.AUTO), _RESCUE_MESSAGES)
    note = sent["messages"][-1]
    assert note["role"] == "system"
    assert "[sage]" in note["content"]
    assert not any(m.get("role") == "user" for m in sent["messages"][len(_RESCUE_MESSAGES):])


def test_a_clean_turn_still_routes_on_the_write_flip():
    # No regression: the long-standing rule is untouched when nothing is failing.
    clean = _RESCUE_MESSAGES[:3]
    sent, labels = _handled(ModelControl(mode=Mode.AUTO), clean)
    assert sent["model"] == "cheap-vendor"
    assert labels.route_reason is None
    assert all(m.get("role") != "system" for m in sent["messages"])


# ---- the outgoing tool-call signature summary (#155) --------------------------------------------

def _gemini_catalog():
    return _replace(CATALOG, plan="gemini-3.7-flash", implement="gemini-3.7-flash",
                    ask="gemini-3.7-flash")


def _batch(*sigs):
    calls = []
    for n, sig in enumerate(sigs):
        c = {"id": f"c{n}", "type": "function",
             "function": {"name": "read", "arguments": '{"path":"a"}'}}
        if sig is not None:
            c["extra_content"] = {"google": {"thought_signature": sig}}
        calls.append(c)
    return {"role": "assistant", "content": "", "tool_calls": calls}


def _gemini_shim(gw):
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    return EnforcementShim(control, _gemini_catalog(), gw)


def test_a_split_model_turn_bound_for_gemini_is_warned_about(caplog):
    """A tool-call message starting with an unsigned call is what Gemini rejects, with an opaque
    "position N" (verified live 2026-09-04, #155). Warning here is what connects the rejection to
    its cause. This is the rarer of the two causes; see the foreign-history test below for the one
    that actually reached a user.

    Driven through `handle` on purpose: the summary reads module state and the RESOLVED model, so a
    unit test of the pure helpers cannot catch a mis-wired call site — an earlier build of this had
    a NameError here that every pure-function test passed straight over.
    """
    gw = FakeGatewayClient()
    with caplog.at_level("WARNING", logger="sage.shim"):
        list(_gemini_shim(gw).handle(
            {"model": "gemini-3.7-flash",
             "messages": [{"role": "user", "content": "go"}, _batch("sig-a"), _batch(None)]},
            project="p"))
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("1 of 2 tool-call message(s)" in m and "no thought_signature" in m
               for m in warnings), warnings


def test_history_from_a_model_that_does_not_sign_is_warned_about(caplog):
    """The cause that reached a user, reproduced end to end on 2026-09-04 (#155).

    The shim re-resolves the model per request, so an auto-plan turn runs on sonnet while OpenCode
    keeps one session and believes it is all Gemini. The implement turn then replays sonnet's
    `toolu_*` tool calls — unsigned, every one — to Gemini, which rejects the whole request with the
    user's verbatim `default_api:bash` 400. Nothing was split; the history simply came from a model
    that never signs.
    """
    gw = FakeGatewayClient()
    with caplog.at_level("WARNING", logger="sage.shim"):
        list(_gemini_shim(gw).handle(
            {"model": "gemini-3.7-flash",
             "messages": [{"role": "user", "content": "go"}, _batch(None), _batch(None)]},
            project="p"))
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("2 of 2 tool-call message(s)" in m for m in warnings), warnings


def test_a_healthy_gemini_parallel_batch_is_not_warned_about(caplog):
    """The live-verified healthy shape: one message, first call signed, the rest bare. This must stay
    silent — it is what every ordinary Gemini turn looks like."""
    gw = FakeGatewayClient()
    with caplog.at_level("WARNING", logger="sage.shim"):
        list(_gemini_shim(gw).handle(
            {"model": "gemini-3.7-flash",
             "messages": [{"role": "user", "content": "go"}, _batch("sig-a", None, None)]},
            project="p"))
    assert not [r for r in caplog.records if "no thought_signature" in r.getMessage()]


def test_an_ordinary_model_is_never_warned_about(caplog):
    # gpt-5.4 and sonnet never sign. Warning here would fire on every normal turn.
    control = ModelControl(mode=Mode.IMPLEMENT, phase=Phase.IMPLEMENT)
    gw = FakeGatewayClient()
    with caplog.at_level("WARNING", logger="sage.shim"):
        list(_shim(control, gw).handle(
            {"model": "cheap-vendor", "messages": [_batch(None), _batch(None)]}, project="p"))
    assert not [r for r in caplog.records if "no thought_signature" in r.getMessage()]


def test_the_outgoing_listing_only_appears_with_debug_stream_on(caplog, monkeypatch):
    gw = FakeGatewayClient()
    shim = _gemini_shim(gw)
    req = {"model": "gemini-3.7-flash", "messages": [_batch("sig-a", None)]}

    with caplog.at_level("INFO", logger="sage.shim"):
        list(shim.handle(dict(req), project="p"))
    assert not [r for r in caplog.records if "outgoing tool calls" in r.getMessage()]

    caplog.clear()
    monkeypatch.setattr(ka, "_debug_stream", True)
    with caplog.at_level("INFO", logger="sage.shim"):
        list(shim.handle(dict(req), project="p"))
    listed = [r.getMessage() for r in caplog.records if "outgoing tool calls" in r.getMessage()]
    assert listed and "msg0[c0/read sig=5, c1/read sig=NONE]" in listed[0], listed


def test_the_signature_summary_never_takes_the_turn_down_on_a_malformed_body():
    # The messages are whatever the client sent. Raising here would turn a bad request into a crash.
    gw = FakeGatewayClient()
    shim = _gemini_shim(gw)
    for messages in ([{"role": "assistant", "tool_calls": [None, "x"]}],
                     [{"role": "assistant", "tool_calls": [{"extra_content": "nope"}]}],
                     "not-a-list"):
        list(shim.handle({"model": "gemini-3.7-flash", "messages": messages}, project="p"))
