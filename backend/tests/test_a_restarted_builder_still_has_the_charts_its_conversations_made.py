"""A Conversation reopened after the Builder restarted still shows its charts (#222).

Six charts rendered, then the same Conversation came back forty minutes later with every card a
broken image: the title was right, the "Show all rows" count was right, and every
`GET /api/project/file/raw?path=examples/<threadId>/chartN.png` answered 404. Only the newest
Conversation's Artifact survived, and the cut was the Builder's boot time to the minute.

Nothing deleted them. They were never committed. `/examples` is a real ignore rule for a Built App,
where `examples` is a symlink up to the Project's Chat Artifacts and a committed link would be
dangling in a fresh clone. But ONE template `.gitignore` seeds two roots — every app AND the
Project root — and at the Project root `examples/` is the Artifacts themselves. So `git add -A`
never staged a single chart, they lived only on the container's own disk, and the restart pulled
back a transcript (`.sage/threads/` IS committed, artifacts.json and all) describing files no clone
had ever seen.

The two halves are tested apart. That the app still gets its rule, and the link still works, is
`test_the_build_agent_can_reach_the_chat_artifacts.py`. This is the half about the Project root:
the Artifacts must reach git, and a Project seeded before the fix must be repaired on the way in.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace import git

from .fake_opencode import FakeOpenCode

CHART = b"\x89PNG events by drug"
# What the app's copy of the rule is for, and what the Project root's copy destroyed.
RULE = "/examples"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n")
    (t / ".gitignore").write_text("node_modules\ndist\n")
    return t


def _orch(tmp: Path) -> tuple[Orchestrator, Path]:
    root = tmp / "mnt" / "code"
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=object(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", opencode_client=FakeOpenCode(root, []))
    return orch, root


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True,
                          check=True).stdout


def _repo(path: Path) -> None:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "dev@example.com")
    _git(path, "config", "user.name", "dev")


def _chart(root: Path, thread_id: str = "thr_a", name: str = "chart1_events_by_drug.png") -> Path:
    """One Chat Artifact where Chat writes them: in the Project, above every app."""
    path = root / "examples" / thread_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CHART)
    return path


def test_the_project_root_gitignore_is_what_lost_them(tmp_path: Path):
    """The bug itself, with no orchestrator in it: this is all it took.

    Stated as git's own behaviour, because the fix is only worth anything if this is true — and it
    is the step every reading of the incident skipped. `.sage/threads/` is committed beside it,
    which is exactly why the Conversation came back looking whole.
    """
    root = tmp_path / "code"
    (root / ".sage" / "threads" / "thr_a").mkdir(parents=True)
    (root / ".sage" / "threads" / "thr_a" / "artifacts.json").write_text('{"items": []}')
    _chart(root)
    _repo(root)

    (root / ".gitignore").write_text(f"node_modules\n{RULE}\n")
    git.commit_all(root, "with the rule")
    tracked = set(_git(root, "ls-files").split())
    assert "examples/thr_a/chart1_events_by_drug.png" not in tracked   # the loss, exactly
    assert ".sage/threads/thr_a/artifacts.json" in tracked             # the card that outlived it

    (root / ".gitignore").write_text("node_modules\n")
    git.commit_all(root, "without it")
    assert "examples/thr_a/chart1_events_by_drug.png" in set(_git(root, "ls-files").split())


def test_a_project_seeded_before_the_fix_is_repaired_on_the_way_in(tmp_path: Path):
    """Every Project that already exists carries the rule in a committed file. Opening it repairs.

    The charts already dropped are gone — nothing can bring those back — but the next commit takes
    the Artifacts the Project still has, and every one it makes from here."""
    orch, root = _orch(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(f"# a comment worth keeping\n{RULE}\ndist\n")

    orch.project(start_preview=False)

    kept = (root / ".gitignore").read_text()
    assert RULE not in kept.split()
    assert "dist" in kept.split()                      # only the one rule goes
    assert "# a comment worth keeping" in kept         # the reasoning stays readable


def test_the_repair_takes_nothing_but_the_one_rule(tmp_path: Path):
    """It runs on every attach, over a file people also edit, so it may only ever remove that line.

    Attach writes rules of its own here (`.sage/scratch/` and friends), which is why this asks what
    survived rather than for the file back verbatim."""
    orch, root = _orch(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text("node_modules\ndist\nmy-own-rule\n")

    orch.project(start_preview=False)

    rules = (root / ".gitignore").read_text().split()
    assert {"node_modules", "dist", "my-own-rule"} <= set(rules)
    assert RULE not in rules


def test_a_chat_artifact_reaches_git_after_the_repair(tmp_path: Path):
    """End to end, and the claim that actually matters: the charts are in the commit.

    A restart restores the working tree from that commit, so what is tracked here is exactly what
    a reopened Conversation gets to show."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    (root / ".gitignore").write_text(f"node_modules\n{RULE}\n")
    _repo(root)
    git.commit_all(root, "a Project seeded before the fix")

    _chart(root)
    _chart(root, name="chart2_severity_distribution.png")
    orch._unignore_chat_artifacts(project)
    git.commit_all(root, "a Chat turn")

    tracked = set(_git(root, "ls-files").split())
    assert "examples/thr_a/chart1_events_by_drug.png" in tracked
    assert "examples/thr_a/chart2_severity_distribution.png" in tracked
    # And a fresh clone — what the restarted Builder actually gets — has the bytes.
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(root), str(clone))
    assert (clone / "examples" / "thr_a" / "chart1_events_by_drug.png").read_bytes() == CHART


def test_the_app_still_keeps_its_own_link_out_of_git(tmp_path: Path):
    """The half of the rule that was always right must survive the fix.

    Without this, the obvious over-correction — dropping `/examples` everywhere — passes every
    test above while committing a symlink that dangles in the clone this whole issue is about."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    app = project.workspace.path
    _repo(root)
    _chart(root)

    orch._ensure_examples_link(project)
    git.commit_all(root, "a build turn")

    assert (app / "examples").is_symlink()
    assert RULE in (app / ".gitignore").read_text().split()
    tracked = set(_git(root, "ls-files").split())
    assert project.repo_rel("examples") not in tracked          # the link stays out
    assert "examples/thr_a/chart1_events_by_drug.png" in tracked  # the Artifacts go in
