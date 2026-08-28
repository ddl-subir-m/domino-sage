"""Stored turns record when they happened (#51).

A conversation's transcript is two files: the Thread store's chat log and the Built App's build
log — one per app since #68, and a conversation can drive several of them (#72). Merging them for
display needs something to order by, and neither writer stamped its entries.

Both writers stamp at the point of writing, in the format the Thread store already uses, so the
halves read alike and turns from different app logs compare against each other. Nothing a person
can see changes yet; these tests hold the prefactor in place.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sage.workspace import manager, threads
from sage.workspace.manager import Workspace
from sage.workspace.threads import ThreadStore

# The Thread store's stamp: UTC, second resolution, sorts as a string.
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _ws(tmp: Path, app_id: str) -> Workspace:
    return Workspace(project_id="p", path=tmp / "apps" / app_id, app_id=app_id)


@pytest.fixture
def clock(monkeypatch):
    """One clock for both writers, ticking a second per entry.

    The real stamp has second resolution, so two turns written by a test land on the same string
    and every ordering assertion below would hold whatever the writers did — a constant would pass
    them. Ticking makes the order the thing under test. Shared between the two modules on purpose:
    a separate sequence each would prove less than the writers already claim."""
    ticks = iter(f"2026-08-28T09:00:{second:02d}Z" for second in range(60))
    monkeypatch.setattr(threads, "_now", lambda: next(ticks))
    monkeypatch.setattr(manager, "_now", lambda: next(ticks))


def test_a_chat_turn_carries_a_stamp(tmp_path: Path):
    store = ThreadStore(tmp_path)
    thread = store.create(title="what's in this CSV?")

    store.append_history(thread["id"], {"type": "user", "text": "what's in this CSV?"})

    (row,) = store.read_history(thread["id"])
    assert _STAMP.match(row["at"])


def test_a_build_turn_carries_a_stamp(tmp_path: Path):
    ws = _ws(tmp_path, "app_a")

    ws.append_history({"type": "user", "text": "add a filter"}, "thr_a")

    (row,) = ws.read_history("thr_a")
    assert _STAMP.match(row["at"])


def test_the_stamp_is_the_one_the_thread_store_already_writes(tmp_path: Path):
    """Not a new format: the same string the Thread store puts on a Thread. The merged transcript
    orders one half against the other, so a second format would be a second answer. Runs on the
    real clock — a test that injected one could not tell you what the writers actually produce."""
    store = ThreadStore(tmp_path)
    thread = store.create(title="desk exposure")
    assert _STAMP.match(thread["createdAt"])  # the format this test is pinning is the store's own

    store.append_history(thread["id"], {"type": "user", "text": "show notional by desk"})
    _ws(tmp_path, "app_a").append_history({"type": "user", "text": "build it"}, thread["id"])

    assert _STAMP.match(store.read_history(thread["id"])[0]["at"])
    assert _STAMP.match(_ws(tmp_path, "app_a").read_history(thread["id"])[0]["at"])


def test_the_two_halves_order_against_each_other(clock, tmp_path: Path):
    """The defect this prefactor exists for: a conversation's Chat and Build turns live in
    different files, so the transcript can only interleave them on the stamp."""
    store = ThreadStore(tmp_path)
    thread = store.create(title="desk exposure")
    ws = _ws(tmp_path, "app_a")

    store.append_history(thread["id"], {"type": "user", "text": "show notional by desk"})
    ws.append_history({"type": "agent", "text": "built it"}, thread["id"])
    store.append_history(thread["id"], {"type": "user", "text": "and by currency?"})

    # Concatenated Build-first, so file order is the wrong answer and only the stamp gives the right one.
    rows = ws.read_history(thread["id"]) + store.read_history(thread["id"])
    assert [r["text"] for r in sorted(rows, key=lambda r: r["at"])] == [
        "show notional by desk", "built it", "and by currency?"]


def test_build_turns_from_different_apps_compare_against_each_other(clock, tmp_path: Path):
    """One conversation, two Built Apps, two logs. The stamp is what puts their turns in one
    order — the file each came from cannot."""
    first = _ws(tmp_path, "app_a")
    second = _ws(tmp_path, "app_b")

    first.append_history({"type": "user", "text": "show notional by desk"}, "thr_a")
    second.append_history({"type": "user", "text": "show daily P&L"}, "thr_a")
    first.append_history({"type": "user", "text": "add a date filter"}, "thr_a")

    rows = second.read_history("thr_a") + first.read_history("thr_a")
    assert [r["text"] for r in sorted(rows, key=lambda r: r["at"])] == [
        "show notional by desk", "show daily P&L", "add a date filter"]


def test_entries_written_before_the_stamp_are_still_readable(tmp_path: Path):
    """No migration (#50): a conversation that already exists keeps its unstamped entries, and both
    readers hand them back rather than failing on the missing key."""
    store = ThreadStore(tmp_path)
    thread = store.create(title="older work")
    chat_row = {"type": "user", "text": "asked before"}
    build_row = {"type": "user", "text": "built before", "conversation": thread["id"],
                 "app": "app_a"}
    store.history_path(thread["id"]).parent.mkdir(parents=True, exist_ok=True)
    store.history_path(thread["id"]).write_text(json.dumps(chat_row) + "\n")

    ws = _ws(tmp_path, "app_a")
    ws.history_path.parent.mkdir(parents=True, exist_ok=True)
    ws.history_path.write_text(json.dumps(build_row) + "\n")

    assert store.read_history(thread["id"]) == [chat_row]
    assert ws.read_history(thread["id"]) == [build_row]
