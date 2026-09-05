"""ADR-0029, "A truncated listing refuses the act" — a listing that stopped short says so.

`walk_files` caps at `_MAX_FILES` and the unmounted path slices to the same number, and neither
said anything about it. The walk is sorted, so the cap cuts the tail: early folders are whole, late
ones are cut or missing, and nothing downstream could tell which is which. A root attach on a
12,000-file Dataset would then attach the 5,000 that happened to be listed and call it all of them.

So truncation becomes a fact the caller reads rather than a silence it has to guess at: the walk
reports it, the provider carries it, the route hands it to the Workbench, and the Dataset tree says
it on screen. A complete listing is unchanged at every one of those steps — the flag is off and
nothing new is drawn.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.assets import provider as assets
from sage.assets.provider import Asset, DominoAssetProvider, FakeAssetProvider
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


def _mount(root: Path, count: int) -> Path:
    """`count` files under one mount, in two folders, so the sorted walk has a tail to cut."""
    for i in range(count):
        f = root / ("early" if i < count // 2 else "late") / f"f{i:05d}.csv"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("a\n")
    return root


# --- the walk ------------------------------------------------------------------------------


def test_a_complete_walk_reports_every_file_and_no_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "_MAX_FILES", 4)
    listing = assets.walk_files(_mount(tmp_path, 3))

    assert [f.path for f in listing.files] == ["early/f00000.csv", "late/f00001.csv",
                                               "late/f00002.csv"]
    assert listing.truncated is False


def test_a_walk_that_exactly_fills_the_cap_is_not_truncated(tmp_path, monkeypatch):
    """The boundary the old `break` could not see: full is not the same as cut."""
    monkeypatch.setattr(assets, "_MAX_FILES", 4)
    listing = assets.walk_files(_mount(tmp_path, 4))

    assert len(listing.files) == 4
    assert listing.truncated is False


def test_a_walk_that_stops_short_says_so_and_still_reports_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "_MAX_FILES", 4)
    listing = assets.walk_files(_mount(tmp_path, 9))

    assert len(listing.files) == 4
    assert listing.truncated is True


def test_what_the_walk_would_have_skipped_anyway_does_not_read_as_truncation(tmp_path, monkeypatch):
    """A dotfile, an empty folder and an unreadable entry are not files this listing lost.

    The cap used to be checked before the filters, so anything at all sitting past the last file —
    a `.ipynb_checkpoints` directory, most often — would have to be reported as a cut tail.
    """
    monkeypatch.setattr(assets, "_MAX_FILES", 4)
    root = _mount(tmp_path, 4)
    (root / "zzz_empty").mkdir()
    (root / "zzz_hidden").mkdir()
    (root / "zzz_hidden" / ".secret").write_text("x")
    (root / ".dotfile").write_text("x")

    assert assets.walk_files(root).truncated is False


# --- the provider --------------------------------------------------------------------------


def _api(monkeypatch, paths):
    """Answer the two `/v4` GETs a listing makes: one snapshot, then a row per named file.

    Rows are handed over UNSORTED where a test says so, because `files/recursive` does not promise
    an order and the cap has to take the sorted prefix.
    """
    import httpx

    def get(url, **kw):
        if "/files/recursive" not in url:
            return httpx.Response(200, json=[{"id": "s1", "version": 0,
                                              "lifecycleStatus": "Active"}])
        return httpx.Response(200, json={"rows": [
            {"name": {"isDirectory": False, "label": p.rsplit("/", 1)[-1], "fileName": p},
             "size": {"sizeInBytes": 1}, "lastModified": 1} for p in paths]})

    monkeypatch.setattr(httpx, "get", get)
    return DominoAssetProvider("http://domino", lambda: "t", mount_roots=[])


def test_an_unmounted_listing_that_overflows_the_cap_reports_the_sorted_prefix(monkeypatch):
    """The whole tree arrives in one response, so the cut and the flag are both decided here.

    `files/recursive` takes no `page_size` and returns no continuation token, so unlike the paged
    SDK listing this replaced, the real count is in hand before anything is dropped — `truncated`
    is read off it rather than inferred from a page that came back full.
    """
    monkeypatch.setattr(assets, "_MAX_FILES", 3)
    provider = _api(monkeypatch, ["late/f3.csv", "early/f1.csv", "late/f4.csv", "early/f2.csv",
                                  "early/f0.csv"])
    listing = provider.list_files(Asset(id="i1", name="ds"))

    assert [f.path for f in listing.files] == ["early/f0.csv", "early/f1.csv", "early/f2.csv"]
    assert listing.truncated is True


def test_an_unmounted_listing_that_exactly_fills_the_cap_is_not_truncated(monkeypatch):
    """The same boundary the walk has: full is not the same as cut."""
    monkeypatch.setattr(assets, "_MAX_FILES", 3)
    listing = _api(monkeypatch, ["a.csv", "b.csv", "c.csv"]).list_files(Asset(id="i1", name="ds"))

    assert len(listing.files) == 3
    assert listing.truncated is False


def test_an_unmounted_listing_that_fits_is_not_truncated(monkeypatch):
    monkeypatch.setattr(assets, "_MAX_FILES", 3)
    listing = _api(monkeypatch, ["raw/f0.csv", "raw/f1.csv"]).list_files(Asset(id="i1", name="ds"))

    assert [f.path for f in listing.files] == ["raw/f0.csv", "raw/f1.csv"]
    assert listing.truncated is False


def test_a_directory_row_does_not_count_toward_the_cut(monkeypatch):
    """Directories arrive as rows too, and counting them would report a cut that never happened.

    Three files and two folders under a cap of 3 is a complete listing. Counted before the folders
    are dropped, it claims to have lost a tail that does not exist, and ADR-0029 then refuses a
    folder act over a Dataset it could have described in full.
    """
    monkeypatch.setattr(assets, "_MAX_FILES", 3)
    import httpx

    def get(url, **kw):
        if "/files/recursive" not in url:
            return httpx.Response(200, json=[{"id": "s1", "version": 0,
                                              "lifecycleStatus": "Active"}])
        rows = []
        for p, is_dir in [("early", True), ("early/f0.csv", False), ("early/f1.csv", False),
                          ("late", True), ("late/f2.csv", False)]:
            rows.append({"name": {"isDirectory": is_dir, "label": p.rsplit("/", 1)[-1],
                                  "fileName": p},
                         "size": {"sizeInBytes": None if is_dir else 1}, "lastModified": 1})
        return httpx.Response(200, json={"rows": rows})

    monkeypatch.setattr(httpx, "get", get)
    listing = DominoAssetProvider("http://domino", lambda: "t", mount_roots=[]).list_files(
        Asset(id="i1", name="ds"))

    assert [f.path for f in listing.files] == ["early/f0.csv", "early/f1.csv", "late/f2.csv"]
    assert listing.truncated is False


def test_a_mounted_dataset_reports_the_walks_own_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "_MAX_FILES", 2)
    asset = Asset(id="i1", name="ds", mount_path=str(_mount(tmp_path, 5)))

    assert _api(monkeypatch, ["should-not-be-used"]).list_files(asset).truncated is True
    assert FakeAssetProvider().list_files(asset).truncated is True


def test_a_dataset_with_no_mount_at_all_lists_nothing_and_claims_nothing(tmp_path):
    """`FakeAssetProvider` answers for an Asset it never seeded; empty is not truncated."""
    listing = FakeAssetProvider().list_files(Asset(id="i1", name="ds"))

    assert listing.files == []
    assert listing.truncated is False


# --- what the Workbench is told ---------------------------------------------------------------


def _orch(tmp: Path, assets_provider=None) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code", template=template, gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", assets=assets_provider,
    )


def test_a_complete_listing_reaches_the_workbench_unchanged(tmp_path):
    orch = _orch(tmp_path)
    dataset = next(a["id"] for a in orch.list_assets() if a["name"] == "sales_2026")

    body = orch.list_asset_files(dataset)

    assert {f["path"] for f in body["files"]} == {"train.csv", "README.md"}
    assert all(not f["attached"] for f in body["files"])
    assert body["truncated"] is False


def test_a_truncated_listing_reaches_the_workbench_as_a_fact(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "_MAX_FILES", 1)
    orch = _orch(tmp_path)
    dataset = next(a["id"] for a in orch.list_assets() if a["name"] == "sales_2026")

    body = orch.list_asset_files(dataset)

    assert len(body["files"]) == 1
    assert body["truncated"] is True


def test_the_route_hands_the_flag_on_beside_the_files(monkeypatch):
    """The route shapes nothing of its own, so the flag cannot be dropped between the two."""
    import sage.orchestrator.app as appmod

    monkeypatch.setattr(appmod.orchestrator, "list_asset_files",
                        lambda _id: {"files": [{"path": "a.csv"}], "truncated": True})
    body = TestClient(appmod.control_app).get("/api/project/assets/ds-1/files").json()

    assert body == {"files": [{"path": "a.csv"}], "truncated": True}


# --- what the Dataset tree says ----------------------------------------------------------------


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


def _tree(files: list[dict], truncated: bool, query: str = "") -> list[dict]:
    return _walk({"files": files, "truncated": truncated, "query": query})[0]


_FILES = [{"path": "raw/2024/a.csv", "size": 3, "dest": "public/data/ds/a.csv", "attached": False},
          {"path": "raw/2025/b.csv", "size": 3, "dest": "public/data/ds/b.csv", "attached": False}]


def _said(nodes: list[dict]) -> str:
    return " ".join(n.get("text", "") for n in nodes)


def test_a_complete_listing_draws_nothing_new(tmp_path):
    nodes = _tree(_FILES, truncated=False)

    assert not [n for n in nodes if n.get("className") == "sw-tree-truncated"]
    assert "raw" in _said(nodes)


def test_the_note_is_not_dressed_as_the_placeholder_it_sits_above():
    """It qualifies the rows under it, and those rows are what the eye reads first.

    `.sw-tree-empty` grey is for a placeholder standing in for content. Wearing it, the one line
    saying the tree is incomplete looked exactly like the inert "No files in this Dataset." — on
    the screen whose whole premise is that a complete-looking tree is what misleads.
    """
    css = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "css" / "shell.css").read_text()
    muted = css.split(".sw-tree-empty, .sw-tree-spin {")[1].split("}")[0]
    note = css.split(".sw-tree-truncated {")[1].split("}")[0]

    assert "sw-tree-truncated" not in muted
    assert "var(--gray-500)" not in note
    # The Workbench's own card for a caveat somebody has to read, borrowed rather than reinvented.
    assert "var(--warning-text)" in note and "var(--warning-bg)" in note
    assert "var(--warning-border)" in note


def test_a_truncated_tree_says_the_listing_is_incomplete():
    nodes = _tree(_FILES, truncated=True)

    (note,) = [n for n in nodes if n.get("className") == "sw-tree-truncated"]
    # The pack's words, not a literal: this sentence names both the assistant and the noun.
    assert "Sage" in note["text"] and "Dataset" in note["text"]
    assert "2" in note["text"]  # how many of them it did list
    assert "raw" in _said(nodes)  # and the files it has are still there to use


def test_a_search_that_finds_nothing_in_a_cut_listing_does_not_call_it_nothing():
    """`No files match` is a lie when the tail was never listed. The note outlives the filter."""
    nodes = _tree(_FILES, truncated=True, query="zzzz")

    assert [n for n in nodes if n.get("className") == "sw-tree-truncated"]


# --- what one Dataset leaves behind for the next -----------------------------------------------


def test_the_next_dataset_does_not_inherit_this_ones_truncation():
    """The tree is one instance walking whichever Resource is expanded, so its state carries."""
    _cut, whole = _walk({"files": _FILES, "truncated": True}, {"files": _FILES, "truncated": False})

    assert [n for n in _cut if n.get("className") == "sw-tree-truncated"]
    assert not [n for n in whole if n.get("className") == "sw-tree-truncated"]


def test_the_next_dataset_does_not_inherit_this_ones_failure():
    """Predates this ticket, in the effect it edits: only `files` was replaced when a walk moved
    on, so a Dataset that answered perfectly well drew the previous one's platform error. The Data
    Source cascade beside it has always cleared both."""
    failed, whole = _walk({"fail": True}, {"files": _FILES, "truncated": False})

    assert [n for n in failed if n.get("className") == "sw-passthrough"]
    assert not [n for n in whole if n.get("className") == "sw-passthrough"]
    assert "raw" in _said(whole)
