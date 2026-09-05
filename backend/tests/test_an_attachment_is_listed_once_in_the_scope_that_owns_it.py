"""An Attachment appears in ONE place: the app that records it (#148, ADR-0011).

WHAT WAS WRONG. `store.js` built one `file` group out of two different scopes' lists — the
Project's Uploads (`project.scratch`) and the selected app's Attachments (`project.attached`) — so
"In this project → Files" listed rows the Project does not own, and "In {app} → Attachments" listed
the same records again. After a crossing (#147) one file was therefore drawn three times in Build:
an Upload under the Project, an Attachment under the Project, and that Attachment under the app.
#147 made the three tell each other apart; this removes the one that should never have been there.

WHY IT IS NOT A DELETION. The `public/data/…` row was load-bearing. `collectTurnRefs` walks
`state.resourceGroups` to turn "@data.csv" in the Build composer into a path the turn can carry, so
dropping the row and putting nothing in its place makes every @mention of the app's own data file a
plain word again — silently, with no refusal and no warning, because the compose-time guard keys the
app's list by BASENAME and goes on suppressing a warning about a mention that no longer resolves.
That is why the claim below is about the path a turn carries and not about a row being on screen:
`test_the_project_no_longer_lists_the_app_s_attachment` passes on that bug, and
`test_a_mention_of_an_attachment_still_reaches_the_turn_as_a_path` is the only thing that fails.

THE READ. `attachment_scope_harness.mjs` moves into a Project whose `/project` answers with both
lists, then reads four surfaces off that one arrival: the panel's groups, the app's own list, the
composer's @ menu, and the body of a real Build turn. One fixture, four readers — which is the shape
of the bug.

WHERE THE APP'S LIST IS. It was a section in the panel; it is the App dependencies modal since #151
([ADR-0032](../../docs/adr/0032-the-panel-is-the-projects-one-list.md)). That move is what makes
"one row per scope" visible rather than merely true — the two scopes are two surfaces now, and a
row in the wrong one has nowhere to hide.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "attachment_scope_harness.mjs"
WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

# The Project's two Uploads and the app's two Attachments, as the harness fixture writes them.
UPLOAD = ".sage/scratch/notes.csv"
CROSSED = ".sage/scratch/margins.csv"
ATTACHMENT = "public/data/desks/margins.csv"
# An Attachment whose basename collides with Sage's own guardrail file, so it is hidden wherever a
# person picks — see `test_an_attachment_hidden_from_the_explorer_is_still_hidden_from_the_menu`.
HIDDEN = "public/data/desks/AGENTS.md"
APP = "Desk margins"

# Every file by name, so a mention has to be resolved rather than guessed: `margins.csv` names the
# Upload AND the Attachment it crossed into, `notes.csv` names an Upload that never crossed, and
# `AGENTS.md` names an Attachment no menu offers.
PROMPT = "update @margins.csv and @notes.csv, read @AGENTS.md"


def _run(prompt: str = PROMPT) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"prompt": prompt}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _group(report: dict, title: str) -> dict:
    """One group of the Project's list, by the label over it."""
    found = [s for s in report["panel"] if s["title"] == title]
    assert len(found) == 1, f"{title} appears {len(found)} times: {report['panel']}"
    return found[0]


def _app_group(report: dict, title: str) -> dict:
    """One group of the app's own list, on the surface that owns that scope."""
    found = [s for s in report["appPanel"] if s["title"] == title]
    assert len(found) == 1, f"{title} appears {len(found)} times: {report['appPanel']}"
    return found[0]


# ---- one row per scope -------------------------------------------------------------------------


@needs_node
def test_the_project_no_longer_lists_the_app_s_attachment():
    """The Project's working set is its Uploads. An Attachment is a record the app keeps, and a
    Project row for one is the Project claiming something it does not own."""
    report = _run()

    assert [f["path"] for f in report["files"]] == [UPLOAD, CROSSED]
    assert all(f["source"] == "scratch" for f in report["files"])
    assert ATTACHMENT not in [f["path"] for f in report["files"]]


@needs_node
def test_the_app_s_own_list_is_the_one_place_the_attachment_appears():
    """The other half of the same claim, read off the screen rather than off the store: after a
    crossing, `margins.csv` stands once under the Project as an Upload and once under the app as an
    Attachment — two scopes, two different files, and no third row."""
    report = _run()

    assert _app_group(report, "Files it carries")["rows"] == ["margins.csv", "AGENTS.md"]
    assert _group(report, "Files")["rows"] == ["notes.csv", "margins.csv"]
    # And the app's list is not in the panel at all — the two scopes are two surfaces (ADR-0032).
    assert [g["title"] for g in report["panel"]] == ["Data", "Files"]


@needs_node
def test_each_head_counts_what_its_own_list_holds():
    """The counts are the summary a person reads before opening anything, so a row moving scope has
    to move the number with it. The Files count was 4 for two Uploads."""
    report = _run()

    assert _group(report, "Files")["count"] == "2"
    assert _app_group(report, "Files it carries")["rows"] == ["margins.csv", "AGENTS.md"]
    # The Data group holds the one Dataset and nothing else — which is what says the Attachment did
    # not land in the Project's list on its way out.
    assert _group(report, "Data")["count"] == "1"


# ---- and the row is still reachable --------------------------------------------------------


@needs_node
def test_the_menu_still_offers_the_app_s_attachment_by_its_path():
    """The @ menu reads the app's section now, so an Attachment is still one keystroke away — and
    the row is identified by the path behind its click, because an Upload and the Attachment it
    crossed into say the same word."""
    report = _run()

    assert report["menu"] == ["Desk margins", "margins.csv", "margins.csv"]
    assert report["picked"] == [CROSSED, ATTACHMENT]


@needs_node
def test_a_mention_of_an_attachment_still_reaches_the_turn_as_a_path():
    """The claim the row could not simply be deleted for. `_resolve_mentions` honors exactly the
    paths in the app's manifest, so the turn has to carry `public/data/…` — a mention that arrives
    as a word reaches the agent as a word, with nothing refused and nothing warned."""
    report = _run()

    assert len(report["sent"]) == 1
    assert report["sent"][0]["prompt"] == PROMPT
    assert ATTACHMENT in report["sent"][0]["mentions"]
    # The Project's own rows still resolve too: the app's list was ADDED to the walk, not swapped in.
    assert report["sent"][0]["mentions"] == [UPLOAD, CROSSED, ATTACHMENT]


@needs_node
def test_an_attachment_hidden_from_the_explorer_is_still_hidden_from_the_menu():
    """The filter that came off with the Project's row, kept rather than dropped.

    `isHiddenFromExplorer` matches on BASENAME, so a Dataset file called `AGENTS.md` lands at
    `public/data/<slug>/AGENTS.md` and is hidden — the Chat explorer is the project's pickable
    working set, not the repo. That filter used to be applied by the Project's `file` group, which
    is the group this issue emptied, so without `SW.util.attachmentRows` re-applying it the row
    would have quietly become mentionable on the way through. The app still LISTS it, because that
    list is the app's manifest and always showed it."""
    report = _run()

    assert HIDDEN in report["appAttachments"]
    assert "AGENTS.md" in _app_group(report, "Files it carries")["rows"]
    # And nowhere a person picks: not in the menu, not on the wire.
    assert report["menuAgents"] == []
    assert HIDDEN not in report["sent"][0]["mentions"]


@needs_node
def test_the_compose_time_warning_still_reads_the_app_s_list_by_basename():
    """Untouched by this change and asserted rather than assumed (#136): the guard keys
    `appAttachments` by basename, which is independent of the working set. So it stays quiet about
    the Upload that has crossed and speaks up about the one that has not."""
    report = _run()

    assert [e["id"] for e in report["warned"]] == [UPLOAD]
    assert all(e["app"] == APP for e in report["warned"])


# ---- one derivation ----------------------------------------------------------------------------


def test_the_row_the_panel_draws_is_the_row_the_menu_offers_and_the_turn_resolves():
    """Three surfaces read the same record now, and a second copy of "an Attachment as a row" is how
    the menu comes to offer something the turn cannot resolve. `SW.util.attachmentRow` is the one."""
    util = (WB / "js" / "util.js").read_text()
    store = (WB / "js" / "store.js").read_text()
    # The app's own list, which is where the Attachment row is drawn since ADR-0032. It derived the
    # basename itself while it was a summary beside the panel's copy; now that it is the only copy,
    # it has to be the same derivation as the menu's and the turn's.
    builder = (WB / "js" / "modes" / "builder.js").read_text()
    composer = (WB / "js" / "components" / "composer.js").read_text()

    assert "attachmentRow(entry) {" in util
    assert "SW.util.attachmentRow(a).name," in builder
    assert "const attached = SW.util.attachmentRows(appAttachments);" in composer
    assert "SW.util.attachmentRows(state.appAttachments)].forEach((rows) => {" in store
    # The path is the field the server keys on, so it cannot be dropped from the row on the way out.
    assert "id: `file:${path}`," in util
    # The plural is the singular plus the pick filter, and the two menus that pick share it — one
    # of them re-deriving the map would be how the filter comes off one door and not the other.
    assert ".filter((e) => !SW.util.isHiddenFromExplorer(e && e.path))" in util
    assert "(appAttachments || []).map(SW.util.attachmentRow)" not in composer


def test_the_project_s_file_group_is_built_from_one_list():
    """The bug in one line: `files` was a concatenation of two scopes' lists. A `project.attached`
    reader here is that shape coming back."""
    store = (WB / "js" / "store.js").read_text()
    files = store[store.index("const files = (project.scratch"):store.index("applyResourceGroups(\n        SW.api.overlayResourceListing(")]

    assert "project.attached" not in files
