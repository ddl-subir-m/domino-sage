"""A chart Artifact that fails to load used to be a broken-image icon sitting on the alt text,
which reads as a rendering fault. Same floor as the empty table card: say so, and offer the file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "image_card_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PATH = "examples/thr_spend/ai_consumption_daily_sample.png"
_SRC = "./api/project/file/raw?path=examples%2Fthr_spend%2Fai_consumption_daily_sample.png"


def _card(failed: bool, **extra) -> dict:
    block = {"type": "image", "title": "ai consumption daily sample", "src": _SRC, "path": _PATH,
             **extra}
    # `relativeTime` ends in `toLocaleDateString`, which renders in the machine's own zone. Pinned
    # so a laptop west of UTC does not read a stamp back a day and fail on it.
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"block": block, "failed": failed}),
                         check=False, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "TZ": "UTC"})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_loaded_chart_is_an_image():
    card = _card(False)
    assert card["title"] == "ai consumption daily sample"
    assert card["img"] == _SRC
    assert card["said"] == []


@needs_node
def test_a_chart_that_does_not_load_says_so_and_offers_the_file():
    card = _card(True)
    assert card["title"] == "ai consumption daily sample"
    assert card["img"] is None
    assert card["said"] == ["This chart didn't load.", "Open the file"]
    assert card["links"] == [_SRC]


@needs_node
def test_a_chart_this_project_never_committed_says_so_and_names_the_day():
    """#255: with **Kept rows** off the PNG is written, shown once, and not committed, so a
    Conversation reopened after a restart holds the card and not the file. That is not a load
    failure — there is nothing to open — so the card says what happened, dates it, and says what
    gets the chart back."""
    card = _card(False, missing=True, notKept=True, producedAt="2026-03-04T09:15:00Z")
    assert card["title"] == "ai consumption daily sample"
    assert card["img"] is None
    assert card["links"] == []  # no link to a file this clone does not have
    assert card["said"] == [
        "Drawn March 4, 2026",
        ("This Project doesn't keep chart images in its files, so this one didn't survive a "
         "restart. Ask for it again to see it."),
    ]


@needs_node
def test_a_chart_gone_for_some_other_reason_does_not_blame_the_setting():
    """`missing` is a `stat`; `notKept` is a reason. A chart can be gone from a Project that DID
    opt in — a Workspace that died before its save, the orphan sweep, somebody deleting it — and a
    card that named the setting there would contradict a switch the person just turned on."""
    card = _card(False, missing=True, producedAt="2026-03-04T09:15:00Z")
    assert card["img"] is None
    assert card["said"] == [
        "Drawn March 4, 2026",
        "This chart isn't in this Project's files. Ask for it again to see it.",
    ]


@needs_node
def test_a_missing_chart_with_no_date_still_says_what_happened():
    """A manifest row written before `producedAt` existed. The sentence is the part that has to
    land; the stamp is the part that can be absent."""
    card = _card(False, missing=True, notKept=True)
    assert card["img"] is None
    assert len(card["said"]) == 1
