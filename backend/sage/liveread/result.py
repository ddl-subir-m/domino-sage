"""What a Live read leaves behind (ADR-0041, ADR-0045).

The rows go to the Artifact the person sees. The assistant is handed a receipt — the columns, a
count, and a path.

ADR-0041 said that needed no consent from anybody, because nothing left Domino on the way to the
answer. That was false wherever the Project's git remote is not Domino's: this file is committed
and pushed, on the turn that read it, to a host nobody named. So the Artifact THIS writes carries
the SHAPE always — columns, a count, the cap, whether the read stopped short, the statement, the
timestamp — and the rows only where the Project answered **Kept rows**. What the assistant may SEE
is unchanged either way: `values` is about the model's context, which that decision never reaches.

One writer, not the rule over every writer. A `.table.json` the agent composes itself never passes
through here, and neither does a chart it saves; those are held where they land.

Filesystem only. Recording the Artifact in the Conversation's manifest belongs to the caller, which
owns the ThreadStore; this writes the file and says what it wrote.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import grant

# Chat's table cap, kept the same on purpose: one Live read and one Chat table disagreeing about how
# much "all of it" is would be a difference nobody could see and everybody would trip on.
CAP_ROWS = 500

# What may reach the MODEL, which is not the same question as what reaches the card. Measured
# against a real DWH table (`MARTS.GONG__CALLS`, 31 columns, ~890 characters a row): handing over
# the card's 500 rows would put roughly 112,000 tokens in one prompt. That is a context blowout,
# not a sample, and it would arrive silently on the one path that is allowed to carry real values.
#
# So the card keeps every row that was read and the model gets as many as fit in this budget.
# Measured in characters rather than rows because the row is not the unit that costs anything: a
# table three times as wide costs three times as much for the same "give me 20 rows".
VALUES_BUDGET_CHARS = 8000


@dataclass(frozen=True)
class Receipt:
    """What the assistant is told about a Live read. Never the rows, unless `values` was earned.

    `values` is filled by `record` and by nothing else, and only where `grant.values_allowed` found
    that the creator had already shared that table. There is no second way to populate it, which is
    what makes the safe default structural rather than merely documented.

    `truncated` travels with `cap` so the sentence a card writes has both halves — "500 of 12,431
    rows" — on ADR-0029's rule that truncation is a fact the caller reads, not a silence.

    `kept` is what was WRITTEN, not what was read: with **Kept rows** off the file holds this
    table's shape and the card renders a receipt, so a sentence telling the assistant the person is
    looking at the values would be describing a screen that does not exist.

    `values` may hold FEWER rows than the card does, because what the model can afford to read and
    what the person can afford to scroll are different budgets (see `VALUES_BUDGET_CHARS`). The
    caller has both numbers — `len(values)` and `rows` — so the sentence it writes can say so
    rather than implying the model saw everything.
    """

    path: str
    columns: list[str]
    rows: int
    truncated: bool
    cap: int = CAP_ROWS
    statement: str | None = None
    values: list[list] | None = None
    kept: bool = False


def record(
    examples_dir: Path,
    slug: str,
    title: str,
    columns: list[str],
    rows: list[list],
    *,
    cap: int = CAP_ROWS,
    truncated: bool = False,
    statement: str | None = None,
    binding: str = "",
    table: str = "",
    shared: tuple[tuple[str, str], ...] = (),
    keep_rows: bool = False,
) -> Receipt:
    """Write the Artifact for one Live read and return what the assistant may be told about it.

    `truncated` comes in already true when the reader upstream stopped short of the end — a capped
    Dataset listing, say — and is OR'd with this cap's own cut, so a result that was short twice
    still reads as short once.

    `keep_rows` is the Project's answer (ADR-0045), and it defaults to the safe one. A writer added
    later that forgets to ask therefore commits a shape rather than somebody's address — the same
    reason `values` can only be filled by `grant.values_allowed` and by nothing else.
    """
    examples_dir.mkdir(parents=True, exist_ok=True)
    kept = list(rows[:cap])
    short = bool(truncated or len(rows) > cap)

    name = f"{slug}.table.json"

    statement_path = None
    if statement:
        # Committed in both states. It carries no values: `sample_rows` takes a table and a limit
        # and cannot filter (ADR-0041). If predicate support ever lands, the statement starts
        # carrying literals and joins the rows on the wrong side of this decision.
        (examples_dir / f"{slug}.sql").write_text(statement.rstrip() + "\n")
        statement_path = f"examples/{examples_dir.name}/{slug}.sql"

    if keep_rows:
        # Chat's shape exactly — `{title, columns, rows}` with rows as positional arrays. The
        # Workbench reads this and nothing else; a bare array of objects renders as "No data" beside
        # a chart that looks fine, which is the failure the Chat instructions spell out at length.
        body: dict = {"title": title, "columns": list(columns), "rows": kept}
    else:
        # No `rows` key at all rather than an empty one: a `rows` array is a promise of values, and
        # an empty one reads as a table that came back with none. `shapeOnly` is what the card tests
        # for, so a file that deliberately holds no rows is never mistaken for a malformed dump the
        # renderer's salvage path could rescue.
        body = {
            "title": title,
            "columns": list(columns),
            "shapeOnly": True,
            "rowCount": len(kept),
            "cap": cap,
            "truncated": short,
            "readAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if statement_path:
            body["statement"] = statement_path
    (examples_dir / name).write_text(json.dumps(body, indent=2, default=str) + "\n")

    return Receipt(
        path=f"examples/{examples_dir.name}/{name}",
        columns=list(columns),
        rows=len(kept),
        truncated=short,
        cap=cap,
        statement=statement_path,
        kept=keep_rows,
        values=_within_budget(kept) if grant.values_allowed(binding, table, shared=shared) else None,
    )


def _within_budget(rows: list[list], budget: int = VALUES_BUDGET_CHARS) -> list[list]:
    """As many rows as fit the model's budget, and never none: one row is the whole point.

    A single row over budget still goes. The alternative is an escape hatch that answers nothing on
    exactly the wide tables somebody would ask about, and the store's own reduction has already cut
    the long values by the time they reach here.
    """
    out: list[list] = []
    spent = 0
    for row in rows:
        cost = len(str(row))
        if out and spent + cost > budget:
            break
        out.append(row)
        spent += cost
    return out
