"""A group note left by a refused listing does not stand until an unrelated act clears it.

`/api/resources` answers per kind, so one leg can refuse while the other two answer. The panel then
draws that reason above rows which are the LAST good answer carried forward — present, stale and
unmarked (ADR-0034). Reported live: the LLM Gateway answered a 40x at `/v1/models` once, while the
token sidecar was still warming, and the note stayed under a group full of models. Every other model
list on screen went on reading right, because they read membership or those carried rows. Opening
Browse Domino cleared it, which is the tell: nothing else re-read the platform.

So the fix is a read, not a wording. Re-reads per scope load at 4, 15 and 60 seconds after the
refusal — a platform that is really down costs three extra calls, not a poll — and once more when
the network or the tab comes back with a note still up (#667). Time is virtual in the harness.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "listing_retry_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)

REFUSAL = "The LLM Gateway answered 400 at /v1/models."
TEN_MINUTES = 600_000


def _looks(refusals, at, then=None) -> list:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"refusals": refusals, "at": at, "then": then}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_refusal_is_read_again_and_the_note_goes():
    """The bug, from the outside: a warning nobody could clear without knowing where to click."""
    first, retry = _looks(1, [4000])
    assert (first["note"], first["reads"]) == (REFUSAL, 1)
    # The second read left on its own, and it is what takes the note down.
    assert (retry["note"], retry["reads"]) == (None, 2)
    assert retry["models"] == ["Risk scorer"]


def test_a_platform_that_stays_down_is_asked_three_more_times_then_left():
    """A short back-off, not a poll. Before #667 this asserted ONE retry; a Domino API that took
    longer than four seconds to recover then left its note up until a reload. Three retries at 4,
    15 and 60 seconds, and a gateway that is really refusing is then left alone — the note it
    raises is true and stays up."""
    looks = _looks(None, [3999, 4000, 18999, 19000, 78999, 79000, TEN_MINUTES])
    assert [(look["at"], look["reads"]) for look in looks] == [
        (0, 1), (3999, 1), (4000, 2), (18999, 2), (19000, 3), (78999, 3), (79000, 4),
        (TEN_MINUTES, 4)]
    assert {look["note"] for look in looks} == {REFUSAL}


def test_a_platform_that_recovers_on_a_later_retry_clears_the_note_and_stops():
    looks = _looks(2, [4000, 19000, TEN_MINUTES])
    assert [(look["note"], look["reads"]) for look in looks] == [
        (REFUSAL, 1), (REFUSAL, 2), (None, 3), (None, 3)]


@pytest.mark.parametrize("event", ["online", "visible"])
def test_the_network_or_the_tab_coming_back_reads_again_after_the_retries_are_spent(event):
    """The tab returns within seconds of the last read, so this is also past the thirty-second
    freshness gate a model picker keeps: a note on screen is worth the read."""
    *_, spent, back = _looks(None, [TEN_MINUTES], event)
    assert (spent["note"], spent["reads"]) == (REFUSAL, 4)
    assert (back["note"], back["reads"]) == (None, 5)
