"""The destroy door names a Dataset by the word the Project holds TODAY (#273, ADR-0011).

Two controls, one Dataset: the menu item `Delete from {dataset}` and the confirm
`Delete {file} from {dataset}?`. Both read the name the entry recorded at ATTACH time, so a
rename on the platform sends somebody through the one door with no undo naming a Dataset the
platform calls something else.

Why #271's answer is not copied here. That ticket gave its removal RECEIPT a third sentence
rather than falling back to the recorded name, because a receipt's `dataset` can be a served
slug path (`_rehydrate_attached`) and pointing a reader at one is what ADR-0011 rules out.
Neither half of that reaches this door: `_rehydrate_attached` writes no `dataset_rel_path` and
no `source: "upload"`, so `isSageUpload` never opens the door on such an entry, and both writers
that do reach it set `dataset` from a real Dataset's `.name`. The fallback here is therefore
always a genuine Dataset name, stale at worst — and a confirm title cannot afford prose anyway.

Driven over one post-rename fixture so the two surfaces are compared rather than asserted twice
on one side. Where a name must be PRESENT the test says so: `not in` alone would pass on a row
that was never drawn (`backend/tests/README.md`).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "attachment_delete_door_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)

_RENAMED = "public/data/sales_2026/uploads/margins.csv"
_GONE = "public/data/archive/uploads/old.csv"


def _report() -> dict:
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
def test_the_menu_names_the_dataset_the_project_names_now():
    """`ds-3` was `sales 2026` when margins.csv was uploaded and is `Sales 2026 EMEA` now. The
    item offering to destroy its bytes says the word every other surface says."""
    row = _report()["rows"][_RENAMED]
    assert row["drawn"]
    assert "Delete from Sales 2026 EMEA" in row["labels"]
    assert "Delete from sales 2026" not in row["labels"]


@needs_node
def test_the_confirm_names_the_dataset_the_project_names_now():
    """The last thing read before a delete with no undo. It resolves the same way the menu that
    opened it does, so the two cannot name one Dataset two ways."""
    asked = _report()["titles"]["margins.csv"]
    assert asked["asked"]
    assert asked["title"] == "Delete margins.csv from Sales 2026 EMEA?"


@needs_node
def test_both_doors_fall_back_to_the_recorded_name():
    """`ds-gone` names no Dataset this client can see, so there is no current name to have. Both
    strings then say what the entry recorded — plainly, with no third sentence and no blank slot,
    because what a Sage upload records is always a real Dataset's name."""
    report = _report()
    row = report["rows"][_GONE]
    asked = report["titles"]["old.csv"]
    assert row["drawn"]
    assert "Delete from Cold archive" in row["labels"]
    assert asked["asked"]
    assert asked["title"] == "Delete old.csv from Cold archive?"


@needs_node
def test_the_two_doors_say_one_word():
    """The property itself, across both surfaces rather than twice on one. The menu item is what
    somebody clicks and the confirm is what answers them, one immediately after the other, so two
    spellings here would be visible in a single gesture."""
    report = _report()
    for path, file in ((_RENAMED, "margins.csv"), (_GONE, "old.csv")):
        labels = [x for x in report["rows"][path]["labels"] if x.startswith("Delete from ")]
        assert len(labels) == 1, path
        named = labels[0][len("Delete from ") :]
        assert report["titles"][file]["title"] == f"Delete {file} from {named}?"
