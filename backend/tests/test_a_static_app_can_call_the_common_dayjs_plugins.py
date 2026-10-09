"""The static template's page loads the Day.js plugins a generated app reaches for (#741).

Measured: a Build turn wrote `curStart.isBetween(...)` in a fastapi-antd component and the page
crashed at render with `isBetween is not a function`. AGENTS.md said so — core only — and the model
spent four repairs adding null guards instead. So the page carries the common plugins and extends
them, and the toolbox row says exactly which plugins the page has.

"The page has" is measured, not listed: `antd.min.js` extends the global `dayjs` with the plugins
its pickers need as it loads, so the row is held to what runs, every vendored script in page order.

The plugins are the builds that ship with the core already vendored: `dayjs.min.js` is
byte-identical from 1.11.10 to 1.11.20, so its version cannot be read off the file, and the newest
release with that core, 1.11.20, is the one pinned. The core itself is pinned too, so the plugins
cannot drift from it unseen.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "template" / "fastapi-antd"
VENDOR = TEMPLATE / "static" / "vendor"
INDEX = (TEMPLATE / "static" / "index.html").read_text()
AGENTS = (TEMPLATE / "AGENTS.md").read_text()

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

# dayjs 1.11.20, `package/plugin/<name>.js` from the npm tarball.
PLUGINS = {
    "isBetween": "00d079ea67e5afd2bcdfd972cdaa4a46d6389d3f1110c753acda28da86fe72d9",
    "isSameOrAfter": "ec4f67ae45b6c9ccc1a2b6d0d69419600e81792bf8aa93ea419d6adce98deb37",
    "isSameOrBefore": "8d224646d3a5f834861c98eb46b8b0003092b1a063f9f19fda46d94f0a4fe4e6",
    "utc": "7e01072e6f1f2e646506a083ccc500ed19c5a657a5a6645b3fafeccdf3ce9c0a",
    "customParseFormat": "b199a58d0fbbdc519a072eb9b140a2ca3d4775ea1b054a0c0e5aeed86603c941",
}
CORE_SHA256 = "9cfdb93f38afcf2d076abecd66d32bfd3383cdf1967654ebc26a26605daf4173"

# The measured shape: a component compares a date against a range at render.
COMPONENT = """
function UsageDrift() {
  const curStart = dayjs('2026-06-01');
  return curStart.isBetween(dayjs('2026-01-01'), dayjs('2026-12-31'))
    && curStart.isSameOrAfter(dayjs('2026-06-01')) && curStart.isSameOrBefore(dayjs('2026-06-01'))
    && dayjs.utc('2026-06-01T12:00:00Z').format('HH') === '12'
    && dayjs('01/06/2026', 'DD/MM/YYYY').format('YYYY-MM-DD') === '2026-06-01';
}
UsageDrift();
"""

# Whether the page's `dayjs` carries each plugin, told by what the plugin adds.
DETECT = """
const d = dayjs('2026-06-01');
({
  isBetween: typeof d.isBetween === 'function',
  isSameOrAfter: typeof d.isSameOrAfter === 'function',
  isSameOrBefore: typeof d.isSameOrBefore === 'function',
  utc: typeof dayjs.utc === 'function',
  customParseFormat: dayjs('01/06/2026', 'DD/MM/YYYY').format('YYYY-MM-DD') === '2026-06-01',
  weekday: typeof d.weekday === 'function',
  localeData: typeof d.localeData === 'function',
  weekOfYear: typeof d.week === 'function',
  weekYear: typeof d.weekYear === 'function',
  advancedFormat: d.format('Do') === '1st',
  relativeTime: typeof d.fromNow === 'function',
  duration: typeof dayjs.duration === 'function',
  timezone: typeof d.tz === 'function',
  isoWeek: typeof d.isoWeek === 'function',
  quarterOfYear: typeof d.quarter === 'function',
  minMax: typeof dayjs.max === 'function',
});
"""

# Runs each script in one shared global, in page order, then the probe. A script that throws fails
# the run. The `document` stub is what `highcharts-more.js` touches as it loads, and no more.
HARNESS = """
const vm = require('vm');
const { scripts, probe } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = vm.createContext({ setTimeout, clearTimeout, MessageChannel, navigator: { userAgent: 'node' },
  document: { createEvent: () => ({ initEvent() {} }) } });
ctx.window = ctx;
ctx.self = ctx;
for (const code of scripts) vm.runInContext(code, ctx);
process.stdout.write(JSON.stringify(vm.runInContext(probe, ctx)));
process.exit(0);  // React's scheduler holds a MessageChannel open
"""

_SCRIPT_RE = re.compile(r"<script(?:\s+src=\"([^\"]+)\")?\s*>(.*?)</script>", re.DOTALL)


def _page_toolbox() -> list[str]:
    """What the page runs before Sage's helpers and the app: every `static/vendor/` script and every
    inline script, in page order."""
    scripts = []
    for src, body in _SCRIPT_RE.findall(INDEX):
        if src.startswith("static/vendor/"):
            scripts.append((TEMPLATE / src).read_text())
        elif not src and body.strip():
            scripts.append(body)
    return scripts


def _on_the_page(probe: str):
    got = subprocess.run(
        ["node", "-e", HARNESS], input=json.dumps({"scripts": _page_toolbox(), "probe": probe}),
        capture_output=True, text=True, timeout=30,
    )
    assert got.returncode == 0, got.stderr[-2000:]
    return json.loads(got.stdout)


@needs_node
def test_a_component_calling_the_plugins_runs_on_the_page():
    assert _on_the_page(COMPONENT) is True


@needs_node
def test_the_toolbox_row_names_exactly_the_plugins_the_page_carries():
    carried = {name for name, present in _on_the_page(DETECT).items() if present}
    row = next(line for line in AGENTS.splitlines() if line.startswith("| `dayjs` |"))
    loaded, _, missing = row.partition("No other plugin")
    assert _named(loaded) & set(re.findall(r"^\s+(\w+):", DETECT, re.MULTILINE)) == carried
    assert _named(missing) & carried == set()
    assert set(PLUGINS) <= carried


def _named(text: str) -> set[str]:
    return set(re.findall(r"`(\w+)`", text))


@pytest.mark.parametrize("name", PLUGINS)
def test_each_plugin_is_the_build_that_ships_with_the_vendored_core(name: str):
    path = VENDOR / f"dayjs-plugin-{name}.min.js"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PLUGINS[name]


def test_the_core_is_not_upgraded_under_its_plugins():
    assert hashlib.sha256((VENDOR / "dayjs.min.js").read_bytes()).hexdigest() == CORE_SHA256


def test_no_dayjs_plugin_is_vendored_that_is_not_pinned():
    assert {p.name for p in VENDOR.glob("dayjs-plugin-*")} == {
        f"dayjs-plugin-{name}.min.js" for name in PLUGINS}
