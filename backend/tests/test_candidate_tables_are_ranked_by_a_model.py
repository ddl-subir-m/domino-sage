"""Candidate tables are ordered by a model, and fail closed to a layer heuristic (#184).

WHY THIS EXISTS. #183 put the store's own tables in front of a person instead of a refusal, and
ordered them by name match — which is not merely a weaker ranking, it is blind to the distinction
that decides the app. Measured on the live warehouse: "gong" matches 27 of 602 tables, and
`MARTS.GONG__CALLS`, the modeled table a daily summary wants, scores IDENTICALLY to
`STAGING.STG_GONG__CALLS`, the raw one it does not.

WHAT THESE TESTS ARE CAREFUL NOT TO ASSERT is that any particular table ranks first because a model
would say so. Every verdict here is scripted, so what is under test is what the ranker does with an
answer — and above all what it does WITHOUT one. The fail rule is the whole point and it inverts
from the plan gate's: `scope` fails open because a classifier nobody can reach must not block
builds, and this one fails CLOSED, because the one answer it must never give is the refusal #183
exists to end.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sage.orchestrator import table_rank
from sage.orchestrator.service import Orchestrator
from sage.orchestrator.table_rank import fqn
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.provider import FakeResourceProvider, ResourceUnavailable
from sage.resources.table_search import Candidate, Ranking
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

PROMPT = "build me a dashboard of daily gong calls"

SOURCE = Binding(KIND_DATA_SOURCE, "ds-dwh", "Snowflake-Data-Warehouse", "Snowflake-Data-Warehouse")

CATALOG = ModelCatalog(
    sovereign_plan="sov-plan", sovereign_implement="sov-implement", sovereign_ask="sov-ask",
    plan="plan-model", implement="implement-model", ask="ask-model",
)


def _c(schema: str, table: str) -> Candidate:
    return Candidate("DWH", schema, table)


# The live warehouse's shape in miniature: one subject spread over the dbt layers, plus tables whose
# names say nothing about the request. The pair a name matcher cannot separate is the first two.
MARTS_CALLS = _c("MARTS", "GONG__CALLS")
STAGING_CALLS = _c("STAGING", "STG_GONG__CALLS")
MARTS_PARTICIPANTS = _c("MARTS", "GONG__CALL_PARTICIPANTS")
FACT_USAGE = _c("MARTS", "FCT_USAGE_DAILY")
DIM_ACCOUNT = _c("MARTS", "DIM_ACCOUNT")
REPORTING_VIEW = _c("REPORTING", "V_ARR_WATERFALL")
RAW_EVENTS = _c("RAW", "RAW_EVENTS")

ALL = (STAGING_CALLS, MARTS_CALLS, MARTS_PARTICIPANTS, FACT_USAGE, DIM_ACCOUNT,
       REPORTING_VIEW, RAW_EVENTS)

COLUMNS = {
    MARTS_CALLS: ["CALL_ID", "CALL_DATE", "DURATION_SECONDS", "ACCOUNT_ID"],
    STAGING_CALLS: ["_AIRBYTE_RAW_ID", "PAYLOAD", "_LOADED_AT"],
    MARTS_PARTICIPANTS: ["CALL_ID", "PERSON_EMAIL"],
    FACT_USAGE: ["USAGE_DATE", "ACCOUNT_ID", "SEATS_ACTIVE"],
    DIM_ACCOUNT: ["ACCOUNT_ID", "ACCOUNT_NAME"],
    REPORTING_VIEW: ["MONTH", "AMOUNT_USD"],
    RAW_EVENTS: ["BODY"],
}


class StubGateway:
    """Answers with scripted verdicts in order, and records what it was asked.

    A verdict is the raw assistant text — the ranker's contract is a list of names, so a test scripts
    names it copied out of the candidate list, or deliberately does not.
    """

    def __init__(self, *answers: str, raises: Exception | None = None, delay: float = 0.0,
                 sse: bool = True):
        self.answers = list(answers)
        self.raises = raises
        self.delay = delay
        self.sse = sse
        self.seen: list[tuple[dict, object]] = []

    def route(self, request, labels):
        self.seen.append((request, labels))
        if self.raises is not None:
            raise self.raises
        if self.delay:
            time.sleep(self.delay)
        answer = self.answers.pop(0) if self.answers else ""
        body = json.dumps({"choices": [{"delta": {"content": answer}}]})
        yield (f"data: {body}\n\ndata: [DONE]\n\n" if self.sse else body).encode()

    @property
    def prompts(self) -> list[str]:
        return [req["messages"][-1]["content"] for req, _ in self.seen]


class SpyColumns:
    """The column pull, as the ranker sees it: what it was asked for, on what budget, and what it
    answers."""

    def __init__(self, *, raises: Exception | None = None, columns=None):
        self.raises = raises
        self.columns = COLUMNS if columns is None else columns
        self.asked: list[list[Candidate]] = []
        self.budgets: list[float] = []

    def __call__(self, shortlist, budget):
        self.asked.append(list(shortlist))
        self.budgets.append(budget)
        if self.raises is not None:
            raise self.raises
        return {c: list(self.columns[c]) for c in shortlist if c in self.columns}


@pytest.fixture(autouse=True)
def _fresh_health():
    """The unreadable streak is process-wide (see table_rank._Health), so without this a test that
    leaves the breaker tripped turns every later test into an assertion about the breaker."""
    table_rank._health.reset()
    yield
    table_rank._health.reset()


def _ranking(candidates=ALL, matched: int = 2) -> Ranking:
    return Ranking(tuple(candidates), matched)


def _rank(gateway, *, columns=None, ranking=None, **kw) -> Ranking:
    return table_rank.rank_with_model(
        PROMPT, SOURCE, ranking or _ranking(),
        columns_for=columns or SpyColumns(),
        gateway=gateway, catalog=CATALOG, **kw)


def _lines(*candidates: Candidate) -> str:
    return "\n".join(fqn(c) for c in candidates)


# ---- the verdict decides the order -------------------------------------------------------------


def test_the_scripted_verdict_and_not_the_name_rank_decides_what_comes_first():
    """The reversal, in one call. `DIM_ACCOUNT` shares not one word with the request, so no name
    matcher would ever raise it — and when the model says it, it leads."""
    gateway = StubGateway(_lines(DIM_ACCOUNT, MARTS_CALLS), _lines(DIM_ACCOUNT, MARTS_CALLS))
    out = _rank(gateway)
    assert out.candidates[0] is DIM_ACCOUNT
    assert out.candidates[1] is MARTS_CALLS


def test_the_second_stage_may_overturn_the_first():
    """Which is what the columns are for: stage one ranks on names it cannot separate, stage two
    reads what the tables hold and is allowed to disagree."""
    gateway = StubGateway(_lines(STAGING_CALLS, MARTS_CALLS),
                          _lines(MARTS_CALLS, STAGING_CALLS))
    assert _rank(gateway).candidates[0] is MARTS_CALLS


def test_tables_the_model_did_not_name_keep_their_place_behind_the_ones_it_did():
    gateway = StubGateway(_lines(DIM_ACCOUNT), _lines(DIM_ACCOUNT))
    out = _rank(gateway)
    assert out.candidates[0] is DIM_ACCOUNT
    # Everything else in exactly the order it arrived in, with the picked table lifted out of it.
    assert list(out.candidates[1:]) == [c for c in ALL if c is not DIM_ACCOUNT]


def test_a_name_the_model_invented_is_dropped_rather_than_read_as_a_table():
    gateway = StubGateway("DWH.MARTS.GONG_CALLS_DAILY\n" + _lines(MARTS_CALLS),
                          _lines(MARTS_CALLS))
    out = _rank(gateway)
    assert out.candidates[0] is MARTS_CALLS
    assert set(out.candidates) == set(ALL)


@pytest.mark.parametrize("answer", [
    "1. DWH.MARTS.GONG__CALLS",
    "- `DWH.MARTS.GONG__CALLS`",
    "  DWH.MARTS.GONG__CALLS  ",
    "**DWH.MARTS.GONG__CALLS**",
    "1. **`DWH.MARTS.GONG__CALLS`** — the modeled table",
])
def test_a_decorated_answer_is_still_an_answer(answer):
    # A contract that failed on bold would fail closed constantly, and three such answers in a row
    # would trip a breaker nothing in the process resets. Bold is not a broken classifier.
    gateway = StubGateway(answer, answer)
    assert _rank(gateway).candidates[0] is MARTS_CALLS


def test_a_longer_name_is_never_read_as_a_shorter_one_with_a_suffix():
    longer = _c("MARTS", "GONG__CALLS_DAILY")
    gateway = StubGateway(fqn(longer), fqn(longer))
    out = _rank(gateway, ranking=_ranking((*ALL, longer)))
    assert out.candidates[0] is longer


# ---- two stages, and only the shortlist's columns -----------------------------------------------


def test_columns_are_pulled_for_the_shortlist_only_and_never_for_every_table():
    """The reason there are two stages at all: the live warehouse's full column catalog is roughly
    237,000 tokens and can never enter a prompt."""
    columns = SpyColumns()
    gateway = StubGateway(_lines(MARTS_CALLS, STAGING_CALLS), _lines(MARTS_CALLS, STAGING_CALLS))
    _rank(gateway, columns=columns)
    assert columns.asked == [[MARTS_CALLS, STAGING_CALLS]]


def test_the_shortlist_is_capped_even_when_the_model_names_more():
    columns = SpyColumns()
    everything = _lines(*ALL)
    gateway = StubGateway(everything, everything)
    _rank(gateway, columns=columns)
    assert len(columns.asked[0]) == table_rank.SHORTLIST


def test_the_first_stage_sees_names_and_the_second_sees_columns():
    gateway = StubGateway(_lines(MARTS_CALLS, STAGING_CALLS), _lines(MARTS_CALLS, STAGING_CALLS))
    _rank(gateway)
    names, with_columns = gateway.prompts
    assert "DURATION_SECONDS" not in names, "the name stage must not carry the column catalog"
    assert fqn(RAW_EVENTS) in names, "the name stage sees every candidate"
    assert "DURATION_SECONDS" in with_columns
    assert fqn(RAW_EVENTS) not in with_columns, "the column stage sees the shortlist only"


# ---- failing closed -----------------------------------------------------------------------------


def _fallback_cases():
    return {
        "timeout": lambda: (StubGateway(_lines(MARTS_CALLS), delay=0.2), {"timeout_s": 0.01}),
        "error": lambda: (StubGateway(raises=RuntimeError("gateway 502")), {}),
        "unreadable": lambda: (StubGateway("I would use the Gong calls table."), {}),
        "empty": lambda: (StubGateway(""), {}),
    }


@pytest.mark.parametrize("case", sorted(_fallback_cases()))
def test_every_failure_still_shows_every_candidate(case):
    """The one answer this path must never give. A ranker that is down has to cost an ordering, not
    the card — "no candidates" is precisely the refusal the feature exists to end."""
    gateway, kw = _fallback_cases()[case]()
    out = _rank(gateway, **kw)
    assert set(out.candidates) == set(ALL)
    assert len(out.candidates) == len(ALL)


@pytest.mark.parametrize("case", sorted(_fallback_cases()))
def test_every_failure_degrades_to_the_layer_heuristic(case):
    gateway, kw = _fallback_cases()[case]()
    assert _rank(gateway, **kw) == table_rank.by_layer(_ranking())


def test_the_layer_heuristic_lifts_the_modeled_table_over_the_raw_one():
    """The distinction the name matcher is blind to, decided without a model at all: `MARTS` and
    `FCT_`/`DIM_` up, `STAGING` and `STG_`/`RAW_` down. Both name-matched the request; only the
    layer separates them."""
    out = table_rank.by_layer(_ranking(matched=2))
    assert out.candidates[0] is MARTS_CALLS
    assert out.candidates[1] is STAGING_CALLS


def test_the_heuristic_does_not_reorder_past_what_the_names_matched():
    """The rule that keeps it from undoing the match it stands in for: a sort by layer over every
    candidate would lift an unrelated MARTS table above a STG_ one whose name answers the request
    word for word."""
    out = table_rank.by_layer(_ranking(matched=2))
    assert out.candidates[2:] == ALL[2:]


def test_with_nothing_matched_the_layer_is_the_only_signal_there_is():
    # Name order carries no information here, so the band is the shortlist instead of an empty one.
    out = table_rank.by_layer(_ranking(matched=0))
    assert out.candidates[0] is MARTS_CALLS
    assert out.candidates[:table_rank.SHORTLIST][-1] is STAGING_CALLS
    assert out.candidates[table_rank.SHORTLIST:] == ALL[table_rank.SHORTLIST:]


def test_tables_of_one_layer_keep_the_name_order_they_arrived_in():
    out = table_rank.by_layer(_ranking(matched=4))
    assert list(out.candidates[:4]) == [MARTS_CALLS, MARTS_PARTICIPANTS, FACT_USAGE, STAGING_CALLS]


def test_the_heuristic_never_changes_how_many_candidates_there_are():
    assert len(table_rank.by_layer(_ranking()).candidates) == len(ALL)
    assert table_rank.by_layer(Ranking((), 0)).candidates == ()


def test_no_candidates_is_returned_untouched_without_spending_a_call():
    gateway = StubGateway()
    assert _rank(gateway, ranking=Ranking((), 0)).candidates == ()
    assert gateway.seen == []


# ---- the second stage falls back to the first, not to the heuristic ------------------------------


def test_columns_that_cannot_be_read_keep_the_name_stages_verdict():
    """A model has already spoken about these names. Its answer is better evidence than the
    heuristic, which exists for when none has."""
    gateway = StubGateway(_lines(DIM_ACCOUNT, MARTS_CALLS))
    columns = SpyColumns(raises=ResourceUnavailable("Snowflake did not answer"))
    out = _rank(gateway, columns=columns)
    assert out.candidates[0] is DIM_ACCOUNT
    assert len(gateway.seen) == 1, "a failed column pull must not cost a second call"


def test_a_store_that_lists_no_columns_keeps_the_name_stages_verdict():
    gateway = StubGateway(_lines(DIM_ACCOUNT, MARTS_CALLS))
    out = _rank(gateway, columns=SpyColumns(columns={}))
    assert out.candidates[0] is DIM_ACCOUNT
    assert len(gateway.seen) == 1


@pytest.mark.parametrize("second", ["", "Neither of those, really.", "DWH.MARTS.NOT_A_TABLE"])
def test_an_unusable_second_answer_keeps_the_name_stages_verdict(second):
    gateway = StubGateway(_lines(DIM_ACCOUNT, MARTS_CALLS), second)
    out = _rank(gateway)
    assert out.candidates[0] is DIM_ACCOUNT
    assert set(out.candidates) == set(ALL)


def test_a_second_stage_that_drops_a_table_keeps_it_behind_the_ones_it_kept():
    # Answering with two of three is not a claim that the third is wrong, only that it stopped.
    gateway = StubGateway(_lines(STAGING_CALLS, MARTS_CALLS, DIM_ACCOUNT),
                          _lines(MARTS_CALLS, DIM_ACCOUNT))
    out = _rank(gateway)
    assert list(out.candidates[:3]) == [MARTS_CALLS, DIM_ACCOUNT, STAGING_CALLS]


def test_the_column_pull_spends_the_rankers_budget_rather_than_one_of_its_own():
    """Three independent ceilings would read as one number and cost three. Everything here runs
    before a single event is yielded, so the budget a caller sets is the silence a person sits in."""
    columns = SpyColumns()
    gateway = StubGateway(_lines(MARTS_CALLS), _lines(MARTS_CALLS))
    _rank(gateway, columns=columns, timeout_s=5.0)
    assert 0 < columns.budgets[0] <= 5.0


def test_a_stage_with_no_budget_left_is_skipped_rather_than_called():
    columns = SpyColumns()
    gateway = StubGateway(_lines(MARTS_CALLS), _lines(MARTS_CALLS))
    out = _rank(gateway, columns=columns, timeout_s=0)
    assert gateway.seen == []
    assert columns.asked == []
    assert out == table_rank.by_layer(_ranking())


# ---- the breaker --------------------------------------------------------------------------------


def test_three_unreadable_answers_trip_the_breaker_and_stop_the_calls():
    garbage = "I think you want the calls table."
    for _ in range(3):
        _rank(StubGateway(garbage))
    assert table_rank._health.is_broken("names")

    after = StubGateway(_lines(DIM_ACCOUNT), _lines(DIM_ACCOUNT))
    out = _rank(after)
    assert after.seen == [], "a broken ranker must not be called again in this process"
    assert out == table_rank.by_layer(_ranking()), "and it still orders the candidates"


def test_a_stage_that_never_answers_trips_on_its_own_streak():
    """The streak is per stage, and it has to be. One shared count cannot see a route that satisfies
    stage one and never stage two: stage one's success would clear it every turn, and every unscoped
    turn would go on paying a schema-wide column query for an answer known to be unusable."""
    columns = SpyColumns()
    for _ in range(3):
        _rank(StubGateway(_lines(MARTS_CALLS, DIM_ACCOUNT), "no idea, sorry"), columns=columns)
    assert table_rank._health.is_broken("columns")
    assert not table_rank._health.is_broken("names"), "stage one still works and still runs"

    after = StubGateway(_lines(DIM_ACCOUNT, MARTS_CALLS))
    out = _rank(after, columns=columns)
    assert len(after.seen) == 1, "only stage one is called once stage two is broken"
    assert len(columns.asked) == 3, "and the warehouse is not queried for it either"
    assert out.candidates[0] is DIM_ACCOUNT, "stage one's verdict still stands"


def test_the_breaker_is_logged_once_at_error(caplog):
    garbage = "I think you want the calls table."
    with caplog.at_level("ERROR"):
        for _ in range(5):
            _rank(StubGateway(garbage))
    broken = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(broken) == 1
    assert "BROKEN" in broken[0].getMessage()


def test_a_readable_answer_clears_the_streak():
    """Two bad answers and a good one is flake, not a broken route. Only three in a row is."""
    for _ in range(2):
        _rank(StubGateway("not a table name"))
    _rank(StubGateway(_lines(MARTS_CALLS), _lines(MARTS_CALLS)))
    _rank(StubGateway("not a table name"))
    assert not table_rank._health.is_broken("names")


def test_a_timeout_does_not_count_towards_the_breaker():
    # An unreachable route is a different fault from an unreadable answer, and tripping the breaker
    # on it would leave a recovered gateway permanently unused.
    for _ in range(4):
        _rank(StubGateway(_lines(MARTS_CALLS), delay=0.2), timeout_s=0.01)
    assert not table_rank._health.is_broken("names")


def test_more_than_one_name_on_a_line_is_more_than_one_table():
    """A model that answers in a sentence has still answered. Reading only the first name would cut
    the shortlist to one without ever looking unreadable."""
    line = ", ".join(fqn(c) for c in (MARTS_CALLS, DIM_ACCOUNT, FACT_USAGE))
    out = _rank(StubGateway(line, line))
    assert list(out.candidates[:3]) == [MARTS_CALLS, DIM_ACCOUNT, FACT_USAGE]


def test_a_ranker_that_raises_outright_still_orders_the_candidates():
    """The module's promise made good for the ways it can break that nobody anticipated.

    Resolving the ask slot happens before the call it is bounded by, so a catalog that cannot answer
    raises where no handler further in was watching for it. The caller runs all of this before a
    single event is yielded and catches nothing, so an escape here is not a worse ranking — it is a
    broken turn where a card was promised.
    """
    class NoAskSlot:
        @property
        def ask(self) -> str:
            raise RuntimeError("no alias is assigned to the ask slot")

    out = table_rank.rank_with_model(PROMPT, SOURCE, _ranking(), columns_for=SpyColumns(),
                                     gateway=StubGateway(), catalog=NoAskSlot())
    assert out == table_rank.by_layer(_ranking())


def test_one_candidate_is_not_worth_a_call():
    gateway = StubGateway()
    only = _ranking((MARTS_CALLS,))
    assert _rank(gateway, ranking=only) == only
    assert gateway.seen == []


def test_a_long_request_is_truncated_rather_than_sent_whole():
    """`scope` caps its prompt for the same reason: a pasted spec past the ask model's window is a
    gateway 400, which turns the ranker off for exactly the requests it helps most."""
    columns = SpyColumns()
    gateway = StubGateway(_lines(MARTS_CALLS), _lines(MARTS_CALLS))
    table_rank.rank_with_model("gong calls " + "x" * 50_000, SOURCE, _ranking(),
                               columns_for=columns, gateway=gateway, catalog=CATALOG)
    for sent in gateway.prompts:
        assert sent.count("x") <= table_rank.MAX_PROMPT_CHARS


# ---- what it spends -----------------------------------------------------------------------------


def test_spend_is_tagged_with_its_own_component_and_not_the_planning_phase():
    gateway = StubGateway(_lines(MARTS_CALLS), _lines(MARTS_CALLS))
    _rank(gateway, session="ses_1", version="abc123")
    assert [labels.component for _, labels in gateway.seen] == ["table-rank", "table-rank"]
    assert {labels.phase for _, labels in gateway.seen} == {"ask"}
    assert {labels.session for _, labels in gateway.seen} == {"ses_1"}


def test_it_runs_on_the_ask_slot_and_not_a_hardcoded_model():
    gateway = StubGateway(_lines(MARTS_CALLS), _lines(MARTS_CALLS))
    _rank(gateway)
    assert {req["model"] for req, _ in gateway.seen} == {CATALOG.ask}


def test_the_call_is_deterministic():
    gateway = StubGateway(_lines(MARTS_CALLS), _lines(MARTS_CALLS))
    _rank(gateway)
    assert {req["temperature"] for req, _ in gateway.seen} == {0}


# ---- what the card is told --------------------------------------------------------------------


def test_a_confident_ranking_does_not_turn_into_a_claim_that_a_name_matched():
    """`matched` decides whether the card says Sage found tables or says no name matched, and it
    means the NAMES answered — a fact no reordering changes.

    Neither stage gives the model a way to abstain: asked for names it returns names. So raising
    `matched` to what it chose would make the honest card unreachable, and a request for support
    tickets against a warehouse holding none would get six confident irrelevant rows.
    """
    gateway = StubGateway(_lines(DIM_ACCOUNT, MARTS_CALLS), _lines(DIM_ACCOUNT, MARTS_CALLS))
    out = _rank(gateway, ranking=_ranking(matched=0))
    assert out.matched == 0
    assert out.candidates[0] is DIM_ACCOUNT, "the order is still the model's"


def test_a_name_match_is_not_lost_either():
    gateway = StubGateway(_lines(DIM_ACCOUNT), _lines(DIM_ACCOUNT))
    assert _rank(gateway, ranking=_ranking(matched=3)).matched == 3


def test_the_heuristic_makes_no_such_claim():
    assert table_rank.by_layer(_ranking(matched=0)).matched == 0


# ---- the seam into the turn ---------------------------------------------------------------------


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp_path: Path, gateway) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=gateway,
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    orch._resources.tree["ds-dwh"] = {
        "DWH": {
            "MARTS": ["GONG__CALLS", "FCT_USAGE_DAILY", "DIM_ACCOUNT"],
            "STAGING": ["STG_GONG__CALLS"],
            "REPORTING": ["V_ARR_WATERFALL"],
        },
    }
    orch._resources.columns.update({
        "GONG__CALLS": [("CALL_ID", "VARCHAR"), ("CALL_DATE", "DATE")],
        "STG_GONG__CALLS": [("_AIRBYTE_RAW_ID", "VARCHAR"), ("PAYLOAD", "VARIANT")],
    })
    orch.bind_data_source("ds-dwh")
    return orch


TURN = "build me a dashboard of daily gong calls from Snowflake"


def _card(orch: Orchestrator, prompt: str = TURN) -> dict:
    events = list(orch._table_offer(prompt, [], {}) or [])
    cards = [e for e in events if e.get("type") == "table-candidates"]
    assert len(cards) == 1, f"expected one card, got {[e.get('type') for e in events]}"
    return cards[0]


def test_the_card_carries_the_order_the_model_gave(tmp_path: Path):
    """End to end through the turn's own gate: the walk, the name rank, both model stages, and the
    card. `DIM_ACCOUNT` shares no word with the request, so only a model puts it first."""
    named = "DWH.MARTS.DIM_ACCOUNT\nDWH.MARTS.GONG__CALLS"
    orch = _orch(tmp_path, StubGateway(named, named))
    card = _card(orch)
    assert card["groups"][0]["tables"][0] == "DIM_ACCOUNT"


def test_the_turn_reads_columns_only_where_the_shortlist_stands(tmp_path: Path):
    """The provider's own record of what was asked. The whole column catalog is 237,000 tokens and
    can never enter a prompt, so only the shortlist's own schemas are read — `REPORTING` holds
    candidates the model did not shortlist and is never touched.

    A query per SCHEMA and not per table, which ADR-0038's spike measured rather than assumed: every
    column in the database was 4.25s and the same query narrowed to one shortlist was 4.63s, so a
    filter buys tokens and never seconds. The narrowing that matters happens on the prompt.
    """
    named = "DWH.MARTS.GONG__CALLS\nDWH.STAGING.STG_GONG__CALLS"
    orch = _orch(tmp_path, StubGateway(named, named))
    asked: list[tuple[str, str]] = []
    inner = orch._resources.list_columns

    def spy(source, database, schema, table=""):
        asked.append((schema, table))
        return inner(source, database, schema, table)

    orch._resources.list_columns = spy  # type: ignore[method-assign]
    _card(orch)
    assert sorted(asked) == [("MARTS", ""), ("STAGING", "")]


def test_a_ranker_that_is_down_still_draws_the_card(tmp_path: Path):
    """The fail rule, at the seam a person would see it. Every table the walk found is still there,
    and the turn still stops to ask instead of handing the work back."""
    orch = _orch(tmp_path, StubGateway(raises=RuntimeError("gateway 502")))
    card = _card(orch)
    assert card["total"] == 5
    assert [g["schema"] for g in card["allGroups"]] == ["MARTS", "STAGING", "REPORTING"]


# ---- the same seam, from Chat -------------------------------------------------------------------
#
# WHY THESE LIVE HERE and not beside the rest of the Chat gate. #188 built Chat's offer against the
# pre-#184 shape, and both tickets were correct on their own branches: the gap only existed once
# both were on main, where the same request against the same store put a different table first
# depending on which mode the person happened to be standing in — and Chat, where ADR-0038 says
# finding happens, had the worse one. A test in either ticket's own file could not have caught that.
# So both cards are drawn from ONE orchestrator, one warehouse and one scripted verdict, and the
# comparison itself is the assertion (#193).


# Not `TURN`: "build me…" is a build request, which Chat answers with a handoff offer before it ever
# reaches the table gate. Both cards below are drawn from this one sentence, because two prompts
# would let the paths agree for the wrong reason.
ASK = "chart me daily gong calls from Snowflake"


def _thread(orch: Orchestrator) -> tuple[ThreadStore, str, list[dict]]:
    """A Thread using `Snowflake-Data-Warehouse` with no table chosen on it, and the context rows
    the turn would hand the gate — read the way `_chat_stream` reads them."""
    store = ThreadStore(orch.project(start_preview=False).record.path)
    thread_id = orch.create_thread()["id"]
    orch.add_thread_context(thread_id, {
        "kind": "data_source", "name": "Snowflake-Data-Warehouse",
        "bindingKey": ["data_source", "ds-dwh"],
    })
    items = [i for i in ((store.read_context(thread_id) or {}).get("items") or []) if i.get("id")]
    return store, thread_id, items


def _chat_card(orch: Orchestrator, prompt: str = ASK) -> dict:
    store, thread_id, items = _thread(orch)
    events = list(orch._chat_table_offer(
        store, orch.project(start_preview=False), thread_id, prompt, items) or [])
    cards = [e for e in events if e.get("type") == "table-candidates"]
    assert len(cards) == 1, f"expected one card, got {[e.get('type') for e in events]}"
    return cards[0]


def _tables(card: dict) -> list[str]:
    return [f"{g['schema']}.{t}" for g in card["groups"] for t in g["tables"]]


def test_the_chat_card_carries_the_order_the_model_gave(tmp_path: Path):
    """`DIM_ACCOUNT` shares no word with the request, so only a model puts it first — which is what
    tells a ranked card from the name rank this path used to hand out."""
    named = "DWH.MARTS.DIM_ACCOUNT\nDWH.MARTS.GONG__CALLS"
    assert _chat_card(_orch(tmp_path, StubGateway(named, named)))["groups"][0]["tables"][0] \
        == "DIM_ACCOUNT"


def test_chat_and_build_list_the_same_tables_in_the_same_order(tmp_path: Path):
    """The ticket itself. Same store, same request, same verdict — and no reason a person could
    learn for the two cards to differ, so they may not."""
    named = "DWH.MARTS.DIM_ACCOUNT\nDWH.MARTS.GONG__CALLS"
    orch = _orch(tmp_path, StubGateway(named, named, named, named))
    assert _tables(_chat_card(orch)) == _tables(_card(orch, ASK))


def test_a_ranker_that_is_down_still_draws_the_chat_card(tmp_path: Path):
    """The fail rule holds on this path too: no model leaves an ordering standing and every table
    the walk found still on the card. A Chat turn that cannot reach one keeps exactly what it had
    before #193 and loses nothing."""
    orch = _orch(tmp_path, StubGateway(raises=RuntimeError("gateway 502")))
    card = _chat_card(orch)
    assert card["total"] == 5
    assert [g["schema"] for g in card["allGroups"]] == ["MARTS", "STAGING", "REPORTING"]


def test_the_chat_rank_is_billed_to_the_conversation_and_not_to_a_build(tmp_path: Path):
    """Where the ranker's inputs come from, pinned. The gateway, the catalog and the Sage version
    are the Project's shim on both paths — there is one shim, and Chat's handoff classifier already
    reads it. The SESSION is the caller's: it is the cost rollup's key, and a Chat rank tagged with
    `project.session_id` would file this question's cost against a build that never asked it.
    """
    named = "DWH.MARTS.GONG__CALLS\nDWH.STAGING.STG_GONG__CALLS"
    gateway = StubGateway(named, named)
    orch = _orch(tmp_path, gateway)
    store, thread_id, items = _thread(orch)
    store.write_session_id(thread_id, "ses_chat", directory=str(tmp_path))
    list(orch._chat_table_offer(
        store, orch.project(start_preview=False), thread_id, ASK, items) or [])
    assert {labels.session for _, labels in gateway.seen} == {"ses_chat"}
    assert {labels.component for _, labels in gateway.seen} == {"table-rank"}
