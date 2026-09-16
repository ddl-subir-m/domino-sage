"""A merge the model resolved says so and can be taken back (#233, ADR-0053).

`git pull` conflicts, Sage hands the files to the model, and the only check that ran was "are the
markers gone". A resolution that deletes one side entirely passed it, `finalize_merge` committed it
and `sync()` pushed it, with nobody having read the result.

The fix is an undo rather than a gate, and the reason is a fact about the product: the Workbench
cannot edit a file, and Pull and build is not a control anybody chooses — the person asked for a
BUILD, and the merge is the prerequisite Sage adds. A gate would stop a request they made with a
task they never asked for and have no tool to finish (#366). So the merge still lands, on both
paths; what changes is that it is now said out loud and can be reverted.

The offer is derived from git and written down nowhere. The commit message IS the record: the
subject marks a merge the model resolved, the body names the files it rewrote, and a revert's
subject names the merge it took back. That is what makes the offer survive the case that most needs
it — `_save_to_git` merges on the way down from a SIGTERM, so the first person to see that merge
sees it after a restart, with no transcript to hold a card.

Each test here kills a plausible wrong build, and says which in its own words.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
OURS = "our version\n"


def _row(orch, app_id: str) -> dict:
    """One rail row, after a deliberate check rather than whatever the background left behind."""
    orch._check_remote(orch.project(start_preview=False))
    return next(r for r in orch.list_apps() if r["id"] == app_id)


def _conflicted(tmp_path: Path, resolution: str = RESOLVED) -> SimpleNamespace:
    """A Project whose next pull conflicts on the selected app's `src/App.tsx`, with the model
    scripted to resolve it. A real conflict, not a simulated one: the whole subject here is what git
    records about a merge, and a faked one records nothing."""
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [])
    project = _project(orch)
    app_id = project.workspace.app_id
    rel = f"apps/{app_id}/src/App.tsx"

    _push_app(root)
    mate = _teammate(tmp_path)
    _mate_edits(mate, rel, "their version\n")
    (root / rel).write_text(OURS)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ours")
    oc.turns.append(Turn(text="resolved", writes={rel: resolution}))
    return SimpleNamespace(root=root, orch=orch, oc=oc, app_id=app_id, rel=rel, mate=mate)


def _merged(tmp_path: Path) -> SimpleNamespace:
    """The same, with the pull already done — so the merge exists and nobody has read it."""
    it = _conflicted(tmp_path)
    result = it.orch.sync()
    assert result["status"] == "merged", result
    return it


# --- the record, which is the whole of the design ------------------------------------------------

def test_the_merge_commits_message_is_the_only_record(tmp_path: Path):
    """LOAD-BEARING FORMAT. Three things ride on this message, and a change to it would retire the
    offer in silence: the subject is how a merge the model resolved is told from one git completed
    by itself, the body is the only place the conflicted files survive the process that knew them,
    and both are read back by `git.resolved_merge`."""
    it = _merged(tmp_path)

    body = _git(it.root, "log", "-n1", "--format=%B", "HEAD")
    parents = _git(it.root, "log", "-n1", "--format=%P", "HEAD").split()

    assert body.splitlines()[0] == git.MERGE_SUBJECT == "build: merge remote changes"
    # A header and then one path per line. Not comma-joined: a path may contain a comma, and
    # splitting one back apart would put two filenames that do not exist in front of somebody
    # deciding whether to undo.
    assert "Resolved conflicts in:\n" + it.rel in body
    # Two parents as well as the subject: `_save_to_git` builds `build: <prompt>` out of what a
    # person typed, so somebody could type the subject. A build commit has one parent.
    assert len(parents) == 2


def test_nothing_new_is_written_under_the_projects_own_record(tmp_path: Path):
    """No sha in `.sage/`, no new file, no migration (ADR-0053 rule four). A second copy could only
    disagree with git, and git's copy is the one that survives a clone."""
    it = _conflicted(tmp_path)
    sage = it.root / ".sage"

    def under_sage() -> list[str]:
        return sorted(p.relative_to(it.root).as_posix() for p in sage.rglob("*")) if sage.exists() else []

    before = under_sage()
    it.orch.sync()
    after_merge = under_sage()
    it.orch.undo_merge()

    assert after_merge == before
    assert under_sage() == before


# --- when the offer is made, and when it is not --------------------------------------------------

def test_a_merge_the_model_resolved_is_offered_back(tmp_path: Path):
    it = _merged(tmp_path)

    offer = _row(it.orch, it.app_id)["resolvedMerge"]

    assert offer is not None
    # The files it rewrote, named the way the merge names them: repo-relative, because a merge is
    # the Project's and is taken back whole or not at all.
    assert offer["files"] == [it.rel] and offer["count"] == 1
    assert offer["sha"] and _git(it.root, "cat-file", "-t", offer["sha"]).strip() == "commit"


def test_a_clean_auto_merge_says_nothing_and_offers_nothing(tmp_path: Path):
    """The other half of the pair above, and it has to be its own test: a build that speaks on every
    merge passes the first one and fails here. Announcing a merge git did by itself trains people to
    ignore the sentence that matters."""
    root = _repo(tmp_path)
    orch, oc = _orch(tmp_path, root, [])
    project = _project(orch)
    app_id = project.workspace.app_id
    _push_app(root)
    # A file this side never touched, so git merges it without asking anybody.
    _mate_edits(_teammate(tmp_path), "README.md", "theirs\n")
    # And a local commit of our own, so the pull is a real three-way merge rather than a
    # fast-forward. That is the point of this test: a fast-forward writes no merge commit at all, so
    # a build that offers to undo ANY merge commit would pass a version of it that never met the
    # case it gets wrong.
    (root / f"apps/{app_id}/src/Local.tsx").write_text("ours\n")

    result = orch.sync()

    assert result["status"] == "merged" and result["conflicts"] == []
    assert oc.prompts == []                       # the model was never called
    assert len(_git(root, "log", "-n1", "--format=%P", "HEAD").split()) == 2
    assert _git(root, "log", "-n1", "--format=%s", "HEAD").strip() != git.MERGE_SUBJECT
    assert _row(orch, app_id)["resolvedMerge"] is None


def test_the_merge_that_made_the_offer_makes_it_without_waiting_for_a_check(tmp_path: Path):
    """The rail's own check runs on a 30-second timer. `sync()` is what CREATES the merge, and the
    browser re-reads the app list the moment it returns, so an offer that waited for the next timed
    check would be missing from the one render the person is looking at."""
    it = _conflicted(tmp_path)

    it.orch.sync()

    # Read off the cache directly — no `_check_remote` of our own, which is what `_row` would do
    # and what would hide this.
    assert it.orch._resolved_merge_now() is not None


def test_a_refused_undo_commits_nothing_and_eats_nothing(tmp_path: Path):
    """"Nothing here has changed" has to be true of the refusal, and an auto-commit on the way in
    makes it false twice over: the commit itself is a change, and it is what turns the person's own
    open edits into the "work committed since then" the refusal would then blame. So nothing is
    committed first — `git revert` wants no clean tree, only `git pull` does.

    Uncommitted edits to a file the merge touched are the common case here, because the person is
    sitting in a Builder looking at it. Git refuses over them, which is the honest answer, and the
    rollback is `revert --abort` rather than a hard reset, so the edits are still there."""
    it = _merged(tmp_path)
    open_edit = it.root / it.rel
    open_edit.write_text("half-finished, not committed\n")
    head_before = _git(it.root, "rev-parse", "HEAD").strip()

    result = it.orch.undo_merge()

    assert result["ok"] is False
    assert "Nothing here has changed" in result["detail"]
    assert open_edit.read_text() == "half-finished, not committed\n"   # not eaten
    assert _git(it.root, "rev-parse", "HEAD").strip() == head_before    # and not committed
    # And it does not blame somebody else for what the person is holding in their own editor.
    assert "committed since" not in result["detail"]


def test_the_offer_survives_a_build_landing_on_top(tmp_path: Path):
    """`HEAD` IS NOT THE TEST, and this is the test whose name says so. `pullAndBuild` builds the
    moment the merge lands and a build ends in `commit_all`, so HEAD stops being the merge commit
    inside the same turn.

    Measured, because the first version of this docstring claimed the condition rested here alone
    and that was false: narrowing the walk-back to HEAD reds four tests in this file — this one,
    `test_two_merges_are_undone_newest_first`, `..._a_build_prompt_that_reads_like_an_undo...`, and
    `..._a_revert_that_will_not_apply_refuses...`. Over-covered, not under-covered. Delete any of
    those and this one still holds the line."""
    it = _merged(tmp_path)
    before = _row(it.orch, it.app_id)["resolvedMerge"]
    merge_head = _git(it.root, "rev-parse", "HEAD").strip()

    it.oc.turns.append(Turn(text="done", writes={"src/chart.tsx": "chart\n"}))
    list(it.orch.build_stream("add a chart", skip_incoming_gate=True))

    assert _git(it.root, "rev-parse", "HEAD").strip() != merge_head
    after = _row(it.orch, it.app_id)["resolvedMerge"]
    assert after is not None and after["sha"] == before["sha"]


def test_the_offer_is_rebuilt_from_git_rather_than_remembered(tmp_path: Path):
    """The restart case, which is the one that matters: `_save_to_git` merges on the way down from a
    SIGTERM, so the first person to see that merge sees it in a fresh process. A live card would
    retire exactly where it is needed. Nothing is carried over here but the directory."""
    it = _merged(tmp_path)

    restarted, _oc = _orch(tmp_path, it.root, [])
    _project(restarted)

    offer = _row(restarted, it.app_id)["resolvedMerge"]
    assert offer is not None and offer["files"] == [it.rel]


# --- undoing it ----------------------------------------------------------------------------------

def test_the_undo_reverts_and_leaves_the_merge_reachable(tmp_path: Path):
    """A revert, never a reset. `sync()` pushes straight after merging, so the merge may already be
    on the remote — rewriting history there would take it out from under a clone somebody else is
    working in. A reset passes "the code is back" and fails this."""
    it = _merged(tmp_path)
    sha = _row(it.orch, it.app_id)["resolvedMerge"]["sha"]

    result = it.orch.undo_merge()

    assert result["ok"] is True and result["sha"] == sha
    assert (it.root / it.rel).read_text() == OURS            # the pre-merge state is back
    assert _git(it.root, "cat-file", "-t", sha).strip() == "commit"
    assert sha in _git(it.root, "rev-list", "HEAD")          # reachable, not rewritten away
    assert result["pushed"] is True
    assert _row(it.orch, it.app_id)["resolvedMerge"] is None  # and the offer goes with it


def test_a_reading_taken_before_the_undo_cannot_land_after_it(tmp_path: Path):
    """The rail's check runs on a daemon thread and holds a network fetch open for seconds. An undo
    finishes in well under one, so without an order between them the older reading lands last and
    puts the button back for a merge that is already gone — and pressing it says there is nothing
    to undo. The undo is not slow enough to lose this race by accident, which is why it is pinned.

    Interleaved rather than threaded: the undo runs from inside the older check's read, which is
    exactly what that window allows and is the same assertion without a timing guess."""
    it = _merged(tmp_path)
    project = it.orch.project(start_preview=False)
    it.orch._check_remote(project)
    assert it.orch._resolved_merge_now() is not None

    real = git.resolved_merge

    def reads_then_waits(path, *a, **k):
        found = real(path, *a, **k)
        git.resolved_merge = real          # only this one reading is slow
        it.orch.undo_merge()               # lands, and retires the offer, while it is still out
        return found

    git.resolved_merge = reads_then_waits
    try:
        it.orch._check_remote(project)
    finally:
        git.resolved_merge = real

    assert it.orch._resolved_merge_now() is None


def test_an_undone_merge_is_not_offered_again_after_a_restart(tmp_path: Path):
    """"Already undone" is read out of git like everything else here — the revert's own subject
    names the merge it took back, so a second merge later does not make the first one ambiguous."""
    it = _merged(tmp_path)
    it.orch.undo_merge()

    restarted, _oc = _orch(tmp_path, it.root, [])
    _project(restarted)

    assert _row(restarted, it.app_id)["resolvedMerge"] is None


def test_a_build_prompt_that_reads_like_an_undo_does_not_retire_the_offer(tmp_path: Path):
    """`_save_to_git` builds `build: <prompt>` out of what a person typed, so a prompt can spell
    either marker. The merge subject is closed by requiring two parents; a revert has one, so the
    undo subject is closed by the sha's shape instead. Without that, typing this sentence would put
    the offer out and nothing would say why."""
    it = _merged(tmp_path)
    sha = _row(it.orch, it.app_id)["resolvedMerge"]["sha"]

    (it.root / "notes.md").write_text("notes\n")
    _git(it.root, "add", "-A")
    _git(it.root, "commit", "-q", "-m", f"build: undo merge {sha}, or so the prompt said")

    assert _row(it.orch, it.app_id)["resolvedMerge"]["sha"] == sha


def test_two_merges_are_undone_newest_first(tmp_path: Path):
    """Which one an undo means, when there are two. `git revert`'s own subject is
    `Revert "build: merge remote changes"` — which names no merge at all and stops telling two
    apart the moment there are two, so the undo writes its own subject naming the sha. The rule is
    the newest un-undone merge, so undoing twice walks back through both in order."""
    it = _merged(tmp_path)
    newer_rel = f"apps/{it.app_id}/src/Chart.tsx"
    _git(it.mate, "pull", "-q")
    _mate_edits(it.mate, newer_rel, "their chart\n")
    (it.root / newer_rel).write_text("our chart\n")
    _git(it.root, "add", "-A")
    _git(it.root, "commit", "-q", "-m", "ours again")
    it.oc.turns.append(Turn(text="resolved", writes={newer_rel: RESOLVED}))
    assert it.orch.sync()["status"] == "merged"

    second = _row(it.orch, it.app_id)["resolvedMerge"]
    assert second["files"] == [newer_rel]          # the newer one, not the first

    assert it.orch.undo_merge()["ok"] is True
    first = _row(it.orch, it.app_id)["resolvedMerge"]
    # The older merge is still un-undone, so it is what the offer means now. Undoing the newer one
    # did not retire it, and nothing had to be written down to know which was which.
    assert first is not None and first["files"] == [it.rel] and first["sha"] != second["sha"]


def test_a_revert_that_will_not_apply_refuses_and_calls_no_model(tmp_path: Path):
    """Rule five, asserted on the CALL rather than on the sentence. Handing a conflicting revert to
    the model is #233 rebuilt inside its own fix, and a build that does it still produces a
    plausible-looking message."""
    it = _merged(tmp_path)
    sha = _row(it.orch, it.app_id)["resolvedMerge"]["sha"]
    # A later build rewrites the very lines the merge settled, so `git revert -m 1` conflicts.
    (it.root / it.rel).write_text("rewritten by a later build\n")
    _git(it.root, "add", "-A")
    _git(it.root, "commit", "-q", "-m", "build: rework the chart")
    calls = len(it.oc.prompts)

    result = it.orch.undo_merge()

    assert result["ok"] is False
    assert len(it.oc.prompts) == calls                     # nothing was handed to a model
    assert sha in result["detail"]                         # it names the merge, for a terminal
    assert (it.root / it.rel).read_text() == "rewritten by a later build\n"   # nothing changed
    assert _git(it.root, "status", "--porcelain") == ""    # nothing left half-applied
    # The offer stands: a refusal is not an undo, and the merge is still the one nobody read.
    assert _row(it.orch, it.app_id)["resolvedMerge"]["sha"] == sha


def test_there_is_nothing_to_undo_before_a_merge_and_after_one_is_undone(tmp_path: Path):
    it = _conflicted(tmp_path)

    assert _row(it.orch, it.app_id)["resolvedMerge"] is None
    assert it.orch.undo_merge()["ok"] is False

    it.orch.sync()
    assert it.orch.undo_merge()["ok"] is True
    # Derived, so a second press has nothing to act on rather than acting twice.
    assert it.orch.undo_merge()["ok"] is False


# --- what is said about it -----------------------------------------------------------------------

def test_no_sentence_on_this_path_names_a_person_kind(tmp_path: Path):
    """Sage cannot know that whoever pushed is a Collaborator — push access to the repo and being on
    the Project are different grants — so no label here names a person-kind (CONTEXT.md). The
    model's prompt is included, because a model repeats what it is told."""
    it = _conflicted(tmp_path)

    merged = it.orch.sync()
    undone = it.orch.undo_merge()
    said = [merged["detail"], merged["pushDetail"], undone["detail"], undone["pushDetail"],
            it.oc.prompts[-1]["text"]]

    for sentence in said:
        low = sentence.lower()
        for banned in ("teammate", "colleague", "their changes", "somebody else"):
            assert banned not in low, sentence
    assert "incoming changes" in merged["detail"].lower()
