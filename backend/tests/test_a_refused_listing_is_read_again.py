"""A group note left by a refused listing does not stand until an unrelated act clears it.

`/api/resources` answers per kind, so one leg can refuse while the other two answer. The panel then
draws that reason above rows which are the LAST good answer carried forward — present, stale and
unmarked (ADR-0034). Reported live: the LLM Gateway answered a 40x at `/v1/models` once, while the
token sidecar was still warming, and the note stayed under a group full of models. Every other model
list on screen went on reading right, because they read membership or those carried rows. Opening
Browse Domino cleared it, which is the tell: nothing else re-read the platform.

So the fix is a read, not a wording. One re-read per scope load, four seconds after the refusal — a
platform that is really down costs one extra call, not a poll.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "listing_retry_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": act}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_refusal_is_read_again_and_the_note_goes():
    """The bug, from the outside: a warning nobody could clear without knowing where to click."""
    out = _act("clears")
    assert out["afterRefusal"]["note"] == "The LLM Gateway answered 400 at /v1/models."
    assert out["afterRefusal"]["reads"] == 1
    # The second read left on its own, and it is what takes the note down.
    assert out["afterRetry"]["reads"] == 2
    assert out["afterRetry"]["note"] is None
    assert [m["name"] for m in out["afterRetry"]["models"]] == ["Risk scorer"]


def test_a_platform_that_stays_down_is_asked_once():
    """One retry, not a poll. A gateway that is really refusing must not be hammered from a panel
    nobody is even looking at, and the note it raises is true — it should stay up."""
    out = _act("once")
    assert out["afterRetry"]["reads"] == 2
    assert out["afterRetry"]["note"] == "The LLM Gateway answered 400 at /v1/models."
    assert out["later"]["reads"] == 2
