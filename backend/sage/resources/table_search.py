"""Which table a request is about, matched by name before the assistant is asked (#183).

Sage finds; the person binds. A creator who added a Data Source and went no further used to get a
refusal — `bound_schema`'s unscoped section told the assistant it could not query and could not
choose, and the assistant obeyed. Reading a store's own catalog and offering what it holds is a
different act from inferring a record: nothing here writes anything, and the record is written by
the click that answers the card (ADR-0038).

Pure functions over names, so this module is testable apart from I/O and apart from a store. The
walk that produces the tables lives in `provider.list_database_tables`, and the sentence a person
reads is written where the offer is assembled — this file holds no copy, which is why it owes no
entry in `brand_coverage.toml`.

RANKING IS NAME MATCHING ONLY, and that is known to be insufficient rather than assumed to be
enough: "gong" matches 27 of the live warehouse's 602 tables, and `MARTS.GONG__CALLS` — the modeled
table a daily summary wants — scores identically to `STAGING.STG_GONG__CALLS`, which it does not.
The model ranker that can tell those apart is its own ticket. Until it lands, the ordering is a
convenience and the card is the answer: the person reads both names and picks, which is the act
that was always going to write the record.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .bindings import KIND_DATA_SOURCE, Binding

# How many candidates the card offers before "show all". Five is what fits above the fold beside a
# request the person can still read; every table stays reachable behind the second list, because a
# bad ranking has to be a delay and never a dead end.
SHORTLIST = 5

_SPLIT = re.compile(r"[^a-z0-9]+")

# Words a request is built out of rather than words that name data. Kept structural on purpose: a
# longer list would drop "daily" and "revenue", which are exactly the words a table is named after.
# Anything under three characters is dropped by `_words` and so is absent here.
_ASKING = frozenset([
    "the", "and", "for", "from", "with", "that", "this", "these", "those", "are", "its",
    "show", "build", "make", "create", "give", "please", "want", "need", "using", "use",
    "also", "into", "over", "all", "can", "you", "your", "our", "new", "one", "about",
    "each", "per", "just",
])

# Words that name A store rather than THIS store. Removed from a Data Source's name before it is
# used as a handle, so "Snowflake-Data-Warehouse" is recognised by "snowflake" and not by "data" —
# and removed from a request before tables are scored, so "from Snowflake" scores nothing.
_GENERIC_SOURCE = frozenset([
    "data", "database", "warehouse", "lake", "store", "source", "connection", "prod",
    "production", "dev", "development", "staging", "test", "replica", "shared", "main",
    "default", "cluster", "server", "instance",
])


@dataclass(frozen=True)
class Candidate:
    """One table a person could pick, at the full position that would be recorded.

    All three levels, because that is what `scope_data_source` is called with. A candidate carrying
    a bare table name would leave the click to guess a database, and `DWH.MARTS.GONG__CALLS` and
    `SANDBOX.PUBLIC.GONG__CALLS` are two different tables.
    """

    database: str
    schema: str
    table: str


@dataclass(frozen=True)
class Ranking:
    """Every table in the Data Source, best first, and how many of them matched anything.

    Both, because they answer different questions and the card asks both. `candidates` is what the
    person can pick from — all of it, so a bad ranking is a scroll rather than a dead end — while
    `matched` is what decides whether the card claims to have found anything. A search that matched
    nothing says so and shows the list; it must never present the alphabetical top five as though
    they were answers.
    """

    candidates: tuple[Candidate, ...]
    matched: int


def _words(text: str) -> list[str]:
    """The words of a sentence or a name, lowercased, short ones dropped."""
    return [w for w in _SPLIT.split(text.lower()) if len(w) >= 3]


def _fold(word: str) -> str:
    """`calls` and `call` are one word to a table name.

    A trailing `s` only, and only on a word long enough to survive losing it. Not a stemmer: the
    matching here is a convenience under a card a person reads, and a stemmer's mistakes would be
    invisible in exactly the place where being wrong is expensive.
    """
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _handles(*names: str) -> set[str]:
    """The words in a Data Source's name that could only mean this one.

    `Snowflake-Data-Warehouse` is reached by "snowflake"; "data" and "warehouse" would answer for
    any store in the project. A name made entirely of generic words keeps them all rather than
    reducing to nothing — a source someone called `test` has no other handle, and a Data Source
    that can never be named would put this whole path out of reach for them.

    Several names rather than one, because what a store is called depends on where it is read
    from: a Binding carries the name it was recorded under and the display name beside it, and a
    row off Domino's listing carries only its own.
    """
    words = {_fold(w) for w in _words(" ".join(names))}
    return (words - _GENERIC_SOURCE) or words


def named_source(prompt: str, mentioned: Iterable[str], bindings: list[Binding]) -> Binding | None:
    """The bound Data Source a request names and has not chosen a table in, or None.

    An @mention wins over the prose, because it is not a guess: the creator picked a row out of a
    list and the id came back with it. Prose is matched on the source's own distinctive words, so
    "a dashboard of daily gong calls from Snowflake" reaches `Snowflake-Data-Warehouse`.

    "Has not chosen a table" is the whole test, not "has no scope at all". A Binding that stopped at
    a database or a schema is a person who answered part of the question, and the part still
    unanswered is the one that decides whether a query can run.

    The first match wins where a request names two of them. Naming two unscoped Data Sources in one
    sentence is one question per source and this answers one — the second is asked on the turn after
    the first is recorded, which is also how a person would answer it.
    """
    unscoped = [b for b in bindings if b.kind == KIND_DATA_SOURCE and not b.table]
    if not unscoped:
        return None
    ids = {str(i) for i in mentioned}
    for binding in unscoped:
        if binding.id in ids:
            return binding
    said = {_fold(w) for w in _words(prompt)}
    for binding in unscoped:
        if said & _handles(binding.display_name, binding.name):
            return binding
    return None


# Words that say "a store somewhere else", read only where the app records NO Data Source at all
# (#185). Deliberately short. This is asked of a request that names nothing we hold, so a word here
# that is also ordinary app-building English — "data", "table", "database" — would stop somebody
# who asked for a to-do app and never meant a warehouse. A word missing from it costs today's
# behaviour instead, which is the direction to be wrong in.
_STORE_WORDS = frozenset(_fold(w) for w in [
    "warehouse", "lakehouse", "datasource", "datasources", "snowflake", "redshift", "bigquery",
    "databricks", "postgres", "postgresql", "mysql", "oracle", "teradata", "athena", "trino",
    "presto", "sqlserver", "mongodb", "clickhouse", "vertica", "netezza",
])


@dataclass(frozen=True)
class Offer:
    """Every Data Source the caller can reach, best first, and how many the request named.

    Both, for the reason `Ranking` carries both: the order is a convenience and the count is a
    claim. A card that drew its first row as the recommended one where nothing was named would be
    recommending the top of a listing — which is this code choosing a store after all, in the one
    place it has no evidence at all.
    """

    sources: tuple[dict, ...]
    named: int


def offer_sources(prompt: str, mentioned: Iterable[str], sources: list[dict]) -> Offer | None:
    """The Data Sources to put in front of a request that has none recorded (#185), or None.

    Three outcomes, and the empty offer is not the None. None says the request was never about a
    store and this turn is none of this code's business. An offer holding nothing says it was, and
    that the platform offers this caller nothing to read it from — which is a sentence somebody
    has to be told, because the alternative is an app built on rows the assistant invented.

    Every reachable source is offered and the ones the request names are only moved to the front.
    A caller who owns exactly one is still asked: using the only one silently is the same
    inference through a side door, and it would change under them the day a second one appears
    (ADR-0038).
    """
    ids = {str(i) for i in mentioned if str(i)}
    said = {_fold(w) for w in _words(prompt)}
    # A row with no id is a row no click can answer: the button would record nothing and, sharing
    # its empty name with every other such row, would leave them all live while it ran.
    sources = [s for s in sources if str(s.get("id") or "")]

    def names(source: dict) -> bool:
        # The WHOLE name, not a word of it, which is what makes a name safe to trigger on. One
        # word off a store's name is an accident waiting to happen — "add a billing page" against
        # a source called `billing-oracle` is a request about a screen, not about a warehouse —
        # while a request that says every distinctive word in a name is naming that store.
        # And the generic words go without coming back. `_handles` keeps them for a store named
        # out of nothing else, so that `test` can still be reached by name once somebody has bound
        # it (#183) — but here that fallback would put this card in front of "add a test page",
        # which is the ordinary English the word list above is kept short to stay out of.
        handles = _handles(str(source.get("name") or "")) - _GENERIC_SOURCE
        return bool(str(source.get("id") or "") in ids or (handles and handles <= said))

    named = [s for s in sources if names(s)]
    if not named and not (said & _STORE_WORDS):
        return None
    return Offer(tuple(named + [s for s in sources if not names(s)]), len(named))


def rank(prompt: str, source: Binding, tables: Iterable[Candidate]) -> Ranking:
    """Every table, ordered by how well its NAME answers the request.

    The source's own name is removed from the request first: "from Snowflake" says which store to
    look in, and leaving it in would score a `SNOWFLAKE_USAGE` table over the one being asked for.

    Ties are broken by the shorter name and then alphabetically, which is stable rather than clever.
    Where a name matcher cannot separate two tables it must not appear to: `GONG__CALLS` and
    `STG_GONG__CALLS` score the same, and the shorter-name tie-break is a coin toss dressed as an
    order until the model ranker arrives to make it a judgement.
    """
    asked = asked_words(prompt, source.display_name, source.name)
    scored = [(name_score(asked, c.table), c) for c in tables]
    scored.sort(key=lambda p: (-p[0][0], -p[0][1], len(p[1].table), p[1].schema, p[1].table))
    return Ranking(tuple(c for _, c in scored),
                   sum(1 for (whole, part), _ in scored if whole or part))


def asked_words(prompt: str, *names: str) -> list[str]:
    """The request's own words: what it says, less the words that name the thing it says it about.

    Public because the Dataset card asks the same question of file names that this file asks of
    table names (#196, ADR-0039). One splitter and one stop list across both, so the two surfaces
    cannot come to disagree about whether a request names something for no reason a person could
    learn.
    """
    asked = [w for w in (_fold(w) for w in _words(prompt)) if w not in _ASKING]
    handles = _handles(*names)
    return [w for w in asked if w not in handles]


def name_score(asked: list[str], name: str) -> tuple[int, int]:
    """How many of the request's words the name says, whole words first.

    Two numbers rather than one weighted sum: a whole word is a different kind of evidence from a
    substring — `CALLS` in `GONG__CALLS` against `CALL` inside `RECALLED` — and collapsing them into
    a score would need a weight nobody can defend from the names alone.
    """
    tokens = {_fold(t) for t in _SPLIT.split(name.lower()) if t}
    flat = "".join(_SPLIT.split(name.lower()))
    whole = sum(1 for w in asked if w in tokens)
    return whole, sum(1 for w in asked if w not in tokens and w in flat)


def grouped(candidates: Iterable[Candidate]) -> list[dict]:
    """Candidates as one entry per schema, in the order the ranking reached them.

    Grouped because the schema is the difference the person is being asked to see: `MARTS` and
    `STAGING` hold the same subject at two layers, and a flat list of names cannot say which of two
    identical-looking names is the modeled one. Rank order rather than alphabetical, so the schema
    holding the best match is the one read first.
    """
    out: list[dict] = []
    index: dict[tuple[str, str], dict] = {}
    for c in candidates:
        group = index.get((c.database, c.schema))
        if group is None:
            group = index[(c.database, c.schema)] = {
                "database": c.database, "schema": c.schema, "tables": [],
            }
            out.append(group)
        group["tables"].append(c.table)
    return out
