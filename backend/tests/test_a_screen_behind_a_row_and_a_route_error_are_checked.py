""""Build is clean" over a Deal page that crashed (#764). Two doors #709/#722 left open:

- The page check opened every tab and nav button, but skipped a disabled one, and the Deal tab is
  disabled until a deal row is chosen. A row click selects; it does not write. So when a screen
  waits on a selection, the walk chooses the first row of a table, once, and opens what it enabled.
- An app route that caught its own failure answered `200 {"error": ...}`, and the data check read
  any 2xx from a route as a pass.
"""
from __future__ import annotations

import pytest

from sage.preview.read_outcomes import read_request, read_result

from .test_a_section_with_no_data_shows_its_state_not_invented_content import _route_read
from .test_an_unwatched_build_checks_its_own_page import _UNAVAILABLE, served  # noqa: F401
from .test_changed_page_validation import build, run  # noqa: F401

# ---- an app route that answers its failure as a 200 ---------------------------------------------

CALL_TOOL_MISCALLED = b'{"error": "call_tool() got multiple values for argument \'url\'"}'


@pytest.mark.parametrize("body", [
    CALL_TOOL_MISCALLED,
    b'{"error": {"message": "refused"}}',
    b'{"items": [], "error": "the CRM did not answer"}',
])
def test_a_route_answering_2xx_with_an_error_is_a_failed_read(body):
    result = read_result(read_request("/api/approvals", kind="route"), 200, body)
    assert result["outcome"] == "failed"
    assert result["reason"] == "error_body"


@pytest.mark.parametrize("body", [
    b'{"reasons": ["over threshold"], "error": null}',
    b'{"reasons": [], "error": ""}',
    b'[{"error": "a row may name one"}]',
    b'{"rows": [{"error": "nor a nested field"}]}',
])
def test_a_route_whose_answer_carries_no_error_passes(body):
    assert read_result(read_request("/api/approvals", kind="route"), 200, body)["outcome"] == "passed"


def test_the_diagnostic_record_keeps_why_the_route_failed():
    from sage.build_diagnostics import _verification

    read = {"kind": "route", "path": "/api/approvals", "resourceIds": [], "status": 200,
            "outcome": "failed", "reason": "error_body"}
    kept = _verification({"overall": "failed", "stages": {"data": "failed"}, "dataReads": [read]})
    assert kept["dataReads"] == [read]


def test_a_route_answering_200_with_an_error_does_not_end_clean(build):  # noqa: F811
    orch, _project, _oc = build
    events, done = run(orch, report=_route_read(orch, 200, CALL_TOOL_MISCALLED, "/api/approvals"))

    assert done["ok"] is False, done
    assert done["decision"].endswith("app route failed"), done["decision"]
    assert done["verification"]["stages"]["data"] == "failed"
    assert done["verification"]["dataReads"][0]["reason"] == "error_body"
    message = next(e for e in events if e["type"] == "data-source-failed")["message"]
    assert "/api/approvals" in message and "error" in message


# ---- a screen reached only by choosing a row -----------------------------------------------------

def _rows(on_row: str, *, waits: bool = True) -> str:
    """A pipeline table and a Details tab disabled until a row is chosen, as antd renders a tab
    item with `disabled: true`. Each row's first cell is a Delete button that posts `/api/delete`;
    the row's own click runs `on_row`. With `waits=False` there is no Details tab at all."""
    details = ("<button role='tab' aria-selected='false' aria-disabled='true'>Details</button>"
               if waits else "")
    return ("const root = document.getElementById('root');"
            "root.innerHTML = \"<div role='tablist'><button role='tab' aria-selected='true'>Pipeline"
            f"</button>{details}</div><div id='pane'><table><thead><tr><th></th><th>Deal</th></tr>"
            "</thead><tbody><tr><td><button>Delete</button></td><td>Acme renewal</td></tr></tbody>"
            "</table></div>\";"
            "const tabs = root.querySelectorAll('[role=tab]');"
            "root.querySelector('tbody button').addEventListener('click', (e) => {"
            " e.stopPropagation(); fetch('/api/delete', { method: 'POST' }); });"
            f"root.querySelector('tbody tr').addEventListener('click', () => {{ {on_row} }});"
            "const openDetails = () => { tabs[0].setAttribute('aria-selected', 'false');"
            " tabs[1].setAttribute('aria-selected', 'true'); DETAILS(); };"
            "if (tabs[1]) tabs[1].addEventListener('click', () => {"
            " if (tabs[1].getAttribute('aria-disabled') !== 'true') openDetails(); });")


ENABLE = "tabs[1].removeAttribute('aria-disabled');"
CRASHES = ("const DETAILS = () => { const chain = {};"
           " document.getElementById('pane').textContent = chain.reasons.length; };")
HEALTHY = ("const DETAILS = () => { fetch('/api/detail', { method: 'POST' });"
           " document.getElementById('pane').textContent = 'Acme renewal: 2 approvals'; };")


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
@pytest.mark.parametrize("on_row", [ENABLE + " openDetails();", ENABLE],
                         ids=["the row opens it", "the row only enables it"])
def test_real_chromium_fails_a_screen_reached_from_a_row_that_crashes(served, on_row):  # noqa: F811
    orch, handler = served
    handler.screen = CRASHES + _rows(on_row)
    _, done = run(orch)
    assert done["verification"]["stages"]["page"] == "passed"
    assert done["verification"]["stages"]["runtime"] == "failed"
    assert done["ok"] is False


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
@pytest.mark.parametrize("on_row", [ENABLE + " openDetails();", ENABLE],
                         ids=["the row opens it", "the row only enables it"])
def test_real_chromium_opens_a_screen_from_a_row_once_and_presses_nothing_in_it(served, on_row):  # noqa: F811
    orch, handler = served
    handler.screen = HEALTHY + _rows(on_row)
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "passed"
    assert [p for p in handler.posts if not p.startswith("/api/preview/")] == ["/api/detail"]
    screens = orch.project(start_preview=False).page_validation.screens
    assert "Acme renewal: 2 approvals" in screens[-1]["texts"]


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
def test_real_chromium_chooses_no_row_when_no_screen_waits_on_one(served):  # noqa: F811
    orch, handler = served
    handler.screen = "const DETAILS = () => {};" + _rows("fetch('/api/row', { method: 'POST' });",
                                                         waits=False)
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "passed"
    assert [p for p in handler.posts if not p.startswith("/api/preview/")] == []
