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
    """Split is what Chat does today. A preference that seeded anything else would move the
    furniture for everyone on first load, which is the one thing this ticket must not do."""
    assert _run([{"viewer": "u1", "op": "get", "name": "conversationView"}]) == ["split"]


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


def test_nothing_reads_the_preference_yet():
    """The prefactor lands with behaviour unchanged: the surface writes the answer, and the only
    other thing that looks at it is the surface drawing its own control."""
    readers = sorted(p.relative_to(_JS).as_posix() for p in _JS.rglob("*.js")
                     if "conversationView" in p.read_text())
    assert readers == ["components/shell.js", "prefs.js"]


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
