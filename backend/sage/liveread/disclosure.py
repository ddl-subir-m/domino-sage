"""What a composed statement's result may tell the MODEL (ADR-0058, ADR-0041).

Pure: no I/O, no provider, no filesystem. It is handed a statement and the answer it produced, and
says whether the values may be put in front of the assistant. Kept apart from `result` for the same
reason `grant` is — that one answers "may this happen", this one answers "may the model see what
came back", and only this one has to be right about disclosure.

THE QUESTION THIS DOES NOT ANSWER is whether the statement is safe to RUN. That is the read-only
warehouse role, and `provider.run_statement` argues it at length. Nothing here is a security check
on the SQL, and a later reader must not promote it into one: a parser that decides disclosure can
fail closed and cost a person a number, while a parser trusted to decide safety fails open the first
time a dialect defeats it.

## The rule, and why it is not "is this an aggregate"

ADR-0041 keeps read rows out of the transcript and out of Recall. ADR-0058 carves out derived
numbers, because `COUNT(*)` must not be collateral damage of that rule. The carve-out is keyed on
the KIND OF VALUE and not on the word "aggregate", because these are all aggregates:

    SELECT MAX(EMAIL) FROM CUSTOMERS      -- one row, one cell, a real address
    SELECT LISTAGG(NAME) FROM ACCOUNTS    -- one row, one cell, a whole column
    SELECT ARRAY_AGG(SSN) FROM PEOPLE
    SELECT ANY_VALUE(PHONE) FROM CONTACTS

Each returns a value that was sitting in a row. A rule asking "is this an aggregate?" would hand all
four to the model and into Recall — the exact leak ADR-0041 exists to prevent.

So a result reaches the model when, and only when:

1. the statement parses, is exactly one statement, and is a plain `SELECT` — not a set operation,
   and with no `*` in its projection;
2. at least one output column is a numerically derived aggregate;
3. EVERY output column is either such an aggregate, or a non-aggregate expression that the statement
   groups by — a label;
4. every value actually returned in an aggregate column is a number or null.

Anything else goes to the card, where rows go.

Catalog discovery has a separate narrow exception: direct schema fields from known metadata
tables may reach the model without an aggregate. Table names and column types describe where to
query; withholding them makes the investigation skill's own discovery query unusable. This does
not admit general catalog rows, comments, defaults, expressions, or values from ordinary tables.

**Group-by labels are allowed on purpose, not by accident.** `SELECT ACCOUNT_NAME, COUNT(*) … GROUP
BY 1` returns stored values in its first column, and it is the shape every real analysis takes. The
permitted alternative is a rule that forbids the most ordinary useful query there is, and the shell
lane already returns exactly this today. ADR-0058 records it as a choice so that a later reader can
see it was made. **Do not "fix" this by blocking the label column; you would be reverting a
decision, not closing a hole.**

**Rule 2 is what stops that carve-out being used as a door.** `SELECT DISTINCT EMAIL FROM CUSTOMERS`
and `SELECT A, B FROM T GROUP BY 1, 2` are both a column of stored values with no number attached —
the LISTAGG leak in a different shape. Requiring a real aggregate means a label is only ever
disclosed as the thing a number is grouped BY, which is the case ADR-0058 argued for.

**Rule 4 is not redundant with rule 3.** A parser knows a function's name, not its result's type,
and `MIN`/`MAX` are numeric aggregates over a numeric column and value-selecting ones over text —
the ADR draws the line there and the statement does not say which side it is on. The returned values
do. This also catches a dialect returning a string where a number was expected, which no list of
function names could anticipate.

## Failing closed

Every way of not knowing is a refusal: an unparseable statement, a dialect `sqlglot` has not met, a
shape this module does not handle, `sqlglot` not installed at all. The alternative — falling through
to "disclose" when the parse is unavailable — is this module's whole purpose inverted, and it would
be invisible, because an unparseable statement still RUNS fine and the card still renders.

A refusal is not a failure. The statement ran, the card holds the answer, and the person is looking
at it; the model is told what it may say rather than handed the values. `reason` is written for the
model to read, so a turn blocked by `MAX(EMAIL)` can compose `COUNT(*)` instead rather than deciding
the question was impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Aggregates whose result is DERIVED from the rows rather than selected out of one. The list is
# ADR-0058's, spelled as `sqlglot` names the nodes; the names in the ADR's prose are the SQL ones.
#
# `Min`/`Max` are deliberately absent. They are in ADR-0058's allowed list "over a numeric column",
# which is a fact about the data and not about the statement, so they are admitted by
# `_NUMERIC_IF_VALUES_ARE` below and settled by rule 4 against what came back. Writing them here
# would be a fact about DATA dressed as a property of the statement.
#
# Every name here was checked against `sqlglot 27` rather than written from the ADR's prose, because
# a node name that can never match is a rule that silently does nothing. Two casualties of that
# check: `VarianceSamp` and `Covar` are not `sqlglot` classes at all — `VAR_SAMP` parses as
# `Variance` and there is no bare `COVAR` — so naming them would have been two entries that could
# only ever be dead. Re-run that check when the pin moves.
_DERIVED = frozenset({
    "Count", "Sum", "Avg", "Stddev", "StddevPop", "StddevSamp", "Variance", "VariancePop",
    "Median", "PercentileCont", "PercentileDisc", "Corr", "CovarPop", "CovarSamp",
    "ApproxDistinct", "ApproxQuantile",
})

# Aggregates that CAN be derived numbers and can equally hand back a stored value, decided by rule 4
# on what actually came back. Kept separate from `_DERIVED` rather than merged, because the two say
# different things: one is "this is always a number", the other is "this is a number if it is".
_NUMERIC_IF_VALUES_ARE = frozenset({"Min", "Max"})

_TABLE_IDENTITY = frozenset({"TABLE_CATALOG", "TABLE_SCHEMA", "TABLE_NAME"})
_CATALOGUE_FIELDS = {
    "TABLES": _TABLE_IDENTITY | {"ROW_COUNT"},
    "COLUMNS": _TABLE_IDENTITY | {"COLUMN_NAME", "DATA_TYPE", "ORDINAL_POSITION", "IS_NULLABLE"},
    "SCHEMATA": frozenset({"CATALOG_NAME", "SCHEMA_NAME"}),
}

# `sqlglot` has no class for a function it does not know, and parses it as `Anonymous` carrying the
# name — so these are matched by NAME rather than by type, and the ADR's own allowed list is why the
# first set is not empty. The whole `REGR_*` family is named in ADR-0058 as reaching the model and
# every member of it lands here, so a type-keyed rule alone would have refused a capability the
# decision grants. Measured, not assumed: `REGR_SLOPE` parses as `Anonymous`, `CORR` does not.
_DERIVED_FUNCTIONS = frozenset({
    "REGR_SLOPE", "REGR_INTERCEPT", "REGR_R2", "REGR_COUNT", "REGR_AVGX", "REGR_AVGY",
    "REGR_SXX", "REGR_SYY", "REGR_SXY",
})

# The other half of the same problem, and the more important half: `LISTAGG` is ADR-0058's own
# example of an aggregate that hands back a whole column, and it is `Anonymous` too. Without this
# set it would still have been refused — an unrecognised projection falls through to the label rule
# and is refused when nothing groups by it — but for the wrong reason and with a sentence telling
# the model to group by it, which is advice that would make things worse.
_VALUE_FUNCTIONS = frozenset({"LISTAGG", "MODE"})


@dataclass(frozen=True)
class Verdict:
    """Whether the model may be shown this result, and what to say when it may not.

    `reason` is empty exactly when `discloses` is true. It is written for the MODEL, because the
    model is the only reader that can act on it: told that `MAX(EMAIL)` selects a stored value, a
    turn can ask a different question, while told "disclosure refused" it can only apologise.

    `derived` names the output columns rule 4 had to confirm, by position. It is kept after the
    decision because the caller records what reached the model (ADR-0058), and "which columns were
    numbers" is the part of that record nothing else can reconstruct.
    """

    discloses: bool
    reason: str = ""
    derived: tuple[int, ...] = field(default_factory=tuple)
    # Schema facts are selected fields, not derived numbers. Keep the receipt honest about both.
    catalogue: tuple[int, ...] = field(default_factory=tuple)


def _refuse(reason: str) -> Verdict:
    return Verdict(False, reason)


def decide(sql: str, rows: list[list], *, connector_type: str = "") -> Verdict:
    """Whether `rows`, produced by `sql`, may be put in front of the model.

    Both halves in one call, rather than a static pass and a value pass a caller must remember to
    make. A caller that ran only the first would disclose `MIN(EMAIL)`, and nothing about the
    signature would have warned it — the safe order is not one to leave to whoever arrives next.
    """
    try:
        import sqlglot
        from sqlglot import expressions as exp
    except ImportError:
        # Fail closed, and say which of the two states this is. "Sage cannot check" and "Sage
        # checked and refused" are different sentences, and only one of them is somebody's job.
        return _refuse("The statement could not be checked here, so only the card has the result.")

    try:
        parsed = sqlglot.parse(sql)
    except Exception:
        # Broad on purpose: `sqlglot` raises more than `ParseError` on malformed input, and every
        # one of them means the same thing here — not parsed, so not disclosed.
        return _refuse("That statement could not be read closely enough to share its numbers, so "
                       "only the card has the result.")

    statements = [node for node in parsed if node is not None]
    if len(statements) != 1:
        return _refuse("Send one statement at a time; only the card has this result.")

    select = statements[0]
    if not isinstance(select, exp.Select):
        # A set operation, a `WITH` whose body is one, or anything that is not a plain SELECT.
        # Refused rather than handled: matching projections across the branches of a UNION is a
        # second rule, and a second rule is a second thing to get wrong about disclosure.
        return _refuse("Only the card has the result: ask this as a single SELECT if you need the "
                       "numbers.")

    projections = list(select.expressions)
    if not projections:
        return _refuse("That statement selects nothing to share; only the card has the result.")

    catalogue = _catalogue_projection(select, exp, connector_type)
    if catalogue:
        if any(len(row) != len(projections) for row in rows):
            return _refuse("The result did not line up with the statement, so only the card has it.")
        return Verdict(True, catalogue=catalogue)

    grouped = _grouped_expressions(select, exp)
    derived: list[int] = []
    for position, projection in enumerate(projections):
        node = projection.unalias() if hasattr(projection, "unalias") else projection
        if isinstance(node, exp.Star) or (isinstance(node, exp.Column)
                                          and isinstance(node.this, exp.Star)):
            # `SELECT *`, `SELECT T.*`, and the star in `SELECT COUNT(*), *`. A star's columns are
            # not in the statement, so there is nothing to classify and nothing that could make this
            # safe.
            #
            # The PROJECTION itself, never a search beneath it. `node.find(exp.Star)` reads as the
            # careful version and refuses `COUNT(*)` — the single most common statement this tool
            # will ever be given — because the star is that count's own argument. `COUNT(*)` counts
            # rows; it does not return any.
            return _refuse("Name the columns you want rather than selecting everything, and the "
                           "numbers can be shared. Only the card has this result.")
        kind = _classify(node, exp)
        if kind == "derived":
            derived.append(position)
            continue
        if kind == "value":
            # An aggregate that is neither derived nor conditionally so — LISTAGG, ARRAY_AGG,
            # ANY_VALUE, MODE, STRING_AGG. One cell, and a stored value sitting in it.
            return _refuse(
                f"`{node.sql()}` returns a value stored in a row rather than a number worked out "
                "from the rows, so only the card has the result. Ask for a count, a total or an "
                "average if you need the number in your answer."
            )
        if _label_of(node, grouped):
            continue
        return _refuse(
            f"`{node.sql()}` returns stored values rather than a number, and the statement does not "
            "group by it, so only the card has the result."
        )

    if not derived:
        # Rule 2. Without it the label carve-out becomes the door: `SELECT DISTINCT EMAIL` and
        # `SELECT A, B FROM T GROUP BY 1, 2` are a column of stored values with no number attached.
        return _refuse("That statement returns stored values rather than a calculation, so only the "
                       "card has the result. Add a count, a total or an average to see numbers "
                       "here.")

    return _confirm(tuple(derived), projections, rows)


def _catalogue_projection(select, exp, connector_type: str) -> tuple[int, ...]:
    """Direct schema facts from one parsed metadata table, never a name-like substring.

    This is narrower than `run.catalogue_read`, which only decides whether to fold a card. A
    comments column can be working material without being safe to disclose. Joins, nested queries
    and CTEs need provenance analysis that this exception does not attempt; the existing derived
    number policy still handles their aggregate results.
    """
    if (select.find(exp.CTE) or select.find(exp.Subquery) or select.find(exp.Join)
            or select.find(exp.Func) or len(list(select.find_all(exp.Select))) != 1):
        return ()
    tables = list(select.find_all(exp.Table))
    if len(tables) != 1 or not isinstance(tables[0].this, exp.Identifier):
        return ()
    table = tables[0]
    name = _metadata_identifier(table.this, connector_type)
    schema = _metadata_identifier(table.args.get("db"), connector_type)
    if schema == "INFORMATION_SCHEMA":
        fields = _CATALOGUE_FIELDS.get(name)
    elif (connector_type == "SnowflakeConfig"
          and (_metadata_identifier(table.args.get("catalog"), connector_type), schema)
          == ("SNOWFLAKE", "ACCOUNT_USAGE")):
        fields = _CATALOGUE_FIELDS.get(name) if name in {"TABLES", "COLUMNS"} else None
    else:
        fields = None
    if not fields:
        return ()
    for projection in select.expressions:
        column = projection.unalias()
        if not isinstance(column, exp.Column) or column.name.upper() not in fields:
            return ()
    return tuple(range(len(select.expressions)))


def _metadata_identifier(identifier, connector_type: str) -> str:
    if identifier is None:
        return ""
    name = identifier.name
    # Quoting preserves case. PostgreSQL's quoted uppercase lookalike is not its lowercase
    # built-in catalog, and Snowflake's quoted lowercase one is not its uppercase catalog. The
    # provider uses quoted uppercase names on Snowflake; other quoted dialects stay conservative.
    if identifier.args.get("quoted"):
        return name if connector_type == "SnowflakeConfig" and name == name.upper() else ""
    return name.upper()


def _confirm(derived: tuple[int, ...], projections: list, rows: list[list]) -> Verdict:
    """Rule 4: every value in a column the parse called a number really is one.

    `MIN`/`MAX` are why this exists — numeric aggregates over a numeric column, value-selecting ones
    over text, and the statement does not say which. It also catches a store answering a `SUM` over
    an interval with a string, which no list of function names would have predicted.

    `None` passes. A null is the absence of a value, not a stored one, and refusing on it would
    block every `AVG` over a column with a gap in it.
    """
    for row in rows:
        for position in derived:
            if position >= len(row):
                # The answer is a different shape from the statement that made it. Nothing here can
                # be trusted to line up, so nothing is disclosed.
                return _refuse("The result did not line up with the statement, so only the card has "
                               "it.")
            value = row[position]
            if value is None or isinstance(value, (int, float)) and not isinstance(value, bool):
                continue
            node = projections[position]
            return _refuse(
                f"`{node.sql()}` came back holding a stored value rather than a number, so only the "
                "card has the result."
            )
    return Verdict(True, "", derived)


def _grouped_expressions(select, exp) -> set[str]:
    """What the statement groups by, as comparable text, including `GROUP BY 1`.

    Positional group-bys are resolved against the projection list here rather than at the call site,
    because `GROUP BY 1` and `GROUP BY ACCOUNT_NAME` are the same statement written two ways and a
    rule that admitted one would be a rule about spelling.
    """
    group = select.args.get("group")
    if group is None:
        return set()
    out: set[str] = set()
    projections = list(select.expressions)
    for item in group.expressions:
        if isinstance(item, exp.Literal) and item.is_int:
            index = int(item.name) - 1
            if 0 <= index < len(projections):
                target = projections[index]
                out.add(_key(target.unalias() if hasattr(target, "unalias") else target))
            continue
        out.add(_key(item))
    return out


def _classify(node, exp) -> str:
    """What one projection returns: a derived number, a stored value, or neither.

    "Neither" is not a third safe answer — it means a label or a row, and the caller decides which
    by asking whether the statement groups by it. An unrecognised function lands there too, so a
    warehouse's next aggregate is refused rather than guessed at.
    """
    if isinstance(node, exp.WithinGroup):
        # `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY X)` parses as a `WithinGroup` wrapping the
        # aggregate, so the type test below would see the wrapper and refuse a function ADR-0058
        # names as reaching the model. The ordering expression inside is not a projection and
        # returns nothing to anybody.
        node = node.this
    name = type(node).__name__
    if name == "Anonymous":
        # A function `sqlglot` does not know, carrying its own name.
        called = str(node.this or "").upper()
        if called in _DERIVED_FUNCTIONS:
            return "derived"
        if called in _VALUE_FUNCTIONS:
            return "value"
        return "plain"
    if name in _DERIVED or name in _NUMERIC_IF_VALUES_ARE:
        return "derived"
    if isinstance(node, exp.AggFunc):
        return "value"
    # A window function is not an aggregate for this purpose even when it wraps one: `MAX(EMAIL)
    # OVER (…)` returns a stored value per row, which is a row read wearing a function.
    if isinstance(node, exp.Window):
        return "value"
    return "plain"


def _label_of(node, grouped: set[str]) -> bool:
    """Whether this projection is a group-by label — the one stored value ADR-0058 admits.

    Matched on the expression rather than on the column name, so `DATE_TRUNC('day', TS)` grouped by
    the same expression is a label, as it should be: it is a bucket, and the bucket is what the
    count is counting.
    """
    return bool(grouped) and _key(node) in grouped


def _key(node) -> str:
    """One expression as text that can be compared with another's.

    `sql()` rather than the node itself, because two structurally equal nodes are not `==` in
    `sqlglot` and an alias or a quoting difference must not decide whether a label is a label.
    """
    return node.sql(comments=False).strip().lower()
