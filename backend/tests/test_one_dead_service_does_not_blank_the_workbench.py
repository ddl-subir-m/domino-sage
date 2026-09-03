"""One dead boot read must not take the whole Workbench with it (ADR-0027).

`init()` reads eight things at boot from four different places, then three more in a deferred tail,
and not one of the eleven is the thing somebody came here to do. Who is looking, the other Projects they could switch to, the chart
registry, the starter deck, the bell: a Builder with all five dead still builds. Three of the eight
were already caught. The other five rejected the Promise.all, and a rejected `init()` is not a
missing panel — `app.js` turns it into the full-page "The workspace could not load", so one dead
service became a jail for somebody whose Builder was fine. ADR-0027 names that exact shape as the
reason a Problem informs and never blocks.

Only running the store shows this, and `ready` on its own proves nothing. It is set inside the
Promise.all that can reject, and the deferred tail rejects AFTER it is set and after the Workbench
has painted — and `app.js` tests its error branch before it tests `ready`, so a late reject pulls a
working Workbench down. Whether somebody got one depends on `ready` and on whether `init()` itself
resolved, so both are read from outside, together, in every test below.

Two things the ticket for this said are worth writing down, because the harness disagreed with both.

The first:
`/api/projects` returning 502 was never the read that blanked the screen. `api.js` has caught that
listing since #47 and folds it into a single row read off `/project`. `listing_502` below is kept
anyway — it is the whole stack under the real status the control plane returns, and it is the
regression guard for the catch that absorbs it. The rejects that actually reached `app.js` came from
the api surface itself: `projects()` also reads `/project`, and a chart or starter read that grows a
URL would join it.

The second: catching `/me` is not free, and the cost is not the greeting. `prefs` keys the viewer's
whole preference record on their id, so a nameless boot that still read prefs would open somebody
else's panels and write over them. Gated, and tested below, because a fix for one blank screen that
quietly loses a person's saved panels has not finished being a fix.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "boot_partial_outage_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

# What /api/project reports for the Project this Builder is bound to. The chip has to say this and
# not the id, because falling back to the id would read as a failure to whoever is looking at it.
BOUND_ID = "p-acme-risk"
BOUND_NAME = "Acme Risk Review"


def _boot(mode: str) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"mode": mode}), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_project_listing_that_answers_502_still_leaves_a_workbench():
    """The status the control plane really returns when it cannot list this viewer's Sage Projects,
    driven the whole way through `api.js`. A Workbench, not a wall: `ready` up and `init()`
    resolved, which are two different questions."""
    out = _boot("listing-502")

    assert out["ready"] is True
    assert out["initResolved"] is True, out["initError"]


def test_a_project_listing_that_answers_502_leaves_the_chip_naming_where_you_are():
    """The chip's whole job is to say which Project you are in. A dead listing costs the ability to
    move somewhere else; it must not cost knowing where you are."""
    out = _boot("listing-502")

    assert out["scopeName"] == BOUND_NAME
    assert out["scopeId"] == BOUND_ID
    # And the loss IS explained, without this ticket writing a word: `provisioning` is false in the
    # caught listing, so the picker draws its existing reason on the disabled New project control.
    assert out["canProvision"] is False


def test_a_project_listing_that_cannot_be_read_at_all_still_leaves_a_workbench():
    """The reject that reached `app.js`. `SW.api.projects` resolves the listing itself, so when IT
    fails there is no row at all — not an empty listing folded into one row, no `projects[0]`."""
    out = _boot("listing-unreadable")

    assert out["ready"] is True
    assert out["initResolved"] is True, out["initError"]
    assert out["projectCount"] == 0  # honestly empty, not a row we did not read


def test_a_project_listing_that_cannot_be_read_at_all_leaves_the_chip_naming_where_you_are():
    """`projects[0]` was carrying two things nothing else carried: the bound Project's display name
    and its model slots. Both come off `/project` instead, which is built from this container and
    asks the control plane nothing — which is why it answers when the listing cannot."""
    out = _boot("listing-unreadable")

    assert out["scopeName"] == BOUND_NAME
    assert out["scopeId"] == BOUND_ID
    # The other half of the same row. Without it Build's picker opens on the seeded catalog with no
    # slot marked current, which is how Build came to have no picker at all once before.
    assert out["buildMode"] == "plan"
    assert out["buildModel"] == "claude-sonnet-4"
    assert out["catalogKeys"] == ["ask", "implement", "plan"]


def test_charts_and_starters_that_cannot_be_read_leave_a_workbench_and_say_nothing():
    """Each falls back to its own empty value and earns no message. A missing starter list passes
    none of ADR-0027's three parts: it makes nothing fail, and there is no remedy to name. The
    Landing page draws the greeting and the composer and no prompt tiles, and that is the whole of
    it — a sentence about it would be noise in a crowded Workbench.

    The bell is in this mode too. It is the stub `empty()` in `api.js`, so it cannot reject today,
    and it was left uncaught under a comment claiming every read beside it was caught. The four
    reads next to it already carry a URL and say what happens the day this one does: a bell with
    nothing in it is not worth a wall."""
    out = _boot("soft-reads-unreadable")

    assert out["ready"] is True
    assert out["initResolved"] is True, out["initError"]
    assert out["chartKeys"] == []
    assert out["starters"] is None  # what `state.starters` already holds before the read lands
    assert out["notificationCount"] == 0
    # Not one toast, notification or modal, on any channel.
    assert out["said"] == []


def test_a_thread_index_that_answers_502_still_leaves_a_workbench():
    """The read the first pass at this missed, and the one that shows why `ready` alone proves
    nothing. `/api/threads` is not in the boot Promise.all — it is in the deferred tail below it, so
    `ready` was already true and the Workbench had already painted when the reject landed. `app.js`
    tests its error branch BEFORE it tests `ready`, so the wall went up over a Workbench that was
    working. A dead Thread index costs the conversation list, not the ability to build."""
    out = _boot("thread-index-502")

    assert out["ready"] is True
    assert out["initResolved"] is True, out["initError"]
    assert out["threadCount"] == 0
    assert out["said"] == []


def test_a_viewer_who_cannot_be_read_still_leaves_a_workbench():
    """`/api/me` is the read nothing in the Workbench needs in order to build, and the greeting is
    written for its absence — it drops the first name."""
    out = _boot("viewer-unreadable")

    assert out["ready"] is True
    assert out["initResolved"] is True, out["initError"]
    assert out["me"] is None


def test_a_viewer_who_cannot_be_read_opens_nobodys_saved_panels():
    """The half of that fallback that is not free. `prefs` keys the viewer's whole preference record
    on their id and falls back to the literal `me` a container with no identity to report answers
    with — the right key for a laptop run and the wrong one for a blipped read on a deployment,
    where it opens somebody else's panel choices and then writes this session's over them.

    The harness seeds exactly such a record, holding the opposite of both defaults, so a boot that
    read it is visible from outside. What this asserts is that the panels came up on the values a
    first visit gets, which is the honest answer for a session that does not know who is looking."""
    out = _boot("viewer-unreadable")

    assert out["me"] is None
    assert out["railHidden"] is True   # the seeded record says False
    assert out["dockTab"] is None      # the seeded record says 'activity'
