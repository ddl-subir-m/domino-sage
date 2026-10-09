"""What one read returned, held in memory for the turn that read it (#729).

Two jobs, and neither is the model's. A chart is drawn by Sage from `rows` — every row the card got,
including the rows the model was never shown and, where **Kept rows** is off, the rows the file on
disk no longer holds (ADR-0045). And the answer's numbers are checked against `disclosed`, which is
exactly what the model was handed: a number may be stated only from a read whose values reached it.
The same two facts say which read an answer was drawn from, so a read a later one replaced is
shown as working rather than as a second answer (#732).

Never written down. The rows live on the orchestrator for one turn and are dropped when the next
turn's token is minted, the same lifetime as `_live_results`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HeldRead:
    title: str
    # The card's name, `<slug>.table.json`. Empty when the read put no card up (no rows).
    slug: str
    columns: list[str]
    rows: list[list]
    # What reached the model: rows, or a calculation's selected values. Empty for structure only.
    disclosed: list = field(default_factory=list)

    @property
    def withheld(self) -> bool:
        return bool(self.rows) and not self.disclosed


# A number as prose writes one: `1,446`, `12.5`, `$1.45B`, `60%`, `3 million`. Not inside a word
# (`Q3`, `FY2026`, an id), a decimal or a time.
_NUMBER = re.compile(
    r"(?<![\w.:])\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?"
    r"(?:\s?(%|percent\b|thousand\b|million\b|billion\b|[kKmMbB](?![\w])))?(?![\w:])")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
# Not claims about data: a calendar date, a clock time, a month and its day, a list marker.
_NOT_CLAIMS = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b"
    r"|^[ \t]*\d+[.)][ \t]", re.MULTILINE)


@dataclass(frozen=True)
class _Stated:
    said: str
    value: float
    decimals: int
    scale: float
    percent: bool
    plain: bool  # no separator, decimals or unit: the shape a year takes


def _stated(text: str) -> list[_Stated]:
    out = []
    for m in _NUMBER.finditer(_NOT_CLAIMS.sub(" ", text)):
        whole, frac, unit = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        out.append(_Stated(
            said=m.group(0).strip(), value=float(whole.replace(",", "") + frac),
            decimals=max(len(frac) - 1, 0), scale=_SCALE.get(unit, 1.0),
            percent=unit in ("%", "percent"), plain=not (frac or unit or "," in whole)))
    return out


@dataclass(frozen=True)
class _Carried:
    value: float
    # A count, or any other value with no fraction. `$9.0M` is never one: a figure stated in
    # thousands, millions or billions matches a value at its own scale, or one already in that unit
    # (`PIPELINE_M = 9.0`), and a deal count of 9 is neither (#747).
    whole: bool


def _carried(read: HeldRead) -> list[_Carried]:
    """Every number the model was handed by this read: its values, its totals, and its shape."""
    shape = [_Carried(float(n), True)
             for n in (len(read.rows), len(read.columns), len(read.disclosed))]
    return [*shape, *_values(read), *_totals(read)]


def _values(read: HeldRead) -> list[_Carried]:
    """The numbers in what this read disclosed, without its shape."""
    out: list[_Carried] = []

    def walk(value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            out.append(_Carried(abs(float(value)), isinstance(value, int)))
        elif isinstance(value, str):
            out.extend(_Carried(s.value * s.scale, s.scale == 1.0 and "." not in s.said)
                       for s in _stated(value))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)

    walk(read.disclosed)
    return out


def _cell(value) -> _Carried | None:
    """A disclosed cell as a number, when the whole cell is one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _Carried(float(value), isinstance(value, int))
    if isinstance(value, str):
        try:
            return _Carried(float(value), "." not in value)
        except ValueError:
            return None
    return None


def _totals(read: HeldRead) -> list[_Carried]:
    """What the disclosed rows add up to, which an answer states as often as a row (#747).

    A numeric column's total, each label's total and row count in every other column, and each
    value's and label total's share of its column. Nothing finer: every subset of rows sums to
    something, and a check that carried those would pass any figure at all.
    """
    rows = [r for r in read.disclosed
            if isinstance(r, (list, tuple)) and len(r) == len(read.columns)]
    if not rows:
        return []
    cells = [[_cell(v) for v in r] for r in rows]
    numeric = [i for i in range(len(read.columns))
               if any(c[i] for c in cells)
               and all(c[i] is not None or r[i] is None for c, r in zip(cells, rows))]
    out: list[_Carried] = []
    for i in numeric:
        whole = all(c[i] is None or c[i].whole for c in cells)
        total = sum(c[i].value for c in cells if c[i])
        groups: dict[tuple[int, str], float] = {}
        counts: dict[tuple[int, str], int] = {}
        for c, r in zip(cells, rows):
            for g in range(len(read.columns)):
                if g not in numeric:
                    key = (g, str(r[g]))
                    groups[key] = groups.get(key, 0.0) + (c[i].value if c[i] else 0.0)
                    counts[key] = counts.get(key, 0) + 1
        out.append(_Carried(abs(total), whole))
        out.extend(_Carried(abs(v), whole) for v in groups.values())
        out.extend(_Carried(float(n), True) for n in counts.values())
        if total:
            out.extend(_Carried(abs(v / total), False)
                       for v in [*(c[i].value for c in cells if c[i]), *groups.values()])
    return out


def _matches(stated: _Stated, carried: list[_Carried]) -> bool:
    tolerance = 0.5 * 10 ** -stated.decimals
    for scale in {stated.scale, 1.0}:
        for c in carried:
            if scale != stated.scale and c.whole:
                continue
            candidates = [c.value, c.value * 100] if stated.percent else [c.value]
            if any(abs(stated.value * scale - v) <= tolerance * scale + 1e-9 for v in candidates):
                return True
    return False


def unsupported_numbers(text: str, reads: list[HeldRead], prompt: str) -> list[str]:
    """The numbers in `text` that no disclosed read this turn carries, as they were written.

    Carried means: a value the model was handed, at the precision the prose states it (`1.4K` is
    1,446; `12%` is -0.1247), or a read's shape — its row and column counts, which a structure-only
    read may still be described by. The person's own numbers and a plain year are not claims.
    """
    carried = [v for read in reads for v in _carried(read)]
    return [stated.said for stated in _claims(text, prompt) if not _matches(stated, carried)]


def _claims(text: str, prompt: str) -> list[_Stated]:
    """The numbers `text` states about data: not a plain year, and not one the person typed."""
    asked = {s.value * s.scale for s in _stated(prompt)}
    return [s for s in _stated(text)
            if not (s.plain and 1900 <= s.value <= 2100)
            and s.value * s.scale not in asked and s.value not in asked]


def replaced(events: list[dict], roles: dict[str, str], reads: list[HeldRead],
             charted: set[str], text: str, prompt: str) -> set[str]:
    """The cards of answer reads a later read in this turn replaced (#732), to be shown as working.

    An earlier answer read is replaced when a LATER answer read from the same source is one the
    answer is drawn from, and the earlier one is not. Drawn from means Sage charted it, the answer
    names its card, or the answer states a number that read carries and the other does not. Both
    reads must be held, so Sage knows what each returned; anything less certain shows both
    (ADR-0063: an answer is never hidden on a guess).
    """
    held = {r.slug: r for r in reads if r.slug}
    claims = _claims(text, prompt)
    answers = [(str(e.get("artifact") or ""), str(e.get("source") or "")) for e in events]
    answers = [(path, source) for path, source in answers
               if path and source and roles.get(path) == "answer"
               and _slug(path) in held]

    def drawn(path: str, other: str) -> bool:
        read, rival = held[_slug(path)], _values(held[_slug(other)])
        mine = _values(read)
        return (read.slug in charted or path.rsplit("/", 1)[-1] in text
                or any(_matches(s, mine) and not _matches(s, rival) for s in claims))

    out: set[str] = set()
    for i, (earlier, source) in enumerate(answers):
        for later, other in answers[i + 1:]:
            if (other == source and later != earlier and drawn(later, earlier)
                    and not drawn(earlier, later)):
                out.add(earlier)
    return out


def _slug(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".table.json")
