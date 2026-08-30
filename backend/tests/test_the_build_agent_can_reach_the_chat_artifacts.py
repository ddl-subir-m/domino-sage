"""The build agent can open the Artifacts the handoff prompt names.

`handoff._HANDOFF_LINE` tells the implement turn that "a Chat Thread produced files under
`examples/`", and the digest lists them by workspace-shaped path. But the Artifacts live at
`<root>/examples/<threadId>/` while the build agent's cwd is `<root>/apps/<appId>/`, two levels
below — so every path the digest named resolved to nothing. It failed silently: the plan is the
spec and the Artifacts are background, so the build still worked and nobody saw the reads fail.

Chat's own workdir has had a link into `examples/` since it was written (`ensure_chat_workdir`),
for this exact reason. This is the app directory getting the same treatment: a relative symlink
`apps/<appId>/examples -> ../../examples`, ensured at the `_refresh_history_archive` seam, which is
the one place that runs before every turn's baseline AND at the tail of Reset.

The link must never be committed — it points outside the app tree, so a fresh clone of a repo that
carried it would get a dangling link. The rule that keeps it out is `/examples`, anchored and with
NO trailing slash, and that detail is load-bearing: git records a symlink as a symlink rather than
as a directory, and `examples/` matches directories only, so the trailing-slash form silently
commits the link. `test_the_link_is_ignored_by_a_rule_that_actually_matches_a_symlink` is what
holds that.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.snapshot import TurnSnapshot

from .fake_opencode import FakeOpenCode, Turn

ARTIFACT = b"\x89PNG revenue by desk"

_PLAN = (
    "A desk exposure dashboard.\n\n"
    "## Plan\n"
    "1. **Desk table** — Show notional by desk.\n\n"
    "## Open questions\n"
    "None — ready to build.\n"
)


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport

        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word for every routed request — the Chat/Build classifier is the only caller."""

    def __init__(self, verdict: str = "CHAT") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The two waits a scripted turn can only ever spend."""
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
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    # The rule under test. The real template ships it; a fixture without it would let every test
    # here pass on the runtime repair alone and never notice the template had stopped carrying it.
    (t / ".gitignore").write_text("node_modules\ndist\n/examples\n")
    return t


def _orch(tmp: Path, turns: list[Turn] | None = None, *, verdict: str = "CHAT"):
    root = tmp / "mnt" / "code"
    orch = Orchestrator(workspace_dir=root, template=_template(tmp),
                        gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(),
                        opencode_client=FakeOpenCode(root, turns or []))
    return orch, root


def _artifact(root: Path, thread_id: str = "thr_a", name: str = "revenue.png") -> Path:
    """One Chat Artifact where Chat writes them: in the Project, above every app."""
    path = root / "examples" / thread_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ARTIFACT)
    return path


def _unlinked(app: Path) -> None:
    """Put an app back into the state every app seeded before this change was in: no link, and a
    .gitignore that never carried the rule."""
    link = app / "examples"
    if link.is_symlink():
        link.unlink()
    gitignore = app / ".gitignore"
    kept = [ln for ln in gitignore.read_text().splitlines() if ln.strip() != "/examples"]
    gitignore.write_text("".join(ln + "\n" for ln in kept))


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True,
                          check=True).stdout


def _repo(path: Path) -> None:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "dev@example.com")
    _git(path, "config", "user.name", "Dev")


# ---- the link is there, and it reaches the file ---------------------------------------------

def test_a_confirmed_handoff_leaves_the_artifacts_readable_from_the_build_agents_cwd(tmp_path: Path):
    """The bug, end to end. Chat produces an Artifact, the handoff hands the implement turn a path
    that names it, and the turn runs in `apps/<appId>/` — so the assertion is the read the agent
    would do: same path, from that directory, same bytes."""
    orch, root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=_PLAN),
                                  Turn(text="Built it.", writes={"src/App.tsx": "// built\n"})])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "build me a desk dashboard"))
    original = _artifact(root, tid)

    orch.draft_handoff_plan(tid)
    orch.confirm_handoff(tid, {"resources": False, "artifacts": True, "transcript": False})
    list(orch.build_stream("build it"))

    app = orch.project(start_preview=False).workspace.path
    assert app.parent.parent == root                      # the agent stands two levels down
    reached = app / "examples" / tid / "revenue.png"
    assert reached.read_bytes() == original.read_bytes()  # the path the digest names, from here
    assert (app / "examples").is_symlink()                # linked, not copied (dataset_probe.py:140)


def test_an_app_seeded_before_this_change_gains_the_link_on_its_next_turn(tmp_path: Path):
    """No app on disk today has the link, and none of them is going to be re-seeded to get one."""
    orch, root = _orch(tmp_path, [Turn(text=_PLAN)])
    app = orch.project(start_preview=False).workspace.path
    _unlinked(app)
    _artifact(root)

    list(orch.build_stream("build me a desk dashboard"))

    assert (app / "examples" / "thr_a" / "revenue.png").read_bytes() == ARTIFACT
    assert "/examples" in (app / ".gitignore").read_text().split()


def test_the_non_streaming_build_turn_gets_the_link_too(tmp_path: Path):
    """The fourth entrypoint. `build()` is the non-streaming turn behind the API's build button —
    it reuses the session and runs the feedback loop without yielding events, so nothing in the
    streaming tests covers it. It is a turn, so it gets the link on the same terms."""
    orch, root = _orch(tmp_path, [Turn(text="Built it.", writes={"src/App.tsx": "// built\n"})])
    app = orch.project(start_preview=False).workspace.path
    _unlinked(app)
    _artifact(root)

    assert orch.build("add a desk table")["ok"] is True

    assert (app / "examples" / "thr_a" / "revenue.png").read_bytes() == ARTIFACT


def test_the_link_is_relative_so_it_survives_the_volume_moving(tmp_path: Path):
    """`../../examples`, not `/mnt/code/examples`. The Project is a git repo that gets cloned into
    another builder's volume, and an absolute link would point at the first builder's path."""
    orch, _root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    app = project.workspace.path
    orch._ensure_examples_link(project)

    assert (app / "examples").readlink() == Path("../../examples")


# ---- creating it must not look like the agent writing ----------------------------------------

def test_a_read_only_turn_that_creates_the_link_still_passes_the_gate(tmp_path: Path):
    """The constraint that picks the seam. Creating the link and adding its ignore rule are writes,
    and after the turn's baseline they would read as an Ask/Plan turn that changed the tree — a
    spurious "gate violated" on the first turn of every app that exists today."""
    orch, root = _orch(tmp_path, [Turn(text=_PLAN)])
    app = orch.project(start_preview=False).workspace.path
    _unlinked(app)   # so this turn does BOTH writes: the rule and the link
    _artifact(root)

    events = list(orch.build_stream("build me a desk dashboard"))

    done = [e for e in events if e["type"] == "done"]
    # A gated turn resolves before the typecheck loop, so "awaiting approval" IS the clean exit;
    # the violation path ends at "gate violated" with an error event and a discarded tree.
    assert done and done[-1]["decision"] == "awaiting approval"
    assert not [e for e in events if e["type"] == "error"]
    assert (app / "examples" / "thr_a" / "revenue.png").exists()


def test_a_question_turn_that_creates_the_link_discards_nothing(tmp_path: Path):
    """The other read-only turn. Answer-only reverts the tree when it thinks the agent wrote, which
    would take the user's built app with it."""
    orch, root = _orch(tmp_path, [Turn(text=_PLAN),
                                  Turn(text="Built it.", writes={"src/App.tsx": "// built\n"}),
                                  Turn(text="It uses Highcharts.")])
    orch.project(start_preview=False)   # attach and seed, without starting Vite
    list(orch.build_stream("build me a desk dashboard"))
    list(orch.approve_stream())
    app = orch.project(start_preview=False).workspace.path
    _unlinked(app)
    _artifact(root)

    events = list(orch.build_stream("what charting library does this use?"))

    assert [e for e in events if e["type"] == "done"][-1]["decision"] == "answered"
    assert (app / "src" / "App.tsx").read_text() == "// built\n"   # not discarded
    assert (app / "examples" / "thr_a" / "revenue.png").exists()


# ---- Reset ------------------------------------------------------------------------------------

def test_reset_leaves_the_link_working_when_the_next_turn_runs(tmp_path: Path):
    """Reset takes the link with the rest of the app — `examples` is deliberately not in
    `_RESET_KEEP`. The seam at the tail of `reset_app` is what puts it back, which is the same way
    `.sage/history.md` is handled, so the invariant stays in one place."""
    orch, root = _orch(tmp_path, [Turn(text=_PLAN),
                                  Turn(text="Built it.", writes={"src/App.tsx": "// built\n"}),
                                  Turn(text=_PLAN)])
    orch.project(start_preview=False)   # attach and seed, without starting Vite
    list(orch.build_stream("build me a desk dashboard"))
    list(orch.approve_stream())
    app = orch.project(start_preview=False).workspace.path
    _artifact(root)
    assert (app / "examples").is_symlink()

    orch.reset_app()

    # Restored by the seam at the tail of Reset, before anything gets a chance to read it...
    assert (app / "examples" / "thr_a" / "revenue.png").read_bytes() == ARTIFACT
    # ...and still working on the turn that follows, which is what the criterion actually names.
    # Reset cleared `built`, so this turn is gated and read-only — the case that would have
    # reported a spurious violation if the link were being re-made after the baseline.
    events = list(orch.build_stream("build me a desk dashboard"))
    assert [e for e in events if e["type"] == "done"][-1]["decision"] == "awaiting approval"
    assert (app / "examples" / "thr_a" / "revenue.png").read_bytes() == ARTIFACT
    # And the Artifacts themselves are the Project's, so Reset never had a claim on them.
    assert (root / "examples" / "thr_a" / "revenue.png").read_bytes() == ARTIFACT


# ---- git ---------------------------------------------------------------------------------------

def test_the_link_is_ignored_by_a_rule_that_actually_matches_a_symlink(tmp_path: Path):
    """The trailing slash is the trap. Git does not follow a symlink, so it records this one as a
    symlink and not as a directory — `/examples/` would match nothing and `git add -A` would commit
    a link pointing outside the app tree, which a fresh clone resolves to nothing.

    Asserted at the two levels it has to hold: the link is not staged, and git never descends
    through it to stage the Artifacts twice. The template's own rule is stripped first, so what is
    on trial is the rule `_ensure_examples_link` writes — otherwise the fixture's copy would cover
    for a regression in the code's."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    app = project.workspace.path
    _unlinked(app)
    _repo(root)
    _artifact(root)
    orch._ensure_examples_link(project)

    _git(root, "add", "-A")
    tracked = _git(root, "ls-files").split()

    assert project.repo_rel("examples") not in tracked
    assert not [p for p in tracked if p.startswith(f"{project.repo_rel('')}examples/")]
    assert "examples/thr_a/revenue.png" in tracked       # the real one still is, per ADR-0006
    assert not _git(root, "status", "--porcelain", "--", str(app / "examples")).strip()


def test_the_template_ships_the_rule(tmp_path: Path):
    """An app seeded after this change never has to be repaired at runtime."""
    template = Path(__file__).resolve().parents[2] / "template" / "react-vite"
    rules = {ln.strip() for ln in (template / ".gitignore").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")}
    assert "/examples" in rules
    assert "/examples/" not in rules and "examples" not in rules


# ---- the stop button --------------------------------------------------------------------------

def test_the_stop_buttons_revert_does_not_see_the_link(tmp_path: Path):
    """`TurnSnapshot` is a `git --git-dir` store over the app dir, and `dataset_probe.py:140` warns
    that moving `examples/` outside the work-tree breaks revert. A link is not a move — but that is
    the assumption this test exists to prove rather than assert.

    What must hold is that the link changes nothing about what a turn captures: the pre-turn commit
    does not record it, a turn that creates it does not read as a turn that wrote, and the revert
    neither deletes the link nor follows it into the Project's Artifacts."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    app = project.workspace.path
    original = _artifact(root)
    snap = TurnSnapshot(app)

    # The link exists before the turn's baseline, exactly as the seam puts it there.
    orch._ensure_examples_link(project)
    before = snap.working_tree_hash()
    snap.commit_before_turn()
    assert snap.changed_since_pre_turn() is False

    # A build turn: the agent edits a file and adds one.
    (app / "src" / "App.tsx").write_text("edited by the agent")
    (app / "src" / "Desk.tsx").write_text("brand new")
    assert snap.changed_since_pre_turn() is True

    snap.discard_changes()

    assert (app / "src" / "App.tsx").read_text() == "export default function App() { return null }\n"
    assert not (app / "src" / "Desk.tsx").exists()
    # The revert left the link alone, and left the Project's Artifacts alone through it.
    assert (app / "examples").is_symlink()
    assert original.read_bytes() == ARTIFACT
    # The link is not in what the snapshot captures, so the revert neither restored nor removed it.
    # This app is template-seeded, so its ignore rule pre-dates the turn and no write happened here
    # — the legacy app's one-off rule write is the case the ordering test above covers.
    assert snap.working_tree_hash() == before


def test_the_symlink_itself_is_invisible_to_git(tmp_path: Path):
    """Half of the claim, and the half that holds forever: once the rule is in place — the steady
    state for every app seeded after this change — creating the link moves nothing at all.

    The other half is the rule's own write, which happens once per legacy app and IS visible. That
    is the half with the ordering constraint, and the test below is what pins it."""
    orch, _root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    app = project.workspace.path   # template-seeded, so .gitignore already carries /examples
    snap = TurnSnapshot(app)
    before = snap.working_tree_hash()

    orch._ensure_examples_link(project)

    assert (app / "examples").is_symlink()
    assert snap.working_tree_hash() == before


def test_the_ignore_rules_write_is_why_the_seam_runs_before_the_baseline(tmp_path: Path):
    """The ordering constraint, stated as the thing that breaks without it.

    It would be comfortable to say the whole operation is invisible to git. It is not. The symlink
    is, but the rule that HIDES the symlink is an ordinary file write, and on every app seeded
    before this change that write lands on the next turn. Run before the turn's pre-turn commit it
    is part of the state the turn starts from. Run after it, the turn opens on a working tree that
    has already changed — and an Ask or Plan turn that wrote nothing reports a gate violation.

    Both orders are asserted, because only the pair says the ordering is load-bearing: a test that
    checked the good order alone would still pass with the call in the wrong place."""
    orch, _root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    _unlinked(project.workspace.path)          # the legacy app: no link, and no rule
    snap = TurnSnapshot(project.workspace.path)

    # The order the seam uses: the write is inside the baseline, so the turn starts clean.
    orch._ensure_examples_link(project)
    snap.commit_before_turn()
    assert snap.changed_since_pre_turn() is False

    # The order the spec forbids, on a second legacy app: the turn starts dirty, and that is
    # exactly what the read-only gate reads as "the agent wrote".
    orch2, _r2 = _orch(tmp_path / "second")
    project2 = orch2.project(start_preview=False)
    _unlinked(project2.workspace.path)
    snap2 = TurnSnapshot(project2.workspace.path)
    snap2.commit_before_turn()
    orch2._ensure_examples_link(project2)
    assert snap2.changed_since_pre_turn() is True


def test_a_stop_does_not_delete_the_link_because_its_rule_pre_dates_the_baseline(tmp_path: Path):
    """What the `commit_before_turn` boundary actually protects, which is not the gate.

    Worth stating precisely, because the gate alone does not pin this line: `agent_wrote()` compares
    against `turn_tree_baseline`, taken later still, so a seam moved a few lines down would still
    pass an Ask turn. The stop button is what makes the earlier boundary the right one. Stop reverts
    to the pre-turn commit and then runs `git clean -fd`. Written after that commit, the reset takes
    the ignore rule back out, `clean` stops seeing the link as ignored, and the stop deletes it —
    so every stopped turn would churn the link out and the next turn would put it back."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    app = project.workspace.path
    _unlinked(app)
    _artifact(root)
    snap = TurnSnapshot(app)

    orch._ensure_examples_link(project)   # the order the seam uses
    snap.commit_before_turn()
    snap.discard_changes()                # the stop button

    assert (app / "examples" / "thr_a" / "revenue.png").read_bytes() == ARTIFACT
    assert "/examples" in (app / ".gitignore").read_text().split()

    # The forbidden order, on a second legacy app: the stop takes the link with it.
    orch2, root2 = _orch(tmp_path / "second")
    project2 = orch2.project(start_preview=False)
    app2 = project2.workspace.path
    _unlinked(app2)
    _artifact(root2)
    snap2 = TurnSnapshot(app2)
    snap2.commit_before_turn()
    orch2._ensure_examples_link(project2)
    snap2.discard_changes()

    assert not (app2 / "examples").exists()
    assert (root2 / "examples" / "thr_a" / "revenue.png").read_bytes() == ARTIFACT  # never followed


# ---- delete_app ---------------------------------------------------------------------------------

def test_deleting_an_app_removes_the_link_and_leaves_the_artifacts_standing(tmp_path: Path):
    """`delete_app` rmtrees the app directory. rmtree unlinks a symlink rather than following it —
    the same property `node_modules` already relies on — so the Project's Artifacts, which every
    other app and every Thread still reads, stay where they are."""
    orch, root = _orch(tmp_path)
    project = orch.project(start_preview=False)
    app = project.workspace.path
    original = _artifact(root)
    orch._ensure_examples_link(project)
    assert (app / "examples" / "thr_a" / "revenue.png").exists()

    orch._wm.delete_app(project.workspace.app_id)

    assert not app.exists()
    assert original.read_bytes() == ARTIFACT
    assert sorted(p.name for p in (root / "examples").iterdir()) == ["thr_a"]
