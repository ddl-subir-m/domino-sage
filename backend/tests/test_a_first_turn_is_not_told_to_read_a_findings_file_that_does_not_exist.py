"""The findings line is conditional on the file, so a first turn pays nothing for it.

An unconditional "read `.sage/threads/<threadId>/findings.md` before you plan" would hand every
first turn in every Thread a path to a file that is not there. The Chat prompt already refuses to
hand over a dead path for the same reason Artifacts are filtered to the ones this clone has: a
path the model is told it may read costs a tool call and an answer written around a missing file.

The absent form is one sentence saying where the file goes when a question outgrows one turn. It
must not carry the read-it-first instruction, which is only true once something is in there.
"""
from __future__ import annotations

from pathlib import Path

from .test_chat_turn import _orch

TID = "thr_f2"


def test_a_thread_with_no_findings_file_is_told_where_it_goes_and_not_to_read_it(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    assert not (orch.project(start_preview=False).record.path
                / ".sage" / "threads" / TID / "findings.md").exists()

    prompt = orch._chat_prompt(TID, "how many accounts are there?", {"items": []})

    assert f".sage/threads/{TID}/findings.md" in prompt
    assert "more than one turn" in prompt
    assert "Read it before you plan this turn" not in prompt
    assert "last written" not in prompt
