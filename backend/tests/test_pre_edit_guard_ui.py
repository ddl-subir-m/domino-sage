"""Issue #527 UI contract, executed against the real JavaScript store."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "js" / "build_events_harness.mjs"
_STORE = Path(__file__).parents[1] / "sage" / "workbench" / "js" / "store.js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


@needs_node
def test_recovery_and_terminal_events_render_once_and_keep_an_approved_plan():
    recovery = "No app edit was made. Sage is restarting once with a clean context."
    terminal = ("Sage stopped before changing the app because the clean retry also reached the "
                "pre-edit work limit.")
    history = [
        {"type": "plan-proposed", "plan": "Build the app", "planId": "plan-a"},
        {"type": "build-recovery", "reason": "pre_edit_limit", "attempt": 1,
         "message": recovery},
        {"type": "build-pre-edit-limit", "trigger": "model_calls", "message": terminal},
        {"type": "done", "ok": False, "decision": "pre_edit_limit"},
    ]
    result = subprocess.run(
        ["node", str(_HARNESS)], input=json.dumps({"history": history}), text=True,
        capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["values"].count(recovery) == 1
    assert payload["values"].count(terminal) == 1
    assert not any(value == "Stopped — pre_edit_limit" for value in payload["values"])
    assert payload["plans"] == [{"pending": True, "cancelled": False}]


def test_live_event_handlers_only_update_ui_state_and_cannot_start_recovery_work():
    source = _STORE.read_text()
    branch = source[source.index("function applyBuildEvent(ev)"):
                    source.index("async function refreshBindings")]
    assert "ev.type === 'build-recovery'" in branch
    assert "ev.type === 'build-pre-edit-limit'" in branch
    assert "sendBuildPrompt(" not in branch and "fetch(" not in branch
