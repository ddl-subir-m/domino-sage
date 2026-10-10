"""A Chat chart, plotted by Sage from the rows a read returned this turn (#729).

The data-artifact lane used to ask the model for SVG, so every bar and label was a literal it
typed — which is how a chart carried 118/94/67/12 calls over a table that said 47/29/25/14. Here the
model names a result and its columns, and nothing else: the labels and the numbers are the rows.

Sized for where it is shown. The Thread's message column is ~450 px wide, so the figure is drawn
4.5 in wide at 200 dpi — 900 px, shown at half — and a 10 pt label lands at ~14 px there. Categories
go down the side as horizontal bars, one label per row, so labels cannot run into each other however
many there are or however long; a series over dates is a line with matplotlib's own date ticks.

Drawn as the result's shape, in its units (#746). A result of one measure by two categories — what a
`GROUP BY` of two columns returns — names the second as `by`, and draws one bar per `x` value split
into stacked segments, not one bar per row. Every number is printed one way, never in scientific
notation, as money or a percentage when the y column is named as one. Measures in different units
get a panel each rather than one shared axis, and the legend sits under the plot, never on it.

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
_TITLE_CHARS = 44
# A bar's text sits right of the bar, sharing the plot's width with it: two short lines keep
# `81 calls · 28.7% win rate · 164 closed deals` inside the figure with room left for the bar.
_BAR_LABEL_CHARS = 24
# One category's row, in inches: a 10 pt label is ~0.14 in, so a one-line label keeps clear of the
# next and a 12-bar chart stays inside the 420 px the Thread gives a drawn page (`chat.css`).
_ROW_IN = 0.24
_TALL_ROW_IN = 0.38
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


def _label(value, chars: int = _LABEL_CHARS) -> str:
    text = "" if value is None else str(value)
    lines = textwrap.wrap(text, chars) or [""]
    if len(lines) > 2:
        lines = [lines[0], textwrap.shorten(" ".join(lines[1:]), chars, placeholder="…")]
    return "\n".join(lines)


def _shown(value: float, unit: str = "") -> str:
    """One way per unit, never scientific notation: `$12.4M`, `$850K`, `$9,400`, `28.7%`,
    `5,194,900`. A count keeps every digit; money is cut to the unit people quote it in."""
    if unit == "%":
        return f"{value:,.1f}%"
    sign, size = ("-" if value < 0 else ""), abs(value)
    if unit == "$":
        if size >= 999.95e6:
            return f"{sign}${size / 1e9:,.1f}B"
        if size >= 999.5e3:
            return f"{sign}${size / 1e6:.1f}M"
        if size >= 1e4:
            return f"{sign}${size / 1e3:.0f}K"
        return f"{sign}${size:,.0f}"
    if size >= 100 or value == int(value):
        return f"{value:,.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _units(ys: list[str], money, percent) -> dict[str, str]:
    for name in [*money, *percent]:
        if name not in ys:
            raise ValueError(f"{name} is not one of the y columns ({', '.join(ys)}). Name in money "
                             "and percent only columns that are plotted.")
        if name in money and name in percent:
            raise ValueError(f"{name} cannot be both money and a percentage.")
    return {y: "$" if y in money else "%" if y in percent else "" for y in ys}


def _series(read: HeldRead, x: str, ys: list[str],
            by: str | None = None) -> tuple[list, dict[str, list]]:
    columns = list(read.columns)
    for name in [x, *ys, *([by] if by else [])]:
        if name not in columns:
            raise ValueError(f"{name} is not a column of {read.title}. Its columns: "
                             f"{', '.join(columns)}.")
    if not ys:
        raise ValueError("Name the column or columns to plot as y.")
    if by is None:
        labels = [row[columns.index(x)] for row in read.rows]
        values = {y: [_number(row[columns.index(y)]) for row in read.rows] for y in ys}
    else:
        if len(ys) != 1:
            raise ValueError(f"by splits one measure into parts: name one column as y, not "
                             f"{len(ys)}.")
        ix, ib, iy = columns.index(x), columns.index(by), columns.index(ys[0])
        labels, parts, cells = [], [], {}
        for row in read.rows:
            if (row[ix], row[ib]) in cells:
                raise ValueError(f"{read.title} has more than one row for the same {x} and {by}. "
                                 f"GROUP BY both in the statement, then chart that result.")
            cells[(row[ix], row[ib])] = _number(row[iy])
            labels += [] if row[ix] in labels else [row[ix]]
            parts += [] if row[ib] in parts else [row[ib]]
        values = {str(p): [cells.get((c, p)) for c in labels] for p in parts}
        values = {p: s for p, s in values.items() if any(v is not None for v in s)}
        if not values:
            raise ValueError(f"{ys[0]} holds no numbers to plot.")
        return labels, values
    for y, series in values.items():
        if all(v is None for v in series):
            raise ValueError(f"{y} holds no numbers to plot.")
    return labels, values


def _tick(value: float, unit: str) -> str:
    if unit == "%" and value == int(value):
        return f"{value:,.0f}%"
    return _shown(value, unit)


def _bar_texts(read: HeldRead, x: str, ys: list[str], by: str | None, dated: bool,
               column: str) -> list[str]:
    """Each row's text from `column`, to label its bar in place of its value (#758)."""
    if column not in read.columns:
        raise ValueError(f"{column} is not a column of {read.title}. Its columns: "
                         f"{', '.join(read.columns)}.")
    if by is not None or len(ys) != 1:
        raise ValueError("bar_label labels the bars of one measure: name one y column and no by, "
                         "or send bar_label as null.")
    if dated:
        raise ValueError(f"The {x} values of {read.title} are dates, so it is drawn as a line, "
                         "which has no bars to label. Send bar_label as null.")
    i = read.columns.index(column)
    return [_label(row[i], _BAR_LABEL_CHARS) for row in read.rows]


def _bars(ax, labels: list, values: dict[str, list], unit: str, stacked: bool, colors,
          texts: list[str] | None = None) -> None:
    """Horizontal bars, one row per label, each labelled with its value (or its total, stacked),
    or with its row's text from a label column."""
    rows = range(len(labels))
    if stacked:
        left = [0.0] * len(labels)
        for color, (name, series) in zip(colors, values.items(), strict=False):
            widths = [v or 0.0 for v in series]
            ax.barh(rows, widths, left=left, height=0.7, color=color, label=name)
            left = [a + b for a, b in zip(left, widths, strict=True)]
        filled = [any(s[i] is not None for s in values.values()) for i in rows]
        for i, total in enumerate(left):
            if filled[i]:
                ax.annotate(_shown(total, unit), (total, i), xytext=(3, 0),
                            textcoords="offset points", va="center", fontsize=_FONT_PT - 1)
        return
    height = 0.8 / len(values)
    for k, (color, (name, series)) in enumerate(zip(colors, values.items(), strict=False)):
        spots = [i + (k - (len(values) - 1) / 2) * height for i in rows]
        bars = ax.barh(spots, [v or 0 for v in series], height=height, color=color, label=name)
        ax.bar_label(bars, labels=texts if texts is not None else
                     [_shown(v, unit) if v is not None else "" for v in series],
                     padding=3, fontsize=_FONT_PT - 1)


def figure(read: HeldRead, x: str, ys: list[str], by: str | None = None,
           money=(), percent=(), bar_label: str | None = None):
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    unit_of = _units(ys, money or (), percent or ())
    labels, values = _series(read, x, ys, by)
    units = {name: unit_of[ys[0]] if by else unit_of[name] for name in values}
    # One axis per unit: calls and a win rate on one scale read as one measure (#746).
    panels = ([[name] for name in values] if len(set(units.values())) > 1 else [list(values)])
    legend = any(len(p) > 1 for p in panels)
    title = textwrap.fill(read.title, _TITLE_CHARS)
    when = [_when(v) for v in labels]
    dated = len(labels) > 1 and all(when)
    texts = None if bar_label is None else _bar_texts(read, x, ys, by, dated, bar_label)
    if dated:
        height = 3.0
    else:
        if len(labels) > _MAX_BARS:
            raise ValueError(f"{read.title} has {len(labels)} rows, and a bar chart shows at most "
                             f"{_MAX_BARS}. ORDER BY what matters and LIMIT the statement, then "
                             "chart that result.")
        shown = [_label(v) for v in labels]
        per = _TALL_ROW_IN if any("\n" in s for s in [*shown, *(texts or [])]) else _ROW_IN
        if not by:
            per *= max(1.0, 0.8 * max(len(p) for p in panels))
        height = (0.8 + 0.2 * title.count("\n") + (0.35 if legend else 0)
                  + (0.2 if len(panels) > 1 else 0) + per * len(labels))
    fig = Figure(figsize=(_WIDTH_IN, height), dpi=_DPI, facecolor="white", layout="constrained")
    axes = fig.subplots(1, len(panels), sharey=True, squeeze=False)[0]
    colors = _COLORS * 3
    for ax, names in zip(axes, panels, strict=True):
        unit = units[names[0]]
        shade = colors[list(values).index(names[0]):] if len(panels) > 1 else colors
        part = {name: values[name] for name in names}
        if dated:
            import matplotlib.dates as mdates

            order = sorted(range(len(labels)), key=lambda i: when[i])
            locator = mdates.AutoDateLocator(maxticks=5)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _, u=unit: _tick(v, u)))
            for color, (name, series) in zip(shade, part.items(), strict=False):
                ax.plot([when[i] for i in order],
                        [math.nan if series[i] is None else series[i] for i in order],
                        color=color, linewidth=2, label=name)
        else:
            _bars(ax, labels, part, unit, stacked=by is not None, colors=shade, texts=texts)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _, u=unit: _tick(v, u)))
            ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
            if len(panels) > 1:
                # Too narrow for ticks to clear each other, and every bar in it is labelled.
                ax.set_xticks([])
        ax.set_facecolor("white")
        ax.tick_params(labelsize=_FONT_PT)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        if len(panels) > 1:
            ax.set_title(textwrap.fill(names[0], 24), fontsize=_FONT_PT)
    if not dated:
        axes[0].set_yticks(range(len(labels)), shown)
        axes[0].invert_yaxis()
        axes[0].set_ylabel(x, fontsize=_FONT_PT)
    fig.suptitle(title, fontsize=_FONT_PT + 1)
    if legend:
        handles, names = axes[0].get_legend_handles_labels()
        fig.legend(handles, names, loc="outside lower center", ncols=min(len(names), 3),
                   fontsize=_FONT_PT - 1, frameon=False)
    return fig


def draw(read: HeldRead, x: str, ys: list[str], by: str | None = None,
         money=(), percent=(), bar_label: str | None = None) -> bytes:
    from matplotlib import font_manager

    out = io.BytesIO()
    try:
        figure(read, x, ys, by, money, percent, bar_label).savefig(out, format="png", dpi=_DPI,
                                                                   facecolor="white")
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
