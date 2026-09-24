from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_the_plan_card_shows_the_saved_request_read_only_and_collapsed():
    harness = Path(__file__).parent / "js" / "plan_original_request_harness.mjs"
    messages = ["Build the table exactly as requested.", "Add the chart below it."]
    result = subprocess.run(
        ["node", str(harness)],
        input=json.dumps(messages),
        text=True,
        capture_output=True,
        check=True,
    )
    drawn = json.loads(result.stdout)

    assert drawn["tag"] == "details"
    assert drawn["open"] is False
    assert drawn["inputs"] == 0
    assert drawn["words"] == ["Original request", *messages]
