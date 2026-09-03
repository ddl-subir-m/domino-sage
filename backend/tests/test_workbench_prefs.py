"""Viewer preferences for the Workbench (#52).

Two claims are worth running rather than reading — that a choice is still there after a reload,
and that a second viewer on the same origin gets their own answer — so those go through the JS
harness. The rest are claims about where the preference is NOT: not in the Project's git repo, not
carrying the handoff's target app, and not yet read by anything that would change behaviour.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "prefs_harness.mjs"
_WB = Path(__file__).resolve().parents[1] / "sage" / "workbench"
_JS = _WB / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_conversation_view_starts_where_the_workbench_already_is():
    """Split is what Chat does today, and #52 seeded it deliberately: that ticket was about giving
    preferences a home, so none of its fallbacks moved anyone's furniture on first load.

    A fallback is not a promise never to change one. #150 changes two on purpose — the Rail now
    starts collapsed and the dock stops appearing in Build — because Build fanned out to four side
    columns and the two panels nobody had opened were 580px of them. The rule that survives both
    tickets is narrower than "never move the furniture": a panel opens and closes when the person
    opens and closes it, and the preference records only what they chose by hand."""
    assert _run([{"viewer": "u1", "op": "get", "name": "conversationView"}]) == ["split"]


def test_the_rail_starts_collapsed_when_nothing_is_on_file():
    """Build's four side columns took 55% of a 1732px screen before the preview got anything, and
    the Rail was 260px of it that nobody had asked for. Collapsed is the fallback, so a person who
    has never touched it gets the preview instead."""
    assert _run([{"viewer": "u1", "op": "get", "name": "railHidden"}]) == [True]


def test_a_rail_opened_by_hand_is_still_open_after_a_reload():
    """The other half of the same rule. The fallback decides what someone who has chosen nothing
    sees; once they have chosen, the choice is the answer — including the choice to overrule the
    fallback."""
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "railHidden", "value": False},
        {"op": "reload"},
        {"op": "get", "name": "railHidden"},
    ])
    assert answers[-1] is False


def test_a_closed_dock_stays_closed_across_a_reload():
    """`null` is a real answer for the dock — it is the closed one — so it has to be in `values`.
    `get` validates against `values`, so an unlisted `null` would read back as the fallback, and a
    dock the person closed would re-open on every load. Which is the bug this ticket is fixing, so
    it must not come back through the preference that fixed it."""
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "dockTab", "value": "resources"},
        {"op": "set", "name": "dockTab", "value": None},
        {"op": "reload"},
        {"op": "get", "name": "dockTab"},
    ])
    assert answers[0] is True
    assert answers[1] is True      # closing is a choice worth storing, not a failed write
    assert answers[3] is None


def test_only_a_hand_closed_rail_is_written_down():
    """Auto-collapse and the stored preference are the same value, so `collapseRail` — the collapse
    that follows picking a Conversation — must not write it. If it did, a person who deliberately
    opened the Rail would lose that choice on their next click, without ever closing anything.
    `toggleRail` is the hand on the control, so `toggleRail` is the one that writes."""
    store = (_JS / "store.js").read_text()
    assert "SW.prefs.set('railHidden'" in _method(store, "toggleRail")
    assert "SW.prefs.set" not in _method(store, "collapseRail")
    # `focusPanel` is Sage asking for a panel, not the person, so it stays a write-free open.
    assert "SW.prefs.set" not in _method(store, "focusPanel")


def _method(source: str, name: str) -> str:
    """One method's body, from its `name(` to the dedented `}` that closes it. The claim is about
    which method writes the preference, and grepping the whole file cannot tell them apart."""
    start = source.index(f"\n    {name}(")
    return source[start:source.index("\n    },", start)]


def test_a_choice_is_still_there_after_the_page_is_loaded_again():
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "conversationView", "value": "unified"},
        {"op": "reload"},
        {"op": "get", "name": "conversationView"},
    ])
    assert answers[-1] == "unified"


def test_one_viewers_choice_is_not_another_viewers():
    """Two people in one Project run two Sage Builders, and both are served from the same Domino
    origin — so the record is keyed by viewer. Without that, sharing a browser profile would let
    one person's answer overwrite the other's (#62's collision, moved into the browser)."""
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "conversationView", "value": "unified"},
        {"viewer": "u2", "op": "get", "name": "conversationView"},
        {"viewer": "u2", "op": "set", "name": "conversationView", "value": "split"},
        {"viewer": "u1", "op": "get", "name": "conversationView"},
    ])
    assert answers[1] == "split"      # u2 reads the default, not u1's answer
    assert answers[3] == "unified"    # u1 still reads their own


def test_storage_that_cannot_be_read_is_the_same_as_no_answer():
    """localStorage is editable by hand and shared with whatever else this origin ever stored, so
    the two ways it can be wrong — unparseable, or a value with no branch behind it — both have to
    land on the default rather than on undefined."""
    answers = _run([
        {"viewer": "u1", "op": "seed", "key": "sw.prefs", "raw": "not json {"},
        {"op": "get", "name": "conversationView"},
        {"op": "seed", "key": "sw.prefs", "raw": '{"u1": {"conversationView": "sideways"}}'},
        {"op": "get", "name": "conversationView"},
    ])
    assert answers[1] == "split"
    assert answers[3] == "split"


def test_a_stored_array_does_not_swallow_every_write():
    """An array passes a `typeof === 'object'` check and then loses named properties on the way
    back through JSON.stringify — so a write would return happily, store nothing, and leave the
    control showing an answer that is not on file. Unreadable storage has to be replaced, not
    written into."""
    answers = _run([
        {"viewer": "u1", "op": "seed", "key": "sw.prefs", "raw": "[]"},
        {"op": "set", "name": "conversationView", "value": "unified"},
        {"op": "reload"},
        {"op": "get", "name": "conversationView"},
    ])
    assert answers[1] is True
    assert answers[3] == "unified"


def test_a_value_the_reader_could_not_get_back_is_not_written():
    """`get` refuses a value it does not recognise, so `set` refuses the same ones. Otherwise the
    write would read back as the default while the radio sat on a value matching neither option,
    and the junk would stay in the viewer's storage for good."""
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "conversationView", "value": "sideways"},
        {"op": "dump", "key": "sw.prefs"},
        {"op": "get", "name": "conversationView"},
    ])
    assert answers[0] is False
    assert answers[1] is None      # nothing was written at all
    assert answers[2] == "split"


def test_the_preference_never_reaches_the_projects_git_repo():
    """The whole reason this is a viewer preference: #62 spent three tickets (Thread index #64,
    rendered history #65, build log #68) taking shared files back OUT of the Project repo, because
    two Builders against one remote collide on anything shared. A preferences file at the Project
    root would put one back."""
    prefs = (_JS / "prefs.js").read_text()
    assert "localStorage" in prefs
    assert "SW.api" not in prefs          # nothing is posted to the Builder, so nothing is written
    backend = Path(__file__).resolve().parents[1] / "sage"
    wrote = [p for p in backend.rglob("*.py") if "conversationView" in p.read_text()]
    assert wrote == []


def test_the_handoffs_target_app_is_not_a_preference():
    """#73 resets the target on purpose: preselecting an app the person did not choose is how a
    build silently overwrites an existing app, which ADR-0008 makes a live risk. It stays in the
    sheet's own state, and it must not arrive here later either."""
    assert "appId" not in (_JS / "prefs.js").read_text()
    assert "appId" not in _settings_drawer()


def test_only_the_store_branches_on_the_preference():
    """#52 landed this answer with nothing reading it. #56 gave it its first reader, and there is
    exactly one: the store decides what a Conversation's messages are, once, and every component
    downstream draws whatever it was handed. A second reader would be a second place for the two
    views to disagree — and #61 has to be able to delete one arm by deleting one branch."""
    readers = sorted(p.relative_to(_JS).as_posix() for p in _JS.rglob("*.js")
                     if "conversationView" in p.read_text())
    assert readers == ["components/shell.js", "prefs.js", "store.js"]


def _settings_drawer() -> str:
    shell = (_JS / "components" / "shell.js").read_text()
    return shell[shell.index("SW.SettingsDrawer"):]


def test_the_account_menu_is_where_the_preferences_live():
    shell = (_JS / "components" / "shell.js").read_text()
    assert "settingsOpen: true" in shell            # the 'account' key opens it
    assert "h(SW.SettingsDrawer, null)" in shell    # and the Shell mounts it
    drawer = _settings_drawer()
    assert "'split'" in drawer and "'unified'" in drawer
    assert "settingsOpen" in (_JS / "store.js").read_text()
    assert '"./js/prefs.js"' in (_WB / "index.html").read_text()
