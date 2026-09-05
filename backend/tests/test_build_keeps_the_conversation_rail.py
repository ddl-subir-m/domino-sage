"""One rail in both modes, and app selection in the Build header (#82).

The rail used to swap its contents under you: Conversations in Chat, Built Apps in Build. #50 made
the transcript one Conversation across both modes, which left the furniture beside it saying the
opposite — so Build keeps the Conversation rail, and the app you are looking at is named in the
header beside the preview it controls.

HALF OF THIS IS A MOUNT. `SW.ConversationRail` already took a `mode` prop and already wrote
`#/build/<thread>?app=<id>` for `build`; nothing had ever passed it. So the rail assertions here are
a comparison — the same rows in Build as in Chat — rather than a description of new markup.

THE GUARD THAT MATTERS. `activeApp` stays the single source of the selected app: the header's rows
write the ROUTE, and the store follows the route on the next render. A picker that wrote the store
directly would light a row and snap back, and it would break the transcript's app card, whose
`showing` flag is computed from `activeApp` (#56).

Nothing is mounted — see `js/build_header_harness.mjs` for why.
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


# ---- the rail stops swapping -------------------------------------------------------------------


@needs_node
def test_build_renders_the_conversation_rail_not_the_app_rail():
    """The ticket's first criterion, and the whole reason for the rest of the file."""
    step = _build()
    assert step["railMode"] == "build"
    assert step["appRails"] == 0


@needs_node
def test_the_rail_draws_the_same_rows_in_build_as_in_chat():
    """"Same rows, app tags and filter" is a comparison, so make it one. The rail is one component
    with one `mode` prop, and mode decides where a row goes, never what a row says."""
    chat, build = _run([{"rail": "chat"}, {"rail": "build"}])
    assert chat["words"] == build["words"]
    # Conversations, not apps — the titles are the rows and the app names arrive as tags on them.
    assert "Desks" in chat["words"]
    assert "Desk dashboard" in chat["words"]


@needs_node
def test_the_app_rail_is_gone_from_the_workbench():
    """`app-list.js` held five things and this ticket is where the bill comes due. The file only
    goes when nothing is left in it — so its absence is the assertion, on disk and in the page."""
    assert not (_WORKBENCH / "js" / "components" / "app-list.js").exists()
    assert "app-list.js" not in (_WORKBENCH / "index.html").read_text()


# ---- the header names the app, and lists them ---------------------------------------------------


@needs_node
def test_the_header_names_the_selected_app():
    step = _build(select="app_c")
    assert step["app"] == "app_c"
    assert "Rate curve viewer" in step["words"]


@needs_node
def test_the_header_lists_every_app_with_its_build_state_and_its_incoming_changes():
    """The decision's first finding: a list, not a line. A one-line selector showing only the
    selected app's name silently destroys #77's `Building…` and #78's `Changes to pull`, whose
    whole point is being seen across the list without a click."""
    said = _said(_build())
    for name in ["Desk dashboard", "P&L report", "Rate curve viewer", "Risk monitor"]:
        assert name in said, name
    assert "Built" in said
    assert "Not built yet" in said
    assert "Building" in said
    assert "Changes to pull" in said


@needs_node
def test_the_list_says_what_it_is_a_list_of():
    """The label the rail's head carried. The button above it names one Built App; this is what
    tells you the other rows are the rest of the Project's."""
    assert "Built Apps in this Project" in _build()["words"]


@needs_node
def test_the_list_is_searchable():
    """The rail's search box came with the rows. A Project can hold twenty Built Apps, and a list
    you can only scroll is the reason the rail had one."""
    assert "Search Built Apps" in _build()["placeholders"]


@needs_node
def test_the_header_offers_new_app():
    step = _build()
    assert "New app" in step["words"]


@needs_node
def test_new_app_is_the_one_primary_action_only_while_there_is_nothing_else_to_do():
    """With no apps it is the only way forward, so it leads. With apps, the composer is what the
    screen is for and a second filled button in the toolbar would compete with it."""
    assert "primary" in _build("thr_none", noapps=True)["buttons"]
    assert "primary" not in _build()["buttons"]


@needs_node
def test_the_header_says_how_many_other_apps_this_conversation_touched():
    """`thr_many` changed three apps and one of them is in the preview, so two are "other"."""
    assert "2 other apps changed here" in _said(_build())


@needs_node
def test_the_header_says_nothing_when_this_conversation_touched_only_the_open_app():
    """A count of nothing is noise. Said only when there is something to say."""
    said = _said(_build("thr_one"))
    assert "other app" not in said
    assert "0 other" not in said


@needs_node
def test_the_count_says_nothing_until_an_app_is_named():
    """"Other" is other than the app in the preview, so with none named there is no count to give.
    Counting every touched app as "other" beside a control reading `Choose from your Built Apps` is
    a header disagreeing with itself on first paint.

    Plural, and read off the render rather than the source: there is no article engine (ADR-0014),
    so "Choose a {builtApp}" read "Choose a Archive" under a pack with a vowel-initial noun."""
    step = _build("thr_many", unselected=True)
    assert "Choose from your Built Apps" in step["words"]
    assert "other app" not in _said(step)


@needs_node
def test_one_other_app_is_singular():
    """`1 other apps changed here` is the kind of thing that makes a screen look unfinished."""
    said = _said(_build("thr_two", select="app_a"))
    assert "1 other app changed here" in said
    assert "1 other apps" not in said


# ---- selecting goes through the route -----------------------------------------------------------


@needs_node
def test_picking_an_app_writes_the_route_and_never_the_store():
    """The one-writer rule (#78, and the reason the rail's rows worked this way). The route says
    which app and the store follows it, so a click that only told the store would be a second
    writer — and since #100 the URL rewrite that keeps the address bar honest would then be
    covering for the picker rather than for the server.
    `activeApp` also has a second reader: the transcript's app card computes `showing` from it
    (#56), and a picker that set it directly would flip that card without moving the preview."""
    step = _run([{"pick": "app_d", "thread": "thr_many", "select": "app_a"}])[-1]
    assert step["rows"] == ["app_a", "app_b", "app_c", "app_d"]
    assert step["hash"] == "#/build/thr_many?app=app_d"
    # The store has not moved. It moves when BuildMode reads the route, one render later.
    assert step["appAfterClick"] == step["appBefore"] == "app_a"


@needs_node
def test_app_route_survives_the_rail_it_used_to_live_in():
    """`SW.appRoute` was housed in the rail but is Build's route grammar, and `store.js` calls it
    twice — after a delete and after a handoff. The harness never loads `app-list.js`, so this
    passing at all is the claim: one grammar, re-homed, still one call away."""
    with_thread, without = _run(
        [{"route": "app_b", "thread": "thr_many"}, {"route": "app_b"}]
    )
    assert with_thread["path"] == "#/build/thr_many?app=app_b"
    assert without["path"] == "#/build?app=app_b"


@needs_node
def test_the_grammar_lives_in_the_router_not_in_a_component():
    source = (_WORKBENCH / "js" / "router.js").read_text()
    assert "SW.appRoute" in source


# ---- the header's pick and the rail's filter are the same filter --------------------------------


@needs_node
def test_picking_an_app_in_the_header_narrows_the_rail_to_it():
    """The filter had one writer — the chip on a row — so the header's dropdown moved the preview
    and left the rail listing every conversation in the Project. Two halves of one screen, one
    naming an app and the other ignoring it."""
    step = _run([{"pick": "app_b", "thread": "thr_many", "select": "app_a"}])[-1]
    assert step["railFilter"] == "app_b"
    # `thr_one` changed only Desk dashboard, and it is not the conversation on screen, so it goes.
    assert step["rail"]["rows"] == ["Desks", "Two of them"]
    assert step["rail"]["chip"] == "Only P&L report"


@needs_node
def test_the_conversation_you_are_standing_in_survives_the_filter():
    """The failure ADR-0009 exists to stop: picking an app emptied the rail under a transcript that
    was still on screen, so the furniture beside one Conversation stopped listing it. `thr_one`
    never touched P&L report and stays anyway, because it is the one you are reading."""
    step = _run([{"pick": "app_b", "thread": "thr_one", "select": "app_a"}])[-1]
    assert step["railFilter"] == "app_b"
    assert "Just the one" in step["rail"]["rows"]


@needs_node
def test_a_chip_filter_set_earlier_does_not_outlive_the_app_it_named():
    """Only `setScope` cleared the filter, so a chip pressed before an app switch left the rail
    saying "Only Desk dashboard" beside a header saying P&L report."""
    step = _run(
        [{"pick": "app_b", "thread": "thr_many", "select": "app_a", "chip": "Desk dashboard"}]
    )[-1]
    assert step["railBefore"]["chip"] == "Only Desk dashboard"
    assert step["rail"]["chip"] == "Only P&L report"
    assert step["railFilter"] == "app_b"


@needs_node
def test_the_filter_moves_on_a_click_and_never_on_the_poll():
    """Why the write is in `pick` and not in an effect on `activeApp`. The selection is per-Project
    on the server and shared across tabs, and the 30s poll moves it under you — so an effect would
    let a second tab silently re-filter this tab's rail. Only a person's own click may move it."""
    step = _run(
        [{"poll": "app_b", "thread": "thr_many", "select": "app_a", "pickFirst": "app_a"}]
    )[-1]
    assert step["activeApp"] == "app_b"
    assert step["railFilterBefore"] == step["railFilter"] == "app_a"
    assert step["rail"]["chip"] == "Only Desk dashboard"


@needs_node
def test_the_chip_names_an_app_no_conversation_has_changed():
    """`filterName` resolved the name by scanning the threads' own tags, which held while a chip was
    the only writer — the app was in some thread's tags by definition. The header can filter to an
    app nobody has built in yet, and that read "Only an app"."""
    step = _run([{"pick": "app_c", "thread": "thr_none", "select": "app_a"}])[-1]
    assert step["rail"]["chip"] == "Only Rate curve viewer"
    assert "an app" not in step["rail"]["chip"]


@needs_node
def test_the_filter_is_visible_and_can_be_dropped():
    """A filter you cannot see is a rail that has silently lost rows, and one you cannot drop is a
    mode. Both halves are on the chip, and dropping it brings every row back."""
    step = _run([{"pick": "app_b", "thread": "thr_many", "select": "app_a", "clear": True}])[-1]
    assert step["rail"]["chip"].startswith("Only ")
    assert step["rail"]["chipClear"] == ["Show all conversations"]
    assert step["cleared"]["railFilter"] is None
    assert step["cleared"]["rail"]["chip"] is None
    assert step["cleared"]["rail"]["rows"] == [
        "Desks", "Just the one", "Two of them", "Nothing built here"
    ]


@needs_node
def test_dropping_the_filter_leaves_the_previewed_app_alone():
    """The rail and the preview are two questions. Clearing the chip answers the first one — show me
    everything again — and must not quietly answer the second by putting Build back on another app."""
    step = _run([{"pick": "app_b", "thread": "thr_many", "select": "app_a", "clear": True}])[-1]
    assert step["cleared"]["activeApp"] == step["appAfterClick"] == "app_a"


@needs_node
def test_a_tag_shows_history_and_never_moves_the_preview():
    """The other direction stays one-way. Clicking a tag asks a question about history; switching
    the app under the preview is an answer to a different one, and `activeApp` keeps its single
    writer — the route."""
    step = _run(
        [{"build": "thr_many", "select": "app_a"}, {"rail": "build", "chip": "P&L report"}]
    )[-1]
    assert step["railFilter"] == "app_b"
    assert step["activeApp"] == "app_a"


def test_the_pick_writes_the_filter_and_no_effect_watches_the_selected_app():
    """The one-line change this must not become. An effect keyed on `activeApp` would pass every
    assertion above and still re-filter the rail from another tab's click, because the poll writes
    `activeApp` too. No node here: the claim is about where the write lives."""
    source = (_WORKBENCH / "js" / "modes" / "builder.js").read_text()
    # One mention in the whole file, and it is the click's. An effect would have to name the filter
    # too, so a second line here is the regression whatever it is keyed on.
    assert [ln.strip() for ln in source.splitlines() if "railAppFilter" in ln] == [
        "SW.store.set({ railAppFilter: app.id });"
    ]
    # And the store stays out of it, `selectApp` above all: that is what the poll and the route both
    # call, so a write in there is the same effect wearing a different hat. Five lines may name the
    # filter, and every one of them CLEARS it — none is keyed on the selected app, which is the
    # claim. Comments stripped, so the claim is about the code rather than how a sentence beside it
    # reads.
    #
    # The six, and why each drops it. A filter is a question about the list, and it goes wherever
    # the list it asks about stops being the one on screen: leaving the Project, and every way the
    # Rail opens or closes (#150). Three were added once the Rail began starting hidden. The Rail
    # OPENING, either by hand or to show a press its own answer, because Build's header can set the
    # filter while nothing is showing, so the same filter nobody can see they applied arrives by
    # those doors instead. And starting a Conversation, because a new one has touched no app, so a
    # standing filter hides the row for the Conversation somebody just pressed for — and the Rail
    # then says nothing has changed that app yet.
    store = (_WORKBENCH / "js" / "store.js").read_text()
    assert [
        ln.split("//")[0].strip() for ln in store.splitlines() if "railAppFilter" in ln
    ] == [
        "railAppFilter: null,",
        "state.railAppFilter = null;",   # setScope
        "state.railAppFilter = null;",   # toggleRail, either way
        "state.railAppFilter = null;",   # collapseRail
        "state.railAppFilter = null;",   # expandRail
        "state.railAppFilter = null;",   # newConversation
    ]


# ---- rename and delete, on Reset's precedent ----------------------------------------------------


@needs_node
def test_rename_and_delete_are_text_items_in_the_overflow_beside_the_app_name():
    """#38 considered moving a destructive app action to the toolbar and recorded no. Reset went to
    the composer's overflow instead: text-labelled, danger-styled, last, below a divider. These two
    land in the header's own `…` on that precedent, not as a new placement."""
    step = _build()
    menus = [m for m in step["menus"] if any(i["key"] == "delete" for i in m["items"])]
    assert len(menus) == 1, step["menus"]
    items = menus[0]["items"]
    # Publish and Open app joined them above Rename (#89) on the same shape, which is the point:
    # the menu grew and none of it turned into an icon. `624ff9b` grouped it — App above, Manage
    # below — and the read-only glances that used to sit in the header came in as items rather than
    # as controls of their own, which is the same shape again one level down.
    assert [i["key"] for i in items if not i["divider"] and i["key"]] == [
        "publish", "open", "reload", "rename", "delete", "dependencies", "history",
    ]
    assert all(i["label"] for i in items if not i["divider"] and not i["group"]), items
    # Danger, last in its group, and below a divider — the three things Reset's shape is made of.
    # "Last in its group" rather than last outright: `624ff9b` split the menu into App and Manage,
    # and what Reset's precedent is about is the destructive item sitting at the bottom of the
    # things that ACT, fenced off by a divider. Manage below it is read-only glances.
    at = [i["key"] for i in items].index("delete")
    assert items[at]["danger"] is True
    assert items[at - 1]["divider"] is True
    # Nothing that acts follows it: the rest of the menu is the next group and its children.
    assert items[at + 1]["divider"] is True
    assert items[at + 2]["group"] is True
    assert not any(i["danger"] for i in items[at + 1:])


@needs_node
def test_the_overflow_says_which_app_it_acts_on():
    """An icon-only control has to say what it does, and this one is the only place two irreversible
    actions live. It sits beside the app it names, so the label names it too."""
    step = _build(select="app_c")
    assert any("Rate curve viewer" in label for label in step["labels"])


@needs_node
def test_rename_and_delete_are_not_repeated_on_every_row():
    """A `…` per row inside a dropdown is a menu inside a menu. One `…`, beside the one app the
    header names, which is also what removes the ambiguity the per-row `…` was solving."""
    step = _build()
    assert len([m for m in step["menus"] if any(i["key"] == "delete" for i in m["items"])]) == 1


# ---- the empty state, and the timer -------------------------------------------------------------


@needs_node
def test_the_no_apps_yet_guidance_is_readable_without_opening_the_app_control():
    """A person with no apps has no reason to open an app picker, so an empty state hidden inside
    one is not reachable by the person it is written for. It goes where the app name would be."""
    step = _build("thr_none", noapps=True)
    said = _said(step)
    assert "No Built Apps yet. Start one with New app, or approve a plan in Chat." in said
    assert "New app" in step["words"]


@needs_node
def test_something_mounted_in_build_still_refreshes_the_app_list_on_a_timer():
    """The rail's 30-second poll was the only thing keeping app state fresh — a teammate's push has
    to reach the screen without anyone opening an app to find out (#78). It moved with the rail."""
    step = _build()
    assert 30000 in step["timers"]
    assert step["loadAppCalls"] >= 1


# ---- the composer agrees with the header --------------------------------------------------------


@needs_node
def test_the_build_composer_names_the_selected_app():
    """Once the header names the app, a composer saying "this app" is a second voice on the same
    screen. Both name it, or they disagree."""
    step = _build(select="app_d")
    assert step["composerPlaceholder"] == "Describe a change to Risk monitor…"


@needs_node
def test_the_composer_falls_back_when_no_app_is_selected():
    step = _build("thr_none", noapps=True)
    assert step["composerPlaceholder"] == "Describe a change, or ask about this app…"


# ---- what this ticket must not do ---------------------------------------------------------------


@needs_node
def test_nothing_here_reads_the_conversation_view_preference():
    """#61 removes everything that branches on it. This work has to survive whichever arm wins, so
    it must not add the first branch to a file that has none."""
    for name in ["modes/builder.js", "components/conversation-list.js"]:
        assert "conversationView" not in (_WORKBENCH / "js" / name).read_text(), name


@needs_node
def test_selecting_an_app_in_the_header_does_not_preselect_a_handoff_target():
    """Viewing is not targeting (#52, #58, ADR-0008). The header puts `activeApp` within easy reach
    of the handoff sheet, and the tempting one-line change is the silent overwrite #73 exists to
    prevent. The sheet's target starts empty and stays empty."""
    source = (_WORKBENCH / "js" / "components" / "handoff.js").read_text()
    assert "useState('')" in source
    assert "activeApp" not in source


# ---- the tags the rail was built around now have a writer ---------------------------------------


def test_the_rail_reads_the_keys_the_server_writes():
    """The tags, the app filter and the app-name search were all coded here and all dead, because
    `thread.touched` had no writer. It has one now (ThreadStore.record_touch), and the two halves
    agree by nothing stronger than these four key names — so a rename on either side has to break a
    test rather than quietly empty the rail.

    No node here: the claim is about the source, and it holds whether or not the rail mounts."""
    rail = (_WORKBENCH / "js" / "components" / "conversation-list.js").read_text()
    # The filter and the search, the two the rail cannot draw at all without.
    assert "x.appId === railAppFilter" in rail
    assert "x.appName.toLowerCase().includes(needle)" in rail
    # The tag itself: which app, and "Built X" versus "Changed X".
    assert "tag.appName" in rail
    assert "tag.kind === 'built'" in rail

    writer = (Path(__file__).resolve().parents[1] / "sage" / "workspace" / "threads.py").read_text()
    assert '{"appId": app_id, "appName": app_name, "kind": kind}' in writer


def test_the_stubs_the_server_replaced_are_gone():
    """`api.touchApp` returned `{touched: []}` and `store.recordChange` was its only caller, with no
    caller of its own. Left beside a field the server now really writes, a stub named for the same
    job is a trap for the next reader."""
    assert "touchApp" not in (_WORKBENCH / "js" / "api.js").read_text()
    assert "recordChange" not in (_WORKBENCH / "js" / "store.js").read_text()
