"""Removing an attachment in Build gives back the copy Chat fetched (#249).

WHAT WAS WRONG. Two surfaces remove the same attached file, and only one of them removed the data.
Chat's chip goes through `remove_thread_context` -> `_release_chat_file`, which deletes the fetched
copy under `.sage/scratch/`. Build's Data panel goes through `detach_file`, which unlinks
`public/data/<slug>/<name>` and never called `_release_chat_file` at all.

WHY NOBODY HAD TO DO ANYTHING UNUSUAL TO REACH IT. A confirmed Chat->Build handoff promotes every
Chat attachment into the new app (`_promote_chat_file`), and the promotion HANDS THE SCRATCH BYTES
OVER rather than fetching them again: `public/data/...` becomes a symlink standing on the scratch
file. From that moment the two doors deadlock. Closing the chip in Chat is refused, correctly, by
`_release_chat_file`'s own guard — an attached entry links at these bytes and deleting them would
leave the app pointing at nothing. Removing the file in Build then drops that entry and the link,
and nothing ever comes back for the scratch copy. It is at the PROJECT root, so deleting the app
does not take it; it is gitignored, so no commit, clone or revert does either.

Live on 2026-09-11: a CSV of card transactions was removed in Build's Data panel and three clean
files attached in its place, and the next build was refused by the `Block PII` guardrail anyway --
on 4,500 matches in a file the person had removed from the only place they could see it.

THE ORDER IS THE TEST. "Remove it in Build, assert the scratch copy is gone" passes with the bug
wide open, because a file the Thread never held was never held back. The chip has to be closed
WHILE the file is attached, so the release is refused once, before Build's door is pressed.

WHAT THIS DOES NOT CLAIM. Releasing is necessary, not sufficient: while a live Conversation still
names the fetch, the bytes stay whichever door is pressed. So both doors now say so rather than
letting the person believe a removal took the data with it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.assets.provider import Asset, DatasetFile, FileListing
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog

DATASET = "ds_cards"
FILE = "transactions.csv"
ROWS = b"name,email,card\nDana,dana@acme.com,4111111111111111\n"
SCRATCH = ".sage/scratch/datasets/card_data/transactions.csv"
ATTACHED = "public/data/card_data/transactions.csv"


class _RemoteAssets:
    """One Dataset with NO mount — the only shape whose Chat fetch leaves real bytes in scratch.

    A mounted Dataset is symlinked at both doors, so nothing Sage owns is ever on disk to give
    back. `FakeAssetProvider` mounts everything it seeds, which is why this stands beside it
    rather than configuring it.
    """

    def __init__(self, src: Path) -> None:
        self._src = src
        self._asset = Asset(DATASET, "card_data", project="Demo")

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        return [self._asset]

    def list_files(self, asset: Asset) -> FileListing:
        return FileListing([DatasetFile(FILE, len(ROWS))])

    def download_file(self, asset: Asset, rel_path: str, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._src / rel_path, dest)
        return dest.stat().st_size

    def list_taxonomy_labels(self, dataset_id: str) -> list[str]:
        return []


class _MountedAssets(_RemoteAssets):
    """The same Dataset, MOUNTED. Sage copies nothing here — Chat links scratch at the mount and
    the handoff links the app at the mount too — so the two doors have only a link to give back."""

    def __init__(self, src: Path) -> None:
        super().__init__(src)
        self._asset = Asset(DATASET, "card_data", project="Demo", mount_path=str(src))


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path, *, mounted: bool = False) -> Orchestrator:
    src = tmp / "remote"
    src.mkdir(parents=True, exist_ok=True)
    (src / FILE).write_bytes(ROWS)
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        assets=_MountedAssets(src) if mounted else _RemoteAssets(src),
        resources=FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    return orch


def _chip(orch: Orchestrator, thread_id: str, **over) -> dict:
    """The Chat chip for the Dataset file, which fetches it into scratch. `inBuild=True` is the
    Build fork instead, which attaches the file and gives the chip the app's own path."""
    return orch.add_thread_context(thread_id, {
        "kind": "file", "name": FILE, "datasetId": DATASET, "datasetRelPath": FILE,
        "datasetName": "card_data", "resourceId": f"dsfile:{DATASET}:{FILE}",
        "parentId": f"dataset:{DATASET}", "addedBy": "user", **over,
    })


def _handed_off(orch: Orchestrator, thread_id: str, item: dict) -> None:
    """The one thing a confirmed handoff does to a Dataset file chip."""
    orch._promote_chat_file(item, thread_id)


def _scratch(orch: Orchestrator) -> Path:
    return orch._chat_project().record.path / SCRATCH


# --- the leak -------------------------------------------------------------------------------


def test_the_fetch_survives_the_chip_while_the_app_still_carries_it(tmp_path: Path):
    """The hold-back that sets the trap, asserted so the test below cannot pass by accident.

    If closing the chip released the bytes here, Build's door would have nothing left to leak and
    every assertion about it would be vacuous.
    """
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    _handed_off(orch, thread, item)

    orch.remove_thread_context(thread, item["id"])

    assert _scratch(orch).is_file()


def test_removing_the_file_in_build_takes_the_chat_fetch_with_it(tmp_path: Path):
    """The ticket. The chip is closed first, WHILE the file is attached, so the release is refused
    once — and Build's door is then the last one that could ever come back for these bytes."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    _handed_off(orch, thread, item)
    orch.remove_thread_context(thread, item["id"])
    assert _scratch(orch).is_file()

    out = orch.detach_file(ATTACHED)

    assert not _scratch(orch).exists()
    assert out["kept_fetch"] == ""


def test_a_conversation_still_holding_the_file_keeps_its_bytes_and_is_said_out_loud(tmp_path: Path):
    """Releasing is necessary, not sufficient. A live chip still names this fetch, so the bytes
    stay — and the surface the person pressed remove on says so rather than letting them believe
    the removal took the data."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    _handed_off(orch, thread, item)

    out = orch.detach_file(ATTACHED)

    assert _scratch(orch).is_file()
    assert out["kept_fetch"] == "conversation"


def test_a_dataset_that_is_mounted_keeps_its_own_bytes(tmp_path: Path):
    """The Dataset's own bytes are never Sage's to delete (ADR-0011). Only what Sage fetched goes,
    and a mounted Dataset is a symlink at both doors, so there is nothing here to take."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    _handed_off(orch, thread, item)
    orch.remove_thread_context(thread, item["id"])

    orch.detach_file(ATTACHED)

    assert (tmp_path / "remote" / FILE).read_bytes() == ROWS


# --- the other doors onto the same record ---------------------------------------------------


def test_removing_the_whole_folder_takes_the_chat_fetch_too(tmp_path: Path):
    """`detach_folder` drops the same entry by another route, so it leaked the same way. Found in
    review rather than in the ticket, which named only the per-file door."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    _handed_off(orch, thread, item)
    orch.remove_thread_context(thread, item["id"])
    assert _scratch(orch).is_file()

    orch.detach_folder(DATASET, "")

    assert not _scratch(orch).exists()


def test_deleting_the_file_takes_the_chat_fetch_too(tmp_path: Path):
    """The third door. It DESTROYS rather than detaches, so a fetched copy surviving it would be
    the promise broken twice over."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    _handed_off(orch, thread, item)
    orch.remove_thread_context(thread, item["id"])
    assert _scratch(orch).is_file()

    orch.delete_file(ATTACHED)

    assert not _scratch(orch).exists()


def test_a_mounted_dataset_lets_the_chip_go_at_the_first_press(tmp_path: Path):
    """Mounted, Sage copies nothing: Chat links scratch at the mount and the handoff links the app
    at the mount too, so the app is standing on the DATASET rather than on this link.

    `_links_at` resolves both sides and cannot tell those apart, so this used to be refused as
    "the app has it" — and then no Build door could keep that promise, because there is no scratch
    path behind the app's link to find. The link goes at the first press instead, which costs the
    app nothing.
    """
    orch = _orch(tmp_path, mounted=True)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    _handed_off(orch, thread, item)
    assert _scratch(orch).is_symlink()

    out = orch.remove_thread_context(thread, item["id"])

    assert out == {"removed": True, "heldBy": ""}
    assert not _scratch(orch).is_symlink()
    assert (tmp_path / "remote" / FILE).read_bytes() == ROWS      # and the Dataset is untouched
    assert (orch.project().workspace.path / ATTACHED).is_symlink()  # and the app still serves it


def test_a_chip_added_in_build_is_not_mistaken_for_a_fetch(tmp_path: Path):
    """A mention typed in a Build Conversation attaches the file, so its chip carries the app's own
    `public/data/` path and no fetch exists at all. Asked about that path, the holder walk finds
    the same chip on a second Conversation and would answer "conversation" — naming a hold over a
    copy that was never made."""
    orch = _orch(tmp_path)
    keep = orch.create_thread()["id"]
    _chip(orch, keep, inBuild=True)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread, inBuild=True)
    assert item["path"] == ATTACHED

    assert orch.remove_thread_context(thread, item["id"]) == {"removed": True, "heldBy": ""}


# --- what the Chat door says ---------------------------------------------------------------


def test_closing_the_chip_says_the_app_is_what_holds_the_bytes(tmp_path: Path):
    """The other surface. The person closed the chip and the fetch stayed, and the reason is one
    they can act on: the Data panel in Build still carries the file."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    _handed_off(orch, thread, item)

    out = orch.remove_thread_context(thread, item["id"])

    assert out == {"removed": True, "heldBy": "app"}


def test_closing_the_chip_says_when_another_conversation_holds_the_bytes(tmp_path: Path):
    """The hold-back the fetched half already had. A fetch is shared, so the person closing one
    chip is not speaking for the other conversation — and now hears why nothing went."""
    orch = _orch(tmp_path)
    keep = orch.create_thread()["id"]
    _chip(orch, keep)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)

    out = orch.remove_thread_context(thread, item["id"])

    assert out == {"removed": True, "heldBy": "conversation"}
    assert _scratch(orch).is_file()


def test_closing_the_last_chip_still_releases_the_bytes_and_says_nothing(tmp_path: Path):
    """Unchanged, and asserted because the reporting above is new: nothing holds the fetch, so it
    goes, and there is nothing for the chip to announce."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]
    item = _chip(orch, thread)
    assert _scratch(orch).is_file()

    out = orch.remove_thread_context(thread, item["id"])

    assert out == {"removed": True, "heldBy": ""}
    assert not _scratch(orch).exists()


def test_an_unknown_item_is_still_nothing(tmp_path: Path):
    """The route answers 404 off this, so it has to stay falsy rather than become a dict that
    happens to say nothing was removed."""
    orch = _orch(tmp_path)
    thread = orch.create_thread()["id"]

    assert not orch.remove_thread_context(thread, "nope")


# --- the two sentences ----------------------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "chat_fetch_release_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


def _drawn() -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_build_receipt_says_the_fetched_copy_stayed():
    """The receipt's first clause is a promise — "the app's copy is gone" — so the one case where
    bytes are left behind has to be said in the same breath, or the promise does the harm."""
    said = _drawn()["receipts"]["conversation"]
    assert "A copy Sage fetched for a conversation stays until you close the chip there." in said


@needs_node
def test_the_build_receipt_does_not_send_the_reader_to_a_chip_that_is_not_there():
    """The receipt reads the holder's word rather than assuming the likelier half. "app" cannot
    arrive today — the entry that would be it left the record a line earlier — but a sentence that
    hardcoded "close the chip" would be wrong the first time it ever did."""
    said = _drawn()["receipts"]["app"]
    assert "another file in this app is standing on the same data" in said
    assert "chip" not in said


@needs_node
def test_the_build_receipt_says_nothing_when_the_fetch_went():
    """The ordinary removal, which is almost every removal. A clause that always fired would teach
    the reader to stop reading the one that matters."""
    assert "fetched" not in _drawn()["receipts"]["released"]


@needs_node
def test_the_chip_says_which_surface_is_holding_the_bytes():
    """The other door, and the useful half of it: the reason is one the person can act on, because
    the Data panel is where the hold can be released."""
    said = _drawn()["chips"]
    assert "Desk margins still carries the file, so Sage's copy stays until you remove it there." \
        in said["app"]
    assert "Another conversation still has the file, so Sage's copy stays." in said["conversation"]
    assert "stays" not in said["released"]
