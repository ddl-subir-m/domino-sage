"""The rail's app tags, app filter and app-name search, given something to read (ADR-0008).

`conversation-list.js` has drawn a tag per Built App a conversation changed since the rail was
built, and filtered and searched on those tags. It never drew one: `thread.touched` had no writer,
so the rail's own footer note pointed at controls that could not appear.

The writer runs where the receipt is already written — inside `persist()`, off the `app_change`
event — so the tag and the transcript's app card cannot disagree about which app a turn changed.

The shape is a list on the Thread and not a column, because a conversation can change several apps
and filing it under one of them would be a lie about the others: one entry per app, however many
turns hit it, named as the app is called now.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn

# The rail itself, for the one claim at the foot of this file. It is about the source rather than
# about a run, because what the tags are FOR is drawn in the browser and asserted there.
_WORKBENCH = Path(__file__).resolve().parents[1] / "sage" / "workbench"


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport

        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for every routed request. The Chat/Build classifier reads it; the scope
    classifier does not recognise it, which is why the follow-up turns below say Implement outright
    (see `_implement`) instead of letting Auto ask a model what they meant."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _plan(title: str, step: str) -> str:
    return (f"{title}\n\n"
            "## Plan\n"
            f"1. **{step}** — Show it.\n\n"
            "## Open questions\n"
            "None — ready to build.\n")


_DESK = _plan("A desk exposure dashboard.", "Desk table")
_PNL = _plan("A daily P&L report.", "P&L table")


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
    (t / "src" / "App.tsx").write_text("export default function App() { return null }")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# rules")
    return t


def _orch(tmp: Path, turns: list[Turn] | None = None):
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(
        workspace_dir=root,
        template=_template(tmp),
        gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        feedback=OkFeedback(),
        opencode_client=oc,
    )
    return orch, oc, root


def _app_from_chat(orch: Orchestrator, ask: str) -> tuple[str, str]:
    """One Chat conversation through a confirmed handoff — an app is born."""
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, ask))
    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, {"resources": False, "artifacts": False, "transcript": False})
    return orch.project(start_preview=False).workspace.app_id, tid


def _implement(orch: Orchestrator) -> None:
    """"Just build it" — the mode a follow-up turn is typed in once the app stands. Auto would send
    this scripted prompt through the scope classifier, which is a model call and not what is under
    test here."""
    orch.project(start_preview=False).control.set_mode(Mode.IMPLEMENT)


def _tags(orch: Orchestrator, thread_id: str) -> list[dict]:
    row = ThreadStore(orch.project(start_preview=False).record.path).get(thread_id) or {}
    return row.get("touched") or []


# ---- one build turn, one tag ---------------------------------------------------------------

def test_a_build_turn_that_changes_an_app_tags_the_conversation_with_it(tmp_path: Path):
    """The whole feature in one turn: the conversation now names the app it changed."""
    turns = [Turn(text=_DESK),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    app_id, tid = _app_from_chat(orch, "build me a desk dashboard")

    assert _tags(orch, tid) == []
    list(orch.approve_stream(conversation=tid))

    assert _tags(orch, tid) == [
        {"appId": app_id, "appName": "A desk exposure dashboard.", "kind": "built"},
    ]


def test_the_tag_says_built_only_when_this_conversation_made_the_app(tmp_path: Path):
    """"Built X" and "Changed X" are different sentences and the rail renders both. The crossing
    is what tells them apart — the app was already standing when this conversation reached it."""
    turns = [Turn(text=_DESK),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"}),
             Turn(text="Darker now.", writes={"src/App.tsx": "// dark desk table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    app_id, tid = _app_from_chat(orch, "build me a desk dashboard")
    list(orch.approve_stream(conversation=tid))

    later = orch.create_thread()["id"]
    _implement(orch)
    list(orch.build_stream("make it dark", conversation=later))

    assert _tags(orch, later) == [
        {"appId": app_id, "appName": "A desk exposure dashboard.", "kind": "changed"},
    ]


# ---- one entry per app, whatever the turn count --------------------------------------------

def test_two_turns_on_one_app_leave_one_tag_carrying_the_newer_name(tmp_path: Path):
    """A conversation that spent twenty turns on one app changed one app. The name is the one the
    app is called now, because the tag is a label on a row in today's rail."""
    turns = [Turn(text=_DESK),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"}),
             Turn(text="Darker now.", writes={"src/App.tsx": "// dark desk table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    app_id, tid = _app_from_chat(orch, "build me a desk dashboard")
    list(orch.approve_stream(conversation=tid))

    orch.rename_app(app_id, "Desk exposure")
    _implement(orch)
    list(orch.build_stream("make it dark", conversation=tid))

    assert _tags(orch, tid) == [
        {"appId": app_id, "appName": "Desk exposure", "kind": "built"},
    ]


def test_two_apps_in_one_conversation_leave_two_tags(tmp_path: Path):
    """The case that makes filing a conversation under one app wrong (ADR-0008). One row in the
    rail, two tags on it, and the app filter finds the row from either."""
    turns = [Turn(text=_DESK),
             Turn(text="A report, then."), Turn(text=_PNL),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"}),
             Turn(text="Built the P&L table.", writes={"src/App.tsx": "// pnl table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    desk, tid = _app_from_chat(orch, "build me a desk dashboard")
    pnl, _other = _app_from_chat(orch, "now build me a daily P&L report")

    orch.select_app(desk)
    list(orch.approve_stream(conversation=tid))
    orch.select_app(pnl)
    list(orch.approve_stream(conversation=tid))

    assert _tags(orch, tid) == [
        {"appId": desk, "appName": "A desk exposure dashboard.", "kind": "built"},
        {"appId": pnl, "appName": "A daily P&L report.", "kind": "changed"},
    ]


# ---- what the rail actually reads ----------------------------------------------------------

def test_the_tag_reaches_the_list_the_rail_reads(tmp_path: Path, monkeypatch):
    """`GET /api/threads` returns the raw record, and it is built from a ThreadStore opened on the
    Chat project's path while the build turn writes through one opened on the build project's.
    They are one bound Project and therefore one path — this is what says so."""
    import sage.orchestrator.app as appmod

    turns = [Turn(text=_DESK),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    app_id, tid = _app_from_chat(orch, "build me a desk dashboard")
    list(orch.approve_stream(conversation=tid))

    monkeypatch.setattr(appmod, "orchestrator", orch)
    rows = TestClient(appmod.control_app).get("/api/threads").json()

    row = next(r for r in rows if r["id"] == tid)
    assert row["touched"] == [
        {"appId": app_id, "appName": "A desk exposure dashboard.", "kind": "built"},
    ]


# ---- what earns no tag ---------------------------------------------------------------------

def test_a_turn_that_changes_no_files_earns_no_tag(tmp_path: Path):
    """A tag is earned by a change. A gated first turn proposes a plan and writes no code, so the
    conversation has not changed an app and the rail must not say it has."""
    turns = [Turn(text=_DESK)]
    orch, _oc, _root = _orch(tmp_path, turns)
    tid = orch.create_thread()["id"]

    kinds = [ev["type"] for ev in orch.build_stream("build me a desk dashboard", conversation=tid)]

    assert "plan-proposed" in kinds
    assert "app_change" not in kinds
    assert _tags(orch, tid) == []


def test_a_thread_written_before_tags_existed_still_lists_and_can_earn_one(tmp_path: Path):
    """No backfill: a record with no `touched` key is a Thread that has not built since, not a
    broken one. It lists, and its next build turn gives it the key."""
    store = ThreadStore(tmp_path)
    row = store.create("Older conversation")
    meta = store.meta_path(row["id"])
    old = json.loads(meta.read_text())
    del old["touched"]
    meta.write_text(json.dumps(old))

    listed = store.list()
    assert [r["id"] for r in listed] == [row["id"]]
    assert "touched" not in listed[0]

    store.record_touch(row["id"], app_id="app_a", app_name="Desk exposure", kind="changed")
    assert store.list()[0]["touched"] == [
        {"appId": "app_a", "appName": "Desk exposure", "kind": "changed"},
    ]


def test_earning_a_tag_is_not_new_activity_in_the_rails_order(tmp_path: Path):
    """The rail sorts on `updatedAt`. A tag is a label on a turn that already happened, so writing
    one must not float the conversation to the top of the list."""
    store = ThreadStore(tmp_path)
    row = store.create("Desks")

    store.record_touch(row["id"], app_id="app_a", app_name="Desk exposure", kind="built")

    assert (store.get(row["id"]) or {})["updatedAt"] == row["updatedAt"]


def test_deleting_an_app_takes_its_tags_with_it(tmp_path: Path):
    """The tag is the one thing naming a Built App from OUTSIDE its directory, so it is the one
    thing a delete leaves behind: a chip nobody can open, and a filter narrowing the rail by an
    appId that is not there. It goes with the app, on the same rule as the plan documents."""
    turns = [Turn(text=_DESK),
             Turn(text="A report, then."), Turn(text=_PNL),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"}),
             Turn(text="Built the P&L table.", writes={"src/App.tsx": "// pnl table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    desk, desk_tid = _app_from_chat(orch, "build me a desk dashboard")
    pnl, pnl_tid = _app_from_chat(orch, "now build me a daily P&L report")

    orch.select_app(desk)
    list(orch.approve_stream(conversation=desk_tid))
    orch.select_app(pnl)
    list(orch.approve_stream(conversation=pnl_tid))

    orch.delete_app(desk)

    assert _tags(orch, desk_tid) == []
    # The other conversation's tag is about an app that is still standing, so it stays.
    assert [t["appId"] for t in _tags(orch, pnl_tid)] == [pnl]


def test_renaming_an_app_relabels_its_tags(tmp_path: Path):
    """The tag says what the app is called NOW, but only a build turn writes one — so a rename has
    to reach the tags itself. Until it did, the app rail and the preview took the new name while
    the chip in the conversation rail went on saying the old one, and the two disagreed about the
    same app until somebody happened to build in it again."""
    turns = [Turn(text=_DESK),
             Turn(text="A report, then."), Turn(text=_PNL),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"}),
             Turn(text="Built the P&L table.", writes={"src/App.tsx": "// pnl table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    desk, desk_tid = _app_from_chat(orch, "build me a desk dashboard")
    pnl, pnl_tid = _app_from_chat(orch, "now build me a daily P&L report")

    orch.select_app(desk)
    list(orch.approve_stream(conversation=desk_tid))
    orch.select_app(pnl)
    list(orch.approve_stream(conversation=pnl_tid))

    orch.rename_app(desk, "Desk exposure")

    assert _tags(orch, desk_tid) == [
        {"appId": desk, "appName": "Desk exposure", "kind": "built"},
    ]
    # The other conversation names a different app, and a rename is not a reason to touch it.
    assert _tags(orch, pnl_tid) == [
        {"appId": pnl, "appName": "A daily P&L report.", "kind": "built"},
    ]


def test_a_rename_reaches_every_conversation_that_changed_the_app(tmp_path: Path):
    """One app, several conversations. The sweep is over the Project's records rather than the
    conversation in front of you, because a chip in the rail is drawn from whichever record the row
    belongs to."""
    turns = [Turn(text=_DESK),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"}),
             Turn(text="Darker now.", writes={"src/App.tsx": "// dark desk table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    app_id, first = _app_from_chat(orch, "build me a desk dashboard")
    list(orch.approve_stream(conversation=first))
    later = orch.create_thread()["id"]
    _implement(orch)
    list(orch.build_stream("make it dark", conversation=later))

    orch.rename_app(app_id, "Desk exposure")

    assert [t["appName"] for t in _tags(orch, first)] == ["Desk exposure"]
    assert [t["appName"] for t in _tags(orch, later)] == ["Desk exposure"]


def test_renaming_an_app_is_not_new_activity_in_the_rails_order(tmp_path: Path):
    """Same rule as earning a tag: the rail sorts on `updatedAt`, and renaming an app is not a turn
    somebody took in a conversation."""
    store = ThreadStore(tmp_path)
    row = store.create("Desks")
    store.record_touch(row["id"], app_id="app_a", app_name="Desks", kind="built")

    store.rename_app("app_a", "Desk exposure")

    assert (store.get(row["id"]) or {})["updatedAt"] == row["updatedAt"]
    assert (store.get(row["id"]) or {})["touched"] == [
        {"appId": "app_a", "appName": "Desk exposure", "kind": "built"},
    ]


def test_resetting_an_app_keeps_its_tags(tmp_path: Path):
    """Reset empties an app and keeps it. The conversation did change that app and the app is still
    there to be named, so the rail has no reason to stop saying so."""
    turns = [Turn(text=_DESK),
             Turn(text="Built the desk table.", writes={"src/App.tsx": "// desk table\n"})]
    orch, _oc, _root = _orch(tmp_path, turns)
    app_id, tid = _app_from_chat(orch, "build me a desk dashboard")
    list(orch.approve_stream(conversation=tid))

    orch.reset_app()

    assert [t["appId"] for t in _tags(orch, tid)] == [app_id]


# ---- the filter has a second writer now ---------------------------------------------------------


def test_the_tag_is_still_the_filters_name_of_last_resort():
    """The filter used to be the chip's alone, so the app it named was in some thread's tags by
    definition and the rail read the name off them. The Build header writes the filter too, and it
    can name an app no conversation has changed — so the Project's app list is asked first.

    The tag stays the fallback, and that is what this holds onto: an app the store has not loaded
    yet, or one dropped from the list, is still named by the conversations that changed it rather
    than read out as "an app"."""
    rail = (_WORKBENCH / "js" / "components" / "conversation-list.js").read_text()
    assert "(apps.find((a) => a.id === railAppFilter) || {}).name" in rail
    assert ".find((x) => x.appId === railAppFilter) || {}).appName" in rail
