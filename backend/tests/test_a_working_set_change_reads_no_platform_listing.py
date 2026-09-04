"""A change to the working set must not re-read the platform (#162).

Every mutation in the store went through `loadScopeData`, the only refresh primitive there was, and
that load ends every call with `/api/resources` and `/api/assets` — the pair measured at 5.10 s on a
real deployment (#160). Adding a Resource to the Project cannot change what Domino holds, so the
5.1 s answered a question the click did not put. So did removing one, pinning a leaf, and unpinning
one.

The trigger is not greppable from the call site: `addToProject` says `refreshWorkingSet()` and
whether that reaches the platform is a property of the function it names. So this runs the real
store against a real `SW.api` and reports the URLs that left the browser during each act.

The two lists the skipped read used to move are reported with them. `catalogueParents` is the
platform's rows MINUS the working set — the catalogue half of the @ menu — so a refresh that got
fast by leaving it alone would leave a row somebody just added still offered as something to add.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "working_set_refresh_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
    """One act against the store, from a project that has finished loading."""
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


def _platform_reads(out: dict) -> list:
    """The two legs of the platform listing, whenever they were asked for.

    Late arrivals count. `loadScopeData` defers its listing read rather than awaiting it, so an act
    that looked quiet while it ran can still fan out a tick after it returned.
    """
    return [
        url for url in out["requests"]
        if url.endswith("/api/resources") or url.endswith("/api/assets")
    ]


def test_adding_a_resource_asks_the_platform_for_nothing():
    """The act the issue was opened on."""
    out = _act("add")
    assert _platform_reads(out) == []
    # The membership file it does read, so the rail agrees the row is in the project.
    assert out["rail"] == ["Risk history", "Sales rows"]


def test_the_added_row_leaves_the_catalogue_half_of_the_mention_menu():
    """`catalogueParents` is the listing minus the working set, and the working set just changed.

    Without the listing re-APPLIED against the new membership, the row would go on being offered as
    something to add to a project it is already in — the fetch was carrying that recount.
    """
    out = _act("add")
    assert out["catalogueParents"] == ["Warehouse"]
    # And re-applied rather than dropped: nothing is on the way to refill it here.
    assert out["listingHeld"] is True


def test_removing_a_resource_asks_the_platform_for_nothing():
    """A Remove cannot change what Domino holds either, and it has the same recount to do.

    In the other direction: the row leaves the project, so the catalogue half of the @ menu has to
    offer it again.
    """
    out = _act("remove")
    assert _platform_reads(out) == []
    assert out["rail"] == []
    assert out["catalogueParents"] == ["Risk history", "Sales rows", "Warehouse"]


def test_pinning_a_leaf_asks_the_platform_for_nothing():
    """Pinning goes through `addToProject`, which joins the parent to the project on the way in.

    So the parent has to reach the rail and leave the catalogue, both off the listing in hand.
    """
    out = _act("pin")
    assert _platform_reads(out) == []
    assert out["rail"] == ["Sales rows", "Warehouse"]
    assert out["catalogueParents"] == ["Risk history"]


def test_unpinning_the_last_leaf_asks_the_platform_for_nothing():
    """And the parent leaves with its last pin, which is the same recount backwards."""
    out = _act("unpin")
    assert _platform_reads(out) == []
    assert out["rail"] == ["Sales rows"]
    assert out["catalogueParents"] == ["Risk history", "Warehouse"]


def test_a_scope_loads_older_snapshot_does_not_land_on_top_of_the_change():
    """The refresh reads `scopeLoad` rather than bumping it, and that is what this pays for.

    A scope load defers its `/project` read behind the 5.1 s listing, so the answer it is holding
    was taken before anything the viewer has done since. Bumping `scopeLoad` to drop it would
    cancel the listing read with it and leave Browse Domino spinning with nothing to refill it, so
    the `/project` read gets a generation of its own instead.

    The act: switch project, then add a Resource while that listing is still out, with an Upload
    appearing in between. Without the generation the Upload is written by the refresh and then
    wiped when the scope load's older snapshot lands.
    """
    out = _act("race")
    assert out["files"] == ["new.csv"]
    assert out["rail"] == ["Risk history", "Sales rows", "new.csv"]
    # And the listing the scope load was waiting on still landed — the guard drops one half of that
    # answer, not the read.
    assert out["listingHeld"] is True
    assert out["catalogueParents"] == ["Warehouse"]


@pytest.mark.parametrize("act", ["overlap", "stale-load"])
def test_a_scope_load_of_the_same_project_does_not_undo_the_change(act):
    """The other half of what reading `scopeLoad` rather than bumping it costs, in both orderings.

    A same-scope `loadScopeData` is what the four Dataset-folder acts still run, and one can be in
    flight when somebody clicks Add. Its membership read was taken before the Add, so it holds a
    rail without that row in it — and because a mutation no longer cancels it, that snapshot is
    free to land. `overlap` lets it answer first, `stale-load` holds it back so it answers last.

    Either way the row has to stay in the rail and out of the @ menu's catalogue half: the server
    has it in the Project, and a rail that disagrees goes on offering it as something to add until
    the next project switch.
    """
    out = _act(act)
    assert out["rail"] == ["Risk history", "Sales rows"]
    assert out["catalogueParents"] == ["Warehouse"]


def test_a_superseded_scope_change_still_drops_the_last_projects_catalogue():
    """The clear hangs off the scope changing, not off which membership read turned out to be newest.

    `catalogueParents` is the complement of the members, so the two have to be dropped together
    when the project changes. Tie the clear to the membership write and a mutation inside the
    switch's own window skips it: the write is superseded, and the @ menu goes on offering the rows
    of the project the viewer has just left for the length of the deferred listing.

    `Left behind` is in no platform listing, so nothing recomputed can put it back — it is only
    there if it was carried across the switch.
    """
    out = _act("switch-race")
    assert out["mid"] == []
    # And the fresh listing still refills it against the new project's membership.
    assert out["catalogueParents"] == ["Warehouse"]
    assert out["rail"] == ["Risk history", "Sales rows"]


def test_switching_project_still_reads_the_whole_platform():
    """The read the narrow refresh drops is the one a scope change exists to make.

    A different project is read against a different membership, and nothing in the store carries
    the answer — so this one has to ask, and `/api/members` with it.
    """
    out = _act("switch")
    assert sorted(_platform_reads(out)) == ["GET ./api/assets", "GET ./api/resources"]
    assert "GET ./api/members" in out["requests"]
