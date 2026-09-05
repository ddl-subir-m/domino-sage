"""Browse Domino must not re-read a platform it already loaded (#159).

The modal used to fetch on an effect keyed on the search box: every keystroke, behind a 140 ms
debounce, fanned out to `/api/resources` and `/api/assets` and then filtered the answer in the
browser. So each round trip returned exactly what the one before it had already returned, and the
same listing was sitting in the store the whole time — `loadScopeData` reads it for the rail and
the @ menu.

None of that is visible in the source. "Where do the rows come from" is a claim about which calls
leave the browser as somebody types, and the window after a project switch — when the store's
listing has been cleared and the fresh one has not landed — is a claim about what the list says
while nothing is in it. An empty catalogue drawn then would read as "Domino holds nothing you can
add", which is the one thing that is not true.

So the harness runs the real component with the real store and the real `SW.api`, fires the real
controls, and reports the URLs that left the browser during each act.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "catalog_reads_once_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
    """One act against Browse Domino, and the modal as somebody would next see it."""
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": act}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_modal_opens_on_rows_rather_than_on_a_wait():
    """The listing is already in the store, so there is nothing to wait for."""
    out = _act("open")
    assert out["onOpen"]["rows"] == [
        "Sales rows",
        "Risk history",
        "Warehouse",
        "Risk scorer",
    ]
    assert out["onOpen"]["skeleton"] is False
    assert out["onOpen"]["empty"] is False
    # Membership still comes through: the row the project already has offers no Add.
    assert out["onOpen"]["inProject"] == ["Sales rows"]


def test_opening_refreshes_the_listing_once_and_the_rows_follow():
    """These are shared Resources — somebody else can add one — so an open re-reads Domino.

    Once, in the background, with the rows on screen throughout. `Risk appetite` exists only in
    the platform's answer, so a row that appears is the refresh reaching the screen.
    """
    out = _act("open")
    assert out["onOpen"]["requests"] == 2, "one refresh is /api/resources plus /api/assets"
    assert "Risk appetite" in out["afterRefresh"]["rows"]
    assert out["afterRefresh"]["requests"] == 0, "the refresh must not restart itself"


def test_typing_in_the_search_box_reaches_no_network():
    """The bug the issue was opened on. A keystroke is a filter over rows already in memory."""
    out = _act("type")
    assert out["afterAct"]["rows"] == ["Risk history", "Risk appetite", "Risk scorer"]
    assert out["afterAct"]["requests"] == []
    # And nothing arrives late either: a debounced fetch would have landed in this window.
    assert out["lateRequests"] == []


def test_the_sidebar_counts_say_where_the_matches_are():
    """A kind offering `12` must not open on nothing once a search is standing."""
    out = _act("type")
    counts = out["afterAct"]["counts"]
    assert counts["dataset"] == 2
    assert counts["model_llm"] == 1
    assert counts["datasource"] == 0


def test_picking_a_kind_reaches_no_network():
    out = _act("kind")
    assert out["afterAct"]["rows"] == ["Risk scorer"]
    assert out["afterAct"]["requests"] == []
    assert out["lateRequests"] == []


def test_the_window_after_a_project_switch_is_a_spinner_not_an_empty_platform():
    """The store's listing is cleared on a scope change and refilled by a deferred read.

    A modal opened inside that window holds nothing. Drawing it as an empty catalogue would tell
    somebody their platform is bare.
    """
    out = _act("cold")
    assert out["onOpen"]["skeleton"] is True
    assert out["onOpen"]["empty"] is False
    assert out["onOpen"]["rows"] == []
    # And it fills itself from the one background read, without a second path of its own.
    assert out["onOpen"]["requests"] == 2
    assert "Risk appetite" in out["afterRefresh"]["rows"]
    assert out["afterRefresh"]["requests"] == 0


def test_adding_a_resource_does_not_blank_the_catalogue_it_was_added_from():
    """Add refreshes the working set, and the scope load is where the listing is dropped.

    Dropped unconditionally, the rows under the click are replaced by the spinner for the length of
    a round trip — the flicker this whole change exists to remove, arriving through a different
    door. The harness redraws on every notify, because a blank between two notifies is one somebody
    watching the screen still sees.
    """
    out = _act("add")
    assert out["afterAct"]["everBlanked"] is False
    assert "Risk history" in out["afterAct"]["rows"]
    # Both members afterwards: the one that already was, and the one just added.
    assert out["afterAct"]["inProject"] == ["Sales rows", "Risk history"]


def test_adding_a_resource_reads_the_project_rather_than_the_platform():
    """An Add cannot change what Domino holds, so it has no reason to ask (#162).

    The pair it used to ask through `loadScopeData` is the 5.1 s read, and it answers a question
    the click did not put. Membership is a local file, and the listing the row was clicked in is
    already in the store — re-applied against the new membership rather than re-read, which is what
    moves the row out of the @ menu's catalogue half with no round trip.
    """
    out = _act("add")
    asked = out["afterAct"]["requests"] + out["lateRequests"]
    assert not [u for u in asked if u.endswith("/api/resources") or u.endswith("/api/assets")], asked
    # The membership file, and `/project` for the app's manifest and the Uploads. Nothing else.
    assert [u.rsplit("/api/", 1)[-1] for u in asked] == [
        "project/resources",
        "project/resources",
        "project",
    ]


def test_the_newest_listing_read_wins():
    """Two reads are routinely in the air: the one a scope load defers and the one an open fires.

    The older answering last would put back a Dataset somebody has since deleted — the store would
    end up describing a platform that no longer exists. `Deleted set` is only in the slow answer.
    """
    out = _act("stale")
    assert "Deleted set" not in out["afterRefresh"]["rows"]
    assert "Risk appetite" in out["afterRefresh"]["rows"]


def test_a_platform_that_would_not_answer_is_not_a_platform_with_nothing_in_it():
    """`fetchDominoListing` answers a refusal with error strings rather than throwing.

    Drawn as an empty catalogue, that reads as "Domino holds nothing you can add". It also has to
    stop spinning: the modal has no retry, so a wait with nothing left to end it is a dead end.
    """
    out = _act("unreadable")
    assert out["onOpen"]["skeleton"] is True, "it waits while the read is out"
    assert out["afterRefresh"]["skeleton"] is False, "and stops waiting when the read comes back"
    assert out["afterRefresh"]["rows"] == []
    assert "Could not list" in (out["afterRefresh"]["emptyText"] or "")


def test_a_listing_that_half_answered_says_which_half():
    """Datasets refused while everything else answered. There are rows, so the failure cannot be
    said in the empty state — it goes above the list, or a whole kind is silently missing."""
    out = _act("partial")
    assert out["afterRefresh"]["rows"] == ["Warehouse", "Risk scorer"]
    assert out["afterRefresh"]["note"] == "Could not list Datasets."


def test_a_kind_that_could_not_be_re_read_keeps_the_rows_it_had():
    """Opening the catalogue re-reads Domino, and a leg of that read can refuse.

    A refusal answers with an error string and no rows, which is not the same fact as a kind Domino
    no longer holds. Applied as it stands it would empty the promote picker and the catalogue half
    of the @ menu until the next project switch — triggered by nothing more than opening a modal.
    """
    out = _act("partial-warm")
    assert out["afterRefresh"]["rows"] == [
        "Sales rows",
        "Risk history",
        "Warehouse",
        "Risk scorer",
    ]
    # And the rows are not passed off as fresh: the line above the list says the read failed.
    assert out["afterRefresh"]["note"] == "Could not list Datasets."


def test_a_search_that_matches_nothing_says_so_even_while_a_leg_is_refusing():
    """Two different reasons for an empty list, and the filter is the one that applies.

    With a query standing, a refusal that has nothing to do with what was typed must not take over
    the sentence — it is said above the list instead, where it stays true.
    """
    out = _act("no-match")
    assert out["afterAct"]["rows"] == []
    assert out["afterAct"]["emptyText"] == 'Nothing in Domino matches "zzz".'
    assert out["afterAct"]["note"] == "Could not list Datasets."
    assert out["afterAct"]["requests"] == []
