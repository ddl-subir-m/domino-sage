"""A Chat turn reads the whole workspace's bytes to find out what it wrote. Two kinds of turn never
wrote anything, and both were reading anyway (#418, #419).

#419 is the turn that CANNOT write: `answer_only` arms `arm_read_only("question")`, whose
`READ_ONLY_DENIED = WRITE_TOOLS | SHELL_TOOLS` strips every write tool and the shell, and such a
turn is never `data_artifact` so it holds no `artifact_write` either. That is known at arming,
before dispatch, so the BEFORE snapshot can be skipped too.

#418 is the turn that DID not write: no `tool_run` event reached the poll loop. That is known only
at the turn's end, so it can skip the end-of-turn scan and nothing else — taking the before
snapshot lazily on the first `tool_run` is a race, because the event fires when the tool is invoked
and Sage sees it a poll later (p50 1000ms), by which time a `bash` step has already written.

The two are kept as separate arguments to `_revert_scan_owed`. Collapsing them into one flag loses
#419's before-snapshot saving or widens #418's skip into the race it rejected (#287).

**The hazard these exist for.** "Skip the snapshot" must produce `None`, never `{}`. Six readers ask
`before` what this turn wrote, and an empty dict answers "all of it". Four of them are destructive,
and the tests below are their receipts — they are written to pass against today's code, so they say
what an empty baseline WOULD do rather than asserting it is impossible. If someone later
"simplifies" the skip into `before = {}`, these are the sentences describing what they shipped.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from sage.orchestrator.service import _revert_scan_owed
from sage.workspace.chat_tables import ChatTables
from sage.workspace.threads import (
    FINDINGS_MAX,
    findings_file,
    new_artifact_paths,
    refuse_oversize_findings,
    revert_denied_writes,
    snapshot_files,
    withhold_table_rows,
)

_SERVICE = Path(__file__).resolve().parents[1] / "sage" / "orchestrator" / "service.py"
_THREAD = "t_write"


def _seed(root: Path) -> dict[str, bytes]:
    """A workspace with one file the allowlist does NOT cover, plus one it does."""
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "main.py").write_text("print('the person wrote this')\n")
    thread_dir = root / "examples" / _THREAD
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "earlier.table.json").write_text(
        json.dumps({"title": "Earlier", "columns": ["a"], "rows": [["kept"]]}))
    return snapshot_files(root)


# --------------------------------------------------------------------------------------------
# The gate itself, driven through all four combinations.
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "before, any_tool_ran, owed, why",
    [
        ({"app/main.py": b"x"}, True, True, "an ordinary turn that ran a tool owes the scan"),
        ({"app/main.py": b"x"}, False, False, "#418: no tool ran, so nothing was written"),
        (None, True, False, "#419: the turn held no tool that could write"),
        (None, False, False, "both reasons at once is still not a reason to scan"),
    ],
)
def test_the_scan_is_owed_only_by_a_turn_that_could_and_did_write(before, any_tool_ran, owed, why):
    assert _revert_scan_owed(before=before, any_tool_ran=any_tool_ran) is owed, why


def test_an_empty_baseline_is_not_a_missing_one(tmp_path: Path):
    """The shape of the whole hazard, in one line. `{}` is falsy and `None` is falsy, so any gate
    written `if not before` would treat a turn that wrote nothing into an empty workspace exactly
    like a turn that took no snapshot. The gate asks `is not None` for this reason."""
    assert _revert_scan_owed(before={}, any_tool_ran=True) is True, (
        "an empty workspace is a real baseline and its turn still owes the scan")
    assert _revert_scan_owed(before=None, any_tool_ran=True) is False


def test_the_call_site_reverts_only_under_the_gate():
    """A green predicate does not prove the call site asks it. Structural rather than a grep: the
    question is whether `revert_denied_writes` has an ancestor `if` testing `_revert_scan_owed`,
    which is about the tree and not about the spelling of a line."""
    tree = ast.parse(_SERVICE.read_text())
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "revert_denied_writes"]
    assert calls, "the revert call has moved or been renamed; this test cannot see it any more"

    for call in calls:
        guarded = False
        node = call
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If) and any(
                    isinstance(t, ast.Call) and getattr(t.func, "id", "") == "_revert_scan_owed"
                    for t in ast.walk(node.test)):
                guarded = True
                break
        assert guarded, "a revert_denied_writes call is not under `_revert_scan_owed`"


def test_the_skipped_snapshot_yields_none_and_never_an_empty_dict():
    """The regression the plants below only DESCRIBE. Every `{}` test in this file says what an
    empty baseline costs; none of them can stop `before = {} if answer_only else ...` being
    written, because no Python test drives `_chat_stream`. This one can: it reads the assignment
    itself and refuses any skip value but `None`.

    Keyed on the assignment rather than on a line number, and it fails loudly if the shape changes
    — a reader who restructures this is the reader who needs to re-decide the question."""
    tree = ast.parse(_SERVICE.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        if getattr(node.targets[0], "id", "") != "before":
            continue
        if not isinstance(node.value, ast.IfExp):
            continue
        calls = {getattr(c.func, "id", "") for c in ast.walk(node.value)
                 if isinstance(c, ast.Call)}
        if "snapshot_files" not in calls:
            continue
        found.append(node)
        skip = node.value.body if isinstance(node.value.test, ast.Name) else None
        assert skip is not None, "the skip condition is no longer a plain name; re-read the gate"
        assert isinstance(skip, ast.Constant) and skip.value is None, (
            "the skipped branch must be `None`. An empty dict is not 'no snapshot' — it tells six "
            "readers this turn wrote every file in the workspace, and four of them act on it.")
        assert node.value.test.id == "answer_only", (
            "the snapshot skip must stay on #419's condition. #418's `any_tool_ran` is known only "
            "at the turn's end and cannot gate a read taken before dispatch.")

    assert len(found) == 1, (
        "expected exactly one conditional `before = ... snapshot_files(...)` in _chat_stream; "
        f"found {len(found)}")


# --------------------------------------------------------------------------------------------
# The `{}` plant: what an empty baseline does to each of the four destructive readers.
# --------------------------------------------------------------------------------------------

def test_an_empty_baseline_deletes_every_file_the_allowlist_does_not_cover(tmp_path: Path):
    """The named plant. `revert_denied_writes` reads `prev = before.get(rel)` and unlinks on `None`,
    so an empty baseline makes every non-allowlisted file look newly created."""
    _seed(tmp_path)
    reverted = revert_denied_writes(tmp_path, _THREAD, {})
    assert "app/main.py" in reverted
    assert not (tmp_path / "app" / "main.py").exists(), (
        "this is what `before = {}` costs: the person's file, deleted by the turn that skipped its "
        "snapshot. The skip must skip the CALL.")


def test_a_real_baseline_leaves_that_same_file_alone(tmp_path: Path):
    """The other half of the plant, so the one above cannot be read as `revert_denied_writes` being
    broken. Given the truth about what was on disk, it touches nothing."""
    before = _seed(tmp_path)
    assert revert_denied_writes(tmp_path, _THREAD, before) == []
    assert (tmp_path / "app" / "main.py").read_text() == "print('the person wrote this')\n"


def test_a_real_baseline_still_reverts_a_planted_denied_write(tmp_path: Path):
    """The guard that must go red if the gate is ever wired backwards: a turn that DID run a tool,
    against a real baseline, still undoes a write outside `examples/<threadId>/`."""
    before = _seed(tmp_path)
    (tmp_path / "app" / "main.py").write_text("print('the model wrote this')\n")
    (tmp_path / "app" / "sneaked.py").write_text("print('new')\n")

    assert _revert_scan_owed(before=before, any_tool_ran=True) is True
    reverted = revert_denied_writes(tmp_path, _THREAD, before)

    assert set(reverted) == {"app/main.py", "app/sneaked.py"}
    assert (tmp_path / "app" / "main.py").read_text() == "print('the person wrote this')\n"
    assert not (tmp_path / "app" / "sneaked.py").exists()


def test_an_empty_baseline_strips_the_rows_of_an_earlier_turns_table(tmp_path: Path):
    """The second destructive reader, and the quietest. `withhold_table_rows` skips a file whose
    bytes match `before`; with `{}` nothing matches, so every table this Thread ever wrote is
    rewritten to shape-only and its rows are gone from disk."""
    _seed(tmp_path)
    rewritten = withhold_table_rows(tmp_path, _THREAD, {}, kept_rows=False)

    assert "examples/t_write/earlier.table.json" in rewritten
    body = json.loads((tmp_path / "examples" / _THREAD / "earlier.table.json").read_text())
    assert body["rows"] == [], "an earlier turn's rows, destroyed by this turn's empty baseline"


def test_an_empty_baseline_unlinks_an_oversize_findings_file(tmp_path: Path):
    """The third. `refuse_oversize_findings` unlinks on `prev is None`, so an investigation's notes
    are deleted rather than restored — by a turn that could not have grown them."""
    _seed(tmp_path)
    notes = findings_file(tmp_path, _THREAD)
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_bytes(b"m" * (FINDINGS_MAX + 1))

    assert refuse_oversize_findings(tmp_path, _THREAD, {}) is not None
    assert not notes.exists(), "the investigation's notes, deleted by an empty baseline"


def test_an_empty_baseline_re_records_every_artifact_as_new(tmp_path: Path):
    """The fourth. Not destructive on disk, but it draws a duplicate card for every Artifact the
    Thread already had — the same file offered again under an answer that never made it."""
    _seed(tmp_path)
    assert new_artifact_paths(tmp_path, _THREAD, {}) == ["examples/t_write/earlier.table.json"]


# --------------------------------------------------------------------------------------------
# `ChatTables` with no baseline.
# --------------------------------------------------------------------------------------------

def test_no_baseline_makes_no_pre_existing_table_a_candidate(tmp_path: Path):
    """`self.before` is what marks a file as THIS turn's work. `None` says the turn wrote nothing,
    so an earlier turn's table is neither validated nor repaired — and with `{}` it would be both."""
    _seed(tmp_path)
    assert ChatTables(tmp_path, _THREAD, None).check("") == {}

    invalid = ChatTables(tmp_path, _THREAD, {}).check("")
    assert invalid == {}, (
        "today an earlier VALID table survives an empty baseline — but it was validated, and it "
        "entered `candidates`, which is the repair target list")
    assert ChatTables(tmp_path, _THREAD, {}).check("").keys() == invalid.keys()


def test_no_baseline_still_catches_a_table_the_answer_named_but_never_made(tmp_path: Path):
    """The reason `check` keeps running on an `answer_only` turn. The model holds no tool to write a
    table, but nothing stops it saying it did — and a named path with nothing under it is exactly
    what this pass is for."""
    _seed(tmp_path)
    named = f"examples/{_THREAD}/promised.table.json"
    invalid = ChatTables(tmp_path, _THREAD, None).check(f"The table is ready: {named}")
    assert invalid.get(named) == "missing or unreadable file"


def test_no_baseline_validates_an_earlier_table_the_answer_pointed_at(tmp_path: Path):
    """And the same path for a file that IS there: naming it asks for it to be checked, and a valid
    one comes back clean whether or not a baseline was taken."""
    _seed(tmp_path)
    named = f"examples/{_THREAD}/earlier.table.json"
    assert ChatTables(tmp_path, _THREAD, None).check(f"See {named}") == {}
