"""A loaded document does not prove that its navigation was checked."""
import sys
from dataclasses import replace

import pytest

from sage.preview import page_check

from .test_an_unwatched_build_checks_its_own_page import (
    _UNAVAILABLE,
    FakeBrowser,
    _settle,
    served,  # noqa: F401
)
from .test_changed_page_validation import build, run  # noqa: F401


def test_a_browser_that_exits_after_ack_is_an_unfinished_check(build, monkeypatch):  # noqa: F811
    orch, _project, _ = build
    monkeypatch.setattr(page_check, "unavailable", lambda: None)
    monkeypatch.setattr(page_check, "_command", lambda url, timeout: [sys.executable, "-c", "pass"])

    def start(url, timeout):
        orch.record_preview_ack(orch.project(start_preview=False).page_validation.id)
        check = page_check.start(url, timeout)
        check.process.wait(timeout=5)
        return check

    orch._page_check = start
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "unverified"
    assert "page check" in done["verification"]["reason"]
    assert "did not finish" in done["verification"]["reason"]


def test_a_walk_that_runs_out_of_time_is_unverified(build):  # noqa: F811
    orch, _, _ = build
    orch._build_policy = replace(orch._build_policy, page_check_wait_seconds=0.5)
    orch._page_check = FakeBrowser(orch.record_preview_ack, walk=lambda _: _settle(1000))
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "unverified"
    assert "page check" in done["verification"]["reason"]


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
def test_replacing_navigation_does_not_hide_the_next_screen(served):  # noqa: F811
    orch, handler = served
    handler.screen = """
      const root = document.getElementById('root');
      function draw(active) {
        root.innerHTML = '<nav>' + ['Overview', 'Insights', 'Details'].map(label =>
          '<button' + (label === active ? ' aria-current="page"' : '') + '>' + label +
          '</button>').join('') + '</nav><main>' + active + '</main>';
        for (const button of root.querySelectorAll('nav button')) {
          button.onclick = () => {
            if (button.textContent === 'Details') throw new Error('Details crashed');
            draw(button.textContent);
          };
        }
      }
      draw('Overview');
    """
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "failed"
    assert done["ok"] is False


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
@pytest.mark.parametrize("base_tag", [False, True], ids=["document-url", "app-base"])
def test_same_page_navigation_links_are_checked(served, base_tag):  # noqa: F811
    orch, handler = served
    handler.screen = """
      document.getElementById('root').innerHTML =
        '<nav><a href="#overview" aria-current="page">Overview</a>' +
        '<a href="#details">Details</a><a href="https://example.com">External</a></nav>';
      document.querySelector('a[href="#details"]').onclick = () => {
        throw new Error('Linked detail crashed');
      };
    """
    if base_tag:
        handler.screen = ("const base = document.createElement('base');"
                          "base.href = location.pathname; document.head.append(base);"
                          + handler.screen)
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "failed"
    assert done["ok"] is False
