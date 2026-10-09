"""Hand-written URL state (`SAGE005`) and a list given as a series colour (`SAGE006`) fail the check (#717).

Live (#709, haiku): the Usage Drift tab kept its controls in the URL with its own
`window.history.replaceState(...)` and `new URLSearchParams(window.location.search)` instead of
`sage.useViewState`, and crashed. `SAGE004` reads only `useViewState` calls, so it saw nothing. The
"Feature adoption change" chart passed `color: adoptionData.map(...)` as a Highcharts series colour;
a series takes one, so every bar drew with `fill="none"`. Every script parsed both times.

`SAGE005` reads both stacks' app sources, and only where the app has the view-state helper to steer
to. `SAGE006` reads fastapi-antd only: react-vite draws with Recharts, not Highcharts. Sage's own
helpers do both of these things on purpose, so a test runs both checks over both templates.

The react-vite build-loop case needs `template/react-vite/node_modules`, which exists only in the
repo root, so it skips in a worktree — run with `-rs`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from .fake_opencode import Turn
from .test_a_view_state_field_without_a_type_fails_the_check import INLINE_GOOD, ROOT, _app, _check, _screen
from .test_controlled_build_faults_on_each_stack import _ack, _run, selected_stack  # noqa: F401

LIVE_REPLACE = "    window.history.replaceState(null, '', `?${params.toString()}`);\n"
LIVE_SEARCH = "    const params = new URLSearchParams(window.location.search);\n"
URL_BAD = _screen("  function syncUrl(params) {\n" + LIVE_REPLACE + "  }\n")

LIVE_SERIES = ("    series: [{ name: 'Change %', data: adoptionData.map(d => d.changePercent),\n"
               "      color: adoptionData.map(d => d.changePercent > 0 ? '#28a464' : '#c20a29') }],\n")


def _chart(series: str) -> str:
    return _screen("  const adoptionData = [{ changePercent: 1 }, { changePercent: -2 }];\n"
                   "  function draw(el) {\n"
                   "    Highcharts.chart(el, {\n"
                   "      chart: { type: 'column' }, colors: ['#4C6EF5', '#28a464'],\n"
                   f"  {series}"
                   "    });\n"
                   "  }\n")


COLOUR_BAD = _chart(LIVE_SERIES)
COLOUR_GOOD = _chart("    series: [{ name: 'Change %', data: adoptionData.map(d => d.changePercent), "
                     "color: '#4C6EF5' }],\n")


def _codes(report, *codes):
    return [(e.file, e.line, e.code) for e in report.errors if e.code in codes]


@pytest.mark.parametrize("line", [LIVE_REPLACE, LIVE_SEARCH,
                                  "    history.pushState({}, '', '?tab=drift');\n",
                                  "    const tab = location.search.slice(1);\n"],
                         ids=["live-replaceState", "live-location-search", "pushState", "bare-location"])
def test_hand_written_url_state_fails_and_names_the_hook(tmp_path: Path, monkeypatch, line: str):
    report = _check(_app(tmp_path, _screen("  function syncUrl(params) {\n" + line + "  }\n")),
                    monkeypatch)

    assert not report.ok
    assert _codes(report, "SAGE005") == [("static/components/MainScreen.js", 4, "SAGE005")]
    message = report.errors[0].message
    assert line.strip()[:40] in message
    assert "sage.useViewState(" in message and 'type: "enum"' in message and "shareable: true" in message


@pytest.mark.parametrize("body", [
    "",
    "  // window.history.replaceState(null, '', '?a') was the old way\n",
    "  /* const params = new URLSearchParams(window.location.search); */\n",
    "  const note = \"history.pushState( and location.search\";\n",
    "  window.location.hash = '#orders';\n",
], ids=["useViewState", "line-comment", "block-comment", "string", "hash"])
def test_the_hook_and_text_that_only_mentions_the_url_pass(tmp_path: Path, monkeypatch, body: str):
    screen = INLINE_GOOD.replace("  window.app.MainScreen", body + "  window.app.MainScreen")

    report = _check(_app(tmp_path, screen), monkeypatch)

    assert report.ok, report.as_agent_message()


def test_an_app_without_the_view_state_helper_is_not_steered_to_it(tmp_path: Path, monkeypatch):
    app = _app(tmp_path, URL_BAD)
    (app / "static" / "sage" / "viewState.js").unlink()

    report = _check(app, monkeypatch)

    assert report.ok, report.as_agent_message()


def _react_app(tmp_path: Path, app_tsx: str) -> Path:
    app = tmp_path / "app"
    (app / ".sage").mkdir(parents=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": "react-vite"}))
    (app / "src").mkdir()
    for name in ("appViewState.ts", "reportRuntimeError.ts"):
        shutil.copy(ROOT / "template/react-vite/src" / name, app / "src" / name)
    (app / "src" / "App.tsx").write_text(app_tsx)
    return app


def test_react_vite_hand_written_url_state_fails_naming_its_hook(tmp_path: Path, monkeypatch):
    app = _react_app(tmp_path, "export default function App() {\n" + LIVE_SEARCH
                     + "  return <main>{params.get('m')}</main>;\n}\n")

    report = _check(app, monkeypatch)

    assert not report.ok
    assert _codes(report, "SAGE005") == [("src/App.tsx", 2, "SAGE005")]
    assert "useViewState from src/appViewState.ts" in report.errors[0].message


def test_react_vite_hook_passes(tmp_path: Path, monkeypatch):
    app = _react_app(tmp_path, (
        'import { useViewState } from "./appViewState";\n'
        "export default function App() {\n"
        '  const [view] = useViewState({ m: { type: "enum", values: ["a"], default: "a", shareable: true } });\n'
        "  return <main>{view.m}</main>;\n}\n"))

    report = _check(app, monkeypatch)

    assert report.ok, report.as_agent_message()


@pytest.mark.parametrize("series", [
    LIVE_SERIES,
    "    series: [{ name: 'Revenue', data: [1, 2], color: ['#4C6EF5', '#28a464'] }],\n",
    "    series: rows.map(r => ({ name: r.name, data: r.values, color: r.values.map(v => '#fff') })),\n",
], ids=["live-map", "array-literal", "series-built-by-map"])
def test_a_list_series_colour_fails_with_the_one_colour_shape(tmp_path: Path, monkeypatch, series: str):
    report = _check(_app(tmp_path, _chart(series)), monkeypatch)

    assert not report.ok
    assert [e.code for e in report.errors] == ["SAGE006"]
    message = report.errors[0].message
    assert "one colour" in message and "data: rows.map(r => ({ y:" in message


@pytest.mark.parametrize("series", [
    "    series: [{ name: 'Change %', data: adoptionData.map(d => d.changePercent), color: '#4C6EF5' }],\n",
    ("    series: [{ name: 'Change %', color: sage.accents[0],\n"
     "      data: adoptionData.map(d => ({ y: d.changePercent, color: d.changePercent > 0 ? '#28a464' : '#c20a29' })) }],\n"),
    "    plotOptions: { pie: { colors: ['#4C6EF5', '#28a464'] } },\n",
    "    tooltip: { style: { color: ['#4C6EF5'] } },\n",
    "    // series: [{ color: adoptionData.map(d => '#fff') }],\n",
], ids=["single-colour", "per-point", "colors-list", "not-in-series", "comment"])
def test_one_colour_and_per_point_colours_pass(tmp_path: Path, monkeypatch, series: str):
    report = _check(_app(tmp_path, _chart(series)), monkeypatch)

    assert report.ok, report.as_agent_message()


@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_neither_check_flags_anything_in_the_templates(tmp_path: Path, monkeypatch, stack: str):
    app = tmp_path / "app"
    shutil.copytree(ROOT / "template" / stack, app, ignore=shutil.ignore_patterns("node_modules"))
    (app / ".sage").mkdir(exist_ok=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": stack}))
    helper = "static/sage/viewState.js" if stack == "fastapi-antd" else "src/appViewState.ts"
    assert "replaceState" in (app / helper).read_text()

    report = _check(app, monkeypatch)

    assert _codes(report, "SAGE005", "SAGE006") == []


@pytest.mark.parametrize("bad, good, code", [
    (URL_BAD, INLINE_GOOD, "SAGE005"),
    (COLOUR_BAD, COLOUR_GOOD, "SAGE006"),
], ids=["url-state", "series-colour"])
@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_a_turn_that_writes_either_pattern_is_sent_back_once(selected_stack, bad, good, code):  # noqa: F811
    app = selected_stack
    screen = "static/components/MainScreen.js"
    app.oc.turns.extend([Turn(writes={screen: bad}), Turn(text="Fixed it", writes={screen: good})])

    events, done = _run(app, _ack(app))

    assert [e["ok"] for e in events if e["type"] == "typecheck"] == [False, True]
    assert code in app.oc.prompts[-1]["text"]
    assert len(app.oc.prompts) == 2
    assert done["ok"] is True and done["verification"]["stages"]["code"] == "passed"


@pytest.mark.parametrize("selected_stack", ["react-vite"], indirect=True)
def test_a_react_vite_turn_that_writes_url_state_is_sent_back_once(selected_stack):  # noqa: F811
    app = selected_stack
    bad = ("export default function App() {\n" + LIVE_SEARCH
           + "  return <main>{params.get('m')}</main>;\n}\n")
    app.oc.turns.extend([Turn(writes={app.entry: bad}),
                         Turn(text="Fixed it", writes={app.entry: app.source})])

    events, done = _run(app, _ack(app))

    assert [e["ok"] for e in events if e["type"] == "typecheck"] == [False, True]
    assert "SAGE005" in app.oc.prompts[-1]["text"]
    assert len(app.oc.prompts) == 2
    assert done["ok"] is True and done["verification"]["stages"]["code"] == "passed"
