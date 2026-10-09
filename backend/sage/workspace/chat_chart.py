"""A Chat chart, plotted by Sage from the rows a read returned this turn (#729).

The data-artifact lane used to ask the model for SVG, so every bar and label was a literal it
typed — which is how a chart carried 118/94/67/12 calls over a table that said 47/29/25/14. Here the
model names a result and its columns, and nothing else: the labels and the numbers are the rows.

Sized for where it is shown. The Thread's message column is ~450 px wide, so the figure is drawn
4.5 in wide at 200 dpi — 900 px, shown at half — and a 10 pt label lands at ~14 px there. Categories
go down the side as horizontal bars, one label per row, so labels cannot run into each other however
many there are or however long; a series over dates is a line with matplotlib's own date ticks.

`matplotlib.figure.Figure` rather than `pyplot`: no global figure registry to leak between requests
on the route's threadpool.
"""

from __future__ import annotations

import io
import math
import textwrap
from datetime import UTC, date, datetime, time

from ..liveread.held import HeldRead

COLUMN_PX = 450
_WIDTH_IN = 4.5
_DPI = 2 * COLUMN_PX / _WIDTH_IN
_FONT_PT = 10
_MAX_BARS = 30
_LABEL_CHARS = 22
_COLORS = ["#4C6EF5", "#F59F00", "#12B886", "#E64980", "#7950F2", "#FA5252"]


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    try:
        out = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def _when(value) -> datetime | None:
    """A date as an aware UTC datetime, so a column mixing zoned and plain stamps still sorts."""
    if isinstance(value, str) and len(value) >= 10 and value[4] == "-":
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, time(), tzinfo=UTC)
    return None


def _label(value) -> str:
    text = "" if value is None else str(value)
    lines = textwrap.wrap(text, _LABEL_CHARS) or [""]
    if len(lines) > 2:
        lines = [lines[0], textwrap.shorten(" ".join(lines[1:]), _LABEL_CHARS, placeholder="…")]
    return "\n".join(lines)


def _shown(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.3g}" if abs(value) >= 1000 else f"{value:.3g}"


def _series(read: HeldRead, x: str, ys: list[str]) -> tuple[list, dict[str, list]]:
    columns = list(read.columns)
    for name in [x, *ys]:
        if name not in columns:
            raise ValueError(f"{name} is not a column of {read.title}. Its columns: "
                             f"{', '.join(columns)}.")
    if not ys:
        raise ValueError("Name the column or columns to plot as y.")
    labels = [row[columns.index(x)] for row in read.rows]
    values = {y: [_number(row[columns.index(y)]) for row in read.rows] for y in ys}
    for y, series in values.items():
        if all(v is None for v in series):
            raise ValueError(f"{y} holds no numbers to plot.")
    return labels, values


def figure(read: HeldRead, x: str, ys: list[str]):
    from matplotlib.figure import Figure

    labels, values = _series(read, x, ys)
    when = [_when(v) for v in labels]
    if len(labels) > 1 and all(when):
        fig = Figure(figsize=(_WIDTH_IN, 3.0), dpi=_DPI, facecolor="white", layout="constrained")
        ax = fig.add_subplot()
        order = sorted(range(len(labels)), key=lambda i: when[i])
        import matplotlib.dates as mdates

        locator = mdates.AutoDateLocator(maxticks=5)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        for color, (y, series) in zip(_COLORS * 3, values.items(), strict=False):
            ax.plot([when[i] for i in order],
                    [math.nan if series[i] is None else series[i] for i in order], color=color,
                    linewidth=2, label=y)
    else:
        if len(labels) > _MAX_BARS:
            raise ValueError(f"{read.title} has {len(labels)} rows, and a bar chart shows at most "
                             f"{_MAX_BARS}. ORDER BY what matters and LIMIT the statement, then "
                             "chart that result.")
        shown = [_label(v) for v in labels]
        tall = any("\n" in s for s in shown)
        per = (0.42 if tall else 0.3) * max(1.0, 0.7 * len(values))
        fig = Figure(figsize=(_WIDTH_IN, 1.1 + per * len(labels)), dpi=_DPI, facecolor="white",
                     layout="constrained")
        ax = fig.add_subplot()
        height = 0.8 / len(values)
        top = 0.0
        for k, (color, (y, series)) in enumerate(zip(_COLORS * 3, values.items(), strict=False)):
            spots = [i + (k - (len(values) - 1) / 2) * height for i in range(len(labels))]
            bars = ax.barh(spots, [v or 0 for v in series], height=height, color=color, label=y)
            ax.bar_label(bars, labels=[_shown(v) if v is not None else "" for v in series],
                         padding=3, fontsize=_FONT_PT - 1)
            top = max([top, *[abs(v) for v in series if v is not None]])
        ax.set_yticks(range(len(labels)), shown)
        ax.invert_yaxis()
        ax.set_xlim(right=top * 1.2 or 1)
        ax.set_ylabel(x, fontsize=_FONT_PT)
    ax.set_facecolor("white")
    ax.tick_params(labelsize=_FONT_PT)
    ax.set_title(textwrap.fill(read.title, 48), fontsize=_FONT_PT + 1)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if len(values) > 1:
        ax.legend(fontsize=_FONT_PT - 1, frameon=False, loc="lower right")
    return fig


def draw(read: HeldRead, x: str, ys: list[str]) -> bytes:
    from matplotlib import font_manager

    out = io.BytesIO()
    try:
        figure(read, x, ys).savefig(out, format="png", dpi=_DPI, facecolor="white")
    finally:
        # matplotlib holds every face it drew with open in a process-wide cache. A chart is drawn
        # a few times a conversation, so reopening one costs nothing next to holding descriptors.
        font_manager._get_font.cache_clear()
    return out.getvalue()


def check_labels(read: HeldRead, x: str, reads: list[HeldRead]) -> None:
    """Refuse a label column of identifiers that another read this turn names.

    Prompt 1 read a role-ID → team-name mapping to label its chart, could not see the names, and
    charted the IDs. The chart takes its labels from ONE result, so the way to names is the
    statement: join the mapping in and group by the name. Only identifiers — values carrying a
    digit — count, so a column of words another read happens to share is not taken for a lookup.
    """
    if x not in read.columns:
        return
    keys = {str(row[read.columns.index(x)]) for row in read.rows
            if row[read.columns.index(x)] is not None}
    if not keys or not all(any(c.isdigit() for c in k) for k in keys):
        return
    for other in reads:
        if other is read or not other.rows:
            continue
        for k, key_column in enumerate(other.columns):
            names = {str(row[k]): row for row in other.rows}
            if not keys <= names.keys():
                continue
            for n, name_column in enumerate(other.columns):
                if n == k:
                    continue
                if all(isinstance(names[key][n], str) and _number(names[key][n]) is None
                       and names[key][n] != key for key in keys):
                    raise ValueError(
                        f"The {x} values in {read.title} are identifiers that {other.title} names "
                        f"in {name_column}. Join {other.title} into the statement so its result "
                        f"carries {name_column}, then chart that result by {name_column}.")
