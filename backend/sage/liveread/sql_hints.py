"""A sentence after a Snowflake regex that probably does not ask what its author meant (#603).

Two traps, both from Snowflake's own docs and both able to turn a real match into a 0 (#600's
`ARM_WORD_CASES = 0`). In a single-quoted literal a backslash is a string escape, so `'\\b'` reaches
the regex engine as a backspace and `'\\d'` as a bare `d`. And `REGEXP_LIKE` / `RLIKE` / `REGEXP`
anchor both ends, so `'\\\\barm\\\\b'` matches only a cell that is exactly `arm`. A third follows
from the second: `.` stops at a newline unless the parameters carry `s`, so `'.*\\\\barm\\\\b.*'`
fails on every multi-line value.

A hint and never a gate: the statement has already run by the time this is asked, and anything
that goes wrong here — an import, a parse, a token — is no sentence rather than an error.
"""

from __future__ import annotations

import re

# Model-facing. Kept identical in both doors' `live_read_query` description (`mcp.py`,
# `tools/live_read.ts`).
# REGEXP_COUNT leads because it does not anchor, so a newline in the text cannot defeat it. Any
# REGEXP_LIKE form must pass 's' (`.` does not match a newline by default) and 'i' (matching is
# case-sensitive by default): without them `'.*\\barm\\b.*'` is FALSE on every multi-line case or
# transcript, and misses 'ARM'.
DESCRIPTION = (
    r"On Snowflake, find a word inside text with REGEXP_COUNT(col, '\\bword\\b', 1, 'i') > 0; "
    r"REGEXP_LIKE and RLIKE match the whole value, so they need REGEXP_LIKE(col, "
    r"'.*\\bword\\b.*', 'is'), and a regex backslash is written twice inside single quotes: "
    r"'\\b', not '\b'."
)
ESCAPE_NOTE = (
    r"Snowflake note: inside single quotes a backslash is a string escape, so '\b' reaches the "
    r"regex as a backspace and '\d' as a plain 'd'; write '\\b' and '\\d', or put the pattern in "
    r"$$...$$."
)
ANCHOR_NOTE = (
    r"Snowflake note: REGEXP_LIKE, RLIKE and REGEXP match the whole value, so '\\barm\\b' matches "
    r"only a cell that is exactly 'arm'; to find a word inside text use "
    r"REGEXP_COUNT(col, '\\barm\\b', 1, 'i') > 0, or REGEXP_LIKE(col, '.*\\barm\\b.*', 'is')."
)
NEWLINE_NOTE = (
    r"Snowflake note: '.' stops at a newline unless the parameters include 's', so a whole-value "
    r"match like '.*\\barm\\b.*' fails on multi-line text; use "
    r"REGEXP_COUNT(col, '\\barm\\b', 1, 'i') > 0, or pass 'is'."
)

# A backslash then a letter, after an even run of backslashes, in the RAW literal. Read off the
# token and not off the parsed value, because `sqlglot`'s Snowflake dialect parses `'\d'` and
# `'\\d'` to the same `\d` — only `'\b'`, which it turns into a real backspace, would show there.
_SINGLE_ESCAPED = re.compile(r"(?<!\\)(?:\\\\)*\\[A-Za-z]")
# A regex `.` in the parsed pattern, not an escaped `\.`, which is a literal dot and never spans.
_UNESCAPED_DOT = re.compile(r"(?<!\\)(?:\\\\)*\.")


def regex_hint(sql: str, connector_type: str) -> str:
    """The notes this statement's regex patterns earn, or `""`. Snowflake only."""
    if connector_type != "SnowflakeConfig":
        return ""
    try:
        return _hint(sql)
    # Broad for the reason `run.catalogue_read` is broad: `sqlglot` raises more than `ParseError`
    # on malformed input, and a hint that raised would fail a statement that already ran.
    except Exception:
        return ""


def _hint(sql: str) -> str:
    import sqlglot
    from sqlglot import expressions as exp
    from sqlglot.tokens import TokenType

    raw: dict[str, list[str]] = {}
    for token in sqlglot.Dialect.get_or_raise("snowflake").tokenize(sql):
        if token.token_type == TokenType.STRING:
            raw.setdefault(token.text, []).append(sql[token.start:token.end + 1])

    escaped = whole_value = newline = False
    for statement in sqlglot.parse(sql, dialect="snowflake"):
        if statement is None:
            continue
        for call in statement.find_all(exp.RegexpLike):
            pattern = call.expression
            if isinstance(pattern, exp.Literal) and pattern.is_string:
                escaped = escaped or any(_SINGLE_ESCAPED.search(r) for r in raw.get(pattern.this, ()))
            elif not isinstance(pattern, exp.RawString):
                continue
            value = str(pattern.this)
            if not (value.startswith((".*", "^")) or value.endswith((".*", "$"))):
                whole_value = True
            # One note per call: a call the anchor note already covers gets no second one.
            elif _UNESCAPED_DOT.search(value) and _without_s(call.args.get("flag"), exp):
                newline = True
    return " ".join(note for note, fired in ((ESCAPE_NOTE, escaped), (ANCHOR_NOTE, whole_value),
                                             (NEWLINE_NOTE, newline)) if fired)


def _without_s(flag, exp) -> bool:
    """No parameters argument, or a literal one without `s`. A parameter this cannot read — a
    column, an expression — is not flagged, because a hint that guesses is noise."""
    if flag is None:
        return True
    return isinstance(flag, exp.Literal) and flag.is_string and "s" not in str(flag.this)
