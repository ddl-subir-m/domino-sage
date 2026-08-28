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
