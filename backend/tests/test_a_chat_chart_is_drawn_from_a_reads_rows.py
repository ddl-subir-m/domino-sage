"""A Chat chart is plotted by Sage from the rows a read returned, never from values the model typed (#729).

The data-artifact lane used to tell the model to send hand-written SVG, which `artifact_write`
rasterised. So every bar height and label was a literal the model typed: prompt 2's chart carried
118/94/67/12 calls that no read returned (the table said 47/29/25/14), prompt 1's x-axis labels ran
into each other, and the type was tiny at the message column's ~450 px.

Now a chart names a result this turn read — by the title the read was given — and the columns to
plot. The bars, the labels and the numbers come from that result's rows, so a series that matches
no read cannot be drawn at all. Held here:

- the plotted labels and values ARE the read's rows, whether or not the model was shown them;
- a chart from anything else — a table the model wrote, SVG, PNG bytes — is refused;
- a label column of identifiers that another read this turn names is refused with the way out
  (join it in the statement), so the chart never shows raw IDs (prompt 1's role IDs);
- the figure is readable at the message column: no overlapping labels, legible type.
"""

from __future__ import annotations

import io
import itertools
import json

import pytest
from PIL import Image

from sage.liveread.held import HeldRead
from sage.workspace import chat_chart

from .test_chat_turn import _orch

CALLS = [["Build In-House", 47], ["Lakehouse", 29], ["Cloud ML", 25], ["Low-Code", 14]]


def _read(title, columns, rows, disclosed=()) -> HeldRead:
    return HeldRead(title=title, slug=title.lower().replace(" ", "-"), columns=columns,
                    rows=rows, disclosed=list(disclosed))


# --- the figure -------------------------------------------------------------------------------

def test_the_bars_are_the_rows_of_the_read_named():
    figure = chat_chart.figure(_read("Competitor calls", ["COMPETITOR", "CALLS"], CALLS),
                               "COMPETITOR", ["CALLS"])
    [ax] = figure.axes
    bars = [p.get_width() for p in ax.patches]
    labels = [t.get_text() for t in ax.get_yticklabels()]
    assert bars == [47, 29, 25, 14]
    assert labels == ["Build In-House", "Lakehouse", "Cloud ML", "Low-Code"]


@pytest.fixture(autouse=True)
def _fonts_closed():
    """Drawing opens font files into matplotlib's cache; `chat_chart.draw` closes them, and a test
    that draws a figure itself must too."""
    from matplotlib import font_manager

    yield
    font_manager._get_font.cache_clear()


def _shown(ax, axis: str):
    """The tick labels actually drawn: a locator also labels ticks outside the view, unshown."""
    lo, hi = sorted(ax.get_xlim() if axis == "x" else ax.get_ylim())
    ticks = ax.get_xticks() if axis == "x" else ax.get_yticks()
    labels = ax.get_xticklabels() if axis == "x" else ax.get_yticklabels()
    return [t for t, at in zip(labels, ticks, strict=True) if lo <= at <= hi]


def _readable(figure, texts):
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    renderer = FigureCanvasAgg(figure).get_renderer()
    figure.draw(renderer)
    boxes = [t.get_window_extent(renderer) for t in texts if t.get_text()]
    for a, b in itertools.combinations(boxes, 2):
        assert not a.overlaps(b), "two labels overlap"
    width = figure.get_figwidth() * figure.dpi
    for box in boxes:
        assert box.x0 >= 0 and box.x1 <= width, "a label runs off the figure"
    # The PNG is scaled to the message column, so the size that matters is the size there.
    scale = chat_chart.COLUMN_PX / width
    for t in texts:
        assert t.get_fontsize() * figure.dpi / 72 * scale >= 11, "type too small at the column"


def test_many_long_labels_neither_overlap_nor_shrink_past_reading():
    rows = [[f"Enterprise Account Executive Team {n} North America", n * 7] for n in range(14)]
    figure = chat_chart.figure(_read("Deals by team", ["TEAM", "DEALS"], rows), "TEAM", ["DEALS"])
    [ax] = figure.axes
    _readable(figure, _shown(ax, "y") + _shown(ax, "x") + [ax.title])


def test_a_series_over_dates_is_a_line_whose_dates_do_not_collide():
    rows = [[f"2026-{m:02d}-{d:02d}", m * 10 + d] for m in range(1, 10) for d in (1, 15)]
    figure = chat_chart.figure(_read("Weekly users", ["WEEK", "WAU"], rows), "WEEK", ["WAU"])
    [ax] = figure.axes
    assert len(ax.lines) == 1 and list(ax.lines[0].get_ydata()) == [r[1] for r in rows]
    _readable(figure, _shown(ax, "x") + _shown(ax, "y"))


def test_the_png_is_sized_for_the_message_column():
    data = chat_chart.draw(_read("Competitor calls", ["COMPETITOR", "CALLS"], CALLS),
                           "COMPETITOR", ["CALLS"])
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert image.size[0] == chat_chart.COLUMN_PX * 2


@pytest.mark.parametrize("x,y,refusal", [
    ("TEAM", ["CALLS"], "TEAM is not a column"),
    ("COMPETITOR", ["COMPETITOR"], "COMPETITOR holds no numbers"),
])
def test_columns_the_read_does_not_have_are_refused(x, y, refusal):
    with pytest.raises(ValueError, match=refusal):
        chat_chart.draw(_read("Competitor calls", ["COMPETITOR", "CALLS"], CALLS), x, y)


# --- the lookup case --------------------------------------------------------------------------

FACT = _read("Deals by role", ["ROLE_ID", "DEALS"],
             [["00EUY000000f3OX2AY", 12], ["00EUY000000f3OY2AY", 9]])
LOOKUP = _read("Team role mapping", ["OPPORTUNITY_OWNER_ROLE_ID", "OPPORTUNITY_OWNER_POSITION"],
               [["00EUY000000f3OX2AY", "Enterprise East"], ["00EUY000000f3OY2AY", "Enterprise West"],
                ["00EUY000000f3OZ2AY", "Commercial"]])


def test_identifiers_another_read_names_are_refused_with_the_way_to_name_them():
    with pytest.raises(ValueError) as refused:
        chat_chart.check_labels(FACT, "ROLE_ID", [FACT, LOOKUP])
    said = str(refused.value)
    assert "Team role mapping" in said and "OPPORTUNITY_OWNER_POSITION" in said
    assert "Join" in said
    assert "00EUY" not in said, "a refusal carries no values"


def test_labels_that_are_already_words_are_not_a_lookup():
    regions = _read("Region totals", ["REGION", "N"], [["EMEA", 3], ["NA", 4]])
    managers = _read("Region managers", ["REGION", "MANAGER"], [["EMEA", "Ana"], ["NA", "Bo"]])
    chat_chart.check_labels(regions, "REGION", [regions, managers])


# --- through artifact_write -------------------------------------------------------------------

def _armed(tmp_path, reads: list[HeldRead]):
    orch, _ = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    orch._mint_live_read_token(tid)
    for read in reads:
        orch._hold_read(tid, read)
    return orch, tid, project


def _write(orch, project, tid, body):
    chat = project.control.arm_chat(tid)
    artifact = project.control.arm_chat_artifact()
    try:
        return orch.write_chat_artifact({"thread_id": tid, **body})
    finally:
        project.control.disarm_chat_artifact(artifact)
        project.control.disarm_chat(chat)


def test_artifact_write_draws_the_chart_from_a_result_read_this_turn(tmp_path):
    orch, tid, project = _armed(tmp_path, [
        _read("Competitor calls", ["COMPETITOR", "CALLS"], CALLS)])
    path = f"examples/{tid}/calls.png"

    result = _write(orch, project, tid, {"path": path, "table": "Competitor calls",
                                         "x": "COMPETITOR", "y": ["CALLS"]})

    assert result["path"] == path
    with Image.open(project.record.path / path) as image:
        assert image.format == "PNG"


@pytest.mark.parametrize("body,refusal", [
    ({"table": "Made up", "x": "COMPETITOR", "y": ["CALLS"]}, "no result called Made up"),
    ({"content": "<svg xmlns='http://www.w3.org/2000/svg'><rect width='118'/></svg>",
      "encoding": "svg"}, "drawn from a result"),
    ({"content": "iVBORw0KGgo=", "encoding": "base64"}, "drawn from a result"),
])
def test_a_chart_whose_values_come_from_no_read_cannot_be_written(tmp_path, body, refusal):
    orch, tid, project = _armed(tmp_path, [
        _read("Competitor calls", ["COMPETITOR", "CALLS"], CALLS)])
    path = f"examples/{tid}/calls.png"

    with pytest.raises(ValueError, match=refusal):
        _write(orch, project, tid, {"path": path, **body})

    assert not (project.record.path / path).exists()


def test_a_table_the_model_wrote_is_not_a_read_and_cannot_be_charted(tmp_path):
    orch, tid, project = _armed(tmp_path, [])
    typed = {"title": "Competitor calls", "columns": ["COMPETITOR", "CALLS"],
             "rows": [["Build In-House", 118], ["Lakehouse", 94]]}
    _write(orch, project, tid, {"path": f"examples/{tid}/competitor-calls.table.json",
                                "content": json.dumps(typed), "encoding": "utf8"})

    with pytest.raises(ValueError, match="no result called Competitor calls"):
        _write(orch, project, tid, {"path": f"examples/{tid}/calls.png",
                                    "table": "Competitor calls", "x": "COMPETITOR", "y": "CALLS"})


def test_a_chart_labelled_by_identifiers_a_lookup_named_is_not_written(tmp_path):
    orch, tid, project = _armed(tmp_path, [FACT, LOOKUP])
    path = f"examples/{tid}/deals.png"

    with pytest.raises(ValueError, match="Join"):
        _write(orch, project, tid, {"path": path, "table": "Deals by role",
                                    "x": "ROLE_ID", "y": ["DEALS"]})
    assert not (project.record.path / path).exists()

    joined = _read("Deals by team", ["OPPORTUNITY_OWNER_POSITION", "DEALS"],
                   [["Enterprise East", 12], ["Enterprise West", 9]])
    orch._hold_read(tid, joined)
    _write(orch, project, tid, {"path": path, "table": "Deals by team",
                                "x": "OPPORTUNITY_OWNER_POSITION", "y": ["DEALS"]})
    assert (project.record.path / path).exists()


def test_the_next_turn_does_not_chart_this_turns_rows(tmp_path):
    orch, tid, project = _armed(tmp_path, [
        _read("Competitor calls", ["COMPETITOR", "CALLS"], CALLS)])
    orch._mint_live_read_token(tid)

    with pytest.raises(ValueError, match="no result called"):
        _write(orch, project, tid, {"path": f"examples/{tid}/calls.png",
                                    "table": "Competitor calls", "x": "COMPETITOR", "y": ["CALLS"]})
