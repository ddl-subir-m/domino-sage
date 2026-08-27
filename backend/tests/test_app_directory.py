"""A Built App lives in its own directory (ADR-0008, #67).

A Project used to BE an app: the volume root held the React code, and Sage's record sat in the
`.sage/` beside it. A Project holds Built Apps now, so the app moved one level down into
`apps/<appId>/` and what is left at the root is the Project's own — Threads, plan documents,
settings. These tests hold that line: the app is born when a handoff is confirmed, it is where the
build agent stands, and its id is the directory's name and never changes.

One app, because that is what this ticket is. The second app is the next one.
"""
from __future__ import annotations

import json
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
    """One scripted word for every routed request — the Chat/Build classifier is its only caller."""

    def __init__(self, verdict: str = "CHAT") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


_PLAN = (
    "A desk exposure dashboard.\n\n"
    "## Plan\n"
    "1. **Desk table** — Show notional by desk.\n\n"
    "## Open questions\n"
    "None — ready to build.\n"
)


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
    # The real template ships one, and it is what the Project's instructions get rendered into.
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    return t


def _orch(tmp: Path, turns: list[Turn] | None = None, *, verdict: str = "CHAT"):
    """An orchestrator that has NOT attached yet, so a test can watch the app be born."""
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, oc, root


def _handoff(orch, thread_id: str) -> dict:
    orch.draft_handoff_plan(thread_id)
    return orch.confirm_handoff(thread_id, {"resources": False, "artifacts": False,
                                            "transcript": False})


def test_chat_alone_never_creates_an_app_directory(tmp_path: Path):
    """A Project with no app on it is the ordinary state: Chat is the door, and asking a question
    is not asking for an app."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="Notional is the sum of the legs.")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "what is notional?"))

    assert not (root / "apps").exists()
    assert not (root / "package.json").exists()   # and no app at the root either
    assert (root / ".sage" / "threads").is_dir()  # the Thread is the Project's, and it is here


def test_a_confirmed_handoff_creates_an_app_directory_seeded_from_the_template(tmp_path: Path):
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_PLAN)])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk dashboard"))

    # Drafting the sheet creates nothing: the plan lands in a document, which is the Project's.
    # Somebody who opens the sheet and closes it again has asked for no app, and must not get one.
    orch.draft_handoff_plan(tid)
    assert not (root / "apps").exists()

    orch.confirm_handoff(tid, {"resources": False, "artifacts": False, "transcript": False})

    app = orch.project(start_preview=False).workspace
    assert [p.name for p in (root / "apps").iterdir()] == [app.app_id]   # one app, named for its id
    assert app.path == root / "apps" / app.app_id
    assert (app.path / "package.json").read_text() == '{"name": "template"}'
    assert (app.path / "src" / "App.tsx").exists()
    # And the builder's copy of the plan is in there with it, ready for the implement turn.
    assert app.read_plan().startswith("A desk exposure dashboard.")


def test_a_dismissed_handoff_sheet_leaves_no_app_behind(tmp_path: Path):
    """A Built App is born when a handoff is CONFIRMED (ADR-0008). Drafting a sheet and walking
    away is a person deciding not to build, and it must cost them nothing — with one app per
    Project that is a stray directory, and with several it would be a stray app per dismissal."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_PLAN)])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk dashboard"))

    orch.draft_handoff_plan(tid)

    assert not (root / "apps").exists()
    assert orch.read_plan_doc("001")["markdown"].startswith("A desk exposure dashboard.")
    assert orch.get_thread(tid)["handoff"]["status"] == "planned"   # the sheet is still offered


def test_a_build_writes_its_code_in_the_app_directory(tmp_path: Path):
    """The app directory is the build agent's working directory, so from the agent's side nothing
    has moved: it writes `src/App.tsx` and that lands one level down."""
    orch, oc, root = _orch(tmp_path, [
        Turn(text="A dashboard.\n\n## Plan\n1. **Table** — Show it.\n"),
        Turn(text="Built it.", writes={"src/App.tsx": "// the desk table\n"}),
    ], verdict="BUILD")
    orch.project(start_preview=False)   # no Vite in tests
    list(orch.build_stream("build me a desk dashboard"))
    list(orch.approve_stream())

    app = orch.project(start_preview=False).workspace
    assert (app.path / "src" / "App.tsx").read_text() == "// the desk table\n"
    assert not (root / "src").exists()                       # nothing at the Project root
    assert oc.sessions[0]["directory"] == str(app.path)      # and that is where the agent stood


def test_the_project_keeps_its_threads_plan_documents_and_settings_at_the_root(tmp_path: Path):
    """App-scoped records resolve inside the app; the Project's stay where the Project is."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_PLAN)])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk dashboard"))
    _handoff(orch, tid)

    project = orch.project(start_preview=False)
    app = project.workspace.path
    assert (root / ".sage" / "threads" / tid).is_dir()
    assert (root / ".sage" / "plan-docs" / "001").is_dir()
    assert project.record.settings_path == root / ".sage" / "settings.json"
    assert not (app / ".sage" / "plan-docs").exists()

    # And the app's own record is inside the app, in a `.sage/` of its own.
    project.workspace.mark_built()
    project.workspace.append_history({"type": "user", "text": "asked"})
    assert (app / ".sage" / "settings.json").is_file()
    assert (app / ".sage" / "history.jsonl").is_file()
    assert not (root / ".sage" / "history.jsonl").exists()


def test_the_file_api_reads_a_chat_artifact_from_the_root_and_app_code_from_the_app(
        tmp_path: Path, monkeypatch):
    """The UI hands back the path it was given, and a Chat Artifact and a source file are shaped
    the same. Resolving both against the app would 404 every chart Chat has ever drawn."""
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    orch, _oc, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    artifact = root / "examples" / "thr_a" / "exposure.table.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"title": "Desks"}')
    (project.workspace.path / "src" / "App.tsx").write_text("// the app\n")

    monkeypatch.setattr(appmod, "orchestrator", orch)
    with TestClient(appmod.control_app) as client:
        assert client.get("/api/project/file?path=examples/thr_a/exposure.table.json"
                          ).json()["content"] == '{"title": "Desks"}'
        assert client.get("/api/project/file?path=src/App.tsx").json()["content"] == "// the app\n"


def test_project_instructions_are_the_projects_and_survive_the_app(tmp_path: Path):
    """Written before there is an app, rendered into every app there turns out to be. The block in
    AGENTS.md is a rendering: the file comes back from the template on Reset, and a second app gets
    its own copy — so the sentence the person wrote is kept on the Project instead."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_PLAN)])
    project = orch.project(start_preview=False, seed_app=False)
    orch.write_instructions(project, "Always label axes in full.")

    # No app yet, so there is no AGENTS.md — and the instructions are kept anyway.
    assert not (root / "apps").exists()
    assert (root / ".sage" / "instructions.md").read_text().strip() == "Always label axes in full."

    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk dashboard"))
    _handoff(orch, tid)

    # The app the handoff created renders them, though its AGENTS.md came from the template.
    app = orch.project(start_preview=False).workspace
    assert "Always label axes in full." in (app.path / "AGENTS.md").read_text()
    assert orch.read_instructions(orch.project(start_preview=False)) == "Always label axes in full."

    # And Reset app puts the render back, having taken AGENTS.md from the template again.
    orch.reset_app()
    assert "Always label axes in full." in (app.path / "AGENTS.md").read_text()


def test_the_app_id_and_its_directory_never_change(tmp_path: Path):
    """Domino fixes a published App's `entryPoint` when the App is created, so a renamed directory
    would strand the deployment. A second attach — a restart — finds the app rather than minting
    a second one beside it, and Reset app replaces the code inside the same directory."""
    orch, _oc, root = _orch(tmp_path)
    first = orch.project(start_preview=False).workspace.app_id

    orch.reset_app()
    assert orch.project(start_preview=False).workspace.app_id == first

    restarted, _oc2, _root = _orch(tmp_path)
    assert restarted.project(start_preview=False).workspace.app_id == first
    assert [p.name for p in (root / "apps").iterdir()] == [first]


def test_the_projects_own_trees_are_kept_out_of_git_at_the_root(tmp_path: Path):
    """The app carries the template's .gitignore. Chat's scratch and its OpenCode workdir stayed
    at the root when the app moved down, and nothing seeds a file up there to carry their rules —
    so `git add -A` would have started committing dataset copies and a directory of symlinks."""
    orch, _oc, root = _orch(tmp_path, [Turn(text="ok")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "hi"))

    ignored = (root / ".gitignore").read_text().split()
    assert ".sage/scratch/" in ignored
    assert ".sage/chat-work/" in ignored
    assert ".sage/threads/*/.*.tmp" in ignored
    assert (root / ".sage" / "chat-work").is_dir()   # the tree the rule is for


def test_a_plan_document_gains_its_app_reference_when_it_binds(tmp_path: Path):
    """A plan is drafted in a Thread, before the app exists, so it cannot be born inside one. It
    stays with the Project and gains the reference at the moment it binds."""
    orch, _oc, _root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_PLAN)])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk dashboard"))

    orch.draft_handoff_plan(tid)
    assert orch.read_plan_doc("001")["appId"] == ""      # drafted, not yet bound to anything

    orch.confirm_handoff(tid, {"resources": False, "artifacts": False, "transcript": False})

    app_id = orch.project(start_preview=False).workspace.app_id
    assert orch.read_plan_doc("001")["appId"] == app_id
