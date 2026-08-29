"""The preview follows the selected Built App (#77, ADR-0008).

A Project holds many Built Apps and each has its own directory, so moving between them is looking
rather than editing. Three rules meet here:

- One preview runs at a time, and it serves whichever app is on screen. Selecting another app stops
  the one that was running and starts it again in the new directory.
- The turn lock stays one per Project. A second turn waits behind the one running (#79), and
  SWITCHING does not take that lock at all — a build that is already running keeps running in the
  app it started in, and the rail says which app that is.

The second rule is what the pin exists for: a turn writes into the app it began in, not the one that
appeared under it while it was working.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from sage.orchestrator import service as svc
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

# Grabbed before the autouse fixture stubs it out, so the one test about crash routing can
# call the real thing while every other test keeps the stub that makes turns fast.
_REAL_AWAIT = Orchestrator._await_runtime_error


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request — the Chat/Build classifier is its only caller here."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class FakeVite:
    """Stands in for ViteSupervisor and records the two things a test asks about a preview: which
    directory it serves, and whether it is running. Every instance ever made is kept, because
    "only one runs at a time" is a claim about the ones that were left behind."""

    made: ClassVar[list] = []

    def __init__(self, workspace, base_prefix: str = "", **_ignored) -> None:
        self.workspace = Path(workspace)
        self.running = False
        self.starts = 0
        FakeVite.made.append(self)

    def start(self, ready_timeout_s: float = 30.0) -> str:
        self.running = True
        self.starts += 1
        return "http://127.0.0.1:5173"

    def upstream(self) -> str:
        if not self.running:
            raise RuntimeError("Vite not ready")
        return "http://127.0.0.1:5173"

    def stop(self) -> None:
        self.running = False


class FakeQueries:
    """The named-query server that rides beside the preview. It follows the same directory."""

    def __init__(self, workspace, template=None) -> None:
        self.workspace = Path(workspace)
        self.port: int | None = None

    def start(self) -> None:
        self.port = 7777

    def refresh(self) -> None:
        pass

    def stop(self) -> None:
        self.port = None


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fake_preview(monkeypatch):
    FakeVite.made = []
    monkeypatch.setattr(svc, "ViteSupervisor", FakeVite)
    monkeypatch.setattr(svc, "PreviewQueries", FakeQueries)
    yield
    FakeVite.made = []


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    return t


def _orch(tmp: Path, turns: list[Turn] | None = None):
    root = tmp / "mnt" / "code"
    oc = FakeOpenCode(root, turns or [])
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    return orch, oc, root


def _two_apps(tmp_path: Path, turns: list[Turn] | None = None):
    """Two Built Apps from the Build rail, the second selected because minting selects it (#74).

    Planning is off: this file is about which app a turn lands in, and a plan gate in the way would
    make every build here a two-step conversation about nothing these tests assert."""
    orch, oc, root = _orch(tmp_path, turns)
    first = orch.project(start_preview=False).workspace.app_id
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    second = orch.create_app()["id"]
    return orch, oc, root, first, second


def _log(root: Path, app_id: str) -> str:
    log = root / "apps" / app_id / ".sage" / "history.jsonl"
    return log.read_text() if log.exists() else ""


# --- the preview follows the app on screen --------------------------------------------------------

def test_selecting_an_app_restarts_the_preview_in_that_apps_directory(tmp_path: Path):
    orch, _oc, root, first, second = _two_apps(tmp_path)
    project = orch.project(start_preview=False)
    orch._ensure_preview_running(project)

    assert project.supervisor.workspace == root / "apps" / second

    orch.select_app(first)
    project = orch.project(start_preview=False)
    orch._ensure_preview_running(project)

    assert project.supervisor.workspace == root / "apps" / first
    assert project.supervisor.running
    assert project.queries.workspace == root / "apps" / first


def test_only_one_preview_runs_at_a_time(tmp_path: Path):
    """The one left behind is stopped, not merely forgotten: a Vite still serving the app somebody
    walked away from is a second dev server on a port this one wants."""
    orch, _oc, _root, first, _second = _two_apps(tmp_path)
    orch._ensure_preview_running(orch.project(start_preview=False))

    orch.select_app(first)
    orch._ensure_preview_running(orch.project(start_preview=False))

    assert [v.running for v in FakeVite.made].count(True) == 1
    assert orch.project(start_preview=False).supervisor.running


# --- switching while a build runs -----------------------------------------------------------------

def _at_first_send(oc, action) -> dict:
    """Run `action()` once, at the moment the agent's first send of a turn goes out.

    Patching the send is what makes this deterministic: whatever the person does lands after the
    turn opened its session and before the agent wrote a line, which is the window the pin has to
    survive. `action`'s return value is kept under "out"."""
    outcome: dict = {}
    send = oc.send_prompt

    def act_then_send(*args, **kwargs):
        if "out" not in outcome:
            outcome["out"] = action()
        send(*args, **kwargs)

    oc.send_prompt = act_then_send
    return outcome


def _stream(events):
    """Drain a turn generator on its own thread, the way the SSE route does (`app.py:_turn_sse`).

    A turn asked for while one is running waits in line now (#79), so `list()`ing one from inside
    the running turn is a deadlock rather than a refusal."""
    import threading

    seen: list[dict] = []
    finished = threading.Event()

    def pump() -> None:
        try:
            for ev in events:
                seen.append(ev)   # noqa: PERF402 — one at a time, so a test can read it as it fills
        finally:
            finished.set()

    threading.Thread(target=pump, daemon=True).start()
    return seen, finished


def _wait_for(predicate, timeout: float = 20.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the turn queue")


def _switch_mid_turn(orch, oc, to_app: str) -> dict:
    """The person clicks another app in the rail while the turn is streaming."""
    return _at_first_send(oc, lambda: {"switched": orch.select_app(to_app),
                                       "rail": orch.list_apps()})


def test_switching_app_is_not_refused_while_a_build_is_running(tmp_path: Path):
    """It used to answer `busy`. A switch takes no working tree and writes no code, so the only
    thing refusing it bought was a person stuck watching one app for the length of a build."""
    orch, oc, _root, first, _second = _two_apps(tmp_path, [Turn(text="Built it.",
                                                                writes={"src/App.tsx": "// P&L\n"})])
    outcome = _switch_mid_turn(orch, oc, first)

    list(orch.build_stream("build me a P&L report"))

    assert outcome["out"]["switched"]["id"] == first
    assert orch.project(start_preview=False).workspace.app_id == first


def test_a_build_that_is_running_keeps_writing_into_the_app_it_started_in(tmp_path: Path):
    """The whole reason the turn pins its app. Minutes of work must not follow the rail."""
    orch, oc, root, first, second = _two_apps(tmp_path, [Turn(text="Built it.",
                                                              writes={"src/App.tsx": "// P&L\n"})])
    _switch_mid_turn(orch, oc, first)

    list(orch.build_stream("build me a P&L report"))

    building = root / "apps" / second
    watched = root / "apps" / first
    assert oc.sessions[-1]["directory"] == str(building)
    assert (building / "src" / "App.tsx").read_text() == "// P&L\n"
    # Everything the turn records lands beside the code it wrote, not in the app now on screen.
    assert "build me a P&L report" in _log(root, second)
    assert _log(root, first) == ""
    assert orch._wm.app_workspace("Sage", second).has_built()
    assert not orch._wm.app_workspace("Sage", first).has_built()
    assert (watched / "src" / "App.tsx").read_text().startswith("export default function App()")


def test_the_preview_follows_the_switch_while_the_build_carries_on(tmp_path: Path):
    """Both halves of the ticket in one turn: the screen changes, the build does not."""
    orch, oc, root, first, second = _two_apps(tmp_path, [Turn(text="Built it.",
                                                              writes={"src/App.tsx": "// P&L\n"})])
    _switch_mid_turn(orch, oc, first)

    list(orch.build_stream("build me a P&L report"))
    orch._ensure_preview_running(orch.project(start_preview=False))

    assert orch.project(start_preview=False).supervisor.workspace == root / "apps" / first
    assert (root / "apps" / second / "src" / "App.tsx").read_text() == "// P&L\n"


def test_the_rail_marks_the_app_a_build_is_running_in(tmp_path: Path):
    """So that "why can't I type" has an answer on screen rather than in the composer alone."""
    orch, oc, _root, first, second = _two_apps(tmp_path, [Turn(text="Built it.",
                                                               writes={"src/App.tsx": "// P&L\n"})])
    outcome = _switch_mid_turn(orch, oc, first)

    list(orch.build_stream("build me a P&L report"))

    assert {r["id"]: r["building"] for r in outcome["out"]["rail"]} == {first: False, second: True}
    # And the mark is the turn's, not the app's: it goes when the turn does.
    assert [r["id"] for r in orch.list_apps() if r["building"]] == []


def test_a_second_turn_asked_mid_build_waits_rather_than_running_alongside(tmp_path: Path):
    """Switching gave up the turn lock; nothing else did. One tree, one turn — still true under the
    queue (#79), and reached differently: the second send is accepted and WAITS, where it used to be
    told a build was already running.

    It waits on its own thread because a queued turn is held on the connection it arrived on. Called
    inline the way the switch above it is, it would be a turn waiting for the turn that is waiting
    for it. That is not a queue artefact — it is what the SSE route already does with every turn."""
    orch, oc, root, first, _second = _two_apps(tmp_path,
                                               [Turn(text="Built it.", writes={"src/App.tsx": "// P&L\n"}),
                                                Turn(text="Charted it.", writes={"src/Chart.tsx": "// c\n"})])
    queued: dict = {}

    def switch_then_send_again():
        orch.select_app(first)                          # allowed
        events, finished = _stream(orch.build_stream("and a chart"))
        queued["events"], queued["finished"] = events, finished
        _wait_for(lambda: any(e.get("type") == "pending" for e in events))
        return events

    _at_first_send(oc, switch_then_send_again)

    list(orch.build_stream("build me a P&L report"))

    assert queued["finished"].wait(30) is True
    waited = queued["events"]
    assert waited[0]["type"] == "pending"
    assert not any("already running" in str(ev.get("message", "")) for ev in waited)
    # And when its turn came it built into the app it was written for — the one the switch above
    # selected, not the one the turn it queued behind was pinned to.
    assert waited[-1]["type"] == "done"
    assert (root / "apps" / first / "src" / "Chart.tsx").exists()


def test_a_crash_in_the_preview_on_screen_is_not_fed_to_a_build_in_another_app(tmp_path: Path):
    """The preview reports for the app it is serving. Once the person has switched, that is not the
    app the turn is fixing, and handing its crash to the agent would send it hunting a file it
    cannot see."""
    orch, _oc, _root, first, second = _two_apps(tmp_path)
    orch.select_app(first)
    orch.record_runtime_error("Cannot read properties of undefined", "at App.tsx:12")
    project = orch.project(start_preview=False)

    assert project.runtime_error["app"] == first

    project.turn_app = orch._wm.app_workspace("Sage", second)      # a turn still running in the other
    assert _REAL_AWAIT(orch, project, since=0.0, timeout=0.0) is None

    project.turn_app = orch._wm.app_workspace("Sage", first)       # its own app's crash still lands
    assert _REAL_AWAIT(orch, project, since=0.0, timeout=0.0)["message"].startswith("Cannot read")


def test_the_route_switches_app_while_a_build_is_running(tmp_path: Path, monkeypatch):
    """The rail's wire. The 409 it used to get back was the only thing between a person and the
    app they clicked."""
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as appmod

    orch, oc, _root, first, _second = _two_apps(tmp_path, [Turn(text="Built it.",
                                                                writes={"src/App.tsx": "// P&L\n"})])
    monkeypatch.setattr(appmod, "orchestrator", orch)

    with TestClient(appmod.control_app) as client:
        outcome = _at_first_send(oc, lambda: client.post(f"/api/apps/{first}/select").status_code)
        list(orch.build_stream("build me a P&L report"))

    assert outcome["out"] == 200


def test_the_end_of_turn_repair_rewrites_the_manifest_of_the_app_that_was_built(tmp_path: Path):
    """The repair that puts back what a turn deleted works from process memory, and the person may
    have pointed that memory at another app before the turn ended. It has to follow the turn."""
    orch, _oc, root, first, second = _two_apps(tmp_path)
    project = orch.project(start_preview=False)
    project.turn_app = orch._wm.app_workspace("Sage", second)
    project.turn_attached = [{"path": "public/data/sales.csv", "dataset_id": None,
                              "dataset": "sales", "file": "sales.csv", "size": 12}]

    orch.select_app(first)
    orch._restore_attachments()

    built = json.loads((root / "apps" / second / ".sage" / "attachments.json").read_text())
    assert [e["path"] for e in built] == ["public/data/sales.csv"]
    assert not (root / "apps" / first / ".sage" / "attachments.json").exists()


_PHASED_PLAN = """A daily P&L report.

## Plan

### 1. Data module
- Files — src/data.ts
- Do — Export the day's rows.
- Done when — src/data.ts exports rows and the app compiles.

### 2. P&L table
- Files — src/Table.tsx
- Do — Render the rows in a table.
- Done when — The preview shows a table.

### 3. Desk filter
- Files — src/Filter.tsx
- Do — Add a desk dropdown above the table.
- Done when — Picking a desk narrows the rows.
"""


def test_every_phase_of_a_phased_build_runs_in_the_app_the_build_started_in(tmp_path: Path):
    """A phased build opens a FRESH session per phase, so it asks which app it is in more than once
    — and it is the longest build there is, which makes it the likeliest to be walked away from."""
    orch, oc, root, first, second = _two_apps(
        tmp_path,
        [Turn(text=_PHASED_PLAN),
         Turn(writes={"src/data.ts": "export const rows = [];\n"}),
         Turn(writes={"src/Table.tsx": "export const Table = () => null;\n"}),
         Turn(writes={"src/Filter.tsx": "export const Filter = () => null;\n"})])
    orch.project(start_preview=False).record.write_settings({"phased_build": True})

    list(orch.build_stream("build me a daily P&L report"))     # the gated plan turn
    _switch_mid_turn(orch, oc, first)                          # ...and away during phase 1
    list(orch.approve_stream())

    building = root / "apps" / second
    assert {s["directory"] for s in oc.sessions if s["id"] != "fake-session"} == {str(building)}
    assert (building / "src" / "data.ts").exists()
    assert (building / "src" / "Table.tsx").exists()
    assert (building / "src" / "Filter.tsx").exists()
    assert not (root / "apps" / first / "src" / "data.ts").exists()
    assert orch.project(start_preview=False).workspace.app_id == first
