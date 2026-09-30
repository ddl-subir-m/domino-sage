"""The Build mode a person picked survives a Builder restart.

The server keeps the mode in memory, so a restarted Builder answers Auto. Live on 2026-09-30: the
composer was re-picked to Implement only after a restarted workspace had already run a turn in Auto,
and the failure gate sent that turn to planning. The browser remembers the pick per Project, the
way ADR-0070 keeps a per-person choice, and puts it back when the server has forgotten it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_mode_restart_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

PROJECT = "p-acme-risk"


def _run(saved, server, pick=None) -> dict:
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"saved": saved, "server": server, "pick": pick}),
                         capture_output=True, text=True, timeout=30, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_restarted_builder_gets_the_saved_mode_back():
    r = _run({PROJECT: "implement"}, "auto")
    assert r["posted"] == ["implement"]
    assert r["serverMode"] == "implement"
    assert r["buildMode"] == "implement"


def test_nothing_is_posted_when_the_server_already_agrees():
    r = _run({PROJECT: "plan"}, "plan")
    assert r["posted"] == []
    assert r["buildMode"] == "plan"


def test_another_projects_mode_is_not_applied_here():
    r = _run({"p-other": "implement"}, "auto")
    assert r["posted"] == []
    assert r["buildMode"] == "auto"


def test_a_pick_is_remembered_for_this_project():
    r = _run(None, "auto", pick="implement")
    assert r["saved"] == {PROJECT: "implement"}
