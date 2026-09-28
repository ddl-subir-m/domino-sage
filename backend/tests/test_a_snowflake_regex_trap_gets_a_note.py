"""#603 — two Snowflake regex traps get a sentence after the result, never a refusal.

Both confirmed in Snowflake's docs and both able to produce #600's `ARM_WORD_CASES = 0` on their
own: in a single-quoted literal `'\\b'` is a backspace (a word boundary is `'\\\\b'` or `$$\\b$$`),
and `REGEXP_LIKE` / `RLIKE` / `REGEXP` anchor both ends, so `'\\\\barm\\\\b'` matches only a cell that
is exactly `arm`.

Written against `sqlglot` as measured in this venv with `dialect="snowflake"`: `'\\b'` parses to a
literal holding a real backspace, while `'\\d'` and `'\\\\d'` BOTH parse to backslash-d. So the
single-escape check has to read the raw token, not the literal's value.
"""

from __future__ import annotations

import re

import pytest

from sage.liveread import mcp, run, sql_hints
from sage.resources.provider import DataSource

from .test_a_chat_turn_works_a_number_out_in_sql import FakeAnswer, turn_for
from .test_a_tool_description_never_says_where_a_feature_is_rolled_out import TOOLS_TS

SF = "SnowflakeConfig"
WHERE = "SELECT COUNT(*) AS N FROM CASES WHERE "


@pytest.mark.parametrize("predicate", [
    r"REGEXP_LIKE(BODY, '.*\barm\b.*')",
    r"REGEXP_LIKE(BODY, '.*\darm.*')",
    r"BODY RLIKE '.*\barm\b.*'",
])
def test_a_single_escaped_pattern_gets_the_escape_note(predicate):
    note = sql_hints.regex_hint(WHERE + predicate, SF)
    assert sql_hints.ESCAPE_NOTE in note
    assert sql_hints.ANCHOR_NOTE not in note


@pytest.mark.parametrize("predicate", [
    r"REGEXP_LIKE(BODY, '\\barm\\b')",
    r"BODY RLIKE 'arm'",
    r"BODY REGEXP '\\barm\\b'",
    r"REGEXP_LIKE(BODY, $$\barm\b$$)",
])
def test_an_unanchored_pattern_gets_the_whole_value_note(predicate):
    note = sql_hints.regex_hint(WHERE + predicate, SF)
    assert sql_hints.ANCHOR_NOTE in note
    assert sql_hints.ESCAPE_NOTE not in note


def test_both_traps_at_once_get_both_notes():
    note = sql_hints.regex_hint(WHERE + r"REGEXP_LIKE(BODY, '\barm\b')", SF)
    assert sql_hints.ESCAPE_NOTE in note and sql_hints.ANCHOR_NOTE in note


@pytest.mark.parametrize("predicate", [
    r"REGEXP_LIKE(BODY, '.*\\barm\\b.*')",
    r"REGEXP_LIKE(BODY, '.*\\barm\\b.*', 'i')",
    r"BODY RLIKE '.*\\barm\\b.*'",
    r"BODY REGEXP '^.*arm'",
])
def test_a_dot_without_s_gets_the_newline_note(predicate):
    assert sql_hints.regex_hint(WHERE + predicate, SF) == sql_hints.NEWLINE_NOTE


@pytest.mark.parametrize("predicate", [
    r"REGEXP_LIKE(BODY, 'a.m')",
    r"REGEXP_LIKE(BODY, '\\barm\\b')",
])
def test_a_call_the_anchor_note_covers_gets_no_second_note(predicate):
    assert sql_hints.regex_hint(WHERE + predicate, SF) == sql_hints.ANCHOR_NOTE


@pytest.mark.parametrize("sql, connector", [
    (WHERE + r"REGEXP_LIKE(BODY, '.*\\barm\\b.*', 'is')", SF),
    (WHERE + r"REGEXP_LIKE(BODY, '.*\\barm\\b.*', 's')", SF),
    (WHERE + r"REGEXP_LIKE(BODY, '.*\\barm\\b.*', FLAGS)", SF),
    (WHERE + r"REGEXP_LIKE(BODY, '^v1\\.2$')", SF),
    (r"SELECT COUNT(*) AS N FROM CASES WHERE REGEXP_COUNT(BODY, '.*\\barm\\b.*') > 0", SF),
    (r"SELECT REGEXP_SUBSTR(BODY, '.*arm.*') AS S FROM CASES", SF),
    (WHERE + r"REGEXP_LIKE(BODY, '.*\\barm\\b.*')", "PostgreSQLConfig"),
    (WHERE + r"REGEXP_LIKE(BODY, '^ARM$')", SF),
    (r"SELECT COUNT(*) AS N FROM CASES WHERE REGEXP_COUNT(BODY, '\\barm\\b') > 0", SF),
    (r"SELECT COUNT(*) AS N FROM CASES WHERE REGEXP_INSTR(BODY, 'arm') > 0", SF),
    (WHERE + r"NOTE = '\b' AND REGEXP_LIKE(BODY, '.*arm.*', 'is')", SF),
    (WHERE + r"REGEXP_LIKE(BODY, '\barm\b')", "PostgreSQLConfig"),
    (WHERE + r"REGEXP_LIKE(BODY, '\barm\b')", ""),
    ("SELECT FROM WHERE REGEXP_LIKE((( '\\b", SF),
    ("", SF),
])
def test_no_note_where_nothing_is_trapped_or_nothing_parses(sql, connector):
    assert sql_hints.regex_hint(sql, connector) == ""


def test_the_note_follows_the_result_and_the_statement_still_runs(tmp_path):
    ran = []

    def query(source, sql, *, limit):
        ran.append(sql)
        return FakeAnswer(["N"], [[0]])

    turn, _ = turn_for(tmp_path, run_statement=query, source_for=lambda _: DataSource(
        "dwh", "DWH", SF, "Shared", connector_type=SF))
    sql = WHERE + r"REGEXP_LIKE(BODY, '\\barm\\b')"
    said = run.perform("live_read_query", {"source": "DWH", "sql": sql}, turn)

    assert ran == [sql]
    assert said.index("Result: [[0]]") < said.index(sql_hints.ANCHOR_NOTE)


@pytest.mark.parametrize("taught", [sql_hints.DESCRIPTION, sql_hints.ANCHOR_NOTE])
def test_every_recommended_form_survives_newlines_and_case(taught):
    """`.` does not match a newline unless 's' is passed, and matching is case-sensitive unless
    'i' is. A REGEXP_LIKE taught without 'is' is FALSE on every multi-line case description and
    call transcript, and misses 'ARM' — the text this guidance exists for."""
    counts = re.findall(r"REGEXP_COUNT\(col, '[^']*'([^)]*)\) > 0", taught)
    likes = re.findall(r"REGEXP_LIKE\(col, '[^']*'([^)]*)\)", taught)
    assert counts, "REGEXP_COUNT leads: it does not anchor, so newlines cannot defeat it"
    assert taught.index("REGEXP_COUNT(col") < taught.index("REGEXP_LIKE(col")
    assert all(params == ", 1, 'i'" for params in counts), counts
    assert likes and all(params == ", 'is'" for params in likes), likes


def test_the_newline_note_points_at_the_forms_that_survive_it():
    note = sql_hints.NEWLINE_NOTE
    assert re.search(r"REGEXP_COUNT\(col, '[^']*', 1, 'i'\) > 0", note), note
    assert "pass 'is'" in note


def test_both_doors_teach_the_two_facts_in_the_same_sentence():
    query = next(t for t in mcp.TOOLS if t["name"] == "live_read_query")
    assert sql_hints.DESCRIPTION in query["description"]
    ts_literal = sql_hints.DESCRIPTION.replace("\\", "\\\\")
    assert ts_literal in "".join(
        part for part in TOOLS_TS.read_text().split('" +\n    "')), "the TypeScript door"
