"""A Chat bar can carry a label read from a column of the result, not only its own value (#758).

Demo rerun on `7a644ff`, prompt 2. The skill asks for each competitor's bar labelled
`71 calls · 28.7% win rate · 94 closed deals`, read from the query. The chart could only print each
bar's own value (`81`, `44`, `43`…), and a second y column became its own panel (#746), so the label
was not expressible. `bar_label` names a column of the result whose text is each bar's label. It
names a COLUMN, never a string: the statement builds the text, so it is still the rows.

One y column, no `by`, bars only. With several series or a stacked split, which bar a row's text
belongs to is not one answer, and a line has no bars — so those are refused, not guessed.
"""

from __future__ import annotations

import io
import itertools

import pytest
from PIL import Image

from sage.liveread.held import HeldRead
from sage.workspace import chat_chart

from .test_a_chat_chart_is_drawn_from_a_reads_rows import _armed, _write
from .test_a_chat_chart_is_laid_out_for_reading import CARD_PX, PIPELINE, _bars, _drawn, _texts

# The demo's shape: 4–8 competitors, calls plotted, the label built in the statement.
_DEMO = [("Amazon Web Services", 81, 28.7, 164), ("Microsoft Azure", 44, 31.2, 101),
         ("Databricks", 43, 22.0, 94), ("Google Cloud Platform", 21, 40.0, 35),
         ("SAS", 18, 100.0, 4), ("Sagemaker", 12, 25.0, 16), ("Build In House", 9, 18.5, 27),
         ("Dataiku", 4, 50.0, 2)]
LABELS = [f"{c} calls · {w}% win rate · {d} closed deals" for _, c, w, d in _DEMO]
COMPETITORS = HeldRead(
    title="Competitor mentions and win rate", slug="competitors",
    columns=["Competitor", "Calls mentioning it", "Win rate %", "Label"],
    rows=[[n, c, w, label] for (n, c, w, _), label in zip(_DEMO, LABELS, strict=True)])
CHART = {"x": "Competitor", "ys": ["Calls mentioning it"], "bar_label": "Label"}


@pytest.fixture(autouse=True)
def _fonts_closed():
    """A test that draws a figure itself opens font files `chat_chart.draw` would have closed."""
    from matplotlib import font_manager

    yield
    font_manager._get_font.cache_clear()


def _said(ax) -> list[str]:
    """Each bar's label, unwrapped: a long one is drawn over two lines."""
    return [" ".join(t.get_text().split()) for t in ax.texts]


# --- the label ------------------------------------------------------------------------------------

def test_each_bar_is_labelled_with_its_rows_text_from_the_column_named():
    fig, _ = _drawn(COMPETITORS, **CHART)
    [ax] = fig.axes
    assert _said(ax) == LABELS
    assert [p.get_width() for p in ax.patches] == [c for _, c, _, _ in _DEMO], "still the calls"


def test_a_row_with_no_label_text_draws_its_bar_unlabelled():
    read = HeldRead(title="t", slug="t", columns=["Competitor", "Calls", "Label"],
                    rows=[["A", 3, "3 calls"], ["B", 2, None]])
    fig, _ = _drawn(read, "Competitor", ["Calls"], bar_label="Label")
    assert _said(fig.axes[0]) == ["3 calls", ""]


def test_without_a_label_column_a_bar_is_labelled_with_its_value():
    fig, _ = _drawn(COMPETITORS, "Competitor", ["Calls mentioning it"])
    assert _said(fig.axes[0]) == [str(c) for _, c, _, _ in _DEMO]


# --- what is refused ------------------------------------------------------------------------------

@pytest.mark.parametrize("read,chart,refusal", [
    (COMPETITORS, {**CHART, "bar_label": "Win label"}, "Win label is not a column"),
    (COMPETITORS, {**CHART, "ys": ["Calls mentioning it", "Win rate %"]}, "one y column"),
    (PIPELINE, {"x": "Team", "ys": ["Open pipeline"], "by": "Stage", "bar_label": "Stage"},
     "by"),
    (HeldRead(title="Calls by week", slug="w", columns=["Week", "Calls", "Label"],
              rows=[["2026-09-01", 4, "4 calls"], ["2026-09-08", 6, "6 calls"]]),
     {"x": "Week", "ys": ["Calls"], "bar_label": "Label"}, "line"),
], ids=["unknown-column", "two-measures", "split", "dated"])
def test_a_label_column_the_chart_cannot_draw_is_refused(read, chart, refusal):
    with pytest.raises(ValueError, match=refusal):
        chat_chart.figure(read, chart["x"], chart["ys"],
                          **{k: v for k, v in chart.items() if k not in ("x", "ys")})


# --- readable at the column -----------------------------------------------------------------------

_OVERLONG = ("81 calls mentioning it across every region · 28.7% win rate over the trailing "
             "four quarters · 164 closed deals")


def test_a_label_past_two_lines_is_cut_short_where_it_can_be_seen():
    read = HeldRead(title="t", slug="t", columns=["Competitor", "Calls", "Label"],
                    rows=[["A", 81, _OVERLONG]])
    fig, _ = _drawn(read, "Competitor", ["Calls"], bar_label="Label")
    [text] = fig.axes[0].texts
    lines = text.get_text().split("\n")
    assert len(lines) == 2 and lines[1].endswith("…"), lines
    assert _OVERLONG.startswith(lines[0])


@pytest.mark.parametrize("n,overlong", [(4, False), (8, False), (8, True)],
                         ids=["four", "eight", "eight-overlong"])
def test_long_labels_sit_clear_of_each_other_and_the_bars_and_inside_the_figure(n, overlong):
    rows = [[*r[:3], _OVERLONG if overlong else r[3]] for r in COMPETITORS.rows[:n]]
    read = HeldRead(title=COMPETITORS.title, slug="c", columns=COMPETITORS.columns, rows=rows)
    fig, renderer = _drawn(read, **CHART)
    texts = _texts(fig)
    boxes = [t.get_window_extent(renderer) for t in texts]
    bars = [b.get_window_extent(renderer) for b in _bars(fig)]
    for (a, ta), (b, tb) in itertools.combinations(zip(boxes, texts, strict=True), 2):
        assert not a.overlaps(b), f"{ta.get_text()!r} overlaps {tb.get_text()!r}"
    width, height = fig.get_figwidth() * fig.dpi, fig.get_figheight() * fig.dpi
    for box, text in zip(boxes, texts, strict=True):
        assert 0 <= box.x0 and box.x1 <= width and 0 <= box.y0 and box.y1 <= height, (
            f"{text.get_text()!r} runs off the figure")
    for text in fig.axes[0].texts:
        box = text.get_window_extent(renderer)
        assert not [b for b in bars if box.overlaps(b)], f"{text.get_text()!r} is on a bar"
        assert not text.get_clip_on() or text.get_clip_box() is None, "clipped by the axes"


def test_a_labelled_chart_fits_its_card():
    data = chat_chart.draw(COMPETITORS, CHART["x"], CHART["ys"], bar_label=CHART["bar_label"])
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
    assert width == chat_chart.COLUMN_PX * 2
    assert height / 2 <= CARD_PX, f"{height / 2:.0f} px tall at the column"


# --- through artifact_write -----------------------------------------------------------------------

def test_artifact_write_passes_the_label_column_to_the_chart(tmp_path):
    orch, tid, project = _armed(tmp_path, [COMPETITORS])
    try:
        path = f"examples/{tid}/competitors.png"
        body = {"path": path, "table": COMPETITORS.title, "x": "Competitor",
                "y": ["Calls mentioning it"]}

        with pytest.raises(ValueError, match="Win label is not a column"):
            _write(orch, project, tid, {**body, "bar_label": "Win label"})
        with pytest.raises(ValueError, match="bar_label"):
            _write(orch, project, tid, {**body, "bar_label": ["Label"]})
        assert not (project.record.path / path).exists()

        _write(orch, project, tid, {**body, "bar_label": "Label"})
        assert (project.record.path / path).exists()
    finally:
        orch.shutdown()
