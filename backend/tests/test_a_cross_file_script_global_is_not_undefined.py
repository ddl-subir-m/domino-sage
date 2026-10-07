"""A name another of the app's scripts declares is not undefined (#691).

Measured: a follow-up on a fastapi-antd app (`Signal Room 3`) made a one-line edit, then failed
"Syntax check: 9 error(s)" on `driftMath`, which `static/driftMath.js` declares at top level and
`static/app.js` uses — both plain scripts on one page, so in the browser the name is defined. The
model rewrote every reference to `window.driftMath` to satisfy the check. #679 made `no-undef` an
error with a fixed list of page globals; the app's own top-level names were never on it.

So a top-level `function`/`const`/`let`/`var`/`class` or a `window.x =` in one of the app's
scripts counts as defined for the others, in the per-file and the end-of-turn check. A name nothing
declares is still an error, and so is #679's hook after an early return.

Real oxlint needs `template/react-vite/node_modules` (repo root only) and `node` on PATH; these
tests skip elsewhere — run with `-rs`.
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

pytestmark = pytest.mark.skipif(
    not (OXLINT.is_file() and shutil.which("node")),
    reason="real oxlint needs template/react-vite/node_modules (repo root only) and node on PATH",
)

DRIFT_MATH_JS = """\
function driftMath(days) { return days * 2; }
const STAGES = ['open', 'won'];
let lastRun = null;
var counter = 0;
class Ledger {}
window.formatDays = function (d) { return d + ' days'; };
"""

APP_JS = """\
(function () {
  const { Card } = antd;
  function App() {
    lastRun = Date.now();
    counter += 1;
    return h(Card, { title: formatDays(driftMath(STAGES.length)) }, String(new Ledger()));
  }
  ReactDOM.createRoot(document.getElementById('root')).render(h(App));
})();
"""


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGE_TEMPLATE", str(REACT_TEMPLATE))
    app = tmp_path / "app"
    (app / ".sage").mkdir(parents=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": "fastapi-antd"}))
    (app / "app.py").write_text("x = 1\n")
    (app / "static").mkdir()
    (app / "static" / "index.html").write_text(
        '<script src="driftMath.js"></script>\n<script src="app.js"></script>\n')
    (app / "static" / "driftMath.js").write_text(DRIFT_MATH_JS)
    (app / "static" / "app.js").write_text(APP_JS)
    return app


def _undefined(report) -> list[str]:
    return [e.message for e in report.errors if e.code == "eslint(no-undef)"]


def test_a_name_another_script_declares_passes_both_checks(app):
    landed = check_file(app, "static/app.js")
    assert landed is not None and landed.ok, landed.as_agent_message()

    turn = FeedbackRunner().check(app)
    assert turn.ok, turn.as_agent_message()


def test_a_typo_of_that_name_still_fails_both_checks(app):
    (app / "static" / "app.js").write_text(APP_JS.replace("driftMath(", "drfitMath("))

    landed = check_file(app, "static/app.js")
    assert landed is not None and _undefined(landed) == ["'drfitMath' is not defined."]

    turn = FeedbackRunner().check(app)
    assert _undefined(turn) == ["'drfitMath' is not defined."]


def test_a_name_declared_only_inside_a_function_is_still_undefined(app):
    """An IIFE's locals are not page globals: a `function helper` indented inside another script's
    wrapper does not define `helper` here."""
    (app / "static" / "helpers.js").write_text("(function () {\n  function helper() {}\n})();\n")
    (app / "static" / "app.js").write_text("console.log(helper);\n")

    landed = check_file(app, "static/app.js")
    assert landed is not None and _undefined(landed) == ["'helper' is not defined."]


def test_a_hook_after_an_early_return_still_fails(app):
    (app / "static" / "app.js").write_text("""\
function Deals() {
  const [open] = React.useState(driftMath(1));
  if (open) return null;
  React.useMemo(() => STAGES, []);
  return null;
}
window.Deals = Deals;
""")
    landed = check_file(app, "static/app.js")
    assert landed is not None and [e.line for e in landed.errors if "rules-of-hooks" in e.code] == [4]
    assert _undefined(landed) == []
