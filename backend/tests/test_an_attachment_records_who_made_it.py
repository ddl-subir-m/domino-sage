"""An Attachment records who made it (#262, ADR-0048 — "The record").

WHAT WAS WRONG. `attach_file` wrote the Dataset id, the Dataset, the file, the path, the size, the
source and the relative path — and nothing about who performed the act. The chip one layer up
already carried `addedBy`, and the Build fork in `add_thread_context` dropped it, handing
`attach_file` only the Dataset id and the relative path.

WHY IT MATTERS HERE AND NOWHERE ELSE. Under
[ADR-0021](../../docs/adr/0021-each-scopes-door-lives-on-the-surface-that-owns-it.md), every act
that reached an app was taken on the app's own surface, so "who" was answerable by standing there.
This fork is the exception: a mention typed into a Build Conversation ships bytes into the app and
commits a manifest entry, and it is the one case where a person cannot reconstruct the answer.
That is why this un-deletes the provenance line
[ADR-0035](../../docs/adr/0035-the-panel-is-the-projects-one-list.md) removed on 2026-09-05 — a
removal its own table flagged as "made without stating a reason, found in review". The reason to
restore it is new rather than a reversal of that call.

THE TWO HALVES. The manifest is the record and the App dependencies row is the reading, so both are
asserted: a field nothing draws is a field nobody checks, and a sentence drawn off a guess is worse
than no sentence. The JS half is the harness at the bottom, on the surface that owns the app's list
since ADR-0035.

WHAT STAYS SILENT. An entry written before this ticket carries no author, and the row says nothing
rather than guessing "you" — absent is not "you". `_rehydrate_attached`'s symlink-scan fallback for
pre-manifest workspaces never carried a Dataset id either and still will not; this ticket does not
invent one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.service import Orchestrator, _dataset_entry
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog

DATASET = "ds_sales_2026"
FILE = "train.csv"
ATTACHED = "public/data/sales_2026/train.csv"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path) -> Orchestrator:
    """An Orchestrator with its Project already attached and no Vite behind it.

    `attach_file` calls `project()` with the preview on, and `project()` is get-or-attach, so the
    attach is settled here once rather than by every test starting a dev server it never reads.
    """
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        assets=FakeAssetProvider(root=tmp / "mounts"),
        resources=FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    return orch


def _mention(orch: Orchestrator, thread_id: str, **over) -> dict:
    """One Dataset file mentioned in a Build Conversation, as `api.js` posts it.

    No `path`: the fork fires only for a row that has none, because a row that already carries one
    names bytes somebody put there already.
    """
    return orch.add_thread_context(thread_id, {
        "kind": "file", "name": FILE, "datasetId": DATASET, "datasetRelPath": FILE,
        "datasetName": "sales_2026", "resourceId": f"dsfile:{DATASET}:{FILE}",
        "parentId": f"dataset:{DATASET}", "inBuild": True, "addedBy": "user", **over,
    })


def _entry(orch: Orchestrator) -> dict:
    """The one manifest entry the mention wrote, read back off disk rather than out of memory —
    the file is what survives a restart and what `_rehydrate_attached` reads."""
    record = orch.project().workspace.path / ".sage" / "attachments.json"
    entries = json.loads(record.read_text())
    assert [e["path"] for e in entries] == [ATTACHED], entries
    return entries[0]


# --- the record ----------------------------------------------------------------------------------


def test_a_mention_in_build_records_the_person_who_made_it(tmp_path: Path):
    """The author the chip already knew, carried across the fork and onto the entry."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]

    _mention(orch, thread)

    assert _entry(orch)["added_by"] == "user"


def test_the_entry_names_the_conversation_the_act_was_taken_in(tmp_path: Path):
    """The other half of "who": an act taken in a Conversation is answerable by going back to it,
    and the Build fork is the one door where the Conversation is not on the app's own surface."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]

    _mention(orch, thread)

    assert _entry(orch)["conversation_id"] == thread


def test_an_attachment_the_assistant_made_records_the_assistant(tmp_path: Path):
    """`addedBy` is already two-valued one layer up — a person picks in the rail, and the assistant
    picks for them (`store.attach`). Recording only the person would make the other case a lie."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]

    _mention(orch, thread, addedBy="sage")

    assert _entry(orch)["added_by"] == "sage"


def test_the_rest_of_the_entry_is_the_shape_every_other_reader_knows(tmp_path: Path):
    """Fields added, never renamed or removed. `_dataset_is_attached`, `_rehydrate_attached`,
    detach, leak detection and the commit backstop all read this record, and none of them is in
    this ticket."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]

    _mention(orch, thread)
    entry = _entry(orch)

    assert entry["dataset_id"] == DATASET
    assert entry["dataset"] == "sales_2026"
    assert entry["file"] == FILE
    assert entry["dataset_rel_path"] == FILE
    assert entry["source"] == "dataset"
    assert entry["size"] > 0


def test_a_file_a_confirmed_handoff_carried_over_records_the_same_author(tmp_path: Path):
    """The other act that makes an Attachment from a Conversation rather than on the app's own
    surface. `_promote_chat_file` turns a Dataset file Chat merely fetched into one the built app
    serves, and the chip it reads already names the author — so a row saying nothing here would be
    silence about an act whose "who" is sitting in the argument."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]

    orch._promote_chat_file({
        "kind": "file", "name": FILE, "datasetId": DATASET, "datasetRelPath": FILE,
        "addedBy": "user",
    }, thread)

    assert _entry(orch)["added_by"] == "user"
    assert _entry(orch)["conversation_id"] == thread


# --- silence, where there is nothing to say ------------------------------------------------------


def test_an_entry_written_before_this_ticket_claims_no_author():
    """Every entry already on disk was written without these two fields. The record has to read
    back as "not known" rather than as the caller having declined to say — which is why the
    absence is asserted on the shared writer and not only on the panel row."""
    entry = _dataset_entry(DATASET, "sales_2026", FILE, ATTACHED, 2048)

    assert entry["added_by"] is None
    assert entry["conversation_id"] is None


def test_the_symlink_scan_fallback_invents_neither_an_author_nor_a_dataset(tmp_path: Path):
    """A workspace written before the manifest existed is rebuilt by scanning `public/data/` for
    symlinks, and a scan knows only what the filesystem says: not the Dataset the bytes came from,
    and not the person who linked them. ADR-0048 names this rather than fixing it."""
    orch = _orch(tmp_path)
    project = orch.project()
    ws = project.workspace.path
    bytes_at = tmp_path / "mounts" / "sales_2026" / FILE
    link = ws / "public" / "data" / "sales_2026" / FILE
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(bytes_at)
    manifest = ws / ".sage" / "attachments.json"
    if manifest.is_file():
        manifest.unlink()
    project.attached.clear()

    orch._rehydrate_attached(project)

    assert [e["path"] for e in project.attached] == [ATTACHED]
    assert project.attached[0]["dataset_id"] is None
    assert project.attached[0].get("added_by") is None


# --- the reading ---------------------------------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "attachment_author_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _rows() -> dict:
    """The App dependencies modal's rows, by name, each with the provenance line under it."""
    out = subprocess.run(
        ["node", str(_HARNESS)],
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    report = json.loads(out.stdout.strip().splitlines()[-1])
    return {row["name"]: row["by"] for row in report["files"]}


@needs_node
def test_the_row_says_the_person_added_it():
    """ADR-0035's subtitle, back on the surface that owns the app's list now."""
    assert _rows()["mine.csv"] == "You added this"


@needs_node
def test_the_row_names_the_assistant_by_the_packs_word():
    """Not "Sage": the assistant's name is the pack's to set (ADR-0014), and a row that hard-codes
    ours is a leak wherever the pack says something else."""
    assert _rows()["theirs.csv"] == "ZZ Helper added this"


@needs_node
def test_a_row_with_no_recorded_author_says_nothing():
    """The entry every workspace already holds. "You added this" over an entry that records no
    author is the panel inventing provenance, which is worse than the line being missing."""
    assert _rows()["older.csv"] == ""
