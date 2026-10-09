"""A fastapi-antd `useViewState` field that is an object the helper still throws on fails the check (#722).

Live (#714 dogfood, "Signal Room 17", haiku): a repair turn rewrote `static/components/UsageDrift.js`
with `bins: { type: 'int', ... }`. `static/sage/viewState.js` accepts string, integer, boolean and enum
and throws on the first render; `SAGE004` (#706) only rejected a field that was not an object, so the
turn ended "Syntax check passed". It now checks each field the way the helper does, and reads the
accepted types from the app's own helper rather than a copy that could drift from the code that throws.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from .test_a_view_state_field_without_a_type_fails_the_check import _app, _check, _screen

USAGE_DRIFT = _screen(
    "  const [view, patchView] = sage.useViewState({\n"
    "    bins: { type: 'int', default: 10, shareable: true },\n"
    "    trendRange: { type: 'int', default: 30, shareable: true },\n"
    "  });\n")


def test_an_int_field_fails_naming_the_field_and_the_accepted_types(tmp_path: Path, monkeypatch):
    report = _check(_app(tmp_path, USAGE_DRIFT), monkeypatch)

    assert [(e.file, e.line, e.code) for e in report.errors] == [
        ("static/components/MainScreen.js", 4, "SAGE004"), ("static/components/MainScreen.js", 5, "SAGE004")]
    bins, trend = (e.message for e in report.errors)
    assert "'bins'" in bins and "'trendRange'" in trend
    assert "'int'" in bins and "string, integer, boolean, enum" in bins


@pytest.mark.parametrize("field, named", [
    ("{ type: 'enum', default: 'a' }", "values"),
    ("{ type: 'string', default: '', shareable: true }", "pattern"),
    ("{ default: 10 }", "type"),
    ('{ type: "", default: 10 }', "type"),
], ids=["enum-without-values", "shareable-string-without-pattern", "no-type", "empty-type"])
def test_a_field_missing_what_its_type_needs_fails(tmp_path: Path, monkeypatch, field: str, named: str):
    screen = _screen(f"  const [view] = sage.useViewState({{ region: {field} }});\n")

    report = _check(_app(tmp_path, screen), monkeypatch)

    assert [e.code for e in report.errors] == ["SAGE004"]
    assert "'region'" in report.errors[0].message and named in report.errors[0].message


def test_the_reserved_key_fails(tmp_path: Path, monkeypatch):
    screen = _screen("  const [view] = sage.useViewState({ v: { type: 'integer', default: 1 } });\n")

    report = _check(_app(tmp_path, screen), monkeypatch)

    assert [e.code for e in report.errors] == ["SAGE004"]
    assert "'v'" in report.errors[0].message


def test_a_field_bound_to_a_const_in_this_file_is_read(tmp_path: Path, monkeypatch):
    screen = _screen("  const BINS = { type: 'int', default: 10 };\n"
                     "  const [view] = sage.useViewState({ bins: BINS });\n")

    report = _check(_app(tmp_path, screen), monkeypatch)

    assert [(e.code, "'bins'" in e.message) for e in report.errors] == [("SAGE004", True)]


@pytest.mark.parametrize("field", [
    "{ type: 'integer', default: 10 }",
    "{ type: 'integer', min: 1, max: 50, default: 10, shareable: true }",
    "{ type: 'boolean', default: false, shareable: true }",
    "{ type: 'enum', values: ['a', 'b'], default: 'a', shareable: true }",
    "{ type: 'string', default: '' }",
    "{ type: 'string', pattern: /\\d+/, default: '', shareable: true }",
    "{ type: 'string', pattern: MONTH, default: '', shareable: true }",
], ids=["integer", "integer-bounded", "boolean", "enum", "draft-string", "shareable-string", "named-pattern"])
def test_a_field_the_helper_accepts_passes(tmp_path: Path, monkeypatch, field: str):
    screen = _screen(f"  const [view] = sage.useViewState({{ bins: {field} }});\n")

    report = _check(_app(tmp_path, screen), monkeypatch)

    assert report.ok, report.as_agent_message()


@pytest.mark.parametrize("field", [
    "{ type: KIND, default: 10 }",
    "{ ...BASE, default: 10 }",
    "{ type: `int`.trim(), default: 10 }",
    "{ type, default: 10 }",
    "{ [KEY]: 'int', default: 10 }",
], ids=["type-from-a-name", "spread", "computed-type", "shorthand", "computed-key"])
def test_a_field_this_file_does_not_show_is_left_alone(tmp_path: Path, monkeypatch, field: str):
    screen = _screen(f"  const [view] = sage.useViewState({{ bins: {field} }});\n")

    report = _check(_app(tmp_path, screen), monkeypatch)

    assert report.ok, report.as_agent_message()


def test_the_accepted_types_are_the_apps_helpers(tmp_path: Path, monkeypatch):
    app = _app(tmp_path, _screen("  const [view] = sage.useViewState({\n"
                                 "    live: { type: 'boolean', default: false },\n"
                                 "    day: { type: 'date', default: '' },\n"
                                 "  });\n"))
    helper = app / "static" / "sage" / "viewState.js"
    shipped = "var TYPES = { string: true, integer: true, boolean: true, enum: true };"
    assert shipped in helper.read_text()
    helper.write_text(helper.read_text().replace(
        shipped, "var TYPES = { string: true, integer: true, enum: true, date: true };"))

    report = _check(app, monkeypatch)

    assert [(e.code, "'live'" in e.message) for e in report.errors] == [("SAGE004", True)]
    assert "string, integer, enum, date" in report.errors[0].message


@pytest.mark.parametrize("types", [
    'var TYPES = { "string": true, "integer": true, "boolean": true, "enum": true, "date": true };',
    'const TYPES = new Set(["string", "integer", "boolean", "enum", "date"]);',
], ids=["quoted-keys", "set"])
def test_the_helpers_list_is_read_however_it_is_written(tmp_path: Path, monkeypatch, types: str):
    app = _app(tmp_path, USAGE_DRIFT)
    helper = app / "static" / "sage" / "viewState.js"
    helper.write_text(helper.read_text().replace(
        "var TYPES = { string: true, integer: true, boolean: true, enum: true };", types))

    report = _check(app, monkeypatch)

    assert [e.code for e in report.errors] == ["SAGE004", "SAGE004"]
    assert "string, integer, boolean, enum, date" in report.errors[0].message


@pytest.mark.parametrize("helper", [None, "var TYPES = {};"], ids=["no-helper", "empty-list"])
def test_without_a_readable_helper_list_only_the_shape_is_checked(tmp_path: Path, monkeypatch, helper):
    app = _app(tmp_path, USAGE_DRIFT)
    path = app / "static" / "sage" / "viewState.js"
    if helper is None:
        path.unlink()
    else:
        path.write_text(helper)

    report = _check(app, monkeypatch)

    assert report.ok, report.as_agent_message()
