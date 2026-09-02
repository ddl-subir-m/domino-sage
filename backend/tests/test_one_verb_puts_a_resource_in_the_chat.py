"""Three surfaces offer the same act, so all three call it the same thing — and Pin says what it
does.

The tree, the drawer and the row menu each let a person put a resource in front of the assistant.
They named that act three different ways: "Add to chat", "Mention in this chat", "Add to this
conversation". Someone comparing two of them had to work out whether the difference in wording meant
a difference in effect. It never did.

Sitting beside the tree's control was Pin, an unlabelled verb with no visible result. Pin only
reorders the `@` menu (`docs/workbench/chat.md`); it sends nothing. Next to a control that DOES
send, it read as a second way to attach, and nothing on screen said otherwise.

Source assertions, in the style of `test_workbench_composer_mention.py`: there is no browser in this
suite, and each assertion below names the wording that was live before it. Copy cannot come back by
accident.
"""
from __future__ import annotations

import re
from pathlib import Path

_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
TREE = (_JS / "components" / "resource-tree.js").read_text()
DRAWER = (_JS / "components" / "resource-drawer.js").read_text()
PANEL = (_JS / "components" / "resource-panel.js").read_text()

SURFACES = {"resource-tree.js": TREE, "resource-drawer.js": DRAWER, "resource-panel.js": PANEL}

# The one verb, and the three it replaced.
USE = "Use in this chat"
GONE = ("Add to chat", "Mention in this chat", "Add to this conversation")


def _flat(src: str) -> str:
    """Comments stripped, runs of whitespace squashed to one space. An assertion over this pins the
    SHAPE of a call — which element wraps which — and not the indentation depth or the wording of a
    neighbouring comment, either of which can change without changing a pixel of what renders."""
    return " ".join(re.sub(r"//[^\n]*", "", src).split())


TREE_FLAT = _flat(TREE)

# The whole Pin/Unpin ternary in one string, which pins four claims at once: Unpin is a bare Button,
# Pin is the one inside the Tooltip, the title is built by the pack, and the assistant is named by a
# token rather than in ink.
PIN_BRANCH = (
    "pinned "
    "? h(Button, { size: 'small', type: 'link', onClick: onUnpin }, 'Unpin') "
    ": h( Tooltip, { title: SW.brand.text( "
    "'Keeps this at the top of the @ menu. It does not send it to {assistantName}.' "
    "), }, h(Button, { size: 'small', type: 'link', onClick: onPin }, 'Pin') )"
)


def test_every_surface_that_attaches_a_resource_uses_the_same_words():
    """One act, one name. Three names for it made a person guess whether "Add to chat" and "Add to
    this conversation" reached the same place — they always did."""
    for name, src in SURFACES.items():
        assert USE in src, f"{name} no longer offers {USE!r}"


def test_the_three_older_names_for_attaching_are_gone_everywhere():
    """Each of these was one surface's private word for the act the other two also offered. Asserted
    as exact phrases, because `Add to ${scope.name}` and `Add to ${d.name}` are a DIFFERENT act —
    putting the thing in the project or a Dataset — and both must survive."""
    for name, src in SURFACES.items():
        for phrase in GONE:
            assert phrase not in src, f"{name} still says {phrase!r}"


def test_the_row_menu_names_stopping_without_borrowing_the_removal_verb():
    """The menu offered "Remove from this conversation" beside "Remove from {project}" and "Remove
    from {app}". Three "Remove from" items, and only one of them threw nothing away — the
    Conversation one. It now says what it does instead."""
    assert "Stop using here" in PANEL
    assert "Remove from this conversation" not in PANEL
    # The two that really do remove keep their verb; this rename must not eat them (ADR-0011).
    assert "`Remove from ${SW.store.get().scope.name}`" in PANEL
    assert "`Remove from ${appScope.app.name}`" in PANEL


def test_the_chip_announces_the_act_the_menu_offers():
    """The chip's X button is the same act as the menu item beside it. While the menu said "Stop
    using here" and the button still announced "Remove ... from this conversation", a screen-reader
    user was the only person hearing the retired verb — and heard the costly one for the free act.

    It says "in this chat" rather than "here" on purpose: the visible control borrows its scope from
    the panel it sits in, and there is no "here" to look at in audio (ADR-0015)."""
    assert "`Stop using ${resource.name} in this chat`" in PANEL
    assert "Remove ${resource.name} from this conversation" not in PANEL
    # The button still calls the same store action — this was copy, not wiring.
    assert "SW.store.removeFromConversation(contextItem)" in PANEL


def test_the_rename_moved_no_wiring():
    """Copy only. The menu keys are what the click handler dispatches on, so a key renamed along
    with its label would leave a control that reads correctly and does nothing."""
    assert "key: 'remove-from-conversation'" in PANEL
    assert "key: attached ? 'remove-resource-from-conversation' : 'mention'," in PANEL


def test_pin_says_that_it_does_not_send_anything():
    """Pin reorders the `@` menu and nothing else. Unlabelled beside the attach control, it read as
    a second way to attach. The sentence is only on screen if it is a Tooltip's title AND that
    Tooltip is the thing wrapping Pin, so assert the wrapper, not the loose string."""
    assert "Tooltip" in TREE.split("= antd;")[0], "Tooltip is not destructured from antd"
    assert PIN_BRANCH in TREE_FLAT, "the Pin/Unpin branch is not the shape the tooltip needs"


def test_the_pin_tooltip_goes_through_the_brand_pack():
    """It names the assistant, so a pack that renames the assistant renames it too (ADR-0014). A
    bare literal here would ship our own word for the assistant onto a partner's screen. Counted,
    so a second copy of the sentence cannot appear outside `SW.brand.text`."""
    assert TREE_FLAT.count("Keeps this at the top of the @ menu") == 1
    assert "{assistantName}" in TREE
    assert "SW.brand.text( 'Keeps this at the top of the @ menu." in TREE_FLAT


def test_only_pin_carries_the_tooltip():
    """Unpin needs no explanation — the thing is already pinned, and whoever pinned it has seen the
    tooltip. Counted over the `h(` call rather than the word, so that importing another antd
    component whose name sits beside Tooltip in the destructure does not read as a second one."""
    assert TREE_FLAT.count("h( Tooltip,") == 1


# Membership stopped being a gate in front of the verb ------------------------

def test_the_drawer_puts_a_catalogue_resource_in_the_chat_in_one_click():
    # Was `Add to {project}`, which named the machine's step rather than the user's.
    assert "await SW.store.addToProject(resource);" not in DRAWER
    assert "SW.store\n        .addToContext(resource, { quiet: true })" in DRAWER
    # The alert above the button has to agree with what the button now does.
    assert "Using it in this chat adds it to " in DRAWER
    assert "SW.brand.text(" in DRAWER


def test_a_join_the_drawer_cannot_make_says_so():
    """Everything else on this button stays put when the call fails — the drawer is still open, the
    alert still says the resource is not in the project, the label still reads `Use in this chat`.
    Without a report, a refused join and a click that never landed look the same."""
    assert ".catch((err) => antd.message.error(String((err && err.message) || err)))" in DRAWER


def test_the_drawer_stays_open_so_the_join_can_be_seen_landing():
    """The alert says this is not in the project. Closing on click would take that sentence off
    screen at the moment it stopped being true, and the only evidence left would be a toast."""
    assert """onClick: inProject
                  ? mention
                  : useHere,""" in DRAWER
    # `mention` is the branch that closes, and only a resource already in the project takes it.
    assert DRAWER.count("close();") == 1
