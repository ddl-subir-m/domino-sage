"""A Chat chart draws the question's shape, in its units, and nothing sits on the bars (#746).

Demo rerun on `e10172cb`, Chat on haiku. Prompt 1, open pipeline by stage and team, came back as one
bar per (team, stage) row with the team names repeated down the axis, `2.69e+06` beside
`5,194,900`, a `1e7` axis offset, and a figure about twice as tall as it was wide. Prompt 2, calls and
win rate per competitor, put the calls and the win-rate % on one axis, with the legend on the bars.
A table of the rows the chart was drawn from was shown under it, though the skill said no table.

None of that was the data. The chart named a result and its x and y columns, and that was all it
could say:

- A result of one measure by two categories — what a `GROUP BY` of two columns returns — had no way
  to name its second category, so every row was a bar. `by` names it: one bar per x value, split
  into stacked segments by `by`.
- A value with a fraction was printed `.3g`, which goes to scientific notation past 1,000, and an
  integer-valued one with commas, so one chart mixed both. Every number now has one format, and
  `money` and `percent` name the y columns that are amounts or percentages.
- Several y columns always shared one value axis. Measures in different units now get one panel
  each; measures in the same unit still share one, grouped.
- The legend sat inside the plot. It now sits under it.
- A charted read's own card stayed drawn as an answer table beside the chart. It now folds under
  the chart as working: the chart is the answer, drawn from every row of that read.
"""

from __future__ import annotations

import io
import itertools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from sage.liveread.held import HeldRead
from sage.workspace import chat_chart

from .test_a_chat_chart_is_drawn_from_a_reads_rows import _armed, _write
from .test_a_replaced_read_is_not_shown_as_an_answer import RIGHT, WRONG, _tables

# Prompt 1's shape: the skill's pinned statement, GROUP BY team, stage. Amounts are NUMBER(38,2)
# sums, so most carry a fraction — the values that printed as 2.69e+06.
PIPELINE_ROWS = [
    ["EMEA", "Discovery", 412000.5, 3], ["EMEA", "Proposal", 1240000.25, 2],
    ["FSI", "Discovery", 2690000.4, 6], ["FSI", "Negotiation", 5194900, 2],
    ["FSI", "Proposal", 816000.75, 3], ["FSI", "Qualification", 1300000.1, 4],
    ["FSI", "Technical Validation", None, 2],
    ["GEO", "Discovery", 120000.2, 5], ["GEO", "Proposal", 450000.0, 1],
    ["GEO", "Qualification", 98000.5, 2],
    ["Life Sciences", "Discovery", 12400000.3, 9], ["Life Sciences", "Negotiation", 1600000, 2],
    ["Public Sector", "Discovery", 300000.5, 4], ["Public Sector", "Proposal", 700000.5, 1],
    ["Strategic", "Discovery", 900000.9, 6], ["Strategic", "Qualification", 2100000.2, 3],
    ["Unassigned", "Discovery", None, 30], ["West", "Discovery", 250000.0, 5],
    ["West", "Proposal", 1100000.5, 2],
]
PIPELINE = HeldRead(title="Open pipeline by stage and team, FY27 Q3–Q4", slug="open-pipeline",
                    columns=["Team", "Stage", "Open pipeline", "Deals"], rows=PIPELINE_ROWS)
TEAMS = ["EMEA", "FSI", "GEO", "Life Sciences", "Public Sector", "Strategic", "Unassigned", "West"]

# Prompt 2's shape: calls and a win rate per competitor, one result.
COMPETITORS = HeldRead(
    title="Competitor mentions and win rate", slug="competitors",
    columns=["Competitor", "Calls mentioning it", "Win rate %"],
    rows=[["Amazon Web Services", 263, 28.7], ["Microsoft Azure", 139, 31.2], ["SAS", 94, 100.0],
          ["Databricks", 79, 22.0], ["Google Cloud Platform", 51, 40.0], ["Sagemaker", 33, 25.0],
          ["Build In House", 30, 18.5], ["Dataiku", 12, 50.0], ["DataRobot", 9, 0.0],
          ["Cloudera", 4, 33.3], ["Alteryx", 2, None]])

# Prompt 3's shape, which drew well and must keep drawing as it did: two counts, one axis.
TOPICS = HeldRead(title="Topics raised", slug="topics",
                  columns=["Topic", "We raised", "Customers raised"],
                  rows=[["Governance", 90, 70], ["GenAI", 54, 46], ["Orchestration", 24, 10]])

PIPELINE_CHART = {"x": "Team", "ys": ["Open pipeline"], "by": "Stage", "money": ["Open pipeline"]}
COMPETITOR_CHART = {"x": "Competitor", "ys": ["Calls mentioning it", "Win rate %"],
                        "percent": ["Win rate %"]}


@pytest.fixture(autouse=True)
def _fonts_closed():
    """A test that draws a figure itself opens font files `chat_chart.draw` would have closed."""
    from matplotlib import font_manager

    yield
    font_manager._get_font.cache_clear()


def _drawn(read, x, ys, **kw):
    """The figure, laid out, and the renderer that laid it out. Agg needs no file or window."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    fig = chat_chart.figure(read, x, ys, **kw)
    renderer = FigureCanvasAgg(fig).get_renderer()
    fig.draw(renderer)
    return fig, renderer


def _ticks(ax, axis):
    """The tick labels drawn: a locator also labels ticks outside the view, which never show."""
    lo, hi = sorted(ax.get_xlim() if axis == "x" else ax.get_ylim())
    ticks = ax.get_xticks() if axis == "x" else ax.get_yticks()
    labels = ax.get_xticklabels() if axis == "x" else ax.get_yticklabels()
    if not labels:  # a panel sharing the first one's category labels draws none of its own
        return []
    return [t for t, at in zip(labels, ticks, strict=True) if lo <= at <= hi]


def _texts(fig):
    """Every piece of text the figure shows: titles, value labels, ticks, offsets and legends."""
    texts = list(fig.texts)
    for ax in fig.axes:
        texts += [ax.title, *ax.texts, *_ticks(ax, "x"), *_ticks(ax, "y"),
                  ax.xaxis.get_offset_text(), ax.yaxis.label]
    for legend in fig.legends + [ax.get_legend() for ax in fig.axes if ax.get_legend()]:
        texts += legend.get_texts()
    return [t for t in texts if t.get_visible() and t.get_text().strip()]


def _bars(fig):
    from matplotlib.patches import Rectangle

    return [p for ax in fig.axes for p in ax.patches
            if isinstance(p, Rectangle) and p.get_width() != 0]


# --- one measure by two categories ----------------------------------------------------------------

def test_one_measure_by_two_categories_is_one_stacked_bar_per_category():
    fig, _ = _drawn(PIPELINE, **PIPELINE_CHART)
    [ax] = fig.axes

    assert [t.get_text() for t in ax.get_yticklabels()] == TEAMS, "one bar per team, in read order"
    totals: dict[int, float] = {}
    for bar in ax.patches:
        row = round(bar.get_y() + bar.get_height() / 2)
        assert bar.get_x() == pytest.approx(totals.get(row, 0.0)), "segments stack end to end"
        totals[row] = bar.get_x() + bar.get_width()
    expected = {TEAMS.index(t): sum(r[2] or 0 for r in PIPELINE_ROWS if r[0] == t) for t in TEAMS}
    assert {k: pytest.approx(v) for k, v in totals.items() if v} == \
        {k: v for k, v in expected.items() if v}
    stages = [t.get_text() for t in fig.legends[0].get_texts()]
    assert stages == ["Discovery", "Proposal", "Negotiation", "Qualification"], (
        "one series per stage, in read order; Technical Validation has no amount, so no segment")


def test_each_stacked_bar_is_labelled_with_its_total():
    fig, _ = _drawn(PIPELINE, **PIPELINE_CHART)
    shown = {t.get_text() for t in _texts(fig)}
    assert {"$10.0M", "$14.0M", "$1.7M"} <= shown, shown


@pytest.mark.parametrize("by,ys,refusal", [
    ("Region", ["Open pipeline"], "Region is not a column"),
    ("Stage", ["Open pipeline", "Deals"], "one measure"),
])
def test_a_split_that_cannot_be_drawn_is_refused(by, ys, refusal):
    with pytest.raises(ValueError, match=refusal):
        chat_chart.figure(PIPELINE, "Team", ys, by=by)


def test_a_split_whose_pairs_repeat_is_refused_rather_than_added_up():
    doubled = HeldRead(title="Not grouped", slug="ng", columns=PIPELINE.columns,
                       rows=PIPELINE_ROWS + PIPELINE_ROWS[:1])
    with pytest.raises(ValueError, match="GROUP BY"):
        chat_chart.figure(doubled, "Team", ["Open pipeline"], by="Stage")


# --- numbers ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("chart", [
    {"x": "Team", "ys": ["Open pipeline"]},
    {"x": "Team", "ys": ["Open pipeline"], "by": "Stage"},
    {"x": "Team", "ys": ["Open pipeline"], "by": "Stage", "money": ["Open pipeline"]},
], ids=["per-row", "split", "split-money"])
def test_no_number_on_a_chart_is_in_scientific_notation(chart):
    fig, _ = _drawn(PIPELINE, **chart)
    said = [t.get_text() for t in _texts(fig)]
    assert not [s for s in said if re.search(r"\de[+-]?\d|^1e\d", s)], said


def test_money_is_shown_as_money():
    fig, _ = _drawn(HeldRead(title="Pipeline by team", slug="p", columns=["Team", "Amount"],
                             rows=[["A", 12400000.3], ["B", 816000.75], ["C", 9400.4],
                                   ["D", 5194900]]),
                    "Team", ["Amount"], money=["Amount"])
    [ax] = fig.axes
    labels = [t.get_text() for t in ax.texts]
    assert labels == ["$12.4M", "$816K", "$9,400", "$5.2M"]
    assert all(t.get_text().startswith("$") for t in ax.get_xticklabels() if t.get_text())


def test_percentages_are_shown_as_percentages():
    fig, _ = _drawn(COMPETITORS, "Competitor", ["Win rate %"], percent=["Win rate %"])
    [ax] = fig.axes
    assert [t.get_text() for t in ax.texts][:3] == ["28.7%", "31.2%", "100.0%"]


def test_one_chart_formats_its_numbers_one_way():
    fig, _ = _drawn(PIPELINE, "Team", ["Open pipeline"])
    labels = [t.get_text() for t in fig.axes[0].texts if t.get_text()]
    assert labels[2:4] == ["2,690,000", "5,194,900"], labels


@pytest.mark.parametrize("ys,money,percent,refusal", [
    (["Open pipeline"], ["Deals"], [], "Deals is not one of the y columns"),
    (["Open pipeline"], ["Open pipeline"], ["Open pipeline"], "both money and a percentage"),
])
def test_a_unit_for_a_column_not_plotted_is_refused(ys, money, percent, refusal):
    with pytest.raises(ValueError, match=refusal):
        chat_chart.figure(PIPELINE, "Team", ys, money=money, percent=percent)


# --- axes, legends and labels -------------------------------------------------------------------

def test_measures_in_different_units_do_not_share_an_axis():
    fig, _ = _drawn(COMPETITORS, **COMPETITOR_CHART)
    assert len(fig.axes) == 2, "calls and a win rate are two scales"
    for ax, measure in zip(fig.axes, COMPETITOR_CHART["ys"], strict=True):
        assert ax.get_title() == measure
        assert len(ax.patches) == len([r for r in COMPETITORS.rows if r[1] is not None])


def test_measures_in_the_same_unit_still_share_one_axis():
    fig, _ = _drawn(TOPICS, "Topic", ["We raised", "Customers raised"])
    [ax] = fig.axes
    assert len(ax.patches) == 6


@pytest.mark.parametrize("read,chart", [
    (PIPELINE, PIPELINE_CHART),
    (COMPETITORS, COMPETITOR_CHART),
    (COMPETITORS, {"x": "Competitor", "ys": ["Calls mentioning it", "Win rate %"]}),
    (TOPICS, {"x": "Topic", "ys": ["We raised", "Customers raised"]}),
], ids=["pipeline", "competitors", "competitors-one-unit", "topics"])
def test_no_label_or_legend_sits_on_a_bar_or_on_another_label(read, chart):
    fig, renderer = _drawn(read, **chart)
    texts = _texts(fig)
    boxes = [t.get_window_extent(renderer) for t in texts]
    bars = [b.get_window_extent(renderer) for b in _bars(fig)]
    for legend in fig.legends + [ax.get_legend() for ax in fig.axes if ax.get_legend()]:
        frame = legend.get_window_extent(renderer)
        assert not [b for b in bars if frame.overlaps(b)], "the legend sits on a bar"
    for (a, ta), (b, tb) in itertools.combinations(zip(boxes, texts, strict=True), 2):
        assert not a.overlaps(b), f"{ta.get_text()!r} overlaps {tb.get_text()!r}"
    for box, text in zip(boxes, texts, strict=True):
        if text in fig.axes[0].texts or any(text in ax.texts for ax in fig.axes):
            assert not [b for b in bars if box.overlaps(b)], f"{text.get_text()!r} is on a bar"
    width, height = fig.get_figwidth() * fig.dpi, fig.get_figheight() * fig.dpi
    for box, text in zip(boxes, texts, strict=True):
        assert 0 <= box.x0 and box.x1 <= width and 0 <= box.y0 and box.y1 <= height, (
            f"{text.get_text()!r} runs off the figure")


# The image card draws the PNG at the column's width with no height of its own, so the figure's
# aspect is the card's. 420 px is the frame Chat already gives a drawn page (`chat.css`): tall enough
# to read a chart in, short enough that the Thread still scrolls past it.
CARD_PX = 420


@pytest.mark.parametrize("read,chart", [
    (PIPELINE, PIPELINE_CHART), (COMPETITORS, COMPETITOR_CHART),
    (TOPICS, {"x": "Topic", "ys": ["We raised", "Customers raised"]}),
    (HeldRead(title="Deals by team", slug="d", columns=["TEAM", "DEALS"],
              rows=[[f"Team {n}", n] for n in range(12)]), {"x": "TEAM", "ys": ["DEALS"]}),
], ids=["pipeline", "competitors", "topics", "twelve-bars"])
def test_the_figure_fits_its_card(read, chart):
    data = chat_chart.draw(read, chart["x"], chart["ys"],
                           **{k: v for k, v in chart.items() if k not in ("x", "ys")})
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
    assert width == chat_chart.COLUMN_PX * 2
    assert height / 2 <= CARD_PX, f"{height / 2:.0f} px tall at the column"


# --- through artifact_write -------------------------------------------------------------------

def test_artifact_write_passes_the_split_and_the_units_to_the_chart(tmp_path):
    orch, tid, project = _armed(tmp_path, [PIPELINE])
    path = f"examples/{tid}/pipeline.png"
    body = {"path": path, "table": PIPELINE.title, "x": "Team", "y": ["Open pipeline"]}

    with pytest.raises(ValueError, match="Region is not a column"):
        _write(orch, project, tid, {**body, "by": "Region"})
    with pytest.raises(ValueError, match="Deals is not one of the y columns"):
        _write(orch, project, tid, {**body, "money": ["Deals"]})
    with pytest.raises(ValueError, match="Deals is not one of the y columns"):
        _write(orch, project, tid, {**body, "percent": "Deals"})
    assert not (project.record.path / path).exists()

    _write(orch, project, tid, {**body, "by": "Stage", "money": ["Open pipeline"],
                                "percent": None})
    assert (project.record.path / path).exists()


_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
_TOOL = Path(__file__).resolve().parents[1] / "sage/liveread/tools/artifact_write.ts"


@_needs_node
def test_the_tool_offers_the_split_and_the_units_as_nullable_arguments():
    out = subprocess.run(
        ["node", "--input-type=module", "-e",
         "const m = await import(process.argv[1]); console.log(JSON.stringify(m.default.args))",
         str(_TOOL)],
        check=False, capture_output=True, text=True, timeout=30, env={**os.environ})
    assert out.returncode == 0, out.stderr
    args = json.loads(out.stdout.strip().splitlines()[-1])
    for name in ("by", "money", "percent"):
        assert {"type": "null"} in args[name]["anyOf"], f"{name} must be nullable"


def test_the_chart_prompt_names_the_split_and_the_units(tmp_path):
    """The turn prompt is where the model learns the call; an argument it is never told about is
    one it never sends. Read off the prompt a chart turn actually sent."""
    from sage.orchestrator.service import Orchestrator

    from .fake_opencode import Turn
    from .test_a_replaced_read_is_not_shown_as_an_answer import SOURCE, _ReadingOpenCode, _Store
    from .test_chat_turn import IntentGateway, OkFeedback, _catalog

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = _ReadingOpenCode(ws, [Turn(text="Here it is.")])
    orch = Orchestrator(workspace_dir=ws, template=template,
                        gateway=IntentGateway({"label": "data_artifact", "confidence": 0.92}),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=_Store())
    try:
        orch.project(start_preview=False)
        tid = orch.create_thread()["id"]
        orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1", "name": SOURCE})
        list(orch.chat_stream(tid, "Chart open pipeline by stage and team"))
    finally:
        orch.shutdown()  # a chat turn arms the idle-save timer; this cancels it

    [said] = [p["text"] for p in oc.prompts if "artifact_write" in p["text"]]
    for words in ("set by to", "money", "percent"):
        assert words in said, words


# --- the charted read's own card ----------------------------------------------------------------

def test_the_read_a_chart_is_drawn_from_folds_under_the_chart(tmp_path):
    roles = _tables(tmp_path, "Negotiate holds most of the open pipeline.",
                    label="data_artifact", chart=True)
    assert roles == {f"{WRONG.slug}.table.json": "working",
                     f"{RIGHT.slug}.table.json": "working", "pipeline.png": "answer"}


def test_a_chart_that_was_not_written_folds_nothing(tmp_path):
    """Folding a read's card is safe only because its chart is on screen; a chart whose file never
    landed must leave the read drawn."""
    orch, tid, project = _armed(tmp_path, [PIPELINE])
    path = f"examples/{tid}/pipeline.png"
    (project.record.path / path).mkdir(parents=True)

    with pytest.raises(OSError):
        _write(orch, project, tid, {"path": path, "table": PIPELINE.title, "x": "Team",
                                    "y": ["Open pipeline"], "by": "Stage"})
    assert PIPELINE.slug not in orch._charted_reads.get(tid, set())


def test_a_read_no_chart_was_drawn_from_still_draws(tmp_path):
    roles = _tables(tmp_path, "Negotiate holds $1.25M of open pipeline and Proposal $640K.")
    assert roles[f"{RIGHT.slug}.table.json"] == "answer"
