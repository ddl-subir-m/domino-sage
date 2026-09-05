"""Editing an already-built plan and building from that edit, in one click (#149, ADR-0024).

Before this, a plan's text went inert the moment its build finished. Nothing re-read `plan.md` — the
build archives it — so a person who spotted a wrong number in a plan they had just built could edit
the document all they liked and no build would ever see it. The only way to change the app was to
describe the change again in the composer.

"Build this again" is the one narrow exception to [ADR-0007](../../docs/adr/0007-the-plan-document-is-durable-the-handoff-is-not.md):
the same click writes the edit and consumes it, so no stale plan is left on disk for an unrelated
later turn to misread. What these tests pin is that pair of promises — it really does build the
edited words, and it really does refuse once the app has moved past them — plus the two side effects
that belong to this action and must not leak into a plain edit: the document goes back to Draft, and
the sign-offs that were given for the old words are cleared.

Asserted on the public surface: the approve turn, the stored document, and the prompt the agent got.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator, PlanArchiveRefused
from sage.router.models import Mode, ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the scope classifier is the only caller here."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The same two waits test_turn_path strips: a scripted turn can only spend them."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _build(tmp: Path, turns: list[Turn], *, phased: bool = False):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    project = orch.project(start_preview=False)
    if phased:
        project.record.write_settings({"phased_build": True})
    return orch, oc


def _done(events: list[dict]) -> dict:
    return next(e for e in reversed(events) if e["type"] == "done")


def _workspace(orch: Orchestrator):
    return orch.project(start_preview=False).workspace


def _only_plan_id(orch: Orchestrator) -> str:
    docs = orch.list_plan_docs()
    assert len(docs) == 1, f"expected one plan document, got {[d['id'] for d in docs]}"
    return docs[0]["id"]


# Every turn here runs in a Conversation, as every real one does: a plan document records the
# Conversation it was written in, and "Build this again" runs its rebuild in that one.
CONVERSATION = "conv_desk"

PLAN = Turn(text="1. Add the table\n2. Wire up the data")
EDITED = "1. Add the table\n2. Wire up the data\n3. Sort it by date\n"


def _built_once(tmp_path: Path, extra: list[Turn] | None = None):
    """A Project whose app has been built from its one plan — the state "Build this again" is for."""
    orch, oc = _build(tmp_path, [
        PLAN,                                              # 1. the plan the gate proposes
        Turn(writes={"src/App.tsx": "// the table\n"}),    # 2. the approved build
        *(extra or []),
    ])
    list(orch.build_stream("build me a consumption dashboard", conversation=CONVERSATION))
    list(orch.approve_stream(conversation=CONVERSATION))
    return orch, oc


# --- the action itself ------------------------------------------------------------------------


def test_build_this_again_builds_the_edited_plan(tmp_path: Path):
    orch, oc = _built_once(tmp_path, [Turn(writes={"src/App.tsx": "// the sorted table\n"})])
    plan_id = _only_plan_id(orch)

    events = list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                                      plan_id=plan_id, build_again=True))

    assert _done(events)["ok"] is True
    # It built the person's own words, and through the builder rather than the read-only planner:
    # the whole point is that nothing re-plans from scratch.
    assert oc.prompts[-1]["agent"] != "sage-plan"
    assert "Sort it by date" in oc.prompts[-1]["text"]
    assert "// the sorted table" in (_workspace(orch).path / "src" / "App.tsx").read_text()
    # And the edit is on record as a version of the same document, not a second document.
    doc = orch.read_plan_doc(plan_id)
    assert "Sort it by date" in doc["markdown"]
    assert doc["version"] == 2
    assert len(orch.list_plan_docs()) == 1


def test_build_this_again_leaves_no_live_plan_behind(tmp_path: Path):
    """The promise that keeps ADR-0007 intact: the write and the read happen in the same click, so
    the next unrelated turn finds nothing to misread."""
    orch, _oc = _built_once(tmp_path, [Turn(writes={"src/App.tsx": "// the sorted table\n"})])

    list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                             plan_id=_only_plan_id(orch), build_again=True))

    ws = _workspace(orch)
    assert ws.read_plan() is None
    assert ws.read_plan_retry_step() == 0
    # The app is now built from the edited text, which is what the rail's pin reads.
    assert "Sort it by date" in (ws.read_archived_plan() or "")


def test_build_this_again_says_so_in_the_conversation(tmp_path: Path):
    """A build that came from an edited plan has to be tellable apart from one somebody typed."""
    orch, _oc = _built_once(tmp_path, [Turn(writes={"src/App.tsx": "// the sorted table\n"})])

    list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                             plan_id=_only_plan_id(orch), build_again=True))

    said = [e.get("text") for e in _workspace(orch).read_history(CONVERSATION) if e.get("type") == "user"]
    # The first build's own bubble, then this one's — the two read differently, which is the point.
    assert said[-1] != said[0]
    assert "Build this again" in said[-1]


# --- what it does to the document ------------------------------------------------------------


def test_build_this_again_returns_the_document_to_draft_with_no_approvals(tmp_path: Path):
    orch, _oc = _built_once(tmp_path, [Turn(writes={"src/App.tsx": "// the sorted table\n"})])
    plan_id = _only_plan_id(orch)
    # The first build approved it, which is what put a sign-off on record.
    assert orch.read_plan_doc(plan_id)["status"] == "approved"
    assert len(orch.read_plan_doc(plan_id)["approvals"]) == 1

    list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                                      plan_id=plan_id, build_again=True))

    doc = orch.read_plan_doc(plan_id)
    # "Approved" would be claiming a review of words nobody has read.
    assert doc["status"] == "draft"
    assert doc["approvals"] == []


def test_a_plain_edit_still_leaves_the_status_and_approvals_alone(tmp_path: Path):
    """The reset is this action's, not every edit's. Editing a section has never cost a plan its
    status or its sign-offs, and nothing here changes that."""
    orch, _oc = _built_once(tmp_path)
    plan_id = _only_plan_id(orch)

    orch.patch_plan_doc(plan_id, {"summary": "A consumption dashboard, sorted."})

    doc = orch.read_plan_doc(plan_id)
    assert doc["status"] == "approved"
    assert len(doc["approvals"]) == 1


def test_the_edit_survives_a_build_that_fails(tmp_path: Path):
    """Nothing a person typed is lost to a build that did not finish: the version is written before
    the build runs, so a refusal or a crash still leaves the words on the document."""
    orch, _oc = _built_once(tmp_path)   # no third scripted turn: the build has nothing to send
    plan_id = _only_plan_id(orch)

    list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                                      plan_id=plan_id, build_again=True))

    assert "Sort it by date" in orch.read_plan_doc(plan_id)["markdown"]


# --- a turn that never ran ---------------------------------------------------------------------
#
# A rebuild can be refused after plan.md is already written: the model the app runs on may be gone
# by the time somebody presses the button. That refusal has to leave the Project exactly as it found
# it, and it very nearly did the opposite of that in both directions at once — destroying sign-offs
# that were still valid, and resurrecting a live plan.md on an app that had already built one.


def _refuse_for_its_model(monkeypatch) -> None:
    """Make the turn's model preflight refuse, the way a stopped sovereign endpoint does.

    Stubbed at the preflight itself rather than reproduced from a dead alias and a Domino gateway
    mode: which slot is broken is `test_a_dead_alias_stops_the_turn_before_it_starts`'s subject,
    and what these tests are about is what the approve turn does on its way out when something
    refuses it after `plan.md` has already been written."""
    monkeypatch.setattr(Orchestrator, "_turn_slot_refusal",
                        lambda *a, **k: "Qwen 2.5 is stopped. Start it, or pick another model.")


def test_a_refused_rebuild_keeps_the_approvals_it_would_have_cleared(tmp_path: Path, monkeypatch):
    """The button's whole warning is that it clears sign-offs. A turn that builds nothing must not
    charge that price — there is no undo for it, and the plan is still the app's current one."""
    orch, oc = _built_once(tmp_path)
    plan_id = _only_plan_id(orch)
    sent_before = len(oc.prompts)
    _refuse_for_its_model(monkeypatch)

    events = list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                                      plan_id=plan_id, build_again=True))

    assert _done(events)["ok"] is False
    assert len(oc.prompts) == sent_before          # nothing was built
    doc = orch.read_plan_doc(plan_id)
    assert doc["status"] == "approved"
    assert len(doc["approvals"]) == 1
    # The words are still kept, though: a refusal must not cost a person their typing either.
    assert "Sort it by date" in doc["markdown"]


def test_a_refused_rebuild_leaves_no_live_plan_on_a_built_app(tmp_path: Path, monkeypatch):
    """The ADR-0007 hazard, reached through the one action that promises never to open it. Writing
    plan.md happens before the model is checked, so a refusal that returned early left a built app
    holding a live plan — current-looking intent that no build ever consumed."""
    orch, _oc = _built_once(tmp_path)
    _refuse_for_its_model(monkeypatch)

    list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                             plan_id=_only_plan_id(orch), build_again=True))

    ws = _workspace(orch)
    assert ws.read_plan() is None
    assert ws.read_plan_retry_step() == 0
    # Cancelled, not consumed: the pin must not go on to describe the app as built from these words.
    assert "Sort it by date" not in (ws.read_archived_plan() or "")


def test_a_refused_first_approve_still_keeps_its_plan_for_another_try(tmp_path: Path, monkeypatch):
    """The behaviour above must not widen. On a first approve the live plan IS the app's only plan
    and the card is what the person presses again after changing the model, so it stays put."""
    orch, _oc = _build(tmp_path, [PLAN])
    list(orch.build_stream("build me a consumption dashboard", conversation=CONVERSATION))
    _refuse_for_its_model(monkeypatch)

    list(orch.approve_stream(conversation=CONVERSATION))

    assert "Add the table" in (_workspace(orch).read_plan() or "")


# --- when it stops being offered --------------------------------------------------------------


def test_build_this_again_is_refused_once_a_newer_plan_owns_the_app(tmp_path: Path):
    """Story 5's rule, enforced on the server and not only on the button: an old plan cannot be
    built over an app that has already moved past it."""
    orch, oc = _built_once(tmp_path, [Turn(writes={"src/App.tsx": "// never sent\n"})])
    plan_id = _only_plan_id(orch)
    sent_before = len(oc.prompts)
    # Somebody else's plan, for the same app, written after this one.
    project = orch.project(start_preview=False)
    project.record.create_plan_doc("1. Add a chart\n", title="Later plan",
                                   app_id=project.workspace.app_id)

    events = list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                                      plan_id=plan_id, build_again=True))

    assert _done(events)["ok"] is False
    assert _done(events)["decision"] == "plan moved on"
    # The refusal says what to do instead, and nothing at all was built or written.
    assert "current plan" in next(e for e in events if e["type"] == "error")["message"]
    assert len(oc.prompts) == sent_before
    assert _workspace(orch).read_plan() is None
    doc = orch.read_plan_doc(plan_id)
    assert doc["version"] == 1 and doc["status"] == "approved"


def test_build_this_again_is_refused_on_a_superseded_plan(tmp_path: Path):
    """Story 20: a plan another Conversation replaced must not be resurrectable."""
    orch, _oc = _built_once(tmp_path, [Turn(writes={"src/App.tsx": "// never sent\n"})])
    plan_id = _only_plan_id(orch)
    orch.project(start_preview=False).record.patch_plan_doc_meta(plan_id, status="superseded")

    events = list(orch.approve_stream(conversation=CONVERSATION, plan_edits=EDITED,
                                      plan_id=plan_id, build_again=True))

    assert _done(events)["decision"] == "plan moved on"
    assert orch.read_plan_doc(plan_id)["version"] == 1


# --- what the Plan page reads off the document ------------------------------------------------


def test_a_built_plan_says_it_can_be_built_again(tmp_path: Path):
    orch, _oc = _built_once(tmp_path)

    state = orch.read_plan_doc(_only_plan_id(orch))["buildAgain"]

    assert state == {"offered": True, "eligible": True, "reason": ""}


def test_a_plan_awaiting_its_first_build_is_not_offered_the_action(tmp_path: Path):
    """Story 16: before a build there is an Approve flow, and this action does not apply."""
    orch, _oc = _build(tmp_path, [PLAN])
    list(orch.build_stream("build me a consumption dashboard", conversation=CONVERSATION))

    state = orch.read_plan_doc(_only_plan_id(orch))["buildAgain"]

    assert state["offered"] is False
    assert state["reason"] == "never built"


def test_a_plan_that_names_no_app_is_not_offered_the_action(tmp_path: Path):
    """A plan drafted by hand in the plan list backs nothing yet."""
    orch, _oc = _build(tmp_path, [PLAN])
    plan_id = orch.create_plan_doc({"title": "Something I typed"})["id"]

    state = orch.read_plan_doc(plan_id)["buildAgain"]

    assert state["offered"] is False
    assert state["reason"] == "no app"


def test_a_plan_the_app_has_moved_past_is_offered_the_action_disabled(tmp_path: Path):
    """Stories 5 and 6: still offered, so the reason has somewhere to be said."""
    orch, _oc = _built_once(tmp_path)
    plan_id = _only_plan_id(orch)
    project = orch.project(start_preview=False)
    project.record.create_plan_doc("1. Add a chart\n", title="Later plan",
                                   app_id=project.workspace.app_id)

    state = orch.read_plan_doc(plan_id)["buildAgain"]

    assert state["offered"] is True
    assert state["eligible"] is False
    assert state["reason"] == "moved on"


def test_a_superseded_plan_says_which_of_the_two_reasons_it_is(tmp_path: Path):
    orch, _oc = _built_once(tmp_path)
    plan_id = _only_plan_id(orch)
    orch.project(start_preview=False).record.patch_plan_doc_meta(plan_id, status="superseded")

    state = orch.read_plan_doc(plan_id)["buildAgain"]

    assert state["offered"] is True
    assert state["eligible"] is False
    assert state["reason"] == "superseded"


# --- a Conversation somebody deleted -----------------------------------------------------------
#
# The plan document survives the Conversation that produced it, and that is correct: ADR-0007 makes
# its lifetime the Project's, and a plan carries review state a conversation delete has no business
# destroying. What it must not do is go on claiming an origin that answers 404 (#167). Three
# controls were built off `originThreadId` alone and all three led somewhere dead.


def _in_a_real_conversation(tmp_path: Path, extra: list[Turn] | None = None):
    """The built-once Project again, with a Conversation the rail actually holds.

    `CONVERSATION` above is a bare id with no Thread record behind it — what a build driven from
    outside the rail looks like, and what every other test in this file wants. A delete needs
    something to tombstone, so this one mints its conversation through `ThreadStore`.
    """
    orch, _oc = _build(tmp_path, [
        PLAN,
        Turn(writes={"src/App.tsx": "// the table\n"}),
        *(extra or []),
    ])
    thread = ThreadStore(orch.project(start_preview=False).record.path).create("Desk exposure")
    list(orch.build_stream("build me a consumption dashboard", conversation=thread["id"]))
    list(orch.approve_stream(conversation=thread["id"]))
    return orch, thread["id"]


def _proposed_in_a_real_conversation(tmp_path: Path):
    """A plan sitting live and unapproved, in a Conversation the rail actually holds.

    The state a build that gave up leaves behind: `_approve_stream` keeps `plan.md` alive on
    purpose so "try again" can resume from it, and nothing else ever clears it. Reached here by
    stopping before the approve, because the two are the same state on disk — a plan waiting for
    its first approval and a plan whose build died look identical, which is what
    `read_plan_retry_step` exists to tell apart.
    """
    orch, _oc = _build(tmp_path, [PLAN])
    thread = ThreadStore(orch.project(start_preview=False).record.path).create("Desk exposure")
    list(orch.build_stream("build me a consumption dashboard", conversation=thread["id"]))
    return orch, thread["id"]


def test_a_plan_whose_conversation_was_deleted_says_the_origin_is_gone(tmp_path: Path):
    """Criterion 1. The id is deliberately still on the document: blanking it in the response —
    which is what `list_threads` does with a `boundAppId` naming a deleted app — would make "the
    conversation was deleted" indistinguishable from "there never was one"."""
    orch, thread_id = _in_a_real_conversation(tmp_path)
    plan_id = _only_plan_id(orch)
    assert orch.read_plan_doc(plan_id)["originLive"] is True

    orch.delete_thread(thread_id)

    doc = orch.read_plan_doc(plan_id)
    assert doc["originThreadId"] == thread_id
    assert doc["originLive"] is False


def test_a_plan_whose_conversation_was_deleted_cannot_be_built_again(tmp_path: Path):
    """Criterion 1's other half. It was reported eligible before this: the dead-origin check asked
    only whether the id was empty, so a tombstoned Thread passed it and the page then opened a
    conversation that lost on its first await."""
    orch, thread_id = _in_a_real_conversation(tmp_path)
    plan_id = _only_plan_id(orch)
    assert orch.read_plan_doc(plan_id)["buildAgain"]["eligible"] is True

    orch.delete_thread(thread_id)

    state = orch.read_plan_doc(plan_id)["buildAgain"]
    assert state["offered"] is True
    assert state["eligible"] is False
    # Distinct from `no conversation`, because the two need different sentences: one says ask for
    # this in a conversation, the other says that door is closed, start a new one.
    assert state["reason"] == "conversation deleted"


def test_a_plan_that_never_recorded_a_conversation_still_says_what_it_always_said(tmp_path: Path):
    """Criterion 2. Two words down one branch, so widening it must not cost the older case its own
    word — a document written before #54 recorded no origin at all."""
    orch, _oc = _built_once(tmp_path)
    plan_id = _only_plan_id(orch)
    orch.project(start_preview=False).record.patch_plan_doc_meta(plan_id, originThreadId="")

    doc = orch.read_plan_doc(plan_id)

    assert doc["originLive"] is False
    assert doc["buildAgain"]["reason"] == "no conversation"


def test_a_conversation_the_rail_never_held_is_not_read_as_a_deleted_one(tmp_path: Path):
    """The tombstone is the only evidence of a delete, and nothing else may be read as one. A build
    driven from outside the rail passes a conversation id the Thread store has no record of, and
    calling that absence a delete would disable the action on every such plan."""
    orch, _oc = _built_once(tmp_path)

    assert orch.read_plan_doc(_only_plan_id(orch))["originLive"] is True


# --- putting a plan away -----------------------------------------------------------------------
#
# `archived` is filtered exactly where `superseded` is — `_plan_docs_naming_app` — so it drops out
# of `_app_plan_docs` and out of eligibility in one place rather than two (#167).


def test_an_archived_plan_stops_answering_build_this_again(tmp_path: Path):
    """Criterion 4. The archived document is no longer a candidate for its app, so the action it
    was offering is refused rather than left pointing at a plan nobody can see."""
    orch, _oc = _built_once(tmp_path)
    plan_id = _only_plan_id(orch)
    assert orch.read_plan_doc(plan_id)["buildAgain"]["eligible"] is True

    orch.archive_plan_doc(plan_id, True)

    state = orch.read_plan_doc(plan_id)["buildAgain"]
    assert state["eligible"] is False
    # Its own word rather than `moved on`, which the filter alone would have left it saying — and
    # which would send somebody to a later plan that does not exist. The remedy for this one is the
    # Unarchive control on the same page.
    assert state["reason"] == "archived"


def test_an_archived_plan_says_the_thing_unarchiving_cannot_fix_first(tmp_path: Path):
    """`archived` is the last reason a person could act on, because its copy promises that
    unarchiving gets the action back. A plan that is archived AND short a conversation would take
    that promise and then refuse anyway — so the reason that survives unarchiving wins."""
    orch, thread_id = _in_a_real_conversation(tmp_path)
    plan_id = _only_plan_id(orch)
    orch.archive_plan_doc(plan_id, True)
    assert orch.read_plan_doc(plan_id)["buildAgain"]["reason"] == "archived"

    orch.delete_thread(thread_id)

    assert orch.read_plan_doc(plan_id)["buildAgain"]["reason"] == "conversation deleted"


def test_archiving_the_plan_that_names_an_app_lets_the_pin_fall_back(tmp_path: Path):
    """Criterion 4's other half. With no document naming the app any more, `_app_plan_docs` gives
    the answer it gives a Project that never bound one: the unbound draft."""
    orch, _oc = _built_once(tmp_path)
    plan_id = _only_plan_id(orch)
    project = orch.project(start_preview=False)
    draft = project.record.create_plan_doc("1. Something else\n", title="A draft")

    orch.archive_plan_doc(plan_id, True)

    docs = Orchestrator._app_plan_docs(orch.project(start_preview=False))
    assert [d["id"] for d in docs] == [draft["id"]]


def test_the_plan_awaiting_approval_right_now_cannot_be_put_away(tmp_path: Path):
    """Criterion 5. Hiding the document an Approve card is asking about would leave that card
    pointing at a plan the panel no longer lists — so the one refusal is on the way in, and it
    reuses the word "Build this again" already has for this state."""
    orch, _oc = _build(tmp_path, [PLAN])
    list(orch.build_stream("build me a consumption dashboard", conversation=CONVERSATION))
    plan_id = _only_plan_id(orch)

    with pytest.raises(PlanArchiveRefused) as refused:
        orch.archive_plan_doc(plan_id, True)

    assert refused.value.reason == "awaiting approval"
    assert orch.read_plan_doc(plan_id)["archived"] is False


def test_the_refusal_stands_while_the_conversation_holding_the_card_is_there(tmp_path: Path):
    """The half of that refusal that survives #167. The Approve card is what the copy sends people
    to, so while the Conversation drawing it still answers, the refusal has somewhere to send
    them — and a plan hidden out from under an open card would leave it pointing at a document the
    panel no longer lists."""
    orch, _thread_id = _proposed_in_a_real_conversation(tmp_path)
    plan_id = _only_plan_id(orch)
    assert orch.read_plan_doc(plan_id)["originLive"] is True

    with pytest.raises(PlanArchiveRefused) as refused:
        orch.archive_plan_doc(plan_id, True)

    assert refused.value.reason == "awaiting approval"


def test_a_plan_left_live_by_a_deleted_conversation_can_still_be_put_away(tmp_path: Path):
    """The refusal named two acts — approve it, or cancel it — and both live on the plan card in
    the Conversation that proposed it. Delete that Conversation and the card goes with it, so the
    refusal named two doors that no longer existed and the document could never be put away.

    A build that gives up leaves its plan live on purpose, to resume from, so this is not a rare
    corner: it is every plan whose build died and whose Conversation was later tidied up.
    """
    orch, thread_id = _proposed_in_a_real_conversation(tmp_path)
    plan_id = _only_plan_id(orch)

    orch.delete_thread(thread_id)
    orch.archive_plan_doc(plan_id, True)

    assert orch.read_plan_doc(plan_id)["archived"] is True


def test_putting_that_plan_away_retires_the_copy_it_left_behind(tmp_path: Path):
    """With nothing left to answer the refusal, putting the document away IS the cancel. The stray
    `plan.md` has to go in the same act, or the app goes on naming a document the panel has just
    hidden — and the next turn reads a plan nobody can open as live intent (ADR-0007)."""
    orch, thread_id = _proposed_in_a_real_conversation(tmp_path)
    plan_id = _only_plan_id(orch)
    workspace = orch.project(start_preview=False).workspace
    assert workspace.live_plan_doc_id() == plan_id

    orch.delete_thread(thread_id)
    orch.archive_plan_doc(plan_id, True)

    assert workspace.read_plan() is None
    assert workspace.live_plan_doc_id() == ""


def test_that_plan_is_retired_as_cancelled_not_as_the_one_the_app_was_built_from(tmp_path: Path):
    """Nothing here knows a build ever consumed it — the plan is live precisely because none did —
    so the rail's pin must not go on to describe the app as built from a plan somebody just put
    away."""
    orch, thread_id = _proposed_in_a_real_conversation(tmp_path)
    plan_id = _only_plan_id(orch)

    orch.delete_thread(thread_id)
    orch.archive_plan_doc(plan_id, True)

    assert orch.project(start_preview=False).workspace.read_archived_plan() is None


def test_a_plan_stuck_live_names_the_deleted_conversation_not_the_approve_card(tmp_path: Path):
    """Both refuse, so the order of the two checks only decides which sentence the person reads.
    "Awaiting approval" sends them to an Approve card in a Conversation that is gone; "the
    conversation was deleted" tells them the truth — that door is closed, start a new one."""
    orch, thread_id = _in_a_real_conversation(tmp_path, [Turn(text="1. Sort it by date")])
    # Plan mode, because the automatic gate only fires before the first build — this app has had
    # one, and an ordinary BUILD turn here would write code instead of proposing a plan.
    orch.project(start_preview=False).control.set_mode(Mode.PLAN)
    list(orch.build_stream("sort it by date", conversation=thread_id))
    stuck = orch.list_plan_docs()[0]["id"]
    assert orch.read_plan_doc(stuck)["buildAgain"]["reason"] == "awaiting approval"

    orch.delete_thread(thread_id)

    assert orch.read_plan_doc(stuck)["buildAgain"]["reason"] == "conversation deleted"


def test_the_conversation_that_produced_an_archived_plan_still_shows_its_card(tmp_path: Path):
    """Criterion 7, and the reason the filter is where it is rather than in `list_plan_docs`.
    Archive is reversible, and a reversible act needs a way back to the thing it hid — the plan
    card in the Conversation is that way."""
    from sage.orchestrator.service import _thread_plan_id

    orch, _oc = _built_once(tmp_path)
    plan_id = _only_plan_id(orch)

    orch.archive_plan_doc(plan_id, True)

    record = orch.project(start_preview=False).record
    assert _thread_plan_id(record, CONVERSATION) == plan_id


# --- phased builds ---------------------------------------------------------------------------
#
# Nothing here is new work: a "Build this again" turn is an approve turn, so the resume point it
# writes and the retry that reads it are the ones that already existed. This is the test that says
# so, because story 14 asks for it and because the flag could easily have been threaded somewhere
# that skipped it.

PHASED_PLAN = """A dashboard for exploring trades.

## Plan

### 1. Data module
- Files — src/data.ts
- Do — Export two hundred sample trade rows.
- Done when — src/data.ts exports rows and the app compiles.

### 2. Trades table
- Files — src/Table.tsx
- Do — Render the rows in a sortable table.
- Done when — The preview shows a sortable table.

### 3. Currency filter
- Files — src/Filter.tsx
- Do — Add a currency dropdown above the table.
- Done when — Picking a currency narrows the visible rows.
"""

PHASED_EDIT = PHASED_PLAN.replace("a sortable table", "a sortable table, newest first")


def _writes(rel: str) -> Turn:
    return Turn(writes={rel: f"// {rel}\nexport const x = 1;\n"})


def test_a_phased_build_this_again_runs_every_phase_of_the_edited_plan(tmp_path: Path):
    orch, _oc = _build(tmp_path, [
        Turn(text=PHASED_PLAN),
        _writes("src/data.ts"), _writes("src/Table.tsx"), _writes("src/Filter.tsx"),   # first build
        _writes("src/data.ts"), _writes("src/Table.tsx"), _writes("src/Filter.tsx"),   # the rebuild
    ], phased=True)
    list(orch.build_stream("build me a trades dashboard", conversation=CONVERSATION))
    list(orch.approve_stream(conversation=CONVERSATION))
    plan_id = _only_plan_id(orch)

    events = list(orch.approve_stream(conversation=CONVERSATION, plan_edits=PHASED_EDIT,
                                      plan_id=plan_id, build_again=True))

    assert _done(events)["ok"] is True
    assert [e["n"] for e in events if e["type"] == "step-start"] == [1, 2, 3]
    assert _workspace(orch).read_plan_retry_step() == 0
