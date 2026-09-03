"""A workspace whose proxy has not started serving is not a fault to report (ADR-0027).

Domino reports a workspace as running before its proxy serves it, so the first call out of a
Workbench somebody has just opened comes back 502 from nginx — from the proxy, not from Sage, which
is why it carries no sentence: `api.js` reads the `error` field off a JSON body and an nginx 502 has
none. It clears itself in a few seconds. ADR-0027 rejected reporting on first detection for exactly
this shape, and named the remedy: retry with backoff at boot.

What the boot did with it before this is worth writing down, because it is not what the ticket for
this said and the harness is what settled it. The full-page "The workspace could not load" was NOT
what a warming proxy produced — every boot read has been caught since #the-one-dead-service work,
so a 502 across the board produced no wall at all. It produced a Workbench: painted, `ready`, and
holding nothing. No Projects, no conversations, no viewer, and not a word about any of it, because
each of those catches is right about one dead service and wrong about a container that has not
opened yet. A silent empty Workbench is the worse of the two failures — there is nothing on screen
to reload past.

So both halves are tested here. Under the budget nobody learns any of it happened; over the budget
the wall stands, with the platform's own 502 quoted on it.

/healthz is the probe because it is already the readiness signal the door redirect waits on, and
`init` already read it for the picker's open-weight list. Nothing was added to the endpoint — this
is that read moved in front of the others.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "boot_warmup_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

WAITING = "Waiting for this workspace to finish starting…"
SPINNER_ONLY = "Loading your workspace…"


def _boot(mode: str) -> dict:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"mode": mode}), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_502_that_clears_inside_the_budget_is_never_reported():
    """The whole point. The proxy 502s for three seconds and then serves, and what is left on
    screen is a Workbench that booted — no wall, and nothing for anybody to reload past."""
    out = _boot("clears")

    assert out["initResolved"] is True, out["initError"]
    assert out["ready"] is True
    assert "Shell" in out["finalTags"]
    assert "Result" not in out["finalTags"]
    # And it was really a 502 that was waited out, not a proxy that answered first time.
    assert out["healthzTries"] > 1


def test_the_boot_screen_says_it_is_waiting_rather_than_spinning():
    """A bare spinner over a wait that can run to ten seconds reads as a hang, and then flips to a
    wall over a fault that was clearing itself the whole time. Build's `previewStatus: 'starting'`
    overlay already answers this for Vite; the boot screen answers it the same way."""
    out = _boot("clears")

    assert WAITING in out["screens"], out["screens"]
    # It says so only after the first try has failed: a workspace that answers straight away must
    # not accuse itself of being slow.
    assert out["screens"].index(SPINNER_ONLY) < out["screens"].index(WAITING)
    # And it stops saying it. `null` is a screen that is not the boot screen at all.
    assert out["screens"][-1] is None
    assert out["bootStatus"] is None


def test_a_502_that_outlasts_the_budget_still_reaches_the_error_page():
    """The retry is a wait, not a blindfold. Ten seconds of 502 is a container that is not coming
    up on its own, and the person has to be told — by the existing full-page error, with the
    platform's own words quoted through `PlatformError` (#121) rather than retold."""
    out = _boot("outlasts")

    assert out["initResolved"] is False
    assert out["ready"] is False
    assert "Result" in out["finalTags"]
    assert "Shell" not in out["finalTags"]
    assert "The workspace could not load" in out["finalWords"]
    assert "502 Bad Gateway" in out["finalWords"]
    assert "Reload" in out["finalWords"]


def test_a_healthz_that_is_merely_unhappy_is_neither_waited_out_nor_walled():
    """A status Sage answered with is not a warm-up.

    /healthz carries one thing this boot needs — the picker's open-weight list — and that read was
    caught long before this change, because a gateway that cannot answer still leaves four working
    slots for the picker to open. Putting the read in front of the others must not turn it into a
    gate on the whole Workbench: a 500 is Sage reached and talking, so it is reported through the
    same catch it always had, on the first try, with nothing on screen about starting up.
    """
    out = _boot("unhealthy")

    assert out["initResolved"] is True, out["initError"]
    assert out["ready"] is True
    assert "Shell" in out["finalTags"]
    assert "Result" not in out["finalTags"]
    # Not waited out: one try, and the boot screen never accused the workspace of starting.
    assert out["healthzTries"] == 1, out["healthzTries"]
    assert WAITING not in out["screens"], out["screens"]


def test_the_budget_is_spent_and_then_stops():
    """A retry with no end is the same hang wearing a different hat. Ten seconds, backing off, and
    then it reports."""
    out = _boot("outlasts")

    assert 9_000 <= out["elapsedMs"] <= 12_000, out["elapsedMs"]
    # Backing off rather than hammering: nine tries across ten seconds, not forty.
    assert 4 <= out["healthzTries"] <= 12, out["healthzTries"]
