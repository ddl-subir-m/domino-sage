"""What the history log costs to read (#49).

`.sage/history.jsonl` grows at ~68KB per user turn — a 100-turn project holds ~6.8MB across
~5,400 lines. Every reader used to pull the whole file and parse every line, including the two
that run on each turn and the one that answers a yes/no. These tests pin the reads that must not
go back to parsing the log, because nothing about the output would show the regression.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.workspace.manager import Workspace


@pytest.fixture()
def parses(monkeypatch) -> list[str]:
    """Every line handed to json.loads while the test body runs."""
    seen: list[str] = []
    real = json.loads

    def counted(s, *args, **kwargs):
        seen.append(s)
        return real(s, *args, **kwargs)

    monkeypatch.setattr(json, "loads", counted)
    return seen


def test_the_stop_button_baseline_counts_lines_without_parsing_them(tmp_path: Path, parses):
    """history_len() runs twice a turn and returns a line position. truncate_history() rewinds by
    line too, so neither side needs the contents of a line to agree with the other."""
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    for i in range(5):
        ws.append_history({"type": "user", "text": f"ask {i}"}, "thr_a")

    assert ws.history_len() == 5
    assert parses == []


def test_the_untagged_check_reads_the_tag_without_parsing_the_entry(tmp_path: Path, parses):
    """It answers yes/no, and it runs on every conversation switch. append_history() is the only
    writer of the tag, so the raw line says whether it is there."""
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.append_history({"type": "user", "text": "written before tagging"})
    for i in range(20):
        ws.append_history({"type": "user", "text": f"ask {i}"}, "thr_a")

    assert ws.has_untagged_history() is True
    assert parses == []


def test_the_untagged_check_says_no_once_every_entry_is_tagged(tmp_path: Path, parses):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    for i in range(20):
        ws.append_history({"type": "user", "text": f"ask {i}"}, "thr_a")

    assert ws.has_untagged_history() is False
    assert parses == []


def test_a_conversation_id_that_prefixes_another_reads_back_only_its_own(tmp_path: Path):
    """The replay pre-filter matches raw text, so "thr_a" must not match "thr_ab"."""
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.append_history({"type": "user", "text": "short id"}, "thr_a")
    ws.append_history({"type": "user", "text": "longer id"}, "thr_ab")

    assert [r["text"] for r in ws.read_history("thr_a")] == ["short id"]
    assert [r["text"] for r in ws.read_history("thr_ab")] == ["longer id"]


def test_an_entry_quoting_the_tag_cannot_forge_one(tmp_path: Path):
    """A user can type anything, including the tag. JSON escapes the quotes, so the raw text of a
    forged tag never matches the real one — the pre-filter cannot hand thr_b's turn to thr_a."""
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.append_history({"type": "user", "text": 'why is "conversation": "thr_a" in my log?'}, "thr_b")

    assert ws.read_history("thr_a") == []
    assert [r["text"] for r in ws.read_history("thr_b")] == ['why is "conversation": "thr_a" in my log?']


def test_the_untagged_check_still_reads_on_to_find_a_late_untagged_entry(tmp_path: Path):
    """Short-circuiting must not turn into "only look at the first row"."""
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.append_history({"type": "user", "text": "tagged"}, "thr_a")
    ws.append_history({"type": "user", "text": "untagged"})

    assert ws.has_untagged_history() is True


def test_the_archive_keeps_only_the_turns_it_writes(tmp_path: Path):
    """render_history_md() runs once a turn and writes at most _MAX_ARCHIVED_TURNS of them. The
    turns it will drop never need to be held, only counted."""
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    for i in range(Workspace._MAX_ARCHIVED_TURNS + 5):
        ws.append_history({"type": "user", "text": f"ask {i}"})
        ws.append_history({"type": "agent", "kind": "text", "text": f"reply {i}"})

    ws.render_history_md()
    md = ws.history_md_path.read_text()

    assert "_Turns 1–5 are older" in md
    assert "**User:** ask 4\n" not in md          # dropped
    assert "## Turn 6\n" in md                    # first kept turn keeps its absolute number
    assert "**User:** ask 5\n" in md
    assert f"**User:** ask {Workspace._MAX_ARCHIVED_TURNS + 4}\n" in md
