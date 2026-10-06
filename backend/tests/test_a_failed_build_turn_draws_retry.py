"""Build draws Chat's Retry on a turn that failed on the gateway, and pressing it sends the request
again (#663).

The transcript is read through a real `store.loadBuild()` and drawn by the same `SW.Message` Build
draws, so a Retry that only exists on a hand-built message cannot pass. The press is followed to
the request body, which is the only place "the same request" can be checked.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_retry_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

REFUSED = ("Sage couldn't finish — model call failed: The model gateway stream stopped "
           "(RemoteProtocolError). Retry the turn.")


def _run(history: list[dict]) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"history": history}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _failed(prompt: str, bubble: str | None = None) -> list[dict]:
    return [
        {"type": "user", "text": bubble or prompt, "order": 0},
        {"type": "error", "message": REFUSED, "reason": "gateway", "prompt": prompt, "order": 1},
        {"type": "done", "ok": False, "decision": "gateway error", "order": 2},
    ]


def test_a_gateway_failure_draws_retry_on_the_failed_turn():
    drawn = _run(_failed("chart revenue from @sales.csv"))

    assert drawn["retries"] == [{"role": "user", "retry": False},
                                {"role": "assistant", "retry": True}]


def test_retry_sends_the_same_request_with_its_chips_and_mode():
    drawn = _run(_failed("chart revenue from @sales.csv"))

    assert drawn["posted"] == [{"prompt": "chart revenue from @sales.csv",
                                "mentions": ["sales.csv"], "howSageWorks": "guided"}]


def test_retry_sends_the_request_not_the_bubble_a_click_left():
    """A card click writes "Build it." as the bubble. Re-sending the bubble would ask for nothing."""
    drawn = _run(_failed("chart revenue from @sales.csv", bubble="Build it."))

    assert [p["prompt"] for p in drawn["posted"]] == ["chart revenue from @sales.csv"]


def test_chats_merged_copy_of_a_failed_build_turn_draws_no_build_retry():
    """Chat's merged read draws Build's rows too. A press there would build into whichever app is
    selected, so the Build Retry stays on Build's own transcript."""
    drawn = _run(_failed("chart revenue from @sales.csv"))

    assert drawn["elsewhere"] is False


def test_a_failure_with_no_request_to_send_draws_no_retry():
    """An approval's row carries an empty prompt: there is no sentence of the person's to replay."""
    drawn = _run(_failed("", bubble="Approved the plan."))

    assert [r["retry"] for r in drawn["retries"]] == [False, False]


def test_a_clean_build_turn_draws_no_retry():
    drawn = _run([{"type": "user", "text": "chart revenue", "order": 0},
                  {"type": "done", "ok": True, "decision": "built", "order": 1}])

    assert [r["retry"] for r in drawn["retries"]] == [False, False]
