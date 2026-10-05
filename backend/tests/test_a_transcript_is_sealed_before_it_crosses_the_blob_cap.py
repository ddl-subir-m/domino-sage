"""A transcript stays in git, and no single segment grows into GitHub's blob cap.

GitHub rejects a push once a blob crosses 100 MB. The check is on the whole file. Sealing
the live `history.jsonl` when the next line would pass the cap keeps every later save
pushable. The sealed segments stay on disk and in git, and a read walks them in order.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.workspace import history_log
from sage.workspace.manager import Workspace
from sage.workspace.threads import ThreadStore


@pytest.fixture
def tiny_cap(monkeypatch):
    """Small enough that the second line seals the first. The real cap is 40 MiB."""
    monkeypatch.setattr(history_log, "BLOB_CAP", 1)


def test_a_short_log_stays_one_file(tmp_path: Path):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.append_history({"type": "user", "text": "one"}, "thr_a")
    ws.append_history({"type": "user", "text": "two"}, "thr_a")

    assert not (tmp_path / ".sage" / "history.d").exists()
    assert [row["text"] for row in ws.read_history("thr_a")] == ["one", "two"]


def test_the_next_line_seals_the_live_file_and_a_read_walks_both(tmp_path: Path, tiny_cap):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.append_history({"type": "user", "text": "one", "detail": "the first call"}, "thr_a")
    ws.append_history({"type": "user", "text": "two"}, "thr_a")

    sealed = tmp_path / ".sage" / "history.d" / "00001.jsonl"
    live = tmp_path / ".sage" / "history.jsonl"
    assert sealed.is_file()
    assert "one" in sealed.read_text()
    assert "two" not in sealed.read_text()
    assert "two" in live.read_text()
    assert "one" not in live.read_text()
    assert [row["text"] for row in ws.read_history("thr_a")] == ["one", "two"]
    assert ws.history_len() == 2
    # The row number is a position in the whole transcript, including the sealed segment.
    assert ws.history_row_detail(0) == "the first call"


def test_a_stop_drops_only_the_turn_that_crossed_the_seal(tmp_path: Path, tiny_cap):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.append_history({"type": "user", "text": "kept"}, "thr_a")
    baseline = ws.history_len()
    ws.append_history({"type": "user", "text": "in progress"}, "thr_a")
    ws.append_history({"type": "agent", "text": "partial"}, "thr_a")

    ws.truncate_history(baseline)

    assert [row["text"] for row in ws.read_history()] == ["kept"]
    assert ws.history_len() == 1


def test_an_untagged_line_in_a_sealed_segment_is_still_adopted_in_place(tmp_path: Path, tiny_cap):
    ws = Workspace(project_id="p", path=tmp_path, app_id="app_t")
    ws.append_history({"type": "user", "text": "before tagging"})
    ws.append_history({"type": "user", "text": "after"}, "thr_a")
    sealed = tmp_path / ".sage" / "history.d" / "00001.jsonl"
    live = tmp_path / ".sage" / "history.jsonl"

    assert ws.has_untagged_history() is True
    ws.adopt_history("thr_a")

    assert sealed.is_file() and live.is_file()
    assert "before tagging" in sealed.read_text()
    assert "after" not in sealed.read_text()
    assert "thr_a" in sealed.read_text()
    assert ws.has_untagged_history() is False
    assert [row["text"] for row in ws.read_history("thr_a")] == ["before tagging", "after"]


def test_a_chat_thread_seals_the_same_way_and_replays_in_order(tmp_path: Path, tiny_cap):
    store = ThreadStore(tmp_path)
    store.append_history("thr_a", {"type": "user", "text": "one"})
    store.append_history("thr_a", {"type": "user", "text": "two"})

    directory = tmp_path / ".sage" / "threads" / "thr_a"
    assert (directory / "history.d" / "00001.jsonl").is_file()
    assert [row["text"] for row in store.read_history("thr_a")] == ["one", "two"]
