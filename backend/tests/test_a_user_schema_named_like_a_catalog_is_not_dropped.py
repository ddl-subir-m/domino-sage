r"""`_` is a LIKE wildcard, so `pg_%` never meant "starts with pg_" (#192).

Both Postgres statements drop the server's own catalogs with one pattern: the cascade's `schemas`
(#11) and the database-wide walk's `database_tables` (#187). Written as `pg_%` the pattern reads
"pg, then any single character, then anything", so a real user schema named `pgbouncer` or
`pganalytics` is dropped from the panel AND from the search — the panel does not offer it, and a
search over it reports "no name matched" for tables that exist.

Both are fixed or neither is. A schema the search offers but the panel does not show is a worse
state than one neither shows, which is why #187 copied the bug forward rather than fixing one side.

No live Postgres is reachable here and none is needed, but asserting the literal string alone would
only pin today's spelling. So these tests spell out what LIKE means and run the pattern against the
names, which is the thing the bug is actually about.

The escape is `ESCAPE '#'` rather than a backslash, and that is the whole reason these tests read
the escape out of the statement instead of assuming one. This entry is shared by Redshift and
Greenplum, and Redshift's string-literal parser consumes a backslash before LIKE sees it — so
`'pg\_%'` would fix PostgreSQL and quietly leave the other two broken, which is the failure the
identity assertion at the bottom would otherwise have called a pass.
"""
from __future__ import annotations

import re

from sage.resources.provider import SQL_DIALECTS

_POSTGRES = SQL_DIALECTS["PostgreSQLConfig"]
# Every user schema that reads like a catalog and is not one. `pgbouncer` is the common case — the
# connection pooler's own stats schema is a thing people query — and the rest are ordinary names.
_MISTAKEN_FOR_CATALOGS = ("pgbouncer", "pganalytics", "pgloader_staging", "pga")
# What the pattern is actually for. `pg_temp_1` and `pg_toast_temp_1` are per-session and appear on
# a busy server, so the filter has to be a prefix match rather than a list of two names.
_REAL_CATALOGS = ("pg_catalog", "pg_toast", "pg_temp_1", "pg_toast_temp_1")


def _patterns() -> list[tuple[str, str, str]]:
    """Every `NOT LIKE` filter in the two Postgres statements, as (where it came from, pattern,
    escape character).

    Every one of them, not the first: a statement that later grows a second filter would otherwise
    keep passing these tests while the new filter went unread.
    """
    found = []
    for level in ("schemas", "database_tables"):
        statement = getattr(_POSTGRES, level)
        matches = re.findall(r"NOT LIKE '([^']*)'(?:\s+ESCAPE '([^']*)')?", statement)
        assert matches, f"{level} no longer filters with NOT LIKE: {statement}"
        found += [(level, pattern, escape) for pattern, escape in matches]
    return found


def _like(pattern: str, value: str, escape: str) -> bool:
    """SQL LIKE, enough of it to read these two statements.

    `%` is any run of characters, `_` is any ONE character, and the statement's own escape character
    turns either into itself. Spelled out rather than asserted as a string because the whole of #192
    is the difference between `_` as a wildcard and `_` as a character, and comparing strings cannot
    tell those apart — it can only pin the spelling that happens to be there today.
    """
    out, i = "", 0
    while i < len(pattern):
        char = pattern[i]
        if escape and char == escape and i + 1 < len(pattern):
            out += re.escape(pattern[i + 1])
            i += 2
            continue
        out += {"%": ".*", "_": "."}.get(char, re.escape(char))
        i += 1
    return re.fullmatch(out, value) is not None


def test_the_underscore_is_a_character_in_the_filter_and_not_a_wildcard():
    for level, pattern, escape in _patterns():
        for name in _MISTAKEN_FOR_CATALOGS:
            assert not _like(pattern, name, escape), \
                f"{level} drops {name}, which is somebody's schema"


def test_the_servers_own_catalogs_are_still_dropped():
    # The other half, and the one that makes the fix a fix rather than a deletion.
    for level, pattern, escape in _patterns():
        for name in _REAL_CATALOGS:
            assert _like(pattern, name, escape), \
                f"{level} now offers {name}, which is the server's own"


def test_the_cascade_and_the_search_agree_about_what_a_catalog_is():
    # The reason #187 copied this forward instead of fixing one side: a schema the search offers and
    # the panel does not show is a worse state than one neither shows.
    assert len({(pattern, escape) for _, pattern, escape in _patterns()}) == 1


def test_the_filter_does_not_depend_on_how_a_store_reads_a_backslash():
    # The identity assertion below proves the STRING is shared. It does not prove the string means
    # the same thing at the far end, and for a backslash it would not: PostgreSQL leaves one alone,
    # Redshift's literal parser eats it, so `'pg\_%'` reaches Redshift as `pg_%` and `pgbouncer` is
    # dropped again on two of the three connectors this entry serves. An explicit `ESCAPE` is read
    # by LIKE itself, after any literal parsing, so all three agree.
    for level, pattern, escape in _patterns():
        assert "\\" not in pattern, f"{level} escapes with a backslash, which Redshift consumes"
        assert escape, f"{level} names no ESCAPE, so it relies on a per-store default"


def test_redshift_and_greenplum_inherit_the_fix_rather_than_a_copy_of_it():
    # Aliases, not repeats — so a correction to Postgres reaches every connector it was a correction
    # for, which is what the note under SQL_DIALECTS says these entries are for.
    assert SQL_DIALECTS["RedshiftConfig"] is _POSTGRES
    assert SQL_DIALECTS["GreenplumConfig"] is _POSTGRES
