"""A Chat turn is told today's date, and that relative periods count from it (#726).

Measured on haiku, demo prompt 1 ("Chart open pipeline by stage and team for this quarter and
next"): nothing in the turn said what day it was, so "this quarter" was read off the data — the
query took MAX(FISCAL_YEAR) in the table, 2031, and the chart was labelled FY2028. A model with no
date has only the data to anchor on, and the data's latest row is not today.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from sage.orchestrator import service

from .test_chat_turn import _orch

TID = "thr_today"


def _prompt(tmp_path: Path, monkeypatch, today: date, question: str) -> str:
    monkeypatch.setattr(service, "_chat_today", lambda: today)
    orch, _ = _orch(tmp_path)
    return orch._chat_prompt(TID, question, {"items": []})


def test_this_quarter_is_anchored_on_today_not_on_the_datas_latest_year(tmp_path, monkeypatch):
    prompt = _prompt(tmp_path, monkeypatch, date(2026, 10, 8),
                     "Chart open pipeline by stage and team for this quarter and next.")

    assert "Today is 2026-10-08" in prompt
    assert "this quarter" in prompt.lower()
    assert "not from the latest date in the data" in prompt
    # Before the question, which stays the prompt's last line.
    assert prompt.index("Today is 2026-10-08") < prompt.index("for this quarter and next")
    assert prompt.rstrip().endswith("for this quarter and next.")


@pytest.mark.parametrize("today, said", [
    (date(2027, 1, 2), "Today is 2027-01-02"),
    (date(2026, 3, 31), "Today is 2026-03-31"),
])
def test_last_n_days_moves_with_the_clock(tmp_path, monkeypatch, today, said):
    """The sibling shape: a trailing window, not a quarter, on a day the first test never saw. The
    date is read per turn, so a constant written into the prompt would fail here."""
    prompt = _prompt(tmp_path, monkeypatch, today,
                     "Which competitors came up most on Gong calls in the last 30 days?")

    assert said in prompt
    assert "last N days" in prompt
