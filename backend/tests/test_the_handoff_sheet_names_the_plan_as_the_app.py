"""The handoff sheet's Plan line names what the sheet will build, run through the real JS (#685).

It used to show the plan's own heading beside a name field holding the de-duplicated name, so a
plan headed "Signal Room" in a project that already had a "Signal Room" read "Plan: Signal Room"
over "Signal Room 3" — as though the new app were reusing the old app's plan.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "handoff_sheet_plan_line_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


@pytest.fixture(scope="module")
def sheet() -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input="", check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_plan_line_and_the_name_field_read_the_same_fresh_name(sheet):
    assert sheet["opened"] == {"plan": "Signal Room 3", "field": "Signal Room 3"}


def test_editing_the_name_field_updates_the_plan_line(sheet):
    assert sheet["edited"] == {"plan": "Pipeline Board", "field": "Pipeline Board"}


def test_a_cleared_name_field_names_what_the_server_will_use(sheet):
    """Left blank, the confirm sends no name and the server names the app from the plan document,
    which is the name the field started from."""
    assert sheet["cleared"]["plan"] == "Signal Room 3"


def test_building_into_an_existing_app_names_that_app(sheet):
    assert sheet["existing"] == {"plan": "Draft app 1", "field": None}
