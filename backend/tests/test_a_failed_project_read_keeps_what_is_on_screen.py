"""A `/project` read that failed writes nothing, at every reader of it (#375).

`refreshAppScope` states the rule in its own comment and is the only reader that kept it:

> A read that failed keeps what is on screen. Emptying on a 502 would have the row report an app
> that ships nothing — the same lie as blanking, arrived at by accident.

Three others did not. `loadBuild` caught a dead `/project` into `{}`, `loadScopeData` and
`refreshWorkingSet` caught it into `{ attached: [] }`, and all three then wrote `project.attached
|| []` anyway — so one 502 made the Build header say the selected app ships nothing, for an app
that ships two files.

WHY THE CUT IS AT THE CATCH. The shape the ticket first proposed — `project.attached ? {...} : {}`
— reads correct and fails silently on two of the three sites, because `{ attached: [] }` has the
key and an empty array is truthy. A fabricated successful read cannot be told apart downstream from
a project that genuinely ships nothing, by any guard placed downstream. `null` can: it is
unmistakably "no read", and every caller has to decide rather than coerce. Lines that already do it
that way are `refreshAppScope` and the boot read.

TWO CONDITIONS, TWO PLANTS. A failed read must not EMPTY the list, and it must not REFUSE a good
read still in flight. The second is the half a single plant hides: writing an empty list also
raises the field's watermark (#101), so a good read issued earlier and landing later loses to a
read that answered nothing — and the store ends up holding the model half of one answer beside the
attachment half of another, which is the pairing #101 exists to forbid.

THREE SITES, NOT ONE. The ticket named `loadBuild` and generalised its fix from that one site. The
same shape as #416: a list of readers cannot know it is short. The two the ticket missed are worse,
not better — they are the reads every panel refresh takes.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_BUILD_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_WORKING_SET_HARNESS = Path(__file__).resolve().parent / "js" / "working_set_refresh_harness.mjs"
_STORE = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "store.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _run(harness: Path, payload) -> dict:
    out = subprocess.run(
        ["node", str(harness)],
        input=json.dumps(payload),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _race(name: str) -> dict:
    """One race against Build, from an app that already has its records on screen."""
    return _run(
        _BUILD_HARNESS,
        [{"race": name, "thread": "thr_many", "select": "app_a"}],
    )[-1]


def _act(act: str) -> dict:
    """One act against the panel, from a project that has finished loading."""
    return _run(_WORKING_SET_HARNESS, {"act": act})


# ---- the catches themselves ------------------------------------------------------------------


def test_no_reader_of_project_catches_a_failure_into_a_body():
    """The enumeration, and the reason it is here rather than left to the three behaviour tests
    below: a fourth reader added later gets the fabricated-read shape back for free unless
    something counts them. `null` is the only catch value that says "no read" — every body,
    `{}` and `{ attached: [] }` alike, is a successful read that never happened."""
    src = _STORE.read_text()
    caught = [
        line.strip()
        for line in src.splitlines()
        if "SW.api.project()" in line and ".catch(" in line
    ]
    assert len(caught) == 5, f"the readers of /project moved — found {len(caught)}: {caught}"
    for line in caught:
        assert "catch(() => null)" in line, line


# ---- loadBuild: the site the ticket named ----------------------------------------------------


def test_a_failed_read_does_not_empty_the_apps_files_in_build():
    """The measured symptom. `app_a` ships two files; a 502 on `/project` used to report none."""
    step = _race("project-read-fails")
    assert step["attachments"] == ["margins.csv", "legacy.csv"]


def test_a_failed_read_does_not_refuse_a_good_one_still_in_flight():
    """The second condition. The good read was issued FIRST and lands LAST, so it only wins if the
    failure left the watermark where it found it. A fix that wrote an empty list would claim the
    field with a higher sequence and this good answer would be dropped on arrival — leaving the
    header saying the app ships nothing with nothing on the way to correct it.

    The good read carries a file the screen does not have yet, so the three outcomes are three
    different lists: the good answer installed (three files), the good answer refused (the two
    already on screen), and the failure writing an empty one (none). A claim that only asked
    whether the list survived could not tell the first two apart."""
    step = _race("failed-read-then-good-one")
    assert step["attachments"] == ["margins.csv", "legacy.csv", "q3.csv"]


def test_the_failed_read_does_not_cost_the_app_its_bindings_either():
    """The rest of the same answer, kept as a control: `/bindings` is read beside `/project` and
    did not fail, so a fix that took the whole load down on one dead route fails here."""
    step = _race("project-read-fails")
    assert step["bindings"] == ["Claude Sonnet 4", "Market data EOD", "Churn risk"]


# ---- the two readers the ticket did not name -------------------------------------------------


def test_a_failed_read_in_the_scope_load_keeps_the_apps_files():
    """`loadScopeData`'s deferred read, the second of the three sites.

    Found in review and recorded rather than fixed: `loadScopeData` is also the project SWITCH, so
    this same guard now keeps the LEFT project's app files in `appAttachments` when the read fails
    on a switch. That is the opposite of what the Uploads half does four lines down, and the
    asymmetry is real. It is not visible without two failures, because `loadAppList` sees the
    selection move and cascades to `refreshAppScope`, which writes attachments from a read of its
    own. Keeping a stale list for one round trip is also the lesser of the two errors here — the
    alternative is the empty list this whole ticket is about. No test covers it: the act below is a
    same-project reload, where there is no switch to make."""
    out = _act("load-read-fails")
    assert out["attachments"] == ["margins.csv"]


def test_the_scope_loads_uploads_group_is_still_emptied_by_a_failed_read():
    """Written down rather than left to be rediscovered, because it looks like the bug above and
    is not the same thing.

    The Uploads group is blanked by the scope load's OWN membership write, ~2.5-3.3 s before the
    deferred `/project` read lands, and that read refilling it is the only reason it comes back.
    So at this site there is nothing on screen to keep by the time the failure is known.

    Carrying the rows across the way `refreshWorkingSet` does would be wrong here: `loadScopeData`
    is also the project SWITCH, and the last project's Uploads must go. Telling a switch from a
    same-scope reload is possible — `state.resourceListingScope` already answers it four lines
    down — but it is new machinery on a path that blanks this group for seconds in the ordinary
    case too, so it is not in this fix. This test fails the day someone changes that, which is the
    point of it."""
    out = _act("load-read-fails")
    assert out["files"] == []


def test_a_failed_read_in_the_working_set_refresh_keeps_the_apps_files():
    """`refreshWorkingSet`, the narrow refresh an Add takes — the third of the three sites."""
    out = _act("refresh-read-fails")
    assert out["attachments"] == ["margins.csv"]


def test_the_working_set_refreshs_uploads_group_is_still_emptied_by_a_failed_read():
    """The other half of the same read, and deliberately NOT given the attachments rule.

    An earlier draft of this fix carried the Uploads rows across the membership write so a 502
    could not blank them. Review found three ways that is wrong, and the third is decisive: two
    callers reach here having just had the server confirm an Upload is GONE — `deleteScratchFile`,
    and `addScratchToDataset` moving the bytes onto a Dataset — and neither takes the row off
    screen optimistically. This read is the only thing that does. Keeping the rows would leave a
    deleted file listed underneath its own success toast, and would draw a promoted file twice,
    once in Uploads and once under its new Dataset. `collectTurnRefs` walks this group to turn
    "@name" into a path a turn carries, so a kept-but-dead row hands a turn a path to bytes that
    no longer exist.

    So the group empties here, as it always has. The two halves of one read get opposite rules
    because the callers know different things about them: nobody has told us the app's manifest
    changed, and somebody has just told us the file is gone."""
    out = _act("refresh-read-fails")
    assert out["files"] == []


def test_the_add_that_failed_the_read_still_landed():
    """The control for the pair above. Keeping what is on screen must not mean dropping the act:
    the Dataset the click added is in the working set whatever `/project` answered."""
    out = _act("refresh-read-fails")
    assert "Risk history" in out["rail"]


def test_the_apps_files_never_blink_empty_on_the_way_through():
    """Sampled per paint, not at the end. The panel draws on every `notify`, so a fix that emptied
    the app's list and put it back a tick later would pass an end-state assertion and still
    flicker on screen. Asserted on the attachments rather than the Uploads group, because the
    Uploads group is legitimately empty here — see the test above."""
    out = _act("refresh-read-fails")
    assert [] not in out["attachmentPaints"], out["attachmentPaints"]
