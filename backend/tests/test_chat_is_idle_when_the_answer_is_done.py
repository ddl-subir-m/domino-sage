"""A classifier's open SSE tail is not a running Chat turn (#417)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
HARNESS = Path(__file__).parent / "js" / "chat_done_harness.mjs"


def run(mode):
    result = subprocess.run(["node", str(HARNESS)], input=json.dumps({"mode": mode}),
                            text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["answered", "failed", "left", "duplicate-done", "late-error"])
def test_done_releases_the_ui_while_the_suggestion_stream_stays_open(mode):
    out = run(mode)
    assert out["whileAnswering"]["busy"] is True
    assert out["streamStillOpen"] is True
    assert out["atDone"]["busy"] is False
    assert out["atDone"]["running"] is None
    assert out["atDone"]["typing"] is None
    assert out["streams"] == 2
    for phase in ("beforeTail", "afterTail", "afterOldClose"):
        assert out[phase]["busy"] is True
        assert out[phase]["running"] == out["beforeTail"]["running"]
        assert out[phase]["typing"] == out["beforeTail"]["typing"]
    assert out["beforeTail"]["typing"]
    assert out["afterTail"]["suggestions"] == (0 if mode == "left" else 1)
    for phase in ("afterSecondDone", "afterClose"):
        assert out[phase]["busy"] is False
        assert out[phase]["running"] is None
        assert out[phase]["typing"] is None


@pytest.mark.parametrize("mode", ["early-eof", "early-error"])
def test_a_stream_without_done_still_releases_its_ui_state(mode):
    out = run(mode)
    assert out["whileAnswering"]["busy"] is True
    assert out["afterClose"]["busy"] is False
    assert out["afterClose"]["running"] is None
    assert out["afterClose"]["typing"] is None
