"""Two plans for one Built App, and what keeps the newer one from reading as a duplicate (#278).

Ask for an app, approve the plan, let it build, then ask for a change. The Build gate proposes a
second plan rather than redrafting the first, because the document behind the built plan is the
record of what shipped and has to stay frozen (#277). So the Project's plan list holds two
documents, and both of them describe the same app — which means the planner wrote the same opening
sentence twice, and `caption` is that sentence until somebody types a title (#216, ADR-0042).
Approve the change too and the review state stops differing as well. A dogfood reader called the
result "two different plans with the same name".

The stamp is what separates them. It was on the document all along and nothing drew it.

The spacing is the whole claim, which is why the fixture holds pairs rather than one plan per
bucket. `relativeTime` — what every other event time in the shell uses — rounds to the hour past
the first hour and to a bare date past the first week, and a plan-build-plan round takes minutes.
Drawn through it, both rows of each pair read the same again, which is the bug and not a fix of it.

`test_an_archived_plan_is_put_away_not_lost` is the prior art for the harness, and shares it.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "plans_group_archive_harness.mjs"

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)

# A day phrase and a clock, whatever the zone and whatever ICU puts between the minute and the
# meridiem — newer ICU uses a narrow no-break space there, so the separator is not the claim.
_STAMP = re.compile(r"Created (today|yesterday|\d days ago|\w+ \d+, \d{4}) at \d{1,2}:\d{2}")


def _named(rows: list[dict], name: str) -> list[dict]:
    """The rows drawing one caption — a pair, for the two that share one."""
    return [row for row in rows if row["name"] == name]


def _day(row: dict) -> str:
    """The day phrase out of a row's stamp, without the clock — so a test can ask whether two rows
    fell on one day and were separated by the time of day alone."""
    found = re.search(r"Created (.+?) at ", row["subtitle"])
    assert found, row["subtitle"]
    return found.group(1)


def _rows() -> list[dict]:
    """The plan rows drawn over two pairs of same-named, both-approved plans for one app — one pair
    ten minutes apart yesterday afternoon, one pair forty minutes apart on one afternoon eight days
    ago — plus a document from before the stamp existed and one whose stamp nothing can parse.

    The harness anchors those stamps to the reader's own midnight, so they do not drift across it
    while the suite runs and these assertions hold at any hour.
    """
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": "dates"}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])["rows"]


@_needs_node
def test_two_plans_written_minutes_apart_do_not_draw_the_same_row():
    """The report in one assertion. The rows are named the same and hold the same status and the
    same app, so if the stamps tie there is nothing on screen that tells the reader which is which.
    Ten minutes apart is the real spacing: the first plan is built before the second is asked for."""
    near = _named(_rows(), "A desk exposure dashboard.")

    assert len(near) == 2
    assert near[0]["subtitle"] != near[1]["subtitle"]


@_needs_node
def test_two_plans_written_minutes_apart_a_week_ago_still_do_not():
    """The second place a coarse stamp collapses, and the one a reader hits without noticing: past
    seven days the shell's relative time is a bare date, and both of these were written on one
    afternoon. A list that tells plans apart only while they are fresh tells them apart by
    accident.

    The two rows have to carry the SAME date here, or this passes on the calendar rather than on the
    clock and says nothing about the pair it was written for."""
    old = _named(_rows(), "A consumption dashboard.")

    assert len(old) == 2
    assert _day(old[0]) == _day(old[1])
    assert old[0]["subtitle"] != old[1]["subtitle"]


@_needs_node
def test_each_plan_row_says_when_it_was_written():
    """Relative inside a week and absolute beyond it, per the writing guidelines, with the clock on
    the end of the phrase — the minute is the part that separates two plans for one app. `createdAt`,
    not `updatedAt`: what tells a change plan from the plan it follows is when it was written."""
    rows = _rows()
    near = _named(rows, "A desk exposure dashboard.")

    assert _STAMP.search(near[0]["subtitle"]), near[0]["subtitle"]
    assert near[0]["subtitle"].startswith("Created yesterday at ")
    # Inside the week but past yesterday it is the weekday, which is shorter than "4 days ago" and
    # does not stack a relative offset on an absolute clock.
    assert re.match(
        r"Created (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) at \d{1,2}:\d{2}",
        _named(rows, "A plan from midweek.")[0]["subtitle"])
    # And past the week it is a date rather than "8 days ago".
    assert re.match(r"Created \w+ \d+, \d{4} at \d{1,2}:\d{2}",
                    _named(rows, "A consumption dashboard.")[0]["subtitle"])


@_needs_node
def test_a_plan_whose_stamp_says_nothing_usable_reads_short():
    """Nothing backfilled `createdAt`, so a document can hold none, and the value is read out of a
    per-Project file that survives every rebuild, so it can hold anything. Either way the row says
    less rather than reading `Created Invalid Date`, and never leaves a bare separator behind."""
    rows = _rows()

    assert _named(rows, "A plan from before the stamp.")[0]["subtitle"] == "Draft · Not built yet"
    assert _named(rows, "A plan with a broken stamp.")[0]["subtitle"] == "Draft · Not built yet"


@_needs_node
def test_the_stamp_comes_before_the_segments_the_two_rows_share():
    """`.sw-res-sub` is one nowrap line with an ellipsis, so a narrow rail cuts from the end. These
    rows share the status and the app name and differ only in the stamp's last few characters, so
    everything they share has to come after it: the other way round the pair truncates to
    `Approved · Desk expos…` and the report is back for anybody who does not hover. The minutes have
    to land early enough to survive, not merely earlier than they did."""
    near = _named(_rows(), "A desk exposure dashboard.")
    first = near[0]["subtitle"]

    assert first.index("Created") < first.index("Approved") < first.index("Desk exposure")
    # The rail is roughly 45 characters of 11px text at its narrowest, and what has to fit is the
    # clock, not the sentence.
    assert first.index("PM") < 30 or first.index("AM") < 30
    # Still backed by the tip, for the width that cuts even this.
    assert near[0]["subtitleTip"] == near[0]["subtitle"]
    assert near[1]["subtitleTip"] == near[1]["subtitle"]
