"""An investigation lost its shell on the follow-up question that continued it.

Turn one asks "which accounts look like adopters?", classifies as an agentic turn, queries the
warehouse through Python and writes what it measured to this Thread's `findings.md`. Turn two asks
"so which of those score highest?" — a question about data, which is `data_answer`, which arms
`arm_read_only("question")`, and `READ_ONLY_DENIED = WRITE_TOOLS | SHELL_TOOLS`
(`router/phase_classifier.py`) takes the shell with it. `live_read_table` accepts no SQL, so the
warehouse is reachable only through Python, which means only through bash. Turn two could neither
measure anything new nor append to the file it was told to keep, and said so in prose instead.

The gate is the file: once this Thread has one, the turns that follow keep the tools the turn that
opened the investigation had.
"""
from __future__ import annotations

from pathlib import Path

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode, _orch


def test_a_data_answer_turn_in_a_thread_with_findings_is_not_armed_read_only(tmp_path: Path):
    turns = [Turn(text="They score highest.")]
    orch, oc = _orch(
        tmp_path, turns,
        gateway=IntentGateway({"label": "data_answer", "confidence": 0.91}),
        client=lambda ws: ObservedControlOpenCode(ws, list(turns)),
    )
    project = orch.project(start_preview=False)
    oc.control = project.control
    tid = orch.create_thread()["id"]
    findings = project.record.path / ".sage" / "threads" / tid / "findings.md"
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text("- 2026-09-16T11:04Z — SFDC_CONTACT_ID populated 16,756/89,399 (18.7%)\n")

    list(orch.chat_stream(tid, "so which of those accounts score highest?"))

    assert oc.snapshots
    assert not oc.snapshots[0].read_only_turn
