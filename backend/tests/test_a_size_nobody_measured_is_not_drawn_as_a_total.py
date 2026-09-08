"""#201, on top of #197 — the Data panel's tree stops drawing a size nothing measured.

Since #153 the sizes are real on both paths: `stat()` on a mount, the platform's own rows on an
unmounted Dataset. What is left is the listing that came back without them, where `or 0` turns a
file nobody weighed into a zero indistinguishable from an empty file. `FileListing.measured` is
the fact about the LISTING that lets a caller leave the number off instead.

The card's answer (#197) does not transfer unchanged, and that difference is this whole ticket.
The card puts a size beside ONE row, so dropping the key there leaves a visible gap. The tree ADDS
sizes into a folder total, so a row that merely loses its size silently under-counts the total —
a second wrong number in place of the first. So the tree says the total was not measured.

Read off `measured` and never off `mount_path`: the platform weighs an unmounted Dataset too, so
keying on the mount throws away sizes it did give. Nothing here needs a mount or a live platform.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.assets.provider import Asset, DatasetFile, FileListing
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


class _Listing:
    """One Dataset whose listing is staged whole — the files it names, and whether it weighed them.

    No mount, because the shape this ticket is about only ever comes back from the unmounted path:
    a mounted walk stats every file and is always measured. `mount_path` is empty for exactly that
    reason, and no test below reads it — the flag is the answer, not the mount.
    """

    def __init__(self, files: dict[str, int], *, measured: bool) -> None:
        self.files, self.measured = files, measured

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        return [Asset("ds_revenue", "revenue", project="Revenue", mount_path="")]

    def list_files(self, asset: Asset) -> FileListing:
        return FileListing([DatasetFile(p, n) for p, n in self.files.items()],
                           measured=self.measured)

    def download_file(self, asset: Asset, rel_path: str, dest: Path) -> int:
        raise AssertionError("no test here attaches anything")


def _orch(tmp: Path, assets) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=template,
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        assets=assets,
    )


def _files(tmp: Path, files: dict[str, int], *, measured: bool) -> dict:
    return _orch(tmp, _Listing(files, measured=measured)).list_asset_files("ds_revenue")


# --- what the route hands the Workbench ---------------------------------------------------------


def test_a_listing_that_weighed_nothing_says_so_beside_its_files(tmp_path):
    """The flag travels WITH the rows. Dropping the sizes alone would leave the tree adding up
    whatever survived and calling the result a total."""
    body = _files(tmp_path, {"raw/a.csv": 0, "raw/b.csv": 0}, measured=False)

    assert body["measured"] is False
    assert {f["path"] for f in body["files"]} == {"raw/a.csv", "raw/b.csv"}


def test_a_row_the_listing_never_weighed_carries_no_size_at_all(tmp_path):
    """"0 bytes" is a lie about the file where no size at all is the truth about the listing."""
    body = _files(tmp_path, {"raw/a.csv": 0, "raw/b.csv": 0}, measured=False)

    assert [f for f in body["files"] if "size" in f] == []


def test_a_row_the_platform_did_weigh_keeps_its_size_in_a_listing_that_missed_others(tmp_path):
    """`measured or size`, not `measured` alone: a listing that under-reported some of its rows
    still reported others, and hiding a size it did give throws away the half it got right (#197).
    """
    body = _files(tmp_path, {"raw/a.csv": 23, "raw/b.csv": 0}, measured=False)

    rows = {f["path"]: f for f in body["files"]}

    assert rows["raw/a.csv"]["size"] == 23
    assert "size" not in rows["raw/b.csv"]


def test_a_measured_listing_keeps_every_size_including_a_genuinely_empty_file(tmp_path):
    """The absence is a fact about the LISTING, never about the number. A listing that weighed its
    files weighed the empty one too, and "0 bytes" there is what somebody needed to read."""
    body = _files(tmp_path, {"empty.csv": 0, "calls.csv": 23}, measured=True)

    rows = {f["path"]: f for f in body["files"]}

    assert body["measured"] is True
    assert rows["empty.csv"]["size"] == 0
    assert rows["calls.csv"]["size"] == 23


def test_an_unmounted_dataset_the_platform_did_weigh_keeps_every_size_it_gave(tmp_path):
    """The state is read off `measured` and never off `mount_path`. Since #153 the platform weighs
    an unmounted Dataset, so keying on the mount would blank real sizes — and blank them unevenly,
    hiding the one row where "0 bytes" is the fact somebody needed."""
    body = _files(tmp_path, {"placeholder.csv": 0, "calls.csv": 23}, measured=True)

    assert body["measured"] is True
    assert {f["path"]: f["size"] for f in body["files"]} == {"placeholder.csv": 0, "calls.csv": 23}


# --- what the Dataset tree draws ----------------------------------------------------------------


def _walk(*steps: dict) -> list[list[dict]]:
    """One flattened tree per Dataset the panel is asked about, in order."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    harness = Path(__file__).resolve().parent / "js" / "dataset_tree_harness.mjs"
    out = subprocess.run(
        ["node", str(harness)], input=json.dumps(list(steps)),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _metas(nodes: list[dict]) -> list[str]:
    """What every folder row says its subtree holds, the Dataset's own row first."""
    return [n["text"] for n in nodes if n.get("className") == "sw-tree-folder-meta"]


_WEIGHED = [{"path": "raw/a.csv", "size": 3, "dest": "public/data/ds/a.csv", "attached": False},
            {"path": "raw/b.csv", "size": 3, "dest": "public/data/ds/b.csv", "attached": False}]
# The same two files as the platform hands them back when it named them without weighing them:
# `or 0` in the API listing is the only place a zero could come from, so this is what the invented
# zeros look like by the time they reach a row.
_UNWEIGHED = [{"path": f["path"], "dest": f["dest"], "attached": False} for f in _WEIGHED]


def test_a_measured_folder_row_still_reads_its_total():
    """The ordinary case is untouched: a listing that weighed its files still totals them."""
    metas = _metas(_walk({"files": _WEIGHED, "measured": True})[0])

    assert metas and all("6 B" in m for m in metas)


def test_a_folder_total_over_files_nobody_weighed_says_it_was_not_measured():
    """Not "0 B", which is a wrong measurement, and not a bare file count either — that is what a
    folder of genuinely empty files reads, and the two are not the same answer."""
    metas = _metas(_walk({"files": _UNWEIGHED, "measured": False})[0])

    assert metas
    for meta in metas:
        assert "size not measured" in meta
        assert "B" not in meta.replace("size not measured", "")


def test_the_datasets_own_row_says_it_too():
    """"All files" is the same act at depth 0, drawn by the same row, and it adds up the same
    sizes — so a total it cannot stand behind is withheld there for the same reason."""
    nodes = _walk({"files": _UNWEIGHED, "measured": False})[0]
    root = nodes.index(next(n for n in nodes if n.get("className") == "sw-tree-root-row"))

    assert "size not measured" in next(
        n["text"] for n in nodes[root:] if n.get("className") == "sw-tree-folder-meta")


def test_a_listing_that_says_nothing_about_sizes_is_not_read_as_a_measured_one():
    """Fails CLOSED, the way `folder_act` beside it does. Reading a missing flag as "measured"
    would draw a total off exactly the listings that carry no answer about one."""
    metas = _metas(_walk({"files": _UNWEIGHED, "measured": None})[0])

    assert metas and all("size not measured" in m for m in metas)


def test_the_next_dataset_does_not_inherit_this_ones_unmeasured_listing():
    """The tree is one instance walking whichever Resource is expanded, so its state carries."""
    unweighed, weighed = _walk({"files": _UNWEIGHED, "measured": False},
                               {"files": _WEIGHED, "measured": True})

    assert all("size not measured" in m for m in _metas(unweighed))
    assert all("6 B" in m for m in _metas(weighed))
