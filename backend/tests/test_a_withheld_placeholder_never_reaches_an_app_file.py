"""The data-use placeholder must never reach an app file (#718).

Live on `779198d` (haiku Build, "Signal Room 17"): the model read `battlecards.md`, then wrote
component files that quoted words from it. `DataUse.prepare` rewrote those WRITES, in the model's
own history, to the placeholder; the model, no longer able to see its own work, rewrote the files by
copying what it was shown. `DealDetail.js` held `[local data withheld]` and `ProductInsights.js` the
#510 repair string, and the preview crashed with React error #130.

Two halves, and the second is the one that holds when the first is not enough:

    the model's own writes are its code    a write or edit is not data Sage read, so it is never
                                           replaced by a copyable placeholder in its history
    an app file carrying any placeholder   fails the end-of-turn check (`SAGE008`) and gets a
                                           repair turn. Keyed on what EVERY placeholder Sage
                                           substitutes carries, not on one rendering of it.

The build-loop case needs `node` on PATH, and skips without it — run with `-rs`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from sage.driver.opencode import with_attachment_listing
from sage.liveread.data_use import DataUse, carries_withheld_mark

from .fake_opencode import Turn
from .test_a_view_state_field_without_a_type_fails_the_check import INLINE_GOOD, ROOT, _app, _check, _screen
from .test_controlled_build_faults_on_each_stack import _ack, _run, selected_stack  # noqa: F401

CARDS = "public/data/upload/uploads/battlecards.md"
CARDS_TEXT = ("# Battlecards\n"
              "Northwind Analytics: wins on price, loses on governance reviews\n"
              "Contoso Insights: strong dashboards, weak lineage story\n")
# What the model wrote: its own code, naming a competitor the file it read also names.
DEAL_DETAIL = ("(function () {\n"
               "  window.app = window.app || {};\n"
               "  const RIVAL = 'Northwind Analytics';\n"
               "  window.app.DealDetail = function DealDetail() {\n"
               "    return React.createElement('h2', null, RIVAL);\n"
               "  };\n"
               "})();\n")


def _prompt():
    return with_attachment_listing("build the deal screens", [{
        "path": CARDS, "name": "battlecards.md", "summary": "Markdown - 3 lines", "detail": ""}],
        chat=False)


def _call(cid, name, args):
    return {"role": "assistant", "tool_calls": [{"id": cid, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


def _history(*turns):
    """The person's message, the read of the data-bearing file, then `turns`."""
    return {"messages": [
        {"role": "user", "content": _prompt()},
        _call("read1", "read", {"filePath": CARDS}),
        {"role": "tool", "tool_call_id": "read1", "content": CARDS_TEXT},
        *turns,
    ]}


def _sent_args(prepared, index):
    return json.loads(prepared["messages"][index]["tool_calls"][0]["function"]["arguments"])


@pytest.mark.parametrize("name, args", [
    ("write", {"filePath": "static/components/DealDetail.js", "content": DEAL_DETAIL}),
    ("edit", {"filePath": "static/components/DealDetail.js", "oldString": "const RIVAL = '';",
              "newString": "const RIVAL = 'Northwind Analytics';"}),
], ids=["live-whole-file-write", "edit"])
def test_the_models_own_write_stays_its_code_in_its_history(name, args):
    data = DataUse()

    prepared, _used = data.prepare(_history(
        _call("w1", name, args),
        {"role": "tool", "tool_call_id": "w1", "content": "Wrote file successfully."},
    ))

    assert _sent_args(prepared, 3) == args, "the model sees the code it wrote, not a placeholder"
    assert not carries_withheld_mark(json.dumps(prepared["messages"][3:]))
    assert "Northwind" not in json.dumps(prepared["messages"][2]), "the read itself is still withheld"


def test_a_command_that_quotes_the_data_is_still_withheld():
    """The exemption is the write class, not every call: a shell command is not app code."""
    data = DataUse()
    command = "echo 'Northwind Analytics' > static/rival.txt"

    prepared, _used = data.prepare(_history(
        _call("b1", "bash", {"command": command, "description": "look"}),
        {"role": "tool", "tool_call_id": "b1", "content": "ok"},
    ))

    assert "Northwind" not in json.dumps(prepared["messages"][3])


def _repair_string_the_model_is_shown():
    """The #510 repair of an echoed write — read off the real path, never written here."""
    prepared, _used = DataUse().prepare({"messages": [
        _call("e1", "write", {"filePath": "static/components/ProductInsights.js",
                              "content": "[local data withheld]"}),
        {"role": "tool", "tool_call_id": "e1", "content": "Wrote file successfully."},
    ]})
    return _sent_args(prepared, 0)["content"]


def test_a_copied_repair_string_is_answered_as_an_echo():
    """`ProductInsights.js`: the repair string was copied and treated as genuine content."""
    repair = _repair_string_the_model_is_shown()
    assert "local data withheld" not in repair, "the repair still cannot teach the marker"

    prepared, _used = DataUse().prepare({"messages": [
        _call("e2", "write", {"filePath": "static/components/ProductInsights.js", "content": repair}),
        {"role": "tool", "tool_call_id": "e2", "content": "Wrote file successfully."},
    ]})

    assert "not a command" in prepared["messages"][-1]["content"]


def _placeholder_errors(report):
    return [(e.file, e.line) for e in report.errors if e.code == "SAGE008"]


def test_the_live_files_fail_the_check_without_repeating_the_placeholder(tmp_path: Path, monkeypatch):
    app = _app(tmp_path, INLINE_GOOD)
    components = app / "static" / "components"
    (components / "DealDetail.js").write_text("[local data withheld]")
    (components / "ProductInsights.js").write_text(_repair_string_the_model_is_shown())

    report = _check(app, monkeypatch)

    assert not report.ok
    assert _placeholder_errors(report) == [("static/components/DealDetail.js", 1),
                                           ("static/components/ProductInsights.js", 1)]
    message = report.as_agent_message()
    assert "placeholder" in message and "Read the file" in message
    assert not carries_withheld_mark(message), "the repair turn must not hand the marker back"


@pytest.mark.parametrize("line", [
    "  const rows = [local data withheld: 2 sources];\n",
    "  // # <local data withheld: public/data/x.csv — a placeholder, not a command>\n",
    "  const note = '[copied withheld placeholder removed after prior execution]';\n",
], ids=["value-form", "command-form", "repair-form"])
def test_a_placeholder_an_edit_put_inside_a_file_fails_at_its_line(tmp_path: Path, monkeypatch, line: str):
    app = _app(tmp_path, _screen(line))

    report = _check(app, monkeypatch)

    assert _placeholder_errors(report) == [("static/components/MainScreen.js", 3)]


def test_react_vite_source_is_read_too(tmp_path: Path, monkeypatch):
    app = tmp_path / "app"
    (app / ".sage").mkdir(parents=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": "react-vite"}))
    (app / "src" / "screens").mkdir(parents=True)
    (app / "src" / "App.tsx").write_text("export default function App() { return <main />; }\n")
    (app / "src" / "screens" / "Deals.tsx").write_text("export const x = 1;\n[local data withheld]\n")

    report = _check(app, monkeypatch)

    assert _placeholder_errors(report) == [("src/screens/Deals.tsx", 2)]


def test_what_is_not_the_apps_own_source_is_not_read(tmp_path: Path, monkeypatch):
    """An uploaded file, a vendored bundle and a Sage-owned helper are not the model's writes: a
    governance CSV that says "local data withheld" is data, and no repair turn can change it."""
    app = _app(tmp_path, INLINE_GOOD)
    for rel in ("public/data/upload/uploads/governance.csv", "static/vendor/lib.js",
                "static/sage/keys.js", ".sage/notes.md"):
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_text("policy,local data withheld\n")

    report = _check(app, monkeypatch)

    assert _placeholder_errors(report) == []


@pytest.mark.parametrize("stack", ["fastapi-antd", "react-vite"])
def test_the_templates_carry_no_placeholder(tmp_path: Path, monkeypatch, stack: str):
    app = tmp_path / "app"
    shutil.copytree(ROOT / "template" / stack, app, ignore=shutil.ignore_patterns("node_modules"))
    (app / ".sage").mkdir(exist_ok=True)
    (app / ".sage" / "settings.json").write_text(json.dumps({"stack": stack}))

    assert _placeholder_errors(_check(app, monkeypatch)) == []


@pytest.mark.parametrize("selected_stack", ["fastapi-antd"], indirect=True)
def test_a_turn_that_writes_the_placeholder_is_sent_back_once(selected_stack):  # noqa: F811
    app = selected_stack
    screen = "static/components/MainScreen.js"
    app.oc.turns.extend([Turn(writes={screen: "[local data withheld]"}),
                         Turn(text="Fixed it", writes={screen: INLINE_GOOD})])

    events, done = _run(app, _ack(app))

    assert [e["ok"] for e in events if e["type"] == "typecheck"] == [False, True]
    assert "SAGE008" in app.oc.prompts[-1]["text"]
    assert len(app.oc.prompts) == 2
    assert done["ok"] is True
