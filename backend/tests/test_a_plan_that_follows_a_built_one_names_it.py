"""A plan written after a build names the plan that build consumed (#277).

Every gated plan turn writes a NEW document, and that is right: a change request is a different plan,
not a redraft, and the document behind a built plan has to stay frozen as the record of what shipped
(one status, one reviewer set, one comment thread). What was missing is the edge between them. The
only writer of a link between two documents was `_supersede_live_plan`, which returns early when
there is no live `plan.md` — and after a build there never is, because ADR-0007 archives it. So the
chain was recorded only for a plan nobody built (the #59 race) and never for the ordinary flow:
plan → approve → build → ask for a change → plan again.

`previousPlanId` is that edge, and it is deliberately NOT `supersededBy`. The two answer different
questions about different plans: `supersededBy` is written on a plan that lost its live copy before
anybody built it, and `previousPlanId` is written on the NEW plan and names the one the app was
actually built from. A built plan stays `approved` and gains nothing — see `test_the_plan_that_was_
built_is_not_touched_by_the_plan_that_follows_it`.

Asserted on the public surface: the stored documents, read back through the orchestrator the way the
plans panel reads them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the Chat/Build classifier is its only caller here."""

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The same two waits test_turn_path strips: a scripted turn can only spend them."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


_DESK = "A desk exposure dashboard.\n\n## Plan\n1. **Table** — Notional by desk.\n"
_FILTER = "A desk exposure dashboard with a date filter.\n\n## Plan\n1. **Filter** — By date.\n"
_CHART = "A desk exposure dashboard with a chart.\n\n## Plan\n1. **Chart** — Daily move.\n"
_BURNDOWN = "A burndown chart.\n\n## Plan\n1. **Burndown** — Remaining by day.\n"
_NOTHING_EXTRA = {"files": [], "artifacts": [], "resources": []}

CONVERSATION = "conv_desk"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building an app\n\nSage's rules go here.\n")
    return t


def _orch(tmp: Path, turns: list[Turn], *, verdict: str = "BUILD"):
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, turns)
    orch = Orchestrator(workspace_dir=root, template=_template(tmp),
                        gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc, root


def _plan_in_build(orch: Orchestrator, ask: str, conversation: str = CONVERSATION) -> None:
    """A turn that gates. The first one gates by itself (nothing is built yet); a later one has to
    be asked for, which is what picking Plan in the composer does."""
    orch.project(start_preview=False).control.set_mode(Mode.PLAN)
    list(orch.build_stream(ask, conversation=conversation))


def _approve(orch: Orchestrator, conversation: str = CONVERSATION) -> None:
    orch.project(start_preview=False).control.set_mode(Mode.AUTO)
    list(orch.approve_stream(conversation=conversation))


def _build_turn() -> Turn:
    return Turn(writes={"src/App.tsx": "// built\n"})


def _planned_approved_and_planned_again(tmp_path: Path):
    """The whole case the ticket is about: one plan built, then a change request planned."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK), _build_turn(), Turn(text=_FILTER)])
    _plan_in_build(orch, "build me a desk exposure dashboard")
    _approve(orch)
    _plan_in_build(orch, "now add a date filter")
    return orch, root


# ---- the edge itself ----------------------------------------------------------------------


def test_a_plan_written_after_a_build_names_the_plan_that_was_built(tmp_path: Path):
    """The defect. Before this, documents 001 and 002 shared nothing but `appId`, and nothing on
    disk could answer "which plan came before this one for this app"."""
    orch, _root = _planned_approved_and_planned_again(tmp_path)

    assert orch.read_plan_doc("002")["previousPlanId"] == "001"


def test_the_plan_that_was_built_is_not_touched_by_the_plan_that_follows_it(tmp_path: Path):
    """`superseded` is the wrong word for this edge and stays the wrong word: that plan WAS built,
    it was not replaced unbuilt. The record of what shipped is frozen, so the new document carries
    the whole edge and the built one gains nothing."""
    orch, _root = _planned_approved_and_planned_again(tmp_path)

    built = orch.read_plan_doc("001")
    assert built["status"] == "approved"
    assert "supersededBy" not in built
    assert built["previousPlanId"] == ""


def test_the_first_plan_for_an_app_names_no_earlier_plan(tmp_path: Path):
    """Nothing was built before it, so "" is the honest answer rather than a guess at the newest
    other document."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK)])

    _plan_in_build(orch, "build me a desk exposure dashboard")

    assert orch.read_plan_doc("001")["previousPlanId"] == ""


def test_a_third_plan_names_the_second_rather_than_the_first(tmp_path: Path):
    """A chain, not a flat "everything after the first build". Each plan names the one the app was
    built from when it was written, so a reader can walk backwards one step at a time."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK), _build_turn(),
                                        Turn(text=_FILTER), _build_turn(),
                                        Turn(text=_CHART)])
    _plan_in_build(orch, "build me a desk exposure dashboard")
    _approve(orch)
    _plan_in_build(orch, "now add a date filter")
    _approve(orch)
    _plan_in_build(orch, "now add a chart")

    assert orch.read_plan_doc("003")["previousPlanId"] == "002"
    assert orch.read_plan_doc("002")["previousPlanId"] == "001"


# ---- the edge this one is NOT ---------------------------------------------------------------


def test_a_plan_that_replaces_an_unbuilt_one_names_no_earlier_plan(tmp_path: Path):
    """The #59 case, held here so the two fields cannot be collapsed into one. Nobody built the
    first plan, so there is no plan this app "follows" — what happened to it is `supersededBy`, on
    the plan it happened to."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK), Turn(text=_BURNDOWN)])
    _plan_in_build(orch, "build me a desk exposure dashboard", "conv_first")
    _plan_in_build(orch, "no, build me a burndown chart", "conv_second")

    assert orch.read_plan_doc("002")["previousPlanId"] == ""
    assert orch.read_plan_doc("001")["supersededBy"] == "002"


def test_a_plan_for_a_different_app_names_that_apps_own_history(tmp_path: Path):
    """The question is per-app. A second Built App has been built from nothing, so its first plan
    follows nothing — even though the Project already holds a built document."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK), _build_turn(), Turn(text=_BURNDOWN)])
    _plan_in_build(orch, "build me a desk exposure dashboard")
    _approve(orch)
    orch.create_app()

    _plan_in_build(orch, "build me a burndown chart", "conv_second")

    assert orch.read_plan_doc("002")["previousPlanId"] == ""


# ---- the Chat door ---------------------------------------------------------------------------


def test_a_chat_plan_confirmed_into_a_built_app_names_the_plan_it_follows(tmp_path: Path):
    """A plan drafted in Chat is written before any app exists, so it cannot know what it follows
    at creation. It learns it at the confirm, which is the same moment it learns its `appId`."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK), _build_turn(),
                                        Turn(text="A burndown, then."), Turn(text=_BURNDOWN)])
    _plan_in_build(orch, "build me a desk exposure dashboard")
    _approve(orch)
    app_id = orch.project(start_preview=False).workspace.app_id

    chat = orch.create_thread()["id"]
    list(orch.chat_stream(chat, "build me a burndown chart"))
    orch.draft_handoff_plan(chat)
    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_id})

    assert orch.read_plan_doc("002")["previousPlanId"] == "001"
    assert orch.read_plan_doc("002")["appId"] == app_id


def _confirmed_from_chat_and_built(tmp_path: Path, tail: list[Turn] | None = None):
    """App built from plan 001, then a Chat plan 002 confirmed into it and built too. The state a
    second press of the confirm button lands in — the card is still on screen after the first."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK), _build_turn(),
                                        Turn(text="A burndown, then."), Turn(text=_BURNDOWN),
                                        _build_turn(), *(tail or [])])
    _plan_in_build(orch, "build me a desk exposure dashboard")
    _approve(orch)
    app_id = orch.project(start_preview=False).workspace.app_id

    chat = orch.create_thread()["id"]
    list(orch.chat_stream(chat, "build me a burndown chart"))
    orch.draft_handoff_plan(chat)
    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_id})
    _approve(orch, chat)
    return orch, chat, app_id


def test_confirming_the_same_sheet_twice_does_not_cost_the_plan_its_edge(tmp_path: Path):
    """The confirm card stays on screen, so a second press is reachable — `_supersede_live_plan`
    already names the case. By then the app has been built from this very plan, so what it was
    "last built from" is the plan itself. Stamping that would replace a true edge with a self-loop.

    What a plan follows is fixed when it binds to an app. A second press of the same button is not
    a second binding."""
    orch, chat, app_id = _confirmed_from_chat_and_built(tmp_path)

    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_id})

    assert orch.read_plan_doc("002")["previousPlanId"] == "001"


def test_confirming_the_same_sheet_twice_cannot_make_a_plan_follow_a_later_one(tmp_path: Path):
    """The same press, one plan further on. A third plan has been built since, so the app's answer
    to "what was I last built from" is now a plan written AFTER this one — 002 → 003 → 002, a cycle
    no reader walking back one step at a time can finish."""
    orch, chat, app_id = _confirmed_from_chat_and_built(
        tmp_path, [Turn(text=_CHART), _build_turn()])
    _plan_in_build(orch, "now add a chart", "conv_third")
    _approve(orch, "conv_third")
    assert orch.read_plan_doc("003")["previousPlanId"] == "002"

    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_id})

    assert orch.read_plan_doc("002")["previousPlanId"] == "001"


def test_confirming_a_sheet_into_another_app_and_back_cannot_close_a_loop(tmp_path: Path):
    """The same press again, with the target changed and changed back — the one path that still
    writes after a plan has been built. Rebinding elsewhere clears the edge rather than keeping the
    first app's history, and coming back cannot re-stamp: 002 would name 003, which already names
    002."""
    orch, chat, app_a = _confirmed_from_chat_and_built(
        tmp_path, [Turn(text=_CHART), _build_turn()])
    _plan_in_build(orch, "now add a chart", "conv_third")
    _approve(orch, "conv_third")
    app_b = orch.create_app()["id"]

    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_b})
    assert orch.read_plan_doc("002")["previousPlanId"] == ""

    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_a})

    assert orch.read_plan_doc("002")["previousPlanId"] == ""
    assert orch.read_plan_doc("003")["previousPlanId"] == "002"


def test_a_hand_set_app_id_does_not_cost_a_plan_the_only_stamp_it_gets(tmp_path: Path):
    """Whether this confirm is the first binding is read off the handoff row, which this flow owns.
    The document's own `appId` is not that signal: `patch_plan_doc` lets a client set it, and one
    set to the app about to be confirmed would read as a re-confirm — costing the plan the single
    write its edge ever gets, with nothing left to recover it from."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text=_DESK), _build_turn(),
                                        Turn(text="A burndown, then."), Turn(text=_BURNDOWN)])
    _plan_in_build(orch, "build me a desk exposure dashboard")
    _approve(orch)
    app_id = orch.project(start_preview=False).workspace.app_id
    chat = orch.create_thread()["id"]
    list(orch.chat_stream(chat, "build me a burndown chart"))
    orch.draft_handoff_plan(chat)
    orch.patch_plan_doc("002", {"appId": app_id})

    orch.confirm_handoff(chat, _NOTHING_EXTRA, {"appId": app_id})

    assert orch.read_plan_doc("002")["previousPlanId"] == "001"


def test_a_chat_plan_confirmed_into_a_new_app_names_no_earlier_plan(tmp_path: Path):
    """The app is born at the confirm, so nothing was ever built in it."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text="A burndown, then."), Turn(text=_BURNDOWN)])
    chat = orch.create_thread()["id"]
    list(orch.chat_stream(chat, "build me a burndown chart"))
    orch.draft_handoff_plan(chat)

    orch.confirm_handoff(chat, _NOTHING_EXTRA, {})

    assert orch.read_plan_doc("001")["previousPlanId"] == ""


# ---- the contract every reader gets ----------------------------------------------------------


def test_a_document_written_before_the_field_existed_reads_as_no_earlier_plan(tmp_path: Path):
    """Answered by the reader rather than passed through, the way `archived` is. Every document
    written before #277 has no such key, and leaving it absent would hand the plans panel an
    `undefined` to tell apart from "follows nothing" — two surfaces guessing at the same fact."""
    orch, _oc, root = _orch(tmp_path, [Turn(text=_DESK)])
    _plan_in_build(orch, "build me a desk exposure dashboard")

    meta_path = root / ".sage" / "plan-docs" / "001" / "meta.json"
    meta = json.loads(meta_path.read_text())
    del meta["previousPlanId"]
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    assert orch.read_plan_doc("001")["previousPlanId"] == ""
    assert [d["previousPlanId"] for d in orch.list_plan_docs()] == [""]
