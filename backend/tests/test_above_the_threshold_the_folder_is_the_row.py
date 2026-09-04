"""ADR-0030, "Above the threshold, the folder is the row" — the `@` menu follows ADR-0029.

The menu shows eight rows. After a 200-file attach that is a window onto a list nobody can see,
and eight rows out of two hundred read as a complete list. So above the SAME threshold the
`AGENTS.md` block collapses at, the folder becomes the row, and a single file is reached by typing
enough of its name — which works because the query reads the whole path.

Two halves are asserted here, and they are the two that can drift apart:

  * WHICH folder a file belongs to is decided once, server-side, by the function the block already
    groups with. The menu is handed the answer per attachment (`menu_folder`) rather than a copy of
    the rule, so the block the agent re-reads every turn and the menu the person picks from cannot
    come to disagree about what a Dataset mention means. `FOLDER_COLLAPSE_THRESHOLD` is read in one
    place and spelled in one language.
  * A folder mention costs real server work. `_resolve_mentions` honours exact manifest paths only,
    so a folder token has to expand to its member paths AND collapse their descriptors — one folder
    summary, never N `detail` blocks. Without the collapse the folder row re-introduces exactly the
    context bloat ADR-0029 removed, through the other door.

The menu itself — what a row draws, what a pick inserts, what a turn then carries — is read off the
real components in `js/mention_folder_row_harness.mjs`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator import service as svc
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

_HARNESS = Path(__file__).resolve().parent / "js" / "mention_folder_row_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


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


def _partitioned(tmp: Path, *, per_year: int = 8) -> FakeAssetProvider:
    """The seeded `sales_2026` mount, partitioned by year, plus a partition holding ONE file.

    The lone partition is not decoration: a folder of one is described exactly as well by naming
    the file, so the block keeps the file line for it, and this asserts the menu and the mention
    keep it too.
    """
    provider = FakeAssetProvider(root=tmp / "mounts")
    mount = Path(next(a.mount_path for a in provider.assets if a.name == "sales_2026"))
    for year in ("2024", "2025"):
        for i in range(per_year):
            f = mount / "raw" / year / f"part-{i}.csv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("a,b\n1,2\n")
    lone = mount / "raw" / "2026" / "only.csv"
    lone.parent.mkdir(parents=True, exist_ok=True)
    lone.write_text("a,b\n9,9\n")
    return provider


def _dataset_id(orch: Orchestrator, name: str = "sales_2026") -> str:
    return next(a["id"] for a in orch.list_assets() if a["name"] == name)


def _ready(tmp: Path, **kw) -> tuple[Orchestrator, str, Path]:
    orch = _orch(tmp, _partitioned(tmp, **kw))
    ws = orch.project(start_preview=False).workspace.path
    return orch, _dataset_id(orch), ws


P2024 = "public/data/sales_2026/raw/2024"
P2025 = "public/data/sales_2026/raw/2025"
P2026 = "public/data/sales_2026/raw/2026"


# --- One rule, read once, for both surfaces ----------------------------------------------------


def test_above_the_threshold_every_attachment_is_told_which_folder_is_its_row(tmp_path: Path):
    """The menu is handed the ANSWER, not the rule. Grouping is a roll-up loop with a floor, and a
    second copy of it in JavaScript is exactly how the two surfaces come to disagree."""
    orch, ds, _ = _ready(tmp_path)

    orch.attach_folder(ds, "raw")

    status = orch.project().status()["attached"]
    assert len(status) == 17
    assert {e["menu_folder"] for e in status} == {P2024, P2025, P2026}
    for entry in status:
        assert entry["path"].startswith(entry["menu_folder"] + "/")


def test_the_menu_folders_are_the_folders_the_agents_block_names(tmp_path: Path):
    """One rule across both surfaces. The block the agent re-reads every turn and the menu the
    person picks from group the same files the same way, because one function groups them."""
    orch, ds, ws = _ready(tmp_path)

    orch.attach_folder(ds, "raw")

    named = {ln.split("`")[1] for ln in (ws / "AGENTS.md").read_text().splitlines()
             if ln.startswith("- ") and " files in `" in ln}
    folders = {e["menu_folder"] for e in orch.project().status()["attached"] if e["menu_folder"]}
    # The lone partition draws a FILE line in the block, so it is named by its file, not its
    # folder — the menu is told its folder all the same, and collapses it back to a file row for
    # the same reason the block does: a group of one.
    assert named == {P2024, P2025}
    assert folders == {P2024, P2025, P2026}


def test_below_the_threshold_no_attachment_carries_a_menu_folder(tmp_path: Path):
    """Per-file rows are right for five files and ruinous for two hundred. Below the threshold the
    menu goes on showing files, and says so by being handed no folder at all."""
    orch, ds, _ = _ready(tmp_path, per_year=2)

    orch.attach_folder(ds, "raw")

    status = orch.project().status()["attached"]
    assert len(status) == 5
    assert all(not e["menu_folder"] for e in status)


def test_the_threshold_is_read_and_not_copied(tmp_path: Path, monkeypatch):
    """Moving the one constant moves both surfaces. A second spelling of it is the defect this
    test exists to catch, so it is asserted the only way a copy cannot survive."""
    orch, ds, _ = _ready(tmp_path, per_year=2)
    monkeypatch.setattr(svc, "FOLDER_COLLAPSE_THRESHOLD", 3)

    orch.attach_folder(ds, "raw")

    assert all(e["menu_folder"] for e in orch.project().status()["attached"])


def test_every_attachment_is_told_how_big_its_folder_row_is(tmp_path: Path):
    """The count comes with the folder, because the menu applies the collapse to what a QUERY
    matched and a count derived from that would say 3 while the pick carried 12. The row stands for
    the folder, so it says the folder's size."""
    orch, ds, _ = _ready(tmp_path)

    orch.attach_folder(ds, "raw")

    sizes = {e["menu_folder"]: e["menu_folder_count"] for e in orch.project().status()["attached"]}
    assert sizes == {P2024: 8, P2025: 8, P2026: 1}


def test_the_manifest_never_learns_the_menus_folder(tmp_path: Path):
    """The record is still one entry per file, exactly the shape a single attach writes (ADR-0029).
    `menu_folder` is computed for the payload and belongs to nothing on disk — rehydrate, detach,
    leak detection and the commit backstop must not meet a second entry shape."""
    orch, ds, ws = _ready(tmp_path)

    orch.attach_folder(ds, "raw")
    orch.project().status()

    record = json.loads((ws / ".sage" / "attachments.json").read_text())
    assert record and all("menu_folder" not in e for e in record)
    assert all("menu_folder" not in e for e in orch.project().attached)


# --- A folder token expands, and its descriptors collapse --------------------------------------


def test_a_folder_mention_carries_every_file_under_it(tmp_path: Path):
    """`_resolve_mentions` honours exact manifest paths only, so a folder token resolved to nothing
    before this: the mention silently carried NOTHING, which is the one outcome ADR-0030 rules
    out."""
    orch, ds, _ = _ready(tmp_path)
    orch.attach_folder(ds, "raw")

    out = orch._resolve_mentions(orch.project(), [P2024])

    assert out and len(out) == 1
    assert out[0]["path"] == P2024
    assert out[0]["summary"].startswith("8 files")


def test_a_folder_mention_is_one_summary_and_never_n_detail_blocks(tmp_path: Path):
    """The collapse is the whole cost of the row. Eight descriptors inlined for eight files is the
    context bloat ADR-0029 removed, arriving through the other door — and two hundred would wedge
    the turn the way attachment-driven bloat already wedged OpenCode once."""
    orch, ds, _ = _ready(tmp_path)
    orch.attach_folder(ds, "raw")

    out = orch._resolve_mentions(orch.project(), [P2024])

    assert [i["detail"] for i in out] == [""]
    per_file = orch._resolve_mentions(orch.project(), [f"{P2024}/part-0.csv"])
    assert per_file[0]["detail"]
    assert len(json.dumps(out)) < len(json.dumps(per_file))


def test_the_folder_summary_names_the_shape_its_files_share(tmp_path: Path):
    """A folder of CSVs sharing one schema is described BETTER once than eight times, which is why
    the collapse is not a loss. Same sentence the block builds, from the same helper."""
    orch, ds, _ = _ready(tmp_path)
    orch.attach_folder(ds, "raw")

    out = orch._resolve_mentions(orch.project(), [P2024])

    entries = [e for e in orch.project().attached if e["path"].startswith(P2024 + "/")]
    shape = orch._shared_shape([orch._descriptor(orch.project(), e) for e in entries])
    assert out[0]["summary"] == f"8 files — {shape}"


def test_a_folder_holding_one_file_is_carried_as_that_file(tmp_path: Path):
    """Naming the file describes it exactly as well as summarising would, and better — the same
    branch the block takes, for the same reason. A folder path handed to the agent is a path its
    read tool cannot use."""
    orch, ds, _ = _ready(tmp_path)
    orch.attach_folder(ds, "raw")

    out = orch._resolve_mentions(orch.project(), [P2026])

    assert [i["path"] for i in out] == [f"{P2026}/only.csv"]
    assert out[0]["detail"]


def test_a_folder_mention_never_inlines_an_image(tmp_path: Path):
    """Images ride the prompt as data URIs. A folder of two hundred would ride two hundred, which
    is the same bloat measured in megabytes."""
    orch, ds, _ = _ready(tmp_path)
    mount = Path(next(a.mount_path for a in orch._assets.list_datasets("Sage")
                      if a.name == "sales_2026"))
    shots = mount / "raw" / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (shots / f"{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    orch.attach_folder(ds, "raw")

    out = orch._resolve_mentions(orch.project(), ["public/data/sales_2026/raw/shots"])

    assert len(out) == 1
    assert "image_uri" not in out[0]


def test_a_folder_mention_persists_the_shapes_it_had_to_work_out(tmp_path: Path):
    """`_descriptor` caches into the entry and leaves the write to its caller (ADR-0029). A folder
    mention describes every member, so the turn that pays for eight descriptions has to be the only
    turn that pays — and `_descriptor` fills the cache in place, so freshness cannot be read after
    the describing is done."""
    orch, ds, ws = _ready(tmp_path)
    orch.attach_folder(ds, "raw")
    record = ws / ".sage" / "attachments.json"
    for e in orch.project().attached:
        e.pop("descriptor", None)
    record.write_text(json.dumps(orch.project().attached))

    orch._resolve_mentions(orch.project(), [P2024])

    written = json.loads(record.read_text())
    described = [e for e in written if e["path"].startswith(P2024 + "/")]
    assert described and all(e.get("descriptor") for e in described)


def test_a_folder_mention_whose_files_are_gone_is_refused_like_a_file(tmp_path: Path):
    """The file branch checks the path on disk before promising it. A folder branch that skipped
    the check would hand the agent a confident path resolving to the SPA fallback — the failure the
    managed block spends a paragraph warning about — and the refusal would stay silent about it.

    A Dataset can go away under an app: the mount is unmounted, or a rehydrate leaves the symlinks
    dangling. The record still says the files are attached, and only the disk says otherwise."""
    orch, ds, _ = _ready(tmp_path)
    orch.attach_folder(ds, "raw")
    project = orch.project()
    shutil.rmtree(next(a.mount_path for a in orch._assets.list_datasets("Sage")
                       if a.name == "sales_2026"))

    resolved = orch._resolve_mentions(project, [P2024])
    line, rows = orch._unusable_mentions(project, resolved, [P2024], None)

    assert resolved is None
    # NOT "not attached to this app": it is attached, its row is in the panel, and attaching it
    # again is not the way back. No row either, because no one button closes this.
    assert "this app holds it, but its files aren't in the workspace" in line
    assert rows == []


def test_a_folder_mention_carries_the_files_that_are_still_there(tmp_path: Path):
    """Half a partition gone is not the whole mention gone. The count is what the turn could
    actually read, so the sentence the agent gets and the files behind it agree."""
    orch, ds, ws = _ready(tmp_path)
    orch.attach_folder(ds, "raw")
    for i in range(5):
        (ws / P2024 / f"part-{i}.csv").unlink()

    out = orch._resolve_mentions(orch.project(), [P2024])

    assert out[0]["summary"].startswith("3 files")


def test_a_folder_mention_is_not_reported_as_something_the_turn_dropped(tmp_path: Path):
    """The refusal reads what the turn USED, off `_resolve_mentions`' own answer. A folder mention
    that worked and was reported as dropped would be the warning telling on itself."""
    orch, ds, _ = _ready(tmp_path)
    orch.attach_folder(ds, "raw")
    project = orch.project()

    resolved = orch._resolve_mentions(project, [P2024, P2026])
    line, rows = orch._unusable_mentions(project, resolved, [P2024, P2026], None)

    assert (line, rows) == ("", [])


def test_a_path_that_names_neither_a_file_nor_a_folder_is_still_refused(tmp_path: Path):
    """Silence is the outcome ruled out, in both directions. Widening the lookup to folders must
    not widen it to anything else: a path the app does not hold still gets its sentence."""
    orch, ds, _ = _ready(tmp_path)
    orch.attach_folder(ds, "raw")
    project = orch.project()
    stray = "public/data/sales_2026/raw/2027"

    resolved = orch._resolve_mentions(project, [stray])
    line, _ = orch._unusable_mentions(project, resolved, [stray], None)

    assert resolved is None
    assert "not attached to this app" in line


def test_a_folder_mention_names_the_set_its_row_and_its_block_line_name(tmp_path: Path):
    """The groups sit at mixed depths, so a Dataset with loose files beside a partitioned subtree
    gives a shallow group AND a deep one. Read by walking the paths under a prefix, the shallow
    mention would carry both — the deeper row's files a second time — while its own caption and its
    own block line both said two. One grouping, so the row, the block and the turn agree."""
    orch = _orch(tmp_path, _partitioned(tmp_path))
    ws = orch.project(start_preview=False).workspace.path
    mount = Path(next(a.mount_path for a in orch._assets.list_datasets("Sage")
                      if a.name == "sales_2026"))
    for name in ("loose-a.csv", "loose-b.csv"):
        (mount / name).write_text("a,b\n1,2\n")
    orch.attach_folder(_dataset_id(orch), "")
    root = "public/data/sales_2026"

    out = orch._resolve_mentions(orch.project(), [root])

    assert out[0]["summary"].startswith("4 files")   # the loose files, not the partitions too
    line = next(ln for ln in (ws / "AGENTS.md").read_text().splitlines()
                if ln.startswith(f"- 4 files in `{root}`"))
    assert line
    sizes = {e["menu_folder"]: e["menu_folder_count"]
             for e in orch.project().status()["attached"]}
    assert sizes[root] == 4


def test_a_path_that_is_no_folder_row_carries_nothing(tmp_path: Path):
    """Widening the lookup to folders must not widen it to every prefix. `public/data` is above the
    grouping's floor and names no row anybody was offered, and a caller-supplied one would otherwise
    resolve to every attachment in the app at once."""
    orch, ds, _ = _ready(tmp_path)
    orch.attach_folder(ds, "raw")

    assert orch._resolve_mentions(orch.project(), ["public/data"]) is None
    assert orch._resolve_mentions(orch.project(), ["public/data/sales_2026/raw/202"]) is None


# --- The menu itself ---------------------------------------------------------------------------


def _run(payload: dict) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(payload), check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_above_the_threshold_the_menu_draws_folders_and_says_how_many(tmp_path: Path):
    """Eight rows out of two hundred read as a complete list. Folder rows are the whole list, and
    each says what it stands for — the count is the one thing a person needs before picking."""
    report = _run({"prompts": []})

    assert [row["name"] for row in report["menuOpen"]] == ["2024", "2025", "only.csv"]
    assert report["menuOpen"][0]["captions"] == ["12 files"]
    # The lone partition is a group of one, so it draws its file — the block's rule, the menu's row.
    assert report["menuOpen"][2]["captions"] == ["file"]


@needs_node
def test_a_single_file_is_reached_by_typing_enough_of_its_name(tmp_path: Path):
    """The other half of the decision. Collapsing without a query that reads the path would leave
    every file in a partitioned Dataset unreachable, which is worse than the menu it replaces."""
    report = _run({"prompts": []})

    assert [row["name"] for row in report["menuOneFile"]] == ["part-3.csv"]
    assert report["menuOneFile"][0]["captions"] == ["2024"]
    assert [row["name"] for row in report["menuPartition"]] == ["2024"]
    assert report["menuPartition"][0]["captions"] == ["12 files"]


@needs_node
def test_a_folder_row_reports_the_folders_size_and_not_the_querys(tmp_path: Path):
    """The collapse is applied to what the query matched, but the PICK carries the folder. A count
    taken from the match would say three and send twelve — the over-carry this whole area exists to
    prevent, arriving as a number instead of a token."""
    report = _run({"prompts": []})

    assert [row["name"] for row in report["menuNarrowed"]] == ["2024"]
    assert report["menuNarrowed"][0]["captions"] == ["12 files"]


@needs_node
def test_two_folders_with_one_name_do_not_draw_two_identical_rows(tmp_path: Path):
    """The defect ADR-0030 is about, arriving at the row that replaced it. Two Datasets partitioned
    by year both offer a `2024`, and two identical rows inserting two different tokens is the state
    where the right one cannot be seen, let alone picked."""
    report = _run({"prompts": []})

    assert [row["name"] for row in report["menuColliding"]] == ["2024", "2024"]
    assert [row["captions"] for row in report["menuColliding"]] == [
        ["12 files in sales/raw"], ["12 files in costs/raw"]]
    assert report["insertedColliding"] == "@sales/raw/2024"


@needs_node
def test_below_the_threshold_the_menu_goes_on_showing_files(tmp_path: Path):
    """The threshold is the server's, and the menu never second-guesses it: handed no folder, it
    draws what it always drew."""
    report = _run({"prompts": []})

    assert [row["name"] for row in report["menuSmallApp"]] == ["a.csv", "b.csv"]


@needs_node
def test_a_folder_row_inserts_a_token_and_the_turn_carries_the_folder(tmp_path: Path):
    """A picked row has to reach the server as a path the server honours. The token is derived by
    the util the turn reads it back with, so the two cannot drift."""
    report = _run({"prompts": ["chart the trend from @2024"]})

    assert report["insertedFolder"] == "@2024"
    assert report["sent"] == [{
        "prompt": "chart the trend from @2024",
        "mentions": ["public/data/sales/raw/2024"],
    }]


@needs_node
def test_chat_keeps_its_file_rows(tmp_path: Path):
    """Chat gains no folder act (ADR-0029) and resolves its tokens against the Conversation's own
    chips, where a folder is not a chip. The menu offers a folder exactly where a folder mention
    can be honoured."""
    report = _run({"prompts": []})

    assert all(row["name"].endswith(".csv") for row in report["menuChat"])
