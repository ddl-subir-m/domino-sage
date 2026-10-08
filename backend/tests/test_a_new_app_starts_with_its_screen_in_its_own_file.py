"""A new app's first screen is its own file, and the entry file is only the shell (#697).

Symptom: a new Guided app put its whole UI in `static/app.js` (or `src/App.tsx`), so a follow-up to
one screen had to re-read and re-edit the entry file, and the instructions insisted on editing it.
Now both starters seed `MainScreen` beside a shell that mounts it, and what has to hold is:

  - an untouched seeded starter still fails `SAGE001`, though its placeholder moved into the screen;
  - implementing only the screen is a complete app, with the shell left byte-identical;
  - a screen script `static/index.html` loads AFTER `static/app.js` fails a code check that says how
    to fix it, and one it does not load at all still fails `SAGE002`;
  - an app whose UI is all in its entry file still passes, and nothing extracts a screen from it;
  - the final provider request carries the new layout rule once, on both stacks;
  - a follow-up that changes only the screen completes, and its repeat can end `already done`.

The checks run against workspaces seeded from the real templates. Only `tsc` is simulated on the
React stack; `node --check` and `py_compile` run for real on the FastAPI one.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage.feedback.runner import FeedbackRunner
from sage.gateway.protocol import Protocol
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode
from sage.shim.native import prepare_native
from sage.workspace.manager import WorkspaceManager

from .fake_opencode import FakeOpenCode, Turn, execution_plan
from .test_native_policy import body as native_body
from .test_native_policy import shim as native_shim
from .test_turn_path import (
    OkFeedback,
    ScriptedGateway,
    _catalog,
    _done,
    _get_built,
    _no_waiting,  # noqa: F401
    _run,
)

REPO = Path(__file__).resolve().parents[2]
SCREEN_TAG = '<script src="static/components/MainScreen.js"></script>'
ENTRY_TAG = '<script src="static/app.js"></script>'
#: The one sentence both starters' instructions carry for the new layout rule.
LAYOUT_RULE = "A follow-up changes only the files its request is about"

FASTAPI_SCREEN = """\
(function () {
  const { createElement: h } = React;
  window.app = window.app || {};

  function MainScreen() {
    return h('main', { className: 'app' },
      h(antd.Typography.Title, { level: 2 }, 'Sample Intake Log'));
  }

  window.app.MainScreen = MainScreen;
})();
"""

REPORT_SCREEN = """\
(function () {
  const { createElement: h } = React;
  window.app = window.app || {};

  function ReportScreen() {
    return h(antd.Card, { title: 'Late samples by site' }, 'Totals');
  }

  window.app.ReportScreen = ReportScreen;
})();
"""

TWO_SCREEN_SHELL = """\
(function () {
  const { createElement: h } = React;
  const { ConfigProvider, Tabs } = antd;

  function App() {
    return h(ConfigProvider, { theme: sage.theme },
      h(Tabs, { items: [
        { key: 'main', label: 'Samples', children: h(window.app.MainScreen) },
        { key: 'report', label: 'Report', children: h(window.app.ReportScreen) },
      ] }));
  }

  ReactDOM.createRoot(document.getElementById('root')).render(
    h(sage.ErrorBoundary, null, h(App)));
})();
"""

# An app built before screens had their own file: the whole UI in the entry, no screen tag.
LEGACY_ENTRY = """\
(function () {
  const { createElement: h } = React;
  const { ConfigProvider, Table } = antd;

  function App() {
    return h(ConfigProvider, { theme: sage.theme },
      h('main', { className: 'app' }, h(Table, { columns: [], dataSource: [] })));
  }

  ReactDOM.createRoot(document.getElementById('root')).render(
    h(sage.ErrorBoundary, null, h(App)));
})();
"""

# The entry every fastapi-antd app born before this change still holds, untouched.
LEGACY_STARTER = """\
(function () {
  const { createElement: h } = React;
  const { ConfigProvider } = antd;

  function App() {
    return h(ConfigProvider, { theme: sage.theme },
      h('main', { className: 'sage-placeholder' },
        h('h1', null, 'Your app will appear here')));
  }

  ReactDOM.createRoot(document.getElementById('root')).render(
    h(sage.ErrorBoundary, null, h(App)));
})();
"""

# A self-contained script loaded after the entry: it defines nothing another script reads.
LATE_SELF_CONTAINED = "(function () { document.title = document.title + ' (beta)'; })();\n"

REACT_SCREEN = "export default function MainScreen() {\n  return <main><h1>Sample Intake Log</h1></main>;\n}\n"
REACT_LEGACY_ENTRY = "export default function App() {\n  return <main><h1>Sample Intake Log</h1></main>;\n}\n"
REACT_LEGACY_STARTER = (
    'export default function App() {\n'
    '  return (\n    <main className="sage-placeholder">\n      <h1>Your app will appear here</h1>\n'
    '    </main>\n  );\n}\n')


def _seed(tmp_path: Path, stack: str) -> tuple[WorkspaceManager, Path]:
    mgr = WorkspaceManager(workspace_dir=tmp_path / "ws", template=REPO / "template" / "react-vite")
    return mgr, mgr.ensure("proj", stack=stack).path


@pytest.fixture
def fastapi_app(tmp_path: Path) -> Path:
    return _seed(tmp_path, "fastapi-antd")[1]


@pytest.fixture
def react_app(tmp_path: Path, monkeypatch) -> Path:
    # Only tsc (and oxlint, where installed) is simulated: a clean typecheck is the case under test.
    monkeypatch.setattr("sage.feedback.runner.subprocess.run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    return _seed(tmp_path, "react-vite")[1]


def _codes(report) -> list[tuple[str, str]]:
    return [(e.file, e.code) for e in report.errors]


def _index(app: Path) -> Path:
    return app / "static" / "index.html"


# ---- the untouched starter is still unfinished ----------------------------------------------------


def test_an_untouched_fastapi_starter_fails_sage001_on_its_screen(fastapi_app: Path):
    report = FeedbackRunner().check(fastapi_app)

    assert _codes(report) == [("static/components/MainScreen.js", "SAGE001")]


def test_an_untouched_react_starter_fails_sage001_on_its_screen(react_app: Path):
    report = FeedbackRunner().check(react_app)

    assert _codes(report) == [("src/screens/MainScreen.tsx", "SAGE001")]


# ---- implementing the screen alone is a finished app ----------------------------------------------


def test_implementing_only_the_screen_completes_a_fastapi_app(fastapi_app: Path):
    shell = (fastapi_app / "static" / "app.js").read_bytes()
    (fastapi_app / "static" / "components" / "MainScreen.js").write_text(FASTAPI_SCREEN)

    report = FeedbackRunner().check(fastapi_app)

    assert report.ok, report.as_agent_message()
    assert shell == (REPO / "template/fastapi-antd/static/app.js").read_bytes()


def test_implementing_only_the_screen_completes_a_react_app(react_app: Path):
    shell = (react_app / "src" / "App.tsx").read_bytes()
    (react_app / "src" / "screens" / "MainScreen.tsx").write_text(REACT_SCREEN)

    report = FeedbackRunner().check(react_app)

    assert report.ok, report.as_agent_message()
    assert shell == (REPO / "template/react-vite/src/App.tsx").read_bytes()


def test_a_two_screen_app_loads_both_screens_and_passes(fastapi_app: Path):
    components = fastapi_app / "static" / "components"
    (components / "MainScreen.js").write_text(FASTAPI_SCREEN)
    (components / "ReportScreen.js").write_text(REPORT_SCREEN)
    (fastapi_app / "static" / "app.js").write_text(TWO_SCREEN_SHELL)
    index = _index(fastapi_app)
    index.write_text(index.read_text().replace(
        SCREEN_TAG, SCREEN_TAG + '\n  <script src="static/components/ReportScreen.js"></script>'))

    report = FeedbackRunner().check(fastapi_app)

    assert report.ok, report.as_agent_message()


# ---- script order -------------------------------------------------------------------------------


def test_a_screen_loaded_after_the_entry_fails_with_the_fix_named(fastapi_app: Path):
    """Both tags present, so `SAGE002` is satisfied; the page still breaks, because the shell runs
    before the screen it mounts has registered."""
    (fastapi_app / "static" / "components" / "MainScreen.js").write_text(FASTAPI_SCREEN)
    index = _index(fastapi_app)
    html = index.read_text()
    index.write_text(html.replace(SCREEN_TAG + "\n", "").replace(
        ENTRY_TAG, ENTRY_TAG + "\n  " + SCREEN_TAG))

    report = FeedbackRunner().check(fastapi_app)

    assert _codes(report) == [("static/index.html", "SAGE003")]
    [error] = report.errors
    assert "static/components/MainScreen.js" in error.message
    assert "above static/app.js" in error.message
    assert index.read_text().splitlines()[error.line - 1].strip() == SCREEN_TAG


def test_a_screen_with_no_script_tag_fails_sage002(fastapi_app: Path):
    (fastapi_app / "static" / "components" / "MainScreen.js").write_text(FASTAPI_SCREEN)
    index = _index(fastapi_app)
    index.write_text(index.read_text().replace(SCREEN_TAG + "\n", ""))

    report = FeedbackRunner().check(fastapi_app)

    assert _codes(report) == [("static/index.html", "SAGE002")]
    assert "static/components/MainScreen.js" in report.errors[0].message


# ---- an app built before this change ------------------------------------------------------------


def _legacy_fastapi(app: Path, entry: str) -> None:
    """The layout every fastapi-antd app born before #697 has: no screen file, no screen tag."""
    shutil.rmtree(app / "static" / "components")
    index = _index(app)
    index.write_text(index.read_text().replace(SCREEN_TAG + "\n", "").replace(
        ENTRY_TAG, ENTRY_TAG + '\n  <script src="static/late.js"></script>'))
    (app / "static" / "late.js").write_text(LATE_SELF_CONTAINED)
    (app / "static" / "app.js").write_text(entry)


def test_a_legacy_fastapi_app_passes_and_is_not_extracted(tmp_path: Path):
    mgr, app = _seed(tmp_path, "fastapi-antd")
    _legacy_fastapi(app, LEGACY_ENTRY)

    report = FeedbackRunner().check(app)
    mgr.ensure("proj")

    assert report.ok, report.as_agent_message()
    assert not (app / "static" / "components").exists()
    assert (app / "static" / "app.js").read_text() == LEGACY_ENTRY


def test_a_legacy_fastapi_starter_still_fails_sage001_on_its_entry(fastapi_app: Path):
    _legacy_fastapi(fastapi_app, LEGACY_STARTER)

    assert _codes(FeedbackRunner().check(fastapi_app)) == [("static/app.js", "SAGE001")]


def test_a_legacy_react_app_passes_and_is_not_extracted(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("sage.feedback.runner.subprocess.run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    mgr, app = _seed(tmp_path, "react-vite")
    shutil.rmtree(app / "src" / "screens")
    (app / "src" / "App.tsx").write_text(REACT_LEGACY_ENTRY)

    report = FeedbackRunner().check(app)
    mgr.ensure("proj")

    assert report.ok, report.as_agent_message()
    assert not (app / "src" / "screens").exists()


def test_a_legacy_react_starter_still_fails_sage001_on_its_entry(react_app: Path):
    shutil.rmtree(react_app / "src" / "screens")
    (react_app / "src" / "App.tsx").write_text(REACT_LEGACY_STARTER)

    assert _codes(FeedbackRunner().check(react_app)) == [("src/App.tsx", "SAGE001")]


# ---- a placeholder nothing loads is not the app's screen ----------------------------------------


@pytest.mark.parametrize("tag", ["kept", "removed"])
def test_a_fastapi_screen_the_shell_no_longer_mounts_is_not_read(fastapi_app: Path, tag: str):
    """A build that rewrote the whole UI into `static/app.js` left the seeded screen behind, and
    kept the shell's header comment, which names the screen without mounting it."""
    if tag == "removed":
        index = _index(fastapi_app)
        index.write_text(index.read_text().replace(SCREEN_TAG + "\n", ""))
    header = (REPO / "template/fastapi-antd/static/app.js").read_text().split("(function")[0]
    assert "MainScreen" in header
    (fastapi_app / "static" / "app.js").write_text(header + LEGACY_ENTRY)

    report = FeedbackRunner().check(fastapi_app)

    assert report.ok, report.as_agent_message()


def test_a_react_screen_the_shell_no_longer_imports_is_not_read(react_app: Path):
    (react_app / "src" / "App.tsx").write_text(REACT_LEGACY_ENTRY)

    report = FeedbackRunner().check(react_app)

    assert report.ok, report.as_agent_message()


# ---- the provider request -----------------------------------------------------------------------


@pytest.mark.parametrize("stack, old_rule", [
    ("fastapi-antd", "edit `static/app.js` (and any other"),
    ("react-vite", "edit `src/App.tsx` (and any"),
])
@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.RESPONSES])
def test_the_provider_request_carries_the_layout_rule_once(stack: str, old_rule: str, protocol):
    template = (REPO / "template" / stack / "AGENTS.md").read_text()
    original = native_body(protocol, opaque=False)
    if protocol is Protocol.MESSAGES:
        original["system"] = [{"type": "text", "text": template}]
    else:
        original["instructions"] = template
    enforcement, _ = native_shim(protocol, mode=Mode.IMPLEMENT, effort="high")

    result, *_ = prepare_native(enforcement, original, protocol, "p", "ses_implement",
                                rewrite_counts={})
    encoded = json.dumps(result, ensure_ascii=False)

    assert encoded.count(LAYOUT_RULE) == 1
    assert old_rule not in encoded


# ---- a follow-up to one screen ------------------------------------------------------------------

SHELL = 'import MainScreen from "./screens/MainScreen";\nexport default function App() { return <MainScreen />; }\n'
STARTER_SCREEN = "export default function MainScreen() { return null }\n"
SCREEN_PLAN = execution_plan("Dashboard", "A dashboard.", "Add a table",
                             files="src/screens/MainScreen.tsx", work="Add a table and chart.")


def _screen_first_build(tmp: Path, turns: list[Turn]):
    template = tmp / "template"
    (template / "src" / "screens").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text(SHELL)
    (template / "src" / "screens" / "MainScreen.tsx").write_text(STARTER_SCREEN)
    (template / "package.json").write_text("{}")
    oc = FakeOpenCode(tmp / "mnt" / "code", turns)
    orch = Orchestrator(workspace_dir=tmp / "mnt" / "code", template=template,
                        gateway=ScriptedGateway("BUILD"), catalog=_catalog(), project_id="Sage",
                        feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch


def test_a_follow_up_to_one_screen_completes_and_leaves_the_shell_alone(tmp_path: Path):
    already = "The severity filter is already in place at src/screens/MainScreen.tsx:1.\nALREADY_DONE"
    orch = _screen_first_build(tmp_path, [
        Turn(text=SCREEN_PLAN),
        Turn(text="Building it.", writes={"src/screens/MainScreen.tsx": "// v1\n"}),
        Turn(text="Added the filter.", writes={"src/screens/MainScreen.tsx": "// v2 severity\n"}),
        Turn(text=already),
    ])
    _get_built(orch)
    app = orch.project(start_preview=False).app_for_turn().path

    follow_up = _done(_run(orch, "add a severity filter"))
    repeat = _done(_run(orch, "add a severity filter"))

    assert (follow_up["ok"], follow_up["decision"]) == (True, "typecheck clean")
    assert (app / "src" / "App.tsx").read_text() == SHELL
    assert (app / "src" / "screens" / "MainScreen.tsx").read_text() == "// v2 severity\n"
    assert (repeat["ok"], repeat["decision"]) == (True, "already done")
