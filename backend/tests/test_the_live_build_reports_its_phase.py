"""The Build chip follows the phase that actually runs, including a pinned first call."""
import json
import subprocess
from pathlib import Path

from .test_a_dead_alias_stops_the_turn_before_it_starts import _no_waiting  # noqa: F401
from .test_a_planned_build_is_reviewed_against_its_done_when import BUILT, PLAN, ReviewGateway, _orch


def test_the_stream_updates_the_phase_in_both_directions():
    harness = Path(__file__).parent / "js" / "build_stream_harness.mjs"
    events = [{"type": "phase", "phase": "implement"},
              {"type": "phase", "n": 2, "total": 3},
              {"type": "phase", "phase": "plan"},
              {"type": "done", "ok": True, "decision": "plan proposed"}]
    result = subprocess.run(
        ["node", str(harness)], input=json.dumps({"events": events}), text=True,
        capture_output=True, check=True, timeout=15,
    )
    assert json.loads(result.stdout)["phases"] == ["plan", "implement", "plan"]


def test_a_pinned_implementation_reports_its_phase_before_it_changes(tmp_path):
    orch, _oc = _orch(tmp_path, ReviewGateway('{"unmet": []}'), [PLAN, BUILT])
    planned = list(orch.build_stream("build me a usage drift tab", conversation="c1"))
    built = list(orch.approve_stream(conversation="c1"))
    assert any(e.get("phase") == "plan" for e in planned if e["type"] == "phase")
    assert any(e.get("phase") == "implement" for e in built if e["type"] == "phase")
