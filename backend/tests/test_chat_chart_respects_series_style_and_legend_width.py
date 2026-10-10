"""Chart metadata can express a skill's series order and colors without supplying values."""

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba

from sage.liveread.held import HeldRead
from sage.workspace import chat_chart

from .test_a_chat_chart_is_drawn_from_a_reads_rows import _armed, _write
from .test_a_chat_chart_is_laid_out_for_reading import PIPELINE, PIPELINE_CHART

ORDER = ["Discovery", "Qualification", "Technical Validation", "Proposal", "Negotiation"]
COLORS = dict(zip(ORDER, ["#9AA5B1", "#7B93DB", "#4C6FFF", "#7A5AF8", "#C026D3"], strict=True))


@pytest.fixture(autouse=True)
def _fonts_closed():
    from matplotlib import font_manager
    yield
    font_manager._get_font.cache_clear()


def test_pipeline_uses_the_skills_order_and_colors():
    fig = chat_chart.figure(PIPELINE, **PIPELINE_CHART,
                            series_order=ORDER, series_colors=COLORS)
    containers = fig.axes[0].containers
    assert [c.get_label() for c in containers] == [n for n in ORDER if n != "Technical Validation"]
    for container in containers:
        assert container.patches[0].get_facecolor() == to_rgba(COLORS[container.get_label()])


def test_long_legend_names_remain_inside_the_chart():
    read = HeldRead("Topics", "topics",
                    ["Topic", "We raised a concern about it", "Customers raised a concern about it"],
                    [["Governance", 90, 70], ["GenAI", 54, 46]])
    fig = chat_chart.figure(read, "Topic", read.columns[1:])
    FigureCanvasAgg(fig).draw()
    box = fig.legends[0].get_window_extent()
    assert 0 <= box.x0 < box.x1 <= fig.bbox.width
    assert "Customers raised a concern about it" in [
        " ".join(text.get_text().split()) for text in fig.legends[0].texts]


def test_every_series_gets_drawn_when_the_palette_repeats():
    names = [f"Measure {i}" for i in range(20)]
    read = HeldRead("Trends", "trends", ["Date", *names],
                    [["2026-10-01", *range(20)], ["2026-10-02", *range(1, 21)]])
    fig = chat_chart.figure(read, "Date", names)
    assert len(fig.axes[0].lines) == len(names)


def test_chart_route_passes_the_skills_series_style(tmp_path, monkeypatch):
    orch, tid, project = _armed(tmp_path, [PIPELINE])
    captured = {}

    def draw(*args, **kwargs):
        captured.update(kwargs)
        return b"chart"

    monkeypatch.setattr(chat_chart, "draw", draw)
    try:
        _write(orch, project, tid, {
            "path": f"examples/{tid}/pipeline.png", "table": PIPELINE.title,
            "x": "Team", "y": ["Open pipeline"], "by": "Stage",
            "series_order": ORDER, "series_colors": COLORS,
        })
        assert captured["series_order"] == ORDER
        assert captured["series_colors"] == COLORS
    finally:
        orch.shutdown()
