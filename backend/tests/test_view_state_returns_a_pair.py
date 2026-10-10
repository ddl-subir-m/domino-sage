"""The hook's pair is not an object with .state / .patch members (Signal Room 26)."""
import pytest

from .test_a_view_state_field_without_a_type_fails_the_check import _app, _check, _screen


@pytest.mark.parametrize("code", [
    "const view = sage.useViewState({}); if (!view.state) return 'Loading...';",
    "const { state, patch } = sage.useViewState({});",
    "const view = sage.useViewState({}); view.patch({ screen: 'details' });",
], ids=["loading-forever", "object-destructuring", "missing-patch"])
def test_reading_the_view_state_pair_as_an_object_fails(tmp_path, monkeypatch, code):
    report = _check(_app(tmp_path, _screen(code)), monkeypatch)
    assert not report.ok
    assert any("const [view, patchView] = sage.useViewState" in error.message
               for error in report.errors)


@pytest.mark.parametrize("code", [
    "const [view, patchView] = sage.useViewState({});",
    "const pair = sage.useViewState({}); const view = pair[0]; const patchView = pair[1];",
    "const pair = sage.useViewState({}); const [view, patchView] = pair;",
    "// const { state } = sage.useViewState({});\n const view = { state: {} };",
    "const pair = sage.useViewState({}); const view = { state: pair[0] };",
])
def test_a_pair_or_unrelated_state_property_is_allowed(tmp_path, monkeypatch, code):
    report = _check(_app(tmp_path, _screen(code)), monkeypatch)
    assert report.ok, report.as_agent_message()
