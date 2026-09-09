"""A chart Artifact that fails to load used to be a broken-image icon sitting on the alt text,
which reads as a rendering fault. Same floor as the empty table card: say so, and offer the file.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "image_card_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PATH = "examples/thr_spend/ai_consumption_daily_sample.png"
_SRC = "./api/project/file/raw?path=examples%2Fthr_spend%2Fai_consumption_daily_sample.png"


def _card(failed: bool) -> dict:
    block = {"type": "image", "title": "ai consumption daily sample", "src": _SRC, "path": _PATH}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"block": block, "failed": failed}),
                         check=False, capture_output=True, text=True, timeout=60)
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
