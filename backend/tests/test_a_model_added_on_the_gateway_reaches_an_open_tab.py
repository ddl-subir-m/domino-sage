"""A model a gateway administrator adds reaches an open tab's pickers without a reload (#646).

The pickers draw from the listing a scope load read, and nothing read it again until a project
switch, an Add or a Remove, or Browse Domino opening. So a model added upstream was invisible to
anybody already in Sage. Opening a model picker, or coming back to the tab, now re-reads the
listing — at most once per freshness window, and never before the first listing has landed.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "model_list_refresh_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"act": act}), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("act", ["open", "visible", "drawer"])
def test_the_new_model_reaches_the_pickers(act):
    out = _act(act)
    assert out["resourceReads"] == 1
    assert out["aliases"] == ["sonnet", "new-model"]


def test_opening_the_menu_twice_reads_once():
    assert _act("open-twice")["resourceReads"] == 1


def test_a_tab_going_out_of_view_reads_nothing():
    assert _act("hidden")["resourceReads"] == 0


def test_nothing_is_read_before_the_scope_load_has_listed():
    """That load reads the listing itself; a second read beside it would be a duplicate."""
    assert _act("cold")["resourceReads"] == 0
