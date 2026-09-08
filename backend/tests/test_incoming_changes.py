"""Incoming changes are shown at the start of a turn (#78, ADR-0008).

Two moments, two rules. While the person is here, a turn that would build on top of somebody else's
push stops and asks: pull and build on their work, or keep building and merge later. Once they have
gone, the save path decides for them — commit, pull, resolve, push — which is what keeps a build's
work from being silently rejected as a non-fast-forward.

Git is the PROJECT's: one repo holds every Built App, so one reading of the remote answers for all
of them, and what makes it this app's business is that the incoming files land in this app's
directory.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

from sage.orchestrator import service as svc
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace import git

from .fake_opencode import FakeOpenCode, Turn


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True,
                          check=True).stdout


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the Chat/Build classifier is its only caller here."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class FakeVite:
    made: ClassVar[list] = []

    def __init__(self, workspace, base_prefix: str = "", **_ignored) -> None:
        self.workspace = Path(workspace)
        self.running = False
        FakeVite.made.append(self)

    def start(self, ready_timeout_s: float = 30.0) -> str:
        self.running = True
        return "http://127.0.0.1:5173"

    def upstream(self) -> str:
        if not self.running:
            raise RuntimeError("Vite not ready")
        return "http://127.0.0.1:5173"

    def stop(self) -> None:
        self.running = False


class FakeQueries:
    def __init__(self, workspace, template=None) -> None:
        self.workspace = Path(workspace)
        self.port: int | None = None

    def start(self) -> None:
        self.port = 7777

    def refresh(self) -> None:
        pass

    def stop(self) -> None:
        self.port = None


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fake_preview(monkeypatch):
    FakeVite.made = []
    monkeypatch.setattr(svc, "ViteSupervisor", FakeVite)
    monkeypatch.setattr(svc, "PreviewQueries", FakeQueries)
    yield
    FakeVite.made = []


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    return t


def _orch(tmp: Path, root: Path, turns: list[Turn] | None = None):
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, oc


def _repo(tmp: Path) -> Path:
    """A Project volume that is the root of its own repo, with a remote to push to."""
    root = tmp / "mnt" / "code"
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("project\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    bare = tmp / "remote.git"
    _git(tmp, "init", "-q", "--bare", str(bare))
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-q", "-u", "origin", "HEAD")
    return root


def _teammate(tmp: Path) -> Path:
    """A second checkout of the same remote, standing in for somebody else in the Project."""
    other = tmp / "other"
    _git(tmp, "clone", "-q", str(tmp / "remote.git"), str(other))
    _git(other, "config", "user.email", "mate@example.com")
    _git(other, "config", "user.name", "Mate")
    return other


def _project(orch: Orchestrator):
    """The bound Project, with the plan gate off so a turn reaches the build path (#74)."""
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    return project


def _push_app(root: Path) -> None:
    """Publish the seeded app to the remote, so a teammate has something to change."""
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "sage: seed app")
    _git(root, "push", "-q")


def _mate_edits(other: Path, rel: str, body: str = "somebody else was here\n") -> None:
    path = other / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "mate: edit")
    _git(other, "push", "-q")


def _events(stream) -> list[dict]:
    return list(stream)


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


# --- reading the remote ------------------------------------------------------------------------

def test_incoming_lists_what_the_remote_has_that_we_do_not(tmp_path: Path):
    root = _repo(tmp_path)
    other = _teammate(tmp_path)
    _mate_edits(other, "apps/a1/src/App.tsx")

    # Nothing is known until somebody pays for the fetch — the badge is a reading, not a guess.
    assert git.incoming(root) == git.Incoming("", [])
    assert git.fetch(root) is True

    found = git.incoming(root)
    assert found.files == ["apps/a1/src/App.tsx"]
    assert found.head == _git(other, "rev-parse", "HEAD").strip()


def test_a_workspace_that_is_only_ahead_has_nothing_to_pull(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "mine.txt").write_text("local work\n")
    git.commit_all(root, "sage: local work")
    git.fetch(root)
    # Ahead, not behind: the diff is against the merge base, so our own commit is not incoming.
    assert git.incoming(root) == git.Incoming("", [])


def test_incoming_without_a_remote_is_empty(tmp_path: Path):
    root = tmp_path / "solo"
    root.mkdir()
    _git(root, "init", "-q")
    assert git.fetch(root) is False
    assert git.incoming(root) == git.Incoming("", [])


# --- the turn ------------------------------------------------------------------------------------

def test_a_turn_stops_and_shows_the_changed_files(tmp_path: Path):
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root)
    project = _project(orch)
    app_id = project.workspace.app_id
    _push_app(root)
    _mate_edits(_teammate(tmp_path), f"apps/{app_id}/src/App.tsx")

    events = _events(orch.build_stream("add a chart"))

    offer = _of(events, "incoming-changes")
    assert len(offer) == 1
    # Named the way this app names its own files, not the way the Project's repo does.
    assert offer[0]["files"] == ["src/App.tsx"] and offer[0]["count"] == 1
    assert offer[0]["prompt"] == "add a chart"
    assert _of(events, "done")[0] == {"type": "done", "ok": False, "decision": "incoming changes"}
    assert oc.prompts == []                       # stopped before any inference
    assert not (project.workspace.path / "src" / "chart.tsx").exists()


def test_a_turn_on_an_app_the_remote_has_not_touched_is_unaffected(tmp_path: Path):
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [Turn(text="done", writes={"src/chart.tsx": "chart\n"})])
    project = _project(orch)
    _push_app(root)
    # A teammate changed the Project's own record and another app — neither is this app's code.
    other = _teammate(tmp_path)
    _mate_edits(other, "apps/other-app/src/App.tsx")

    events = _events(orch.build_stream("add a chart"))

    assert _of(events, "incoming-changes") == []
    assert len(oc.prompts) == 1
    assert (project.workspace.path / "src" / "chart.tsx").exists()


def test_no_remote_means_no_gate(tmp_path: Path):
    root = tmp_path / "mnt" / "code"
    root.mkdir(parents=True)
    orch, oc = _orch(tmp_path, root, [Turn(text="done", writes={"src/chart.tsx": "chart\n"})])
    _project(orch)

    events = _events(orch.build_stream("add a chart"))

    assert _of(events, "incoming-changes") == []
    assert len(oc.prompts) == 1


def test_keep_building_runs_the_turn_and_is_not_asked_again(tmp_path: Path):
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root, [Turn(text="done", writes={"src/chart.tsx": "chart\n"}),
                                       Turn(text="done", writes={"src/table.tsx": "table\n"})])
    project = _project(orch)
    app_id = project.workspace.app_id
    _push_app(root)
    _mate_edits(_teammate(tmp_path), f"apps/{app_id}/src/App.tsx")
    assert _of(_events(orch.build_stream("add a chart")), "incoming-changes")

    answered = _events(orch.build_stream("add a chart", skip_incoming_gate=True))

    assert _of(answered, "incoming-changes") == []
    assert (project.workspace.path / "src" / "chart.tsx").exists()
    # The decision stands. The remote is still ahead of where it was when they answered, and asking
    # again every turn would be a wall rather than a choice.
    again = _events(orch.build_stream("add a table"))
    assert _of(again, "incoming-changes") == []
    assert (project.workspace.path / "src" / "table.tsx").exists()


def test_the_offer_comes_back_when_the_remote_moves_on(tmp_path: Path):
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root, [Turn(text="done", writes={"src/chart.tsx": "chart\n"})])
    project = _project(orch)
    app_id = project.workspace.app_id
    _push_app(root)
    other = _teammate(tmp_path)
    _mate_edits(other, f"apps/{app_id}/src/App.tsx")
    _events(orch.build_stream("add a chart", skip_incoming_gate=True))

    # Somebody pushes again: a new decision, so a new offer.
    _git(other, "pull", "-q")
    _mate_edits(other, f"apps/{app_id}/src/Chart.tsx")

    assert _of(_events(orch.build_stream("add a table")), "incoming-changes")


def test_the_offer_is_replayable_but_its_buttons_are_not(tmp_path: Path):
    """The transcript keeps the offer; only the live frame carries what a button needs."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    app_id = project.workspace.app_id
    _push_app(root)
    _mate_edits(_teammate(tmp_path), f"apps/{app_id}/src/App.tsx")

    _events(orch.build_stream("add a chart", conversation="th_1"))

    kinds = [e["type"] for e in orch.history("th_1")]
    assert kinds == ["user", "incoming-changes", "done"]


# --- the rail ------------------------------------------------------------------------------------

def test_the_rail_badges_the_app_the_remote_is_ahead_of(tmp_path: Path):
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    first = project.workspace.app_id
    second = orch.create_app()["id"]
    _push_app(root)
    _mate_edits(_teammate(tmp_path), f"apps/{first}/src/App.tsx")

    # Before the check has run, the rail claims nothing: a badge is a reading, not an assumption.
    assert [r["behind"] for r in orch.list_apps()] == [False, False]

    orch._check_remote(orch.project(start_preview=False))

    rows = {r["id"]: r["behind"] for r in orch.list_apps()}
    assert rows == {first: True, second: False}


def test_pulling_puts_the_badge_out(tmp_path: Path):
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root)
    project = _project(orch)
    app_id = project.workspace.app_id
    _push_app(root)
    _mate_edits(_teammate(tmp_path), f"apps/{app_id}/src/App.tsx")
    orch._check_remote(project)
    assert next(r["behind"] for r in orch.list_apps() if r["id"] == app_id)

    result = orch.sync()

    assert result["status"] == "merged" and result["pushed"] is True
    assert not next(r["behind"] for r in orch.list_apps() if r["id"] == app_id)
    assert (project.workspace.path / "src" / "App.tsx").read_text() == "somebody else was here\n"


# --- the save path, which is the other half of the rule -------------------------------------------

def test_a_save_still_commits_pulls_and_pushes(tmp_path: Path):
    """Criteria 4 and 5: the save-time behaviour is unchanged, and a push that would be rejected as
    non-fast-forward still lands because the save pulls first."""
    root = _repo(tmp_path)
    orch, _oc = _orch(tmp_path, root, [Turn(text="done", writes={"src/chart.tsx": "chart\n"})])
    project = _project(orch)
    app_id = project.workspace.app_id
    _push_app(root)
    # The remote moves after the turn is let through, so the push would be non-fast-forward.
    _mate_edits(_teammate(tmp_path), "README.md", "mate was here\n")

    events = _events(orch.build_stream("add a chart", skip_incoming_gate=True))

    saved = _of(events, "saved")
    assert saved and saved[0]["ok"] is True and saved[0]["pushed"] is True
    # Both sides are on the remote: the build's work reached the repo, and the pull kept theirs.
    on_remote = _git(tmp_path / "remote.git", "ls-tree", "-r", "--name-only", "HEAD")
    assert f"apps/{app_id}/src/chart.tsx" in on_remote
    assert (root / "README.md").read_text() == "mate was here\n"
