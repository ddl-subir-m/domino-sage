"""What the catalogue sidebar says beside a filter, and what it says out loud (#368, #369).

Under ADR-0054's nesting the parent's number is visibly the sum of its children, so a child's `0`
now carries the parent's authority: the section reads complete and the child reads empty. `0` is
what a kind whose read REFUSED reports, because `api.js` fills every count key unconditionally —
and more often than that `keepUnreadKinds` carries the PREVIOUS rows over the refusal, so the child
reports a stale non-zero instead. Neither number is a thing Domino said. So the third state is
triggered by the error key and never by the value: a test that only covered the `0` would pass over
the commoner half of the defect, which is why the stale case below is not optional.

The other half is that the nesting is a `padding-left` and nothing else. An indent is not read out,
and `aria-label` replaces a button's contents for the readers who need it — so the `—` the first
half draws reaches them as a mark with no meaning unless the label spells it. Both halves are the
same twelve lines of `resource-catalog.js`, which is why they are one file here.

Drawn rather than grepped: the state is composed at draw time out of three inputs — the counts, the
listing's errors, and `KINDS` — that never appear together in the source.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "data_grouping_harness.mjs"

# One kind that answered, so the parent always has something honest to sum.
_ANSWERED = [{"id": "dataset:d1", "name": "Sales rows", "kind": "dataset"}]

# What `keepUnreadKinds` leaves behind: the rows from the LAST good read, still in `groups`, under
# an error saying the fresh read failed. This is the normal shape of a refusal, not the rare one.
_CARRIED_OVER = [
    {"id": f"data_source:s{n}", "name": f"Warehouse {n}", "kind": "datasource"}
    for n in range(1, 5)
]

_ONE_LEG_REFUSED = {"data_sources": "Couldn't read data connections."}

# A fault in the read itself, which `store.js` answers with a listing that has no kind key at all.
_WHOLE_READ_FAULTED = {"listing": "Couldn't read Domino."}


def _side(**extra) -> dict:
    """The sidebar, one entry per row, keyed by the word the row draws."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": "catalog", **extra}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    drawn = json.loads(out.stdout.strip().splitlines()[-1])["side"]
    return {entry["label"]: entry for entry in drawn}


# ------------------------------------------------------- #368: a read that refused says so

def test_a_kind_whose_read_refused_draws_a_mark_rather_than_a_zero():
    """`0` is a fact about the platform. A leg that refused has no fact to report, and the two
    must not share a glyph — a person reads the `0` as "this platform has no connections" and
    stops looking."""
    side = _side(errors=_ONE_LEG_REFUSED, groups={"dataset": _ANSWERED})
    assert side["Data connection"]["count"] == "—"
    # And the reason is one hover away rather than only in the note above the rows.
    assert side["Data connection"]["unreadTitle"] == "Couldn't read data connections."


def test_the_mark_is_drawn_over_the_stale_rows_carried_across_the_refusal():
    """The case the `0` never shows. `keepUnreadKinds` carries the last good rows forward, so the
    usual reading after a refusal is `Data connection 4` — present, stale and wrong at once. The
    trigger is the error key, so the number it would have drawn does not matter."""
    side = _side(
        errors=_ONE_LEG_REFUSED,
        groups={"dataset": _ANSWERED, "datasource": _CARRIED_OVER},
    )
    assert side["Data connection"]["count"] == "—"
    # The parent sums only the children that answered, so its `1` is a number Domino gave. The
    # dash sitting directly under it is what explains why it is not `5`.
    assert side["Data"]["count"] == 1


def test_a_whole_read_that_faulted_marks_every_kind_that_could_have_refused():
    """The listing that `store.js` synthesises carries no kind key at all, so nothing keyed by kind
    can reach these entries: without their own branch every one of them reads `0` on its own
    authority, at full width."""
    side = _side(errors=_WHOLE_READ_FAULTED, groups={})
    for label in ("Data", "File volume", "Data connection", "Language models", "Predictive models"):
        assert side[label]["count"] == "—", label
    # Three kinds report no error key and cannot fail this way, so they are left as they were.
    for label in ("Agents", "Skills", "MCPs"):
        assert side[label]["count"] == 0, label


def test_a_listing_that_answered_draws_the_number_it_drew_before():
    """The third state is a third state, not a rewrite of the first two."""
    side = _side()
    assert side["Data"]["count"] == 2
    assert side["File volume"]["count"] == 1
    assert side["Data connection"]["count"] == 1
    assert side["Data"]["unreadTitle"] is None


# ------------------------------------------- #369: the indent, for the readers who cannot see it

def test_a_child_filter_says_what_it_is_under_and_what_it_counts():
    """`is-child` is a `padding-left`. The only thing that says this row is a shape OF the row
    above it is the indent, and an indent is not read out."""
    assert _side()["File volume"]["aria"] == "File volume, in Data, 1"


def test_a_filter_that_is_not_a_child_lets_its_own_text_speak():
    """A label REPLACES a button's contents, so one on a row that is already right can only make
    it wrong — and it is one more copy of a word to keep in step with the visible one. Everything
    answered here, so every row's contents ARE right; the row below is the case where they are
    not."""
    for entry in _side().values():
        if not entry["isChild"]:
            assert entry["aria"] is None, entry["label"]


def test_a_filter_with_no_parent_still_says_a_count_it_could_not_read():
    """The indent is what the label was for, and three of the four kinds that can refuse have no
    parent to name. But `—` reaches a reader as nothing wherever it is drawn, so leaving those
    three to their own contents would have them announce LESS after the mark landed than the wrong
    `0` announced before it. A row with nothing to explain still says nothing."""
    side = _side(errors={"llm_aliases": "Couldn't read language models."}, groups={})
    assert side["Language models"]["aria"] == "Language models, not read"
    assert side["Agents"]["aria"] is None


def test_a_child_that_could_not_be_read_says_so_in_words():
    """A screen reader is handed the label instead of the contents, so `—` never reaches it. If
    the label carries the count it has to carry this state too, or the row loses its count."""
    side = _side(errors=_ONE_LEG_REFUSED, groups={"dataset": _ANSWERED})
    assert side["Data connection"]["aria"] == "Data connection, in Data, not read"
