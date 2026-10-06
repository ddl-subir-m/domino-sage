"""The handoff sheet's way out, run through the real JS (#661).

The server half is pinned in `test_a_handoff_never_leaves_the_person_stuck.py`. These pin the wire:
a Cancel that only closed the modal, a name field whose answer never reached the confirm, or a
redraft button that posted no flag would each look like the sheet worked.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parent / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(harness: str) -> dict:
    out = subprocess.run(["node", str(_JS / harness)], input="", check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def sheet() -> dict:
    return _run("handoff_sheet_acts_harness.mjs")


@pytest.fixture(scope="module")
def store() -> dict:
    return _run("handoff_cancel_harness.mjs")


def test_the_name_field_starts_as_the_plans_title(sheet):
    assert sheet["defaultName"] == "Pipeline Signal Board"


def test_open_builder_sends_the_name_the_person_typed(sheet):
    assert sheet["confirmTargets"] == [{"appId": "", "name": "Signal Room"}]


def test_cancel_on_the_sheet_goes_through_the_store(sheet):
    assert sheet["cancels"] == 1


def test_the_sheet_can_ask_for_a_new_plan(sheet):
    assert sheet["redrafts"] == [{"redraft": True}]


def test_cancel_puts_the_write_a_plan_offer_back(store):
    cancelled = store["cancelled"]
    assert cancelled["open"] is False
    assert cancelled["offers"] == 1
    assert cancelled["handoff"]["status"] == "suggested"
    assert "planId" not in cancelled["handoff"]


def test_cancel_tells_the_server(store):
    assert {"url": "api/threads/t1", "method": "PATCH",
            "body": {"handoff": "cancel"}} in store["cancelled"]["routes"]


def test_a_redraft_asks_the_server_for_a_new_plan(store):
    assert [r["body"] for r in store["redrafted"]["routes"]] == [{"redraft": True}]
