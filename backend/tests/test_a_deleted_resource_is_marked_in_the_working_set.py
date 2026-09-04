"""A working-set row whose Resource Domino no longer holds says so (#161, ADR-0034).

Domino Resources are shared and another user can delete one at any time, while the working set is a
durable record rather than a cache — so a membership row can outlive the Resource it names by
weeks. Nothing reconciled the two: `add_project_resource` trusts the row the client sends, and
`list_project_resources` enriches each row with `usedBy` without ever asking whether the Resource
still exists.

The reconciliation is a subtraction in the browser — members MINUS the platform listing, the other
half of the one `catalogueParents` already computes — so nothing on a route and nothing in the
membership file can be read to check it. This drives the real store through its own `setScope`, so
the membership read and the listing read race the way they really do, and reads the marks off the
real rail, the real row menu and the real @ menu.

The trap the whole decision turns on: the listing fails PER KIND and one kind cannot be checked at
all, so "absent from the listing" does not mean "deleted". Three of the tests below are about
absence that must NOT be read as death.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "working_set_liveness_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
    """One arrival of the platform listing, and the working set drawn against it."""
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


def _marks(report: dict) -> dict:
    return {row["name"]: row["mark"] for row in report["rows"]}


def test_a_resource_absent_from_a_listed_kind_is_marked_missing():
    """The bug. `Retired rows` is in the project and no longer on Domino, and the rail said nothing.

    Marked, never removed: the creator picked that Resource deliberately, an app may still bind it,
    and a row that vanished overnight would leave nobody anything to act on.
    """
    marks = _marks(_act("listed"))
    assert marks["Retired rows"] == "not in Domino"
    assert marks["Old warehouse"] == "not in Domino"
    # A live row beside each dead one, so the mark is a verdict rather than a stamp on everything.
    assert marks["Sales rows"] is None
    assert marks["Warehouse"] is None
    assert marks["Risk scorer"] is None


def test_a_child_takes_its_parents_word():
    """The listing fetches parents only, so a pinned table is a level below anything it reads.

    A table under a Data Source that is gone is certainly unreachable. The converse — a table
    dropped from a Data Source that still exists — is not covered and would cost a cascade call per
    row on every scope load.
    """
    liveness = _act("listed")["liveness"]
    assert liveness["legacy_orders"] == "missing", "its Data Source is gone"
    assert liveness["orders"] == "live"


def test_a_kind_that_errored_marks_none_of_its_rows_and_says_so_above_them():
    """One outage must not turn into a rail claiming the creator's whole working set is gone.

    The language-model leg refuses while the rail still holds two of its rows, which is the case
    that had nowhere to appear: `listingError` was rendered only in the empty state, so a kind that
    errored AND has rows said nothing at all. The sentence is the kind's, so it goes at the group
    head — stamping it on twelve rows would say it twelve times.
    """
    report = _act("errored")
    marks = _marks(report)
    assert marks["Retired scorer"] is None, "absent because nobody could look, not because it died"
    assert marks["Risk scorer"] is None
    assert report["liveness"]["Retired scorer"] == "unchecked"
    assert report["notes"] == ["Could not list language models."]
    # The other kinds answered, so their verdicts stand. A refusal in one leg is not a blackout.
    assert marks["Retired rows"] == "not in Domino"


def test_a_model_api_absent_from_a_successful_listing_is_never_marked():
    """`list_model_apis` skips any non-home project that fails and caps the fan-out at twenty-five.

    So it returns 200 with no error while permanently missing whole projects' worth of rows. A
    creator on thirty projects has five whose Model APIs would be declared dead forever. `Churn
    scorer` is absent from a listing that reported complete success and still must not be marked —
    a false death is a new harm where a false life is an old one.
    """
    report = _act("listed")
    assert _marks(report)["Churn scorer"] is None
    assert report["liveness"]["Churn scorer"] == "unchecked"
    # And no sentence about it either: the cap is not the dependency refusing to answer, so
    # "we could not check" stays a state rather than becoming a line somebody has to read.
    assert report["notes"] == []


def test_nothing_is_checked_until_the_listing_lands():
    """`state.resourceListing` is null between a project switch and the deferred read arriving.

    Every row is `unchecked` there, and none is marked. Collapsing that into `live` would call rows
    good off a listing nobody has, and into `missing` would mark the whole working set dead for one
    round trip on every project switch.
    """
    report = _act("cold")
    assert set(report["liveness"].values()) == {"unchecked"}
    assert [row["mark"] for row in report["rows"]] == [None] * len(report["rows"])
    assert report["notes"] == []


def test_a_listing_that_could_not_be_read_at_all_marks_nothing():
    """The failure mode the whole decision was taken to avoid, reached by the other door.

    A leg that refuses answers with an error string keyed on its own kind, and `stampLiveness` reads
    that key. A read that faults outright does not: `refreshResourceListing` writes a synthetic
    listing whose error is keyed on the whole listing and whose `groups` is empty, so every per-kind
    error lookup misses and every kind looks like a successful empty answer. The next working-set
    change re-applies it — and that would mark every Dataset, Data Source and Alias in the project
    dead at once, off a listing nobody ever got.
    """
    report = _act("unreadable")
    assert set(report["liveness"].values()) == {"unchecked"}
    assert [row["mark"] for row in report["rows"]] == [None] * len(report["rows"])


def test_giving_the_binding_back_lets_a_stuck_row_leave_the_project():
    """The stuck row's exit has to actually be an exit.

    `usedBy` is computed per read by `list_project_resources` off the apps' own manifests, and the
    unbind route answers with the APP's list rather than the Project's. So without a fresh
    membership read the row would go on naming an app that no longer binds it — hiding the Project
    removal behind a door the creator has already walked through, with no way out but a reload.
    """
    report = _act("unbind")
    assert report["stuckRemovals"] == ["Remove from quick-start"]
    # Still marked, because Domino still does not hold it. Only the way out changed.
    assert _marks(report)["Bound rows"] == "not in Domino"


def test_a_missing_row_is_still_offered_and_carries_its_mark_where_it_is_picked():
    """It informs; it does not block.

    Making the @ menu refuse would be Sage's third refusal, added to prevent one the creator gets a
    step later anyway from code that knows more about the failure than a picker does. So the row
    stays selectable and says why at the point of picking.
    """
    report = _act("listed")
    assert report["mentionRetired"] == [
        {"name": "Retired rows", "mark": "not in Domino"},
        {"name": "Retired scorer", "mark": "not in Domino"},
    ]
    assert report["mentionLive"] == [{"name": "Sales rows", "mark": None}]
    # And a kind nobody could check offers the same row unmarked, rather than withholding it.
    assert _act("errored")["mentionRetired"][1] == {"name": "Retired scorer", "mark": None}


def test_a_missing_row_an_app_still_binds_points_at_the_apps_own_door():
    """Gone from Domino and still bound is stuck: `remove_project_resource` answers 409.

    That refusal stays. The Binding belongs to the app and removal lives with the list that owns the
    scope (ADR-0011), so unbinding is the app's act — and relaxing the guard for a dead Resource
    would silently break an app that still ships the Binding. What changes is only which door the
    row's action points at: the app's, rather than the Project removal that is certain to be
    refused.
    """
    report = _act("listed")
    assert report["stuckRemovals"] == ["Remove from Sales trends"]
    # Pressing it lands on that app, where its Bindings list is.
    assert report["routes"] == ["#/build/conv_1?app=app_a"]
    # A dead row nothing binds can just leave, so it keeps the ordinary door.
    assert report["freeRemovals"] == ["Remove from quick-start"]


def test_liveness_is_computed_on_read_and_never_written_down():
    """The `usedBy` precedent, and for the same reason: a stored copy is wrong the moment anybody
    deletes anything.

    A whole scope load, a listing applied over it and a rail drawn from the result write nothing at
    all. A liveness kept in the membership file, or computed by a route, could not be.
    """
    assert _act("listed")["writes"] == []
