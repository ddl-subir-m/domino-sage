"""Which of a composed statement's results the MODEL may see (ADR-0058, ADR-0041, #408).

The whole file is one question asked many ways: `COUNT(*)` must not be collateral damage of the rule
that keeps read rows out of the transcript, and `MAX(EMAIL)` must not get through on the strength of
being an aggregate. A rule keyed on the word "aggregate" admits all four of ADR-0058's counter-
examples, so the tests below are written around the pairs that a careless rule would not tell apart.

Two of these tests pin DECISIONS rather than defects, and both are marked where they sit: group-by
labels reach the model on purpose, and a statement with no aggregate at all does not even when every
column is a label. Anyone tightening the first or loosening the second is reverting ADR-0058, not
closing a hole.

Failing closed is tested as behaviour, not asserted in prose. An unparseable statement still RUNS
and its card still renders, so a fall-through to "disclose" here would be invisible from everywhere
except this file.
"""

from __future__ import annotations

import builtins

import pytest

from sage.liveread.disclosure import decide


def _rows(*rows: list) -> list[list]:
    return [list(r) for r in rows]


# --- the numbers, which are the point of the tool --------------------------------------------


@pytest.mark.parametrize("sql, row", [
    ("SELECT COUNT(*) AS N FROM EVENTS", [41234]),
    ("SELECT COUNT(DISTINCT USER_ID) AS U FROM EVENTS", [812]),
    ("SELECT SUM(AMOUNT) AS T FROM ORDERS", [10412.5]),
    ("SELECT AVG(AMOUNT) AS A FROM ORDERS", [41.2]),
    ("SELECT STDDEV(AMOUNT) FROM ORDERS", [3.1]),
    ("SELECT VARIANCE(AMOUNT) FROM ORDERS", [9.6]),
    ("SELECT VAR_POP(AMOUNT) FROM ORDERS", [9.6]),
    ("SELECT MEDIAN(AMOUNT) FROM ORDERS", [38.0]),
    ("SELECT CORR(A, B) FROM T", [0.82]),
    ("SELECT COVAR_POP(A, B) FROM T", [1.4]),
    ("SELECT APPROX_COUNT_DISTINCT(USER_ID) FROM EVENTS", [800]),
    ("SELECT MIN(AMOUNT) FROM ORDERS", [0.0]),
    ("SELECT MAX(AMOUNT) FROM ORDERS", [99.5]),
])
def test_a_derived_number_reaches_the_model(sql, row):
    verdict = decide(sql, _rows(row))
    assert verdict.discloses is True, verdict.reason
    assert verdict.reason == ""


def test_the_count_distinct_case_from_the_original_report():
    """#408's measured question, which the read-only lane could not answer at all."""
    sql = ("SELECT COUNT(*) AS EVENTS, COUNT(DISTINCT USER_ID) AS USERS FROM MIXPANEL.EVENTS "
           "WHERE TS >= DATEADD(day, -30, CURRENT_DATE)")
    verdict = decide(sql, _rows([41234, 812]))
    assert verdict.discloses is True, verdict.reason
    assert verdict.derived == (0, 1)


def test_the_regr_family_reaches_the_model_although_sqlglot_has_no_class_for_it():
    """ADR-0058 names `REGR_*` as reaching the model, and `sqlglot` parses every member of it as
    `Anonymous`. A rule keyed on node type alone would have refused a capability the decision
    grants — which is the reason `_DERIVED_FUNCTIONS` exists at all."""
    verdict = decide("SELECT REGR_SLOPE(Y, X) FROM T", _rows([1.4]))
    assert verdict.discloses is True, verdict.reason


def test_percentile_cont_reaches_the_model_through_its_within_group_wrapper():
    """It parses as `WithinGroup` wrapping the aggregate, so the wrapper has to be unwrapped before
    the type is read or an ADR-allowed function is refused by its own syntax."""
    sql = "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY AMOUNT) AS P50 FROM ORDERS"
    verdict = decide(sql, _rows([38.0]))
    assert verdict.discloses is True, verdict.reason


def test_a_null_is_not_a_stored_value():
    """An empty `AVG` is absence, not somebody's data. Refusing on it would block every average over
    a column with a gap in it."""
    verdict = decide("SELECT AVG(AMOUNT) FROM ORDERS", _rows([None]))
    assert verdict.discloses is True, verdict.reason


# --- the four counter-examples ADR-0058 names, which a rule keyed on "aggregate" would admit ----


@pytest.mark.parametrize("sql, row", [
    ("SELECT LISTAGG(NAME) FROM ACCOUNTS", ["Northwind,Bluebird"]),
    ("SELECT ARRAY_AGG(SSN) FROM PEOPLE", ["['123-45-6789']"]),
    ("SELECT ANY_VALUE(PHONE) FROM CONTACTS", ["+1-555-0100"]),
    ("SELECT STRING_AGG(NAME, ',') FROM ACCOUNTS", ["Northwind,Bluebird"]),
    ("SELECT MODE(REGION) FROM ACCOUNTS", ["EMEA"]),
])
def test_an_aggregate_that_hands_back_a_stored_value_does_not_reach_the_model(sql, row):
    """The SENTENCE is asserted, not only the refusal, and that is deliberate.

    Every one of these is refused twice over: by this branch, and again by the rule that a
    non-aggregate projection must be grouped by. Defence in depth is worth having and it makes the
    branch invisible to a test that only checks `discloses` — a plant that removed this branch
    entirely left the whole file green. What the branch is actually FOR is the sentence: told that
    the function collects a stored value, the model can ask for a count instead; told that the
    statement "does not group by it", it would try to group by `LISTAGG(NAME)`, which is worse than
    saying nothing.
    """
    verdict = decide(sql, _rows(row))
    assert verdict.discloses is False
    assert "worked out from the rows" in verdict.reason, verdict.reason


def test_max_over_text_is_refused_by_its_value_rather_than_by_its_name():
    """`MAX(EMAIL)` is ADR-0058's hardest counter-example and it does NOT take the branch above.

    `MAX` is a numeric aggregate over a numeric column, so the parse admits it and rule 4 catches it
    on what came back. Pinned as a separate test because the two paths produce different sentences
    and a reader should not have to guess which one this case takes.
    """
    verdict = decide("SELECT MAX(EMAIL) FROM CUSTOMERS", _rows(["ops@northwind.example"]))
    assert verdict.discloses is False
    assert "came back holding a stored value" in verdict.reason, verdict.reason


def test_max_over_text_is_refused_although_max_over_a_number_is_allowed():
    """The pair that proves the rule is keyed on the VALUE and not on the function name.

    A parser knows `MAX`; it cannot know the column's type. The values that came back do, which is
    the only reason rule 4 is not redundant with rule 3.
    """
    assert decide("SELECT MAX(AMOUNT) FROM ORDERS", _rows([99.5])).discloses is True
    blocked = decide("SELECT MAX(EMAIL) FROM CUSTOMERS", _rows(["ops@northwind.example"]))
    assert blocked.discloses is False
    assert "stored value" in blocked.reason


def test_a_string_in_a_column_the_parse_called_a_number_still_blocks_it():
    """Rule 4 against a store answering in a shape no list of function names predicted — a `SUM`
    over an interval type, say. The parse said number; the answer says otherwise, and the answer
    wins."""
    verdict = decide("SELECT SUM(SPAN) FROM T", _rows(["3 days 04:00:00"]))
    assert verdict.discloses is False


def test_a_true_is_not_a_number():
    """`bool` is an `int` in Python, so a value check written the obvious way would let a stored
    flag through as though it were a count."""
    assert decide("SELECT MAX(IS_ADMIN) FROM USERS", _rows([True])).discloses is False


def test_a_window_function_is_a_row_read_wearing_a_function():
    """`MAX(EMAIL) OVER (…)` returns one stored value per row, so it is a row read however much it
    looks like an aggregate."""
    verdict = decide("SELECT MAX(EMAIL) OVER (PARTITION BY ACCOUNT) FROM CUSTOMERS",
                     _rows(["ops@northwind.example"]))
    assert verdict.discloses is False


# --- the decisions, which are not defects -------------------------------------------------------


def test_a_group_by_label_reaches_the_model_on_purpose():
    """ADR-0058 RECORDS THIS AS A CHOICE. `SELECT ACCOUNT_NAME, COUNT(*) … GROUP BY 1` returns
    stored values in its first column, and it is the shape every real analysis takes — a chart needs
    exactly this pair, a label and a number per bar.

    If you are here because this looks like a leak: the permitted alternative forbids the most
    ordinary useful query there is, and the shell lane already returns exactly this today. Changing
    it is reverting a decision, not closing a hole.
    """
    verdict = decide("SELECT ACCOUNT_NAME, COUNT(*) AS N FROM EVENTS GROUP BY 1",
                     _rows(["Northwind Trading", 412], ["Bluebird Health", 208]))
    assert verdict.discloses is True, verdict.reason
    assert verdict.derived == (1,), "only the count is a number; the label is admitted as a label"


def test_a_label_grouped_by_name_and_a_label_grouped_by_position_are_one_statement():
    """`GROUP BY 1` and `GROUP BY ACCOUNT_NAME` are the same query written two ways. A rule that
    admitted one would be a rule about spelling."""
    rows = _rows(["Northwind Trading", 412])
    by_position = decide("SELECT ACCOUNT_NAME, COUNT(*) FROM E GROUP BY 1", rows)
    by_name = decide("SELECT ACCOUNT_NAME, COUNT(*) FROM E GROUP BY ACCOUNT_NAME", rows)
    assert by_position.discloses is True, by_position.reason
    assert by_name.discloses is True, by_name.reason


def test_a_bucket_expression_is_a_label_when_the_statement_groups_by_it():
    """Matched on the expression, not the column name: the bucket is what the count is counting."""
    sql = "SELECT DATE_TRUNC('day', TS) AS D, COUNT(*) AS N FROM EVENTS GROUP BY 1"
    verdict = decide(sql, _rows(["2026-09-01", 412]))
    assert verdict.discloses is True, verdict.reason


def test_a_column_the_statement_does_not_group_by_is_a_row():
    """The same column, the same type, one word of difference in the statement."""
    verdict = decide("SELECT ACCOUNT_NAME, EMAIL, COUNT(*) FROM E GROUP BY 1",
                     _rows(["Northwind", "ops@northwind.example", 412]))
    assert verdict.discloses is False
    assert "EMAIL" in verdict.reason


def test_labels_with_no_number_attached_do_not_reach_the_model():
    """RULE 2, AND IT IS WHAT STOPS THE LABEL CARVE-OUT BECOMING A DOOR.

    `SELECT DISTINCT EMAIL` and `SELECT A, B … GROUP BY 1, 2` are both a column of stored values
    with no number attached — the LISTAGG leak in a different shape. Requiring a real aggregate
    means a label is only ever disclosed as the thing a number is grouped BY, which is the case
    ADR-0058 argued for.
    """
    distinct = decide("SELECT DISTINCT EMAIL FROM CUSTOMERS", _rows(["ops@northwind.example"]))
    assert distinct.discloses is False
    pair = decide("SELECT ACCOUNT_NAME, REGION FROM ACCOUNTS GROUP BY 1, 2",
                  _rows(["Northwind", "EMEA"]))
    assert pair.discloses is False


def test_a_plain_row_read_does_not_reach_the_model():
    verdict = decide("SELECT EMAIL, CREATED_AT FROM CUSTOMERS LIMIT 10",
                     _rows(["ops@northwind.example", "2024-03-11"]))
    assert verdict.discloses is False


# --- failing closed, which is the property the module exists to have ----------------------------


@pytest.mark.parametrize("sql, says", [
    ("SELECT COUNT(*) FROM", "could not be read closely enough"),
    # An empty statement parses to nothing rather than failing, so it lands on the count of
    # statements rather than on the parse. Recorded as what it is rather than what it looks like.
    ("", "one statement at a time"),
    ("SELECT COUNT(*) FROM A; SELECT COUNT(*) FROM B", "one statement at a time"),
    ("SELECT * FROM EVENTS", "Name the columns"),
    ("SELECT T.* FROM EVENTS T", "Name the columns"),
    ("SELECT COUNT(*), * FROM EVENTS", "Name the columns"),
    ("SELECT COUNT(*) FROM A UNION SELECT COUNT(*) FROM B", "single SELECT"),
    ("UPDATE T SET X = 1", "single SELECT"),
    ("SELECT WEIRD_AGG(EMAIL) FROM CUSTOMERS", "stored values"),
])
def test_anything_this_cannot_read_closely_enough_is_card_only(sql, says):
    """Every way of not knowing is a refusal, and each one says which way it was.

    The sentence is asserted for the same reason as the value-selecting case above: several of these
    are ALSO caught by the rule that an ungrouped non-aggregate is a row, so a test checking only
    `discloses` leaves the specific branches unpinned — three plants proved exactly that. The
    sentence is the part the model acts on, so the sentence is what gets held still.

    Note what is NOT claimed: refusing `UPDATE` here is not what stops an `UPDATE` running. That is
    the read-only warehouse role (see `provider.run_statement`). This only declines to put a result
    in front of the model, and a later reader must not promote this list into a safety check.
    """
    verdict = decide(sql, _rows([1]))
    assert verdict.discloses is False
    assert says in verdict.reason, verdict.reason


def test_a_refusal_always_carries_a_sentence_and_a_disclosure_never_does():
    """`reason` is empty exactly when `discloses` is true. The caller renders one or the other, and
    a refusal with nothing to say leaves the model to invent why it could not answer — the failure
    ADR-0041's opening transcript is made of."""
    allowed = decide("SELECT COUNT(*) FROM E", _rows([1]))
    assert (allowed.discloses, allowed.reason) == (True, "")
    refused = decide("SELECT EMAIL FROM C", _rows(["x@y.example"]))
    assert refused.discloses is False and refused.reason


def test_with_no_parser_installed_nothing_reaches_the_model(monkeypatch):
    """The state the dependency comment in `pyproject.toml` describes: safe, and useless.

    Planted rather than reasoned about, because this is the one failure that looks like success from
    every other angle — the statement still runs, the card still renders, and only the model's
    context would quietly have filled with rows.
    """
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "sqlglot" or name.startswith("sqlglot."):
            raise ImportError("no sqlglot here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    verdict = decide("SELECT COUNT(*) FROM EVENTS", _rows([41234]))
    assert verdict.discloses is False
    assert "could not be checked" in verdict.reason


def test_a_result_shaped_unlike_the_statement_that_made_it_is_refused():
    """Nothing can be trusted to line up, so nothing is disclosed."""
    verdict = decide("SELECT COUNT(*), COUNT(DISTINCT X) FROM T", _rows([1]))
    assert verdict.discloses is False
