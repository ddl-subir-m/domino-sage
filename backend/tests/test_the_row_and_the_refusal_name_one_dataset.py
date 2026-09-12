"""One Dataset, two surfaces, one spelling (#266, ADR-0011).

#263 pinned the POINTER: a removal refused because a Built App carries the files names the
Dataset in the words its destination uses, and keeps naming it after a rename, though the files
go on being served from the old slug. The DESTINATION is the App dependencies modal's "Files it
carries" rows, which read `SW.util.attachmentRow` — and that drew the name the entry recorded at
attach time, or, for an entry that records no Dataset at all, the raw served path.

So the two halves are driven here together off one fixture: the server's refusal on one side, the
modal's own row on the other, asserting they say the same word about the same Dataset.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator, ResourceStillBound
from sage.router.models import ModelCatalog


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(workspace_dir=tmp / "mnt" / "code", template=template, gateway=object(),
                        catalog=_catalog(), project_id="Sage")
    orch.project(start_preview=False)
    return orch


def _new_app(orch: Orchestrator, name: str) -> str:
    app_id = orch._wm.create_app("Sage").app_id
    orch.select_app(app_id)
    orch.rename_app(app_id, name)
    return app_id


def _selected(orch: Orchestrator):
    return orch.project(start_preview=False).workspace


# --- the pointer ---------------------------------------------------------------------------------


def _refusal_carriers(tmp_path: Path) -> list[str]:
    """The refusal's own words for the SAME Dataset the harness below draws a row for: attached
    under `sales 2026`, renamed to `Sales 2026 EMEA` afterwards, still served from `sales_2026/`."""
    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "dataset:ds-3", "kind": "dataset", "name": "sales 2026"})
    first = _selected(orch).app_id
    _new_app(orch, "Desk margins")
    _selected(orch).write_attachments([
        {"path": "public/data/sales_2026/raw/q3.csv", "dataset": "sales 2026",
         "dataset_id": "ds-3", "file": "q3.csv", "source": "dataset"},
    ])
    orch.select_app(first)
    orch.project(start_preview=False).record.update_project_resources(
        lambda items: [{**r, "name": "Sales 2026 EMEA"} if r["id"] == "dataset:ds-3" else r
                       for r in items])
    with pytest.raises(ResourceStillBound) as refused:
        orch.remove_project_resource("dataset:ds-3")
    return refused.value.carriers


# --- the destination -----------------------------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "attachment_source_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)


def _report() -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _lines() -> dict:
    """The modal's "Files it carries" rows, by file name, with the line drawn under each."""
    return {row["name"]: row["by"] for row in _report()["files"]}


@needs_node
def test_the_row_names_the_dataset_by_the_name_the_project_holds_now():
    """Not the name the entry recorded. A rename moves every other surface at once — the rail, the
    card, the refusal — and a row left on the old word is the reader reconciling two spellings of
    one Dataset by hand."""
    assert _lines()["q3.csv"].startswith("Sales 2026 EMEA")


@needs_node
def test_the_row_still_says_who_added_it():
    """The provenance line #262 put here (ADR-0048). The source goes BESIDE it, in the panel's own
    separator, rather than in place of it: two facts about one row, one quiet line."""
    assert _lines()["q3.csv"] == "Sales 2026 EMEA · You added this"


@needs_node
def test_an_entry_that_records_no_dataset_shows_no_path():
    """`_rehydrate_attached`'s symlink scan records the served folder, not a name — a slug the
    client does not own the rule for. `public/data/sales_2026/raw/q2.csv` under a row already
    called `q2.csv` teaches the reader a second spelling for a Dataset that has a name everywhere
    else, so the row says nothing at all instead."""
    assert _lines()["q2.csv"] == ""


@needs_node
def test_the_two_surfaces_name_the_dataset_the_same_way(tmp_path: Path):
    """The property itself, driven across both halves rather than asserted twice in one place.
    The refusal rolls the folder up (`Sales 2026 EMEA/raw`) because a folder attach writes an entry
    per file; the row names the file itself and so needs only the Dataset in front of it. What has
    to agree is the Dataset's own word."""
    carriers = _refusal_carriers(tmp_path)
    assert carriers == ["Sales 2026 EMEA/raw"]
    named = carriers[0].split("/")[0]
    assert _lines()["q3.csv"].split(" · ")[0] == named


@needs_node
def test_the_menu_and_the_turn_read_the_same_fields_they_always_did():
    """`attachmentRow` is shared with the @ menu and `collectTurnRefs`, which is why #263 left it
    alone. Neither draws this subtitle: the menu reads the name, the path and the folder fields,
    and `collectTurnRefs` reads `row.subtitle` only on the branch a `bindingKey` opens — which an
    Attachment row, naming a path rather than a Binding, never reaches. So this says the fix landed
    on the one reader it was for."""
    rows = {row["name"]: row for row in _report()["rows"]}
    assert rows["q3.csv"]["path"] == "public/data/sales_2026/raw/q3.csv"
    assert rows["q3.csv"]["id"] == "file:public/data/sales_2026/raw/q3.csv"
    assert rows["q2.csv"]["path"] == "public/data/sales_2026/raw/q2.csv"
