"""A number worked out from derived aggregates is itself derived (#726, ADR-0058).

Measured on haiku, demo prompt 2: asked for "our win rate against each" competitor, the turn
answered that the win rate was not available, while the card it had just written held it. The
statement's win-rate column was `ROUND(100.0 * SUM(...) / COUNT(*), 1)`, and `decide` classified
only a BARE aggregate as derived, so arithmetic over two counts read as a stored value and the whole
result was withheld from the model. It was told to "answer about the shape", and did.

The same gap hides a percentage change (prompt 4's biggest mover) and even `ROUND(AVG(X), 2)`.

What stays refused is anything that could carry a stored value through the arithmetic: MIN/MAX
(text on one column, a number on another), a value aggregate, a window, a bare column, a function
not on the arithmetic list. And rule 4 still reads what came back.
"""
from __future__ import annotations

import pytest

from sage.liveread.disclosure import decide

WIN_RATE = ("SELECT COMPETITOR, COUNT(*) AS MENTIONS, "
            "ROUND(100.0 * SUM(CASE WHEN IS_WON THEN 1 ELSE 0 END) / COUNT(*), 1) AS WIN_RATE "
            "FROM DWH.MARTS.GONG_MENTIONS GROUP BY 1")


def test_a_win_rate_reaches_the_model():
    verdict = decide(WIN_RATE, [["acme", 12, 41.7], ["globex", 9, 0.0]])

    assert verdict.discloses, verdict.reason
    assert verdict.derived == (1, 2)


@pytest.mark.parametrize("sql, row", [
    (("SELECT ACCOUNT, (SUM(CUR_EVENTS) - SUM(PREV_EVENTS)) / NULLIF(SUM(PREV_EVENTS), 0) AS CHG "
      "FROM T GROUP BY 1"), ["novartis", -0.143]),
    ("SELECT ROUND(AVG(AMOUNT), 2) AS A FROM ORDERS", [41.25]),
    ("SELECT COALESCE(SUM(AMOUNT), 0) AS T FROM ORDERS", [0]),
    ("SELECT -COUNT(*) FROM T", [-3]),
    ("SELECT CAST(COUNT(*) AS FLOAT) / 7 FROM T", [5.5]),
    ("SELECT ABS(SUM(A) - SUM(B)) FROM T", [3]),
])
def test_a_percentage_change_and_a_rounded_average_reach_the_model(sql, row):
    verdict = decide(sql, [row])

    assert verdict.discloses, verdict.reason


@pytest.mark.parametrize("sql, row", [
    # MIN/MAX are numbers only if the column is; through arithmetic a numeric-looking text value
    # (a phone stored as text) would come back as a number and pass rule 4.
    ("SELECT MAX(PHONE) + 0 FROM CONTACTS", [5550100]),
    ("SELECT ROUND(MIN(SSN), 0) FROM PEOPLE", [123456789]),
    # A function off the arithmetic list can read characters out of a stored value.
    ("SELECT LENGTH(MAX(EMAIL)) FROM CUSTOMERS", [17]),
    # Arithmetic only, whatever the function wraps: the list is the rule, not a judgement per call.
    ("SELECT LENGTH(SUM(AMOUNT)) FROM ORDERS", [5]),
    ("SELECT ASCII(LISTAGG(NAME)) + 0 FROM ACCOUNTS", [65]),
    # A window is a row read wearing a function, inside arithmetic or not.
    ("SELECT SUM(AMOUNT) OVER () / COUNT(*) FROM ORDERS", [10.0]),
    # A bare column in the arithmetic is a row value.
    ("SELECT AMOUNT * 1.0 FROM ORDERS", [41.2]),
    ("SELECT SUM(AMOUNT) / AMOUNT FROM ORDERS", [1.0]),
    # No aggregate at all.
    ("SELECT 1 + 1", [2]),
])
def test_arithmetic_that_could_carry_a_stored_value_stays_on_the_card(sql, row):
    verdict = decide(sql, [row])

    assert not verdict.discloses
    assert verdict.reason


def test_a_string_out_of_the_arithmetic_is_still_refused_by_what_came_back():
    """Rule 4 is the backstop for the whole family: COALESCE can return its literal."""
    verdict = decide("SELECT COALESCE(SUM(AMOUNT), 'none') FROM ORDERS", [["none"]])

    assert not verdict.discloses
