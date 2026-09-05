"""What the working set's Plans group does with a plan somebody put away (#167).

Archiving is reversible, so hiding an archived plan with nothing on screen saying so would fail the
empty-state rule: the person who put it away and now wants it back has no answer to "where did it
go". The group head is that answer — it counts what is hidden and offers the way in.

One interaction had to be got right. The panel marks a row `live` when it matches `activePlanId`,
which comes from `_thread_plan_id`, and that read deliberately still sees archived documents so the
Conversation that produced one goes on showing its plan card. So a document really can be archived
and live at once. Archived wins: a hidden-but-highlighted row is the worst of both.

`test_a_resource_group_offers_a_way_in_whether_or_not_it_is_empty` is the prior art for the harness.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "plans_group_archive_harness.mjs"
_CSS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "css" / "shell.css"

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _act(act: str) -> dict:
    """The panel drawn over two plans, the newer of which is archived and is also the
    Conversation's own current plan."""
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


@_needs_node
def test_an_archived_plan_is_not_listed_in_the_plans_group():
    drawn = _act("drawn")

    assert [row["name"] for row in drawn["rows"]] == ["A consumption dashboard."]
    # And the head's count names what is on screen rather than what exists.
    assert drawn["head"]["label"] == "Plans (1)"


@_needs_node
def test_the_group_head_counts_what_it_hid_and_offers_the_way_back_to_it():
    """The count is on the label rather than in a tooltip: it is the answer to "where did my plan
    go", and a number you have to hover to read is no answer."""
    head = _act("drawn")["head"]

    assert head["archivedLabel"] == "Show archived (1)"
    # A real button, not a clickable span, or it is unreachable by keyboard — the same rule the
    # group's add door is held to.
    assert head["archivedIsButton"] is True
    assert head["archivedPressed"] is False


@_needs_node
def test_an_archived_plan_is_never_drawn_as_the_live_one():
    """The archived document IS the Conversation's current plan here, which is the whole hazard.
    Hidden, it cannot be highlighted; revealed, it still must not be, because the highlight means
    "this is what is being built from" and an archived plan is not."""
    drawn = _act("drawn")
    assert [row["live"] for row in drawn["rows"]] == [False]

    pressed = _act("press")
    revealed = next(r for r in pressed["rows"] if r["name"] == "A desk exposure dashboard.")
    assert revealed["live"] is False


@_needs_node
def test_pressing_the_toggle_shows_the_archived_plan_and_says_it_is_archived():
    """A revealed row that reads exactly like a live one would make the toggle pointless: the
    person pressed it to find out which plan was put away."""
    pressed = _act("press")

    assert [row["name"] for row in pressed["rowsBefore"]] == ["A consumption dashboard."]
    assert [row["name"] for row in pressed["rows"]] == [
        "A desk exposure dashboard.", "A consumption dashboard."]
    revealed = pressed["rows"][0]
    assert revealed["subtitle"].startswith("Archived")
    # The review outcome is still on it, beside the word: archiving is a flag, not a status.
    assert "Approved" in revealed["subtitle"]


@_needs_node
def test_the_toggle_says_it_is_showing_them_once_it_is():
    """A control that reports itself pressed while its own words still say "Show" is a control that
    cannot be pressed back."""
    head = _act("press")["head"]

    assert head["archivedLabel"] == "Hide archived (1)"
    assert head["archivedPressed"] is True
    # And the count is off the whole list, so it does not fall to zero the moment they are shown.
    assert head["label"] == "Plans (2)"


@_needs_node
def test_showing_them_into_a_folded_group_unfolds_it():
    """The rows sit under the group's own caret. Pressing the way in on a folded group flipped the
    label to "Hide archived (1)" and bumped the count while nothing appeared — a control reporting
    itself pressed with nothing on screen to show for it."""
    pressed = _act("press-while-collapsed")

    assert pressed["head"]["collapsed"] is False
    assert [row["name"] for row in pressed["rows"]] == [
        "A desk exposure dashboard.", "A consumption dashboard."]


@_needs_node
def test_hiding_them_again_leaves_the_group_open():
    """The un-collapse belongs to the way in alone. Folding the group again on the way out would
    take the live plans with it, and nobody asked to hide those."""
    back = _act("press-and-back")

    assert back["head"]["archivedLabel"] == "Show archived (1)"
    assert back["head"]["collapsed"] is False
    assert [row["name"] for row in back["rows"]] == ["A consumption dashboard."]


def test_the_toggles_focus_is_visible():
    """Keyboard reach is worth nothing if the focused control is indistinguishable from the rest —
    the same rule the group's add door is held to."""
    assert ".sw-res-group-archived:focus-visible" in _CSS.read_text()


# ---- What the Build rail pins while a document is put away (#171) ----
#
# The guard above stops an archived plan being drawn as the live one, which is the symptom. The
# rail should not have pinned it in the first place: `planId` on a rail row is read by `store.js`
# into `activePlanId` and then loaded into the plan panel, so a pin on a document the Plans group
# is hiding opens it a few pixels from where it is hidden. `_plan_docs_naming_app` is the one place
# that question is answered, and both rail reads go through it now.


class _OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class _ScriptedGateway:
    """One scripted word for every routed request. No turn is run here; the rail is read cold."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _plan_markdown(title: str) -> str:
    return (f"{title}\n\n"
            "## Plan\n"
            "1. **Desk table** — Show notional by desk.\n\n"
            "## Open questions\n"
            "None — ready to build.\n")


def _rail(tmp: Path):
    """A Project with one Built App in it and nothing pinned to it yet."""
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

    from .fake_opencode import FakeOpenCode

    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text('{"name": "template"}')
    (template / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")

    root = tmp / "mnt" / "code"
    orch = Orchestrator(workspace_dir=root, template=template, gateway=_ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=_OkFeedback(),
                        opencode_client=FakeOpenCode(root, []))
    app_id = orch.create_app()["id"]
    return orch, orch.project(start_preview=False, seed_app=False).record, app_id


def _pinned(orch, app_id: str) -> str:
    """The rail's answer, asserted to be one answer: `list_apps` and `_one_app` are two copies of
    the same question, and a fix to one that missed the other would be the same bug again."""
    row = next(r for r in orch.list_apps() if r["id"] == app_id)
    assert orch._one_app(app_id)["planId"] == row["planId"]
    return row["planId"]


def test_the_rail_does_not_pin_an_app_to_its_newest_archived_plan(tmp_path: Path):
    """The pin skips it and lands on the newest document still in the Plans group, which is the
    plan the person can actually see."""
    orch, record, app_id = _rail(tmp_path)
    live = record.create_plan_doc(_plan_markdown("A consumption dashboard."),
                                  title="A consumption dashboard.", app_id=app_id)
    away = record.create_plan_doc(_plan_markdown("A desk exposure dashboard."),
                                  title="A desk exposure dashboard.", app_id=app_id)
    assert _pinned(orch, app_id) == away["id"]

    record.patch_plan_doc_meta(away["id"], archived=True)

    assert _pinned(orch, app_id) == live["id"]


def test_the_rail_does_not_pin_an_app_to_its_newest_superseded_plan(tmp_path: Path):
    """The older half of the same bug: a superseded document lost its live copy to a newer plan,
    so pinning it would open one plan beside another one's markdown."""
    orch, record, app_id = _rail(tmp_path)
    live = record.create_plan_doc(_plan_markdown("A consumption dashboard."),
                                  title="A consumption dashboard.", app_id=app_id)
    stepped = record.create_plan_doc(_plan_markdown("A desk exposure dashboard."),
                                     title="A desk exposure dashboard.", app_id=app_id)
    record.patch_plan_doc_meta(stepped["id"], status="superseded")

    assert _pinned(orch, app_id) == live["id"]


def test_an_app_whose_only_plans_are_put_away_pins_nothing(tmp_path: Path):
    """The behaviour change. The app used to pin one of them; it pins nothing now, which is the
    answer an app that never had a plan gives — and the rail already draws that app."""
    orch, record, app_id = _rail(tmp_path)
    assert _pinned(orch, app_id) == ""                      # the app with no plan at all
    archived = record.create_plan_doc(_plan_markdown("A desk exposure dashboard."),
                                      title="A desk exposure dashboard.", app_id=app_id)
    record.patch_plan_doc_meta(archived["id"], archived=True)
    superseded = record.create_plan_doc(_plan_markdown("A consumption dashboard."),
                                        title="A consumption dashboard.", app_id=app_id)
    record.patch_plan_doc_meta(superseded["id"], status="superseded")

    assert _pinned(orch, app_id) == ""


def test_taking_a_plan_back_out_of_the_archive_restores_the_pin(tmp_path: Path):
    """Archiving is reversible, so everything it took away has to come back — including the pin,
    or the plan is out of the archive and still not the one the rail opens."""
    orch, record, app_id = _rail(tmp_path)
    doc = record.create_plan_doc(_plan_markdown("A desk exposure dashboard."),
                                 title="A desk exposure dashboard.", app_id=app_id)
    record.patch_plan_doc_meta(doc["id"], archived=True)
    assert _pinned(orch, app_id) == ""

    record.patch_plan_doc_meta(doc["id"], archived=False)

    assert _pinned(orch, app_id) == doc["id"]


def test_a_plan_put_away_in_one_app_does_not_move_another_apps_pin(tmp_path: Path):
    """One read serves the whole rail, so the filter has to be applied per app rather than once
    over the list — a shared read that dropped documents for everybody would be a new bug."""
    orch, record, first = _rail(tmp_path)
    second = orch.create_app()["id"]
    mine = record.create_plan_doc(_plan_markdown("A consumption dashboard."),
                                  title="A consumption dashboard.", app_id=first)
    theirs = record.create_plan_doc(_plan_markdown("A desk exposure dashboard."),
                                    title="A desk exposure dashboard.", app_id=second)
    record.patch_plan_doc_meta(theirs["id"], archived=True)

    assert _pinned(orch, first) == mine["id"]
    assert _pinned(orch, second) == ""
