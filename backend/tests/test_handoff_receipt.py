"""The plan card reports what crossed (#60).

#58 took the four questions off the handoff sheet, which is what created the need for this: the
crossing became silent, and a silent crossing is the magic docs/workbench/handoff.md §1 forbids.
Everything that crosses is written to the Project as a real file precisely so it can be inspected,
so the card that already stands at the end of a handoff is where the person is told what went and
where it went.

The card is rebuilt from the Conversation's transcript on every reload, so the receipt has to live
there. It rides on the `plan-proposed` row the confirm already writes — not on a row of its own,
because a second row is a second card and "only one card appears for a handoff" is a criterion.

Change redoes the crossing. It deliberately does NOT run the confirm again: a confirm writes a plan
card, and a second card for one handoff is the thing forbidden above. It rewrites what crosses and
appends `handoff-recrossed`, which the card folds onto the receipt it already has. The target is not
a parameter and never becomes one — which Built App a handoff lands in is a per-handoff decision
(ADR-0008).

Undo means "I am not building this", never "erase what happened". It is the existing cancel path,
which archives non-destructively; the plan document, the digest, the Bindings and the Artifacts
under `examples/` are all left exactly where they are, and a Built App the handoff minted stays.
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
from sage.workspace.threads import ThreadStore

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


_DESK = ("A desk exposure dashboard.\n\n"
         "## Plan\n1. **Desk table** — Show it.\n\n"
         "## Open questions\n- None, ready to build.\n")
_REPORT = ("A daily P&L report.\n\n"
           "## Plan\n1. **P&L table** — Show it.\n\n"
           "## Open questions\n- None, ready to build.\n")

ALL_OFF = {"resources": False, "artifacts": False, "transcript": False}
ALL_ON = {"resources": True, "artifacts": True, "transcript": True}


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
    orch = Orchestrator(workspace_dir=root, template=_template(tmp),
                        gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, oc, root


def _store(orch) -> ThreadStore:
    return ThreadStore(orch.project(start_preview=False).record.path)


def _a_conversation_with_something_to_carry(tmp_path: Path, *, more: list[Turn] | None = None,
                                            verdict: str = "CHAT"):
    """A Chat Conversation holding a chart and a Data Source, drafted into a plan and no further.

    Both halves matter to the receipt: the chart is what "list the specific charts" means, and the
    Data Source is a context row that becomes a Binding rather than a file. `more` scripts the
    turns a test needs after this one; `verdict` is APP for a test that hands off twice, because a
    second handoff starts with a fresh suggestion on a Conversation that already bound one.
    """
    orch, _oc, root = _orch(tmp_path,
                            [Turn(text="A dashboard, then."), Turn(text=_DESK)] + (more or []),
                            verdict=verdict)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk exposure dashboard"))
    store = _store(orch)
    art = root / "examples" / tid
    art.mkdir(parents=True, exist_ok=True)
    (art / "by-desk.table.json").write_text("[]")
    store.record_artifact(tid, path=f"examples/{tid}/by-desk.table.json")
    store.write_context(tid, {"items": [
        {"id": "ctx_1", "kind": "data_source", "name": "trades",
         "bindingKey": ["data_source", "ds-1"]},
    ]})
    orch.draft_handoff_plan(tid)
    return orch, root, tid


def _receipt(orch, tid: str) -> dict:
    """What the card reads: the crossing recorded on this Conversation's plan card row, with any
    later Change folded on the way the card folds it."""
    rows = orch.project(start_preview=False).workspace.read_history(tid)
    crossed: dict = {}
    for row in rows:
        if row.get("type") in ("plan-proposed", "handoff-recrossed") and row.get("crossed"):
            crossed = {**crossed, **row["crossed"]}
    return crossed


# ---- the card names what crossed, and where it went ----------------------------------------


def test_the_plan_card_row_carries_what_crossed(tmp_path: Path):
    """Criterion 1. The receipt rides on the row the card is already built from, so a reload
    reports the crossing rather than forgetting it."""
    orch, _root, tid = _a_conversation_with_something_to_carry(tmp_path)

    orch.confirm_handoff(tid, ALL_ON)

    rows = orch.project(start_preview=False).workspace.read_history(tid)
    proposed = [r for r in rows if r["type"] == "plan-proposed"]
    assert len(proposed) == 1
    crossed = proposed[0]["crossed"]
    assert crossed["resources"] is True
    assert crossed["artifacts"] is True
    assert crossed["transcript"] is True
    assert crossed["conversation"] == tid


def test_the_receipt_lists_the_specific_charts_and_context_that_crossed(tmp_path: Path):
    """Criterion 2. Not counts — the chart by name and the path it sits at, because the point of
    writing everything to disk is that the person can go and look at it."""
    orch, _root, tid = _a_conversation_with_something_to_carry(tmp_path)

    orch.confirm_handoff(tid, ALL_ON)

    crossed = _receipt(orch, tid)
    assert [c["path"] for c in crossed["charts"]] == [f"examples/{tid}/by-desk.table.json"]
    assert crossed["charts"][0]["title"]
    assert crossed["context"] == ["trades"]
    assert ".sage/plan.md" in crossed["files"]
    assert ".sage/handoff.md" in crossed["files"]
    assert ".sage/handoff-transcript.md" in crossed["files"]
    assert ".sage/bindings.json" in crossed["files"]


def test_the_receipt_names_the_built_app_and_says_it_is_a_new_one(tmp_path: Path):
    """Criterion 3, the New app half. The name is the app's, not the Project's — a Project holds
    many (ADR-0008), so "your app" would name nothing."""
    orch, _root, tid = _a_conversation_with_something_to_carry(tmp_path)

    orch.confirm_handoff(tid, ALL_OFF)

    app_id = orch.project(start_preview=False).workspace.app_id
    crossed = _receipt(orch, tid)
    assert crossed["appId"] == app_id
    assert crossed["appName"] == "A desk exposure dashboard."
    assert crossed["newApp"] is True


def test_the_receipt_says_when_the_plan_went_into_an_app_that_already_existed(tmp_path: Path):
    """Criterion 3, the other half. Building into an app somebody already has is a different fact
    to minting one, and it is the fact the person needs when they wonder what happened to it."""
    orch, _root, tid = _a_conversation_with_something_to_carry(tmp_path)
    existing = orch.create_app()["id"]

    orch.confirm_handoff(tid, ALL_OFF, {"appId": existing})

    crossed = _receipt(orch, tid)
    assert crossed["appId"] == existing
    assert crossed["newApp"] is False


def test_a_crossing_that_carried_nothing_extra_says_so(tmp_path: Path):
    """The default-off transcript, and both preferences turned off. The receipt has to report an
    empty crossing as empty rather than fall back to naming everything the Conversation holds."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)

    orch.confirm_handoff(tid, ALL_OFF)

    crossed = _receipt(orch, tid)
    assert crossed["charts"] == []
    assert crossed["context"] == []
    assert crossed["transcript"] is False
    assert ".sage/handoff-transcript.md" not in crossed["files"]
    app_id = orch.project(start_preview=False).workspace.app_id
    assert not (root / "apps" / app_id / ".sage" / "handoff-transcript.md").exists()


# ---- Change redoes the crossing -------------------------------------------------------------


def test_change_redoes_the_crossing_with_different_choices(tmp_path: Path):
    """Criterion 4. The files on disk are what changed, not just the sentence on the card: the
    transcript that was left out is written, and the receipt says so."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_OFF)
    app_id = orch.project(start_preview=False).workspace.app_id
    transcript = root / "apps" / app_id / ".sage" / "handoff-transcript.md"
    assert not transcript.exists()

    orch.recross_handoff(tid, ALL_ON)

    assert transcript.exists()
    crossed = _receipt(orch, tid)
    assert crossed["transcript"] is True
    assert [c["path"] for c in crossed["charts"]] == [f"examples/{tid}/by-desk.table.json"]
    assert crossed["context"] == ["trades"]


def test_change_leaves_one_card_for_the_handoff(tmp_path: Path):
    """Criterion 11, which is why Change is not simply a second confirm. A confirm writes a plan
    card, so running one again would leave the person reading two cards for one crossing."""
    orch, _root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_OFF)

    orch.recross_handoff(tid, ALL_ON)
    orch.recross_handoff(tid, ALL_OFF)

    rows = orch.project(start_preview=False).workspace.read_history(tid)
    assert len([r for r in rows if r["type"] == "plan-proposed"]) == 1


def test_change_does_not_re_target_the_app(tmp_path: Path):
    """Criterion 5. ADR-0008 makes the target a per-handoff decision the sheet asks every time, so
    Change has nowhere to put an answer to it: no new app is minted, and the one already bound is
    the one rewritten."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_OFF)
    app_id = orch.project(start_preview=False).workspace.app_id

    orch.recross_handoff(tid, ALL_ON)

    assert [p.name for p in (root / "apps").iterdir()] == [app_id]
    assert orch.project(start_preview=False).workspace.app_id == app_id
    assert _receipt(orch, tid)["appId"] == app_id
    # And where it went is carried forward rather than re-decided by the row that redoes what
    # crossed: the new answer is about the crossing only.
    assert _receipt(orch, tid)["newApp"] is True


def test_change_does_not_disturb_the_plan_awaiting_approval(tmp_path: Path):
    """Change is about what crosses. The plan is not one of the answers it redoes, so the copy the
    builder consumes is untouched and nothing is archived behind it."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_OFF)
    app_id = orch.project(start_preview=False).workspace.app_id

    orch.recross_handoff(tid, ALL_ON)

    assert (root / "apps" / app_id / ".sage" / "plan.md").read_text().startswith("A desk exposure")
    assert not (root / "apps" / app_id / ".sage" / "plans").exists()
    assert orch.read_plan_doc("001")["status"] == "draft"


def test_change_redoes_the_crossing_the_card_is_showing_not_the_newest_one(tmp_path: Path):
    """One Conversation can hand off twice, to a different Built App each time (ADR-0008), and both
    cards are in its transcript. The card names the plan it belongs to, so Change reaches the
    crossing the person is looking at — the newest answering for all of them would rewrite the
    second app's crossing from the first app's card."""
    orch, root, tid = _a_conversation_with_something_to_carry(
        tmp_path, more=[Turn(text="A report, then."), Turn(text=_REPORT)], verdict="APP")
    orch.confirm_handoff(tid, ALL_OFF)
    first = orch.project(start_preview=False).workspace.app_id
    list(orch.chat_stream(tid, "and a second one"))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, ALL_OFF)
    second = orch.project(start_preview=False).workspace.app_id
    assert first != second

    orch.recross_handoff(tid, ALL_ON, plan_id="001")

    assert (root / "apps" / first / ".sage" / "handoff-transcript.md").exists()
    assert not (root / "apps" / second / ".sage" / "handoff-transcript.md").exists()


def test_a_crossing_that_stops_carrying_resources_still_names_the_bindings_it_made(tmp_path: Path):
    """Turning Resources off does not withdraw a Binding already made — the sheet says so, and
    taking a Resource from an app that may be reading it is a deliberate act. So the receipt goes
    on naming the file: one that stopped would read as an app connected to nothing."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_ON)
    app_id = orch.project(start_preview=False).workspace.app_id

    orch.recross_handoff(tid, ALL_OFF)

    assert json.loads((root / "apps" / app_id / ".sage" / "bindings.json").read_text())
    assert ".sage/bindings.json" in _receipt(orch, tid)["files"]


def test_change_before_a_handoff_was_ever_confirmed_is_refused(tmp_path: Path):
    """There is no crossing to redo, and inventing one would bind an app nobody asked for — which
    is what confirming is for (ADR-0008)."""
    orch, _root, tid = _a_conversation_with_something_to_carry(tmp_path)

    with pytest.raises(ValueError):
        orch.recross_handoff(tid, ALL_ON)


# ---- Undo -----------------------------------------------------------------------------------


def test_undo_leaves_the_plan_document_the_digest_and_the_artifacts_alone(tmp_path: Path):
    """Criterion 8. Undo says "I am not building this", not "erase what happened" — so the live
    plan is archived by the path that already keeps that history, and nothing else moves."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_ON)
    app_id = orch.project(start_preview=False).workspace.app_id
    app = root / "apps" / app_id

    orch.cancel_plan(conversation=tid, plan_id="001")

    assert orch.read_plan_doc("001") is not None
    assert orch.read_plan_doc("001")["markdown"].startswith("A desk exposure")
    assert (app / ".sage" / "handoff.md").read_text()
    assert (app / ".sage" / "handoff-transcript.md").exists()
    assert json.loads((app / ".sage" / "bindings.json").read_text())
    assert (root / "examples" / tid / "by-desk.table.json").exists()
    # The plan itself moved rather than vanished.
    assert [p.name for p in (app / ".sage" / "plans").glob("*.md")] == ["001-cancelled.md"]


def test_undo_leaves_the_built_app_it_minted_in_place_and_says_so(tmp_path: Path):
    """Criterion 9. Removing the app is #76's deliberate action, never a side effect of deciding
    not to build — so the app is still there, and the Conversation's transcript carries the fact
    the card needs to say it after a reload."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_OFF)
    app_id = orch.project(start_preview=False).workspace.app_id

    orch.cancel_plan(conversation=tid, plan_id="001")

    assert (root / "apps" / app_id).is_dir()
    assert app_id in [row["id"] for row in orch.list_apps()]
    told = [r for r in orch.project(start_preview=False).workspace.read_history(tid)
            if r["type"] == "plan-cancelled"]
    assert [r["planId"] for r in told] == ["001"]


def test_undo_is_safe_to_press_twice(tmp_path: Path):
    """Criterion 10. The second press has nothing to archive, so it archives nothing and records
    nothing — one cancelled plan, one row, whatever the button does."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_OFF)
    app_id = orch.project(start_preview=False).workspace.app_id

    first = orch.cancel_plan(conversation=tid, plan_id="001")
    second = orch.cancel_plan(conversation=tid, plan_id="001")

    assert first["archived"] is True
    assert second["archived"] is False
    plans = sorted(p.name for p in (root / "apps" / app_id / ".sage" / "plans").glob("*.md"))
    assert plans == ["001-cancelled.md"]
    told = [r for r in orch.project(start_preview=False).workspace.read_history(tid)
            if r["type"] == "plan-cancelled"]
    assert len(told) == 1


def test_undo_on_a_card_whose_plan_has_already_moved_cancels_nothing(tmp_path: Path):
    """A tab left open on a plan a second Conversation superseded still draws it as pending (#59
    corrects it on reload, not in place). Undo there must not archive the plan that took its
    place: the person is dismissing the plan on their screen, not one they never saw."""
    orch, root, tid = _a_conversation_with_something_to_carry(
        tmp_path, more=[Turn(text="A report, then."), Turn(text=_REPORT)])
    orch.confirm_handoff(tid, ALL_OFF)
    app_id = orch.project(start_preview=False).workspace.app_id
    second = orch.create_thread()["id"]
    list(orch.chat_stream(second, "build me something else"))
    orch.draft_handoff_plan(second)
    orch.confirm_handoff(second, ALL_OFF, {"appId": app_id})

    assert orch.cancel_plan(conversation=tid, plan_id="001")["archived"] is False

    plans = sorted(p.name for p in (root / "apps" / app_id / ".sage" / "plans").glob("*.md"))
    assert plans == ["001-superseded.md"]        # the supersede, and nothing this Undo did
    assert (root / "apps" / app_id / ".sage" / "plan.md").exists()
    assert not [r for r in orch.project(start_preview=False).workspace.read_history(tid)
                if r["type"] == "plan-cancelled"]


def test_a_cancel_that_names_no_conversation_still_works(tmp_path: Path):
    """The gate's own plan card cancels through the same route and has always been allowed to say
    nothing. It archives exactly as before, and writes no row into a Conversation it cannot name."""
    orch, root, tid = _a_conversation_with_something_to_carry(tmp_path)
    orch.confirm_handoff(tid, ALL_OFF)
    app_id = orch.project(start_preview=False).workspace.app_id

    assert orch.cancel_plan()["archived"] is True

    assert [p.name for p in (root / "apps" / app_id / ".sage" / "plans").glob("*.md")] == [
        "001-cancelled.md"]
    assert not [r for r in orch.project(start_preview=False).workspace.read_history(tid)
                if r["type"] == "plan-cancelled"]


# ---- what the card actually draws ------------------------------------------------------------
#
# The receipt is only worth recording if the card reads it, so these drive `store.loadBuild()` the
# way a person coming back to Build drives it and read what came out. `plan_superseded_harness` is
# the prior art, and this card is the one that ticket also extended.

_HARNESS = Path(__file__).resolve().parent / "js" / "handoff_receipt_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_CROSSED = {
    "conversation": "conv_first",
    "appId": "app_a", "appName": "Desk exposure", "newApp": True,
    "resources": True, "artifacts": True, "transcript": False,
    "charts": [{"title": "By desk", "path": "examples/conv_first/by-desk.table.json"},
               {"title": "By book", "path": "examples/conv_first/by-book.table.json"}],
    "context": ["trades", "positions.csv"],
    "files": [".sage/plan.md", ".sage/handoff.md", "examples/conv_first/", ".sage/bindings.json"],
}


def _proposed(crossed: dict | None) -> list[dict]:
    row = {"type": "plan-proposed", "plan": "A desk exposure dashboard.", "kind": "plan",
           "planId": "001", "steps": 0}
    if crossed is not None:
        row["crossed"] = crossed
    return [row, {"type": "done", "ok": True, "decision": "awaiting approval"}]


def _card(history: list[dict], press: str = "") -> dict:
    payload = {"history": history, "press": press}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_card_names_what_crossed_and_where_it_went():
    """Criteria 1 and 3 from the card's side, and criterion 12: nothing here says a word about
    whether the app is published or how to reach the preview — that is the build-run row's job."""
    card = _card(_proposed(_CROSSED))

    assert "Desk exposure" in card["text"]
    assert "a new Built App" in card["text"]
    assert "2 charts" in card["text"]
    assert "Approve & build" in card["buttons"]
    assert "publish" not in card["text"].lower()
    assert "preview" not in card["text"].lower()


@needs_node
def test_the_detail_expands_to_the_specific_charts_and_context():
    """Criterion 2. Collapsed the card says how much crossed; expanded it says exactly what, by
    the names and paths a person can go and open."""
    collapsed = _card(_proposed(_CROSSED))
    expanded = _card(_proposed(_CROSSED), press="What crossed")

    assert "By desk" not in collapsed["text"]
    assert "By desk" in expanded["text"]
    assert "By book" in expanded["text"]
    assert "trades" in expanded["text"]
    assert "positions.csv" in expanded["text"]
    assert ".sage/handoff.md" in expanded["text"]


@needs_node
def test_a_plan_that_never_came_from_a_handoff_grows_no_receipt():
    """A plan the Build gate wrote crossed nothing, so there is nothing to report and no Change or
    Undo to offer — it keeps the Cancel it has always had."""
    card = _card(_proposed(None))

    assert "crossed" not in card["text"].lower()
    assert "Change what crosses" not in card["buttons"]
    assert "Cancel" in card["buttons"]
    assert "Undo" not in card["buttons"]


@needs_node
def test_the_handoff_card_offers_change_and_undo_instead_of_a_bare_cancel():
    """Criterion 11 again, from the buttons: one card, and one control that stops the build. Undo
    IS the cancel, so a Cancel beside it would be the same button twice."""
    card = _card(_proposed(_CROSSED))

    assert "Change what crosses" in card["buttons"]
    assert "Undo" in card["buttons"]
    assert "Cancel" not in card["buttons"]


@needs_node
def test_undo_goes_through_the_existing_cancel_path_and_says_the_app_stays():
    """Criteria 7 and 9. One POST to the route that already archives plans, and a card that then
    says what it did not do — because a Built App silently left behind is the thing the person
    would otherwise go looking for."""
    card = _card(_proposed(_CROSSED), press="Undo")

    assert card["posted"] == ["/project/plan/cancel"]
    assert card["cancelled"] == [{"conversation": "conv_first", "planId": "001"}]
    assert "Desk exposure" in card["text"]
    assert "stays" in card["text"]
    assert "Approve & build" not in card["buttons"]
    assert "Undo" not in card["buttons"]


@needs_node
def test_a_card_undone_in_an_earlier_session_still_says_so():
    """The record is in the transcript, so coming back to it reads the same as pressing it did.
    Without that, Undo would be a sentence that lived for as long as the tab did."""
    card = _card(_proposed(_CROSSED) + [{"type": "plan-cancelled", "planId": "001"}])

    assert "stays" in card["text"]
    assert "Approve & build" not in card["buttons"]


@needs_node
def test_the_change_sheet_asks_what_crosses_and_never_where_it_lands():
    """Criteria 4, 5 and 6. Three answers and an offer to keep them; no app anywhere in it, and
    nothing offering to remember one — ADR-0008 keeps the target a per-handoff decision."""
    card = _card(_proposed(_CROSSED), press="Change what crosses")

    assert card["sheet"]["open"] is True
    assert card["sheet"]["fields"] == ["resources", "artifacts", "transcript"]
    assert card["sheet"]["values"] == {"resources": True, "artifacts": True, "transcript": False}
    assert card["sheet"]["remember"] is False
    # Nothing to pick a target with, and nothing naming the one this handoff already has.
    assert card["sheet"]["choosers"] == []
    assert "Desk exposure" not in card["sheet"]["text"]
    assert "Built App" not in card["sheet"]["text"]


@needs_node
def test_an_abandoned_change_does_not_come_back_as_an_answer_nobody_gave():
    """The card stays mounted, so a tick left behind by a closed sheet would still be there next
    time — and this sheet's whole claim is that it shows what actually crossed."""
    card = _card(_proposed(_CROSSED),
                 press="Change what crosses|transcript|close|Change what crosses")

    assert card["sheet"]["values"] == {"resources": True, "artifacts": True, "transcript": False}
    assert card["recrossed"] == []


@needs_node
def test_the_change_sheet_saves_the_answers_as_a_preference_only_when_asked():
    """Criterion 6. The crossing is redone either way; the preference moves only if the person
    said to keep it, because one handoff's answer is not a standing answer."""
    kept = _card(_proposed(_CROSSED), press="Change what crosses|remember|Redo the crossing")
    once = _card(_proposed(_CROSSED), press="Change what crosses|Redo the crossing")

    answers = {"resources": True, "artifacts": True, "transcript": False}
    # And the card sends the plan it belongs to, so a Conversation that handed off twice changes
    # the crossing on screen rather than its newest one.
    assert kept["recrossed"] == [{"include": answers, "planId": "001"}]
    assert kept["prefs"] == {"handoffResources": True, "handoffArtifacts": True,
                             "handoffTranscript": False}
    assert once["recrossed"] == [{"include": answers, "planId": "001"}]
    assert once["prefs"] == {}


@needs_node
def test_redoing_the_crossing_leaves_the_app_selected(tmp_path: Path):
    """A recross refreshes what the selected app ships (#95), and refreshing that must not put the
    selection down.

    The comment on the refresh says why the selection matters at exactly this moment: the crossing
    selected the app it wrote into, so a rail that stops highlighting it leaves the next build
    landing somewhere the person is not looking. The card's own press is the only way in — nothing
    else calls `recrossHandoff` — so this is asserted through it rather than against the store."""
    card = _card(_proposed(_CROSSED), press="Change what crosses|Redo the crossing")

    assert card["recrossed"] == [{"include": {"resources": True, "artifacts": True,
                                              "transcript": False}, "planId": "001"}]
    assert card["activeApp"] == "app_a"
