"""A turn that follows one which started an investigation was never told the findings file existed.

#380 made `.sage/threads/<threadId>/findings.md` reachable and the pinned prompt blessed writing
it, and both are about what a turn MAY do. Neither says what is on disk right now. So turn two
opened cold: the pinned rule is one paragraph in a 12.5 KB pack, and the turn's own "where you
stand" block — the one the model reads before it plans — listed the Thread id, the Artifact folder
and this Thread's Artifacts, and nothing about the file the previous turn had spent a whole turn
filling. The measurements were on disk and were re-derived from scratch, or ignored.

The line goes in that block, not near the fail-closed rule at the end of the prompt, which is last
on purpose.
"""
from __future__ import annotations

from pathlib import Path

from .test_chat_turn import _orch

TID = "thr_f1"


def test_the_turn_is_told_the_findings_file_is_there_and_to_read_it_before_planning(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    root = orch.project(start_preview=False).record.path
    findings = root / ".sage" / "threads" / TID / "findings.md"
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text("- 2026-09-16T11:04Z — SFDC_CONTACT_ID populated 16,756/89,399 (18.7%)\n")

    prompt = orch._chat_prompt(TID, "so which accounts score highest?", {"items": []})

    assert f".sage/threads/{TID}/findings.md" in prompt
    assert "Read it before you plan this turn" in prompt
    assert "Append what you measure" in prompt
    # The block the model reads before it plans, not the fail-closed rule that is deliberately last.
    assert prompt.index("findings.md") < prompt.index("Read token:")
