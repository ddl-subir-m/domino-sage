"""An AGENTS.md at the volume root is moved off OpenCode's walk-up path (#548).

OpenCode reads AGENTS.md from the session directory AND every directory up to the project root, so
a file at the volume root is a standing instruction to every Chat and Build turn in the Project.
MEASURED 2026-09-24 against the pinned binary with a fake model: planting one took the system
prompt from 38,458 to 70,524 wire bytes. The stage profile cannot help — it strips the `implement`
block from the SESSION file and never sees this one — so the duplicate is ~43% of an implement
turn and ~75% of a plan turn.

**Not keyed on Project age.** The old rule was "only Projects made before `36c8167`". Measured live
in a Project its owner believed was new: the file was there at 30,338 bytes, byte-for-byte the
voiced `template/react-vite/AGENTS.md` of `e1a52227` (2026-09-21). These tests therefore key on
the file being present, and never on when the Project was made.

The move is a rename, never a delete or a rewrite: the before-state is the file itself, still on
disk under a name that says what it is.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.workspace.manager import WorkspaceManager

LEGACY = "# Legacy\nSay **{dataSource}** when you mean a connection.\n"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Sage app\n")
    (t / ".gitignore").write_text("node_modules\n")
    return t


def _manager(tmp: Path) -> WorkspaceManager:
    return WorkspaceManager(workspace_dir=tmp / "ws", template=_template(tmp))


def _root(tmp: Path) -> Path:
    return tmp / "ws" / "AGENTS.md"


def _kept(tmp: Path) -> Path:
    return tmp / "ws" / ".sage" / "legacy-root-AGENTS.md"


# --- present: moved, byte for byte -----------------------------------------------------------

def test_a_root_agents_md_is_moved_out_of_the_way(tmp_path: Path):
    _manager(tmp_path).ensure("proj1")
    _root(tmp_path).write_text(LEGACY)

    _manager(tmp_path).ensure("proj1")

    assert not _root(tmp_path).exists(), "the file is still where OpenCode walks up to it"
    assert _kept(tmp_path).read_text() == LEGACY, "the bytes must survive the move unchanged"


def test_the_destination_is_not_a_name_opencode_looks_for(tmp_path: Path):
    """OpenCode globs `AGENTS.md`, `CLAUDE.md` and `CONTEXT.md` by exact name. Chat's session is
    `.sage/chat-work`, so `.sage/` IS on its walk-up — a file named AGENTS.md in there would be
    read by Chat even though Build never sees it."""
    _manager(tmp_path).ensure("proj1")
    _root(tmp_path).write_text(LEGACY)

    _manager(tmp_path).ensure("proj1")

    assert _kept(tmp_path).name not in {"AGENTS.md", "CLAUDE.md", "CONTEXT.md"}
    walked = [p for p in (tmp_path / "ws").rglob("*")
              if p.name in {"AGENTS.md", "CLAUDE.md", "CONTEXT.md"}
              and p.parent in {tmp_path / "ws", tmp_path / "ws" / ".sage"}]
    assert walked == [], f"still on a walk-up path: {walked}"


def test_the_move_does_not_collide_with_the_chat_work_stub(tmp_path: Path):
    """`.sage/chat-work/AGENTS.md` is a live 151-byte stub. Landing on it would replace a file
    Chat depends on with 30 KB of the wrong stack's rules."""
    _manager(tmp_path).ensure("proj1")
    stub = tmp_path / "ws" / ".sage" / "chat-work" / "AGENTS.md"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("stub\n")
    _root(tmp_path).write_text(LEGACY)

    _manager(tmp_path).ensure("proj1")

    assert stub.read_text() == "stub\n"
    assert _kept(tmp_path).read_text() == LEGACY


# --- absent: nothing happens -------------------------------------------------------------------

def test_nothing_happens_when_there_is_no_root_agents_md(tmp_path: Path):
    _manager(tmp_path).ensure("proj1")
    assert not _root(tmp_path).exists()

    _manager(tmp_path).ensure("proj1")

    assert not _kept(tmp_path).exists(), "a Project with no legacy file gained a stray record"


def test_a_directory_named_agents_md_is_left_alone(tmp_path: Path):
    """`is_file()`, not `exists()`: renaming a directory here would move a tree nobody asked about."""
    _manager(tmp_path).ensure("proj1")
    (tmp_path / "ws" / "AGENTS.md").mkdir()

    _manager(tmp_path).ensure("proj1")

    assert (tmp_path / "ws" / "AGENTS.md").is_dir()
    assert not _kept(tmp_path).exists()


# --- twice: the second run is a no-op ----------------------------------------------------------

def test_running_twice_is_a_no_op_and_keeps_the_first_move(tmp_path: Path):
    _manager(tmp_path).ensure("proj1")
    _root(tmp_path).write_text(LEGACY)
    _manager(tmp_path).ensure("proj1")
    first = _kept(tmp_path).read_text()

    _manager(tmp_path).ensure("proj1")
    _manager(tmp_path).ensure("proj1")

    assert _kept(tmp_path).read_text() == first
    assert not _root(tmp_path).exists()


def test_a_second_root_file_does_not_overwrite_the_record_of_the_first(tmp_path: Path):
    """The moved copy is the only copy of those bytes. A later root file must not destroy it —
    the prompt keeps the duplicate rather than this losing data."""
    _manager(tmp_path).ensure("proj1")
    _root(tmp_path).write_text(LEGACY)
    _manager(tmp_path).ensure("proj1")

    _root(tmp_path).write_text("# a different file\n")
    _manager(tmp_path).ensure("proj1")

    assert _kept(tmp_path).read_text() == LEGACY, "the first move's record was overwritten"
    assert _root(tmp_path).read_text() == "# a different file\n"


# --- failure: the Project still opens -----------------------------------------------------------

def test_a_move_that_fails_does_not_stop_the_project_opening(tmp_path: Path, monkeypatch):
    """Read-only volume, or a permission this process does not hold. The cost of not moving is a
    long prompt; the cost of raising is a Project that will not open at all (#549's lesson)."""
    _manager(tmp_path).ensure("proj1")
    _root(tmp_path).write_text(LEGACY)

    def refuse(self, target):
        raise PermissionError("read-only volume")

    monkeypatch.setattr(Path, "rename", refuse)

    assert _manager(tmp_path).ensure("proj1") is not None
    assert _root(tmp_path).read_text() == LEGACY, "a failed move must not lose the file"


def test_bytes_that_are_not_utf8_are_moved_rather_than_read(tmp_path: Path):
    """The voicer this replaced read the file, so a non-UTF-8 one raised out of the first step of
    opening a Project (#303). A rename never decodes."""
    _manager(tmp_path).ensure("proj1")
    _root(tmp_path).write_bytes(b"\xff\xfe# not utf-8\n")

    assert _manager(tmp_path).ensure("proj1") is not None
    assert _kept(tmp_path).read_bytes() == b"\xff\xfe# not utf-8\n"


# --- the prompt no longer carries it -------------------------------------------------------------

def test_the_walk_up_no_longer_reaches_a_duplicate_after_the_move(tmp_path: Path):
    """The point of the whole change, asserted the way OpenCode asks the question: walking from a
    session directory up to the Project root must find the app's AGENTS.md and nothing above it.
    Mirrors `Instruction.systemPaths` in the pinned binary, which globs by exact name.
    """
    manager = _manager(tmp_path)
    workspace = manager.ensure("proj1")
    _root(tmp_path).write_text(LEGACY)

    _manager(tmp_path).ensure("proj1")

    root = tmp_path / "ws"
    found, cursor = [], Path(workspace.path)
    while True:
        candidate = cursor / "AGENTS.md"
        if candidate.is_file():
            found.append(candidate)
        if cursor == root:
            break
        cursor = cursor.parent

    assert found == [Path(workspace.path) / "AGENTS.md"], (
        f"the walk-up still reaches more than the app's own instructions: {found}")
    assert LEGACY not in "".join(p.read_text() for p in found)


@pytest.mark.parametrize("session", [Path("apps") / "an-app", Path(".sage") / "chat-work"])
def test_neither_session_directory_walks_up_to_a_duplicate(tmp_path: Path, session):
    """Build's session and Chat's session walk up through different directories, and the file has
    to be out of BOTH. Chat's passes through `.sage/`, which is where the move puts the record."""
    _manager(tmp_path).ensure("proj1")
    _root(tmp_path).write_text(LEGACY)
    _manager(tmp_path).ensure("proj1")

    root = tmp_path / "ws"
    cursor, found = root / session, []
    while True:
        if (cursor / "AGENTS.md").is_file():
            found.append(cursor / "AGENTS.md")
        if cursor == root:
            break
        cursor = cursor.parent

    assert all(p.read_text() != LEGACY for p in found), f"{session} still walks up to it: {found}"
