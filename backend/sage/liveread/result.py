"""What a Live read leaves behind (ADR-0041).

The rows go to the Artifact the person sees. The assistant is handed a receipt — the columns, a
count, and a path. That is the whole reason "show me one sample conversation" needs no consent from
anybody: nothing leaves Domino on the way to the answer, so there is no decision to ask for.

Filesystem only. Recording the Artifact in the Conversation's manifest belongs to the caller, which
owns the ThreadStore; this writes the file and says what it wrote.
"""

from __future__ import annotations

import json
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
) -> Receipt:
    """Write the Artifact for one Live read and return what the assistant may be told about it.

    `truncated` comes in already true when the reader upstream stopped short of the end — a capped
    Dataset listing, say — and is OR'd with this cap's own cut, so a result that was short twice
    still reads as short once.
    """
    examples_dir.mkdir(parents=True, exist_ok=True)
    kept = list(rows[:cap])
    short = bool(truncated or len(rows) > cap)

    name = f"{slug}.table.json"
    # Chat's shape exactly — `{title, columns, rows}` with rows as positional arrays. The Workbench
    # reads this and nothing else; a bare array of objects renders as "No data" beside a chart that
    # looks fine, which is the failure the Chat instructions spell out at length.
    (examples_dir / name).write_text(
        json.dumps({"title": title, "columns": list(columns), "rows": kept}, indent=2, default=str) + "\n"
    )

    statement_path = None
    if statement:
        (examples_dir / f"{slug}.sql").write_text(statement.rstrip() + "\n")
        statement_path = f"examples/{examples_dir.name}/{slug}.sql"

    return Receipt(
        path=f"examples/{examples_dir.name}/{name}",
        columns=list(columns),
        rows=len(kept),
        truncated=short,
        cap=cap,
        statement=statement_path,
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
