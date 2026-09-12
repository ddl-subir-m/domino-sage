"""A removal receipt names a Dataset by the word the Project holds for it TODAY (#271, ADR-0011).

#263 pinned this on the refusal and #266 on the row the refusal points at. This is the third
surface: the sentence `removeAttachmentFromApp` leaves behind, which read the `dataset` the entry
recorded at ATTACH time — so a Dataset renamed since said "the file stays in sales 2026" of one the
Project now calls Sales 2026 EMEA, and sent the reader looking for a name nothing else on the
platform uses.

Why the rule #266 settled could not simply be carried here, which is what this ticket had to
decide: `attachmentSource` answers '' rather than guess, because a row is better silent than wrong.
This sentence is a PROMISE — it tells somebody their bytes are safe somewhere — and a promise with
the place left blank ("the file stays in .") is worse than the stale name, while the served path is
the one substitute ADR-0011 rules out. So the receipt needs a name it can ALWAYS produce, and it
gets one by having a second sentence for the case where the current name cannot be had: the promise
is kept and the missing half is named as missing. The two surfaces share the lookup
(`datasetNameNow`) and not the sentence, which is what keeps one of them from being wrong.

Driven over the same post-rename fixture #266 uses — the entry recording the old name, the working
set holding the new one — and the cross-surface test below asserts the receipt and the row say one
word about one Dataset rather than asserting the same fact twice on one side. The refusal is the
third corner and #266 already holds it against the row.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "attachment_removal_receipt_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)


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
def test_the_receipt_names_the_dataset_by_the_name_the_project_holds_now():
    """Not the name the entry recorded. Every other surface a rename moves — the rail, the card,
    the refusal, the row — and a receipt left on the old word is the one sentence that also tells
    the reader where to go look."""
    said = _report()["receipts"]["q3.csv"]
    assert "The app's copy is gone and the file stays in Sales 2026 EMEA." in said
    assert "sales 2026" not in said


@needs_node
def test_the_receipt_keeps_its_promise_when_it_cannot_name_the_dataset():
    """The case that made this a decision rather than a copy of #266. No current name is to be had
    for `ds-gone`, so the sentence written for that case is drawn: the bytes are still promised
    safe, the absence is stated, and nothing is filled in from the record."""
    said = _report()["receipts"]["old.csv"]
    assert "it came from keeps the file" in said
    # "not to hand" and never a claim about what the manifest holds: `datasetNameNow` answers ''
    # for a Dataset nobody lists and for a listing read that failed alike, and `SW.api.resources`
    # swallows the second into an empty list. A sentence saying the record holds only the old name
    # would be false on exactly the occasion the Project's list merely did not arrive.
    assert "Its current name is not to hand, so it is not named here." in said
    # Never the stale name, never a blank slot where a name was going to go, and never the path.
    assert "Cold archive" not in said
    assert "stays in ." not in said
    assert "public/data" not in said


@needs_node
def test_an_entry_that_records_no_dataset_says_so_as_it_did():
    """An entry with no `dataset_id` — what `_rehydrate_attached`'s symlink scan leaves in a
    pre-manifest workspace — records no Dataset at all, which is a different thing from a Dataset
    whose current name cannot be found. Its sentence predates #271 and is untouched by it, and it
    must never fall back to the served slug its `dataset` field actually holds."""
    said = _report()["receipts"]["q2.csv"]
    assert "This file records no Data Box, so there is no source to name." in said
    assert "sales_2026" not in said


@needs_node
def test_both_sentences_name_the_dataset_in_the_packs_own_noun():
    """The receipt speaks the deployment's word for a Dataset, not ours (ADR-0014). The harness
    loads a pack calling it a Data Box, so a hardcoded "Dataset" fails here — and these are the two
    branches that name the noun rather than an instance of it."""
    receipts = _report()["receipts"]
    assert "Data Box" in receipts["old.csv"]
    assert "Data Box" in receipts["q2.csv"]


@needs_node
def test_the_receipt_and_the_row_name_one_dataset():
    """The property itself, driven across the two surfaces rather than asserted twice on one. The
    modal draws the row and then the receipt about the same file, one under the other, so two
    spellings here would be visible in a single glance."""
    report = _report()
    row = report["lines"]["q3.csv"].split(" · ")[0]
    assert row == "Sales 2026 EMEA"
    assert f"the file stays in {row}." in report["receipts"]["q3.csv"]
    # And they fall quiet together: no current name means no line under the row and no name in the
    # promise, from one lookup rather than two rules that happen to agree today.
    assert report["lines"]["old.csv"] == "You added this"
