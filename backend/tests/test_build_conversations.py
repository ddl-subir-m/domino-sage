"""Build is per conversation (ADR-0005).

Build used to run one OpenCode session and one transcript per Project, so "New conversation" in
the rail changed the route and nothing else. Both now belong to a Thread, the way Chat's already
did. These tests hold the two halves together: a fresh conversation must get a fresh session AND
a fresh transcript, or the screen empties while the agent still remembers.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.manager import ProjectRecord, Workspace
from sage.workspace.threads import ThreadStore


def _ws(tmp: Path) -> Workspace:
    return Workspace(project_id="p", path=tmp, app_id="app_t")


def _record(tmp: Path) -> ProjectRecord:
    return ProjectRecord(project_id="p", path=tmp)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),  # never called: no build runs here
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
    )


# ---- the transcript ------------------------------------------------------------------------

def test_a_conversation_reads_back_only_its_own_turns(tmp_path: Path):
    ws = _ws(tmp_path)
    ws.append_history({"type": "user", "text": "add a filter"}, "thr_a")
    ws.append_history({"type": "user", "text": "make it dark"}, "thr_b")

    assert [r["text"] for r in ws.read_history("thr_a")] == ["add a filter"]
    assert [r["text"] for r in ws.read_history("thr_b")] == ["make it dark"]
    # history.md renders the whole log: the agent's memory stays per app on purpose (ADR-0008).
    assert len(ws.read_history()) == 2


def test_untagged_history_goes_to_one_conversation_and_keeps_its_order(tmp_path: Path):
    ws = _ws(tmp_path)
    ws.append_history({"type": "user", "text": "first"})
    ws.append_history({"type": "done", "ok": True})
    ws.append_history({"type": "user", "text": "later"}, "thr_new")
    assert ws.has_untagged_history()

    ws.adopt_history("thr_old")

    assert not ws.has_untagged_history()
    assert len(ws.read_history("thr_old")) == 2
    assert [r["text"] for r in ws.read_history("thr_new")] == ["later"]
    assert [r.get("text") for r in ws.read_history()] == ["first", None, "later"]

    ws.adopt_history("thr_other")  # idempotent: nothing is left to claim
    assert len(ws.read_history("thr_old")) == 2
    assert ws.read_history("thr_other") == []


def test_the_stop_button_baseline_survives_adoption(tmp_path: Path):
    """adopt_history rewrites the log in place. The revert point is positional, so a rewrite that
    reordered or dropped a line would revert the wrong turn."""
    ws = _ws(tmp_path)
    ws.append_history({"type": "user", "text": "one"})
    ws.adopt_history("thr_old")

    baseline = ws.history_len()
    ws.append_history({"type": "user", "text": "two"}, "thr_new")
    ws.truncate_history(baseline)

    assert ws.read_history("thr_new") == []
    assert [r["text"] for r in ws.read_history("thr_old")] == ["one"]


# ---- the session ---------------------------------------------------------------------------

def test_each_conversation_owns_its_build_session(tmp_path: Path):
    # The Project's record, not the app's: a session belongs to a conversation, and it is filed
    # beside that Thread's chat session so a deleted Thread takes both halves with it (ADR-0008).
    record = _record(tmp_path)
    record.write_session_id("ses_a", "thr_a")
    record.write_session_id("ses_b", "thr_b")

    assert record.read_session_id("thr_a") == "ses_a"
    assert record.read_session_id("thr_b") == "ses_b"
    assert record.read_session_id() is None  # the unscoped record is its own
    assert record.build_session_path("thr_a") == tmp_path / ".sage" / "threads" / "thr_a" / "build-session.json"


def test_a_build_that_names_no_conversation_still_has_a_session(tmp_path: Path):
    """The CLI and the tests build without a rail. They keep the pre-ADR path."""
    record = _record(tmp_path)
    record.write_session_id("ses_cli")

    assert record.read_session_id() == "ses_cli"
    assert record.build_session_path() == tmp_path / ".sage" / "session.json"


def test_switching_conversation_drops_the_cached_session(tmp_path: Path):
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.build_conversation, project.session_id = "thr_a", "ses_a"

    Orchestrator._switch_conversation(project, "thr_b")
    assert project.build_conversation == "thr_b"
    assert project.session_id is None  # re-read from thr_b's own record

    project.session_id = "ses_b"
    Orchestrator._switch_conversation(project, "thr_b")
    assert project.session_id == "ses_b"  # same conversation keeps its session


# ---- through the orchestrator and the route -------------------------------------------------

def test_history_written_before_tagging_lands_on_the_oldest_conversation(tmp_path: Path):
    """An upgraded Project keeps its transcript, and the conversation the person just created
    still opens empty — which is the whole point of the button."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    old = store.create(title="first")
    new = store.create(title="second")
    project.workspace.append_history({"type": "user", "text": "built before tagging"})

    assert [r["text"] for r in orch.history(old["id"])] == ["built before tagging"]
    assert orch.history(new["id"]) == []
    assert len(orch.history()) == 1


def test_the_history_route_filters_to_the_conversation_it_is_asked_for(tmp_path: Path, monkeypatch):
    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    project.workspace.append_history({"type": "user", "text": "in a"}, "thr_a")
    project.workspace.append_history({"type": "user", "text": "in b"}, "thr_b")
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    assert [r["text"] for r in client.get("/api/project/history?conversation=thr_a").json()["history"]] == ["in a"]
    assert client.get("/api/project/history?conversation=thr_missing").json()["history"] == []
    assert len(client.get("/api/project/history").json()["history"]) == 2


# ---- the rail's name for it -------------------------------------------------------------------
#
# A Conversation opened in Build kept the literal words "New conversation" for the rest of its life,
# while the identical Conversation opened in Chat read back the sentence it was started with. Both
# modes have always shared this record; only Chat ever wrote its title (see `_chat_stream`). The
# rail draws one field for both, so the mode you happened to start in decided whether the row was
# findable a week later.


def _named_orch(tmp_path: Path):
    """A Project whose build turns actually run, so the title is asserted off the real path rather
    than off the helper called directly. `_orch` above cannot: its gateway is never called."""
    from .fake_opencode import FakeOpenCode, Turn

    template = _template(tmp_path)
    ws = tmp_path / "mnt" / "code"
    orch = Orchestrator(
        workspace_dir=ws, template=template, gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        opencode_client=FakeOpenCode(ws, [Turn(text="ok"), Turn(text="ok"), Turn(text="ok")]),
    )
    project = orch.project(start_preview=False)
    return orch, ThreadStore(project.record.path)


def test_a_build_conversation_is_named_after_the_first_thing_typed_into_it(tmp_path: Path):
    orch, store = _named_orch(tmp_path)
    thread = store.create()
    assert store.get(thread["id"])["title"] == "New conversation"

    list(orch.build_stream("add a revenue chart by region", None, None, conversation=thread["id"]))

    assert store.get(thread["id"])["title"] == "add a revenue chart by region"


def test_the_second_prompt_does_not_rename_the_conversation(tmp_path: Path):
    """The title is what the Conversation was STARTED for. A later turn changing it would move the
    row out from under someone mid-build."""
    orch, store = _named_orch(tmp_path)
    thread = store.create()

    list(orch.build_stream("add a revenue chart by region", None, None, conversation=thread["id"]))
    list(orch.build_stream("make it dark", None, None, conversation=thread["id"]))

    assert store.get(thread["id"])["title"] == "add a revenue chart by region"


def test_a_name_typed_into_the_rail_outranks_every_prompt_after_it(tmp_path: Path):
    """Same guard Chat uses, and this is what it buys: a rename is a decision, a prompt is not."""
    orch, store = _named_orch(tmp_path)
    thread = store.create()
    store.update(thread["id"], title="Q3 exposure")

    list(orch.build_stream("add a revenue chart by region", None, None, conversation=thread["id"]))

    assert store.get(thread["id"])["title"] == "Q3 exposure"


def test_a_build_naming_no_conversation_writes_no_title(tmp_path: Path):
    """A build driven from outside the rail — the CLI, a test — passes an id this store has no row
    for, or none at all. Neither is a Conversation to name."""
    orch, store = _named_orch(tmp_path)

    list(orch.build_stream("add a revenue chart by region", None, None, conversation=None))
    list(orch.build_stream("add a revenue chart by region", None, None, conversation="thr_missing"))

    assert store.list() == []
