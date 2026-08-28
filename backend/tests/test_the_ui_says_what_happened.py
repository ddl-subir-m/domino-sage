"""A call that fails says so, and the plan card shows the plan you actually have.

Four of these share one shape: the UI updates first — the chip drawn, the token typed, the file
accepted — and the call behind it can reject. A swallowed rejection leaves the screen asserting
something that never happened, and the person finds out later or not at all.

The fifth has no failure in it. `BuildPlanCard` rendered `block.plan` outside edit mode while the
button that leaves edit mode is labelled "Preview", so a person edited the plan, clicked Preview to
check it, and watched their own typing disappear. `approveBuild` had been sending the edit all
along; only the screen disagreed.

Each test drives the real component and reads what it drew or what it reported. Before the fixes,
the plan card showed the original and the other four took down the harness with an unhandled
rejection — which is what a floating promise does in a browser too, minus the stack trace anybody
would see.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "ui_feedback_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(scenario: str) -> dict:
    """What the person would have seen. A non-zero exit is the unhandled rejection itself."""
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"scenario": scenario}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_plan_card_previews_the_edit_not_the_original():
    """Edit the plan, click the button labelled Preview, read the card. It must show the edit —
    that is what Approve is going to send."""
    shown = _run("planPreview")["preview"]
    assert "THE EDITED PLAN" in shown
    assert "THE ORIGINAL PLAN" not in shown


@needs_node
def test_one_rejected_upload_does_not_abandon_the_rest_of_the_drop():
    """Two files, both refused. Two messages: the loop carried on and named each file. One message
    would mean the first failure took the rest of the drop with it."""
    reported = _run("uploadFailures")["reported"]
    assert len(reported) == 2
    assert any("first.csv" in m for m in reported)
    assert any("second.csv" in m for m in reported)


@needs_node
def test_a_resource_that_could_not_be_attached_says_so():
    """Dropped on the composer, refused by the server. No chip appears, so without a message the
    next prompt names a file that was never attached."""
    out = _run("contextAddFailure")
    assert out["reported"], "the drop failed silently"
    assert out["chips"] == 1, "the refused resource must not draw a chip"


@needs_node
def test_a_chip_that_could_not_be_removed_says_so():
    """The chip's own close button, with the delete refused. The chip stays on screen, so silence
    reads as a dead button and the person presses it again."""
    assert _run("detachFailure")["reported"], "the detach failed silently"


@needs_node
def test_a_failed_save_to_project_reaches_the_person():
    """`GraduationModal.save` had a `finally` and no `catch`. The modal stayed open saying nothing
    and Enter retried into the same silence. Its sibling `HandoffSheet.go` already did this right."""
    out = _run("graduationFailure")
    assert out["reported"] == ["the server said no"]
    assert out["stillOpen"] is True, "a failed save must not close the modal"
