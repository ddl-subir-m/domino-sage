"""The Dataset gate was built but never registered as a gate (#196, ADR-0039).

`GATE_DECISIONS` in `store.js` is the list of endings that stop ON PURPOSE, and its own comment
says a red "Stopped —" line under one of them "reads as the app having broken rather than as a
question waiting for an answer". The comment above it counts "all five gate decisions"; the five
are reset, incoming, source, table and Dataset. Only four are in the list. `dataset files` — the
last of the five to ship — is missing.

One missing key, three behaviours, because two more objects are BUILT from that one:

- the red line itself, drawn where `!GATE_DECISIONS[ev.decision]`;
- `KEEPS_THE_PLAN_CARD`, which spreads it — so a plan still waiting for approval loses its Approve
  and Cancel the moment a Dataset card is drawn beside it, which is the fault #178 fixed for the
  question that arrives the same way;
- `ASKED_FOR`, which also spreads it — so `endedBadly` calls a question a failure and buys the
  ADR-0027 preflight listing to explain a turn that did exactly what it was designed to do.

The first two are pinned here. The third is the same key and has no observable in this harness: it
is a fetch nobody sees, which is most of why it went unnoticed for as long as it did.

Held against the table and Data Source cards rather than alone, because the bug is that the Dataset
card is not treated like them, and a test that never names them cannot say so.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_events_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

PROMPT = "build me a dashboard from the data in revenue_2026"

# The card exactly as `_dataset_files_events` writes it, cut to the keys the transcript reads.
CARD = {"type": "dataset-files", "prompt": PROMPT,
        "message": "Nothing in revenue_2026 matched. Pick a file, or continue without attaching "
                   "one.",
        "datasetId": "ds_revenue", "datasetName": "revenue_2026", "answered": {},
        "rows": [{"kind": "file", "path": "calls_daily.csv", "size": 2048}],
        "allRows": [{"kind": "file", "path": "calls_daily.csv", "size": 2048}],
        "total": 1, "listed": 1, "matched": 0, "truncated": False}


def _drawn(history: list[dict]) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"history": history}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_dataset_card_is_not_doubled_by_a_red_stopped_line():
    """What the person actually saw. The card above it is a list of buttons asking them to pick a
    file; "Stopped — dataset files" underneath reads as the app having broken, and the two together
    say the question both was and was not answered."""
    drawn = _drawn([
        {"type": "user", "text": PROMPT},
        CARD,
        {"type": "done", "ok": False, "decision": "dataset files"},
    ])

    assert not any("Stopped — dataset files" in v for v in drawn["values"])


@needs_node
def test_the_two_cards_beside_it_are_already_quiet():
    """The comparison that makes the one above a bug rather than a preference. These two ship the
    same shape — a list of buttons, a `done` that carries `ok: False` — and neither draws a line."""
    for decision in ("table candidates", "data source candidates"):
        drawn = _drawn([
            {"type": "user", "text": PROMPT},
            {"type": "done", "ok": False, "decision": decision},
        ])

        assert not any(f"Stopped — {decision}" in v for v in drawn["values"])


@needs_node
def test_a_dataset_question_leaves_a_pending_plan_card_approvable():
    """`KEEPS_THE_PLAN_CARD` is spread from the same map, so the missing key takes the buttons too.

    The sequence is ordinary: a plan is proposed and waits, the person asks for something that names
    a Dataset the app has attached nothing from, and the gate asks which file. That turn never ran
    and wrote nothing, so the plan under it is still the plan the server would build — but its
    Approve and Cancel are gone, and the only way back to them is a reload.
    """
    drawn = _drawn([
        {"type": "user", "text": "build me a consumption dashboard"},
        {"type": "plan-proposed", "plan": "1. Add the table", "planId": "pd_1", "steps": 2},
        {"type": "user", "text": PROMPT},
        CARD,
        {"type": "done", "ok": False, "decision": "dataset files"},
    ])

    assert drawn["plans"] == [{"pending": True, "cancelled": False}]


@needs_node
def test_a_build_that_really_stopped_still_says_so():
    """The property this must not widen. A gate decision is quiet because a card answers for it;
    an ending with no card must keep both the red line and the closed plan card."""
    drawn = _drawn([
        {"type": "user", "text": PROMPT},
        {"type": "plan-proposed", "plan": "1. Add the table", "planId": "pd_1", "steps": 2},
        {"type": "done", "ok": False, "decision": "gateway error"},
    ])

    assert any("Stopped — gateway error" in v for v in drawn["values"])
    assert drawn["plans"] == [{"pending": False, "cancelled": False}]
