"""The Chat composer's @mention menu, pinned at the source.

There is no browser in this suite, so these are source assertions in the style of test_fonts.py:
each one names a way the mention menu misbehaved live, and the shape of the fix that stops it. A
behavioural test would need a DOM; what these buy is that the mistakes cannot come back by accident.

The first two moved here from test_builder_composer.py when the single-file builder UI became the
Workbench: the bugs were found there, and the properties that stop them are properties of whatever
composer is on screen.
"""
from pathlib import Path

WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
UI = (WB / "js" / "components" / "composer.js").read_text()
CSS = (WB / "css" / "chat.css").read_text()


def test_picking_a_mention_inserts_at_name_instead_of_stripping_it():
    # The token itself is derived in util.js, because the turn has to read the same one back out of
    # the prompt (see test_workbench.test_a_build_turn_carries_what_its_mentions_name).
    assert "SW.util.mentionToken(resource, mentionPeers)" in UI
    assert "setText(text.slice(0, mention.start) + token + pad + after)" in UI
    assert "The mention resolves into a chip rather than into text" not in UI


def test_mention_menu_includes_thread_artifacts_as_this_thread():
    assert "thread.artifacts" in UI
    assert "In this thread" in UI


def test_moving_the_mention_highlight_does_not_rebuild_the_rows():
    # The arrow keys read as dead. The menu sits over the composer, so the pointer is usually resting
    # on it, and the old builder rebuilt every row on each move — a fresh row inserted under a still
    # pointer fires mouseenter, which handed the highlight straight back to the hovered row. Here the
    # rows are keyed, so a move changes one class and React reuses the DOM the pointer is over.
    assert "key: resource.id," in UI
    assert "className: `sw-mention-item${index === cursor ? ' is-active' : ''}`," in UI
    for arrow in ("ArrowDown", "ArrowUp"):
        assert f"e.key === '{arrow}'" in UI
    # The keys move the cursor and nothing else — no refetch, no re-open, no rebuild.
    assert "setCursor((c) => (c + 1) % suggestions.length);" in UI
    assert "setCursor((c) => (c - 1 + suggestions.length) % suggestions.length);" in UI


def test_nothing_competes_with_the_keyboard_highlight():
    # `.mm-item.active, .mm-item:hover` shared one declaration in the builder, so even a move that
    # DID land looked identical to a hover and read as nothing happening. There is one highlight
    # here, and the pointer drives it through the same cursor the keys do (onMouseEnter -> setCursor)
    # rather than through a second style that can disagree with it.
    assert ".sw-mention-item.is-active" in CSS
    assert ".sw-mention-item:hover" not in CSS
    assert "onMouseEnter: () => setCursor(index)," in UI


def test_a_deletion_never_opens_a_closed_mention_menu():
    # Backspacing over a finished mention re-opened the picker on every keystroke: the caret lands
    # just after "@BigQuery_Dem", which still matches the token regex. The user was deleting and got
    # a menu — and the open menu then took the next Enter for row selection instead of sending.
    assert "if (String(inputType || '').startsWith('delete') && !mention) return;" in UI
    # The rule needs the browser to say what kind of edit this was, so the handler has to pass it on.
    assert "e.nativeEvent && e.nativeEvent.inputType" in UI
    # A deletion may still NARROW a menu that is already open, so the guard is conditional on state.
    assert "&& !mention) return;" in UI


def test_one_backspace_takes_the_whole_mention():
    # A mention is plain text in the box and not a chip, so the browser's own Backspace charged a
    # keystroke per letter — and "@BigQuery_Demo" is fourteen of them, each one leaving a token
    # that names nothing. The key is read only while the picker is closed, because mid-typing the
    # same key is how the query is narrowed.
    assert "if (e.key === 'Backspace' && !mention) {" in UI
    # The picker's own reading of where a mention starts, not a second one free to drift from it:
    # a finished mention is textually the unfinished one.
    assert "const found = mentionAt(el.value, caret);" in UI
    # Widening the selection instead of rewriting the value leaves the delete to the browser,
    # which is what keeps undo working and the caret where it belongs.
    assert "el.setSelectionRange(found.start, caret);" in UI
    branch = UI.split("if (e.key === 'Backspace' && !mention) {")[1].split("return;")[0]
    assert "setText(" not in branch
    # A caret inside the token still means the letter behind it. Eating the rest of the token
    # would be a forward delete, which is not the key that was pressed.
    assert "const atEnd = caret === el.value.length || /\\s/.test(el.value[caret]);" in UI
    # A selection already says what to delete.
    assert "if (caret === el.selectionEnd && atEnd) {" in UI


# The sixth group: a resource the project has not joined yet -------------------

STORE = (WB / "js" / "store.js").read_text()
API = (WB / "js" / "api.js").read_text()
UTIL = (WB / "js" / "util.js").read_text()


def test_the_menu_offers_resources_the_project_has_not_joined_yet():
    # Adding used to be two acts — join the project, then mention it — and the first one existed
    # for a machine reason. The menu now offers the catalogue too, and picking does both.
    assert "catalogueParents" in UI
    assert "Not in ${scope.name} yet" in UI


def test_the_new_group_is_appended_last():
    # Last because it is the only group whose rows are not already here: everything above is
    # something this project or this thread already holds, and those stay easier to reach.
    #
    # The order lives in `SW.util.workingSetFirst` since #141, shared with the Build header's picker
    # so the two menus that offer a Resource cannot drift. This menu hands it the catalogue AS the
    # catalogue and never as one more group above it, and the helper is what puts it last.
    #
    # `attached` — the selected app's Attachments — joined the working set when the Project stopped
    # listing them (#148). It goes above the catalogue like every other group here, because those
    # files are already in this Project and picking one joins nothing.
    assert "groups: [context, produced, resourceGroups.pin || [], project, files, attached]," in UI
    assert "catalogue: catalogueParents," in UI
    assert "[...(groups || []), catalogue || []].forEach(" in UTIL


def test_the_cap_survives_the_new_group():
    # A sixth source of rows is a sixth way to overflow the menu. The cap is asked for once and
    # applied once, by the shared helper, after every group has been through it — so it still
    # counts them all.
    assert UI.count("limit: 10,") == 1
    assert "return limit ? rows.slice(0, limit) : rows;" in UTIL


def test_no_table_or_dataset_file_can_reach_the_new_group():
    """The `@` menu must never fetch a warehouse catalog (docs/workbench/chat.md). Tables and
    Dataset files are leaves reached by expanding a parent in the rail, and each one is a round
    trip to Domino — a menu that listed them would make every keystroke expensive."""
    # The store fills the group from the parent kinds only, off a listing that has no leaves in it.
    assert "SW.util.MEMBERSHIP_PARENT_KINDS.flatMap(" in STORE
    # Pinned whole, so no leaf kind can be added to it without this line changing.
    assert "MEMBERSHIP_PARENT_KINDS = ['dataset', 'datasource', 'model_llm', 'model_predictive']" in UTIL
    # And the listing it filters has no leaf in it to begin with: a table is a level below any
    # group `fetchDominoListing` builds, and reaching one is the round trip this menu never makes.
    assert "warehouse" in STORE.split("state.catalogueParents = SW.util")[0].split("datasetTargets =")[1]


def test_a_catalogue_row_says_so_on_the_row_itself():
    """The menu draws ONE heading and picks it off `suggestions[0]`. Catalogue rows come last, so
    any row above them makes that heading read `In {project}` — over a row that is not in the
    project. The row carries the correction, the way `in context` already does for the same
    reason: the heading cannot speak for six groups."""
    assert "`not in ${scope.name}`" in UI
    assert "catalogueIds.has(resource.id)" in UI
    # And the heading itself is right when a catalogue row IS first.
    assert "Not in ${scope.name} yet" in UI


def test_the_group_holds_only_resources_the_project_does_not_have():
    # A row in both lists would appear twice — once under the project's own heading and once under
    # "not in it yet", which reads as the menu disagreeing with itself.
    assert "state.catalogueParents = SW.util.MEMBERSHIP_PARENT_KINDS.flatMap(" in STORE
    assert ".filter((r) => !members.has(r.id))" in STORE


def test_the_group_is_dropped_when_the_scope_changes_rather_than_when_it_refills():
    """`catalogueParents` is the complement of the members list, so the two have to turn over
    together. The members are applied synchronously and the catalogue arrives one deferred listing
    later, so a group left standing across a scope change describes the OLD project — and a
    resource that is a member of the newly picked one gets captioned `not in {project}`, the exact
    opposite of true, until the listing lands."""
    reset = "state.catalogueParents = [];"
    assert reset in STORE
    # Beside the synchronous members write, ahead of the deferred listing that refills it.
    before, after = STORE.split(reset, 1)
    assert "applyResourceGroups(resources.groups," in before.rsplit("async function loadScopeData", 1)[-1]
    assert "state.catalogueParents = SW.util.MEMBERSHIP_PARENT_KINDS.flatMap(" in after


def test_the_store_asks_for_nothing_new_to_fill_the_group():
    """`resourceListing()` already returned the whole catalogue and `overlayResourceListing` threw
    the non-members away. Keeping them costs no request; a second fetch on every scope load would."""
    assert STORE.count("SW.api.resourceListing()") == 1


def test_the_join_flag_reaches_the_store_that_reads_it():
    # store.js has handled `attachment.joinedProject` since the panel was written; the API layer
    # dropped it on the floor, so the rail never heard about a join.
    assert "joinedProject: Boolean(row.joinedProject)," in API
    assert "if (attachment.joinedProject) {" in STORE
