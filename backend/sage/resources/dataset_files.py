"""Which files in a Dataset a request is about, matched on their names (#196, ADR-0039).

NOTHING IS SEARCHED FOR HERE, and that is the whole difference from `table_search`. A Data Source
hides its own address — nothing short of querying the catalog says which tables exist — so #179
spent five tickets buying a database-wide statement, a session cache and a two-stage ranker. A
Dataset's listing already names every file it holds, so all that is left is an ordering over names
somebody can already see.

Which is also why there is no model over this. ADR-0039 weighed one and rejected it: it is
disproportionate where the person can read the whole list, and the one distinction a Data Source
ranker exists to make — `MARTS` against `STG_` — has no Dataset equivalent. The order is a
convenience; the click is the answer.

Matched on the WHOLE relative path rather than the file name alone, because a partitioned Dataset
puts the subject in the folder and the date in the file: `gong/2026-07-01.csv` is about gong calls,
and a matcher reading only `2026-07-01.csv` would score it exactly like every other partition of
every other subject.

Pure functions over names, so this is testable with no platform. It puts no sentence in front of a
person, which is why it owes no `brand_coverage.toml` entry.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..assets.provider import DatasetFile
from . import table_search

# How many rows the card offers before "show all". Five, the same number and for the same reason
# `table_search.SHORTLIST` is five: it fits above the fold beside the request that is still
# readable, and everything else stays one click behind — a bad ordering costs a scroll and is never
# a dead end.
SHORTLIST = 5

# How many rows a card carries at all, behind the "show all". The listing's own cap is 5,000 files
# and a Dataset that reaches it is precisely the one whose folder act is withheld — so without this
# the transcript takes five thousand rows per card on the shape most likely to produce them. What is
# past it is reachable through the Data panel's tree, which is the way back this card adds nothing
# to (#194, story 15).
MAX_ROWS = 200


@dataclass(frozen=True)
class Ranking:
    """Every file in the Dataset, best first, and how many of them the request named.

    Both, for the reason `table_search.Ranking` carries both: the order is a convenience and the
    count is a claim. A card saying it found the files where nothing matched would be presenting
    the top of a listing as an answer.
    """

    candidates: tuple[DatasetFile, ...]
    matched: int


def rank(prompt: str, dataset_name: str, files: Iterable[DatasetFile]) -> Ranking:
    """Every file, ordered by how well its path answers the request, then by the listing's order.

    The Dataset's own name is removed from the request first, exactly as a Data Source's is: "from
    the sales_2026 Dataset" says where to look, and leaving it in would score every file under a
    `sales_2026/` prefix as a match on the strength of the folder they all share.

    Ties break on the path, which is the order the listing already came in — so a request that
    names nothing gets the Dataset tree's own order rather than an arbitrary one.
    """
    asked = table_search.asked_words(prompt, dataset_name)
    scored = [(table_search.name_score(asked, f.path), f) for f in files]
    scored.sort(key=lambda p: (-p[0][0], -p[0][1], p[1].path))
    return Ranking(tuple(f for _, f in scored),
                   sum(1 for (whole, part), _ in scored if whole or part))
