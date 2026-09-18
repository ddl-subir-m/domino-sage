"""A write that truncates its destination is readable, briefly, as a file that is not there yet.

`workspace/manager.py` pairs that window with readers which turn any fault into a plausible value:
`read_attachments` answers `[]`, `read_session_id` answers `None`, `_read_settings_file` answers
`{}`. None of those are distinguishable from a legitimate answer, so a millisecond of timing stops
being a crash and becomes the product stating something false about the person's data in its own
voice. #289 hit exactly this on `model_overrides.json` and its sentence for it is the one this
file exists to enforce: *making a loud failure quiet is what turned a harmless window into a
plausible lie.*

The fix is upstream of every reader — remove the window rather than widen a catch — so the rule
this file pins is about WRITES, and it is stated on the axis that creates the window rather than on
the file's format:

    A write that replaces a file's whole contents goes through `_write_atomic`.
    A write that APPENDS does not, because it never removes content that is already there.

Format is the wrong axis and JSON-only was the wrong count. `truncate_history` and `adopt_history`
rewrite `history.jsonl` WHOLE — an append-shaped log is not an append — while `plan.md` and
`architecture.md` are markdown and tear exactly the same way, into a `""` that reads as "no plan".
Keying on `.json` would have missed four sites and caught two that were never at risk.

The census below is therefore a ZERO, not a number: no truncating write may sit outside the helper.
A number would have to be maintained, and a write added next year would join a stale count in
silence. A zero reds on the write itself. Exemptions are named rather than counted, for the same
reason every line number in #308's body went stale the moment #303 landed.

WHAT THE CENSUS CAN AND CANNOT SEE, stated because a promise wider than the check reads as covered
and is worse than no promise. It is a source survey, so it recognises SPELLINGS, not behaviour:
`write_text`, `write_bytes`, the `shutil` copy/move family, and `open`/`Path.open` whose mode is a
literal. A `Path.open(mode)` whose mode is computed is reported rather than skipped, because a
non-constant mode is precisely where a truncating write would hide from a survey that reads only
literals. What it still cannot reach is a handle opened somewhere else and written here, a raw
`os.write` on a descriptor, or a write inside a called library. Those are not covered by anything
in this file, and the first version of it could not see `write_bytes` either — which is how it
reported a clean zero over the three `.gitignore` writers, the paths in the module where a torn
read costs the most.
"""
from __future__ import annotations

import ast
import functools
import json
import os
import shutil
import stat
import subprocess
import threading
from pathlib import Path

import pytest

from sage.workspace import manager

# --------------------------------------------------------------------------------------
# The census: the rule and the count, pinned together.
# --------------------------------------------------------------------------------------

def _enclosing_function(tree: ast.AST, target: ast.AST) -> str:
    """The nearest `def` above `target`, or "<module>".

    Reported instead of a line number on purpose. #308 was filed against lines 207/285/550/624/684
    and every one of them was wrong by the time anybody read it, because #303 landed in between.
    A function name survives the edit that moves it.
    """
    best = "<module>"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno <= target.lineno <= (node.end_lineno or node.lineno):
            # Innermost wins: a nested def is a better answer than the method holding it.
            best = node.name
    return best


# `shutil` calls that land bytes at a destination. `copyfile`/`copy`/`copy2`/`copytree` all truncate
# or create; `move` can be a rename (atomic) or a copy+unlink (not), and which one it is depends on
# whether the two paths share a filesystem — a runtime fact, so it is surveyed rather than assumed.
_SHUTIL_WRITES = frozenset({"copy", "copy2", "copyfile", "copytree", "move", "copyfileobj"})


def _is_shutil(func: ast.Attribute) -> bool:
    return isinstance(func.value, ast.Name) and func.value.id == "shutil"


def _has_const_mode(call: ast.Call) -> bool:
    """True when this `Path.open(...)` names its mode as a literal the survey can read."""
    if call.args:
        return isinstance(call.args[0], ast.Constant)
    return any(kw.arg == "mode" and isinstance(kw.value, ast.Constant) for kw in call.keywords)


def _mode_of(call: ast.Call, *, builtin: bool) -> str:
    """The mode string an `open` call was given, or "" when it took the default (read)."""
    pos = 1 if builtin else 0
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
        return str(call.args[pos].value)
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return ""


def _writes(*, inside_helper: bool) -> list[tuple[str, str]]:
    """Every call in `manager.py` that opens a file for writing, as (function, what).

    `inside_helper=False` excludes `_write_atomic`'s own subtree — that one call is the staged
    write the rule exists to funnel everything into, and counting it would make the census
    self-defeating.
    """
    tree = ast.parse(Path(manager.__file__).read_bytes())
    helper = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_write_atomic"),
        None,
    )
    assert helper is not None, (
        "`_write_atomic` is gone from workspace/manager.py. The census below cannot mean anything "
        "without it, so this fails here rather than reporting a suspiciously clean zero."
    )
    helper_nodes = {id(n) for n in ast.walk(helper)}

    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (id(node) in helper_nodes) is not inside_helper:
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("write_text", "write_bytes"):
            found.append((_enclosing_function(tree, node), func.attr))
        elif isinstance(func, ast.Attribute) and _is_shutil(func) and func.attr in _SHUTIL_WRITES:
            found.append((_enclosing_function(tree, node), f"shutil.{func.attr}"))
        elif isinstance(func, ast.Attribute) and func.attr == "open" and not node.args \
                and not any(kw.arg == "mode" for kw in node.keywords):
            pass  # `Path.open()` with no mode is a read
        elif isinstance(func, ast.Attribute) and func.attr == "open" and not _has_const_mode(node):
            # A mode this test cannot evaluate. Reported rather than skipped: a non-constant mode is
            # exactly where a truncating write would hide from a census that only reads literals.
            found.append((_enclosing_function(tree, node), "Path.open(<non-constant mode>)"))
        elif isinstance(func, ast.Attribute) and func.attr == "open":
            mode = _mode_of(node, builtin=False)
            if any(c in mode for c in "wax+"):
                found.append((_enclosing_function(tree, node), f"Path.open({mode!r})"))
        elif isinstance(func, ast.Name) and func.id == "open":
            mode = _mode_of(node, builtin=True)
            if any(c in mode for c in "wax+"):
                found.append((_enclosing_function(tree, node), f"open({mode!r})"))
    return found


# Writes the census sees and does not require to be atomic. Each is here for a reason about the
# mechanism, and the reason is what a new entry has to earn — not the inconvenience of the red.
_EXEMPT = {
    # An append cannot remove bytes that are already on disk, so no reader can observe the log
    # missing an entry it previously had. A partial final line is the only artefact, and
    # `_iter_history` already drops an unparseable line rather than failing the read.
    ("append_history", "Path.open('a')"),
    # `copy2` is what preserves the +x bit Domino needs to run `app.sh`, and `_write_atomic` would
    # drop it: the helper carries the mode from the file being REPLACED, and a seed has none to
    # carry from, so a fresh file would take whatever the umask says. Converting this trades a
    # window nobody has hit for an app that cannot start. `_seed_file` takes the same branch for
    # every template file that is not voiced; the one it DOES voice goes through the helper, and
    # that file is `AGENTS.md`, which nothing executes.
    ("_seed_file", "shutil.copy2"),
    ("refresh_entry_script", "shutil.copy2"),
    # `copytree` materialises the template into a directory that does not exist yet — `ensure`
    # seeds it, `reset` re-seeds it after removing everything but `_RESET_KEEP`. There is no
    # previous content for a reader to lose, the unit is a tree rather than a file, and it carries
    # the same mode bits `copy2` does one level down. A per-file staged publish here would be a
    # different operation, not a safer one.
    ("ensure", "shutil.copytree"),
    ("reset", "shutil.copytree"),
}


def test_no_whole_file_write_sits_outside_the_atomic_helper():
    """The census. A zero, so a write added later reds this instead of joining a stale number.

    Twenty-two sites were routed through the helper for #308: nineteen wrote straight at the
    destination, and three already staged through a temp file but open-coded the staging — two of
    those three shared one temp NAME, which is the same torn read arrived at from the other side
    (see `test_two_writers_to_one_path_do_not_share_a_temp_name`).

    `write_bytes` counts, and leaving it out is how the first version of this test passed over three
    live counterexamples. `ensure_ignore_line` and `remove_ignore_line` are read-modify-writes on
    `.gitignore`, which is the path in this module where a torn read costs the most — a reader that
    sees `b""` republishes the file holding only its own rule, dropping the two lines that keep
    credentials and sampled rows out of git. A promise the check does not make is worse than no
    promise, because it reads as covered.
    """
    surveyed = _writes(inside_helper=False)
    truncating = [w for w in surveyed if w not in _EXEMPT]
    assert truncating == [], (
        "These write a file's whole contents without staging through `_write_atomic`, so each "
        "leaves the destination readable-as-empty for the length of the write:\n  "
        + "\n  ".join(f"{fn}: {what}" for fn, what in truncating)
    )


def test_every_exemption_is_still_a_real_write_somebody_chose():
    """The other direction: an exemption that no longer matches anything is a stale licence.

    Without this, renaming or deleting an exempt writer would leave its entry in `_EXEMPT` covering
    a name that could later come back as something else entirely — the list would keep saying yes to
    a question nobody is asking any more.
    """
    surveyed = set(_writes(inside_helper=False))
    stale = _EXEMPT - surveyed
    assert stale == set(), (
        f"These exemptions match no write in workspace/manager.py any more: {sorted(stale)}. "
        f"Remove them rather than leaving a licence lying around."
    )


def test_the_helper_stages_its_write_somewhere_other_than_the_destination(tmp_path: Path,
                                                                         monkeypatch):
    """The inverse of the census: proves the helper is the thing the rule points at.

    Asserted on the PATHS, not on the presence of a write call. Checking only that `_write_atomic`
    contains some write leaves the one rewrite that defeats everything above still green: a body of
    `path.write_text(text)` is a write, and the census excludes the helper's own subtree, so the
    zero would hold over a helper that truncates exactly like the sites it replaced.
    """
    dest = tmp_path / "record.json"
    swaps: list[tuple[str, str]] = []
    real_replace = os.replace

    def watching_replace(src, dst, *a, **k):
        swaps.append((str(src), str(dst)))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(manager.os, "replace", watching_replace)
    manager._write_atomic(dest, '{"n": 1}')

    assert len(swaps) == 1, f"`_write_atomic` did not publish through one rename: {swaps}"
    src, dst = swaps[0]
    assert dst == str(dest), f"published at {dst}, not at {dest}"
    assert src != dst, "the staged file IS the destination, so the write was never staged at all"
    assert dest.read_text() == '{"n": 1}'


def test_the_ignore_rules_cover_every_staging_name_the_helper_can_make(tmp_path: Path):
    """The price of a unique staging name, asked of git rather than reasoned about.

    A fixed `<name>.tmp` overwrites itself, so a writer killed mid-write leaves at most one file. A
    unique name is what stops two writers promoting each other's half-written bytes, and it makes
    those leftovers ACCUMULATE — inside `.sage/`, which is committed. So the ignore rule is part of
    this change rather than tidying after it.

    Asked of `git check-ignore` because the first rule written here was `.sage/**/.*.tmp`, which
    reads as though it covers everything and is short by three: the helper also stages
    `.AGENTS.md.<hex>.tmp` at the volume root and at the app root, neither under `.sage/`. Reasoning
    about a glob is how that was missed, and only running it found it.

    The negative half is load-bearing too. A rule broad enough to catch every staging file is broad
    enough to swallow the records beside them, and a `.sage/settings.json` that silently stops being
    committed is a worse bug than the one this fixes.
    """
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not on PATH")
    # The developer's own git config is kept out of it. `check-ignore` consults `core.excludesFile`
    # and `~/.config/git/ignore`, so a machine with `*.tmp` globally ignored would pass the positive
    # half of this test with `**/.*.tmp` deleted, and a global rule covering `AGENTS.md` would red
    # the negative half for a reason that has nothing to do with `_PROJECT_IGNORE`. Either way the
    # test would be reporting on the machine rather than on the tuple it names.
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    run = functools.partial(subprocess.run, cwd=tmp_path, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run([git, "init", "-q", "."], check=True)
    (tmp_path / ".gitignore").write_text("\n".join(manager._PROJECT_IGNORE) + "\n")

    staging = [
        ".sage/.settings.json.a1b2c3d4.tmp",              # a ProjectRecord sidecar
        ".sage/plan-docs/001/.meta.json.e5f6a7b8.tmp",    # one directory down
        ".sage/threads/t1/.session.json.c9d0e1f2.tmp",    # two down, per conversation
        ".AGENTS.md.a3b4c5d6.tmp",                        # volume root, NOT under .sage/
        "apps/a1/.AGENTS.md.e7f8a9b0.tmp",                # app root, NOT under .sage/
        "apps/a1/.sage/.bindings.json.c1d2e3f4.tmp",      # the app's own committed .sage/
        "apps/a1/.sage/.history.jsonl.a5b6c7d8.tmp",
    ]
    records = [
        ".sage/settings.json",
        ".sage/plan-docs/001/meta.json",
        "apps/a1/.sage/bindings.json",
        "apps/a1/AGENTS.md",
        "AGENTS.md",
    ]
    for rel in staging + records:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()

    def ignored(rel: str) -> bool:
        return run([git, "-c", f"core.excludesFile={os.devnull}", "check-ignore", "-q", rel],
                   check=False).returncode == 0

    missed = [rel for rel in staging if not ignored(rel)]
    assert missed == [], (
        f"`_write_atomic` can leave these behind and git would commit them: {missed}. "
        f"A killed writer drops a fresh one on every attempt."
    )
    swallowed = [rel for rel in records if ignored(rel)]
    assert swallowed == [], (
        f"The staging rule is too broad — it also hides real records: {swallowed}. "
        f"These are the files the Project is supposed to keep."
    )


# --------------------------------------------------------------------------------------
# The window itself, driven by a real torn file rather than a mocked exception.
# --------------------------------------------------------------------------------------

def _big_attachments(n: int = 4000) -> list[dict]:
    """A payload wide enough that the truncate-to-write window is observable.

    Size is the whole point: `write_text` truncates at `open` and fills afterwards, so the window
    is proportional to the payload. A handful of entries closes it faster than a reader in the same
    process can be scheduled into it, and this test would then pass against the very bug it exists
    to catch.
    """
    return [{"path": f"public/data/file-{i:05d}.csv", "name": f"file-{i:05d}.csv",
             "note": "x" * 64} for i in range(n)]


def test_a_reader_never_sees_a_half_written_attachments_file(tmp_path: Path):
    """#308's headline pair, and the one the ticket names first.

    `read_attachments` feeds `_rehydrate_attached`, and therefore `project()` and `select_app`. Its
    catch answers `[]` — "this app has no attachments" — which is a perfectly ordinary thing for an
    app to be, so a torn read here is not a visible fault. It is the product telling somebody whose
    attachments are fine that they have none.

    Driven by a real torn file: a writer rewrites the SAME contents in a loop while a reader spins.
    Seeding the file with those contents first is what makes any other observation a tear rather
    than a race the reader lost — old value and new value are equal, so there is no legitimate
    third answer.
    """
    ws = manager.Workspace("proj_1", tmp_path, "app_1")
    entries = _big_attachments()
    ws.write_attachments(entries)
    assert len(ws.read_attachments()) == len(entries), "the seed write itself did not land"

    torn: list[int] = []
    stop = threading.Event()

    def rewrite() -> None:
        try:
            for _ in range(400):
                ws.write_attachments(entries)
        finally:
            stop.set()

    writer = threading.Thread(target=rewrite, daemon=True)
    writer.start()
    reads = 0
    while not stop.is_set():
        got = ws.read_attachments()
        reads += 1
        if len(got) != len(entries):
            torn.append(len(got))
    writer.join(timeout=30)
    assert not writer.is_alive(), "the writer thread did not finish"

    assert reads > 0, "the reader never ran; this test proved nothing"
    assert torn == [], (
        f"{len(torn)} of {reads} reads saw an attachments list that was never written "
        f"(lengths: {sorted(set(torn))[:5]}). A truncating write leaves the file readable as "
        f"empty, and the reader's catch turns that into 'no attachments'."
    )


def test_a_torn_write_cannot_report_a_plan_as_empty(tmp_path: Path):
    """The same window on markdown, which is why the rule is not keyed on `.json`.

    `read_plan` does not swallow anything — it has no catch at all — and it still cannot tell a
    torn read from a real one: `read_text()` on a truncated file returns `""`, and every caller
    reads `""` as "there is no plan". A reader does not have to be swallowing to be lied to; it
    only has to have a plausible value to land on.
    """
    ws = manager.Workspace("proj_1", tmp_path, "app_1")
    text = "# Plan\n\n" + "\n".join(f"{i}. do the thing" for i in range(20000))
    ws.write_plan(text)

    torn: list[int] = []
    stop = threading.Event()

    def rewrite() -> None:
        try:
            for _ in range(400):
                ws.write_plan(text)
        finally:
            stop.set()

    writer = threading.Thread(target=rewrite, daemon=True)
    writer.start()
    reads = 0
    while not stop.is_set():
        got = ws.read_plan()
        reads += 1
        if got != text:
            torn.append(len(got or ""))
    writer.join(timeout=30)
    assert not writer.is_alive(), "the writer thread did not finish"

    assert reads > 0, "the reader never ran; this test proved nothing"
    assert torn == [], (
        f"{len(torn)} of {reads} reads saw a plan that was never written (lengths: "
        f"{sorted(set(torn))[:5]}). A zero-length one presents as 'no plan'."
    )


# --------------------------------------------------------------------------------------
# The helper's own contract — the three things the open-coded sites disagreed about.
# --------------------------------------------------------------------------------------

def test_two_writers_to_one_path_do_not_share_a_temp_name(tmp_path: Path, monkeypatch):
    """A SHARED temp name is the torn read arrived at from the other side.

    `write_catalog_overrides` says so in its own comment, and until #308 the two sites beside it
    did not follow it: `update_project_resources` and `update_bindings` both staged through
    `<name>.tmp`, one fixed name per path. Two writers on one volume then take turns filling and
    promoting each other's half-written bytes, and `os.replace` publishes the result — atomically,
    which is what makes it hard to see.

    Checked by watching what the helper actually stages, over two SEQUENTIAL calls. Uniqueness is a
    property of one call — a name derived per call cannot collide, a fixed one always does — so
    holding two writers in the window at once would only add a way for this test to hang without
    telling us anything the second name does not.
    """
    dest = tmp_path / "shared.json"
    seen: list[str] = []
    real_replace = os.replace

    def watching_replace(src, dst, *a, **k):
        seen.append(Path(src).name)
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(manager.os, "replace", watching_replace)
    manager._write_atomic(dest, json.dumps({"n": 0}))
    manager._write_atomic(dest, json.dumps({"n": 1}))

    assert len(seen) == 2, f"expected two staged writes, saw {seen}"
    assert seen[0] != seen[1], (
        f"both writers staged through the same temp name ({seen[0]}). Each was free to overwrite "
        f"the other's bytes before either `os.replace` ran."
    )


def test_an_atomic_write_carries_the_mode_of_the_file_it_replaces(tmp_path: Path):
    """`os.replace` swaps the INODE, so the destination inherits the temp file's mode.

    On the shared Project volume that silently drops group-write on the first save by any Builder,
    and the next collaborator's save fails EACCES — which the readers here then report as "couldn't
    be read. Fix that file", sending somebody to edit contents that are fine. #289 worked this out
    on one path; the other two open-coded sites never carried it.
    """
    dest = tmp_path / "shared.json"
    dest.write_text("{}")
    dest.chmod(0o664)
    before = stat.S_IMODE(dest.stat().st_mode)

    manager._write_atomic(dest, json.dumps({"n": 1}))

    assert stat.S_IMODE(dest.stat().st_mode) == before, (
        "the replacement carried the temp file's mode (whatever the umask said) rather than the "
        "mode of the file it replaced."
    )


def test_a_brand_new_file_is_created_with_its_parent_directory(tmp_path: Path):
    """The helper owns the `mkdir` every call site used to repeat, so no caller can forget it."""
    dest = tmp_path / "does" / "not" / "exist" / "settings.json"
    manager._write_atomic(dest, json.dumps({"n": 1}))
    assert json.loads(dest.read_text()) == {"n": 1}


@pytest.mark.parametrize("fails_at", ["write", "replace"])
def test_a_failed_write_leaves_no_temp_file_behind(tmp_path: Path, monkeypatch, fails_at: str):
    """Uniqueness is what makes a failed write ACCUMULATE rather than overwrite.

    ENOSPC or EACCES would otherwise drop a fresh file into `.sage/` on every attempt, in the one
    directory this whole change keeps calling committed and shared — so the `finally` is not
    tidiness, it is the price of the unique name. Both failure points are covered because they
    leave the temp file in different states.
    """
    dest = tmp_path / "sage" / "settings.json"
    dest.parent.mkdir(parents=True)
    dest.write_text("{}")

    def boom(*a, **k):
        raise RuntimeError("disk said no")

    monkeypatch.setattr(manager.os, "replace" if fails_at == "replace" else "fsync", boom)
    with pytest.raises(RuntimeError):
        manager._write_atomic(dest, json.dumps({"n": 1}))

    leftovers = [p.name for p in dest.parent.iterdir() if p.name != dest.name]
    assert leftovers == [], f"a failed write left {leftovers} in a committed directory"
    assert dest.read_text() == "{}", "a failed write changed the destination"
