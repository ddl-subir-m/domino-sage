"""The investigation skill teaches the tools a turn actually has, and how to judge text (#605).

On the #600 transcript the model followed a skill that taught Python scripts and paging, spent its
turn on the catalogue, and never sent a line of text to a model. Each phrase below is a piece of
the method that turn was missing. Whitespace is collapsed first, so rewrapping the prose does not
red this; dropping the guidance does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2] / "template" / "skills" / "investigate-weak-signals" / "SKILL.md"


def _body() -> str:
    return " ".join(SKILL.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("phrase", [
    # Numbers and catalogue reads go through one SELECT, not a script.
    "`live_read_query`",
    # Row text is judged server-side from a filtered statement.
    "`live_read_table` with `operation=analyze_text` and `sql`",
    # Without it the judgments come back with no ids to join on.
    "Pass `id_column`",
    # Over budget the reply is counts per label; ids must not be made up.
    "comes back as `counts` per label with no ids",
    "do not invent ids",
    # Snowflake: '\b' is a backspace, and REGEXP_LIKE anchors the whole value.
    "`'\\\\b'` or `$$\\b$$`",
    "`REGEXP_LIKE` and `RLIKE` anchor the whole value",
    # '.' stops at a newline and matching is case-sensitive, so a word-in-text match needs both.
    "`.` does not cross newlines and matching is case-sensitive",
    "`REGEXP_COUNT(t, '\\\\bword\\\\b', 1, 'i') > 0`",
    "`REGEXP_LIKE(t, '.*\\\\bword\\\\b.*', 'is')`",
    # What a turn stopped at its ceiling keeps is best-effort.
    "Append after every measurement that changes the plan",
])
def test_the_skill_carries_the_method(phrase):
    assert phrase in _body()


def test_a_named_account_stays_in_the_reply_and_out_of_findings():
    """The fusion example named an account, and that shape was copied into findings.md.

    Aggregates stay the findings rule. An account name is allowed in the reply.
    """
    body = _body()
    assert "Acme — score" not in body
    assert "including an account's name, belongs in the reply" in body
    assert "The findings file stays aggregates and column facts" in body


def test_every_word_match_the_skill_shows_is_case_insensitive():
    """A case-sensitive example is copied as written, and misses 'arm' in a note about ARM."""
    calls = re.findall(r"REGEXP_(?:COUNT|INSTR)\([^)]*\)", _body())
    assert calls
    assert [c for c in calls if not c.endswith("'i')")] == []
