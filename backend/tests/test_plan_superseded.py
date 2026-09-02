"""A second plan supersedes the first instead of clobbering it (#59).

#67 already made the live plan file one per Built App, so two Conversations handing off to
DIFFERENT apps no longer meet at all. What is left is two Conversations handing off to the SAME
one, and there the second still wrote straight over `apps/<appId>/.sage/plan.md`. The sheet's
"This replaces the plan in <app>" was the only thing standing between them, and a warning is not
an outcome: the first Conversation was never told, so its plan card went on offering
"Approve & build" for a plan the app no longer holds.

Nothing here deletes anything. The archive path `archive_plan()` already keeps history and the
plan document was never in danger; what changes is that the live copy MOVES instead of being
overwritten, the earlier document says it was superseded and by which Conversation, and the
earlier Conversation's transcript carries that fact so its card can report it.

The archive is written `NNN-superseded.md` rather than `NNN.md` for the same reason a cancelled
one is: `read_archived_plan` answers "what was this app built from", and a plan nobody built is
not an answer to that.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for any routed request: the Chat/Build classifier is its only caller."""

    def __init__(self, verdict: str = "CHAT") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _plan(title: str, step: str) -> str:
    return (f"{title}\n\n"
            "## Plan\n"
            f"1. **{step}** — Show it.\n\n"
            "## Open questions\n"
            "- None, ready to build.\n")


_DESK = _plan("A desk exposure dashboard.", "Desk table")
_BURNDOWN = _plan("A burndown chart.", "Burndown")
_NOTHING_EXTRA = {"resources": False, "artifacts": False, "transcript": False}


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building an app\n\nSage's rules go here.\n")
    return t


def _orch(tmp: Path, turns: list[Turn] | None = None, *, verdict: str = "CHAT"):
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, oc, root


def _gate_in_build(orch, ask: str, conversation: str) -> list[dict]:
    """A first turn on a Built App, driven from a Build conversation the way the Workbench drives
    it: typing in Build opens a Thread first and every turn names it."""
    return list(orch.build_stream(ask, conversation=conversation))


def _two_conversations_into_one_app(tmp_path: Path):
    """The whole case the ticket is about: conversation A leaves a plan awaiting approval in an
    app, and conversation B plans into that same app without A ever approving."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK), Turn(text=_BURNDOWN)])
    first = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a desk exposure dashboard", first)
    app_id = orch.project(start_preview=False).workspace.app_id

    second = orch.create_thread()["id"]
    _gate_in_build(orch, "no, build me a burndown chart", second)

    return orch, root, app_id, first, second


def _plans_dir(root: Path, app_id: str) -> Path:
    return root / "apps" / app_id / ".sage" / "plans"


# ---- the live copy moves, it is not overwritten -------------------------------------------


def test_a_second_conversation_archives_the_first_live_plan_rather_than_overwriting_it(tmp_path):
    """Criteria 1 and 2. The earlier live copy is on disk under the app, and the newer plan is the
    one the builder would now consume."""
    _orchestrator, root, app_id, _first, _second = _two_conversations_into_one_app(tmp_path)

    archived = sorted(_plans_dir(root, app_id).glob("*.md"))
    assert [p.name for p in archived] == ["001-superseded.md"]
    assert archived[0].read_text().startswith("A desk exposure")
    assert (root / "apps" / app_id / ".sage" / "plan.md").read_text().startswith("A burndown")


def test_a_superseded_plan_is_not_what_the_app_was_built_from(tmp_path: Path):
    """The archive is history, not a build. Nobody approved the desk plan, so the rail's plan pin
    must not start claiming the app was built from it — the same rule a cancelled plan follows."""
    orch, _root, _app_id, _first, _second = _two_conversations_into_one_app(tmp_path)

    assert orch.project(start_preview=False).workspace.read_archived_plan() is None
    # "draft", not "awaiting": the pin now carries the live document's own status rather than a
    # blanket "not built yet", and nobody has reviewed this one.
    assert orch.read_plan_pin()["status"] == "draft"
    assert orch.read_plan_pin()["markdown"].startswith("A burndown")


# ---- the earlier document survives, and says what happened to it --------------------------


def test_the_earlier_plan_document_survives_with_its_versions_and_its_comments(tmp_path: Path):
    """Criterion 4, and the rule the whole ticket rests on: nothing is deleted. A comment left on
    the desk plan is still there afterwards, and its body still reads as it did."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK), Turn(text=_BURNDOWN)])
    first = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a desk exposure dashboard", first)
    orch.review_plan_doc("001", {"action": "comment", "text": "Which desks?"})
    before = orch.read_plan_doc("001")

    second = orch.create_thread()["id"]
    _gate_in_build(orch, "no, build me a burndown chart", second)

    after = orch.read_plan_doc("001")
    assert after is not None
    assert after["markdown"] == before["markdown"]
    assert after["version"] == before["version"]
    assert [c["text"] for c in after["comments"]] == ["Which desks?"]


def test_the_earlier_plan_document_records_which_conversation_superseded_it(tmp_path: Path):
    """Criterion 5's durable half, and what #54 bought: a plan records the Conversation that
    produced it, so the newer plan can be named by more than an id."""
    orch, _root, _app_id, first, second = _two_conversations_into_one_app(tmp_path)

    doc = orch.read_plan_doc("001")
    assert doc["status"] == "superseded"
    assert doc["supersededBy"] == "002"
    assert doc["supersededByThreadId"] == second
    assert orch.read_plan_doc("002")["originThreadId"] == second
    assert doc["originThreadId"] == first
    assert orch.read_plan_doc("002").get("status") != "superseded"


def test_the_earlier_conversation_is_told_its_plan_was_superseded(tmp_path: Path):
    """Criterion 5. The defect: the first Conversation was never told, so its card went on looking
    like current intent. The fact lands in that conversation's own transcript, which is what the
    card is rebuilt from."""
    orch, _root, _app_id, first, second = _two_conversations_into_one_app(tmp_path)

    workspace = orch.project(start_preview=False).workspace
    told = [r for r in workspace.read_history(first) if r["type"] == "plan-superseded"]
    assert len(told) == 1
    assert told[0]["planId"] == "001"
    assert told[0]["by"] == "002"
    assert told[0]["conversation"] == first          # it belongs to the conversation being told
    assert told[0]["byConversation"] == second       # and it names the one that superseded it

    # And the conversation that did the superseding is not told about its own plan.
    assert not [r for r in workspace.read_history(second) if r["type"] == "plan-superseded"]


# ---- the cases that must not change -------------------------------------------------------


def test_planning_into_a_different_built_app_leaves_the_first_apps_plan_alone(tmp_path: Path):
    """Criterion 3, which #67 already won — held here so it cannot be lost again. Two apps, two
    live plans, no archive and no supersede anywhere."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK), Turn(text=_BURNDOWN)])
    first = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a desk exposure dashboard", first)
    app_a = orch.project(start_preview=False).workspace.app_id
    app_b = orch.create_app()["id"]

    second = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a burndown chart", second)

    assert (root / "apps" / app_a / ".sage" / "plan.md").read_text().startswith("A desk exposure")
    assert (root / "apps" / app_b / ".sage" / "plan.md").read_text().startswith("A burndown")
    assert not _plans_dir(root, app_a).exists()
    assert orch.read_plan_doc("001")["status"] == "draft"


def test_planning_into_an_app_with_no_live_plan_behaves_as_it_did(tmp_path: Path):
    """Criterion 7. One conversation, one plan, nothing to supersede: no archive is written and the
    document is an ordinary draft."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK)])
    conversation = orch.create_thread()["id"]

    _gate_in_build(orch, "build me a desk exposure dashboard", conversation)

    app_id = orch.project(start_preview=False).workspace.app_id
    assert not _plans_dir(root, app_id).exists()
    assert orch.read_plan_doc("001")["status"] == "draft"
    assert "supersededBy" not in orch.read_plan_doc("001")


# ---- the Chat half of the same door ------------------------------------------------------


def test_a_chat_handoff_into_an_app_whose_plan_awaits_approval_supersedes_it(tmp_path: Path):
    """The other entry path. A handoff confirmed against an existing Built App writes that app's
    plan.md just as the gate does, so it has to move the live copy aside the same way — and the
    Build conversation still waiting on the old plan has to hear about it."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK),
                                       Turn(text="A burndown, then."), Turn(text=_BURNDOWN)])
    built = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a desk exposure dashboard", built)
    app_id = orch.project(start_preview=False).workspace.app_id

    chat = orch.create_thread()["id"]
    list(orch.chat_stream(chat, "build me a burndown chart"))
    orch.draft_handoff_plan(chat)
    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_id})

    assert [p.name for p in sorted(_plans_dir(root, app_id).glob("*.md"))] == ["001-superseded.md"]
    assert (root / "apps" / app_id / ".sage" / "plan.md").read_text().startswith("A burndown")
    doc = orch.read_plan_doc("001")
    assert doc["status"] == "superseded"
    assert doc["supersededBy"] == "002"
    assert doc["supersededByThreadId"] == chat

    workspace = orch.project(start_preview=False).workspace
    told = [r for r in workspace.read_history(built) if r["type"] == "plan-superseded"]
    assert [r["by"] for r in told] == ["002"]


# ---- what the earlier Conversation sees when it comes back --------------------------------
#
# The card is rebuilt from the transcript on every reload, so these drive `store.loadBuild()` for
# real and read what the card drew. `test_the_ui_says_what_happened` is the prior art.

_HARNESS = Path(__file__).resolve().parent / "js" / "plan_superseded_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PROPOSED = [
    {"type": "user", "text": "build me a desk exposure dashboard"},
    {"type": "plan-proposed", "plan": "A desk exposure dashboard.", "kind": "plan",
     "planId": "001", "steps": 0},
    {"type": "done", "ok": True, "decision": "awaiting approval"},
]
_SUPERSEDED = {"type": "plan-superseded", "planId": "001", "by": "002",
               "byConversation": "conv_second"}


def _card(history: list[dict], threads: list[dict] | None = None) -> dict:
    payload = {"history": history, "threads": threads if threads is not None else []}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_plan_nobody_superseded_still_reads_as_current_intent():
    """The control, and criterion 7 from the card's side. Without the entry the transcript is what
    it was, so the card is still the live one and still the thing to approve."""
    card = _card(_PROPOSED)

    assert card["pending"] is True
    assert card["superseded"] is None
    assert "Approve & build" in card["buttons"]
    assert "Reopen this plan" not in card["buttons"]


@needs_node
def test_the_superseded_card_stops_offering_a_build_and_says_which_conversation_took_it():
    """Criterion 5. The card no longer looks like current intent, and it names the Conversation
    that replaced it rather than pointing at an id — which is what #54 made possible."""
    card = _card(_PROPOSED + [_SUPERSEDED],
                 [{"id": "conv_second", "title": "The burndown talk"}])

    assert card["pending"] is False
    assert "Superseded by a newer plan" in card["text"]
    assert "“The burndown talk” planned this Built App again" in card["text"]
    assert "Approve & build" not in card["buttons"]


@needs_node
def test_the_superseded_card_offers_a_way_back_into_both_plans():
    """Criterion 6, and the rule underneath it. Nothing was deleted, so the card can still open
    this plan — and it offers the newer one too, since that is where intent went."""
    card = _card(_PROPOSED + [_SUPERSEDED],
                 [{"id": "conv_second", "title": "The burndown talk"}])

    assert card["buttons"] == ["Reopen this plan", "Open the newer plan"]
    assert card["opened"] == ["001", "002"]
    assert "This plan is kept, with its comments and every version." in card["text"]


@needs_node
def test_a_conversation_the_rail_cannot_name_still_leaves_a_sentence_that_reads():
    """The superseding Conversation may be one this viewer has not loaded, or one since deleted.
    The card still has to report what happened rather than draw a hole where a title goes."""
    card = _card(_PROPOSED + [_SUPERSEDED])

    assert "Another conversation planned this Built App again" in card["text"]
    assert card["buttons"] == ["Reopen this plan", "Open the newer plan"]


@needs_node
def test_a_superseded_card_that_lost_its_newer_plan_still_offers_the_way_back_in():
    """An entry written before the newer document had an id, or one whose document has since
    gone. The offer that matters — this plan, which is kept — must not go with it."""
    card = _card(_PROPOSED + [{"type": "plan-superseded", "planId": "001",
                               "byConversation": "conv_second"}])

    assert card["pending"] is False
    assert card["buttons"] == ["Reopen this plan"]
    assert card["opened"] == ["001"]


def test_confirming_the_same_handoff_twice_does_not_archive_its_own_plan(tmp_path: Path):
    """A double-confirm reopens the app it already bound rather than minting a twin, and it writes
    the same plan back. There is no earlier plan there to keep, so nothing steps aside."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_DESK)])
    chat = orch.create_thread()["id"]
    list(orch.chat_stream(chat, "build me a desk exposure dashboard"))
    orch.draft_handoff_plan(chat)
    orch.confirm_handoff(chat, _NOTHING_EXTRA)
    app_id = orch.project(start_preview=False).workspace.app_id

    orch.confirm_handoff(chat, _NOTHING_EXTRA)

    assert not _plans_dir(root, app_id).exists()
    assert (root / "apps" / app_id / ".sage" / "plan.md").read_text().startswith("A desk exposure")
    assert orch.read_plan_doc("001")["status"] == "draft"


# ---- which plan the live copy actually IS ---------------------------------------------------
#
# "The newest document" is not the same question as "the document plan.md was written from". A
# plan drafted in Chat is created when the sheet is drafted and only becomes live when it is
# confirmed, which can be long after a Build conversation wrote a newer one into the same app.
# Guessing by date supersedes the wrong document and leaves the one that really lost its live copy
# still reading as current intent — the very defect this ticket is about.


def _desk_planned_in_chat_lands_after_a_burndown_planned_in_build(tmp_path: Path):
    """Chat drafts first, Build plans second, Chat confirms third. The live plan is then the
    OLDER document, and the newer one is the superseded one."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_DESK),
                                       Turn(text=_BURNDOWN), Turn(text=_plan("A rota.", "Rota"))])
    chat = orch.create_thread()["id"]
    list(orch.chat_stream(chat, "build me a desk exposure dashboard"))
    orch.draft_handoff_plan(chat)                       # 001, drafted first, no app yet

    build = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a burndown chart", build)   # 002, live in the app
    app_id = orch.project(start_preview=False).workspace.app_id

    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_id})   # 001 is live now, 002 lost it
    return orch, root, app_id, chat, build


def test_the_older_document_can_be_the_live_one_and_the_newer_one_the_superseded_one(tmp_path):
    """The setup itself, asserted: confirming supersedes the plan that held plan.md, not the plan
    that happens to be newest."""
    orch, _root, _app_id, chat, build = _desk_planned_in_chat_lands_after_a_burndown_planned_in_build(
        tmp_path)

    assert orch.read_plan_doc("002")["status"] == "superseded"
    assert orch.read_plan_doc("002")["supersededByThreadId"] == chat
    assert orch.read_plan_doc("001")["status"] == "draft"
    assert orch.read_plan_doc("001")["originThreadId"] == chat
    assert orch.read_plan_doc("002")["originThreadId"] == build


def test_a_third_plan_supersedes_the_one_that_holds_the_live_copy_not_the_newest_one(tmp_path):
    """The bug a date-ordered guess produces. A third Conversation plans into the same app: what
    it takes is 001's live copy, so 001 is what must be marked and 001's Conversation is what must
    be told. Marking 002 again would leave the Conversation that really lost its plan still
    looking at current intent, and would re-stamp one that already knows."""
    orch, _root, _app_id, chat, _build = (
        _desk_planned_in_chat_lands_after_a_burndown_planned_in_build(tmp_path))
    third = orch.create_thread()["id"]

    _gate_in_build(orch, "actually build me a rota", third)

    assert orch.read_plan_doc("001")["status"] == "superseded"
    assert orch.read_plan_doc("001")["supersededBy"] == "003"
    assert orch.read_plan_doc("001")["supersededByThreadId"] == third
    # 002 keeps the supersede it already had rather than collecting a second one.
    assert orch.read_plan_doc("002")["supersededBy"] == "001"

    workspace = orch.project(start_preview=False).workspace
    assert [r["planId"] for r in workspace.read_history(chat)
            if r["type"] == "plan-superseded"] == ["001"]


def test_the_plan_pin_names_the_document_the_live_plan_came_from(tmp_path: Path):
    """A superseded document is not this app's plan any more, so the rail must not pin its id
    beside the newer plan's markdown — and an edit to it must not reach the live copy."""
    orch, _root, _app_id, _chat, _build = (
        _desk_planned_in_chat_lands_after_a_burndown_planned_in_build(tmp_path))

    pin = orch.read_plan_pin()
    assert pin["planId"] == "001"
    assert pin["markdown"].startswith("A desk exposure")


def test_a_live_plan_with_no_document_behind_it_is_still_kept(tmp_path: Path):
    """A plan.md written before plan documents existed, which is what an upgraded project has.
    There is no document to mark and no card to correct, but the rule is the rule: it is archived
    rather than written over."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK)])
    workspace = orch.project(start_preview=False).workspace
    workspace.write_plan("An older plan nobody recorded.\n")

    conversation = orch.create_thread()["id"]
    _gate_in_build(orch, "build me a desk exposure dashboard", conversation)

    archived = sorted(_plans_dir(root, workspace.app_id).glob("*.md"))
    assert [p.name for p in archived] == ["001-superseded.md"]
    assert archived[0].read_text() == "An older plan nobody recorded.\n"
    assert (root / "apps" / workspace.app_id / ".sage" / "plan.md").read_text().startswith(
        "A desk exposure")
