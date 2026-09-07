"""ADR-0039, the belt-and-braces line: a bound Dataset with nothing attached says so (#195).

`bind_dataset` records that the app uses a Dataset. It does not attach anything, and ADR-0020 keeps
the working set out of the prompt — so the agent is told the app uses that Dataset and is handed no
path it can read. What it does with that is invent rows, and the result looks finished.

The managed block says one line about that state. One line, because the block is re-read every
turn and this is a sentence about a declaration, never a listing of what the Dataset holds.
"""
from __future__ import annotations

from pathlib import Path

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Building apps in this workspace\n\nTemplate body here.\n")
    return t


def _ready(tmp: Path) -> tuple[Orchestrator, Path]:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", assets=FakeAssetProvider(root=tmp / "mounts"),
    )
    return orch, orch.project(start_preview=False).workspace.path


def _dataset_id(orch: Orchestrator, name: str) -> str:
    return next(a["id"] for a in orch.list_assets() if a["name"] == name)


def _unreadable(ws: Path) -> list[str]:
    """The block's lines about a Dataset nothing was attached from."""
    return [ln for ln in (ws / "AGENTS.md").read_text().splitlines()
            if "has no files attached from it" in ln]


def test_a_bound_dataset_with_nothing_attached_is_named_as_unreadable(tmp_path: Path):
    """The state that makes an agent invent rows, stated instead of left silent."""
    orch, ws = _ready(tmp_path)

    orch.bind_dataset(_dataset_id(orch, "sales_2026"))

    lines = _unreadable(ws)
    assert len(lines) == 1
    assert "sales_2026" in lines[0]


def test_the_line_names_which_dataset_when_the_app_records_two(tmp_path: Path):
    """"A Dataset cannot be read" is unactionable in an app that records two of them."""
    orch, ws = _ready(tmp_path)

    orch.bind_dataset(_dataset_id(orch, "sales_2026"))
    orch.bind_dataset(_dataset_id(orch, "app_logs"))
    orch.attach_file(_dataset_id(orch, "sales_2026"), "train.csv")

    lines = _unreadable(ws)
    assert len(lines) == 1
    assert "app_logs" in lines[0] and "sales_2026" not in lines[0]


def test_a_dataset_with_files_attached_gains_no_line(tmp_path: Path):
    """The Attachment IS the readable path, so the gap the line reports is closed."""
    orch, ws = _ready(tmp_path)
    ds = _dataset_id(orch, "sales_2026")

    orch.bind_dataset(ds)
    orch.attach_file(ds, "train.csv")

    assert _unreadable(ws) == []


def test_an_attachment_alone_closes_the_gap_the_binding_opened(tmp_path: Path):
    """Detaching the last file re-opens it. The line is a function of the record on disk, not of
    the act that happened to be last."""
    orch, ws = _ready(tmp_path)
    ds = _dataset_id(orch, "sales_2026")
    orch.bind_dataset(ds)
    orch.attach_file(ds, "train.csv")

    orch.detach_file("public/data/sales_2026/train.csv")

    assert len(_unreadable(ws)) == 1


def test_an_app_with_no_dataset_binding_gains_no_line(tmp_path: Path):
    """An app that records no Dataset has no gap to report, and an unbound Dataset somebody
    attached a file from is not one either."""
    orch, ws = _ready(tmp_path)

    orch.attach_file(_dataset_id(orch, "sales_2026"), "train.csv")

    assert _unreadable(ws) == []


def test_the_dataset_contents_stay_out_of_the_block(tmp_path: Path):
    """ADR-0020. One sentence about a declaration — listing the Dataset here would put the working
    set in the prompt, which is the rule this whole design is built around."""
    orch, ws = _ready(tmp_path)

    orch.bind_dataset(_dataset_id(orch, "sales_2026"))

    text = (ws / "AGENTS.md").read_text()
    assert "train.csv" not in text and "README.md" not in text


def test_a_rehydrated_attachment_still_answers_for_its_binding(tmp_path: Path):
    """A workspace written before the attachments manifest existed has its list rebuilt by scanning
    `public/data/` for symlinks, and that scan records the served FOLDER — a slug, with the nested
    subfolders on it — where a Dataset's name would otherwise be. Matching on the name there finds
    nothing, and the line would tell the agent not to read files it can see."""
    orch, ws = _ready(tmp_path)
    ds = _dataset_id(orch, "sales_2026")
    nested = tmp_path / "mounts" / "sales_2026" / "raw" / "2026"
    nested.mkdir(parents=True)
    (nested / "part.csv").write_text("a,b\n1,2\n")
    orch.bind_dataset(ds)
    orch.attach_file(ds, "raw/2026/part.csv")

    for entry in orch.project().attached:      # exactly what the symlink scan writes
        entry.pop("dataset_id", None)
        entry["dataset"] = "sales_2026/raw/2026"
    orch.bind_dataset(ds)                      # re-binding re-derives the block from the record

    assert _unreadable(ws) == []
