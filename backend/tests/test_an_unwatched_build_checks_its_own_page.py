"""A build nobody is watching still gets its page and runtime checks (#707).

The page check used to be a property of the person's browser tab: only a visible Workbench in Build
mode loaded `preview/<app>/?sageValidation=<id>`, so an API-driven or backgrounded build ended
`typecheck clean` even when its screen threw on render. Sage now loads that page itself in headless
Chromium. The page's own `reportRuntimeError` still posts the ack and the crash; nothing here judges
the page.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from sage.build_policy import BuildPolicy
from sage.preview import page_check
from sage.router.models import Mode

from .fake_opencode import Turn
from .test_an_approved_plan_runs_as_implement import _build
from .test_changed_page_validation import Preview, build, run  # noqa: F401


class FakeCheck:
    def __init__(self, walk=()):
        self.closed, self.polls, self._walk = 0, 0, iter(walk)

    def walked(self):
        """One poll while the script opens tabs: runs the next step of `walk`, done when it runs out."""
        self.polls += 1
        step = next(self._walk, None)
        if step is None:
            return True
        step()
        return False

    def close(self):
        self.closed += 1


class FakeBrowser:
    """Stands in for headless Chromium: `page(validation_id)` is what the loaded document does, and
    `walk(validation_id)` what each poll sees while its tabs are opened."""

    def __init__(self, page=lambda validation_id: None, walk=lambda validation_id: ()):
        self.page, self.walk, self.urls, self.checks = page, walk, [], []

    def __call__(self, url: str, timeout: float):
        self.urls.append(url)
        validation_id = parse_qs(urlsplit(url).query)["sageValidation"][0]
        self.checks.append(FakeCheck(self.walk(validation_id)))
        self.page(validation_id)
        return self.checks[-1]


def _settle(polls: int):
    return [lambda: None] * polls


def test_the_check_loads_the_changed_page_through_sages_local_address(build, monkeypatch):  # noqa: F811
    orch, project, _ = build
    monkeypatch.setenv("SAGE_CONTROL_PORT", "4321")
    monkeypatch.setattr(page_check, "domino_base_prefix", lambda: "/u/p/notebookSession/r1")
    browser = FakeBrowser()
    orch._page_check = browser
    events, _ = run(orch)
    [event] = [e for e in events if e["type"] == "preview-validation"]
    assert browser.urls == [(f"http://127.0.0.1:4321/u/p/notebookSession/r1/preview/"
                             f"{project.workspace.app_id}/?sageValidation={event['validationId']}")]


def test_an_unwatched_page_that_loads_passes(build):  # noqa: F811
    orch, _, _ = build
    orch._page_check = FakeBrowser(orch.record_preview_ack)
    _, done = run(orch)
    assert done["verification"]["stages"]["page"] == "passed"
    assert done["verification"]["overall"] == "passed"


def test_an_unwatched_page_that_crashes_on_render_is_sent_back_for_repair(build):  # noqa: F811
    orch, project, oc = build
    oc.turns.append(Turn(writes={"src/App.tsx": "// repaired\n"}))
    seen = []

    def page(validation_id):
        seen.append(validation_id)
        orch.record_preview_ack(validation_id)
        if len(seen) == 1:
            orch.record_runtime_error("render crashed", validation_id=validation_id)

    orch._page_check = FakeBrowser(page)
    _, done = run(orch)
    assert len(seen) == 2
    assert (project.workspace.path / "src/App.tsx").read_text() == "// repaired\n"
    assert done["verification"]["overall"] == "passed"


def test_a_crash_at_the_repair_cap_ends_runtime_failed(build):  # noqa: F811
    orch, _, _ = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)

    def page(validation_id):
        orch.record_preview_ack(validation_id)
        orch.record_runtime_error("render crashed", validation_id=validation_id)

    orch._page_check = FakeBrowser(page)
    _, done = run(orch)
    assert done["ok"] is False
    assert done["verification"]["stages"]["runtime"] == "failed"


def test_a_crash_behind_the_second_tab_is_sent_back_for_repair(build):  # noqa: F811
    orch, project, oc = build
    oc.turns.append(Turn(writes={"src/App.tsx": "// repaired\n"}))
    walks = []

    def tabs(validation_id):
        walks.append(validation_id)
        crash = lambda: orch.record_runtime_error(
            "Cannot read properties of undefined (reading 'join')", validation_id=validation_id)
        # Each poll is 0.1s, so the crash lands well after the 0.2s runtime wait would have ended.
        return [*_settle(5), crash, *_settle(5)] if len(walks) == 1 else _settle(5)

    orch._page_check = FakeBrowser(orch.record_preview_ack, walk=tabs)
    _, done = run(orch)
    assert len(walks) == 2
    assert (project.workspace.path / "src/App.tsx").read_text() == "// repaired\n"
    assert done["verification"]["overall"] == "passed"


def test_a_crash_behind_a_tab_at_the_repair_cap_ends_runtime_failed(build):  # noqa: F811
    orch, _, _ = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)

    def tabs(validation_id):
        return [*_settle(5), lambda: orch.record_runtime_error(
            "query sales has no column 'Account'", validation_id=validation_id)]

    orch._page_check = FakeBrowser(orch.record_preview_ack, walk=tabs)
    _, done = run(orch)
    assert done["ok"] is False
    assert done["verification"]["stages"]["runtime"] == "failed"


def test_a_tab_walk_that_never_finishes_ends_at_the_check_budget(build):  # noqa: F811
    orch, _, _ = build
    orch._build_policy = replace(orch._build_policy, page_check_wait_seconds=3.0)

    def forever(_validation_id):
        yield from _settle(1000)
        raise AssertionError("Sage kept waiting on the tab walk past the check's budget")

    browser = FakeBrowser(orch.record_preview_ack, walk=forever)
    orch._page_check = browser
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "passed"
    [check] = browser.checks
    assert check.closed == 1
    assert check.polls <= 31


@pytest.mark.parametrize("ending", ["acked", "stopped", "never_loaded", "crashed_on_a_tab",
                                    "stopped_on_a_tab", "walk_never_ends"])
def test_the_check_is_closed_however_the_validation_ends(build, ending):  # noqa: F811
    orch, project, _ = build
    orch._build_policy = replace(orch._build_policy, runtime_repair_limit=0)
    page = {"stopped": lambda _id: setattr(project, "stop_requested", True),
            "never_loaded": lambda _id: None}.get(ending, orch.record_preview_ack)
    walk = {"crashed_on_a_tab": lambda vid: [lambda: orch.record_runtime_error("boom", validation_id=vid)],
            "stopped_on_a_tab": lambda _id: [lambda: setattr(project, "stop_requested", True)],
            "walk_never_ends": lambda _id: _settle(1000)}.get(ending, lambda _id: ())
    browser = FakeBrowser(page, walk=walk)
    orch._page_check = browser
    run(orch)
    assert [check.closed for check in browser.checks] == [1]


def test_a_running_check_gets_its_own_wait_budget(build):  # noqa: F811
    orch, _, _ = build
    orch._build_policy = replace(orch._build_policy, page_check_wait_seconds=3.0)
    orch._page_check = FakeBrowser()
    _, done = run(orch)
    assert done["verification"]["reason"] == "the preview didn't load the changed page within 3s"


def test_with_no_headless_browser_the_check_is_as_before_and_says_why(build, monkeypatch):  # noqa: F811
    orch, _, _ = build
    monkeypatch.setenv("SAGE_PAGE_CHECK_CHROMIUM", "/nowhere/chrome-headless-shell")
    monkeypatch.setattr(page_check, "_has_playwright_core", lambda: True)
    monkeypatch.setattr(page_check, "_node", lambda: "/usr/bin/node")
    orch._page_check = page_check.start
    _, done = run(orch)
    assert done["verification"]["stages"]["page"] == "unverified"
    assert done["verification"]["reason"] == (
        "the preview didn't load the changed page within 0.5s; "
        "no headless browser in this environment (SAGE_PAGE_CHECK_CHROMIUM is not a file)")


def test_a_workbench_ack_still_passes_while_the_check_runs(build):  # noqa: F811
    orch, _, _ = build
    orch._page_check = FakeBrowser()
    _, done = run(orch, report=lambda event: orch.record_preview_ack(event["validationId"]))
    assert done["verification"]["overall"] == "passed"


def test_the_service_wires_the_real_check():
    from sage.orchestrator import app
    assert app.orchestrator._page_check is page_check.start


# --- The process: killed and reaped on every path ------------------------------------------------

def _sleeper(tmp_path: Path, says: str = ""):
    """A stand-in for node + Chromium: a parent that starts a child, both of which would outlive us.
    It prints `says` once its child is up, as the script prints `done` after its tab walk."""
    pid_file = tmp_path / "child.pid"
    code = ("import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
            f"print({says!r}, flush=True); "
            f"open({str(pid_file)!r}, 'w').write(str(c.pid)); time.sleep(600)")
    return [sys.executable, "-c", code], pid_file


def _gone(pid: int) -> bool:
    import os
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _started(monkeypatch, tmp_path, then=lambda: None, says=""):
    argv, pid_file = _sleeper(tmp_path, says)
    monkeypatch.setattr(page_check, "_command", lambda url, timeout: argv)
    monkeypatch.setattr(page_check, "unavailable", lambda: None)
    checks = []

    def start(url, timeout):
        checks.append(page_check.start(url, timeout))
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        then()
        return checks[-1]

    return start, checks, pid_file


@pytest.mark.parametrize("ending", ["timeout", "stopped", "walk_never_ends"])
def test_the_browser_process_group_is_killed_and_reaped(build, monkeypatch, tmp_path, ending):  # noqa: F811
    orch, project, _ = build
    then = {"stopped": lambda: setattr(project, "stop_requested", True),
            "walk_never_ends": lambda: orch.record_preview_ack(project.page_validation.id)}
    start, checks, pid_file = _started(monkeypatch, tmp_path, then=then.get(ending, lambda: None))
    orch._page_check = start
    _, done = run(orch)
    assert done["verification"]["stages"]["page"] == ("passed" if ending == "walk_never_ends"
                                                       else "unverified")
    [check] = checks
    assert check.process.returncode is not None
    assert _gone(int(pid_file.read_text()))


def test_closing_twice_is_harmless(monkeypatch, tmp_path):
    start, _, _ = _started(monkeypatch, tmp_path)
    check = start("http://127.0.0.1:1/", 5)
    check.close()
    check.close()
    assert check.process.returncode is not None


@pytest.mark.parametrize("says", ["done", ""])
def test_the_check_says_when_its_tab_walk_is_done(monkeypatch, tmp_path, says):
    start, _, _ = _started(monkeypatch, tmp_path, says=says)
    check = start("http://127.0.0.1:1/", 5)
    try:
        assert check.walked() is (says == "done")
    finally:
        check.close()
    assert check.process.returncode is not None


def test_a_check_that_exits_early_is_not_waited_on(monkeypatch, tmp_path):
    monkeypatch.setattr(page_check, "_command", lambda url, timeout: [sys.executable, "-c", "pass"])
    monkeypatch.setattr(page_check, "unavailable", lambda: None)
    check = page_check.start("http://127.0.0.1:1/", 5)
    try:
        check.process.wait(10)
        assert check.walked() is True
    finally:
        check.close()


def test_the_screen_walk_fits_inside_the_check_budget():
    script = page_check.SCRIPT.read_text()
    screens, click, settle = (int(re.search(rf"const {name} = (\d+)", script).group(1))
                              for name in ("MAX_SCREENS", "CLICK_MS", "SETTLE_MS"))
    # The rest of the budget is for launching Chromium and loading a cold preview.
    assert screens * (click + settle) / 1000 <= BuildPolicy().page_check_wait_seconds - 10
    # Every control the walk opens counts against MAX_SCREENS: one loop, one counter.
    assert script.count("opened += 1") == 1 and "if (opened === MAX_SCREENS) break;" in script


# --- Real Chromium ------------------------------------------------------------------------------

_UNAVAILABLE = page_check.unavailable()

HEALTHY = "document.getElementById('root').textContent = 'ok';"
THROWS = "throw new Error('render crashed: useViewState is not defined');"


def _tabbed(second_pane: str) -> str:
    """Two tabs whose second pane renders only when it is opened, as antd mounts a lazy pane."""
    return ("const root = document.getElementById('root');"
            "root.innerHTML = \"<div role='tablist'><button role='tab' aria-selected='true'>Overview"
            "</button><button role='tab' aria-selected='false'>Insights</button></div>"
            "<div id='pane'>overview</div>\";"
            "const [first, second] = root.querySelectorAll('[role=tab]');"
            "second.addEventListener('click', () => {"
            " first.setAttribute('aria-selected', 'false'); second.setAttribute('aria-selected', 'true');"
            f" {second_pane} }});")


HEALTHY_TABS = _tabbed("document.getElementById('pane').textContent = 'insights';")
CRASHES_ON_SECOND_TAB = _tabbed(
    "const state = {}; document.getElementById('pane').textContent = state.metrics.join(',');")


def _navved(second_screen: str) -> str:
    """Screens switched by plain `<button>`s in a `<nav>` (#722), with a "Write brief" button before
    the nav and another after it that each post `/api/brief`, as a button that calls a model would.
    The second screen posts `/api/screen` when it renders."""
    return ("const root = document.getElementById('root');"
            "root.innerHTML = \"<header><button>Write brief</button><nav><button aria-current='page'>"
            "Overview</button><button>Usage Drift</button></nav></header><main id='screen'>overview"
            "</main><button>Write brief</button>\";"
            "for (const b of root.querySelectorAll('button:not(nav button)')) b.addEventListener("
            "'click', () => fetch('/api/brief', { method: 'POST' }));"
            "root.querySelectorAll('nav button')[1].addEventListener('click', () => {"
            f" {second_screen} }});")


HEALTHY_NAV = _navved("fetch('/api/screen', { method: 'POST' });"
                      " document.getElementById('screen').textContent = 'usage drift';")
CRASHES_ON_SECOND_NAV_SCREEN = _navved(
    "const state = {}; document.getElementById('screen').textContent = state.bins.map(String);")
REPORTER = (Path(__file__).resolve().parents[2] / "template" / "fastapi-antd" / "static" / "sage"
            / "reportRuntimeError.js")


class _Preview(BaseHTTPRequestHandler):
    orch = None
    app_id = ""
    screen = ""

    def log_message(self, *args):
        pass

    def do_GET(self):
        if not self.path.startswith(f"/preview/{self.app_id}/"):
            self.send_response(404)
            self.end_headers()
            return
        body = (f"<!doctype html><html><body><div id='root'></div>"
                f"<script>window.sage = {{base: '/preview/{self.app_id}', preview: true}};</script>"
                f"<script>{REPORTER.read_text()}</script><script>{self.screen}</script>"
                f"</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"] or 0)) or b"{}")
        self.posts.append(self.path)
        if self.path == "/api/preview/ack":
            self.orch.record_preview_ack(body.get("validationId", ""))
        elif self.path == "/api/preview/runtime-error":
            self.orch.record_runtime_error(body.get("message", ""), body.get("stack", ""),
                                           validation_id=body.get("validationId", ""))
        self.send_response(204)
        self.end_headers()


@pytest.fixture
def served(tmp_path, monkeypatch):
    orch, _ = _build(tmp_path, [Turn(writes={"src/App.tsx": "// changed\n"})],
                     build_policy=replace(BuildPolicy(), runtime_error_wait_seconds=1.0,
                                          page_ack_wait_seconds=0.5, page_check_wait_seconds=30.0,
                                          runtime_repair_limit=0))
    project = orch.project(start_preview=False)
    monkeypatch.setattr(orch, "_restart_preview_for_config_change", lambda project: None)
    project.supervisor = Preview(project.workspace.app_id)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    handler = type("Handler", (_Preview,), {"orch": orch, "app_id": project.workspace.app_id, "posts": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="preview-server")
    thread.start()
    monkeypatch.setenv("SAGE_CONTROL_PORT", str(server.server_address[1]))
    monkeypatch.setattr(page_check, "domino_base_prefix", lambda: "")
    orch._page_check = page_check.start
    try:
        yield orch, handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
def test_real_chromium_passes_a_healthy_page_with_no_workbench(served):
    orch, handler = served
    handler.screen = HEALTHY
    _, done = run(orch)
    assert done["verification"]["stages"]["page"] == "passed"
    assert done["verification"]["stages"]["runtime"] == "passed"


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
def test_real_chromium_fails_a_page_that_throws_on_render_with_no_workbench(served):
    orch, handler = served
    handler.screen = THROWS
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "failed"
    assert done["ok"] is False


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
def test_real_chromium_passes_a_healthy_page_with_tabs(served):
    orch, handler = served
    handler.screen = HEALTHY_TABS
    _, done = run(orch)
    assert done["verification"]["stages"]["page"] == "passed"
    assert done["verification"]["stages"]["runtime"] == "passed"


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
def test_real_chromium_fails_a_page_whose_second_tab_crashes(served):
    orch, handler = served
    handler.screen = CRASHES_ON_SECOND_TAB
    _, done = run(orch)
    assert done["verification"]["stages"]["page"] == "passed"
    assert done["verification"]["stages"]["runtime"] == "failed"
    assert done["ok"] is False


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
def test_real_chromium_opens_each_nav_screen_and_no_other_button(served):
    orch, handler = served
    handler.screen = HEALTHY_NAV
    _, done = run(orch)
    assert done["verification"]["stages"]["runtime"] == "passed"
    assert [p for p in handler.posts if not p.startswith("/api/preview/")] == ["/api/screen"]


@pytest.mark.skipif(_UNAVAILABLE is not None, reason=f"real headless page check: {_UNAVAILABLE}")
def test_real_chromium_fails_a_page_whose_nav_screen_crashes(served):
    orch, handler = served
    handler.screen = CRASHES_ON_SECOND_NAV_SCREEN
    _, done = run(orch)
    assert done["verification"]["stages"]["page"] == "passed"
    assert done["verification"]["stages"]["runtime"] == "failed"
    assert done["ok"] is False


def test_availability_names_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(page_check, "_has_playwright_core", lambda: True)
    monkeypatch.setattr(page_check, "_node", lambda: "/usr/bin/node")
    monkeypatch.setenv("SAGE_PAGE_CHECK_CHROMIUM", str(tmp_path / "missing"))
    assert page_check.unavailable() == (
        "no headless browser in this environment (SAGE_PAGE_CHECK_CHROMIUM is not a file)")
    monkeypatch.delenv("SAGE_PAGE_CHECK_CHROMIUM")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert page_check.unavailable() == "no headless browser in this environment (no Chromium found)"
    shell = tmp_path / "chromium_headless_shell-1248" / "chrome-headless-shell-linux64"
    shell.mkdir(parents=True)
    (shell / "chrome-headless-shell").write_text("")
    assert page_check.unavailable() is None
    assert page_check.find_chromium() == shell / "chrome-headless-shell"
    monkeypatch.setattr(page_check, "_node", lambda: None)
    assert page_check.unavailable() == "no headless browser in this environment (node is not on PATH)"

