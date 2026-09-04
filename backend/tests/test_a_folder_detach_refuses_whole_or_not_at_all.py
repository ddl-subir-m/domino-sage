"""ADR-0029, "No partial state, in either direction" — the removal half of the folder act.

`attach_folder` measures the subtree first and refuses the WHOLE act over the cap. **Remove folder
from `<app>`** mirrors it and mirrors its refusal: for a folder of 200 where 3 are still referenced
by the app's own source, the whole detach is refused and those 3 are named. A part-detached folder
would leave the Built App fetching data that is no longer there, which is the same "a decision made
for them, badly" the attach half turned down.

What each half of that buys is asserted separately, because they pull in opposite directions:

  * the REFUSAL is all-or-nothing — nothing unlinks, nothing leaves the record, and the sentence
    names the files rather than a count;
  * the REMOVAL is one write per record and not one per file, and one reference SCAN over the whole
    set rather than one per file — the cost `_scan_app_sources` carries is the reason the ADR says
    "the reference scan runs once over the set, not 200 times";
  * the removal reads the APP's own record and nothing else, so a Dataset that has since lost its
    mount, or grown past the listing cap, can still have its folder taken back out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator import service as svc
from sage.orchestrator.service import DataReferenced, Orchestrator
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp: Path, assets_provider) -> Orchestrator:
    return Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        assets=assets_provider,
    )


def _partitioned(tmp: Path, *, per_year: int = 2, body: str = "a,b\n1,2\n") -> FakeAssetProvider:
    """The seeded `sales_2026` mount, with a partitioned folder under it.

    A basename appears once across the whole mount, because `_data_usage` matches a reference by
    basename as well as by served path — two partitions holding `part-0.csv` would make every claim
    here about one folder true of the other."""
    provider = FakeAssetProvider(root=tmp / "mounts")
    mount = Path(next(a.mount_path for a in provider.assets if a.name == "sales_2026"))
    for year in ("2024", "2025"):
        for i in range(per_year):
            f = mount / "raw" / year / f"part-{year}-{i}.csv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body)
    return provider


def _dataset_id(orch: Orchestrator, name: str = "sales_2026") -> str:
    return next(a["id"] for a in orch.list_assets() if a["name"] == name)


def _manifest(ws: Path) -> list[dict]:
    record = ws / ".sage" / "attachments.json"
    return json.loads(record.read_text()) if record.is_file() else []


def _ready(tmp: Path, **kw) -> tuple[Orchestrator, str, Path]:
    """An app already carrying `raw/2024` and `raw/2025`, which is what a detach acts on."""
    orch = _orch(tmp, _partitioned(tmp, **kw))
    ws = orch.project(start_preview=False).workspace.path
    ds = _dataset_id(orch)
    orch.attach_folder(ds, "raw")
    return orch, ds, ws


def _paths(orch: Orchestrator) -> set[str]:
    return {e["path"] for e in orch.project().attached}


_HELD = {
    "public/data/sales_2026/raw/2024/part-2024-0.csv",
    "public/data/sales_2026/raw/2024/part-2024-1.csv",
    "public/data/sales_2026/raw/2025/part-2025-0.csv",
    "public/data/sales_2026/raw/2025/part-2025-1.csv",
}


# --- The removal, when nothing stands in its way ------------------------------------------------


def test_removing_a_folder_takes_every_file_below_it(tmp_path: Path):
    orch, ds, ws = _ready(tmp_path)

    out = orch.detach_folder(ds, "raw/2024")

    assert out["detached"] == 2
    assert out["dataset"] == "sales_2026"
    assert out["folder"] == "raw/2024"
    assert _paths(orch) == {p for p in _HELD if "2025" in p}
    assert not (ws / "public" / "data" / "sales_2026" / "raw" / "2024").exists()


def test_the_dataset_root_is_the_same_detach_at_depth_zero(tmp_path: Path):
    """The whole Dataset is this act at depth 0, not a second one with its own name."""
    orch, ds, ws = _ready(tmp_path)

    assert orch.detach_folder(ds, "")["detached"] == 4
    assert orch.project().attached == []
    assert not (ws / "public" / "data" / "sales_2026").exists()


def test_a_folder_the_app_carries_none_of_is_a_no_op_not_a_failure(tmp_path: Path):
    """The end state asked for already holds — the same no-op the attach half makes when the app
    already carries every file below a folder."""
    orch, ds, _ = _ready(tmp_path)
    orch.detach_folder(ds, "raw/2024")

    out = orch.detach_folder(ds, "raw/2024")

    assert out["detached"] == 0
    assert _paths(orch) == {p for p in _HELD if "2025" in p}


def test_the_dataset_bytes_are_never_touched(tmp_path: Path):
    """Removal takes the app's copy and the declaration. The Dataset's own bytes are not Sage's."""
    orch, ds, _ = _ready(tmp_path)
    mount = Path(next(a["mount_path"] for a in orch.list_assets() if a["name"] == "sales_2026"))

    orch.detach_folder(ds, "")

    assert sorted(p.name for p in (mount / "raw" / "2024").iterdir()) == \
        ["part-2024-0.csv", "part-2024-1.csv"]


def test_a_sibling_folder_with_a_shared_name_prefix_is_not_taken_too(tmp_path: Path):
    """`raw/2024` names a folder, never a prefix of a file path — `raw/2024x/` is a different one."""
    orch = _orch(tmp_path, _partitioned(tmp_path))
    orch.project(start_preview=False)
    mount = Path(next(a.mount_path for a in orch._assets.assets if a.name == "sales_2026"))
    (mount / "raw" / "2024x").mkdir(parents=True, exist_ok=True)
    (mount / "raw" / "2024x" / "part-x.csv").write_text("a,b\n1,2\n")
    ds = _dataset_id(orch)
    orch.attach_folder(ds, "raw")

    orch.detach_folder(ds, "raw/2024")

    assert "public/data/sales_2026/raw/2024x/part-x.csv" in _paths(orch)


def test_a_dataset_sharing_a_slug_keeps_its_own_files(tmp_path: Path):
    """`_slug` collapses punctuation, so `my data` and `my-data` land in one `public/data/my_data`
    tree. For the block that describes them that is a naming wrinkle; here it would delete."""
    orch, ds, ws = _ready(tmp_path)
    twin = dict(orch.project().attached[0])
    twin.update(dataset_id="ds_twin", dataset="sales-2026",
                path="public/data/sales_2026/raw/2024/twin.csv", file="raw/2024/twin.csv")
    orch.project().attached.append(twin)

    out = orch.detach_folder(ds, "raw")

    assert out["detached"] == 4
    assert _paths(orch) == {"public/data/sales_2026/raw/2024/twin.csv"}


def test_a_rehydrated_entry_records_no_dataset_so_it_goes_with_its_folder(tmp_path: Path):
    """`_rehydrate_attached` fills `dataset` from the symlink's parent directory and leaves no
    `dataset_id`. There is no Dataset to attribute it to, which is why `detach_file` is keyed on the
    workspace path — the folder act inherits that rather than quietly skipping the entry."""
    orch, ds, _ = _ready(tmp_path)
    for entry in orch.project().attached:
        entry.pop("dataset_id", None)

    assert orch.detach_folder(ds, "raw")["detached"] == 4


def test_an_unlink_that_fails_leaves_the_record_describing_what_is_still_served(tmp_path: Path):
    """No unwind is possible — bytes downloaded in place of a link are gone once unlinked — so the
    guarantee is the other one: the manifest never names a file the preview no longer serves."""
    orch, ds, ws = _ready(tmp_path)
    doomed = sorted(_HELD)
    real = svc._prune_empty_dirs
    seen: list[int] = []

    def fail_after_two(start, stop):
        seen.append(1)
        if len(seen) > 2:
            raise OSError("read-only file system")
        return real(start, stop)

    svc._prune_empty_dirs = fail_after_two
    try:
        with pytest.raises(OSError):
            orch.detach_folder(ds, "raw")
    finally:
        svc._prune_empty_dirs = real

    left = _paths(orch)
    assert left == {e["path"] for e in _manifest(ws)}          # the record follows the disk
    assert all(not (ws / p).is_symlink() for p in set(doomed) - left)
    assert all((ws / p).is_symlink() for p in left)


# --- The refusal is over the whole set, and it names the files ----------------------------------


def test_one_referenced_file_refuses_the_whole_detach_and_names_it(tmp_path: Path):
    """Three of two hundred is the case in the ADR: the whole act is refused, and the refusal names
    the files rather than a count, because the files are what a person can act on."""
    orch, ds, ws = _ready(tmp_path)
    (ws / "src" / "App.tsx").write_text(
        "fetch('data/sales_2026/raw/2024/part-2024-0.csv')"
    )

    with pytest.raises(DataReferenced) as caught:
        orch.detach_folder(ds, "raw")

    assert caught.value.files == ["public/data/sales_2026/raw/2024/part-2024-0.csv"]
    assert caught.value.refs == ["src/App.tsx"]


def test_a_refusal_unlinks_nothing_and_forgets_nothing(tmp_path: Path):
    """No partial state: the 197 that could have gone stay, in the record and on disk both."""
    orch, ds, ws = _ready(tmp_path)
    (ws / "src" / "App.tsx").write_text("fetch('data/sales_2026/raw/2024/part-2024-0.csv')")

    with pytest.raises(DataReferenced):
        orch.detach_folder(ds, "raw")

    assert _paths(orch) == _HELD
    assert {e["path"] for e in _manifest(ws)} == _HELD
    for path in _HELD:
        assert (ws / path).is_symlink()


def test_bytes_inlined_into_the_source_refuse_the_detach_the_way_a_fetch_does(tmp_path: Path):
    """An inlined copy IS app logic reading the data — deleting the source file would nuke it, so
    it counts as still-used exactly as `detach_file` already reports it."""
    body = "".join(f"row{i},{i}\n" for i in range(40))
    orch, ds, ws = _ready(tmp_path, body=body)
    (ws / "src" / "App.tsx").write_text(f"const rows = `{body}`;")

    with pytest.raises(DataReferenced) as caught:
        orch.detach_folder(ds, "raw/2024")

    assert caught.value.files == [
        "public/data/sales_2026/raw/2024/part-2024-0.csv",
        "public/data/sales_2026/raw/2024/part-2024-1.csv",
    ]
    assert caught.value.copies == ["src/App.tsx"]


def test_a_raw_copy_the_agent_leaked_is_removed_rather_than_a_refusal(tmp_path: Path):
    """A same-named file under the app tree is the data copied in, with no app logic of its own —
    `detach_file` deletes it because the commit backstop stops covering it the moment the entry
    leaves the record. The folder act inherits that, so a leak is not a reason to refuse."""
    orch, ds, ws = _ready(tmp_path)
    leaked = ws / "src" / "part-2024-0.csv"
    leaked.write_text("a,b\n1,2\n")

    out = orch.detach_folder(ds, "raw/2024")

    assert out["detached"] == 2
    assert out["removed_copies"] == ["src/part-2024-0.csv"]
    assert not leaked.exists()


def test_a_source_file_that_merely_shares_a_name_is_not_deleted(tmp_path: Path):
    """`_data_usage` calls any app file with the attachment's BASENAME a copy, without reading
    either — cheap, and right for the thing it was written for, which is spotting a leaked CSV. It
    is not enough to delete on. A Dataset holding a file called `App.tsx` would take `src/App.tsx`
    with it, and the app would stop building for a name collision."""
    orch = _orch(tmp_path, _partitioned(tmp_path))
    ws = orch.project(start_preview=False).workspace.path
    mount = Path(next(a.mount_path for a in orch._assets.assets if a.name == "sales_2026"))
    (mount / "raw" / "2024" / "App.tsx").write_text("export default function DatasetOne() {}")
    ds = _dataset_id(orch)
    orch.attach_folder(ds, "raw")
    theirs = (ws / "src" / "App.tsx").read_text()

    out = orch.detach_folder(ds, "raw/2024")

    assert out["removed_copies"] == []
    assert (ws / "src" / "App.tsx").read_text() == theirs


def test_the_bytes_are_what_make_a_copy_this_acts_to_delete(tmp_path: Path):
    """Matching bytes prove the copy, so it goes; differing bytes are the app's own file under a
    name the Dataset happens to share, so it stays. One rule, whatever the extension."""
    orch = _orch(tmp_path, _partitioned(tmp_path))
    ws = orch.project(start_preview=False).workspace.path
    mount = Path(next(a.mount_path for a in orch._assets.assets if a.name == "sales_2026"))
    (mount / "raw" / "2024" / "rows.json").write_text('[{"a": 1}]')
    (mount / "raw" / "2024" / "conf.json").write_text('{"from": "the dataset"}')
    ds = _dataset_id(orch)
    orch.attach_folder(ds, "raw")
    copied = ws / "src" / "rows.json"
    copied.write_text('[{"a": 1}]')                    # byte for byte the attached file
    theirs = ws / "src" / "conf.json"
    theirs.write_text('{"from": "the app"}')           # same name, the app\'s own

    out = orch.detach_folder(ds, "raw/2024")

    assert out["removed_copies"] == ["src/rows.json"]
    assert not copied.exists()
    assert theirs.exists()


def test_an_app_asset_that_is_not_source_is_still_the_apps(tmp_path: Path):
    """`_SCAN_EXTS` is the eleven web-source extensions and nothing else, so "not source" sweeps in
    every `.svg`, `.png`, `.md` and `.txt` an app legitimately owns. A Dataset holding `logo.svg`
    must not take `src/assets/logo.svg` with it."""
    orch = _orch(tmp_path, _partitioned(tmp_path))
    ws = orch.project(start_preview=False).workspace.path
    mount = Path(next(a.mount_path for a in orch._assets.assets if a.name == "sales_2026"))
    (mount / "raw" / "2024" / "logo.svg").write_text("<svg>from the dataset</svg>")
    ds = _dataset_id(orch)
    orch.attach_folder(ds, "raw")
    theirs = ws / "src" / "assets" / "logo.svg"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text("<svg>the app's own</svg>")

    out = orch.detach_folder(ds, "raw/2024")

    assert out["removed_copies"] == []
    assert theirs.read_text() == "<svg>the app's own</svg>"


def test_a_partial_copy_is_kept_because_it_is_not_proven(tmp_path: Path):
    """The trade this act makes, stated out loud. A sample rather than a whole copy cannot be told
    from a file the person wrote, so it stays — and the cost is real: the entry leaves the record,
    the commit backstop stops covering it, and those bytes can reach git. Keeping is still the
    right way round, because a leak shows up in the next save's diff and can be taken back out,
    while a deleted file the person wrote has no undo."""
    orch, ds, ws = _ready(tmp_path)
    sample = ws / "src" / "part-2024-0.csv"
    sample.write_text("a,b\n")                     # the first row only, not the whole file

    out = orch.detach_folder(ds, "raw/2024")

    assert out["removed_copies"] == []
    assert out["kept_copies"] == ["src/part-2024-0.csv"]     # named, not silently left
    assert sample.exists()


def test_one_path_is_never_both_taken_and_left(tmp_path: Path):
    """Two attachments can share a basename and both claim the same copy, and `_is_leaked_copy` can
    prove it against one and not the other. Reported in both lists it would tell someone to go and
    check a file that is no longer there."""
    orch = _orch(tmp_path, _partitioned(tmp_path, body="a,b\n1,2\n"))
    ws = orch.project(start_preview=False).workspace.path
    mount = Path(next(a.mount_path for a in orch._assets.assets if a.name == "sales_2026"))
    (mount / "raw" / "2024" / "part.csv").write_text("a,b\n1,2\n")
    (mount / "raw" / "2025" / "part.csv").write_text("different bytes entirely\n")
    ds = _dataset_id(orch)
    orch.attach_folder(ds, "raw")
    (ws / "src" / "part.csv").write_text("a,b\n1,2\n")      # the 2024 one, copied in

    out = orch.detach_folder(ds, "raw")

    assert out["removed_copies"] == ["src/part.csv"]
    assert out["kept_copies"] == []
    assert not (ws / "src" / "part.csv").exists()


def test_a_copy_whose_attachment_cannot_be_read_is_kept(tmp_path: Path):
    """A Dataset that lost its mount leaves a dangling symlink, so there is nothing to compare
    against and nothing is proven. Same trade, same direction."""
    orch, ds, ws = _ready(tmp_path)
    copy = ws / "src" / "part-2024-0.csv"
    copy.write_text("a,b\n1,2\n")
    (ws / "public" / "data" / "sales_2026" / "raw" / "2024" / "part-2024-0.csv").unlink()

    assert orch.detach_folder(ds, "raw/2024")["removed_copies"] == []
    assert copy.exists()


def test_a_source_file_is_the_apps_own_whatever_it_is_called(tmp_path: Path):
    """The extension decides nothing. A `.tsx` sharing a Dataset file's name is the app's, and so
    is a `.svg` — the only thing that makes a file this act's to delete is being the bytes."""
    orch = _orch(tmp_path, _partitioned(tmp_path))
    ws = orch.project(start_preview=False).workspace.path
    mount = Path(next(a.mount_path for a in orch._assets.assets if a.name == "sales_2026"))
    (mount / "raw" / "2024" / "App.tsx").write_text("export default function DatasetOne() {}")
    ds = _dataset_id(orch)
    orch.attach_folder(ds, "raw")
    theirs = (ws / "src" / "App.tsx").read_text()

    out = orch.detach_folder(ds, "raw/2024")

    assert out["removed_copies"] == []
    assert (ws / "src" / "App.tsx").read_text() == theirs


def test_a_bare_name_in_unrelated_source_does_not_block_the_whole_folder(tmp_path: Path):
    """`_data_usage` also counts the bare BASENAME appearing anywhere as a reference, which errs
    towards "used" — right where the answer only warns, and wrong where it refuses. A Dataset
    holding `index.html` or `data.json` would otherwise be permanently un-removable in bulk."""
    orch = _orch(tmp_path, _partitioned(tmp_path))
    ws = orch.project(start_preview=False).workspace.path
    mount = Path(next(a.mount_path for a in orch._assets.assets if a.name == "sales_2026"))
    (mount / "raw" / "2024" / "config.json").write_text('{"from": "the dataset"}')
    ds = _dataset_id(orch)
    orch.attach_folder(ds, "raw")
    (ws / "src" / "main.jsx").write_text("import settings from './config.json';")

    assert orch.detach_folder(ds, "raw/2024")["detached"] == 3
    assert (ws / "src" / "main.jsx").exists()


def test_the_served_path_is_a_real_dependency_and_still_refuses(tmp_path: Path):
    """Source that fetches the path the app actually serves the file from is the dependency the
    refusal exists for, and it still refuses the whole act."""
    orch, ds, ws = _ready(tmp_path)
    (ws / "src" / "App.tsx").write_text("fetch('data/sales_2026/raw/2024/part-2024-0.csv')")

    with pytest.raises(DataReferenced) as caught:
        orch.detach_folder(ds, "raw")

    assert caught.value.files == ["public/data/sales_2026/raw/2024/part-2024-0.csv"]


def test_source_that_fetches_the_folder_refuses_even_with_no_file_named(tmp_path: Path):
    """The shape Sage itself asks for. The managed `AGENTS.md` block teaches the agent to build each
    URL from a pattern — fetch `data/<slug>/<folder>/<subpath>` — so no individual file's path
    appears anywhere and a per-file scan finds nothing to refuse over, while the app is left
    fetching data that is gone."""
    orch, ds, ws = _ready(tmp_path)
    (ws / "src" / "App.tsx").write_text(
        "const url = (n) => `data/sales_2026/raw/2024/${n}`;\n"
        "export default () => fetch(url(pick()));\n"
    )

    with pytest.raises(DataReferenced) as caught:
        orch.detach_folder(ds, "raw/2024")

    assert caught.value.files == []              # one line of code is the dependency, not a file
    assert caught.value.refs == ["src/App.tsx"]
    assert _paths(orch) == _HELD                 # and nothing moved


def test_a_template_over_the_parent_also_refuses_the_child(tmp_path: Path):
    """The neighbouring row in the same tree. Source that builds `data/<slug>/raw/${year}/${name}`
    reaches into `raw/2024` as surely as into `raw`, so removing the child has to refuse too — it is
    the same app left fetching data that is gone."""
    orch, ds, ws = _ready(tmp_path)
    (ws / "src" / "load.ts").write_text("const at = (y, n) => `data/sales_2026/raw/${y}/${n}`;")

    with pytest.raises(DataReferenced):
        orch.detach_folder(ds, "raw/2024")
    assert _paths(orch) == _HELD


def test_a_template_over_a_different_subtree_refuses_nothing(tmp_path: Path):
    """And the reason it is the literal the source names, rather than "the folder or any ancestor
    appears": matching the Dataset root would refuse every folder in it for an app that fetches one
    other subtree by pattern."""
    orch, ds, _ = _ready(tmp_path)
    ws = orch.project(start_preview=False).workspace.path
    (ws / "src" / "load.ts").write_text("const at = (n) => `data/sales_2026/curated/${n}`;")

    assert orch.detach_folder(ds, "raw")["detached"] == 4


def test_the_folder_refusal_names_the_source_rather_than_two_hundred_files(route):
    client, ds, _, ws = route
    (ws / "src" / "App.tsx").write_text("const at = (n) => `data/sales_2026/raw/${n}`;")

    refused = _post(client, ds, "raw")

    assert refused.status_code == 409
    body = refused.json()
    assert body["files"] == []
    assert body["refs"] == ["src/App.tsx"]
    assert "reads files from it" in body["error"]


def test_a_reference_to_a_file_outside_the_folder_does_not_refuse_it(tmp_path: Path):
    """The scan answers for the set the act names, and nothing wider."""
    orch, ds, ws = _ready(tmp_path)
    (ws / "src" / "App.tsx").write_text("fetch('data/sales_2026/raw/2025/part-2025-0.csv')")

    assert orch.detach_folder(ds, "raw/2024")["detached"] == 2


# --- One scan over the set, and one write per record --------------------------------------------


def test_the_reference_scan_runs_once_over_the_set_not_once_per_file(tmp_path: Path):
    """`_scan_app_sources` walks the whole app tree and reads every code file into memory. Per file
    that is the 200-times cost ADR-0029 exists to remove — so it is hoisted and handed in."""
    orch, ds, _ = _ready(tmp_path)
    calls: list[int] = []
    real = orch._scan_app_sources

    def counted(workspace):
        calls.append(1)
        return real(workspace)

    orch._scan_app_sources = counted

    orch.detach_folder(ds, "raw")

    assert len(calls) == 1


def test_the_whole_set_is_written_once_and_not_once_per_file(tmp_path: Path, monkeypatch):
    """One `write_attachments` for the act, not one per file — the same thing the attach half
    fixed, and for the same reason: `_write_agents_data_block` rides on every one of them."""
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
    for name in ("_write_agents_data_block", "_rebaseline_turn"):
        monkeypatch.setattr(orch, name, count(name, getattr(orch, name)))

    assert orch.detach_folder(ds, "raw")["detached"] == 12
    assert counts == {"write_attachments": 1, "_write_agents_data_block": 1,
                      "_rebaseline_turn": 1}


def test_the_agents_block_is_rewritten_once_and_stops_naming_what_left(tmp_path: Path):
    orch, ds, ws = _ready(tmp_path)

    orch.detach_folder(ds, "raw/2024")

    agents = (ws / "AGENTS.md").read_text()
    assert "raw/2024" not in agents
    assert "raw/2025/part-2025-0.csv" in agents


# --- The removal reads the app's record, and needs nothing from the Dataset ---------------------


def test_a_dataset_that_lost_its_mount_can_still_have_its_folder_removed(tmp_path: Path):
    """Bulk ATTACH is offered only where the size is knowable, because the cap has to be
    pre-flighted and the confirmation has to show real numbers. Removal has no cap and no numbers
    to find — every path and size it acts on is in the app's own manifest — so a Dataset that is
    no longer mounted, or whose listing is now truncated, does not strand what it already gave."""
    orch, ds, _ = _ready(tmp_path)
    orch._assets.assets = [
        a.__class__(id=a.id, name=a.name, tags=a.tags, project=a.project, mount_path=None)
        for a in orch._assets.assets
    ]

    assert orch.detach_folder(ds, "raw")["detached"] == 4
    assert orch.project().attached == []


def test_the_removal_acts_on_the_set_the_tree_offered_not_an_older_one(tmp_path: Path):
    """A rename plus a re-attach leaves entries under two slugs. The tree draws its offer against
    the Dataset's CURRENT name, so this has to as well — reading the record's root instead would
    take the old set, report success, and leave the files the button was drawn over still there."""
    orch, ds, _ = _ready(tmp_path)
    renamed = [a.__class__(id=a.id, name="sales-2026", tags=a.tags, project=a.project,
                           mount_path=a.mount_path) if a.id == ds else a
               for a in orch._assets.assets]
    orch._assets.assets = renamed
    orch.attach_folder(ds, "raw")                     # a second set, under the new slug

    out = orch.detach_folder(ds, "raw")

    assert out["detached"] == 4
    assert all(p.startswith("public/data/sales_2026/") for p in _paths(orch))   # the old set stays
    assert not any(p.startswith("public/data/sales-2026/") for p in _paths(orch))


def test_a_dataset_unshared_from_the_project_still_gives_its_files_back(tmp_path: Path):
    """The strongest form of the claim above, and the one that decides where the served root comes
    from. Rebuilding it from the Dataset's NAME would make a removal depend on the Dataset still
    being shared — so an unshare would strand the app's files with no bulk way out."""
    orch, ds, _ = _ready(tmp_path)
    orch._assets.assets = [a for a in orch._assets.assets if a.id != ds]

    assert orch.detach_folder(ds, "raw")["detached"] == 4
    assert orch.project().attached == []


def test_a_dataset_nothing_is_carried_from_and_nobody_lists_is_a_lookup_failure(tmp_path: Path):
    """Nothing recorded against it, so nothing to strand — and the Dataset is then the only place
    its name could come from."""
    orch, _, _ = _ready(tmp_path)

    with pytest.raises(LookupError):
        orch.detach_folder("ds_gone", "raw")


def test_a_folder_that_climbs_out_is_refused_rather_than_renormalised(tmp_path: Path):
    """`_path_parts` DROPS `..`, which is right for a file path and wrong for a folder: `../other`
    would name a subtree the caller never asked for and the act would take it."""
    orch, ds, _ = _ready(tmp_path)

    for climb in ("../raw", "raw/../2024"):
        with pytest.raises(ValueError):
            orch.detach_folder(ds, climb)
        with pytest.raises(ValueError):
            orch.attach_folder(ds, climb)
    assert _paths(orch) == _HELD

    # A leading separator is not a climb. `/raw` in a Dataset can only mean `raw`, so both doors
    # read it that way rather than one 404ing and the other answering a silent `detached: 0`.
    assert orch.detach_folder(ds, "/raw/2024")["detached"] == 2
    assert orch.detach_folder(ds, "/")["detached"] == 2


# --- The route the Workbench reaches it through -------------------------------------------------


@pytest.fixture()
def route(tmp_path, monkeypatch):
    import sage.orchestrator.app as appmod
    from fastapi.testclient import TestClient

    orch, ds, ws = _ready(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app), ds, orch, ws


def _post(client, ds, folder):
    return client.post(f"/api/project/assets/{ds}/files/detach-folder", json={"folder": folder})


def test_the_route_removes_the_subtree_and_says_what_it_did(route):
    client, ds, orch, _ = route

    body = _post(client, ds, "raw/2024").json()

    assert body["detached"] == 2
    assert body["dataset"] == "sales_2026"
    assert body["folder"] == "raw/2024"
    assert len(orch.project().attached) == 2


def test_the_route_takes_the_root_as_the_same_act(route):
    client, ds, _, _ = route

    assert _post(client, ds, "").json()["detached"] == 4
    # A body with no `folder` at all is malformed, never a silent whole-Dataset detach.
    assert client.post(f"/api/project/assets/{ds}/files/detach-folder",
                       json={}).status_code == 400


def test_the_route_refuses_with_the_files_a_person_can_go_and_fix(route):
    client, ds, _, ws = route
    (ws / "src" / "App.tsx").write_text("fetch('data/sales_2026/raw/2024/part-2024-0.csv')")

    refused = _post(client, ds, "raw")

    assert refused.status_code == 409
    body = refused.json()
    assert "part-2024-0.csv" in body["error"]
    assert body["files"] == ["public/data/sales_2026/raw/2024/part-2024-0.csv"]
    assert body["refs"] == ["src/App.tsx"]


def test_the_route_refuses_a_dataset_the_project_does_not_list(route):
    client, _, _, _ = route

    assert _post(client, "ds_gone", "raw").status_code == 404


def test_the_route_says_so_when_the_platform_is_the_thing_that_failed(route):
    """Only reachable on the one branch that asks the platform for a name. A 500 with an opaque
    body would put the blame on Sage for an outage it merely reported."""
    client, _, orch, _ = route

    def down(project_id):
        raise svc.ResourceUnavailable("Domino is not answering")

    orch._assets.list_datasets = down

    assert _post(client, "ds_gone", "raw").status_code == 502


def test_the_route_refuses_a_folder_that_climbs_out_from_both_doors(route):
    """A climb names a subtree the caller did not, so it is a 400 rather than a quiet
    renormalisation — and the same 400 from both doors."""
    client, ds, _, _ = route

    assert _post(client, ds, "../elsewhere").status_code == 400
    assert client.post(f"/api/project/assets/{ds}/files/attach-folder",
                       json={"folder": "../elsewhere"}).status_code == 400


def test_the_route_reads_a_leading_separator_as_the_folder_it_names(route):
    client, ds, orch, _ = route

    assert _post(client, ds, "/raw/2024").json()["detached"] == 2
    assert len(orch.project().attached) == 2


def test_a_folder_that_is_not_a_string_is_malformed_not_the_whole_dataset(route):
    """`0`, `false` and `[]` all read as `""` further in, which is the Dataset root — the one value
    these routes refuse to guess their way to."""
    client, ds, orch, _ = route

    for bad in (0, False, [], {}):
        assert client.post(f"/api/project/assets/{ds}/files/detach-folder",
                           json={"folder": bad}).status_code == 400
        assert client.post(f"/api/project/assets/{ds}/files/attach-folder",
                           json={"folder": bad}).status_code == 400
    # The envelope too: `[]`, a bare string and an empty body have no `folder` to read, and used to
    # reach the route as an `AttributeError` or a decode error — an opaque 500 for a malformed body.
    for envelope in ([], "raw", None):
        assert client.post(f"/api/project/assets/{ds}/files/detach-folder",
                           json=envelope).status_code == 400
        assert client.post(f"/api/project/assets/{ds}/files/attach-folder",
                           json=envelope).status_code == 400
    assert client.post(f"/api/project/assets/{ds}/files/detach-folder",
                       content=b"").status_code == 400
    assert len(orch.project().attached) == 4          # and nothing moved either way


def test_a_removal_that_stops_part_way_does_not_claim_nothing_was_removed(route, monkeypatch):
    """The record follows the disk, so some of the folder HAS gone. "Could not be removed" would
    send the person looking for files that are already out."""
    client, ds, orch, _ = route
    seen: list[int] = []
    real = svc._prune_empty_dirs

    def fail_after_one(start, stop):
        seen.append(1)
        if len(seen) > 1:
            raise OSError("read-only file system")
        return real(start, stop)

    monkeypatch.setattr(svc, "_prune_empty_dirs", fail_after_one)

    failed = _post(client, ds, "raw")

    assert failed.status_code == 500
    assert "stopped part way" in failed.json()["error"]
    monkeypatch.undo()
    assert 0 < len(orch.project().attached) < 4       # part of it is genuinely out


def test_a_failure_names_the_copies_it_deleted_before_it(route, monkeypatch):
    """A copy taken out of `src/` was never in the manifest, so the file list this failure points
    at for everything else is no record of it."""
    client, ds, _, ws = route
    (ws / "src" / "part-2024-0.csv").write_text("a,b\n1,2\n")   # a leak, deleted on the way
    seen: list[int] = []
    real = svc._prune_empty_dirs

    def fail_after_one(start, stop):
        seen.append(1)
        if len(seen) > 1:
            raise OSError("read-only file system")
        return real(start, stop)

    monkeypatch.setattr(svc, "_prune_empty_dirs", fail_after_one)

    failed = _post(client, ds, "raw")

    assert failed.status_code == 500
    assert failed.json()["removed_copies"] == ["src/part-2024-0.csv"]
    assert "src/part-2024-0.csv" in failed.json()["error"]


def test_a_record_that_cannot_be_written_says_so_rather_than_a_bare_500(route, monkeypatch):
    """The writes that commit the record are disk writes too. One failing there replaces the
    `DetachStopped` on its way out, and without its own handler that is an opaque framework 500 for
    the one failure where the manifest and the disk have come apart."""
    client, ds, orch, _ = route

    def refuse(self, entries):                       # patched on the class, so it takes `self`
        raise OSError("read-only file system")

    monkeypatch.setattr(type(orch.project().workspace), "write_attachments", refuse)

    failed = _post(client, ds, "raw")

    assert failed.status_code == 500
    assert "could not write" in failed.json()["error"]


def test_a_failure_never_says_nothing_went_and_then_names_what_did(route, monkeypatch):
    """The copies an entry justifies removing go AFTER the attachment itself. The other way round,
    a first `dest.unlink()` that failed left nothing detached and copies already deleted — a
    refusal reading "Nothing was removed" that then listed the files it had just removed."""
    client, ds, _, ws = route
    (ws / "src" / "part-0.csv").write_text("a,b\n1,2\n")     # a verbatim leak of the first file
    real = svc._safe_join

    def refuse(root, rel):
        if str(rel).startswith("public/data/"):
            raise OSError("read-only file system")
        return real(root, rel)

    monkeypatch.setattr(svc, "_safe_join", refuse)

    body = _post(client, ds, "raw").json()

    assert "Nothing was removed" in body["error"]
    assert body["removed_copies"] == []                     # and it names nothing it took
    monkeypatch.undo()
    assert (ws / "src" / "part-0.csv").exists()


def test_a_removal_that_stops_on_the_first_file_does_not_claim_a_partial(route, monkeypatch):
    """The first entry can fail as easily as the hundredth. Telling someone to go looking for files
    that all turned out to still be there is its own wrong answer."""
    client, ds, orch, _ = route

    real = svc._safe_join

    def refuse(root, rel):
        if str(rel).startswith("public/data/"):
            raise OSError("read-only file system")
        return real(root, rel)

    monkeypatch.setattr(svc, "_safe_join", refuse)      # before the first unlink, so nothing goes

    failed = _post(client, ds, "raw")

    assert failed.status_code == 500
    assert "Nothing was removed" in failed.json()["error"]
    monkeypatch.undo()
    assert len(orch.project().attached) == 4          # and nothing is


# --- What the Dataset tree offers, and what the click commits to --------------------------------


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


_MIXED = [
    {"path": "raw/2024/a.csv", "size": 1024, "attached": True},
    {"path": "raw/2024/b.csv", "size": 2048, "attached": True},
    {"path": "raw/2025/c.csv", "size": 512},
    {"path": "top.csv", "size": 100},
]


def test_a_folder_the_app_carries_offers_the_removal_beside_the_attach():
    """Both folder acts on one row, each naming the app it acts on (ADR-0011)."""
    rows = _tree(files=_MIXED, app="Desk margins")["rows"]

    by_path = {r["path"]: r for r in rows}
    assert by_path["raw/2024"]["remove"] == "Remove folder from Desk margins"
    assert by_path["raw/2024"]["removeDanger"] is True
    assert by_path[""]["remove"] == "Remove folder from Desk margins"


def test_a_folder_the_app_carries_nothing_from_offers_no_removal():
    """Offered anyway it would open a question about zero files and answer with a no-op."""
    rows = _tree(files=_MIXED, app="Desk margins")["rows"]

    assert {r["path"]: r["remove"] for r in rows}["raw/2025"] == ""


def test_the_removal_confirms_by_naming_the_count_and_the_app():
    """No cap decides a removal, so it names no size — the attach half shows one because the cap is
    what its numbers are for, and a number nothing turns on is decoration that can also be wrong."""
    out = _tree(files=_MIXED, app="Desk margins", pressRemove="raw/2024")

    assert out["confirm"]["title"] == "Remove 2 files from Desk margins?"
    assert out["confirm"]["okText"] == "Remove folder from Desk margins"
    assert out["confirm"]["danger"] is True
    assert "Desk margins" in out["confirm"]["content"]
    assert out["posted"] == []          # asked, and nothing sent until somebody answers


def test_answering_the_removal_sends_the_folder_and_nothing_else():
    out = _tree(files=_MIXED, app="Desk margins", pressRemove="raw/2024", confirm=True)

    assert [(p["path"], p["folder"]) for p in out["posted"]] == \
        [("/project/assets/ds_1/files/detach-folder", "raw/2024")]


def test_a_removal_that_stopped_part_way_still_re_reads_the_listing():
    """The one failure whose listing goes stale. `loadScopeData` refreshes what the app carries, so
    the removal count shrinks — but the listing's `attached` flags still say the files that DID go
    are carried, so their rows would offer no way to put them back."""
    out = _tree(files=_MIXED, app="Desk margins", pressRemove="raw/2024", confirm=True,
                partial="The removal stopped part way through.")

    before = {r["path"]: r for r in out["rows"]}
    after = {r["path"]: r for r in out["after"]}
    assert before["raw/2024"]["remove"] == "Remove folder from Desk margins"
    # One of the two went, so the row still carries one — and the one that LEFT is attachable
    # again, which a listing nobody re-read would have gone on calling attached.
    assert after["raw/2024"]["remove"] == "Remove folder from Desk margins"
    assert after["raw/2024"]["disabled"] is False
    assert before["raw/2024"]["disabled"] is True                   # nothing to add before it


def test_a_removal_refusal_reaches_the_person_in_the_servers_own_words():
    """Only the server's sentence can name the files the app still uses, so it is passed through
    rather than retold."""
    out = _tree(files=_MIXED, app="Desk margins", pressRemove="raw/2024", confirm=True,
                refuse="Desk margins still uses raw/2024/a.csv.")

    assert [p["folder"] for p in out["posted"]] == ["raw/2024"]   # tried, and turned down


def test_the_removal_is_offered_even_where_the_attach_is_withheld():
    """The two acts do not share a gate. Attach needs the subtree measured; removal reads the app's
    own record, so an unmounted or truncated Dataset does not strand what it already gave."""
    rows = _tree(files=_MIXED, app="Desk margins",
                 folder_act={"available": False, "reason": "not mounted"})["rows"]

    by_path = {r["path"]: r for r in rows}
    assert by_path["raw/2024"]["disabled"] is True                 # the attach
    assert by_path["raw/2024"]["remove"] == "Remove folder from Desk margins"
    assert by_path["raw/2024"]["removeDisabled"] is False


def test_the_tree_re_reads_after_an_act_rather_than_describing_the_state_before_it():
    """Every row's numbers are built from the listing's `attached` flag, so a tree that is not
    re-read goes on offering to attach what was just attached — and leaves the removal standing over
    a folder the app no longer carries."""
    out = _tree(files=_MIXED, app="Desk margins", pressRemove="raw/2024", confirm=True)

    before = {r["path"]: r for r in out["rows"]}
    after = {r["path"]: r for r in out["after"]}
    assert before["raw/2024"]["remove"] == "Remove folder from Desk margins"
    assert after["raw/2024"]["remove"] == ""                    # nothing carried there now
    assert after["raw/2024"]["act"] == "Attach folder to Desk margins"
    assert after["raw/2024"]["disabled"] is False               # and it is attachable again


def test_a_cancelled_confirmation_changes_nothing_and_costs_no_re_read():
    out = _tree(files=_MIXED, app="Desk margins", pressRemove="raw/2024")

    assert out["posted"] == []
    assert {r["path"]: r["remove"] for r in out["after"]}["raw/2024"] \
        == "Remove folder from Desk margins"


def test_the_count_comes_from_the_app_record_not_from_the_listing():
    """The server removes what the app carries under the prefix, whether or not the Dataset still
    lists it. Counting the listing would promise two and remove three."""
    carried = [
        {"path": "public/data/revenue/raw/2024/a.csv", "file": "raw/2024/a.csv",
         "dataset_id": "ds_1", "dataset": "Revenue", "size": 1024},
        {"path": "public/data/revenue/raw/2024/b.csv", "file": "raw/2024/b.csv",
         "dataset_id": "ds_1", "dataset": "Revenue", "size": 2048},
        # Attached earlier, and since deleted from the Dataset — so the listing never names it.
        {"path": "public/data/revenue/raw/2024/gone.csv", "file": "raw/2024/gone.csv",
         "dataset_id": "ds_1", "dataset": "Revenue", "size": 64},
    ]

    out = _tree(files=_MIXED, attached=carried, app="Desk margins", pressRemove="raw/2024")

    assert out["confirm"]["title"] == "Remove 3 files from Desk margins?"


def test_a_listing_that_carried_no_answer_withholds_the_attach():
    """Fails CLOSED. Reading a missing `folder_act` as available would draw an enabled button on
    exactly the Datasets the route turns down, which is what that field exists to prevent."""
    rows = _tree(files=_MIXED, app="Desk margins", folder_act=False)["rows"]

    assert all(r["disabled"] for r in rows)
    assert all("could not tell" in r["reason"] for r in rows)


def test_the_count_is_taken_against_the_root_the_server_names():
    """`_slug` is a server function, and the one copy of it stays there. The tree counts under
    whatever root the listing sends rather than rebuilding the slug from the Dataset's name."""
    carried = [{"path": "public/data/sales_data/raw/2024/a.csv", "file": "raw/2024/a.csv",
                "dataset_id": "ds_1", "dataset": "sales data", "size": 1024}]

    out = _tree(files=_MIXED, attached=carried, attach_root="public/data/sales_data/",
                app="Desk margins", pressRemove="raw/2024")

    assert out["confirm"]["title"] == "Remove 1 file from Desk margins?"


def test_a_rehydrated_workspace_still_offers_the_removal():
    """`_rehydrate_attached`'s symlink-scan fallback records no `dataset_id` at all. The server
    removes those entries deliberately, so a tree that could not find the served root without one
    would hide an act the server would have carried out."""
    carried = [{"path": "public/data/revenue/raw/2024/a.csv", "file": "raw/2024/a.csv",
                "dataset": "revenue", "size": 1024}]

    out = _tree(files=_MIXED, attached=carried, app="Desk margins", pressRemove="raw/2024")

    assert out["confirm"]["title"] == "Remove 1 file from Desk margins?"


def test_another_datasets_files_under_a_shared_slug_are_not_counted():
    """`_slug` collapses punctuation, so two Datasets can land in one `public/data/<slug>` tree. The
    row counts what the server would take, which is this Dataset's files and the rehydrated entries
    that record no Dataset at all."""
    carried = [
        {"path": "public/data/revenue/raw/2024/a.csv", "file": "raw/2024/a.csv",
         "dataset_id": "ds_1", "dataset": "Revenue", "size": 1024},
        {"path": "public/data/revenue/raw/2024/twin.csv", "file": "raw/2024/twin.csv",
         "dataset_id": "ds_other", "dataset": "Revenue-2", "size": 1},
        {"path": "public/data/revenue/raw/2024/old.csv", "file": "raw/2024/old.csv",
         "dataset": "revenue", "size": 1},                       # rehydrated: no dataset_id
    ]

    out = _tree(files=_MIXED, attached=carried, app="Desk margins", pressRemove="raw/2024")

    assert out["confirm"]["title"] == "Remove 2 files from Desk margins?"
