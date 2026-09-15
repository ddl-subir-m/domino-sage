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
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.tools import brand_lint as lint

_HARNESS = Path(__file__).resolve().parent / "js" / "data_grouping_harness.mjs"
_UTIL = (
    Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "util.js"
).read_text()

# The gate sits inside `_draw` rather than on the module. A module-level `pytestmark` takes the
# whole file with it, and the one test here that reads the icon bundle straight off disk needs no
# node at all — under a module mark it went silent on exactly the laptops the harness went silent
# on, which is where an icon typo would otherwise have been caught.

# The words the old naming used, which must not survive as a heading in either surface. Kept as a
# list rather than asserted one at a time so a heading that comes back reports itself by name.
_OLD_HEADINGS = ["Datasets", "Data Sources", "Dataset", "Data Source", "Tabular"]


def _draw(act: str, **extra) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps({"act": act, **extra}),
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


@pytest.mark.parametrize(
    ("kind", "said"),
    [("dataset", "Pick a File volume to continue"),
     ("datasource", "Pick a Data connection to continue"),
     ("model_llm", "Pick a model to continue")],
)
def test_the_rail_asks_for_a_kind_by_the_name_it_draws(kind, said):
    """The sentence points at the section under it. Asked for a `dataset` it used to say `Pick a
    Dataset`, over rows now headed `Data / File volume` and beside a catalogue whose filter is
    called the same — a word neither surface draws any more. Kinds with no type word are unchanged:
    the rail still heads them with the Domino noun, so the sentence still says it."""
    assert _draw("panel-both", filter=kind)["hint"] == said


# --------------------------------------------------- every place that names a kind

# Every file that asks `SW.util.labelFor` what to call a kind, and whether it reaches for the type
# word first. This is a roster and not a rule because the answer is a judgement each time, and the
# judgement is the thing worth pinning: ADR-0054 moved two surfaces onto the type word and the first
# pass at it moved ONE, leaving a Chat card saying `attach one Dataset` beside a button that opened
# a rail saying `Pick a File volume`. A file that starts naming kinds reds this test and has to say
# which word it says.
_NAMES_A_KIND = {
    # The rail's "Pick a X to continue", and the Chat card whose button opens that same rail on that
    # same kind. They are one sentence split across two surfaces, so they say one word.
    "components/resource-panel.js": True,
    "modes/chat.js": True,
    # The catalogue row's meta line — the other half of the Data section.
    "components/resource-catalog.js": True,
    # The `@`-mention row's caption, deliberately NOT type-aware: a type word reads as a type
    # because a section above it already named the domain, and this list has no section. Somebody
    # scanning it is looking for a thing they know Domino by name. See ADR-0054.
    "components/composer.js": False,
}


def test_every_place_that_names_a_kind_has_decided_which_word_it_says():
    """The defect this catches is a site nobody opened, not a site somebody got wrong."""
    root = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
    found = {}
    for path in sorted(root.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        body = path.read_text()
        if "SW.util.labelFor(" not in body:
            continue
        found[str(path.relative_to(root))] = "SW.util.dataTypeLabel(" in body

    assert set(found) == set(_NAMES_A_KIND), (
        "a file started or stopped naming a kind; decide which word it says and list it here"
    )
    assert found == _NAMES_A_KIND, "a listed file changed which word it says"


# ------------------------------------------------------------------ the icons


def test_a_data_row_draws_an_icon_rather_than_an_emoji():
    """`📦` and `🔌` were the two this replaces. Drawn from the set the Workbench already bundles,
    so this adds no dependency."""
    icons = {i["name"]: i for i in _draw("panel-both")["icons"]}
    assert icons["Sales rows"]["kind"] == "node"
    assert icons["Sales rows"]["glyph"] == "HddOutlined"
    assert icons["Warehouse"]["glyph"] == "ApiOutlined"


def test_a_data_row_draws_an_icon_the_bundle_actually_exports():
    """The harness cannot answer this one. Its `icons` stub is a Proxy that hands back any name it
    is asked for, and the app's own `theme.js` proxies a missing name to a blank span, so a rename
    to an icon the bundle does not carry draws an empty slot in the rail and stays green everywhere
    else. This reads the bundle.

    No example name is given on purpose. The first plant written for this test was `DatabaseFilled`,
    which the bundle exports — the plant passed, and it was right to. An example here would be a
    name somebody later checks against the bundle instead of running the test."""
    bundle = (
        Path(__file__).resolve().parents[1]
        / "sage" / "workbench" / "vendor" / "icons.umd.min.js"
    ).read_text()
    blocks = re.findall(r"DATA_ICONS = \{([^}]*)\}", _UTIL)
    assert len(blocks) == 1, "DATA_ICONS is no longer one brace-delimited map in util.js"
    drawn = re.findall(r"'([A-Z][A-Za-z]+)'", blocks[0])
    assert drawn, "DATA_ICONS no longer reads as a map of quoted icon names"
    for name in drawn:
        assert f"{name}:" in bundle, f"{name} is not exported by the bundled icon set"


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
