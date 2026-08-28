"""The Build header reports the app's state and names its controls honestly (#87).

#86 left three small things behind, and this is all three.

THE STATE LINE. A Built App has no "Running" state. The design proposal borrowed the word from
the Gallery, which suppresses it deliberately — "a card that says Running on every tile teaches
nothing" (`modes/gallery.js:101`). Three states are real and each has a producer: the preview
process (`previewStatus`), the build turn (`app.building`, #77) and the remote being ahead
(`app.behind`, #78). The header reports all three in those words. The last two are on the app
list's rows as well, and that repetition is the point: the rows are behind a click, and the app in
the preview is the one nobody should have to open a menu to ask about.

THE ASSERTIONS NAME A CONTROL, NOT THE SCREEN. Once the header reports the preview, two things on
the same screen say "Starting preview…" — the canvas overlay, which has said it since Build was
built, and the header. `parts` is what tells them apart: it carries each element's own className
beside the strings directly under it, so "the header says it" is a claim about a control rather
than about a substring somewhere in the tree.

THE RESERVED ROW. #85 is parked until the resource model is settled, and it asked where the app's
own row lives. This ticket answers only the layout half — the header has a place for it, beneath
the app identity row, app-scoped and named for the app it belongs to. What goes in it is #85's,
and no Binding is read here. The reason it cannot wait is the problem #85 was filed about: the
composer's Session context chips are Conversation-scoped (#84, `CONTEXT.md:176-177`), and a
Conversation-scoped row flush against an app-scoped pane with nothing to tell them apart is what
made someone read `market-data-eod` as something the news app uses.

Nothing mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_WORKBENCH = Path(__file__).resolve().parents[1] / "sage" / "workbench"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _run(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _build(thread: str = "thr_many", select: str | None = None, **extra) -> dict:
    return _run([{"build": thread, "select": select, **extra}])[-1]


def _said(step: dict) -> str:
    return " ".join(step["words"])


def _texts(step: dict, prefix: str) -> list[str]:
    """What the elements whose class starts with `prefix` said, and nothing else on the screen."""
    return [t for p in step["parts"] if p["className"].startswith(prefix) for t in p["texts"]]


# ---- the state line ---------------------------------------------------------------------------


@needs_node
def test_the_header_says_the_preview_is_starting():
    """The overlay over the canvas says it too, and that is not the same claim: the overlay is on
    the pane, and what the header has to answer is what state the app you selected is in."""
    assert "Starting preview…" in _texts(_build(select="app_a", preview="starting"), "sw-build-state")


@needs_node
def test_the_header_says_the_preview_did_not_start():
    assert "Preview didn’t start" in _texts(_build(select="app_a", preview="err"), "sw-build-state")


@needs_node
def test_the_header_says_nothing_about_a_preview_that_is_live():
    """The state the header must NOT report, because it is the one it cannot keep current.
    `probePreview()` runs from `loadBuild()` and `refreshPreview()` only, and `PreviewPane`'s poll
    stops as soon as the status leaves `starting` — so nothing re-reads a live preview. A process
    that dies mid-session leaves `ok` behind it, and a word for `ok` is a claim nobody is checking.

    The argument for keeping it was that a person who never meets the word cannot read its absence.
    That does not hold here: `Starting preview…` is on screen during every first load, so the
    vocabulary is met before the silence is."""
    assert _texts(_build(select="app_a", preview="ok"), "sw-build-state") == []


@needs_node
def test_the_header_claims_nothing_about_a_preview_that_was_never_asked_for():
    """`previewStatus` starts `idle` and first paint happens before the probe lands. Reporting
    `live` there would be the header inventing the answer it is there to report."""
    assert _texts(_build(select="app_a", preview="idle"), "sw-build-state") == []


@needs_node
def test_the_header_claims_no_preview_state_while_no_app_is_named():
    """`AppBar` returns early only when the Project has NO apps, and `activeApp` is null whenever
    the server flags none selected or `clearApp()` has run — the same state #82's count test uses.
    A preview word in that row sits among app-scoped chips beside `Choose a Built App`, which
    reads as a claim about an app nobody has picked.

    `starting`, not `ok`: since the header went silent on a live preview, `ok` would leave this
    empty whether or not the `activeApp` guard is there, and the test would pass on a bug."""
    assert _texts(_build("thr_many", preview="starting", unselected=True), "sw-build-state") == []


@needs_node
def test_the_header_says_when_a_build_is_running_in_the_selected_app():
    """#77: a build the person walked away from goes on running. The app list says so on the row,
    but that row is behind a click — the app in the preview is the one you did not have to open a
    menu to ask about."""
    assert "Building…" in _texts(_build(select="app_c"), "sw-build-state")


@needs_node
def test_the_header_does_not_say_a_build_is_running_in_an_app_that_is_not_building():
    """`app_a` is built and idle. A state line that fires on every app is the Gallery's Running
    tag again, one row further up."""
    assert "Building…" not in _texts(_build(select="app_a"), "sw-build-state")


@needs_node
def test_the_header_says_when_a_teammate_has_pushed_to_the_selected_app():
    """The third of the ticket's three real states (#78). It is on the row as well, and the row is
    behind a click — which is the same reason the build turn is repeated up here."""
    assert "Changes to pull" in _texts(_build(select="app_d"), "sw-build-state")


@needs_node
def test_the_header_says_nothing_about_a_teammate_who_has_not_pushed():
    assert "Changes to pull" not in _texts(_build(select="app_a"), "sw-build-state")


@needs_node
def test_the_build_and_the_preview_are_two_facts_and_both_are_said():
    """Different producers, both still true: your turn is writing files AND the preview is
    restarting to show them. #78's badge sits beside #77's on the row for the same reason."""
    said = _texts(_build(select="app_c", preview="starting"), "sw-build-state")
    assert "Building…" in said
    assert "Starting preview…" in said


@needs_node
def test_the_header_never_says_running():
    """The whole of the ticket's first half. `Running` is the Gallery's word for a deployed App,
    and a Built App is not deployed — borrowing it names a state that does not exist."""
    for step in _run(
        [
            {"build": "thr_many", "select": "app_c", "preview": "starting"},
            {"build": "thr_many", "select": "app_a", "preview": "ok"},
            {"build": "thr_many", "select": "app_d", "preview": "err"},
        ]
    ):
        assert "Running" not in _said(step), step["step"]
        assert not any("Running" in label for label in step["labels"]), step["step"]
        assert not any("Running" in title for title in step["titles"]), step["step"]


# ---- the relabel ------------------------------------------------------------------------------


@needs_node
def test_the_open_control_names_the_preview_in_its_label_and_its_tooltip():
    """It opens the local preview, and `Open in a new tab` does not say which of the two doors it
    is. It becomes misleading the moment `Open app` lands (#89), so it says which one now."""
    step = _build()
    assert "Open preview in a new tab" in step["labels"]
    assert "Open preview in a new tab" in step["titles"]


@needs_node
def test_the_open_control_is_not_left_unqualified():
    step = _build()
    assert "Open in a new tab" not in step["titles"]
    assert "Open preview" not in step["labels"]


# ---- the reserved row -------------------------------------------------------------------------


@needs_node
def test_the_header_leaves_a_place_for_the_selected_apps_own_resources():
    """#85 decides what goes in it. This only guarantees it has somewhere to land, so answering
    #85 does not mean reopening the header."""
    assert "sw-app-scope" in _build(select="app_a")["classes"]


@needs_node
def test_that_place_is_app_scoped_and_names_the_app_it_belongs_to():
    """Which is the whole difference from the composer's chips. A row that did not say whose
    resources these are would be the ambiguity #85 was filed about, moved up a pane."""
    a, c = _run([{"build": "thr_many", "select": "app_a"}, {"build": "thr_many", "select": "app_c"}])
    assert any("Desk dashboard" in t for t in _texts(a, "sw-app-scope"))
    assert any("Rate curve viewer" in t for t in _texts(c, "sw-app-scope"))
    assert not any("Desk dashboard" in t for t in _texts(c, "sw-app-scope"))


@needs_node
def test_that_place_belongs_to_an_app_so_there_is_none_without_one():
    """A row headed by no app is a row about nothing, and a Project with no Built Apps is the one
    screen whose only job is `New app`."""
    assert "sw-app-scope" not in _build("thr_none", noapps=True)["classes"]


@needs_node
def test_that_place_is_not_the_composers_session_context_row():
    """Two rows, two scopes. Session context is the Conversation's and must not follow the
    selected app (#84); this row is the app's. The header does not borrow the composer's row, and
    it does not borrow its styling either."""
    source = (_WORKBENCH / "js" / "modes" / "builder.js").read_text()
    assert "sw-composer-chips" not in source
    assert "sw-chip" not in source
    css = (_WORKBENCH / "css" / "builder.css").read_text()
    assert ".sw-app-scope" in css


@needs_node
def test_that_place_sits_between_the_app_identity_row_and_the_preview():
    """Beneath the identity row, because it is about the app that row names; above the canvas,
    because it is part of the header rather than something floating over the app. `classes` is the
    tree in document order, so this is the layout rather than a reading of the source."""
    classes = _build(select="app_a")["classes"]
    assert classes.index("sw-builder-toolbar") < classes.index("sw-app-scope")
    assert classes.index("sw-app-scope") < classes.index("sw-builder-canvas is-live")


# ---- what this ticket must not do -------------------------------------------------------------


@needs_node
def test_nothing_here_adds_a_publish_control_or_an_open_app_control():
    """Both wait for #70 (#89). A publish button with no publish flow behind it is a dead control,
    and `Open app` with nothing deployed is a door onto nothing."""
    step = _build(select="app_a")
    surfaces = step["words"] + step["labels"] + step["titles"]
    assert not any("Publish" in s for s in surfaces), surfaces
    assert not any("Open app" in s for s in surfaces), surfaces


@needs_node
def test_nothing_here_reads_the_conversation_view_preference():
    """#61 deletes everything that does."""
    assert "conversationView" not in (_WORKBENCH / "js" / "modes" / "builder.js").read_text()


# ---- a preview that never comes up (#90) -------------------------------------------------------


@needs_node
def test_build_stops_checking_a_preview_that_never_answers():
    """The give-up is right — polling every 1.5s forever costs something and buys nothing after the
    first minute. What was wrong is that it stopped the polling and left `previewStatus` alone, so
    the screen went on saying `Starting preview…` with nothing behind it checking."""
    step = _build(select="app_a", preview="starting", giveUp=True)
    assert 90000 in step["waits"]
    assert step["previewStatus"] == "stalled"


@needs_node
def test_the_header_stops_saying_a_preview_is_starting_once_it_has_given_up():
    said = _texts(_build(select="app_a", preview="starting", giveUp=True), "sw-build-state")
    assert "Starting preview…" not in said
    assert "Preview never came up" in said


@needs_node
def test_the_canvas_stops_saying_it_too():
    """Both surfaces read the same status, so both had the same dead end."""
    step = _build(select="app_a", preview="starting", giveUp=True)
    said = _texts(step, "sw-preview-overlay")
    assert not any("Starting preview" in t for t in said)
    assert any("stopped checking" in t for t in said)


@needs_node
def test_never_came_up_is_not_the_same_answer_as_came_up_broken():
    """`err` is the preview answering with something bad; this is it never answering. Different
    causes — a first build installing dependencies is slow, a broken one is broken — so the two
    do not share a word."""
    stalled = _texts(_build(select="app_a", preview="starting", giveUp=True), "sw-build-state")
    failed = _texts(_build(select="app_a", preview="err"), "sw-build-state")
    assert stalled != failed
    assert "Preview didn’t start" not in stalled


@needs_node
def test_the_way_to_try_again_is_on_the_overlay_not_only_the_toolbar():
    """The person this is written for has just been told the thing they were waiting for is not
    coming. Sending them to an icon-only Reload at the other end of the row is the part that made
    it a dead end."""
    step = _build(select="app_a", preview="starting", giveUp=True)
    assert "Check again" in step["labels"] or "Check again" in step["words"]


@needs_node
def test_that_overlay_can_actually_be_clicked():
    """`.sw-preview-overlay` is `pointer-events: none` on purpose — an overlay that ate clicks over
    a live preview would be a bug of its own — so the one state carrying a button has to turn them
    back on for itself."""
    step = _build(select="app_a", preview="starting", giveUp=True)
    assert any("is-stalled" in c for c in step["classes"])
    css = (_WORKBENCH / "css" / "builder.css").read_text()
    assert ".sw-preview-overlay.is-stalled" in css
    assert "pointer-events: auto" in css.split(".sw-preview-overlay.is-stalled")[1][:400]


@needs_node
def test_a_preview_that_comes_up_in_time_is_untouched():
    """The give-up only fires while the status is still `starting`. A preview that landed first
    leaves it alone rather than talking over a working pane."""
    step = _build(select="app_a", preview="ok", giveUp=False)
    assert step["previewStatus"] == "ok"
    assert _texts(step, "sw-build-state") == []
