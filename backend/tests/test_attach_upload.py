"""Upload / delete / manifest behavior for the builder Context panel.

Uses the FakeAssetProvider's seeded datasets (writable temp mounts): sales_2026 (the resolved
default), customer_pii, app_logs. Uploads land under uploads/."""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from sage.assets.provider import Asset, DatasetFile, FakeAssetProvider, FileListing
from sage.orchestrator.service import (
    AttachTooLarge,
    DataReferenced,
    Orchestrator,
    UploadUnavailable,
)
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


def _orch(tmp: Path, assets=None) -> Orchestrator:
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
                        gateway=object(), catalog=_catalog(), project_id="Sage", assets=assets)


def _dataset(orch: Orchestrator, name: str) -> str:
    return next(a["id"] for a in orch.list_assets() if a["name"] == name)


def _manifest(ws: Path) -> list[dict]:
    return json.loads((ws / ".sage" / "attachments.json").read_text())


def _wind_back_to_pre_274(root: Path) -> None:
    """What the build before #274 left on a volume: entries spelling `source`, and no ledger."""
    for manifest in (root / "apps").glob("*/.sage/attachments.json"):
        rows = json.loads(manifest.read_text())
        for e in rows:
            e.pop("sage_upload", None)
        manifest.write_text(json.dumps(rows, indent=2))
    (root / ".sage" / "uploads.json").unlink(missing_ok=True)


def _door(orch: Orchestrator, rel: str) -> bool:
    attached = orch.project(start_preview=False).attached
    return next(e for e in attached if e.get("dataset_rel_path") == rel)["sage_upload"]


def test_upload_writes_to_default_dataset_mount_and_attaches(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path

    res = orch.upload_file("my data.csv", b"a,b\n1,2\n")

    link = ws / res["path"]
    assert link.is_symlink() and link.read_bytes() == b"a,b\n1,2\n"
    assert "/uploads/" in str(link.resolve())          # bytes live on the dataset mount, not copied
    entry = _manifest(ws)[0]
    assert entry["source"] == "upload" and entry["dataset_rel_path"] == "uploads/my_data.csv"


def test_agents_block_gives_exact_served_path_and_guardrails(tmp_path: Path):
    # The agent must be told the EXACT nested served URL (not a flat /data/<name> it would guess,
    # which 404s to the SPA fallback and reads as null data) and be steered off the git-leaking
    # workaround of copying data into src/.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("my data.csv", b"a,b\n1,2\n")

    agents = (ws / "AGENTS.md").read_text()
    assert "fetch `data/sales_2026/uploads/my_data.csv`" in agents   # nested, base-relative
    # Base-aware fetch by string concatenation, NOT new URL(path, BASE_URL) — BASE_URL is a path,
    # so new URL() throws "Invalid base URL" and crashes the built app on load.
    assert 'import.meta.env.BASE_URL + "data/' in agents
    assert "Invalid base URL" in agents                              # warns off the crashing pattern
    assert "src/" in agents and "gitignored" in agents               # don't-copy-into-git guardrail


def test_manifest_rehydrates_attachments(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    orch.upload_file("secret.csv", b"x")

    # A fresh orchestrator over the same volume rebuilds from the committed manifest.
    proj = _orch(tmp_path).project(start_preview=False)
    assert [e["file"] for e in proj.attached] == ["uploads/secret.csv"]


def test_delete_removes_uploaded_symlink_and_dataset_bytes(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x")
    src = (ws / res["path"]).resolve()
    assert src.is_file()

    orch.delete_file(res["path"])

    assert not (ws / res["path"]).exists() and not src.exists()  # symlink AND dataset bytes gone
    assert _manifest(ws) == []


def test_delete_never_removes_a_pre_existing_dataset_files_bytes(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    ds = _dataset(orch, "sales_2026")
    res = orch.attach_file(ds, "train.csv")
    asset = next(a for a in orch._assets.list_datasets("Sage") if a.id == ds)
    src = Path(asset.mount_path) / "train.csv"
    assert src.is_file()

    orch.delete_file(res["path"])            # delete on a dataset-sourced file is detach-only

    assert src.is_file()                     # the user's original data is preserved


def test_delete_removes_bytes_for_an_uploads_file_reattached_as_dataset(tmp_path: Path):
    # A Sage upload that later shows up as a dataset-browser attachment (source flips to
    # 'dataset') is still Sage's to delete, because the upload wrote that fact down (#274) and the
    # entry carries it. The folder it sits in says nothing either way.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x")
    src = (ws / res["path"]).resolve()
    assert src.is_file()
    for e in orch.project().attached:                        # simulate the re-attach reclassification
        e["source"] = "dataset"

    orch.delete_file(res["path"])

    assert not (ws / res["path"]).exists() and not src.exists()  # symlink AND dataset bytes gone
    assert _manifest(ws) == []


def test_the_unlink_asks_the_ledger_again_rather_than_the_apps_copy(tmp_path: Path):
    """The stamp draws the door; the ledger authorizes the unlink, and is asked at that moment.

    `sage_upload` is a per-app copy refreshed only when a manifest is read, and one Project volume
    can carry two Workspaces (#274). The other one can destroy this file and forget its line while
    this app sits open, and nothing on this side can notice — so a copy that still says yes would
    take the person's replacement file with no undo. The ledger on disk is what both sides share,
    which is why the irreversible half reads it rather than the stamp that drew the control.

    Driven by editing the ledger behind this orchestrator's back, which is precisely what the other
    Workspace is: a writer this process shares a file with and nothing else.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"sage")
    ds = _dataset(orch, "sales_2026")
    mount = Path(next(a for a in orch._assets.list_datasets("Sage") if a.id == ds).mount_path)
    assert next(e for e in orch.project().attached
                if e["path"] == res["path"])["sage_upload"] is True

    (project.record.path / ".sage" / "uploads.json").write_text("[]")   # the other Workspace forgot
    (mount / "uploads" / "d.csv").write_bytes(b"theirs")                # the person wrote their own

    done = orch.delete_file(res["path"])
    assert done["bytes_removed"] is False                              # and it says so
    assert done["bytes_kept"] == "no-record"                           # for the other reason

    assert (mount / "uploads" / "d.csv").read_bytes() == b"theirs"      # their file stays
    assert not (project.workspace.path / res["path"]).exists()          # the detach still happens


def test_a_delete_that_found_nothing_leaves_the_folder_alone(tmp_path: Path):
    """Sage tidies away the folder it emptied, and only that one (#274).

    The prune walks up inside the Dataset removing empty directories, which is right after this act
    took the last file out of one. It is not right when the file had already gone some other way:
    the folder is then one Sage neither created nor emptied, and removing it is a write into the
    person's store on a path where this found nothing at all.
    """
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x")
    (ws / res["path"]).resolve().unlink()                    # the bytes leave some other way

    orch.delete_file(res["path"])

    ds = _dataset(orch, "sales_2026")
    mount = Path(next(a for a in orch._assets.list_datasets("Sage") if a.id == ds).mount_path)
    assert (mount / "uploads").is_dir()


def test_an_upload_into_an_empty_app_does_not_retire_the_migration(tmp_path: Path):
    """The one backfill a project gets belongs to the PROJECT, not to the app on screen (#274).

    The ledger file is what marks the migration done, and an ordinary upload writes that file. Open
    an upgraded project on an app holding nothing, upload one thing there, and a migration that ran
    off the selected app's manifest would never have run at all — while its mark is now set, so no
    later open can. Every pre-#274 upload in every other app loses its door for good, which is the
    dead end the backfill exists to prevent.
    """
    orch = _orch(tmp_path)
    first = orch.project(start_preview=False).workspace.app_id
    orch.upload_file("d.csv", b"sage")
    orch.create_app()                                        # empty, and minting selects it
    _wind_back_to_pre_274(orch.project(start_preview=False).record.path)

    upgraded = _orch(tmp_path, assets=orch._assets)
    upgraded.project(start_preview=False)                    # the one open this project gets
    upgraded.upload_file("e.csv", b"other")                  # writes the ledger, i.e. the mark
    upgraded.select_app(first)

    assert _door(upgraded, "uploads/d.csv") is True


def test_a_clone_that_has_not_built_its_links_keeps_its_doors(tmp_path: Path):
    """Absence of a symlink is not proof the bytes are gone (#274).

    `public/data/` is gitignored and nothing rebuilds it at rehydrate, so a fresh clone of the
    Project has the manifest — which is exactly what it is committed for — and no links at all. A
    migration that read that as "destroyed" would write an empty ledger, set its mark, and retire
    every pre-#274 upload's door on the one shape the manifest exists to survive.
    """
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("d.csv", b"sage")
    root = orch.project(start_preview=False).record.path
    _wind_back_to_pre_274(root)
    shutil.rmtree(ws / "public" / "data")                    # the clone never built them

    upgraded = _orch(tmp_path, assets=orch._assets)

    assert _door(upgraded, "uploads/d.csv") is True


def test_a_ledger_nobody_can_decode_costs_doors_and_not_the_project(tmp_path: Path):
    """An unreadable ledger is answered, never raised (#274).

    A reader that degrades to empty loses a door, which is the safe direction. Raising instead puts
    the failure in the path of merely OPENING the Project — and the read runs on every app switch,
    so nothing would open at all. Bytes that are not UTF-8 are the case that got past a catch named
    for JSON and OS errors only.
    """
    orch = _orch(tmp_path)
    root = orch.project(start_preview=False).record.path
    orch.upload_file("d.csv", b"sage")
    (root / ".sage" / "uploads.json").write_bytes(b"\xff\xfe not utf-8 at all")

    upgraded = _orch(tmp_path, assets=orch._assets)

    assert _door(upgraded, "uploads/d.csv") is False         # the door goes...
    assert upgraded.project(start_preview=False).attached     # ...and the Project still opens


def test_a_second_apps_door_closes_when_another_app_destroys_the_bytes(tmp_path: Path):
    """One app's delete reaches the other app's door, because neither app is the authority (#274).

    An entry is a per-app copy of a claim about bytes both apps share. Attach a Sage upload in a
    second app and its entry records the upload honestly; destroy it from the first and that copy is
    the only thing still saying so. Left to answer on its own it offers a no-undo destroy door onto
    whatever the person has written at that path since — the folder-name bug with the folder swapped
    for a path, and a delete in one app cannot reach into another's manifest to stop it.

    So the copy is not the authority: the ledger is, and the manifest read on every app switch is
    where the copy is brought back into line. Asserted through the door and then through the delete
    behind it, because a closed door that still destroys on the API is the half that matters.
    """
    orch = _orch(tmp_path)
    first = orch.project(start_preview=False).workspace.app_id
    orch.upload_file("d.csv", b"sage")
    ds = _dataset(orch, "sales_2026")
    mount = Path(next(a for a in orch._assets.list_datasets("Sage") if a.id == ds).mount_path)
    second = orch.create_app()["id"]                         # minting selects it
    in_second = orch.attach_file(ds, "uploads/d.csv")["path"]
    assert next(e for e in orch.project().attached
                if e["path"] == in_second)["sage_upload"] is True

    orch.select_app(first)
    orch.delete_file(next(e["path"] for e in orch.project().attached
                          if e.get("dataset_rel_path") == "uploads/d.csv"))
    (mount / "uploads").mkdir(parents=True, exist_ok=True)   # the delete pruned the empty folder
    (mount / "uploads" / "d.csv").write_bytes(b"theirs")     # the person writes their own, later
    orch.select_app(second)

    entry = next(e for e in orch.project().attached if e["path"] == in_second)
    assert entry["sage_upload"] is False                     # the door closes...
    orch.delete_file(entry["path"])
    assert (mount / "uploads" / "d.csv").read_bytes() == b"theirs"   # ...and their file stays


def test_a_second_apps_record_does_not_bring_back_a_forgotten_upload(tmp_path: Path):
    """A destroyed upload stays forgotten, even though a second app still spells it (#274).

    The record of who wrote the bytes is per PROJECT and the manifest that feeds it is per APP, so
    the two do not converge on their own: uploading one name in two apps writes `source: "upload"`
    in both manifests, and the delete in one can only reach its own. Rehydrate runs on every app
    switch, so a backfill that believed the second manifest would write the line back for bytes
    nothing holds any more — and hand their door to whoever writes at that path next, which is #274
    with the folder swapped for a path.

    Only never asking twice settles it, which is what the ledger FILE marks. The obvious guard —
    backfill only what the app's symlink still resolves — cannot, and this is the ordering that
    shows why: by the time the second app is opened, the person has written their own file at that
    path and the symlink resolves perfectly. The two files differ in nothing the check can read.

    Driven over an upgraded project, because that is the only shape where the backfill runs at all.
    """
    orch = _orch(tmp_path)
    first = orch.project(start_preview=False).workspace.app_id
    orch.upload_file("d.csv", b"sage")
    second = orch.create_app()["id"]                         # minting selects it
    orch.upload_file("d.csv", b"sage")                       # same Dataset path, second manifest
    ds = _dataset(orch, "sales_2026")
    mount = Path(next(a for a in orch._assets.list_datasets("Sage") if a.id == ds).mount_path)

    # Wind the volume back to what the old build left: entries spelling `source`, and no ledger.
    root = orch.project(start_preview=False).record.path
    for manifest in (root / "apps").glob("*/.sage/attachments.json"):
        rows = json.loads(manifest.read_text())
        for e in rows:
            e.pop("sage_upload", None)
        manifest.write_text(json.dumps(rows, indent=2))
    (root / ".sage" / "uploads.json").unlink()

    orch = _orch(tmp_path, assets=orch._assets)
    orch.select_app(first)                                   # the one backfill this project gets
    orch.delete_file(next(e["path"] for e in orch.project().attached
                          if e.get("dataset_rel_path") == "uploads/d.csv"))
    assert not (mount / "uploads" / "d.csv").exists()        # destroyed, and forgotten with it
    (mount / "uploads").mkdir(parents=True, exist_ok=True)   # the delete pruned the empty folder
    (mount / "uploads" / "d.csv").write_bytes(b"theirs")     # the person writes their own, later

    orch.select_app(second)                                  # rehydrates the manifest that still says it

    entry = next(e for e in orch.project().attached
                 if e.get("dataset_rel_path") == "uploads/d.csv")
    assert entry["sage_upload"] is False                     # no door on a file Sage did not write
    orch.delete_file(entry["path"])
    assert (mount / "uploads" / "d.csv").read_bytes() == b"theirs"


def test_an_upgrade_keeps_the_door_on_an_upload_it_can_still_read(tmp_path: Path):
    """A project that predates #274 has no ledger, and one kind of entry can still rebuild it.

    `source: "upload"` on a live entry IS a record that Sage wrote those bytes — the same fact the
    ledger keeps, in the only place the old build kept it. Read at rehydrate it survives the detach
    that deletes the entry; unread, an upgrade would turn a recoverable door into a dead end, with
    Sage's own bytes in the Dataset and nothing in Sage able to remove them.

    Paired against the two entries that must NOT be rebuilt. The person's own file under their own
    `uploads/` records no upload, which is exactly what #274 is about. And `gone.csv` DOES spell the
    upload, but its bytes left the Dataset before the upgrade — a record naming a path nothing holds
    is a record waiting for the next writer there, so the pass takes only what the app's own symlink
    still resolves. Nothing is invented for any of the three.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace.path
    ds = _dataset(orch, "sales_2026")
    orch.upload_file("d.csv", b"x")
    orch.upload_file("gone.csv", b"x")
    mount = Path(next(a for a in orch._assets.list_datasets("Sage") if a.id == ds).mount_path)
    (mount / "uploads" / "gone.csv").unlink()                # it left the Dataset some other way
    (mount / "uploads" / "budget.csv").write_bytes(b"theirs")
    orch.attach_file(ds, "uploads/budget.csv")

    # Wind the volume back to what the old build left: entries spelling `source`, and no ledger.
    manifest = _manifest(ws)
    for e in manifest:
        e.pop("sage_upload", None)
    (ws / ".sage" / "attachments.json").write_text(json.dumps(manifest, indent=2))
    (project.record.path / ".sage" / "uploads.json").unlink()

    upgraded = _orch(tmp_path, assets=orch._assets)
    upgraded.project(start_preview=False)                    # rehydrates, and reads the old record

    # `gone.csv` never leaves its entry, because there is nothing on the mount to attach again; the
    # restamp is what answers for it, which is the same record the other two are re-read against.
    doors = {"uploads/gone.csv": next(e for e in upgraded.project().attached
                                      if e.get("dataset_rel_path") == "uploads/gone.csv")["sage_upload"]}
    for rel in ("uploads/d.csv", "uploads/budget.csv"):
        upgraded.detach_file(next(e["path"] for e in upgraded.project().attached
                                  if e.get("dataset_rel_path") == rel))
        entry = upgraded.attach_file(ds, rel)
        doors[rel] = next(e for e in upgraded.project().attached
                          if e["path"] == entry["path"])["sage_upload"]
    assert doors == {"uploads/d.csv": True,
                     "uploads/budget.csv": False, "uploads/gone.csv": False}


def test_an_unreadable_ledger_costs_one_door_and_not_every_door(tmp_path: Path):
    """A ledger Sage cannot parse is never written over, and never fails the upload (#274).

    It is committed at the Project root, so two Builders uploading can leave conflict markers in it
    in the ordinary course. Read as empty and republished, one upload would take every other file's
    destroy door with it — silently, permanently, and nowhere near the act that did it. So the note
    is refused, and refused quietly: bookkeeping for a door must not turn a good upload into a
    rolled-back one when the Dataset mount was writable all along.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ledger = project.record.path / ".sage" / "uploads.json"
    orch.upload_file("a.csv", b"a")
    orch.upload_file("b.csv", b"b")
    good = ledger.read_text()
    ledger.write_text("<<<<<<< HEAD\n" + good)               # what a merge leaves behind

    res = orch.upload_file("c.csv", b"c")                    # the upload still lands

    assert (project.workspace.path / res["path"]).is_symlink()
    # And draws no destroy door, because the ledger the unlink will ask does not name it. Stamped
    # True on the strength of Sage having written the bytes, this would offer a no-undo control
    # that detaches and reports a delete.
    assert _door(orch, "uploads/c.csv") is False
    assert ledger.read_text().startswith("<<<<<<< HEAD")     # and takes nothing else down with it
    ledger.write_text(good)

    # The pair, over one fixture: c.csv paid the cost this test is named for, a.csv and b.csv did
    # not. Asserting only the survivors would pass on a build that quietly rewrote the ledger past
    # the conflict markers, which is the failure being guarded against. Driven on this orchestrator
    # rather than a fresh one on purpose — a reload rehydrates the manifest, and `c.csv` is still
    # spelled `source: "upload"` there, so the backfill would hand its door back.
    ds = _dataset(orch, "sales_2026")
    doors = {}
    for name in ("a.csv", "b.csv", "c.csv"):
        rel = f"uploads/{name}"
        orch.detach_file(next(e["path"] for e in orch.project().attached
                              if e.get("dataset_rel_path") == rel))
        entry = orch.attach_file(ds, rel)
        doors[name] = next(e for e in orch.project().attached
                           if e["path"] == entry["path"])["sage_upload"]
    assert doors == {"a.csv": True, "b.csv": True, "c.csv": False}


@pytest.mark.parametrize("unreachable", ["unlisted", "unmounted"])
def test_a_delete_that_could_not_reach_the_bytes_keeps_their_door(tmp_path: Path, unreachable: str):
    """A Dataset that is not there to be emptied leaves its file's door standing (#274).

    `_delete_upload_bytes` gives up quietly on a Dataset it cannot reach, and the symlink goes
    either way. What must not go with it is the ledger line: Sage's bytes are still in that Dataset,
    and forgetting them here would throw away the only record that grants their door — a loss
    nothing afterwards can see, which is why it is pinned rather than left to the docstring.

    Both shapes of unreachable, because they fail different checks and only one is obvious. A
    Dataset the platform still LISTS, carrying a `mount_path` this container never mounted, gets
    past the listing check and arrives at a path where no file is found — which is indistinguishable
    from bytes that are genuinely gone unless the MOUNT is what gets asked. `is_file()` on the file
    answers the same for both; `is_dir()` on the mount root separates them.

    The third shape, a mount point that exists with no Dataset behind it, is NOT here. It reads as
    a Dataset whose file is gone, deliberately — see the test below.
    """
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x")
    src = (ws / res["path"]).resolve()
    ds = _dataset(orch, "sales_2026")
    listed = orch._assets.assets
    if unreachable == "unlisted":
        orch._assets.assets = [a for a in listed if a.id != ds]
    else:
        orch._assets.assets = [replace(a, mount_path=str(tmp_path / "not-mounted")) if a.id == ds
                               else a for a in listed]

    done = orch.delete_file(res["path"])
    assert done["bytes_removed"] is False
    # And says WHY it kept them. Sage holds the record here and kept it on purpose; told "no record"
    # the person would go looking for a door that is coming back the moment the Dataset does.
    assert done["bytes_kept"] == "unreachable"

    assert not (ws / res["path"]).exists()                   # the symlink goes
    assert src.is_file()                                     # the bytes it could not reach do not
    orch._assets.assets = listed
    reattached = orch.attach_file(ds, "uploads/d.csv")
    assert next(e for e in orch.project().attached
                if e["path"] == reattached["path"])["sage_upload"] is True


def test_the_destroy_door_reads_the_record_not_the_folder_name(tmp_path: Path):
    """Two files under one Dataset's `uploads/` — one Sage wrote, one the person did — through one
    door, which must answer them opposite ways (#274).

    Both arrive as `source: "dataset"` with a `dataset_rel_path` under `uploads/`, so the folder
    name cannot separate them. What can is the note `upload_file` leaves behind: the real
    detach-then-re-attach is driven here rather than simulated, because it is the round trip that
    has to carry that note across a manifest entry being deleted and rewritten.

    Asserted as one pair over one fixture. Two tests each asserting one half would both pass on a
    door that is never drawn at all (`backend/tests/README.md`).

    budget.csv is also the shape every entry written before #274 has — `source: "dataset"`, under
    `uploads/`, recording no upload — so the answer asserted for it is the stated answer for those
    too: no door, whoever wrote the bytes.
    """
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    ds = _dataset(orch, "sales_2026")
    mount = Path(next(a for a in orch._assets.list_datasets("Sage") if a.id == ds).mount_path)

    theirs = mount / "uploads" / "budget.csv"                 # a folder they happened to name that
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_bytes(b"theirs")

    ours = Path(orch.upload_file("margins.csv", b"ours").get("path"))
    ours_bytes = (ws / ours).resolve()
    orch.detach_file(ours.as_posix())                         # the entry that recorded the upload
    reattached = orch.attach_file(ds, "uploads/margins.csv")  # ...is gone, and written again here
    attached_theirs = orch.attach_file(ds, "uploads/budget.csv")

    entries = {e["path"]: e for e in orch.project().attached}
    assert entries[reattached["path"]]["source"] == "dataset"        # same shape at the door
    assert entries[attached_theirs["path"]]["source"] == "dataset"

    orch.delete_file(reattached["path"])
    orch.delete_file(attached_theirs["path"])

    assert not ours_bytes.exists()                            # Sage's own bytes: destroyed
    assert theirs.read_bytes() == b"theirs"                   # theirs: never touched


def test_delete_blocked_while_app_fetches_the_file(tmp_path: Path):
    # Deleting data the dashboard fetches at runtime would orphan that code — block it (Detach stays).
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
    (ws / "src" / "App.tsx").write_text('fetch(import.meta.env.BASE_URL + "data/sales_2026/uploads/d.csv")')

    with pytest.raises(DataReferenced) as ei:
        orch.delete_file(res["path"])

    assert ei.value.refs and not ei.value.copies
    assert (ws / res["path"]).exists()               # nothing removed — the block ran first


def test_delete_blocked_when_data_was_copied_into_src(tmp_path: Path):
    # A copied file (same basename under the app tree) is the git-leak — and why delete "does nothing".
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
    (ws / "src" / "data").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "data" / "d.csv").write_text("a,b\n1,2\n")     # agent copied it into src/

    with pytest.raises(DataReferenced) as ei:
        orch.delete_file(res["path"])

    assert ei.value.copies == ["src/data/d.csv"]


def test_delete_allowed_when_app_does_not_use_the_file(tmp_path: Path):
    # The template App.tsx is a placeholder that never references the upload -> delete proceeds.
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"x")

    orch.delete_file(res["path"])                    # no DataReferenced

    assert not (ws / res["path"]).exists() and _manifest(ws) == []


def test_data_usage_flags_inlined_bytes_as_a_copy(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    body = b"name,score\n" + b"".join(b"row%d,%d\n" % (i, i) for i in range(20))
    res = orch.upload_file("d.csv", body)
    (proj.workspace.path / "src" / "rows.ts").write_text("export const RAW = `" + body.decode() + "`;")

    entry = next(e for e in proj.attached if e["path"] == res["path"])
    usage = orch._data_usage(proj, entry)
    assert "src/rows.ts" in usage["copies"]


def test_detect_leaks_finds_copied_data_for_the_commit_backstop(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.upload_file("sales.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "sales.csv").write_text("a,b\n1,2\n")   # agent copied it into src/

    assert orch._detect_leaks(proj) == [("sales.csv", ["src/sales.csv"])]
    # The exclude is handed to git, which runs at the Project root, so it names the app's directory.
    assert orch._leaked_copy_paths(proj) == [f"apps/{proj.workspace.app_id}/src/sales.csv"]


def test_no_leak_when_app_only_fetches_from_data(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    orch.upload_file("sales.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "App.tsx").write_text('fetch("data/sales_2026/uploads/sales.csv")')

    assert orch._detect_leaks(proj) == []          # a fetch is the intended pattern, not a leak
    assert orch._leaked_copy_paths(proj) == []


def test_detach_removes_a_leaked_copy_so_it_cant_reach_git(tmp_path: Path):
    # The core hole: while attached, a copy in src/ is kept out of commits by _detect_leaks. Detaching
    # forgets the entry, so the commit backstop stops covering it — detach must delete the copy itself.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "data").mkdir(parents=True, exist_ok=True)
    (proj.workspace.path / "src" / "data" / "d.csv").write_text("a,b\n1,2\n")   # agent copied it into src/

    out = orch.detach_file(res["path"])

    assert out["removed_copies"] == ["src/data/d.csv"]
    assert not (proj.workspace.path / "src" / "data" / "d.csv").exists()   # leaked bytes gone from the tree
    assert orch._leaked_copy_paths(proj) == []                            # nothing left for the backstop
    assert _manifest(proj.workspace.path) == []


def test_detach_keeps_a_source_file_that_only_shares_the_name(tmp_path: Path):
    # _data_usage calls any app file with the attachment's BASENAME a copy, and reads neither — right
    # for spotting a leaked CSV cheaply, not enough to delete on. An upload named App.tsx must not
    # take src/App.tsx with it, or the app stops building over a name collision nobody chose.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("App.tsx", b"export default function FromTheDataset() {}")
    theirs = (proj.workspace.path / "src" / "App.tsx").read_text()

    out = orch.detach_file(res["path"])

    assert out["removed_copies"] == []
    assert (proj.workspace.path / "src" / "App.tsx").read_text() == theirs


def test_detach_names_a_same_named_copy_it_could_not_prove_and_left(tmp_path: Path):
    # The other half of the trade _is_leaked_copy makes: what it cannot prove stays, and the entry
    # leaves the record either way — so _leaked_copy_paths stops covering the file and it would
    # otherwise reach the next save with nothing on screen having mentioned it.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "d.csv").write_text("a,b\n")   # the first row only, not the file

    out = orch.detach_file(res["path"])

    assert out["removed_copies"] == []
    assert out["kept_copies"] == ["src/d.csv"]
    assert (proj.workspace.path / "src" / "d.csv").exists()


def test_detach_reports_still_referenced_files_without_deleting_source(tmp_path: Path):
    # A fetch (or bytes inlined into a code file) is app logic, not a raw-file copy: detach must NOT
    # delete the source, but must report it so the UI can warn and offer the agent cleanup.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"x")
    (proj.workspace.path / "src" / "App.tsx").write_text('fetch("data/sales_2026/uploads/d.csv")')

    out = orch.detach_file(res["path"])

    assert out["removed_copies"] == []
    assert "src/App.tsx" in out["refs"]
    assert (proj.workspace.path / "src" / "App.tsx").exists()   # app code left untouched


def test_detach_reports_a_hardcoded_sample_of_the_file_not_just_a_full_copy(tmp_path: Path):
    # The agent hardcoded the prompt PREVIEW (leading rows) into the app instead of fetching the
    # file. That's a partial copy: the app renders a stale sample, so detach must still report it.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    rows = "event_id,patient,outcome\n" + "\n".join(
        f"EV{i:04d},patient_{i:04d},outcome_value_{i}" for i in range(200))  # 200-row dataset
    res = orch.upload_file("big.csv", rows.encode())
    sample = "\n".join(rows.splitlines()[:6])                              # header + first 5 rows only
    (proj.workspace.path / "src" / "App.tsx").write_text(f"const data = `{sample}`;")

    out = orch.detach_file(res["path"])

    assert out["removed_copies"] == []                    # inlined into code -> source left in place
    assert "src/App.tsx" in out["refs"]                   # but reported so the UI warns
    assert (proj.workspace.path / "src" / "App.tsx").exists()


def test_read_file_previews_an_attached_symlink_but_still_blocks_escapes(tmp_path: Path, monkeypatch):
    # The attachment is a symlink under public/data/ pointing at the dataset mount (outside the
    # workspace); the file-open endpoint must preview it read-only, while a real escape still 400s.
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"a,b\n1,2\n")
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    ok = client.get("/api/project/file", params={"path": res["path"]})
    assert ok.status_code == 200 and ok.json()["content"] == "a,b\n1,2\n"

    escape = client.get("/api/project/file", params={"path": "../../../../../../etc/passwd"})
    assert escape.status_code == 400            # not a known attachment -> resolver rejects the escape


def test_resolve_mentions_only_honors_known_attachments(tmp_path: Path):
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"x")

    assert [m["path"] for m in orch._resolve_mentions(proj, [res["path"]])] == [res["path"]]
    assert orch._resolve_mentions(proj, ["public/data/not-attached.csv"]) is None  # unknown -> ignored
    assert orch._resolve_mentions(proj, None) is None


def test_a_mention_the_turn_cannot_use_is_reported_rather_than_dropped(tmp_path: Path):
    # The picker offers more than a build can honor — Chat's own uploads live at the Project root, and
    # a Resource is usable only by the app holding a Binding for it — and both used to be skipped in
    # silence. That silence is how a turn builds from the wrong file while the right one sits in the
    # panel, so every mention the turn drops now says so, and says what to do about it.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    res = orch.upload_file("d.csv", b"x")

    used = orch._resolve_mentions(proj, [res["path"]])
    # It WAS used: nothing to say, and nothing to offer a button for either.
    assert orch._unusable_mentions(proj, used, [res["path"]], None) == ("", [])

    # Named and explained, and no longer sent anywhere: the button beside it attaches the file and
    # sends the request again, so the directions moved onto the card that has none (#213).
    chat, rows = orch._unusable_mentions(proj, None, [".sage/scratch/events.csv"], None)
    assert "@events.csv" in chat and "in Chat" in chat
    assert [r["kind"] for r in rows] == ["file"]

    plain, _ = orch._unusable_mentions(proj, None, ["public/data/gone.csv"], None)
    assert "@gone.csv" in plain and "Attach it to this app" in plain

    ref = {"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}
    unbound, rows = orch._unusable_mentions(proj, None, None, [ref])
    # The destination is the Built App's own surface, which is the only place the bind lives
    # (ADR-0021, #144) — the Resources panel offered it until then and does not now. It is the row's
    # button that goes there since #213, so the row is what says which app.
    assert "@Warehouse" in unbound
    assert [(r["kind"], r["app"]) for r in rows] == [(KIND_DATA_SOURCE, "Draft app 1")]
    # And a Resource this app IS bound to is not reported — the report reads the same Binding list the
    # turn honors, so a bound Resource must never come back as one the turn refused.
    proj.workspace.update_bindings(
        lambda entries: [*entries, Binding(KIND_DATA_SOURCE, "ds1", "Warehouse", "Warehouse").to_dict()])
    assert orch._unusable_mentions(proj, None, None, [ref]) == ("", [])


def test_upload_is_unavailable_when_no_writable_dataset_exists(tmp_path: Path):
    # UploadUnavailable fires only when there's genuinely nowhere writable to store bytes.
    prov = FakeAssetProvider()
    prov.assets = []
    orch = _orch(tmp_path, assets=prov)
    orch.project(start_preview=False)

    with pytest.raises(UploadUnavailable):
        orch.upload_file("x.csv", b"x")


def test_upload_to_a_picked_dataset_lands_under_uploads(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    pii = _dataset(orch, "customer_pii")

    res = orch.upload_file("note.csv", b"x", dataset_id=pii)

    assert res["dataset"] == "customer_pii"
    assert _manifest(ws)[0]["dataset_rel_path"] == "uploads/note.csv"


# --- typed descriptors: the agent gets each file's SHAPE, never its bytes ------------------------

def test_mentions_hand_the_agent_the_workspace_path_not_the_mount_path(tmp_path: Path):
    """The regression that matters: OpenCode's read tool hangs forever on absolute /mnt/data paths
    (outside its project root), while the in-root public/data/ symlink reads fine."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")
    path = project.attached[0]["path"]

    out = orch._resolve_mentions(project, [path])

    assert len(out) == 1
    assert out[0]["path"] == path == "public/data/sales_2026/uploads/q3.csv"
    assert out[0]["name"] == "q3.csv"
    assert not any("/mnt/" in str(v) for v in out[0].values())


def test_descriptor_is_cached_in_the_manifest_so_the_mount_is_read_once(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")

    d = _manifest(ws)[0]["descriptor"]
    assert d["kind"] == "tabular"
    # The shape is cached. The rows are not: `detail` carries three verbatim rows and this file is
    # committed, so it is withheld and re-read per @mention instead (#237).
    assert "2 columns" in d["summary"]
    assert d["detail"] == "" and d["withheld"]

    # Second use must not re-read the mount — the cached descriptor is returned verbatim.
    project = orch.project()
    project.attached[0]["descriptor"]["summary"] = "sentinel"
    assert orch._resolve_mentions(project, [project.attached[0]["path"]])[0]["summary"] == "sentinel"


def test_agents_md_lists_each_attachment_with_a_one_line_shape(tmp_path: Path):
    """The AGENTS.md block is re-read every turn, so it carries the one-line summary only — the full
    descriptor is inlined by send_prompt for @mentioned files alone."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")

    block = (ws / "AGENTS.md").read_text()
    line = next(ln for ln in block.splitlines() if "q3.csv" in ln and ln.startswith("- disk"))
    assert "CSV" in line
    assert "fetch `data/sales_2026/uploads/q3.csv`" in line


def test_the_collapsed_folder_line_admits_the_placeholder_and_opens_the_one_door(tmp_path: Path):
    """Above the threshold the block stops naming files and hands over a folder plus `<name>`.
    That is right for the prompt budget and wrong for the agent: grep is banned three lines up and
    would find nothing anyway (gitignored, symlinked), so the only move left is the listing it is
    also told not to do. Live, that turn spent itself on five identical `ls -R` calls."""
    from sage.orchestrator.service import FOLDER_COLLAPSE_THRESHOLD

    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    for i in range(FOLDER_COLLAPSE_THRESHOLD + 1):
        orch.upload_file(f"part_{i:02d}.csv", b"region,revenue\nwest,10\n")

    line = next(ln for ln in (ws / "AGENTS.md").read_text().splitlines()
                if ln.startswith(f"- {FOLDER_COLLAPSE_THRESHOLD + 1} files in "))

    assert "`<name>` is a placeholder, not a file name" in line
    assert "list that folder to read the real names." in line
    assert "Grep will not find them." in line


def test_the_block_says_what_to_do_when_a_path_it_names_does_not_open(tmp_path: Path):
    """The grep ban is correct and was the whole instruction. A ban with no exit is what turns one
    wrong path into a ten-minute turn: the agent may not search, may not invent, and was never told
    that reporting the failure is the right answer."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\n")

    block = (ws / "AGENTS.md").read_text()

    assert "If a path above does not open, say which one and stop" in block
    assert "do not run the same look-up again expecting a different answer" in block


def test_a_binary_attachment_never_puts_decoded_bytes_in_front_of_the_agent(tmp_path: Path):
    """A PDF used to be utf-8-decoded into the prompt as a 'SCHEMA SAMPLE' of mojibake."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("report.pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n")

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert "�" not in out["detail"] and "�" not in out["summary"]
    assert project.attached[0]["descriptor"]["kind"] not in ("tabular", "text")


def _png_bytes(px: int = 40) -> bytes:
    """A REAL png. Stub headers no longer suffice: fit_image verifies the pixels decode, because a
    corrupt image that sails through would fail at the provider after the UI promised the agent
    could see it. Random pixels so the file can't compress away when we need it large."""
    import io as _io
    import os as _os

    from PIL import Image
    buf = _io.BytesIO()
    Image.frombytes("RGB", (px, px), _os.urandom(px * px * 3)).save(buf, "PNG")
    return buf.getvalue()


def test_an_attached_image_is_inlined_as_a_data_uri_for_the_agent(tmp_path: Path):
    """Images are the one type where the pixels ARE the shape, so the descriptor isn't enough.
    A data: URI is required — OpenCode emits malformed media for every file-path form."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("shot.png", _png_bytes())

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert out["image_uri"].startswith("data:image/png;base64,")
    assert out["summary"] == "PNG image — 40x40"       # descriptor still travels alongside


def test_an_oversized_image_is_shrunk_so_the_agent_can_still_see_it(tmp_path: Path):
    """Refusing an oversized image costs the agent the whole picture, and a phone photo or hi-DPI
    screenshot is exactly what users attach. Verified live: gpt-5.4 read the correct quadrants off
    the shrunk copy of a file that the old hard cap rejected outright."""
    import base64 as _b64

    from sage.orchestrator import service as svc

    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    big = _png_bytes(1400)
    assert len(big) > svc._MAX_INLINE_IMAGE_BYTES
    orch.upload_file("huge.png", big)

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert out["image_uri"].startswith("data:image/")
    inlined = _b64.b64decode(out["image_uri"].split(",", 1)[1])
    assert len(inlined) <= svc._MAX_INLINE_IMAGE_BYTES
    assert len(inlined) < len(big)                     # actually shrunk, not passed through


def test_an_undecodable_image_reaches_the_agent_with_no_pixels(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
                     + (900).to_bytes(4, "big") * 2 + b"junk" * 50)

    out = orch._resolve_mentions(project, [project.attached[0]["path"]])[0]

    assert out["image_uri"] is None                    # send_prompt turns this into the "NOT shown" note


def test_a_tabular_attachment_carries_no_image_uri(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"a,b\n1,2\n")

    assert "image_uri" not in orch._resolve_mentions(project, [project.attached[0]["path"]])[0]


def test_a_failed_upload_leaves_no_orphan_bytes_on_the_dataset_mount(tmp_path: Path, monkeypatch):
    """The bytes land on the mount (outside git) before anything records them. Without a rollback a
    mid-upload failure strands data on a shared mount that detach/delete can't even see."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    asset = next(a for a in orch._assets.list_datasets("Sage") if a.name == "sales_2026")
    monkeypatch.setattr(Orchestrator, "_write_agents_data_block",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only workspace")))

    with pytest.raises(OSError):
        orch.upload_file("q3.csv", b"a,b\n1,2\n")

    assert not (Path(asset.mount_path) / "uploads" / "q3.csv").exists()   # bytes undone
    # Symlink undone and its dirs pruned back to public/data/, which detach leaves standing too.
    assert not (project.workspace.path / "public" / "data" / "sales_2026").exists()
    assert project.attached == []
    assert _manifest(project.workspace.path) == []


def test_a_failed_re_upload_does_not_delete_the_bytes_that_were_already_there(tmp_path: Path,
                                                                              monkeypatch):
    """Overwriting a same-named upload already destroyed the old bytes — deleting the file on
    rollback would turn one lost version into no file at all."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"original\n")
    dest = next(a for a in orch._assets.list_datasets("Sage")
                if a.name == "sales_2026").mount_path
    monkeypatch.setattr(Orchestrator, "_write_agents_data_block",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(OSError):
        orch.upload_file("q3.csv", b"replacement\n")

    assert (Path(dest) / "uploads" / "q3.csv").exists()   # kept, not compounded into a deletion


def test_agents_block_warns_that_search_cannot_see_attached_files(tmp_path: Path):
    """Live failure: the agent grepped an attached CSV for a value on line 619, got no matches, and
    answered "not found". ripgrep skips gitignored paths and won't follow symlinks — attachments are
    both — so search silently returns nothing. That's a wrong answer, not an error."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    orch.upload_file("q3.csv", b"region,note\nZZ,ORION-7734\n")

    agents = (ws / "AGENTS.md").read_text()
    assert "read tool on its exact disk path" in agents
    assert "Do NOT use grep/search" in agents.replace("\n", " ")
    assert "finds nothing here proves nothing" in agents


def test_an_attached_image_records_whether_the_agent_will_see_it(tmp_path: Path):
    """The Data panel needs this: a user who attaches an image the agent can't see currently gets
    no signal at all — the agent just answers "unknown" and nothing explains why."""
    from PIL import Image

    orch = _orch(tmp_path, assets=FakeAssetProvider())
    ws = orch.project(start_preview=False).workspace.path
    good = tmp_path / "ok.png"
    Image.new("RGB", (40, 40), (230, 30, 30)).save(good, "PNG")

    res = orch.upload_file("ok.png", good.read_bytes())

    assert res["descriptor"]["kind"] == "image"
    assert res["descriptor"]["shown"] is True          # returned inline, so the panel can flag now
    assert _manifest(ws)[0]["descriptor"]["shown"] is True


def test_an_undecodable_image_is_flagged_as_unseen_for_the_data_panel(tmp_path: Path):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    orch.project(start_preview=False)
    broken = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (900).to_bytes(4, "big") * 2 + b"junk" * 50

    res = orch.upload_file("broken.png", broken)

    assert res["descriptor"]["kind"] == "image"
    assert res["descriptor"]["shown"] is False


def test_upload_during_a_turn_is_not_mistaken_for_the_agent_writing(tmp_path: Path):
    """Uploading a file mid-turn writes AGENTS.md, .gitignore and a public/data/ symlink — all
    inside the snapshotted working tree, none of them the agent's doing. Before the fix, the turn's
    end-of-run tree comparison read those as "the agent wrote code", which on a gated (Plan) turn
    meant a false `gate violated` AND a discard_changes() that deleted the fresh upload. The upload
    path must move the running turn's baseline forward instead."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)

    # Stand in for a turn in flight: the baseline build_stream would have taken at its start.
    project.turn_tree_baseline = project.snapshot.working_tree_hash()
    before = project.turn_tree_baseline

    orch.upload_file("mid_turn.csv", b"a,b\n1,2\n")

    assert project.snapshot.working_tree_hash() != before      # the upload really did change the tree
    assert project.turn_tree_baseline == project.snapshot.working_tree_hash()  # ...and was absorbed


def test_upload_outside_a_turn_leaves_the_baseline_alone(tmp_path: Path):
    """No turn running means no baseline to move — the rebaseline hook must stay a no-op rather
    than seed one, or the next turn would start by comparing against a stale hash."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    assert project.turn_tree_baseline == ""

    orch.upload_file("idle.csv", b"a,b\n1,2\n")

    assert project.turn_tree_baseline == ""


def test_a_turn_that_deletes_an_attachment_gets_it_put_back(tmp_path: Path):
    # #37, live 2026-08-24: told to "remove everything you have built", the agent took the user's
    # uploaded CSV with it. The file left the @ menu and they had to attach it again to say the same
    # sentence. Neither obvious enforcement point can carry this — the shim gates by tool NAME and a
    # bash `rm` is not a write tool, and the turn snapshot stages with `add -A`, which honours the
    # .gitignore attach_file writes `public/data/` into. project.attached is process memory, which no
    # tool reaches, so that is what the repair rebuilds from.
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")
    link = project.workspace.path / project.attached[0]["path"]
    assert link.is_symlink()

    link.unlink()                        # what the turn did
    project.workspace.attachments_path.unlink()
    orch._restore_attachments()

    assert link.is_symlink()             # the file is back in the @ menu
    assert link.read_bytes() == b"region,revenue\nwest,10\neast,20\n"   # and points at the real rows
    assert [e["path"] for e in _manifest(project.workspace.path)] == [project.attached[0]["path"]]
    ev = [e for e in project.workspace.read_history() if e["type"] == "attachments-restored"]
    assert len(ev) == 1 and ev[0]["paths"] == [project.attached[0]["path"]]   # said out loud, not silent


def test_a_turn_that_deletes_nothing_restores_nothing_and_says_nothing(tmp_path: Path):
    # The repair runs at the end of EVERY turn, so a quiet turn must stay quiet: no warning card for
    # a build that behaved.
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    project = orch.project(start_preview=False)
    orch.upload_file("q3.csv", b"region,revenue\nwest,10\neast,20\n")

    orch._restore_attachments()

    assert not [e for e in project.workspace.read_history() if e["type"] == "attachments-restored"]


def test_scratch_upload_does_not_need_a_dataset(tmp_path: Path):
    orch = _orch(tmp_path)
    # Scratch is Chat's, so it lives with the Project rather than inside the Built App.
    root = orch.project(start_preview=False).record.path
    res = orch.upload_scratch("my data.csv", b"a,b\n1,2\n")
    assert res["source"] == "scratch"
    assert res["path"] == ".sage/scratch/my_data.csv"
    assert (root / res["path"]).read_bytes() == b"a,b\n1,2\n"
    gi = (root / ".gitignore").read_text()
    assert ".sage/scratch/" in gi
    assert "scratch" in {e["source"] for e in orch.project(start_preview=False).status()["scratch"]}


def test_promote_scratch_copies_onto_a_dataset_and_drops_the_scratch_copy(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    scratch = orch.upload_scratch("note.csv", b"x")
    ds = orch.default_dataset_id()
    res = orch.promote_scratch_to_dataset(scratch["path"], ds)
    assert not (ws / scratch["path"]).exists()
    assert res["path"].startswith("public/data/")
    assert (ws / res["path"]).read_bytes() == b"x"


def test_list_asset_files_accepts_a_membership_id(tmp_path: Path):
    orch = _orch(tmp_path)
    files = orch.list_asset_files("dataset:ds_sales_2026")["files"]
    names = {f["path"] for f in files}
    assert "train.csv" in names


class _UnmountedAssets:
    """One Dataset this container has no mount for — the ordinary case for anything shared.

    Mounts are fixed when the execution starts and only ever cover one project, so a Dataset the
    person can read is usually not on this disk. It is still readable through the data library.
    """

    def __init__(self, payload: bytes = b"a,b\n1,2\n"):
        self.asset = Asset(id="ds_shared", name="Oil-and-Gas-Demo", project="Oil-and-Gas-Demo")
        self.payload = payload
        self.downloads: list[str] = []

    def list_datasets(self, project_id):
        return [self.asset]

    def list_files(self, asset):
        return FileListing([DatasetFile("raw/wells.csv", 0)])  # the API listing carries no sizes

    def download_file(self, asset, rel_path, dest):
        self.downloads.append(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payload)
        return len(self.payload)


def test_a_dataset_with_no_mount_still_lists_its_files(tmp_path: Path):
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    files = orch.list_asset_files("ds_shared")["files"]
    assert [f["path"] for f in files] == ["raw/wells.csv"]
    assert files[0]["attached"] is False


def test_attaching_from_an_unmounted_dataset_downloads_the_bytes(tmp_path: Path):
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    ws = orch.project(start_preview=False).workspace.path

    res = orch.attach_file("ds_shared", "raw/wells.csv")

    dest = ws / res["path"]
    assert dest.is_file() and not dest.is_symlink()      # nothing to link to; the bytes are copied
    assert dest.read_bytes() == b"a,b\n1,2\n"
    assert assets.downloads == ["raw/wells.csv"]
    assert res["size"] == 8
    assert not list(dest.parent.glob("*.part"))          # no half-written leftovers


def test_an_over_cap_download_is_discarded_rather_than_attached(tmp_path: Path):
    # The listing has no sizes, so the cap can only be judged after the bytes arrive. What must not
    # happen is a file that blew the cap being left behind as an attachment.
    assets = _UnmountedAssets(payload=b"x" * 4096)
    orch = _orch(tmp_path, assets=assets)
    orch._attach_max_bytes = 16
    ws = orch.project(start_preview=False).workspace.path

    with pytest.raises(AttachTooLarge):
        orch.attach_file("ds_shared", "raw/wells.csv")

    assert orch.project(start_preview=False).attached == []
    assert not list((ws / "public" / "data").rglob("*"))


def test_a_chat_fetch_lands_in_scratch_and_leaves_the_app_alone(tmp_path: Path):
    """A question has no app. Routing a chip through attach_file put the bytes in the published
    app's asset tree and wrote them into the committed manifest, so asking what was in a file
    enrolled it in every later publish of an app that may never reference it."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws, root = project.workspace.path, project.record.path

    res = orch.fetch_dataset_file_for_chat(_dataset(orch, "sales_2026"), "train.csv")

    assert res["path"] == ".sage/scratch/datasets/sales_2026/train.csv"
    link = root / res["path"]
    assert link.is_symlink()                                  # mounted: still no byte copy
    assert link.read_text().startswith("month,revenue")
    assert not (ws / "public" / "data").exists()
    assert orch.project(start_preview=False).attached == []
    assert ".sage/scratch/" in (root / ".gitignore").read_text()


def test_two_chips_naming_the_same_file_fetch_it_once(tmp_path: Path):
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    orch.project(start_preview=False)

    first = orch.fetch_dataset_file_for_chat("ds_shared", "raw/wells.csv")
    again = orch.fetch_dataset_file_for_chat("ds_shared", "raw/wells.csv")

    assert first["path"] == again["path"]
    assert assets.downloads == ["raw/wells.csv"]


def test_a_handoff_hands_the_scratch_bytes_over_instead_of_fetching_them_again(tmp_path: Path):
    """The handoff is where a Thread becomes an app, so it is where the app's data tree gets the
    file. The bytes are already here — asking Domino for them a second time is the copy this whole
    split exists to avoid — and the scratch copy stays, because the Thread's chip still names it."""
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    project = orch.project(start_preview=False)
    ws, root = project.workspace.path, project.record.path
    fetched = orch.fetch_dataset_file_for_chat("ds_shared", "raw/wells.csv")

    orch._promote_chat_file({"kind": "file", "datasetId": "ds_shared",
                             "datasetRelPath": "raw/wells.csv", "path": fetched["path"]})

    assert assets.downloads == ["raw/wells.csv"]              # once, not once per surface
    entry = _manifest(ws)[0]
    assert entry["path"] == "public/data/Oil-and-Gas-Demo/raw/wells.csv"
    served = ws / entry["path"]
    assert served.read_bytes() == b"a,b\n1,2\n"
    assert (root / fetched["path"]).is_file()                 # the chip's path still resolves


def test_a_chip_marked_inbuild_attaches_into_the_app_instead_of_scratch(tmp_path: Path):
    """A Built App started from the Build rail has no handoff behind it (#74), so the server can't
    tell "is this Build" from the Thread alone — the client's own URL is the only signal, carried
    as `inBuild`. When it's set, a file mention takes the same route a folder attach already does:
    straight into `public/data/`, not the chat-scratch fetch."""
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    project = orch.project(start_preview=False)
    ws, root = project.workspace.path, project.record.path
    tid = orch.create_thread()["id"]

    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "wells.csv",
        "datasetId": "ds_shared", "datasetRelPath": "raw/wells.csv", "inBuild": True,
    })

    assert row["path"] == "public/data/Oil-and-Gas-Demo/raw/wells.csv"
    entry = _manifest(ws)[0]
    assert entry["path"] == row["path"]
    assert (ws / entry["path"]).read_bytes() == b"a,b\n1,2\n"
    assert not (root / ".sage" / "scratch" / "datasets").exists()  # never took the scratch route


def test_inbuild_is_a_routing_signal_and_is_never_persisted(tmp_path: Path):
    """The flag says which route THIS mention takes, once — it has no meaning as stored chip
    state, and the client sends it on every mention regardless of whether one already exists."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    orch.project(start_preview=False)
    tid = orch.create_thread()["id"]

    orch.add_thread_context(tid, {
        "kind": "file", "name": "wells.csv",
        "datasetId": "ds_shared", "datasetRelPath": "raw/wells.csv", "inBuild": True,
    })

    stored = orch.thread_context(tid)["items"]
    assert stored and all("inBuild" not in i for i in stored)


def test_the_stored_chip_records_which_app_took_the_bytes(tmp_path: Path):
    """The receipt the composer chip draws (ADR-0048). `inBuild` says which route the mention took
    and is then thrown away, so without this the only thing left to read is whatever app happens to
    be selected when somebody next looks — which relabels the chip on the next app switch and marks
    a Chat mention the moment Build is opened. Neither is a receipt."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]

    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "wells.csv",
        "datasetId": "ds_shared", "datasetRelPath": "raw/wells.csv", "inBuild": True,
    })

    assert row["attachedApp"] == project.workspace.app_id
    stored = orch.thread_context(tid)["items"]
    assert [i["attachedApp"] for i in stored] == [project.workspace.app_id]


def test_a_chat_mention_records_no_app_because_it_reached_none(tmp_path: Path):
    """The same field, absent. It says "this chip is Session context", which is what a mention with
    no `inBuild` is — the bytes went to scratch and no app carries them."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    orch.project(start_preview=False)
    tid = orch.create_thread()["id"]

    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "wells.csv",
        "datasetId": "ds_shared", "datasetRelPath": "raw/wells.csv",
    })

    assert "attachedApp" not in row
    assert orch.thread_context(tid)["items"]
    assert all("attachedApp" not in i for i in orch.thread_context(tid)["items"])


def test_a_refused_attach_keeps_the_chip_and_names_no_app(tmp_path: Path, monkeypatch):
    """The chip is worth keeping without a path — the turn prompt routes the agent at the Domino
    data library instead. What it must not keep is a receipt for a copy that was refused."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    monkeypatch.setattr(orch, "_attach_max_bytes", 1)

    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "wells.csv",
        "datasetId": "ds_shared", "datasetRelPath": "raw/wells.csv", "inBuild": True,
    })

    assert not row.get("path")
    assert "attachedApp" not in row


def _chip(orch, thread_id: str) -> dict:
    return orch.add_thread_context(thread_id, {
        "kind": "file", "name": "wells.csv",
        "datasetId": "ds_shared", "datasetRelPath": "raw/wells.csv",
    })


def test_closing_the_last_chip_releases_the_bytes_it_fetched(tmp_path: Path):
    """Nothing else releases them, and every fetch counts against the cap — so scratch filled up
    and then quietly refused new fetches, with nothing on screen to say why."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    root = orch.project(start_preview=False).record.path
    tid = orch.create_thread()["id"]
    row = _chip(orch, tid)
    fetched = root / row["path"]
    assert fetched.is_file()

    assert orch.remove_thread_context(tid, row["id"]) == {"removed": True, "heldBy": ""}

    assert not fetched.exists()
    assert not fetched.parent.exists()          # and no empty folders left standing
    assert (root / ".sage" / "scratch" / "datasets").is_dir()


def test_a_chip_in_another_thread_keeps_the_file(tmp_path: Path):
    """A fetch is shared. The person closing one chip is not speaking for the other conversation."""
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    root = orch.project(start_preview=False).record.path
    one, two = orch.create_thread()["id"], orch.create_thread()["id"]
    first, second = _chip(orch, one), _chip(orch, two)
    assert first["path"] == second["path"]
    assert assets.downloads == ["raw/wells.csv"]

    orch.remove_thread_context(one, first["id"])

    assert (root / second["path"]).is_file()


def test_a_file_the_app_now_stands_on_is_not_released(tmp_path: Path):
    """After a handoff the app's data path is a symlink onto these bytes. Deleting them would
    leave the app pointing at nothing."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    project = orch.project(start_preview=False)
    ws, root = project.workspace.path, project.record.path
    tid = orch.create_thread()["id"]
    row = _chip(orch, tid)
    orch._promote_chat_file({"kind": "file", "datasetId": "ds_shared",
                             "datasetRelPath": "raw/wells.csv", "path": row["path"]})

    orch.remove_thread_context(tid, row["id"])

    assert (root / row["path"]).is_file()
    assert (ws / _manifest(ws)[0]["path"]).read_bytes() == b"a,b\n1,2\n"


def test_deleting_a_thread_releases_what_it_fetched(tmp_path: Path):
    """The chips go with the Thread, so the only record of what it fetched goes with it too."""
    orch = _orch(tmp_path, assets=_UnmountedAssets())
    ws = orch.project(start_preview=False).workspace.path
    tid = orch.create_thread()["id"]
    row = _chip(orch, tid)

    orch.delete_thread(tid)

    assert not (ws / row["path"]).exists()


def test_closing_a_chip_never_touches_an_attachment_the_app_owns(tmp_path: Path):
    """A file attached in Build can be pinned into a Thread. Closing that chip is not a detach."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    ds = _dataset(orch, "sales_2026")
    res = orch.attach_file(ds, "train.csv")
    tid = orch.create_thread()["id"]
    row = orch.add_thread_context(tid, {
        "kind": "file", "name": "train.csv", "path": res["path"],
        "datasetId": ds, "datasetRelPath": "train.csv",
    })

    orch.remove_thread_context(tid, row["id"])

    assert (ws / res["path"]).exists()
    assert _manifest(ws)[0]["path"] == res["path"]


def test_a_chip_that_is_not_a_dataset_file_is_not_promoted(tmp_path: Path):
    """The handoff loop sees every chip. A Data Source has no bytes to hand over."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path

    orch._promote_chat_file({"kind": "data_source", "name": "trades"})
    orch._promote_chat_file({"kind": "file", "name": "notes.md", "path": ".sage/scratch/notes.md"})

    assert orch.project(start_preview=False).attached == []
    assert not (ws / "public" / "data").exists()


def test_an_unmounted_attachment_is_rehydrated_by_downloading_it_again(tmp_path: Path):
    assets = _UnmountedAssets()
    orch = _orch(tmp_path, assets=assets)
    ws = orch.project(start_preview=False).workspace.path
    rel = orch.attach_file("ds_shared", "raw/wells.csv")["path"]

    (ws / rel).unlink()                                   # the agent deleted it mid-turn
    orch._restore_attachments()

    assert (ws / rel).read_bytes() == b"a,b\n1,2\n"
    assert assets.downloads == ["raw/wells.csv", "raw/wells.csv"]


def test_the_exclude_list_covers_a_copy_in_an_idle_built_app(tmp_path: Path):
    # The commit runs `git add -A` at the Project root and stages every Built App, so an exclude
    # list drawn from the app being built lets a copy sitting in the other one ride out with it
    # (#81). The nudge stays narrow: this agent did not make that copy and cannot move it.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    idle = proj.workspace.app_id
    orch.upload_file("sales.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "sales.csv").write_text("a,b\n1,2\n")   # agent copied it into src/

    orch.create_app()                                    # mint a second app and build in that one

    assert orch._detect_leaks(proj) == []
    assert orch._leaked_copy_paths(proj) == [f"apps/{idle}/src/sales.csv"]


def test_the_turns_own_app_stays_covered_after_the_person_looks_away(tmp_path: Path):
    # #77: a build carries on in the app it started in while the person reads another. The tree the
    # agent copied into is the pinned one, and it is still in the commit the turn ends with.
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    building = proj.workspace.app_id
    orch.upload_file("sales.csv", b"a,b\n1,2\n")
    (proj.workspace.path / "src" / "sales.csv").write_text("a,b\n1,2\n")
    proj.turn_app, proj.turn_attached = proj.workspace, list(proj.attached)   # what a turn pins

    orch.create_app()                                                        # person looks away

    assert orch._detect_leaks(proj) == [("sales.csv", ["src/sales.csv"])]
    assert orch._leaked_copy_paths(proj) == [f"apps/{building}/src/sales.csv"]


# ---- an Upload crosses by becoming an Attachment (ADR-0023, #147) --------------------------


def test_a_chat_upload_crosses_into_the_default_dataset_and_keeps_the_scratch_copy(tmp_path: Path):
    orch = _orch(tmp_path)
    root = orch.project(start_preview=False).record.path
    scratch = orch.upload_scratch("note.csv", b"x")

    out = orch._cross_chat_upload({"kind": "file", "name": "note.csv", "path": scratch["path"]})

    assert out == {"name": "note.csv", "crossed": True, "dataset": "sales_2026"}
    assert (root / scratch["path"]).exists()          # scratch bytes survive the crossing
    assert any(e["dataset_rel_path"] == "uploads/note.csv"
               for e in orch.project(start_preview=False).attached)


def test_a_chat_upload_refuses_to_cross_when_no_dataset_is_writable(tmp_path: Path):
    prov = FakeAssetProvider()
    prov.assets = []
    orch = _orch(tmp_path, assets=prov)
    root = orch.project(start_preview=False).record.path
    scratch = orch.upload_scratch("note.csv", b"x")

    out = orch._cross_chat_upload({"kind": "file", "name": "note.csv", "path": scratch["path"]})

    assert out == {"name": "note.csv", "crossed": False,
                   "reason": "note.csv stayed in Chat — no writable Dataset is mounted here"}
    assert (root / scratch["path"]).exists()          # nothing moved on a refusal
    assert orch.project(start_preview=False).attached == []


def test_a_dataset_fetched_chip_is_not_treated_as_a_chat_upload(tmp_path: Path):
    """`_promote_chat_file` already carries this one across — a chip with a datasetId and a
    datasetRelPath is a question's answer, not the composer's file (ADR-0023)."""
    orch = _orch(tmp_path)
    orch.project(start_preview=False)

    out = orch._cross_chat_upload({"kind": "file", "name": "train.csv",
                                    "path": ".sage/scratch/datasets/sales_2026/train.csv",
                                    "datasetId": "ds1", "datasetRelPath": "train.csv"})

    assert out is None


def test_delete_scratch_removes_the_uploads_bytes(tmp_path: Path):
    orch = _orch(tmp_path)
    root = orch.project(start_preview=False).record.path
    scratch = orch.upload_scratch("note.csv", b"x")

    out = orch.delete_scratch(scratch["path"])

    assert out["deleted"] == scratch["path"]
    assert not (root / scratch["path"]).exists()


def test_delete_scratch_refuses_a_path_outside_scratch(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False)

    with pytest.raises(ValueError):
        orch.delete_scratch("public/data/sales_2026/uploads/note.csv")


def test_delete_scratch_refuses_a_chat_data_fetch_path(tmp_path: Path):
    """A Dataset file Chat fetched for a question is not an Upload, so this door does not reach
    it — closing its chip is the existing "Stop using here" release, not a byte-deleting one."""
    orch = _orch(tmp_path)
    orch.project(start_preview=False)

    with pytest.raises(ValueError):
        orch.delete_scratch(".sage/scratch/datasets/sales_2026/train.csv")


def test_a_crossed_upload_shows_up_in_the_confirm_receipts_uploads_list(tmp_path: Path):
    """The wiring `_write_crossing` relies on: a Chat Upload sitting in a Conversation's context
    is named in the receipt the plan card reads (ADR-0023)."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    store = ThreadStore(project.record.path)
    tid = orch.create_thread()["id"]
    scratch = orch.upload_scratch("note.csv", b"x")
    orch.add_thread_context(tid, {"kind": "file", "name": "note.csv", "path": scratch["path"]})
    (project.workspace.path / ".sage").mkdir(parents=True, exist_ok=True)   # a real handoff has one

    crossed = orch._write_crossing(project, store, tid,
                                    {"resources": True, "artifacts": False, "transcript": False})

    assert crossed["uploads"] == [{"name": "note.csv", "crossed": True, "dataset": "sales_2026"}]
    assert (project.record.path / scratch["path"]).exists()


def test_one_apps_unreadable_manifest_does_not_shut_the_project(tmp_path: Path):
    """The migration reads every app, so one app's bad file must cost only that app (#274).

    `_rehydrate_attached` is called unguarded by `project()` and by `select_app`, so anything the
    backfill lets escape stops the Project opening — and before the migration existed, a manifest
    nobody could read in one app was invisible from every other. Bytes that are not UTF-8 at all are
    the shape that gets past a catch named for JSON and OS errors, which is the same trap the ledger
    read carries a comment about one function away.

    The bad manifest belongs to an app nobody is looking at, which is the whole point: the migration
    is what reaches it. `Workspace.read_attachments` carries its own narrow catch for the SELECTED
    app's manifest and predates all of this — a separate bug, and deliberately not what this drives.
    """
    orch = _orch(tmp_path)
    first = orch.project(start_preview=False).workspace.app_id
    orch.create_app()                                        # minting selects it, so it is the one
    orch.upload_file("d.csv", b"sage")                       # the upgrade will open on
    root = orch.project(start_preview=False).record.path
    _wind_back_to_pre_274(root)
    unread = root / "apps" / first / ".sage" / "attachments.json"
    unread.parent.mkdir(parents=True, exist_ok=True)
    unread.write_bytes(b"\xff\xfe not utf-8")

    upgraded = _orch(tmp_path, assets=orch._assets)

    assert _door(upgraded, "uploads/d.csv") is True          # the readable app migrated anyway


def test_a_mount_point_with_nothing_behind_it_costs_the_door_not_the_data(tmp_path: Path):
    """The residue of asking the mount and not the file, chosen rather than overlooked (#274).

    A container can pre-create a mount point and the mount then fail, leaving a real empty directory
    where a Dataset should be. Sage cannot tell that from a mounted Dataset whose file is gone, and
    it does not try: probing below the mount root for a second opinion is what it used to do, and
    that probe read Sage's OWN pruned `uploads/` as an unreachable Dataset — keeping a dead ledger
    line and telling the person their data was still there when it was not.

    So this direction is taken on purpose. The cost is a destroy door that disappears; the cost the
    other way is a destroy door that appears over somebody else's file, and only one of those two
    reaches their data. Asserted so that a future reader restoring that probe meets this first.
    """
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    res = orch.upload_file("d.csv", b"sage")
    src = (ws / res["path"]).resolve()
    ds = _dataset(orch, "sales_2026")
    hollow = tmp_path / "mount-point-with-no-dataset"
    hollow.mkdir()
    orch._assets.assets = [replace(a, mount_path=str(hollow)) if a.id == ds else a
                           for a in orch._assets.assets]

    done = orch.delete_file(res["path"])

    assert done["bytes_removed"] is True                      # read as "the file is already gone"
    assert src.is_file()                                      # though the real bytes are untouched


def test_a_row_with_a_field_nobody_reads_yet_still_names_its_bytes(tmp_path: Path):
    """One comparison, so a ledger row can gain a field without a door quietly vanishing (#274).

    Six readers had grown around this record asking different questions of it. They agree only while
    the writer emits exactly two keys — and ADR-0050's own retirement adds a third as its first step.
    On that day each fails differently, so all of them are driven here: the stamp (both the
    single-file and the folder path), the destroy door's second gate, and the FORGET, which is the
    dangerous one — a miss there leaves a record of bytes that have just been destroyed, waiting for
    whoever writes at that path next.
    """
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ds = _dataset(orch, "sales_2026")
    mount = Path(next(a for a in orch._assets.list_datasets("Sage") if a.id == ds).mount_path)
    (mount / "uploads").mkdir(parents=True, exist_ok=True)
    for name in ("one.csv", "two.csv"):
        (mount / "uploads" / name).write_bytes(b"sage")
    ledger = project.record.path / ".sage" / "uploads.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps(                            # what a later build writes
        [{"dataset_id": ds, "path": f"uploads/{n}", "sha256": "not-read-yet"}
         for n in ("one.csv", "two.csv")], indent=2))

    single = orch.attach_file(ds, "uploads/one.csv")
    orch.attach_folder(ds, "uploads")

    assert _door(orch, "uploads/one.csv") is True            # the single-file path reads it...
    assert _door(orch, "uploads/two.csv") is True            # ...and the folder path agrees

    done = orch.delete_file(single["path"])                  # the door's second gate reads it too
    assert done["bytes_removed"] is True
    assert not (mount / "uploads" / "one.csv").exists()
    # And the forget reached the row it was deleting. Left standing, it names bytes that are gone
    # and hands their door to whoever writes at that path next.
    assert [r["path"] for r in json.loads(ledger.read_text())] == ["uploads/two.csv"]


def test_a_path_with_no_record_says_nothing_about_its_bytes(tmp_path: Path):
    """`no-record` is a claim about the BYTES, and this path never reached them (#274).

    A `public/data/` path with no manifest row — the symlink-scan rehydrate writes none, and a panel
    row can go stale between being drawn and being clicked — has not been asked who wrote anything.
    Answering `no-record` would state as permanent ("nothing will ever remove these for you") a
    thing that was never looked at.
    """
    orch = _orch(tmp_path)
    orch.project(start_preview=False)

    done = orch.delete_file("public/data/sales_2026/uploads/never-recorded.csv")

    assert done["bytes_removed"] is False
    assert done["bytes_kept"] is None
