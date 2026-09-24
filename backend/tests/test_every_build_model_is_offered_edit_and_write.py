"""Every Build model is offered `edit` and `write`, not only `apply_patch` (#539).

OpenCode 1.18.4 chooses the edit tools from the model HANDLE (`ToolRegistry.tools`): an id that
contains `gpt-`, and not `oss` or `gpt-4`, gets `apply_patch` INSTEAD of `edit`/`write`. Sage sends
every alias under one handle, and the shim swaps the real alias in after OpenCode has chosen the
tools. That handle was `gpt-5.4`, so Sonnet, Gemini, GLM and Qwen all had `apply_patch` alone, and a
weaker model's broken envelope can return "Success" and write nothing (#534).

Rig, 2026-09-24, real 1.18.4 against a fake model: the same `sage-implement` turn under `gpt-5.4`
and under a neutral handle differed ONLY in the tool set and the "You are powered by" line. So each
prompt now names its handle: `gpt-5.4` when the turn runs on GPT, which is trained on the patch
envelope, and the neutral `sage-model` for every other model.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from .fake_opencode import Turn
from .opencode_server import BINARY, _opencode_server
from .test_a_prompt_naming_no_app_asks_what_to_build import (  # noqa: F401  (_no_waiting: autouse)
    _REFUSAL,
    _build,
    _no_waiting,
    _run,
)
from .test_enforcement_shim import CATALOG, _patch_messages, _tool_names
from .test_enforcement_shim import _handled_with as _handled  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


def _config() -> dict:
    return json.loads((REPO / "opencode.json").read_text())


def test_the_default_handle_is_one_opencode_gives_edit_and_write():
    handle = _config()["model"].split("/", 1)[1]
    assert "gpt-" not in handle  # the substring OpenCode's registry keys on
    models = _config()["provider"]["sage-gateway"]["models"]
    assert handle in models and "gpt-5.4" in models  # a saved session's handle still resolves
    assert models[handle]["limit"] == models["gpt-5.4"]["limit"]


def test_apply_patch_is_not_withdrawn_from_a_turn_that_has_no_edit():
    """#494 withdraws `apply_patch` after two refusals so the model takes `edit`. OpenCode offers
    one or the other, never both, so a session still on the old handle had no `edit` to take:
    the withdrawal left it with no way to edit at all."""
    from sage.gateway.client import FakeGatewayClient
    from sage.router.model_control import ModelControl
    from sage.router.models import Mode
    from sage.shim.enforcement import EnforcementShim

    gw = FakeGatewayClient()
    shim = EnforcementShim(ModelControl(mode=Mode.AUTO), CATALOG, gw)
    tools = [{"type": "function", "function": {"name": n}} for n in ("apply_patch", "read", "bash")]
    list(shim.handle({"model": "cheap-vendor", "messages": _patch_messages(2), "tools": tools},
                     project="p1"))
    assert "apply_patch" in _tool_names(gw.seen[-1][0])


def _tools_for(url: str, directory: str, handle: str) -> set[str]:
    response = httpx.get(url + "/experimental/tool", timeout=120, params={
        "provider": "sage-gateway", "model": handle, "agent": "sage-implement",
        "directory": directory})
    response.raise_for_status()
    return {str(t.get("id") or t.get("name")) for t in response.json()}


@pytest.mark.skipif(not BINARY.exists(), reason="the pinned OpenCode binary is not installed")
def test_the_pinned_opencode_offers_edit_to_the_default_handle_and_patch_to_gpt(tmp_path):
    """Asked of the binary itself, so an OpenCode upgrade that moves the rule reds here."""
    config = _config()
    config["plugin"] = []
    config["mcp"] = {}
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    config_path = runtime / "opencode.json"
    config_path.write_text(json.dumps(config))
    env = dict(os.environ)
    env.update(OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE="true",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_CACHE_HOME=str(runtime / "cache"), XDG_STATE_HOME=str(runtime / "state"),
               OPENCODE_CONFIG_DIR=str(runtime / "config" / "opencode"))
    with _opencode_server(runtime, env) as url:
        default = _tools_for(url, str(runtime), config["model"].split("/", 1)[1])
        legacy = _tools_for(url, str(runtime), "gpt-5.4")
    assert {"edit", "write"} <= default and "apply_patch" not in default
    assert "apply_patch" in legacy and not {"edit", "write"} & legacy


# --- GPT keeps `apply_patch`: the handle is chosen per prompt ----------------------------------
#
# Rig, 2026-09-24: in ONE session, a prompt naming `gpt-5.4` was offered `apply_patch` and the next,
# naming `sage-model`, `edit` and `write`. OpenCode honours `model` on each v1 prompt.

def _project(catalog=CATALOG, mode=None):
    from types import SimpleNamespace

    from sage.router.model_control import ModelControl
    from sage.router.models import Mode

    return SimpleNamespace(control=ModelControl(mode=mode or Mode.AUTO),
                           shim=SimpleNamespace(catalog=catalog))


def _handle(project) -> str:
    from sage.orchestrator.service import _tool_handle

    handle = _tool_handle(project)
    assert handle["providerID"] == "sage-gateway"
    return handle["modelID"]


def _catalog(**slots):
    from dataclasses import replace
    return replace(CATALOG, **slots)


def test_gpt_on_both_auto_phases_gets_the_gpt_handle():
    assert _handle(_project(_catalog(plan="gpt-5.4", implement="gpt-5.5"))) == "gpt-5.4"


def test_auto_with_one_phase_off_gpt_gets_the_neutral_handle():
    """Auto moves between plan and implement call by call, inside one prompt. GPT given `edit`
    still edits; GLM given only `apply_patch` does not."""
    assert _handle(_project(_catalog(plan="gpt-5.4", implement="glm-5.3"))) == "sage-model"
    assert _handle(_project(_catalog(plan="glm-5.3", implement="gpt-5.4"))) == "sage-model"


def test_a_pinned_mode_reads_only_its_own_model():
    from sage.router.models import Mode

    catalog = _catalog(plan="glm-5.3", implement="gpt-5.4")
    assert _handle(_project(catalog, Mode.IMPLEMENT)) == "gpt-5.4"
    assert _handle(_project(catalog, Mode.PLAN)) == "sage-model"


def test_a_chat_turn_reads_its_own_pick_not_the_build_slots():
    project = _project(_catalog(plan="glm-5.3", implement="glm-5.3"))
    project.control.pick_chat("gpt-5.4")
    project.control.arm_chat("thread")
    assert _handle(project) == "gpt-5.4"
    project.control.pick_chat("sonnet")
    assert _handle(project) == "sage-model"


def test_the_ids_opencode_itself_leaves_on_edit_get_the_neutral_handle():
    for model in ("gpt-oss-120b", "gpt-4o", "sonnet", "GLM 5.3 OR"):
        assert _handle(_project(_catalog(plan=model, implement=model))) == "sage-model", model


def test_a_turn_that_will_not_resolve_gets_the_neutral_handle():
    project = _project(_catalog(plan="gpt-5.4", implement="gpt-5.4"))
    project.control.arm_sensitivity(frozenset())  # an empty approved set raises in the router
    assert _handle(project) == "sage-model"


def test_every_prompt_of_a_build_turn_names_its_handle(tmp_path):
    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])
    _run(orch, "build me a table of lab samples")
    assert [p["model"] for p in oc.prompts] == [
        {"providerID": "sage-gateway", "modelID": "sage-model"}] * len(oc.prompts)
