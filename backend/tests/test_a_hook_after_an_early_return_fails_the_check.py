"""A hook called after an early return fails the build's check (#679).

Measured: a fastapi-antd app wrote `if (openDeal) return h(DealPage, …)` above the rest of its
hooks. Clicking a deal crashed the page with Minified React error #300, and the build that wrote it
had reported "Syntax check clean" — `node --check` and `tsc` both read that file as valid, because
it is. Only a rules-of-hooks lint sees it, and the React template already shipped one that nothing
ran.

So oxlint runs where the other checks run: on the one file a write landed (`check_file`) and over
the app's source when the turn ends (`FeedbackRunner.check`). Its errors are `FeedbackError`s and
reach the model the way a type error does; its warnings are not failures; and where the binary is
not installed the check is exactly what it was before.

The real-oxlint tests need `template/react-vite/node_modules`, which exists only in the repo root,
and `node` on PATH (the `oxlint` launcher is a Node script). They skip elsewhere — run with `-rs`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackRunner, check_file

ROOT = Path(__file__).resolve().parents[2]
REACT_TEMPLATE = ROOT / "template" / "react-vite"
OXLINT = REACT_TEMPLATE / "node_modules" / ".bin" / "oxlint"

needs_oxlint = pytest.mark.skipif(
    not (OXLINT.is_file() and shutil.which("node")),
    reason="real oxlint needs template/react-vite/node_modules (repo root only) and node on PATH",
)

# The shape of the crash: a component returns early on a piece of state, and a hook sits below it.
HOOK_AFTER_RETURN_JS = """\
(function () {
  const { createElement: h } = React;
  const { Table } = antd;
  function Deals({ rows }) {
    const [openDeal, setOpenDeal] = React.useState(null);
    if (openDeal) return h(Table, { dataSource: [openDeal] });
    const sorted = React.useMemo(() => rows.slice(), [rows]);
    return h(Table, { dataSource: sorted, onRow: (r) => ({ onClick: () => setOpenDeal(r) }) });
  }
  ReactDOM.createRoot(document.getElementById('root')).render(
    h(sage.ErrorBoundary, null, h(Deals, { rows: [] })));
})();
"""
HOOK_AFTER_RETURN_TSX = """\
import { useMemo, useState } from "react";

export default function App() {
  const [openDeal, setOpenDeal] = useState<string | null>(null);
  if (openDeal) return <main onClick={() => setOpenDeal(null)}>{openDeal}</main>;
  const rows = useMemo(() => ["a", "b"], []);
  return <main>{rows.map((r) => <button key={r} onClick={() => setOpenDeal(r)}>{r}</button>)}</main>;
}
"""


def _fastapi_app(tmp: Path, app_js: str) -> Path:
    app = tmp / "app"
    (app / ".sage").mkdir(parents=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": "fastapi-antd"}))
    (app / "app.py").write_text("x = 1\n")
    (app / "static").mkdir()
    (app / "static" / "app.js").write_text(app_js)
    return app


def _react_app(tmp: Path, app_tsx: str) -> Path:
    """A react-vite app as the workspace manager seeds one: the template's tsconfigs, its
    `node_modules` linked rather than copied, and the app's own `src/`."""
    app = tmp / "app"
    (app / ".sage").mkdir(parents=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": "react-vite"}))
    for name in ("package.json", "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json"):
        shutil.copy(REACT_TEMPLATE / name, app / name)
    (app / "node_modules").symlink_to(REACT_TEMPLATE / "node_modules")
    (app / "src").mkdir()
    (app / "src" / "App.tsx").write_text(app_tsx)
    (app / "src" / "vite-env.d.ts").write_text('/// <reference types="vite/client" />\n')
    return app


def _hooks_errors(report) -> list[tuple[str, int]]:
    return [(e.file, e.line) for e in report.errors if "rules-of-hooks" in e.code]


@pytest.fixture
def react_template(monkeypatch):
    """The template the workspace manager seeds from, which is where a fastapi-antd app (with no
    `node_modules` of its own) finds the binary."""
    monkeypatch.setenv("SAGE_TEMPLATE", str(REACT_TEMPLATE))


@needs_oxlint
def test_a_fastapi_antd_hook_after_an_early_return_fails_both_checks(tmp_path, react_template):
    app = _fastapi_app(tmp_path, HOOK_AFTER_RETURN_JS)

    landed = check_file(app, "static/app.js")
    assert landed is not None and not landed.ok
    assert _hooks_errors(landed) == [("static/app.js", 7)]

    turn = FeedbackRunner().check(app)
    assert not turn.ok
    assert _hooks_errors(turn) == [("static/app.js", 7)]
    assert "static/app.js:7:" in turn.as_agent_message()


@needs_oxlint
def test_a_react_vite_hook_after_an_early_return_fails_both_checks(tmp_path):
    app = _react_app(tmp_path, HOOK_AFTER_RETURN_TSX)

    landed = check_file(app, "src/App.tsx")
    assert landed is not None and not landed.ok
    assert _hooks_errors(landed) == [("src/App.tsx", 6)]

    turn = FeedbackRunner().check(app)
    assert not turn.ok
    assert _hooks_errors(turn) == [("src/App.tsx", 6)]


@needs_oxlint
def test_the_page_globals_are_not_undefined(tmp_path, react_template):
    """Without the globals declared, `no-undef` would flag every line of every app. The starter's
    own scripts and an app using every global the page loads lint clean; a name nothing declares
    is still an error, so the rule is on and not just quiet."""
    template = ROOT / "template" / "fastapi-antd"
    app = _fastapi_app(tmp_path, """\
(function () {
  const { Card } = antd;
  function App() {
    const [n] = React.useState(0);
    return h(Card, { title: dayjs().format('YYYY'), extra: h(icons.ReloadOutlined) },
      String(n), String(typeof Highcharts), String(sage.theme));
  }
  ReactDOM.createRoot(document.getElementById('root')).render(h(App));
})();
""")
    shutil.copy(template / "static" / "theme.js", app / "static" / "theme.js")

    for name in ("static/app.js", "static/theme.js"):
        report = check_file(app, name)
        assert report is not None and report.ok, report.as_agent_message()

    (app / "static" / "app.js").write_text("console.log(notDeclaredAnywhere);\n")
    report = check_file(app, "static/app.js")
    assert [(e.file, e.code) for e in report.errors] == [("static/app.js", "eslint(no-undef)")]


def test_a_missing_oxlint_changes_nothing(tmp_path, monkeypatch):
    """No binary is no new failure mode: the check passes and says nothing, as `check_file` already
    does when `node` is missing."""
    monkeypatch.setenv("SAGE_TEMPLATE", str(tmp_path / "no-template-here"))
    if shutil.which("node"):
        app = _fastapi_app(tmp_path, HOOK_AFTER_RETURN_JS)
        landed = check_file(app, "static/app.js")
        assert landed is not None and landed.ok and landed.errors == []
        turn = FeedbackRunner().check(app)
        assert turn.ok, turn.as_agent_message()

    vite = tmp_path / "vite"
    (vite / ".sage").mkdir(parents=True)
    (vite / ".sage" / "settings.json").write_text(json.dumps({"stack": "react-vite"}))
    (vite / "package.json").write_text("{}")
    (vite / "src").mkdir()
    (vite / "src" / "App.tsx").write_text(HOOK_AFTER_RETURN_TSX)
    assert check_file(vite, "src/App.tsx") is None
