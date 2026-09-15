"""The Data section names one type per row, and says each fact once (ADR-0054).

Three labels stood over two things. The Resource Browser drew a `Data` group holding `Datasets` and
`Data Sources`; Browse Domino's sidebar offered `Data`, `Datasets` and `Data Sources` as three
peers, the first one's count being the sum of the next two. `Data` itself was in neither glossary
nor ADR — it arrived in #164 as somewhere for a group's add door to land and became vocabulary by
sitting there.

What replaces it is one word per row, in one vocabulary: the TYPE says what a person is looking at
— File volume, Data connection — and it is what the subheads say, what the sidebar filters by, and
what a catalogue row's meta line says. Reach and shape are one fact while every connected data row
is a Data Source and every Data Source is connected; the ADR says what breaks when that stops being
true, and this file pins the behaviour that is true now.

The other half is saying each fact once. A catalogue row printed its Project twice — as the
description `in <project>` and again as `originName` — and drew a separator for an `ownerName` that
has been the empty string since the modal was written.

None of this is greppable out of the source. Which subhead a Project sees depended on which kinds
held rows, a row's meta line is composed at draw time, and the sidebar's nesting is a class on a
button. So both components are drawn and the labels are read off the tree.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.tools import brand_lint as lint

_HARNESS = Path(__file__).resolve().parent / "js" / "data_grouping_harness.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH (it is in the Sage image)",
)

# The words the old naming used, which must not survive as a heading in either surface. Kept as a
# list rather than asserted one at a time so a heading that comes back reports itself by name.
_OLD_HEADINGS = ["Datasets", "Data Sources", "Dataset", "Data Source", "Tabular"]


def _draw(act: str) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": act}),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------ the rail's one Data section


def test_the_rail_draws_one_data_section_over_both_types():
    """Not `Data` over `Datasets` and `Data Sources`. One section, and its subheads are types."""
    drawn = _draw("panel-both")
    assert "Data (2)" in drawn["heads"]
    assert drawn["subheads"] == ["File volume", "Data connection"]


def test_no_heading_in_the_rail_names_a_domino_thing():
    """A heading says what a person is looking at. The Domino noun is what a pack renames, and it
    is still what every other surface says — it is this list that stopped repeating it."""
    drawn = _draw("panel-both")
    headings = drawn["heads"] + drawn["subheads"]
    assert [h for h in headings if any(old in h for old in _OLD_HEADINGS)] == []


def test_a_type_is_named_even_when_it_is_the_only_one_present():
    """The rule this replaces named a subgroup only when a sibling also had rows, so a Project
    holding only Datasets drew them under `Data` while the Project next door drew the identical
    rows under `Data / File volume`. The type is a fact about the rows, not about what is beside
    them."""
    drawn = _draw("panel-one-type")
    assert drawn["subheads"] == ["File volume"]
    assert "Data (1)" in drawn["heads"]


@pytest.mark.parametrize("word", ["File volume", "Data connection", "Dataset", "Data Source"])
def test_a_row_does_not_repeat_what_its_subhead_already_said(word):
    """An intermediate draft drew the type as a subhead and the Domino noun on the row below it,
    which is two names for one thing and a third line in a 320px rail. Read over everything a row
    draws, not over one class: a class that is empty in this fixture would pass either way."""
    for row in _draw("panel-both")["rowText"]:
        assert word not in row, row


# ------------------------------------------------------------------ Browse Domino's sidebar


def test_browse_domino_nests_the_two_types_under_data():
    """Both types stay as filters — somebody wanting only connections wants that filter — but as
    children. As peers they were three rows whose first count was the sum of the next two, which
    reads as a third kind of thing rather than as the pair above its halves."""
    side = _draw("catalog")["side"]
    rows = [(e["label"], e["isChild"]) for e in side]
    assert ("Data", False) in rows
    assert ("File volume", True) in rows
    assert ("Data connection", True) in rows
    assert [e["label"] for e in side if e["label"] in _OLD_HEADINGS] == []


def test_the_section_counts_what_its_types_count():
    """A group entry has no kind of its own to count, so it adds its children up. The nesting is
    what makes that honest rather than a third number competing with them."""
    counts = {e["label"]: e["count"] for e in _draw("catalog")["side"]}
    assert counts["Data"] == counts["File volume"] + counts["Data connection"]


def test_a_catalog_row_says_its_type_in_the_words_the_sidebar_filters_by():
    """One vocabulary across the modal: the row must not say `Dataset` under a filter called File
    volume. A kind with no type of its own keeps the Domino noun, which is all this slot ever
    said."""
    rows = {r["name"]: r["meta"] for r in _draw("catalog")["rows"]}
    assert rows["Sales rows"] == ["File volume"]
    assert rows["Warehouse"] == ["Data connection"]
    assert rows["Risk scorer"] == ["model"]


def test_a_catalog_row_says_each_fact_once():
    """The Project was printed twice — as `in <project>` above and as `originName` here — and
    `ownerName` has been the empty string since this modal was written, so it drew a separator with
    nothing after it. A meta line of exactly one entry is what both of those leaving looks like."""
    for row in _draw("catalog")["rows"]:
        assert len(row["meta"]) == 1, row


# ------------------------------------------------------------------ the icons


def test_a_data_row_draws_an_icon_rather_than_an_emoji():
    """`📦` and `🔌` were the two this replaces. Drawn from the set the Workbench already bundles,
    so this adds no dependency."""
    icons = {i["name"]: i for i in _draw("panel-both")["icons"]}
    assert icons["Sales rows"]["kind"] == "node"
    assert icons["Sales rows"]["glyph"] == "HddOutlined"
    assert icons["Warehouse"]["glyph"] == "ApiOutlined"


def test_a_kind_with_no_drawn_icon_keeps_its_emoji():
    """The two sets sit side by side until somebody decides the rest, and a kind that was not
    given an icon must go on drawing what it drew rather than nothing at all."""
    icons = {i["name"]: i for i in _draw("panel-both")["icons"]}
    assert icons["Risk scorer"] == {"kind": "emoji", "glyph": "\U0001f9e0", "name": "Risk scorer"}


# ------------------------------------------------------------------ the types are words


@pytest.mark.parametrize("term", ["Data", "File volume", "Data connection"])
def test_a_type_is_a_word_and_owes_no_key(term):
    """`_Kind_: word` under ADR-0026, and the reason is not bookkeeping: a type says what a person
    is looking at rather than naming a Domino primitive, so there is nothing for a partner to
    rename it TO. A pack that calls a Dataset a Collection has not stopped it holding files. Marked
    `name`, each would owe a pack key with nothing to put in it — and the lint would demand one."""
    assert lint.glossary_kinds()[term] == "word"
