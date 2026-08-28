"""The agent that resolves a merge conflict can reach the conflicted files.

`sync()` pulls, and on conflict hands the files to the agent to reconcile. Git names them from the
repo root — the Project volume — so a conflict in a Built App arrives as `apps/<appId>/src/App.tsx`.
The session that was handed the prompt is the BUILD session, opened at `app_for_turn().path`, so
that same relative path resolves to `apps/<appId>/apps/<appId>/src/App.tsx` and the agent edits
nothing. `files_with_conflict_markers` then still finds markers at the root, the merge is aborted,
and the pull is rolled back. Every time — there was no input for which this path could succeed.

Conflicts outside the selected app — a second Built App, or the Project's own files at the root —
were not merely mis-addressed but unreachable from that directory at all.

The turn is a Project-level operation on a Project-level merge, so it gets a session at the Project
root. See `Orchestrator._resolve_conflicts`.
"""
from __future__ import annotations

from pathlib import Path

from sage.workspace import git

from .fake_opencode import Turn
from .test_incoming_changes import (  # noqa: F401 — fixtures come with them
    _fake_preview,
    _git,
    _mate_edits,
    _no_waiting,
    _orch,
    _project,
    _push_app,
    _repo,
    _teammate,
)

RESOLVED = "both sides, reconciled\n"


def _conflict(tmp_path: Path, rel_from_root: str, resolution: dict[str, str]):
    """A real merge conflict on `rel_from_root`, with the agent scripted to write `resolution`.

    Both sides edit the same file, so the pull genuinely conflicts and the markers are real — the
    point of the exercise is what the agent can reach, and a simulated conflict would let a broken
    path pass.
    """
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [Turn(text="resolved", writes=resolution)])
    project = _project(orch)
    _push_app(root)

    mate = _teammate(tmp_path)
    _mate_edits(mate, rel_from_root, "their version\n")

    ours = root / rel_from_root
    ours.parent.mkdir(parents=True, exist_ok=True)
    ours.write_text("our version\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ours")
    return root, orch, oc, project


def test_the_agent_is_given_a_path_it_can_actually_open(tmp_path: Path):
    """The bug, at the seam that caused it: the prompt names the file the way git does, and the
    session has to be rooted where that name resolves."""
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [Turn(text="resolved")])
    project = _project(orch)

    orch._resolve_conflicts(project, ["apps/app_x/src/App.tsx"])

    session = oc.sessions[-1]
    named = oc.prompts[-1]["text"]
    assert "apps/app_x/src/App.tsx" in named
    # The directory the prompt's paths are relative to is the one git named them from.
    assert Path(session["directory"]) == project.record.path


def test_a_conflict_in_the_selected_app_is_resolved_rather_than_rolled_back(tmp_path: Path):
    """End to end. The agent writes to the path it was given; with the session at the app the write
    landed at `apps/<id>/apps/<id>/...` and the real file kept its markers."""
    app_id = None
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [])
    project = _project(orch)
    app_id = project.workspace.app_id
    rel = f"apps/{app_id}/src/App.tsx"

    _push_app(root)
    _mate_edits(_teammate(tmp_path), rel, "their version\n")
    (root / rel).write_text("our version\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ours")
    oc.turns.append(Turn(text="resolved", writes={rel: RESOLVED}))

    result = orch.sync()

    assert result["status"] == "merged", result
    assert (root / rel).read_text() == RESOLVED
    assert git.files_with_conflict_markers(root, [rel]) == []


def test_a_conflict_outside_the_selected_app_is_reachable_at_all(tmp_path: Path):
    """The half that was not merely mis-addressed. A Project file at the volume root cannot be
    named from inside `apps/<appId>/`, so this conflict had no expressible path before."""
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [])
    _project(orch)

    _push_app(root)
    _mate_edits(_teammate(tmp_path), "README.md", "their readme\n")
    (root / "README.md").write_text("our readme\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ours")
    oc.turns.append(Turn(text="resolved", writes={"README.md": RESOLVED}))

    result = orch.sync()

    assert result["status"] == "merged", result
    assert (root / "README.md").read_text() == RESOLVED


def test_an_agent_that_leaves_markers_still_rolls_the_pull_back(tmp_path: Path):
    """The rollback is the right answer to an unresolved conflict and stays. What changed is that
    it is now reached by an agent that failed, rather than by one that was never addressable."""
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [])
    project = _project(orch)
    rel = f"apps/{project.workspace.app_id}/src/App.tsx"

    _push_app(root)
    _mate_edits(_teammate(tmp_path), rel, "their version\n")
    (root / rel).write_text("our version\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ours")
    oc.turns.append(Turn(text="I gave up"))          # writes nothing; markers survive

    result = orch.sync()

    assert result["status"] == "conflict-unresolved", result
    assert (root / rel).read_text() == "our version\n"     # pre-pull state is back


def test_the_build_session_is_not_the_one_that_resolves(tmp_path: Path):
    """A build session is the Built App's and belongs to a conversation; this turn is neither. It
    also must not inherit the app directory, which is the whole bug."""
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [Turn(text="resolved")])
    project = _project(orch)
    build_sid = orch._ensure_session(project, project.build_conversation)

    orch._resolve_conflicts(project, ["README.md"])

    assert oc.prompts[-1]["session"] != build_sid
