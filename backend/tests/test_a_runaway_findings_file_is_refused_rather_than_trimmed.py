"""`findings.md` is committed and model-authored, so it needs a ceiling — and the ceiling refuses.

ADR-0045's rule is that what a Chat turn commits is aggregates, never rows, and this file is the
one place the rule is enforced by a size rather than by shape: a `findings.md` growing past 32 KB
is no longer a page of measurements. Either the model is pasting rows into it, or it is appending
the same schema facts every turn, and both are worth seeing.

Trimming would hide exactly that. A file cut at 32 KB looks like a healthy file, reads as complete
to the next turn, and silently drops whichever half the trim did not keep — the "half-recorded"
failure, which is worse than not recording, because the next turn plans against it. So the turn's
growth is put back and the turn says so.

What was in the file before this turn is untouched: an earlier turn's measurements were decided
under whatever ceiling held then, and re-deciding them now would rewrite notes nobody reopened.

The second test is the boundary of the same rule, found in review. What is refused is growth past
the ceiling, not size — an oversize file can already be on disk, because #380 made the file
writable before this ceiling shipped and a turn that dies never reaches the turn end that enforces
it. Reverting a turn that COMPACTED one would pin the file at its high-water mark and refuse every
repair, which is the one state a 32 KB ceiling must not create.
"""
from __future__ import annotations

from pathlib import Path

from sage.workspace.threads import ThreadStore

from .fake_opencode import Turn
from .test_chat_turn import _orch


def test_a_forty_kb_findings_file_is_put_back_not_cut_to_the_ceiling(tmp_path: Path):
    kept = "- 2026-09-16T11:04Z — SFDC_CONTACT_ID populated 16,756/89,399 (18.7%).\n"
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    rel = f".sage/threads/{tid}/findings.md"
    findings = project.record.path / rel
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text(kept)
    oc.turns.append(Turn(text="Noted.", writes={rel: kept + "x" * 40_000}))

    events = list(orch.chat_stream(tid, "keep measuring"))

    assert findings.read_text() == kept, "the turn's growth was kept or trimmed, not refused"
    # By reason, not by position: this turn end appends a table-generation error ahead of this one,
    # so `[0]` would read the wrong event on a turn that also failed a table.
    refusals = [e for e in events if e.get("reason") == "findings file too large"]
    assert refusals, "a refused findings file left no trace on the Thread"
    # Persisted, not only streamed. A refusal a reload cannot show is the silent turn again.
    history = ThreadStore(project.record.path).read_history(tid)
    assert [e for e in history if e.get("reason") == "findings file too large"]


def test_a_turn_that_compacts_an_already_oversize_file_is_not_refused(tmp_path: Path):
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    rel = f".sage/threads/{tid}/findings.md"
    findings = project.record.path / rel
    findings.parent.mkdir(parents=True, exist_ok=True)
    findings.write_text("x" * 40_000)
    repaired = "- 2026-09-16T11:04Z — SFDC_CONTACT_ID populated 16,756/89,399 (18.7%).\n" * 500
    assert len(repaired) > 32 * 1024, "the repair must still be over the ceiling to test the rule"
    oc.turns.append(Turn(text="Tidied.", writes={rel: repaired}))

    events = list(orch.chat_stream(tid, "trim those notes down"))

    assert findings.read_text() == repaired, "a repair was reverted to the larger file"
    assert not [e for e in events if e.get("reason") == "findings file too large"]


def test_a_first_turn_that_creates_a_runaway_leaves_no_half_file_behind(tmp_path: Path):
    """The branch that removes rather than restores, which is the one a mistake cannot be undone in.

    With nothing on disk before the turn there is no earlier version to put back, so refusing means
    the file goes. Left untested it would be the only path here that destroys, and the assertion
    that it removes the WHOLE file is what says it did not stop half way.
    """
    orch, oc = _orch(tmp_path)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    rel = f".sage/threads/{tid}/findings.md"
    findings = project.record.path / rel
    assert not findings.exists()
    oc.turns.append(Turn(text="Noted.", writes={rel: "y" * 40_000}))

    events = list(orch.chat_stream(tid, "measure everything"))

    assert not findings.exists(), "a refused first findings file was left on disk"
    assert [e for e in events if e.get("reason") == "findings file too large"]
