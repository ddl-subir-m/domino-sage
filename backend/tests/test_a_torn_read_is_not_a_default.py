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
silence. A zero reds on the write itself. The one append is named rather than counted, for the same
reason every line number in #308's body went stale the moment #303 landed.
"""
from __future__ import annotations

import ast
import json
import os
import stat
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
        if isinstance(func, ast.Attribute) and func.attr == "write_text":
            found.append((_enclosing_function(tree, node), "write_text"))
        elif isinstance(func, ast.Attribute) and func.attr == "open":
            mode = _mode_of(node, builtin=False)
            if any(c in mode for c in "wax+"):
                found.append((_enclosing_function(tree, node), f"Path.open({mode!r})"))
        elif isinstance(func, ast.Name) and func.id == "open":
            mode = _mode_of(node, builtin=True)
            if any(c in mode for c in "wax+"):
                found.append((_enclosing_function(tree, node), f"open({mode!r})"))
    return found


def test_no_whole_file_write_sits_outside_the_atomic_helper():
    """The census. A zero, so a write added later reds this instead of joining a stale number.

    Nineteen sites were routed through the helper for #308: sixteen wrote straight at the
    destination, and three already staged through a temp file but open-coded the staging — two of
    those three shared one temp NAME, which is the same torn read arrived at from the other side
    (see `test_two_writers_to_one_path_do_not_share_a_temp_name`).
    """
    truncating = [w for w in _writes(inside_helper=False) if "'a'" not in w[1]]
    assert truncating == [], (
        "These write a file's whole contents without staging through `_write_atomic`, so each "
        "leaves the destination readable-as-empty for the length of the write:\n  "
        + "\n  ".join(f"{fn}: {what}" for fn, what in truncating)
    )


def test_the_only_exempt_write_is_the_one_that_appends():
    """The exemption, named rather than numbered.

    `append_history` is safe for a reason that is about the mechanism and not about the file: an
    append never removes bytes that are already on disk, so no reader can observe the log missing
    an entry it previously had. A partial final line is the only artefact, and `_iter_history`
    already drops an unparseable line rather than failing the read.

    Named, so that adding a second append is a deliberate edit to this list and not a silent join.
    """
    appends = {fn for fn, what in _writes(inside_helper=False) if "'a'" in what}
    assert appends == {"append_history"}, (
        "The set of append-mode writes in workspace/manager.py changed. An append is exempt from "
        "the atomic rule because it cannot remove existing content — confirm that is true of the "
        "new one, then add it here."
    )


def test_the_helper_stages_its_write_somewhere_other_than_the_destination():
    """The inverse of the census: proves the helper is the thing the rule points at.

    Without this, deleting the body of `_write_atomic` would leave every test above green — the
    census would report a clean zero over a helper that writes nothing.
    """
    staged = _writes(inside_helper=True)
    assert staged, "`_write_atomic` opens no file for writing. It is not writing anything."


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
