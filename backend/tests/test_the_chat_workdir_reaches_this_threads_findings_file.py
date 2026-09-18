"""A Chat turn can open and write this Thread's `findings.md`.

`chat_path_allowed` has permitted `.sage/threads/<threadId>/` since it was written — a write there
is not reverted, and `revert_denied_writes` names the prefix in its own docstring. But permission
is not reach. A Chat turn's cwd is `.sage/chat-work`, and `ensure_chat_workdir` linked in exactly
`examples/`, `.sage/scratch/` and `public/data/`. With no `.sage/threads` link, the one file a long
investigation is meant to keep across turns resolved to nothing from where the agent stands: a read
found no file, and a write landed in a `.sage/threads` directory INSIDE the workdir that nothing
ever reads back. Both failed quietly, which is the worst shape for a file whose whole purpose is to
still be there next turn.
"""
from __future__ import annotations

from pathlib import Path

from sage.shim.chat_paths import chat_path_allowed
from sage.workspace.threads import ensure_chat_workdir

TID = "thr_a1b2c3"
REL = f".sage/threads/{TID}/findings.md"


def test_the_turn_reads_this_threads_findings_file_at_its_workspace_shaped_path(tmp_path: Path):
    (tmp_path / ".sage" / "threads" / TID).mkdir(parents=True)
    (tmp_path / REL).write_text("2026-09-16T11:04Z SFDC_CONTACT_ID 16,756/89,399 (18.7%)\n")

    work = ensure_chat_workdir(tmp_path, "# chat", thread_id=TID)

    assert (work / REL).read_text().startswith("2026-09-16T11:04Z")


def test_a_write_from_the_turn_lands_on_the_projects_own_findings_file(tmp_path: Path):
    """Reach has to go both ways. A write through the link must reach the Project's file rather
    than a lookalike under the workdir, or the next turn reads the old bytes back."""
    work = ensure_chat_workdir(tmp_path, "# chat", thread_id=TID)

    (work / REL).parent.mkdir(parents=True, exist_ok=True)
    (work / REL).write_text("2026-09-16T11:12Z DMM accounts 89/1,204 (7.4%)\n")

    assert (tmp_path / REL).read_text().startswith("2026-09-16T11:12Z")
    # And the path it was written at is the one the turn is allowed to keep.
    assert chat_path_allowed(REL, TID)


def test_the_workdir_link_is_relative_so_a_moved_checkout_still_resolves(tmp_path: Path):
    """Every other link here is relative for this reason — an absolute one breaks when the volume
    is mounted at a different path in the container than it was written at."""
    work = ensure_chat_workdir(tmp_path, "# chat", thread_id=TID)

    link = work / ".sage" / "threads" / TID
    assert link.is_symlink()
    assert not Path(link.readlink()).is_absolute()
    assert link.resolve() == (tmp_path / ".sage" / "threads" / TID).resolve()


def test_the_turn_can_write_its_scratch_dir_at_the_path_the_prompt_names(tmp_path: Path):
    """The same lesson for the scratch dir the prompts moved onto in #415.

    `chat_path_allowed` permitting `.sage/scratch/<threadId>/` is half of it; the turn also has to
    be able to REACH it from `.sage/chat-work`, and the leaf has to exist. The lane this exists
    for is the read-only lane, which has no shell — so it cannot `mkdir` the directory itself, and
    a destination it has to create before using is a destination it does not have.
    """
    scratch = f".sage/scratch/{TID}/fetch.py"
    assert chat_path_allowed(scratch, TID)

    work = ensure_chat_workdir(tmp_path, "# chat", thread_id=TID)

    # Created on the way in, not left to the turn.
    assert (tmp_path / ".sage" / "scratch" / TID).is_dir()
    assert (work / ".sage" / "scratch" / TID).is_dir()

    (work / scratch).write_text("import pandas as pd\n")
    assert (tmp_path / scratch).read_text() == "import pandas as pd\n"


def test_another_threads_scratch_is_not_writable_from_this_turn(tmp_path: Path):
    """Scoping, checked at the door rather than only in the allowlist: the uploads the person can
    see in the rail sit FLAT in `.sage/scratch/`, so a turn that could write the whole tree could
    overwrite one of them."""
    ensure_chat_workdir(tmp_path, "# chat", thread_id=TID)

    assert not chat_path_allowed(".sage/scratch/their_upload.csv", TID)
    assert not chat_path_allowed(".sage/scratch/thr_other/fetch.py", TID)
