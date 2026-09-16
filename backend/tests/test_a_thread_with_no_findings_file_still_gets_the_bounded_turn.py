"""The absence half of the gate is load-bearing: no findings file, no exemption.

#364 bounds a Chat turn that only answers a question, and this ticket carves one hole in that
bounding. The hole has to close again when the condition that opened it is gone, or the carve-out
is not a carve-out — it is the bounding removed, with a condition written beside it that never
holds. A guard gated on a value that is always true reads exactly like a guard that works.

So this is the other half of `test_an_investigation_in_progress_keeps_its_shell_on_a_data_answer`,
and the two only mean something together: the same question, in a Thread with nothing on disk,
still arms `arm_read_only("question")`.
"""
from __future__ import annotations

from pathlib import Path

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode, _orch


def test_a_data_answer_turn_without_findings_is_still_armed_read_only(tmp_path: Path):
    turns = [Turn(text="They score highest.")]
    orch, oc = _orch(
        tmp_path, turns,
        gateway=IntentGateway({"label": "data_answer", "confidence": 0.91}),
        client=lambda ws: ObservedControlOpenCode(ws, list(turns)),
    )
    project = orch.project(start_preview=False)
    oc.control = project.control
    tid = orch.create_thread()["id"]
    assert not (project.record.path / ".sage" / "threads" / tid / "findings.md").exists()

    list(orch.chat_stream(tid, "so which of those accounts score highest?"))

    assert oc.snapshots
    assert oc.snapshots[0].read_only_turn
    assert oc.snapshots[0].read_only_reason == "question"
