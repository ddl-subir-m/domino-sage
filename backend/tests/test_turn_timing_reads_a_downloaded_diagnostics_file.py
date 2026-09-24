"""`scripts/turn-timing.py --file` reads the Build diagnostics a person downloads (#534).

The per-call table for #534 was built by hand from two `build-<turnId>.json` downloads, because the
readout only spoke HTTP to a running Builder. The download is what arrives in a ticket.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "turn-timing.py"


def _call(n, verified):
    return {"n": n, "model": "sonnet", "phase": "implement", "protocol": "chat", "routeVerified": verified,
            "atMs": 10 + n * 1000, "ms": 900, "ttfbMs": 700, "firstActionMs": 700,
            "forwardedReqBytes": 150 * 1024, "tools": ["apply_patch"]}


def test_the_file_readout_names_each_call_its_wait_and_an_unmeasured_lane(tmp_path):
    doc = {"schemaVersion": 1, "turn": {"phase": "implementation", "startedAt": 1790269775.0},
           "buildOutcome": {"status": "success"},
           "timing": {"ms": 3000, "running": False, "calls": [_call(1, True), _call(2, False)],
                      "spans": [{"name": "agent-turn.1", "depth": 0, "atMs": 0, "ms": 2900, "open": False}],
                      "tools": [], "intervals": [], "counters": {}, "observations": {}}}
    path = tmp_path / "build-turn_x.json"
    path.write_text(json.dumps(doc))
    out = subprocess.run([sys.executable, str(SCRIPT), "--file", str(path)],
                         capture_output=True, text=True, check=True).stdout
    assert "before first action, summed: 1.4s of 1.8s" in out
    lines = [line.split() for line in out.splitlines() if line.strip().startswith("#")]
    assert [(row[0], row[5], row[6]) for row in lines] == [("#1", "chat", "apply_patch"),
                                                          ("#2", "chat?", "apply_patch")]
