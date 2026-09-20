"""Git persistence for the bound workspace.

After a clean build the orchestrator commits + pushes the workspace so the app code AND the
`.sage/` transcript become durable (git-based Domino compute is ephemeral — only committed files
survive a restart). Push relies on Domino's pre-authorized credential helper, so no token handling
lives here; auto-*creating* the remote for brand-new apps is a separate concern (Phase 4).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SaveResult:
    """rejected is True only when a push was attempted and git refused it (e.g. non-fast-forward).
    Distinct from pushed=False for "no remote" or "nothing to commit", which are not failures."""
    pushed: bool
    detail: str
    rejected: bool = False


@dataclass
class SyncResult:
    """Outcome of pulling the remote into the workspace.

    status is one of: "up-to-date" (nothing to pull), "merged" (remote changes integrated),
    "conflict" (merge left markers in `conflicts` for the caller to resolve), "conflict-unresolved"
    (resolution failed and the merge was rolled back), "no-remote", or "error"."""
    status: str
    conflicts: list[str]
    detail: str


@dataclass
class Incoming:
    """What the remote has that this workspace does not, as of the last fetch.

    `head` is the remote commit those changes end at, and "" when there is nothing to pull. It is
    the thing a caller remembers when somebody chooses to build anyway (#78), so the same decision
    isn't asked for again until the remote moves on."""
    head: str
    files: list[str]


@dataclass
class ResolvedMerge:
    """A merge whose conflicts a model resolved, with nobody having read the result (#233).

    `sha` is abbreviated and `files` are the paths the model rewrote. Both are read back out of the
    commit — there is no second record anywhere (ADR-0053 rule four), because `sha^1` IS the
    pre-merge state, permanently and in every clone, and a copy under `.sage/` could only disagree
    with git while being the one that does not survive a clone."""
    sha: str
    files: list[str]


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(path), capture_output=True, text=True, check=check
    )


def is_repo_root(path: Path) -> bool:
    """True only when `path` is the ROOT of its own repo — not merely somewhere inside one.

    The distinction is the whole bug in #20. Locally the workspace sits at `backend/workspaces/app`,
    inside Sage's own source tree and gitignored, so it is not its own repo. The old check asked
    `--is-inside-work-tree`, which walks up until it finds a repo and answers `true` — and a save
    from a subdirectory stages the whole enclosing tree, so stopping the local orchestrator committed
    and pushed Sage's uncommitted source to `origin/main`. `--show-toplevel` names the repo it found,
    which lets the caller notice the answer came from somewhere above it.

    Paths are resolved on both sides: on macOS a `/tmp` workspace reports `/private/tmp`, and a
    string compare would call a real repo root not-a-root.
    """
    p = Path(path)
    if not p.is_dir():
        return False
    r = _git(p, "rev-parse", "--show-toplevel", check=False)
    if r.returncode != 0:
        return False
    return Path(r.stdout.strip()).resolve() == p.resolve()


def has_remote(path: Path) -> bool:
    r = _git(path, "remote", check=False)
    return r.returncode == 0 and bool(r.stdout.strip())


def push_url(path: Path) -> str:
    """Where a save from this Project would send its commits, or "" when that cannot be read.

    Sage's knowledge of its own destination used to be `has_remote` above — a boolean — which is
    enough to decide whether to push and nothing like enough to ask somebody's permission to push
    rows (ADR-0045). The name of the host is the only part of the audience Sage can read, because
    a Domino Project pushes through whatever git credential is present and that can be GitHub,
    GitLab or an enterprise install.

    Rooted on `is_repo_root` for the reason #20 records: locally the workspace sits INSIDE Sage's
    own source tree, and a bare `remote get-url` there walks up and answers with Sage's repository.
    The dialog would then name a destination this Project has never pushed to in its life, which is
    worse than naming none. `--push` because that is the URL a push actually uses when a `pushurl`
    is configured.
    """
    if not is_repo_root(path):
        return ""
    r = _git(path, "remote", "get-url", "--push", "origin", check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def destination_name(url: str) -> str:
    """A remote written the way a person can weigh it: `github.com/acme/analytics`.

    The credential comes out first, and that is why this is here rather than in the browser: the
    helper can leave a token in the URL, and this string goes on a screen and into screenshots.
    One place to get it right.

    A scheme, a port and a trailing `.git` are dropped because none of them is part of the
    judgement being asked for, and the two spellings of one destination — HTTPS and SSH — have to
    read the same or the same question looks like two. A remote that is a filesystem path has no
    host to name, so the path is the whole of the honest answer.
    """
    text = (url or "").strip()
    if not text:
        return ""
    _, scheme, after = text.partition("://")
    if scheme:
        rest = after
    else:
        host, colon, repo = text.partition(":")
        if not colon or "/" in host:
            return _without_dot_git(text)
        rest = f"{host}/{repo.lstrip('/')}"
    authority, slash, repo = rest.partition("/")
    authority = authority.rpartition("@")[2].split(":", 1)[0]
    return _without_dot_git(authority + slash + repo)


def _without_dot_git(text: str) -> str:
    trimmed = text.rstrip("/")
    return trimmed.removesuffix(".git")


def _identity_args(path: Path) -> list[str]:
    """Use the repo's configured identity (the platform sets it) when present; otherwise fall back to
    a neutral one so an unconfigured environment still commits cleanly rather than erroring.

    De-branded once, not per-pack: this is the author line of every save in a repo the partner's own
    customer can read, and git history is immutable, so a name written here can never be re-branded
    later without falsifying a record already committed. ADR-0014's third arm, the same call as
    `build: ` one field over. `agent` because that is what wrote the commit, and the address claims
    no domain at all.
    """
    args: list[str] = []
    if not _git(path, "config", "user.email", check=False).stdout.strip():
        args += ["-c", "user.email=agent@localhost"]
    if not _git(path, "config", "user.name", check=False).stdout.strip():
        args += ["-c", "user.name=agent"]
    return args


def commit_all(path: Path, message: str, exclude: list[str] | None = None) -> bool:
    """Stage everything and commit. Returns False (not an error) when there's nothing to commit.
    `exclude` unstages the given workspace-relative paths after staging, so bytes that must never be
    committed (attached-data copies leaked into src/) are kept out of the commit — they stay on disk,
    just untracked."""
    _git(path, "add", "-A")
    if exclude:
        _git(path, "reset", "-q", "--", *exclude, check=False)
    if _git(path, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    _git(path, *_identity_args(path), "commit", "-m", message)
    return True


def untrack(path: Path, rel: str) -> bool:
    """Drop `rel` from the index, leaving it on disk. True if git was tracking it.

    An ignore rule does nothing to a file git already knows about, so a workspace that committed
    one before it became generated state needs this once. A no-op every turn after that."""
    if _git(path, "ls-files", "--error-unmatch", "--", rel, check=False).returncode != 0:
        return False
    _git(path, "rm", "--cached", "-q", "--", rel)
    return True


def tracked_under(path: Path, prefix: str) -> list[str]:
    """Every path git tracks under `prefix`, repo-relative. Empty when it tracks none.

    The companion `untrack` needs the names one at a time, and the caller that has them is asking
    about a rule rather than a file — "what is in the index that this ignore line now covers?".
    `-z`, because a filename is allowed to contain a newline and a chart's is derived from a
    question somebody typed."""
    r = _git(path, "ls-files", "-z", "--", prefix, check=False)
    if r.returncode != 0:
        return []
    return [p for p in r.stdout.split("\0") if p]


def push(path: Path) -> SaveResult:
    """Push HEAD. Returns pushed=False (not an error) when there's no remote or the push is
    rejected (e.g. a non-fast-forward — the caller should pull first)."""
    if not has_remote(path):
        return SaveResult(pushed=False, detail="committed (no remote)")
    # A branch with no upstream is refused outright by a bare `git push` — `fatal: the current
    # branch X has no upstream branch`, exit 128, which reads here as `rejected` and is the one
    # refusal a retry genuinely could fix. `unsent()` calls that branch the worst case rather than
    # a quiet one (#459), so the save path now drives a push at it; setting the upstream is what
    # makes that answer true instead of a permanent refusal. Only when there is none: a branch that
    # already tracks something keeps `push.default` and its own upstream.
    tracked = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    args = ["push"] if tracked.returncode == 0 else ["push", "-u", "origin", "HEAD"]
    r = _git(path, *args, check=False)
    if r.returncode != 0:
        detail = f"push failed: {(r.stderr or r.stdout).strip()[:200]}"
        return SaveResult(pushed=False, detail=detail, rejected=True)
    return SaveResult(pushed=True, detail="pushed")


def commit_and_push(path: Path, message: str) -> SaveResult:
    """Stage everything, commit, and push. Returns pushed=False (not an error) when there's nothing
    to commit or no remote; raises only on an unexpected git failure (the caller treats that as a
    non-fatal saved:ok=false)."""
    if not commit_all(path, message):
        return SaveResult(pushed=False, detail="no changes to commit")
    return push(path)


def current_branch(path: Path) -> str:
    return _git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip() or "main"


def pull(path: Path) -> SyncResult:
    """Fetch the remote and merge the current branch's upstream into the working tree. On conflict
    the tree is left with markers and SyncResult.conflicts lists the files, for the caller (the
    agent) to resolve and then finalize_merge(). Never pushes. Assumes a clean tree (commit first)."""
    if not has_remote(path):
        return SyncResult("no-remote", [], "no remote to pull from")
    fetch = _git(path, "fetch", "origin", check=False)
    if fetch.returncode != 0:
        raise RuntimeError(f"git fetch failed: {(fetch.stderr or fetch.stdout).strip()[:200]}")
    ref = f"origin/{current_branch(path)}"
    # No upstream branch yet (nothing pushed) -> nothing to pull.
    if _git(path, "rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
        return SyncResult("up-to-date", [], "no upstream branch")
    merge = _git(path, *_identity_args(path), "merge", "--no-edit", ref, check=False)
    if merge.returncode == 0:
        if "up to date" in merge.stdout.lower():
            return SyncResult("up-to-date", [], "already up to date")
        return SyncResult("merged", [], merge.stdout.strip()[:200] or "merged remote changes")
    conflicts = unresolved_conflicts(path)
    if conflicts:
        return SyncResult("conflict", conflicts, "merge conflicts need resolution")
    # A non-conflict merge failure (e.g. local changes would be overwritten) — roll back and raise.
    _git(path, "merge", "--abort", check=False)
    raise RuntimeError(f"git merge failed: {(merge.stderr or merge.stdout).strip()[:200]}")


def unresolved_conflicts(path: Path) -> list[str]:
    """Files git considers unmerged (conflicted) in the index."""
    r = _git(path, "diff", "--name-only", "--diff-filter=U", check=False)
    return [f for f in r.stdout.splitlines() if f.strip()]


_CONFLICT_MARKERS = ("<<<<<<< ", ">>>>>>> ")


def files_with_conflict_markers(path: Path, files: list[str]) -> list[str]:
    """Of `files`, those that still contain conflict markers — used to verify the agent actually
    resolved them (the index stays "unmerged" until `git add`, so diff-filter=U can't confirm this)."""
    out: list[str] = []
    for f in files:
        try:
            text = (Path(path) / f).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if any(m in text for m in _CONFLICT_MARKERS):
            out.append(f)
    return out


def finalize_merge(path: Path, message: str) -> None:
    """Stage the resolved files and commit the in-progress merge."""
    _git(path, "add", "-A")
    _git(path, *_identity_args(path), "commit", "--no-edit", "-m", message)


def abort_merge(path: Path) -> None:
    """Roll back an in-progress merge, restoring the pre-pull state."""
    _git(path, "merge", "--abort", check=False)


# ---- the three strings the undo offer is derived from (#233, ADR-0053) ---------------------------
#
# LOAD-BEARING. These are not descriptions of what happened; they are the record. `resolved_merge`
# finds a merge by MERGE_SUBJECT plus two parents, reads what was rewritten out of the
# `_RESOLVED_IN` line, and calls it already taken back when it finds `_UNDO_PREFIX` + that sha
# nearer HEAD. Change any of them and the offer silently stops appearing for merges made by the old
# spelling. `test_a_resolved_merge_can_be_undone.py` pins the format for that reason.
#
# The subject is what tells a merge a model resolved from one git completed by itself: `pull()`
# merges with `git merge --no-edit` and git writes its own subject, and `finalize_merge` has exactly
# one caller. Two parents are required as well as the subject, because `_save_to_git` builds
# `f"build: {prompt}"` out of what a person typed and somebody could type this.
MERGE_SUBJECT = "build: merge remote changes"
# A header line and then one path per line, rather than one comma-joined line. A path may contain a
# comma — this repo's own charts are named from something somebody typed — and splitting on one
# would put two invented filenames in front of a person deciding whether to undo. Git quotes a path
# containing a newline, so one path per line is a partition and not a guess.
_RESOLVED_IN = "Resolved conflicts in:"
_UNDO_PREFIX = "build: undo merge "
# How far back the walk looks. A cap rather than a claim: offering to undo a merge from fifty builds
# ago is almost certainly wrong, and a revert that no longer applies refuses rather than failing
# badly. `HEAD` itself is NOT the test — a build commits on top inside the same turn.
_UNDO_WINDOW = 50
# Fixed rather than git's own abbreviation, which grows with the repo: the length is part of the
# marker that `_UNDO_PREFIX` searches for, so an undo written today has to still match a merge read
# back next year.
_SHA_LEN = 12


def merge_message(files: list[str]) -> str:
    """The commit message for a merge the model resolved: the marker subject, and under it the files
    it rewrote.

    The body is the only place that list can live. Git records which files DIFFER, not which ones
    conflicted (`show --cc` answers a wider question), and the conflict list is a local variable that
    dies with the process — which is exactly the case the offer has to survive, since `_save_to_git`
    merges on the way down from a SIGTERM."""
    named = "\n".join(f for f in files if f.strip())
    return f"{MERGE_SUBJECT}\n\n{_RESOLVED_IN}\n{named}" if named else MERGE_SUBJECT


def _short(sha: str) -> str:
    return sha[:_SHA_LEN]


def resolved_merge(path: Path, window: int = _UNDO_WINDOW) -> ResolvedMerge | None:
    """The newest merge a model resolved that has not been undone, or None.

    Walks newest-first, so an undo is always met before the merge it took back and the two need no
    ordering of their own. An undo is nearer HEAD than its merge by construction, so a merge inside
    the window has its undo inside it too."""
    r = _git(path, "log", f"-n{window}", "--format=%H%x1f%P%x1f%B%x1e", "HEAD", check=False)
    if r.returncode != 0:
        return None
    undone: set[str] = set()
    for record in r.stdout.split("\x1e"):
        lines = record.strip("\n").split("\x1f")
        if len(lines) != 3:
            continue
        sha, parents, body = lines
        subject = body.splitlines()[0] if body.strip() else ""
        if subject.startswith(_UNDO_PREFIX):
            # Shape-checked for the reason the merge subject is parent-checked: `_save_to_git`
            # builds `build: <prompt>` out of what a person typed, and a revert has one parent so
            # there is no count to lean on here. A subject that does not end in an abbreviated sha
            # was not written by `undo_merge`, and taking it for one would silently suppress a real
            # offer.
            named = subject[len(_UNDO_PREFIX):].strip()
            if len(named) == _SHA_LEN and all(c in "0123456789abcdef" for c in named):
                undone.add(named)
        elif subject == MERGE_SUBJECT and len(parents.split()) == 2 and _short(sha) not in undone:
            return ResolvedMerge(sha=_short(sha), files=_resolved_files(body))
    return None


def _resolved_files(body: str) -> list[str]:
    lines = body.splitlines()
    for at, line in enumerate(lines):
        if line.strip() == _RESOLVED_IN:
            return [f.strip() for f in lines[at + 1:] if f.strip()]
    return []


def undo_merge(path: Path, sha: str) -> bool:
    """Revert the merge `sha` and commit it under a subject naming what it undid.

    Returns False, with the tree as it found it, when the revert does not apply — which is what a
    build landing on top makes likely. That refusal is the end of it: handing the conflict to a model
    would rebuild #233 inside its own fix (ADR-0053 rule five).

    A revert and never a reset, because `sync()` pushes straight after merging: by the time anybody
    reads the offer the merge may already be on the remote, where rewriting history would take it out
    from under a clone somebody else is working in.

    `revert --abort` and never `reset --hard` for the rollback, for the same reason one level down.
    The caller commits first, but `commit_all`'s `exclude` deliberately leaves tracked files
    MODIFIED in the tree (the attached-data copies that must never be committed), and those are
    exactly what a dirty-tree revert refuses over. A hard reset would answer "this could not be
    applied" by destroying the work that stopped it. `--abort` restores the pre-revert state and
    leaves untouched anything the revert never reached — measured, not assumed.

    The commit is checked like every other call here rather than raising: it comes after the revert
    is already in the tree, so a failure that escaped would leave the undo applied, unrecorded and
    still being offered, for the next unrelated build to sweep into its own commit."""
    r = _git(path, "revert", "-m", "1", "--no-commit", sha, check=False)
    if r.returncode != 0:
        _git(path, "revert", "--abort", check=False)
        return False
    if _git(path, "diff", "--cached", "--quiet", check=False).returncode == 0:
        # Nothing to take back — the merge's changes are already gone from the tree. Reported as a
        # refusal rather than as an undo, because no `undo merge` commit was written and the offer
        # has to keep matching what git says.
        _git(path, "revert", "--quit", check=False)
        return False
    done = _git(path, *_identity_args(path), "commit", "-m", f"{_UNDO_PREFIX}{_short(sha)}",
                check=False)
    if done.returncode != 0:
        _git(path, "revert", "--abort", check=False)
        return False
    return True


def fetch(path: Path) -> bool:
    """Refresh the remote-tracking refs. Best-effort by design, unlike `pull`'s fetch: this one runs
    at the top of a turn and on a timer, and an unreachable remote has to leave both exactly as an
    up-to-date one would rather than stopping the turn with a network error."""
    if not has_remote(path):
        return False
    return _git(path, "fetch", "origin", check=False).returncode == 0


def incoming(path: Path) -> Incoming:
    """Commits on the remote branch this workspace hasn't merged, and the files they change.

    Reads the refs `fetch` left behind and never touches the network itself, so the answer is a
    local read once someone has paid for the fetch. Local commits are not incoming: `HEAD...ref`
    diffs from the merge base, so a workspace that is merely ahead reads as nothing to pull."""
    if not has_remote(path):
        return Incoming("", [])
    ref = f"origin/{current_branch(path)}"
    if _git(path, "rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
        return Incoming("", [])
    # Counted, not diffed, to decide: a commit that changes nothing back is still a commit the
    # local branch has to merge before it can push.
    behind = _git(path, "rev-list", "--count", f"HEAD..{ref}", check=False).stdout.strip()
    if not behind or behind == "0":
        return Incoming("", [])
    changed = _git(path, "diff", "--name-only", f"HEAD...{ref}", check=False)
    files = [f for f in changed.stdout.splitlines() if f.strip()]
    return Incoming(_git(path, "rev-parse", ref, check=False).stdout.strip(), files)


def unsent(path: Path) -> bool:
    """Whether this branch holds commits the remote has never been given.

    The mirror of `incoming()` and built the same way — a `rev-list --count` over refs that a push
    or a fetch already left behind, and **no network call of its own**. That is sound because the
    refs record what the remote actually took: a refused push does not advance `origin/<branch>`,
    and a successful one does.

    Where it stops being a mirror is a missing `origin/<branch>`. `incoming()` reads that as
    nothing to pull, which is right for it; here it means nothing has EVER been sent, which is the
    worst case rather than a quiet one, so it answers True. A repo with no commits at all is the
    other side of the same missing ref and answers False — there is no HEAD to send, and a `git
    push` on an empty repo fails in a way the caller would read as a refusal.

    False without a remote, and false outside a repo root: neither is saved through a push, and a
    workspace that is not the root of its own repo is not saved through git at all (#20).
    """
    if not is_repo_root(path) or not has_remote(path):
        return False
    if _git(path, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
        return False
    branch = current_branch(path)
    if branch == "HEAD":
        # Detached. `current_branch`'s `or "main"` does not catch this — `rev-parse --abbrev-ref`
        # succeeds and prints the literal "HEAD" — and `origin/HEAD` RESOLVES in a clone, which
        # every Domino workspace is, so the naive read counts against the remote's default branch.
        # There is no branch to send and `git push` refuses a detached head anyway.
        return False
    ref = f"origin/{branch}"
    if _git(path, "rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
        return True
    counted = _git(path, "rev-list", "--count", f"{ref}..HEAD", check=False)
    if counted.returncode != 0:
        # Follows the missing-ref branch, not the zero one. A count that could not be read is no
        # evidence that the remote has everything, and "the remote has everything" is the single
        # answer this reader must never guess — it is the one that strands the work.
        return True
    ahead = counted.stdout.strip()
    return bool(ahead) and ahead != "0"
