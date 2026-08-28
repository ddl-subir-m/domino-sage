"""What a handoff carries stops being four checkboxes (#58).

The sheet keeps its one question — which Built App — and everything it used to ask on top of that
is the viewer's saved answer now. Two claims are worth running rather than reading: the saved
defaults have to land exactly where the hardcoded ones were, and an answer has to survive the
reload, so those go through the same JS harness #52 built. The rest are claims about what the
sheet still asks, and about the one thing that must never become a preference.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "prefs_harness.mjs"
_SAGE = Path(__file__).resolve().parents[1] / "sage"
_JS = _SAGE / "workbench" / "js"

# The three answers that used to be checkboxes on the sheet, in the order the drawer lists them.
CROSSINGS = ("handoffResources", "handoffArtifacts", "handoffTranscript")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _run(steps: list[dict]) -> list:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(steps), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _sheet() -> str:
    """The handoff sheet alone. The same file holds the graduation modal, and that one still has
    every control it ever had."""
    handoff = (_JS / "components" / "handoff.js").read_text()
    return handoff[:handoff.index("SW.GraduationModal = function")]


def _settings_drawer() -> str:
    # The definition, not the line in Shell that mounts it — that one comes first in the file.
    shell = (_JS / "components" / "shell.js").read_text()
    return shell[shell.index("SW.SettingsDrawer = function"):shell.index("SW.HelpDrawer = function")]


def test_the_defaults_are_what_the_sheet_used_to_do():
    """Criterion 5. Nobody has opened the drawer yet on the build that ships this, so the saved
    answer is the fallback for everyone — and the fallback has to write the file set the four
    checkboxes wrote, or the prefactor moves the furniture for every viewer at once."""
    assert _run([{"viewer": "u1", "op": "get", "name": n} for n in CROSSINGS]) == [True, True, False]


def test_an_answer_is_still_there_on_the_next_handoff():
    """The ticket in one test: the same person stops answering the same question every time. A
    reload is the honest version of "next handoff" — the sheet is rebuilt from scratch each time
    it opens, so a value only carries if it outlived the page that wrote it."""
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "handoffTranscript", "value": True},
        {"op": "set", "name": "handoffArtifacts", "value": False},
        {"op": "reload"},
        {"op": "get", "name": "handoffTranscript"},
        {"op": "get", "name": "handoffArtifacts"},
        {"op": "get", "name": "handoffResources"},
    ])
    assert answers[0] is True and answers[1] is True  # both writes were stored
    assert answers[3] is True   # transcript, turned on
    assert answers[4] is False  # artifacts, turned off
    assert answers[5] is True   # and the one nobody touched is still the default


def test_each_viewer_answers_for_themselves():
    """Two people share a Project and a Domino host, so they share an origin. Turning the
    transcript on is a statement about how you work, not about the Project."""
    answers = _run([
        {"viewer": "u1", "op": "set", "name": "handoffTranscript", "value": True},
        {"viewer": "u2", "op": "get", "name": "handoffTranscript"},
        {"viewer": "u1", "op": "get", "name": "handoffTranscript"},
    ])
    assert answers[1] is False  # u2 reads the default, not u1's answer
    assert answers[2] is True


def test_a_stored_answer_that_is_not_a_boolean_is_no_answer():
    """`values` is an allowlist and it has to stay one now that the values are booleans:
    localStorage is editable by hand and outlives the build that wrote it, and the string "false"
    is not false. A write of one is refused for the same reason a read of one is ignored —
    otherwise the checkbox would sit on a value nothing else agrees with."""
    seeded = {"handoffTranscript": "true", "handoffArtifacts": "false"}
    answers = _run([
        {"viewer": "u1", "op": "seed", "key": "sw.prefs", "raw": json.dumps({"u1": seeded})},
        {"op": "get", "name": "handoffTranscript"},
        {"op": "get", "name": "handoffArtifacts"},
        {"op": "set", "name": "handoffTranscript", "value": "true"},
        {"op": "dump", "key": "sw.prefs"},
    ])
    assert answers[1] is False  # the string is not the answer
    assert answers[2] is True
    assert answers[3] is False  # and the write is refused rather than stored
    assert json.loads(answers[4])["u1"] == seeded  # the refused write left the record alone


def test_the_sheet_asks_only_which_built_app():
    """Criterion 6, and the ticket's flat refusal to remove the sheet. #73 gave it a second job the
    checkboxes never had: a Project holds many Built Apps and the target is chosen per handoff. So
    the four checkboxes leave and the target row stays."""
    sheet = _sheet()
    assert "'Build into'" in sheet
    assert "'Bring across'" not in sheet
    assert "Full conversation transcript" not in sheet
    assert "setInclude" not in sheet  # nothing on the sheet writes the answer any more


def test_the_sheet_still_defaults_to_a_new_app_and_preselects_nothing():
    """Criterion 7. Empty is New app, and every open starts empty — persisting the target is the
    one thing this ticket must not do, because building over an app the person did not choose is
    the silent overwrite ADR-0008 exists to close."""
    sheet = _sheet()
    assert "setAppId('')" in sheet
    assert "{ value: '' }" in sheet


def test_targeting_an_existing_app_still_says_it_replaces_that_apps_plan():
    """Criterion 8. This warning appears nowhere else in the Workbench, so it leaves with the sheet
    or not at all."""
    sheet = _sheet()
    assert "This replaces the plan in" in sheet
    assert "type: 'warning'" in sheet


def test_what_crosses_is_read_from_the_viewers_preferences():
    """Criterion 3. The sheet used to rebuild the same four answers from hardcoded defaults every
    time it opened; now it reads the one the person gave."""
    sheet = _sheet()
    for name in CROSSINGS:
        assert f"SW.prefs.get('{name}')" in sheet


def test_the_preferences_sit_with_the_conversation_view_preference():
    """Criterion 4. One drawer, not a second settings surface — these are the same kind of answer
    as the conversation view: about how this person works, and the same in every Project."""
    drawer = _settings_drawer()
    for name in CROSSINGS:
        assert f"'{name}'" in drawer
    assert "conversationView" in drawer


def test_the_target_app_never_becomes_a_preference():
    """The ticket says it twice: persist what crosses, never where it lands. #52 held this line for
    a surface that stored one preference, and it has to hold now that the surface stores the
    handoff's other answers."""
    assert "appId" not in (_JS / "prefs.js").read_text()
    assert "appId" not in _settings_drawer()


def test_a_confirm_that_says_nothing_still_writes_what_the_drawer_promises():
    """The server keeps its own fallbacks for a caller that sends no `include` — the sheet's answer
    is not the only way in. The two sets have to agree: if they drift, the drawer promises one file
    set and a silent confirm writes another, and nothing in either half would say so."""
    service = (_SAGE / "orchestrator" / "service.py").read_text()
    assert 'include.get("resources", True)' in service
    assert 'include.get("artifacts", True)' in service
    assert 'include.get("transcript", False)' in service
    assert _run([{"viewer": "u1", "op": "get", "name": n} for n in CROSSINGS]) == [True, True, False]


def test_the_callout_is_left_exactly_where_it_was():
    """The ticket keeps the offer as it is: once per Conversation, declinable permanently, and a
    classifier deliberately biased against suggesting. Shrinking the sheet behind it must not
    quietly widen it — and declining still has to leave the manual route into Build."""
    store = (_JS / "store.js").read_text()
    assert "handoff: 'suppress'" in store          # Not now is still permanent
    assert "'plan_suggestion'" in (_JS / "components" / "message-blocks.js").read_text()
    assert "draftHandoffPlan" in (_JS / "modes" / "chat.js").read_text()  # Open in Build survives
