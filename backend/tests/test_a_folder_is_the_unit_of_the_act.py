"""ADR-0029 — a folder is the unit of the act, and a file is the unit of the record.

A partitioned Dataset — `raw/2024/…`, `raw/2025/…` — was two hundred clicks, because every door
that put data into a Built App took one `path`. The folder act takes the subtree instead, at any
depth including the Dataset root.

What each half of the title buys is asserted separately, because they pull in opposite directions:

  * the ACT is the folder — one cap pre-flight over the subtree, all-or-nothing, and ONE write of
    each record the act touches rather than one per file;
  * the RECORD is still the file — one `.sage/attachments.json` entry each, exactly the shape a
    single attach writes, so rehydrate, detach, leak detection and the commit backstop never learn
    a second entry shape;
  * and what the AGENT is told collapses with the act, or the block it re-reads every turn would
    grow with the file count forever.

The act is also refused wherever the subtree cannot be measured: an unmounted Dataset reports no
sizes at all, and a truncated listing cannot prove any subtree is whole.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.assets import provider as assets
from sage.assets.provider import FakeAssetProvider
from sage.orchestrator import service as svc
from sage.orchestrator.service import (
    AttachTooLarge,
    AttachWouldClobber,
    FolderActUnavailable,
    Orchestrator,
)
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path, assets_provider) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code", template=_template(tmp), gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", assets=assets_provider,
    )


def _partitioned(tmp: Path, *, per_year: int = 2, body: str = "a,b\n1,2\n") -> FakeAssetProvider:
    """The seeded `sales_2026` mount, with a partitioned folder under it."""
    provider = FakeAssetProvider(root=tmp / "mounts")
    mount = Path(next(a.mount_path for a in provider.assets if a.name == "sales_2026"))
    for year in ("2024", "2025"):
        for i in range(per_year):
            f = mount / "raw" / year / f"part-{i}.csv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body)
    return provider


def _dataset_id(orch: Orchestrator, name: str = "sales_2026") -> str:
    return next(a["id"] for a in orch.list_assets() if a["name"] == name)


def _manifest(ws: Path) -> list[dict]:
    record = ws / ".sage" / "attachments.json"
    return json.loads(record.read_text()) if record.is_file() else []


def _ready(tmp: Path, **kw) -> tuple[Orchestrator, str, Path]:
    orch = _orch(tmp, _partitioned(tmp, **kw))
    ws = orch.project(start_preview=False).workspace.path
    return orch, _dataset_id(orch), ws


# --- The act is a folder, at any depth, including the root -------------------------------------


def test_attaching_a_folder_takes_every_file_below_it(tmp_path: Path):
    orch, ds, ws = _ready(tmp_path)

    out = orch.attach_folder(ds, "raw/2024")

    assert out["attached"] == 2
    assert sorted(e["file"] for e in orch.project().attached) == [
        "raw/2024/part-0.csv", "raw/2024/part-1.csv"]
    # Recursive over its own subtree and no wider: the sibling partition is untouched.
    assert all("2025" not in e["file"] for e in orch.project().attached)
    for entry in orch.project().attached:
        assert (ws / entry["path"]).is_symlink()


def test_the_dataset_root_is_the_same_act_at_depth_zero(tmp_path: Path):
    # "The whole Dataset" is this act with no folder, rather than a second feature with its own
    # name and its own edge cases.
    orch, ds, _ = _ready(tmp_path)

    out = orch.attach_folder(ds, "")

    # Both partitions plus the two files the mount is seeded with at its root.
    assert out["attached"] == 6
    assert {e["file"] for e in orch.project().attached} >= {
        "raw/2024/part-0.csv", "raw/2025/part-1.csv", "train.csv", "README.md"}


def test_a_file_already_attached_is_not_attached_twice(tmp_path: Path):
    orch, ds, _ = _ready(tmp_path)
    orch.attach_file(ds, "raw/2024/part-0.csv")

    out = orch.attach_folder(ds, "raw/2024")

    assert out["attached"] == 1                      # the act reports what it added
    assert len(orch.project().attached) == 2         # and the record holds each file once
    # A folder already carried whole is a no-op, not a rewrite of every record it touches.
    assert orch.attach_folder(ds, "raw/2024")["attached"] == 0


def test_a_folder_the_dataset_does_not_hold_is_not_an_empty_success(tmp_path: Path):
    orch, ds, _ = _ready(tmp_path)

    with pytest.raises(FileNotFoundError):
        orch.attach_folder(ds, "raw/2027")


# --- The record stays one entry per file -------------------------------------------------------


def test_the_record_gains_one_entry_per_file_and_never_a_folder(tmp_path: Path):
    """The load-bearing half of the title: nothing downstream learns a second entry shape."""
    orch, ds, ws = _ready(tmp_path)

    orch.attach_folder(ds, "raw/2024")

    entries = _manifest(ws)
    assert len(entries) == 2
    assert [e["path"] for e in entries] == ["public/data/sales_2026/raw/2024/part-0.csv",
                                            "public/data/sales_2026/raw/2024/part-1.csv"]
    for e in entries:
        # Exactly the shape `attach_file` writes for one file, field for field.
        assert set(e) >= {"dataset_id", "dataset", "file", "path", "size", "source",
                          "dataset_rel_path"}
        assert e["source"] == "dataset" and e["size"] > 0
    # No entry stands for the folder itself.
    assert not [e for e in entries if e["path"].endswith("/2024")]


def test_the_whole_set_is_written_once_and_not_once_per_file(tmp_path: Path, monkeypatch):
    """N single attaches rewrote the whole manifest N times, because `_descriptor` persisted its
    own cache per file. The batched act computes the set, then writes each record once."""
    orch, ds, _ = _ready(tmp_path, per_year=6)
    counts: dict[str, int] = {}

    def count(name: str, fn):
        def wrapped(*a, **k):
            counts[name] = counts.get(name, 0) + 1
            return fn(*a, **k)
        return wrapped

    workspace = type(orch.project().workspace)
    monkeypatch.setattr(workspace, "write_attachments",
                        count("write_attachments", workspace.write_attachments))
    for name in ("_write_agents_data_block", "_ensure_gitignored", "_rebaseline_turn"):
        monkeypatch.setattr(orch, name, count(name, getattr(orch, name)))

    out = orch.attach_folder(ds, "raw")

    assert out["attached"] == 12
    assert counts == {"write_attachments": 1, "_write_agents_data_block": 1,
                      "_ensure_gitignored": 1, "_rebaseline_turn": 1}


# --- No partial state --------------------------------------------------------------------------


def test_a_folder_over_the_cap_attaches_nothing_and_carries_the_three_numbers(tmp_path: Path,
                                                                             monkeypatch):
    monkeypatch.setenv("SAGE_ATTACH_MAX_BYTES", "12")
    orch, ds, ws = _ready(tmp_path, per_year=2, body="0123456789")   # 10 bytes each, 4 files

    with pytest.raises(AttachTooLarge) as refused:
        orch.attach_folder(ds, "raw")

    # The three numbers the person decides on: what the folder weighs, what is already carried,
    # and the cap. A refusal that names them is a decision they can act on.
    assert refused.value.incoming == 40
    assert refused.value.current == 0
    assert refused.value.cap == 12
    assert orch.project().attached == []
    assert not (ws / "public" / "data" / "sales_2026").exists()   # not even the directories


def test_a_folder_that_fails_part_way_through_leaves_nothing_behind(tmp_path: Path, monkeypatch):
    """No partial state is not only about the cap. A link that will not be made unwinds the ones
    that were, so a Built App never gets a data directory nobody chose."""
    orch, ds, ws = _ready(tmp_path)
    real = svc._safe_join

    def explode(root: Path, rel: str) -> Path:
        if rel.endswith("part-1.csv"):
            raise OSError("the mount went away")
        return real(root, rel)

    monkeypatch.setattr(svc, "_safe_join", explode)

    with pytest.raises(OSError):
        orch.attach_folder(ds, "raw/2024")

    monkeypatch.undo()
    assert orch.project().attached == []
    assert _manifest(ws) == []
    assert not (ws / "public" / "data" / "sales_2026").exists()


# --- What the agent is told collapses with the act ---------------------------------------------


def test_below_the_threshold_the_block_still_names_every_file(tmp_path: Path):
    orch, ds, ws = _ready(tmp_path, per_year=2)

    orch.attach_folder(ds, "raw")

    agents = (ws / "AGENTS.md").read_text()
    assert agents.count("- disk `public/data/sales_2026/raw/") == 4
    assert "fetch `data/sales_2026/raw/2024/part-0.csv`" in agents


def test_above_the_threshold_the_block_names_the_folder_once(tmp_path: Path):
    """The block is re-read every turn. Per-file lines are right for five files and ruinous for two
    hundred — the prompt would grow with the file count, on every turn, forever."""
    orch, ds, ws = _ready(tmp_path, per_year=8)      # 16 files, over the threshold of 10

    orch.attach_folder(ds, "raw")

    agents = (ws / "AGENTS.md").read_text()
    assert "- disk `public/data/sales_2026/raw/2024/part-0.csv`" not in agents
    # One line per folder: the count, the shape the files share, and the served-path pattern.
    assert "- 8 files in `public/data/sales_2026/raw/2024`" in agents
    assert "- 8 files in `public/data/sales_2026/raw/2025`" in agents
    assert "fetch `data/sales_2026/raw/2024/<name>`" in agents
    assert "from dataset **sales_2026**" in agents


def test_the_collapsed_block_keeps_the_guardrails_it_exists_for(tmp_path: Path):
    """The collapse is safe because the block's whole reason for existing survives it: agents
    otherwise guess a flat `/data/<name>`, hit the SPA fallback, and "fix" it by copying the file
    into `src/` — which leaks the data into the app's git repo."""
    orch, ds, ws = _ready(tmp_path, per_year=8)

    orch.attach_folder(ds, "raw")

    agents = (ws / "AGENTS.md").read_text()
    assert 'import.meta.env.BASE_URL + "data/' in agents
    assert "Invalid base URL" in agents
    assert "src/" in agents and "gitignored" in agents


def test_the_shared_shape_is_named_once_and_a_mixed_folder_says_so(tmp_path: Path):
    orch = _orch(tmp_path, FakeAssetProvider(root=tmp_path / "mounts"))
    ws = orch.project(start_preview=False).workspace.path
    ds = _dataset_id(orch)
    mount = Path(next(a.mount_path for a in orch._assets.list_datasets("Sage")
                      if a.name == "sales_2026"))
    for i in range(svc.FOLDER_COLLAPSE_THRESHOLD + 1):
        f = mount / "mixed" / (f"part-{i}.csv" if i % 2 else f"part-{i}.json")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("a,b\n1,2\n" if i % 2 else '{"a": 1}')

    orch.attach_folder(ds, "mixed")

    line = next(ln for ln in (ws / "AGENTS.md").read_text().splitlines()
                if ln.startswith("- 11 files in"))
    # Honest about the mix rather than describing all eleven as whichever one sorted first.
    assert "json" in line and "tabular" in line


def test_a_folder_partitioned_to_the_day_still_collapses(tmp_path: Path):
    """Grouping by the immediate parent alone is defeated by a normal shape: partitioned to the day,
    every folder holds one file, so the block would still grow with the file count on every turn —
    the whole cost this collapse removes. The deepest level rolls up until it does not."""
    orch = _orch(tmp_path, FakeAssetProvider(root=tmp_path / "mounts"))
    ws = orch.project(start_preview=False).workspace.path
    mount = Path(next(a.mount_path for a in orch._assets.list_datasets("Sage")
                      if a.name == "sales_2026"))
    for day in range(1, 25):
        f = mount / "raw" / "2026" / f"{day:02d}" / "part.csv"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("a,b\n1,2\n")

    orch.attach_folder(_dataset_id(orch), "raw")

    block = [ln for ln in (ws / "AGENTS.md").read_text().splitlines() if ln.startswith("- ")]
    # `<subpath>`, never `<name>`: the roll-up put the line's folder a level ABOVE where the files
    # are, and `raw/2026/part.csv` does not exist. An agent following that pattern would get the SPA
    # fallback instead of the CSV and "fix" it by copying the file into `src/` — the leak this block
    # exists to prevent.
    assert block == [("- 24 files in `public/data/sales_2026/raw/2026` — CSV — 2 columns, 1 rows "
                     "— fetch `data/sales_2026/raw/2026/<subpath>` (relative to base) "
                     "— from dataset **sales_2026**")]


def test_a_folder_whose_files_really_are_in_it_still_says_name(tmp_path: Path):
    """The other branch. `<subpath>` where the roll-up moved the line up, `<name>` where it did not
    — a pattern that is vaguer than the paths it describes teaches less than it could."""
    orch, ds, ws = _ready(tmp_path, per_year=8)      # 16 files, two folders, neither rolled up

    orch.attach_folder(ds, "raw")

    block = [ln for ln in (ws / "AGENTS.md").read_text().splitlines() if ln.startswith("- ")]
    assert all("<name>` (relative to base)" in ln for ln in block)
    assert len(block) == 2


def test_the_collapse_never_rolls_two_datasets_into_one_line(tmp_path: Path):
    """The floor is one line per Dataset. Rolling past it would say `public/data`, and the served
    path is the one thing this block exists to be exact about."""
    provider = FakeAssetProvider(root=tmp_path / "mounts")
    orch = _orch(tmp_path, provider)
    ws = orch.project(start_preview=False).workspace.path
    for asset in provider.assets:
        for i in range(6):
            f = Path(asset.mount_path) / "part" / f"{i:02d}" / "f.csv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("a,b\n1,2\n")
        orch.attach_folder(asset.id, "part")

    folders = [ln.split("`")[1] for ln in (ws / "AGENTS.md").read_text().splitlines()
               if ln.startswith("- ") and " files in " in ln]

    assert sorted(folders) == ["public/data/app_logs/part",
                               "public/data/customer_pii/part",
                               "public/data/sales_2026/part"]


def test_one_constant_governs_the_collapse(tmp_path: Path, monkeypatch):
    """The threshold is a number, not a rule written twice. ADR-0030's `@` menu will follow the
    same one, so the block the agent reads every turn and the menu a person picks from cannot come
    to disagree — which only holds while the block reads the constant rather than a literal."""
    orch, ds, ws = _ready(tmp_path, per_year=3)          # 6 files
    monkeypatch.setattr(svc, "FOLDER_COLLAPSE_THRESHOLD", 5)

    orch.attach_folder(ds, "raw")

    agents = (ws / "AGENTS.md").read_text()
    assert "- 3 files in `public/data/sales_2026/raw/2024`" in agents
    assert "- disk `public/data/sales_2026/raw/2024/part-0.csv`" not in agents


# --- Bulk is offered only where the size is knowable -------------------------------------------


def test_an_unmounted_dataset_refuses_the_folder_act_with_a_reason(tmp_path: Path):
    """Every file would come down through `_download_attachment`, one at a time, with nothing to
    report an unbounded serial download through. (The sizes ARE known here since #153; that was the
    second reason ADR-0029 gave, and this one is the reason that survived it.)"""
    orch = _orch(tmp_path, _Unmounted())
    orch.project(start_preview=False)

    with pytest.raises(FolderActUnavailable) as refused:
        orch.attach_folder("ds_shared", "raw")

    assert "mounted" in refused.value.reason


def test_the_listing_says_whether_the_folder_act_is_available(tmp_path: Path):
    """The row draws the reason the refusal would carry, so the two cannot disagree — and a
    reason is only worth drawing if the act is genuinely unavailable."""
    orch, ds, _ = _ready(tmp_path)
    assert orch.list_asset_files(ds)["folder_act"] == {"available": True, "reason": ""}

    orch = _orch(tmp_path / "b", _Unmounted())
    orch.project(start_preview=False)
    unavailable = orch.list_asset_files("ds_shared")["folder_act"]
    assert unavailable["available"] is False and "mounted" in unavailable["reason"]


def test_a_truncated_listing_refuses_the_folder_act_at_every_level(tmp_path: Path, monkeypatch):
    """The walk is sorted, so truncation cuts the tail: early folders are whole, late ones are cut
    or absent, and nothing downstream can tell which. No subtree can be proven complete."""
    monkeypatch.setattr(assets, "_MAX_FILES", 3)
    orch, ds, _ = _ready(tmp_path, per_year=3)

    assert orch.list_asset_files(ds)["folder_act"]["available"] is False
    for folder in ("", "raw", "raw/2024"):
        with pytest.raises(FolderActUnavailable) as refused:
            orch.attach_folder(ds, folder)
        assert "list" in refused.value.reason
    assert orch.project().attached == []


# --- The route the Workbench reaches it through ------------------------------------------------


@pytest.fixture
def route(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch, ds, ws = _ready(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app), ds, orch, ws


def _post(client, ds, folder):
    return client.post(f"/api/project/assets/{ds}/files/attach-folder", json={"folder": folder})


def test_the_route_attaches_the_subtree_and_says_what_it_did(route):
    client, ds, orch, _ = route

    body = _post(client, ds, "raw/2024").json()

    assert body["attached"] == 2 and body["dataset"] == "sales_2026"
    assert body["folder"] == "raw/2024" and body["bytes"] > 0
    assert len(orch.project().attached) == 2


def test_the_route_takes_the_root_as_the_same_act(route):
    client, ds, _orch, _ = route

    assert _post(client, ds, "").json()["attached"] == 6
    # A body with no `folder` at all is malformed, never a silent whole-Dataset attach.
    assert client.post(f"/api/project/assets/{ds}/files/attach-folder", json={}).status_code == 400


def test_the_route_refuses_a_folder_the_dataset_does_not_hold(route):
    client, ds, _, _ = route
    assert _post(client, ds, "raw/2027").status_code == 404


def test_the_act_refuses_rather_than_overwrite_a_file_it_did_not_put_there(route):
    """`_link_attachment` replaces whatever is at the path, which is right for a stale symlink and
    for a single attach. Over a folder it is not: destroyed bytes are the one thing the unwind
    cannot give back, so this is settled before the first link rather than after the fifth."""
    client, ds, orch, ws = route
    theirs = ws / "public" / "data" / "sales_2026" / "raw" / "2024" / "part-1.csv"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text("not Sage's")

    refused = _post(client, ds, "raw")

    assert refused.status_code == 409
    assert "raw/2024/part-1.csv" in refused.json()["error"]
    assert theirs.read_text() == "not Sage's"                 # untouched
    assert orch.project().attached == []                      # and nothing else attached either


def test_a_file_standing_where_a_directory_must_go_is_named_too(route):
    """The pre-flight has to cover the directories the links need, not only the leaves. A real file
    at `public/data/<slug>/raw` is no leaf path, so it slipped through and surfaced as a
    `NotADirectoryError` out of `mkdir` — a generic 500 in place of the refusal that names it."""
    client, ds, orch, ws = route
    theirs = ws / "public" / "data" / "sales_2026" / "raw"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text("not a directory")

    refused = _post(client, ds, "raw")

    assert refused.status_code == 409
    assert "public/data/sales_2026/raw" in refused.json()["error"]
    assert theirs.read_text() == "not a directory"
    assert orch.project().attached == []


def test_the_clobber_refusal_survives_a_workspace_reached_through_a_symlink(tmp_path: Path):
    """`_safe_join` builds on `root.resolve()`, so the path being checked is resolved. Comparing it
    against an unresolved workspace root never matched where a component of that root is a symlink:
    the ancestor walk ran past the workspace to `/`, and naming the result raised `ValueError` —
    answered as "invalid folder", a 400 for a refusal that had a path to give."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    orch = Orchestrator(
        workspace_dir=link / "code",                  # reached through the symlink, not the target
        template=_template(tmp_path),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        assets=_partitioned(tmp_path),
    )
    ws = orch.project(start_preview=False).workspace.path
    theirs = ws / "public" / "data" / "sales_2026" / "raw"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text("not a directory")

    with pytest.raises(AttachWouldClobber) as caught:
        orch.attach_folder(_dataset_id(orch), "raw")

    assert caught.value.path == "public/data/sales_2026/raw"
    assert theirs.read_text() == "not a directory"


def test_a_stale_symlink_is_replaced_rather_than_refused(route):
    """A symlink is how a re-attach works, and how this act's own leftovers clear on a retry."""
    client, ds, _orch, ws = route
    stale = ws / "public" / "data" / "sales_2026" / "raw" / "2024" / "part-1.csv"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.symlink_to(ws / "package.json")

    assert _post(client, ds, "raw").json()["attached"] == 4
    assert stale.is_symlink() and stale.read_text() == "a,b\n1,2\n"


def test_a_record_that_cannot_be_written_unwinds_the_links(route, monkeypatch):
    """Detach cannot take bytes back, so it tells the truth when the record fails to follow.
    Attach still can: the links it made unwind, and "Nothing was attached" stays a true sentence
    rather than a 500 over a data directory nobody chose."""
    client, ds, orch, ws = route

    def refuse(self, entries):
        raise OSError("read-only file system")

    monkeypatch.setattr(type(orch.project().workspace), "write_attachments", refuse)

    failed = _post(client, ds, "raw/2024")

    assert failed.status_code == 500
    assert "Nothing was attached" in failed.json()["error"]
    monkeypatch.undo()
    assert orch.project().attached == []
    assert _manifest(ws) == []
    assert not (ws / "public" / "data" / "sales_2026").exists()
    agents = (ws / "AGENTS.md").read_text() if (ws / "AGENTS.md").exists() else ""
    assert "part-0.csv" not in agents


def test_the_route_does_not_blame_the_folder_for_a_file_the_mount_lost(route):
    """The folder plainly exists — the person picked it off a row showing its count and its size —
    so "folder not found" contradicts the screen and points at the wrong thing to fix. What changed
    is the Dataset, under a listing already drawn."""
    from sage.assets.provider import DatasetFile

    client, ds, orch, _ = route
    real = orch._assets.list_files
    # A listing naming a file the mount does not hold — the state a Dataset that changed since the
    # tree was drawn leaves behind.
    def stale(asset):
        listing = real(asset)
        listing.files.append(DatasetFile("raw/2024/ghost.csv", 12))
        return listing

    orch._assets.list_files = stale

    refused = _post(client, ds, "raw/2024")

    assert refused.status_code == 404
    error = refused.json()["error"]
    assert "raw/2024/ghost.csv" in error
    assert "folder not found" not in error
    assert orch.project().attached == []          # and still nothing attached


def test_the_route_refuses_over_the_cap_with_the_three_numbers(route, monkeypatch):
    client, ds, orch, _ = route
    monkeypatch.setattr(orch, "_attach_max_bytes", 12)

    refused = _post(client, ds, "raw")

    assert refused.status_code == 413
    error = refused.json()["error"]
    assert "32 B" in error and "12 B" in error and "0 B" in error
    assert orch.project().attached == []


def test_the_route_refuses_an_unmounted_dataset_with_the_rows_own_reason(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path, _Unmounted())
    orch.project(start_preview=False)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    refused = _post(client, "ds_shared", "raw")

    assert refused.status_code == 409
    assert refused.json()["error"] == client.get(
        "/api/project/assets/ds_shared/files").json()["folder_act"]["reason"]


# --- What the Dataset tree offers, and what the click commits to -------------------------------
#
# Rendered, not grepped: every claim here is about arithmetic over a listing or about which branch
# the component took, and neither is readable in the source.


def _tree(**step) -> dict:
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node is not on PATH")
    harness = Path(__file__).resolve().parent / "js" / "dataset_folder_act_harness.mjs"
    out = subprocess.run(["node", str(harness)], input=json.dumps(step), check=False,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


_PARTITIONED = [
    {"path": "raw/2024/a.csv", "size": 1024},
    {"path": "raw/2024/b.csv", "size": 2048},
    {"path": "raw/2025/c.csv", "size": 512},
    {"path": "top.csv", "size": 100},
]


def test_every_folder_row_carries_its_count_its_size_and_the_act(tmp_path: Path):
    rows = _tree(files=_PARTITIONED, app="Desk margins")["rows"]

    assert [(r["name"], r["meta"]) for r in rows] == [
        ("All files", "4 files · 3.6 KB"),      # the Dataset root, this act at depth 0
        ("raw", "3 files · 3.5 KB"),
        ("2024", "2 files · 3.0 KB"),
        ("2025", "1 file · 512 B"),
    ]
    assert {r["act"] for r in rows} == {"Attach folder to Desk margins"}
    assert not [r for r in rows if r["disabled"]]


def test_the_filter_narrows_what_is_shown_and_never_what_the_act_takes():
    """A filter-driven attach commits a set that changes as you type, so there would be no stable
    thing to name in the confirmation. The row keeps the whole subtree's numbers."""
    rows = _tree(files=_PARTITIONED, app="Desk margins", query="a.csv")["rows"]

    assert [r["meta"] for r in rows if r["path"] == "raw/2024"] == ["2 files · 3.0 KB"]


def test_the_act_confirms_with_both_numbers_and_the_app_it_acts_on():
    """The total attach budget is what decides whether this act can succeed at all, so the number
    is on screen before the click rather than inside the refusal after it."""
    out = _tree(files=_PARTITIONED, app="Desk margins", press="raw")

    assert out["confirm"]["title"] == "Attach 3 files (3.5 KB) to Desk margins?"
    assert out["confirm"]["okText"] == "Attach folder to Desk margins"
    assert "Desk margins" in out["confirm"]["content"]
    assert out["posted"] == []          # asked, and nothing sent until somebody answers


def test_the_question_counts_what_it_would_add_not_what_the_folder_holds():
    """A file the app already carries is passed over rather than attached twice, so counting it
    into the question would promise four files and attach two — and the size it showed would not be
    the number the cap decides on."""
    part = [dict(f, attached=f["path"].endswith("a.csv")) for f in _PARTITIONED]

    out = _tree(files=part, app="Desk margins", press="raw/2024")

    # The row still says what the FOLDER holds; the question says what the act would add.
    assert [r["meta"] for r in out["rows"] if r["path"] == "raw/2024"] == ["2 files · 3.0 KB"]
    assert out["confirm"]["title"] == "Attach 1 file (2.0 KB) to Desk margins?"


def test_a_folder_the_app_already_carries_whole_offers_nothing_to_press():
    """Offered anyway it would open a question about zero files and answer with a no-op."""
    part = [dict(f, attached=True) for f in _PARTITIONED]

    rows = _tree(files=part, app="Desk margins")["rows"]

    assert all(r["disabled"] for r in rows)
    assert all("already carries" in r["reason"] for r in rows)


def test_answering_the_confirmation_sends_the_folder_and_nothing_else():
    out = _tree(files=_PARTITIONED, app="Desk margins", press="raw", confirm=True)

    assert [p["folder"] for p in out["posted"]] == ["raw"]


def test_the_dataset_root_sends_the_empty_folder_rather_than_a_second_act():
    out = _tree(files=_PARTITIONED, app="Desk margins", press="", confirm=True)

    assert [p["folder"] for p in out["posted"]] == [""]


def test_a_listing_the_server_withheld_the_act_for_says_why_on_the_row():
    """Unavailable WITH its reason at every level, in the server's own words — a row that simply
    offers nothing is indistinguishable from one nobody has built the act for."""
    reason = "This Cube isn't mounted in this workspace."
    rows = _tree(files=_PARTITIONED, app="Desk margins",
                 folder_act={"available": False, "reason": reason})["rows"]

    assert len(rows) == 4
    assert all(r["disabled"] and r["reason"] == reason for r in rows)
    assert all(r["press"] is None for r in rows)


def test_a_dataset_that_reports_no_sizes_shows_no_size():
    """An unmounted Dataset is listed through the data library, which carries no sizes — so every
    file reports 0, and "0 B" beside 43 files would be a measurement rather than a missing one."""
    rows = _tree(files=[{"path": "raw/a.csv", "size": 0}], app="Desk margins",
                 folder_act={"available": False, "reason": "not mounted"})["rows"]

    assert [r["meta"] for r in rows] == ["1 file", "1 file"]


def test_with_no_app_selected_the_act_says_that_rather_than_naming_none():
    rows = _tree(files=_PARTITIONED)["rows"]

    assert {r["act"] for r in rows} == {"Attach folder"}
    assert all(r["disabled"] and "No app selected" in r["reason"] for r in rows)


def test_a_refusal_reaches_the_person_in_the_servers_own_words():
    """Only the server's sentence can name the three numbers a cap refusal turns on, so it is
    passed through rather than retold."""
    out = _tree(files=_PARTITIONED, app="Desk margins", press="raw", confirm=True,
                refuse="Attaching this folder (3.5 KB) would take this app over the 1.0 KB limit.")

    assert [p["folder"] for p in out["posted"]] == ["raw"]   # tried, and turned down


class _Unmounted:
    """A Dataset this container has no mount for: readable, and its files report size 0."""

    def list_datasets(self, project_id):
        from sage.assets.provider import Asset
        return [Asset(id="ds_shared", name="shared_ds")]

    def list_files(self, asset):
        from sage.assets.provider import DatasetFile, FileListing
        return FileListing([DatasetFile("raw/a.csv", 0), DatasetFile("raw/b.csv", 0)])

    def download_file(self, asset, rel_path, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("x")
        return 1
