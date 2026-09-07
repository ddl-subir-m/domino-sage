"""Which of a Data Source's tables the request actually meant, judged by a model (#184).

The third read-only classifier on the ask slot, beside the plan gate (`scope`) and the handoff
classifier (`handoff`), and it follows the model choice the person already made for the same reason
they do: this is a question about their request, answered before their turn runs.

WHY A MODEL AT ALL. `table_search.rank` orders by name match, and name matching here is not merely
worse — it is blind to the distinction that decides the app. Measured on the live warehouse: "gong"
matches 27 of 602 tables, and `MARTS.GONG__CALLS`, the modeled table a daily summary wants, scores
IDENTICALLY to `STAGING.STG_GONG__CALLS`, the raw one it does not. No matcher over names can
separate them, because the names carry the same words.

THE FAIL RULE INVERTS, and that is the point. `scope` fails OPEN — a classifier nobody can reach must
not block builds. This one fails CLOSED: unreachable, slow, or unreadable, it falls back to a layer
heuristic over the same shortlist (prefer MARTS/FACT/DIM, demote STG_/RAW_/TMP_) and still hands the
card a full list of candidates. It must never degrade to "no candidates", which is the refusal the
whole feature exists to end.

TWO STAGES, because the full column catalog of the live warehouse is roughly 237,000 tokens and can
never enter a prompt. Stage one ranks NAMES down to a shortlist; stage two pulls columns for that
shortlist only — about 5,100 tokens for the Gong shortlist — and re-ranks with them in front of it.
The column pull is the caller's, handed in as `columns_for`: the I/O policy belongs where the
provider lives, and passing it in is what makes every failure path here reachable from a test
without a warehouse.

No user-facing copy lives in this file, so it owes no `brand_coverage.toml` entry. What the card
says is written where the offer is assembled.
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from collections.abc import Callable, Sequence

from ..gateway.client import CostLabels, GatewayClient
from ..resources.bindings import Binding
from ..resources.table_search import Candidate, Ranking
from ..router.models import ModelCatalog
from .scope import _extract, _model_for

log = logging.getLogger(__name__)

# Wall-clock budget for the WHOLE ranker — both model calls and the column pull between them, not
# one budget each. Everything here runs before a single event is yielded, on top of the ~3.8s
# database-wide walk that produced the candidates, so this number is what a person may sit in silence
# for before the card appears. Three independent ceilings would read as twelve seconds and cost
# thirty-six, which is the difference between a pause and a turn that looks hung.
#
# Spent, not sliced: a stage gets what is left, and a stage with nothing left is skipped rather than
# waited for. Every one of those exits keeps an ordering (see `rank_with_model`).
TIMEOUT_S = 20.0
MAX_UNREADABLE = 3

# How many tables survive stage one. Bigger than the five the card shows above the fold, so the
# re-rank has something to reorder, and small enough that pulling columns for it is a handful of
# narrow queries rather than a sweep of the database.
SHORTLIST = 6

# The names stage one is shown. The live warehouse's 602 fully-qualified names are roughly 6,000
# tokens, which is a promptable list and the whole reason the stages split where they do. Past the
# cap the tail is dropped in name-rank order — a store this size is already outside what was
# measured, and truncating beats refusing when the fallback is a heuristic over the same list.
MAX_NAMES = 800

# How much of the request each stage carries, matching `scope.MAX_PROMPT_CHARS`. Truncated rather
# than refused: what a table search needs is in the first sentence or two, and the alternative is a
# pasted spec pushing the request past the ask model's window — the gateway 400s, the ranker goes
# quiet for exactly the long detailed requests it helps most, and the failed call is still billed.
MAX_PROMPT_CHARS = 2000

# Columns per table in the stage-two prompt. A wide fact table can carry two hundred, and the ones
# past the first few dozen are audit and surrogate-key columns that say nothing about what the table
# is for.
MAX_COLUMNS = 40

_STAGE_ONE = """\
You are choosing which tables in a data warehouse could answer a request.

You are given the request and a list of fully-qualified table names, one per line.

Reply with at most {n} of those names, best first, one per line, copied exactly as given. \
Nothing else: no numbering, no explanation, and no name that is not in the list.

Where two tables cover the same subject in different layers, prefer the modeled table over the raw \
or staging copy of it."""

_STAGE_TWO = """\
You are ordering a shortlist of warehouse tables by how well each one answers a request.

Each table is given with its column names.

Reply with those same names, best first, one per line, copied exactly as given. Nothing else: no \
numbering, no explanation, and no name that is not in the list. Every table in the list must appear \
in your answer."""

# Layer words, and only the ones the fallback was specified with plus the dbt spelling of them that
# the live warehouse actually uses (`FCT_`, not `FACT_`). Guessing a wider vocabulary here would be
# the same mistake the name matcher makes, one level up.
_RAW_SCHEMAS = frozenset({"STAGING", "STG", "RAW", "TMP", "TEMP"})
_RAW_PREFIXES = ("STG_", "RAW_", "TMP_")
_MODELED_SCHEMAS = frozenset({"MARTS", "MART"})
_MODELED_PREFIXES = ("FACT_", "FCT_", "DIM_")

# The column pull, and the seconds it is allowed to take. The budget travels with the call because
# it is the ranker that owns the ceiling and the caller that owns the I/O: a pull with its own
# independent timeout would silently add itself to the number this module promises.
ColumnsFor = Callable[[Sequence[Candidate], float], dict[Candidate, list[str]]]


class _Health:
    """Consecutive unreadable answers, and the breaker they trip.

    Process-wide, like the other two classifiers': what is being tracked is the gateway route for
    the ask slot, not any one Data Source or turn.

    Counted PER STAGE, which the two siblings have no equivalent of because they have one call each.
    One shared streak cannot see a route that answers stage one and never stage two: stage one's
    success would clear the count every turn, the streak would go 0 → 1 forever, and every unscoped
    turn would go on paying a schema-wide column query (~4.6s) and a second gateway call whose answer
    is discarded. Breaking per stage also keeps the right thing when only stage two is gone — stage
    one's verdict is a model's ranking, and it is better than the heuristic that replaces no model.
    """

    def __init__(self) -> None:
        self.unreadable: dict[str, int] = {}
        self.broken: set[str] = set()

    def reset(self) -> None:
        self.unreadable.clear()
        self.broken.clear()

    def is_broken(self, stage: str) -> bool:
        return stage in self.broken

    def answered(self, stage: str) -> None:
        self.unreadable[stage] = 0

    def unreadable_answer(self, stage: str, answer: str) -> None:
        """Record an answer naming none of the tables it was given, and trip after three."""
        streak = self.unreadable.get(stage, 0) + 1
        self.unreadable[stage] = streak
        if streak < MAX_UNREADABLE:
            log.warning("table rank: unreadable %s answer %r (%d in a row)",
                        stage, answer[:80], streak)
            return
        if stage not in self.broken:
            self.broken.add(stage)
            log.error("table rank: the %s stage is BROKEN — %d unreadable answers in a row (last "
                      "%r). Not calling it again in this process. Check the gateway route for the "
                      "ask model.", stage, streak, answer[:80])


_health = _Health()


def fqn(candidate: Candidate) -> str:
    """`DB.SCHEMA.TABLE`, or `SCHEMA.TABLE` in a store with no database level.

    The same three levels `scope_data_source` is called with, because the answer has to name one
    table and `GONG__CALLS` alone names as many as there are schemas holding one.
    """
    return ".".join(p for p in (candidate.database, candidate.schema, candidate.table) if p)


def by_layer(ranking: Ranking) -> Ranking:
    """The shortlist reordered by dbt layer, everything after it untouched. The fallback.

    Reordering is confined to the band the name match already agreed about. `table_search.rank` puts
    every table whose name answered the request first, and `matched` is exactly where that band
    ends — so sorting across it would UNDO the match this is standing in for, lifting an unrelated
    `MARTS` table over a `STG_` one whose name answers the request word for word. Inside the band,
    where the names all answered, the layer is the best evidence left, and it is the one that tells
    `MARTS.GONG__CALLS` from `STAGING.STG_GONG__CALLS`.

    With nothing matched the band is the shortlist instead, because then the name order carries no
    information at all and the layer is the only signal there is.

    Stable, so tables of the same layer keep the name order they arrived in.
    """
    band = ranking.matched or min(SHORTLIST, len(ranking.candidates))
    head = sorted(ranking.candidates[:band], key=_tier)
    return Ranking(tuple(head) + ranking.candidates[band:], ranking.matched)


def _tier(candidate: Candidate) -> int:
    """0 modeled, 1 neither, 2 raw. Raw is checked first, so `STAGING.STG_GONG__CALLS` is demoted
    whatever else its name happens to start with."""
    schema = candidate.schema.upper()
    table = candidate.table.upper()
    if schema in _RAW_SCHEMAS or table.startswith(_RAW_PREFIXES):
        return 2
    if schema in _MODELED_SCHEMAS or table.startswith(_MODELED_PREFIXES):
        return 0
    return 1


def rank_with_model(
    prompt: str,
    source: Binding,
    ranking: Ranking,
    *,
    columns_for: ColumnsFor,
    gateway: GatewayClient,
    catalog: ModelCatalog,
    session: str | None = None,
    version: str | None = None,
    timeout_s: float = TIMEOUT_S,
) -> Ranking:
    """`ranking` reordered so the table the request meant comes first.

    Never returns fewer candidates than it was given and never returns none: every failure path ends
    at an ordering of the same tuple. Which ordering depends on how far it got — a stage two that
    fails keeps stage one's verdict, because a model has already spoken about these names and its
    answer is better evidence than the heuristic that exists for when none has.

    `timeout_s` is the budget for all of it. One deadline is taken here and every step downstream
    gets what is left of it, so the ceiling a caller sets is the ceiling a person waits.

    The outer guard is the module's promise made good. "Fails closed to an ordering" has to hold for
    every way this can go wrong, not only the ones anticipated one frame down — a `ThreadPoolExecutor`
    refused during interpreter shutdown, a catalog with no ask alias, a `display_name` that is not
    what it was taken for. The caller runs this before a single event is yielded and catches nothing
    but `TurnWedged`, so anything escaping here is not a worse ranking, it is a broken turn where the
    card was promised.
    """
    # One table cannot be reordered, and two gateway calls plus a schema-wide column read to find
    # that out is up to the whole budget of silence for a card that could not have differed.
    if len(ranking.candidates) < 2:
        return ranking
    if _health.is_broken("names"):
        return by_layer(ranking)
    try:
        return _ranked(prompt, source, ranking, columns_for=columns_for, gateway=gateway,
                       catalog=catalog, session=session, version=version,
                       deadline=time.monotonic() + timeout_s)
    except Exception:
        log.exception("table rank: ranking failed outright — ordering by layer instead")
        return by_layer(ranking)


def _ranked(
    prompt: str,
    source: Binding,
    ranking: Ranking,
    *,
    columns_for: ColumnsFor,
    gateway: GatewayClient,
    catalog: ModelCatalog,
    session: str | None,
    version: str | None,
    deadline: float,
) -> Ranking:
    """The two stages themselves. Every exit is an ordering of `ranking.candidates`."""
    index = {fqn(c).lower(): c for c in ranking.candidates}
    names = [fqn(c) for c in ranking.candidates[:MAX_NAMES]]
    payload = (f"Request: {prompt.strip()[:MAX_PROMPT_CHARS]}\n\n"
               f"Tables in {source.display_name}:\n" + "\n".join(names))
    first = _ask(_STAGE_ONE.format(n=SHORTLIST), payload, index,
                 gateway=gateway, catalog=catalog, session=session, version=version,
                 deadline=deadline, stage="names")
    if not first:
        return by_layer(ranking)

    shortlist = first[:SHORTLIST]
    ordered = _rerank_on_columns(prompt, source, shortlist, columns_for=columns_for,
                                 gateway=gateway, catalog=catalog, session=session,
                                 version=version, deadline=deadline)
    return _reordered(ranking, ordered)


def _left(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _rerank_on_columns(
    prompt: str,
    source: Binding,
    shortlist: list[Candidate],
    *,
    columns_for: ColumnsFor,
    gateway: GatewayClient,
    catalog: ModelCatalog,
    session: str | None,
    version: str | None,
    deadline: float,
) -> list[Candidate]:
    """Stage two: the shortlist ordered again with its columns in front of the model.

    Returns the shortlist unchanged when the columns cannot be read or the second answer cannot be,
    which keeps stage one's verdict rather than throwing it away for a heuristic.

    Stage two may name only the shortlist, which is why it is given an index built from the
    shortlist rather than the one stage one chose from: a re-rank is an ordering of six tables, and
    a seventh arriving here would be the model widening a choice it was asked to narrow.

    Skipped entirely once this stage's breaker has tripped, and the column read is skipped with it:
    the expensive half of stage two is the warehouse query, not the call, and paying it for an answer
    already known to be unusable is the cost the breaker exists to stop.
    """
    if _health.is_broken("columns"):
        return shortlist
    try:
        columns = columns_for(shortlist, _left(deadline))
    except Exception:
        log.exception("table rank: could not read columns for the shortlist — keeping the name rank")
        return shortlist
    described = [c for c in shortlist if columns.get(c)]
    if not described:
        return shortlist
    lines = [f"{fqn(c)}: " + ", ".join(columns[c][:MAX_COLUMNS]) for c in described]
    payload = (f"Request: {prompt.strip()[:MAX_PROMPT_CHARS]}\n\nShortlisted tables in "
               f"{source.display_name}, with their columns:\n" + "\n".join(lines))
    narrowed = {fqn(c).lower(): c for c in described}
    second = _ask(_STAGE_TWO, payload, narrowed, gateway=gateway, catalog=catalog, session=session,
                  version=version, deadline=deadline, stage="columns")
    if not second:
        return shortlist
    # Anything the re-rank dropped keeps its stage-one place behind what it kept. A model asked for
    # every name that answers with five of six has not said the sixth is wrong, only that it stopped.
    return second + [c for c in shortlist if c not in second]


def _ask(
    system: str,
    payload: str,
    index: dict[str, Candidate],
    *,
    gateway: GatewayClient,
    catalog: ModelCatalog,
    session: str | None,
    version: str | None,
    deadline: float,
    stage: str,
) -> list[Candidate]:
    """One bounded call, and the tables its answer named, best first. Empty on every failure."""
    timeout_s = _left(deadline)
    if not timeout_s:
        # An earlier step spent the whole budget. Skipped rather than called with nothing left,
        # because the caller of a stage that cannot finish already has the answer it will keep.
        log.warning("table rank: no budget left for the %s stage", stage)
        return []
    request = {
        "model": _model_for(catalog),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": payload}],
        # A ceiling, not a spend: the answer is a handful of names, but a route with extended
        # thinking on burns budget before it emits any content, and #29 is what an answer truncated
        # to nothing looks like from here.
        "max_tokens": 512,
        "temperature": 0,
        "stream": True,
    }
    # phase="ask" and a component of its own: this is not planning overhead, and the ticket asks for
    # it to be separable from both the plan gate and the build it runs in front of.
    labels = CostLabels(phase="ask", mode="auto", component="table-rank",
                        session=session, version=version)

    def _call() -> str:
        return _extract(b"".join(gateway.route(request, labels)))

    # Not a `with` block: the executor's context manager shuts down with wait=True, so leaving it
    # would block for the worker and buy nothing over the timeout above.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sage-tablerank")
    try:
        answer = pool.submit(_call).result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        log.warning("table rank: %s stage timed out after %.1fs", stage, timeout_s)
        return []
    except Exception as e:
        log.warning("table rank: %s stage failed (%s: %s)", stage, type(e).__name__, e)
        return []
    finally:
        pool.shutdown(wait=False)

    picked = _read(answer, index)
    if not picked:
        # An empty body is a route that said nothing rather than a model that answered badly, so it
        # belongs with the timeout above and not with the garbage the breaker counts.
        if not answer.strip():
            log.warning("table rank: %s stage returned an empty body", stage)
        else:
            _health.unreadable_answer(stage, answer)
        return []
    _health.answered(stage)
    log.info("table rank: %s stage picked %s (of %d) session=%s", stage,
             ", ".join(fqn(c) for c in picked[:SHORTLIST]), len(index), session or "-")
    return picked


def _read(answer: str, index: dict[str, Candidate]) -> list[Candidate]:
    """The tables an answer named, in the order it named them, duplicates and inventions dropped.

    A name is looked FOR inside each line rather than the line being required to equal one. Stripping
    the decorations instead was tried and is a trap: it can only strip the ones it was written to
    expect, so `**DWH.MARTS.GONG__CALLS**` reads as no table at all, an answer of six such lines is
    "unreadable", and three of those trip a breaker that nothing in the process ever resets. Bold is
    not a broken classifier. The cost of looking is that a line saying "not DWH.MARTS.GONG__CALLS"
    counts as naming it, which is a real misread — and a far cheaper one than switching the ranker
    off over markdown.

    Longest first, so `MARTS.GONG__CALLS_DAILY` is never read as `MARTS.GONG__CALLS` with a suffix,
    and each name found is blanked out of the line so a shorter one inside it cannot match the same
    characters twice.

    A line may hold MORE than one name. Stopping at the first was tried and is a quiet disaster: the
    prompt asks for one name per line, but a model that answers with a comma-separated sentence would
    yield a shortlist of one — not unreadable, so nothing is logged and the streak is cleared, while
    five of six picks are dropped and a whole schema of columns is read to order a list of one.

    A name that is not in the list it was given is discarded rather than counted against the answer:
    a reply that names four real tables and invents a fifth has still answered. Only a reply naming
    NONE of them is unreadable, which is the same bar the other two classifiers set — the call
    worked and the contract did not.
    """
    keys = sorted(index, key=len, reverse=True)
    out: list[Candidate] = []
    for line in (answer or "").splitlines():
        text = line.strip().lower()
        if not text:
            continue
        found: list[tuple[int, Candidate]] = []
        for key in keys:
            at = text.find(key)
            if at < 0:
                continue
            found.append((at, index[key]))
            text = text[:at] + " " * len(key) + text[at + len(key):]
        for _, candidate in sorted(found, key=lambda hit: hit[0]):
            if candidate not in out:
                out.append(candidate)
    return out


def _reordered(ranking: Ranking, chosen: list[Candidate]) -> Ranking:
    """The full tuple with the model's picks in front, everything else in the order it had.

    `matched` is carried through untouched, and deliberately: it means the NAMES answered, which is
    a fact about the warehouse that no reordering changes. Raising it to the number of tables the
    model chose was tried and is wrong, because neither stage gives the model a way to abstain —
    asked for six names it returns six, so `matched` would never be zero again and the card that
    says "no table name matches this request, so Sage will not guess one" would become unreachable.
    A request for a support-ticket dashboard against a warehouse holding no ticket table would get
    six confident irrelevant rows instead. The model's judgement is expressed as the ORDER, which is
    what it was asked for; whether any name matched is not its question.
    """
    rest = [c for c in ranking.candidates if c not in chosen]
    return Ranking(tuple(chosen) + tuple(rest), ranking.matched)
