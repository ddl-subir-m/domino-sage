"""The kind of app New app makes is the viewer's saved answer, and the answer reaches the wire (#490).

Three claims. The preference has a home with the right default and survives a reload, which the
prefs harness (#52) proves by running it. The Build rail's New app sends that answer to
`POST /api/apps`, which is the one place it applies. And the drawer offers exactly the names the
server's registry knows, so a person cannot pick a stack Sage cannot seed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.workspace.stack import STACKS, default_stack_name

_HARNESS = Path(__file__).resolve().parent / "js" / "prefs_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _settings_drawer() -> str:
    shell = (_JS / "components" / "shell.js").read_text()
    return shell[shell.index("SW.SettingsDrawer = function"):shell.index("SW.HelpDrawer = function")]


def test_the_default_is_the_no_build_stack_and_a_choice_survives_a_reload():
    answers = _run([
        {"viewer": "u1", "op": "get", "name": "appStack"},
        {"op": "set", "name": "appStack", "value": "react-vite"},
        {"op": "reload"},
        {"op": "get", "name": "appStack"},
        {"op": "set", "name": "appStack", "value": "cobol-cgi"},
        {"op": "get", "name": "appStack"},
    ])
    assert answers[0] == "fastapi-antd"
    assert answers[1] is True and answers[3] == "react-vite"
    assert answers[4] is False, "a name the registry does not know is refused, not stored"
    assert answers[5] == "react-vite"


def test_the_drawer_offers_exactly_the_stacks_the_server_can_seed():
    drawer = _settings_drawer()
    block = drawer[drawer.index("appStack"):]
    offered = set(re.findall(r"value: '([a-z-]+)'", block[:block.index("Conversation view")]))
    assert offered == set(STACKS), "a stack in one list and not the other is a 400 or a dead button"
    prefs = (_JS / "prefs.js").read_text()
    listed = re.search(r"appStack: \{ fallback: '([a-z-]+)', values: \[([^\]]+)\] \}", prefs)
    assert listed and set(re.findall(r"'([a-z-]+)'", listed.group(2))) == set(STACKS)


def test_new_app_sends_the_saved_answer_and_nothing_else_reads_it():
    store = (_JS / "store.js").read_text()
    api = (_JS / "api.js").read_text()
    assert "SW.api.createApp({ stack: SW.prefs.get('appStack') })" in store
    assert "post('/apps', stack ? { stack } : {})" in api
    # One reader. A second one — a mode switch, a handoff — would make the preference decide
    # something for an app that already exists, which is the one thing a stack never does.
    everything = "\n".join(p.read_text() for p in _JS.rglob("*.js"))
    assert everything.count("prefs.get('appStack')") == 2, "the drawer and New app, no third reader"


def test_the_deployment_default_matches_the_viewers_default(monkeypatch):
    """Two defaults, one answer: an older Workbench that sends no stack and a viewer who never
    opened the drawer both get the same kind of app."""
    monkeypatch.delenv("SAGE_DEFAULT_STACK", raising=False)
    prefs = (_JS / "prefs.js").read_text()
    viewer_default = re.search(r"appStack: \{ fallback: '([a-z-]+)'", prefs).group(1)
    assert default_stack_name() == viewer_default
