"""The second silent route into a bashless turn mid-investigation is the Artifact lane.

"give me a table of the top candidates" classifies as `data_artifact`, and that turn's tool list
becomes read plus `artifact_write` only. It is the right shape for a turn that charts what is
already known and the wrong one for a turn that must still measure something: `findings.md` stops
being writable — `artifact_write` refuses any path outside `examples/<threadId>/` — and the
warehouse stops being reachable, because reaching it is Python and Python is bash.

Same gate as `data_answer`, at the other arming site: the Artifact token is not minted while this
Thread holds an open investigation.
"""
from __future__ import annotations

from pathlib import Path

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode, _orch


def test_a_data_artifact_turn_in_a_thread_with_an_investigation_keeps_the_full_lane(tmp_path: Path):
    turns = [Turn(text="Charted.")]
    orch, oc = _orch(
        tmp_path, turns,
        gateway=IntentGateway({"label": "data_artifact", "confidence": 0.92}),
        client=lambda ws: ObservedControlOpenCode(ws, list(turns)),
    )
    project = orch.project(start_preview=False)
    oc.control = project.control
    tid = orch.create_thread()["id"]
    # `data_artifact` is downgraded without one, so the lane this test is about never opens.
    path = ".sage/scratch/accounts.csv"
    bound = project.workspace.path / path
    bound.parent.mkdir(parents=True, exist_ok=True)
    bound.write_text("account,events\nVLTA,291\n")
    orch.add_thread_context(tid, {"kind": "file", "name": "accounts.csv", "path": path})
    orch.decide_thread_investigation(tid, "open")

    list(orch.chat_stream(tid, "give me a table of the top candidates"))

    assert oc.snapshots
    assert not oc.snapshots[0].chat_artifact_turn
