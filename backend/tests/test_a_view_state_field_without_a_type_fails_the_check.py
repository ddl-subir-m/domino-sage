"""A fastapi-antd `useViewState` field given a bare default fails the check (#706).

Live (#702 rerun, `sage-signal-room`, haiku): `static/components/MainScreen.js` called
`sage.useViewState({ event_name: '__all__' })`. `static/sage/viewState.js` wants every field to be
an object naming its type and throws on the first render, so the preview showed "The app crashed
while rendering". Every script parsed, so the turn ended `Syntax check clean`. The plain-script
stack has no types to catch it; `SAGE004` reads the call instead.

What it reads: the schema written inline, or a `const` object in the same file passed by name. A
schema from anywhere else is not this check's to guess, nor is Sage's own `static/sage/`.

Also here: react-vite needs no such check, because `tsc` already rejects the call through
`appViewState.ts`'s types. That test needs `template/react-vite/node_modules`, which exists only in
the repo root, so it skips in a worktree — run with `-rs`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage.feedback.runner import FeedbackRunner

from .fake_opencode import Turn
from .test_controlled_build_faults_on_each_stack import _ack, _run, selected_stack  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
REACT_TEMPLATE = ROOT / "template" / "react-vite"

INDEX = """\
<!DOCTYPE html>
<html><body>
  <div id="root"></div>
  <script src="static/sage/viewState.js"></script>
  <script src="static/components/MainScreen.js"></script>
  <script src="static/app.js"></script>
</body></html>
"""
GOOD_FIELDS = """\
    screen: { type: "enum", values: ["orders", "customers"], default: "orders", shareable: true },
    month: { type: "string", pattern: /\\d{4}-(0[1-9]|1[0-2])/, default: "", shareable: true },
    search: { type: "string", default: "" },  // draft text: never in the URL
"""


def _screen(body: str) -> str:
    return ("(function () {\n"
            "  window.app = window.app || {};\n"
            f"{body}"
            "  window.app.MainScreen = function MainScreen() { return React.createElement('main'); };\n"
            "})();\n")


INLINE_BAD = _screen("  const view = sage.useViewState({\n    event_name: '__all__'\n  });\n")
INLINE_GOOD = _screen(f"  const [view, patchView] = sage.useViewState({{\n{GOOD_FIELDS}  }});\n")
NAMED_GOOD = _screen(f"  const VIEW = {{\n{GOOD_FIELDS}  }};\n"
                     "  const [view, patchView] = sage.useViewState(VIEW);\n")


def _app(tmp_path: Path, screen: str) -> Path:
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "settings.json").write_text('{"stack": "fastapi-antd"}')
    (tmp_path / "static" / "components").mkdir(parents=True)
    (tmp_path / "static" / "sage").mkdir()
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "static" / "index.html").write_text(INDEX)
    (tmp_path / "static" / "sage" / "viewState.js").write_text(
        (ROOT / "template/fastapi-antd/static/sage/viewState.js").read_text())
    (tmp_path / "static" / "components" / "MainScreen.js").write_text(screen)
    (tmp_path / "static" / "app.js").write_text(
        "ReactDOM.createRoot(document.getElementById('root'))"
        ".render(React.createElement(window.app.MainScreen));\n")
    return tmp_path


def _check(app: Path, monkeypatch):
    # Only node and oxlint are simulated (clean): this check reads the source, not their output.
    monkeypatch.setattr("sage.feedback.runner.subprocess.run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    return FeedbackRunner().check(app)


def test_a_bare_default_fails_naming_the_field_and_the_shape(tmp_path: Path, monkeypatch):
    report = _check(_app(tmp_path, INLINE_BAD), monkeypatch)

    assert not report.ok
    assert [(e.file, e.line, e.code) for e in report.errors] == [
        ("static/components/MainScreen.js", 4, "SAGE004")]
    message = report.errors[0].message
    assert "'event_name'" in message and "'__all__'" in message
    assert 'type: "enum"' in message and "default:" in message


@pytest.mark.parametrize("value", ["5", "-1", "true", "null", "`x`", '["a", "b"]', "/a+/", "ALL"])
def test_any_other_non_object_field_fails(tmp_path: Path, monkeypatch, value: str):
    screen = _screen("  const ALL = '__all__';\n"
                     f"  const view = sage.useViewState({{ region: {value} }});\n")

    report = _check(_app(tmp_path, screen), monkeypatch)

    assert [(e.code, "'region'" in e.message) for e in report.errors] == [("SAGE004", True)]


def test_a_named_const_schema_with_a_bare_default_fails(tmp_path: Path, monkeypatch):
    screen = _screen("  const VIEW = { screen: { type: 'enum', values: ['a'], default: 'a' },\n"
                     "    region: '__all__' };\n"
                     "  const [view] = sage.useViewState(VIEW);\n")

    report = _check(_app(tmp_path, screen), monkeypatch)

    assert [e.code for e in report.errors] == ["SAGE004"]
    assert "'region'" in report.errors[0].message and "'screen'" not in report.errors[0].message


@pytest.mark.parametrize("screen", [INLINE_GOOD, NAMED_GOOD], ids=["inline", "named-const"])
def test_a_typed_schema_passes(tmp_path: Path, monkeypatch, screen: str):
    report = _check(_app(tmp_path, screen), monkeypatch)

    assert report.ok, report.as_agent_message()


@pytest.mark.parametrize("body", [
    "  const [view] = sage.useViewState(window.app.VIEW);\n",
    "  const [view] = sage.useViewState(VIEW);\n",
    ("  const REGION = { type: 'string', default: '' };\n"
     "  const [view] = sage.useViewState({ region: REGION, screen: window.app.SCREEN });\n"),
    "  const view = React.useState({ event_name: '__all__' });\n",
    "  // sage.useViewState({ event_name: '__all__' }) was the old call\n",
    "  const note = \"sage.useViewState({ event_name: '__all__' })\";\n",
], ids=["member-expression", "undefined-here", "field-consts", "no-hook", "comment", "string"])
def test_what_this_file_does_not_show_is_left_alone(tmp_path: Path, monkeypatch, body: str):
    report = _check(_app(tmp_path, _screen(body)), monkeypatch)

    assert report.ok, report.as_agent_message()


def test_sages_own_scripts_are_not_read(tmp_path: Path, monkeypatch):
    app = _app(tmp_path, INLINE_GOOD)
    (app / "static" / "sage" / "example.js").write_text("sage.useViewState({ event_name: '__all__' });\n")

    report = _check(app, monkeypatch)

    assert report.ok, report.as_agent_message()


@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_a_turn_that_writes_the_bad_call_is_sent_back_once(selected_stack):  # noqa: F811
    app = selected_stack
    screen = "static/components/MainScreen.js"
    app.oc.turns.extend([Turn(writes={screen: INLINE_BAD}),
                         Turn(text="Typed the view state", writes={screen: INLINE_GOOD})])

    events, done = _run(app, _ack(app))

    assert [e["ok"] for e in events if e["type"] == "typecheck"] == [False, True]
    assert "SAGE004" in app.oc.prompts[-1]["text"] and "event_name" in app.oc.prompts[-1]["text"]
    assert len(app.oc.prompts) == 2
    assert done["ok"] is True and done["verification"]["stages"]["code"] == "passed"


@pytest.mark.skipif(
    not ((REACT_TEMPLATE / "node_modules/typescript/bin/tsc").is_file() and shutil.which("node")),
    reason="real tsc needs template/react-vite/node_modules (repo root only) and node on PATH")
@pytest.mark.parametrize("schema, ok", [
    ("{ event_name: '__all__' }", False),
    ('{ screen: { type: "enum", values: ["a", "b"], default: "a", shareable: true } }', True),
], ids=["bare-default", "typed"])
def test_react_vite_tsc_already_rejects_a_bare_default(tmp_path: Path, schema: str, ok: bool):
    app = tmp_path / "app"
    (app / ".sage").mkdir(parents=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": "react-vite"}))
    for name in ("package.json", "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json"):
        shutil.copy(REACT_TEMPLATE / name, app / name)
    (app / "node_modules").symlink_to(REACT_TEMPLATE / "node_modules")
    (app / "src").mkdir()
    shutil.copy(REACT_TEMPLATE / "src" / "appViewState.ts", app / "src" / "appViewState.ts")
    (app / "src" / "vite-env.d.ts").write_text('/// <reference types="vite/client" />\n')
    (app / "src" / "App.tsx").write_text(
        'import { useViewState } from "./appViewState";\n\n'
        "export default function App() {\n"
        f"  const [view] = useViewState({schema});\n"
        "  return <main>{JSON.stringify(view)}</main>;\n"
        "}\n")

    report = FeedbackRunner().check(app)

    assert report.ok is ok, report.as_agent_message()
    if not ok:
        assert any(e.file == "src/App.tsx" and e.code.startswith("TS") for e in report.errors)
