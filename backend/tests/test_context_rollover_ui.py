"""Issue #528 UI contract against the real store and reducer."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "js" / "build_events_harness.mjs"
_STORE = Path(__file__).parents[1] / "sage" / "workbench" / "js" / "store.js"
_BLOCKS = Path(__file__).parents[1] / "sage" / "workbench" / "js" / "components" / "message-blocks.js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)")


def _run(history, *, continuation=None, click=False):
    result = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"history": history, "continuation": continuation, "click": click}),
        text=True, capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_node
def test_rollover_and_context_limit_render_once_and_replay_needs_server_confirmation():
    history = [
        {"type": "build-rollover", "generation": 1,
         "message": "The build context reached its safe size. Sage is continuing once in a clean session."},
        {"type": "build-context-limit", "kept": True, "continuationId": "opaque",
         "message": "The build reached its context limit twice. Current app changes are saved. Continue in a new clean session."},
        {"type": "done", "ok": False, "decision": "context_limit"},
    ]
    unavailable = _run(history)
    assert unavailable["types"].count("build_context_limit") == 1
    assert unavailable["context"] == [{
        "live": False,
        "continuationId": "opaque",
        "message": history[1]["message"],
    }]

    live = _run(history, continuation={
        "continuationId": "opaque", "conversation": "conv_1", "appId": "app_a",
        "state": "available",
    })
    assert live["context"][0]["live"] is True


@needs_node
def test_continue_click_sends_only_opaque_id_and_scope_and_cannot_double_start():
    history = [{
        "type": "build-context-limit", "kept": False, "continuationId": "opaque",
        "message": "Continue in a new clean session.",
    }]
    result = _run(history, continuation={
        "continuationId": "opaque", "conversation": "conv_1", "appId": "app_a",
        "state": "available",
    }, click=True)
    assert result["calls"] == [{
        "path": "/project/build/continue",
        "body": {"continuationId": "opaque", "conversation": "conv_1", "appId": "app_a"},
    }]
    assert "prompt" not in json.dumps(result["calls"])


def test_context_limit_uses_a_dedicated_component_and_event_handlers_do_not_auto_continue():
    blocks = _BLOCKS.read_text()
    assert "function BuildContextLimit" in blocks
    assert "case 'build_context_limit'" in blocks
    source = _STORE.read_text()
    branch = source[source.index("function applyBuildEvent(ev)"):
                    source.index("async function refreshBindings")]
    assert "ev.type === 'build-context-limit'" in branch
    assert "continueContextBuild(" not in branch and "fetch(" not in branch
