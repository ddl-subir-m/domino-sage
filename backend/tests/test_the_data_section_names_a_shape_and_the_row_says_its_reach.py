"""The Data section groups by shape, and a row says which Domino thing it is (ADR-0053).

Three labels stood over two things. The Resource Browser drew a `Data` group holding `Datasets` and
`Data Sources`; Browse Domino's sidebar offered `Data`, `Datasets` and `Data Sources` as three
peers, the first one's count being the sum of the next two. `Data` itself was in neither glossary
nor ADR — it arrived in #164 as somewhere for a group's add door to land and became vocabulary by
sitting there.

What replaces it splits the two axes that were fighting over one label. The TYPE names the shape of
the data — File volume, Tabular — and the row says its own reach, `connected`, only where the thing
is reached over a connection. A third type on the reach axis was the tempting move and is the one
this file pins closed: a Data Source would have qualified for two types, and the cell neither names
— file-shaped, reached over a connection, which is what Domino's own volume products are — would
still have had none.

None of this is greppable out of the source. Which subhead a Project sees depended on which kinds
held rows, the noun on a row is resolved from the pack at draw time, and the sidebar's nesting is a
class on a button. So both components are drawn and the labels are read off the tree.
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
_OLD_HEADINGS = ["Datasets", "Data Sources", "Dataset", "Data Source"]


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


def test_the_rail_draws_one_data_section_over_both_shapes():
    """Not `Data` over `Datasets` and `Data Sources`. One section, and its subheads are shapes."""
    drawn = _draw("panel-both")
    assert "Data (2)" in drawn["heads"]
    assert drawn["subheads"] == ["File volume", "Tabular"]


def test_no_heading_in_the_rail_names_a_domino_thing():
    """A heading names a shape; the Domino noun belongs on the row, where the pack renames it."""
    drawn = _draw("panel-both")
    headings = drawn["heads"] + drawn["subheads"]
    assert [h for h in headings if any(old in h for old in _OLD_HEADINGS)] == []


def test_a_shape_is_named_even_when_it_is_the_only_one_present():
    """The rule this replaces named a subgroup only when a sibling also had rows, so a Project
    holding only Datasets drew them under `Data` while the Project next door drew the identical
    rows under `Data / File volume`. Shape is a fact about the rows, not about what is beside
    them."""
    drawn = _draw("panel-one-shape")
    assert drawn["subheads"] == ["File volume"]
    assert "Data (1)" in drawn["heads"]


# ------------------------------------------------------------------ what a row says about itself


def test_a_data_row_carries_the_domino_noun():
    """The subhead names a shape now, so without this the panel told somebody a shape and never
    which Domino thing it was — the icon and the pack's noun were the only two candidates, and one
    of them is an emoji. Read from the pack, so this is also what a renaming pack rewrites."""
    rows = {r["name"]: r["meta"] for r in _draw("panel-both")["rows"]}
    assert rows["Sales rows"] == "Dataset"
    assert rows["Warehouse"].startswith("Data Source")


def test_only_a_row_that_reaches_outside_says_connected():
    """Reach is said where it is true and nowhere else: silence means the Project holds the thing,
    and marking that case too would put a word on every data row to distinguish nothing."""
    rows = {r["name"]: r["meta"] for r in _draw("panel-both")["rows"]}
    assert rows["Warehouse"] == "Data Source · connected"
    assert "connected" not in rows["Sales rows"]


def test_a_row_that_is_not_data_gains_no_meta_line():
    """The line is the Data section's debt, not every row's. A model row is untouched, which is
    what keeps a 320px rail from growing a third line per row for nothing."""
    drawn = _draw("panel-both")
    assert drawn["namesWithoutMeta"] == ["Risk scorer"]


# ------------------------------------------------------------------ Browse Domino's sidebar


def test_browse_domino_nests_the_two_shapes_under_data():
    """Both shapes stay as filters — somebody wanting only tabular things wants that filter — but
    as children. As peers they were three rows whose first count was the sum of the next two, which
    reads as a third kind of thing rather than as the pair above its halves."""
    side = _draw("catalog")["side"]
    rows = [(e["label"], e["isChild"]) for e in side]
    assert ("Data", False) in rows
    assert ("File volume", True) in rows
    assert ("Tabular", True) in rows
    assert [e["label"] for e in side if any(old == e["label"] for old in _OLD_HEADINGS)] == []


def test_the_section_counts_what_its_shapes_count():
    """A group entry has no kind of its own to count, so it adds its children up. The nesting is
    what makes that honest rather than a third number competing with them."""
    counts = {e["label"]: e["count"] for e in _draw("catalog")["side"]}
    assert counts["Data"] == counts["File volume"] + counts["Tabular"]


def test_a_catalog_row_says_the_noun_then_the_reach():
    """Beside the noun rather than as a type of its own: the sidebar's shapes and this are two
    axes, and a `Data connection` filter up there would have claimed a Data Source that `Tabular`
    already claims."""
    rows = {r["name"]: r["meta"] for r in _draw("catalog")["rows"]}
    assert rows["Warehouse"][:2] == ["Data Source", "connected"]
    assert "connected" not in rows["Sales rows"]


# ------------------------------------------------------------------ the shapes are words


@pytest.mark.parametrize("term", ["Data", "File volume", "Tabular", "connected"])
def test_a_shape_and_a_reach_are_words_and_owe_no_key(term):
    """`_Kind_: word` under ADR-0026, and the reason is not bookkeeping: these describe the shape of
    a thing and how far it reaches, so there is nothing for a partner to rename them TO. A pack that
    calls a Dataset a Collection has not stopped it from holding files. Marked `name` instead, each
    would owe a pack key with nothing to put in it — and the lint would demand one."""
    assert lint.glossary_kinds()[term] == "word"
