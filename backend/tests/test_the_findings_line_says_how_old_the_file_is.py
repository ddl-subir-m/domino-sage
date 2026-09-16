"""The line hands over the file's mtime and byte size, so the model can judge staleness unread.

Without them the only way to find out whether the notes are from this Conversation or from a
Conversation someone left a week ago is to read the file — a tool call and a chunk of context
spent before the turn has planned anything, on a file that is often irrelevant. Workspaces are
cloned and `findings.md` is committed, so a fresh container can open a Thread whose findings are
older than the question.

The size is the other half of the same lever: 400 bytes is a turn's notes and 30 KB is an
investigation, and the model plans differently for each.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from .test_chat_turn import _orch

TID = "thr_f3"


def test_the_line_carries_the_files_mtime_and_its_byte_size(tmp_path: Path):
    orch, _ = _orch(tmp_path)
    root = orch.project(start_preview=False).record.path
    findings = root / ".sage" / "threads" / TID / "findings.md"
    findings.parent.mkdir(parents=True, exist_ok=True)
    body = "- 2026-09-16T11:12Z — `Monitoring tab visited`, 90d: 291 events, 89 distinct users.\n"
    findings.write_text(body)
    stale = datetime(2026, 9, 9, 11, 2, tzinfo=UTC).timestamp()
    os.utime(findings, (stale, stale))

    prompt = orch._chat_prompt(TID, "and how many of those are customers?", {"items": []})

    assert f"{len(body.encode())} bytes" in prompt
    assert "2026-09-09T11:02" in prompt
