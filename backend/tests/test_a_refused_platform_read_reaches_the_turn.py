"""A platform read the relay refused reaches the turn that wrote the call (#556).

Measured 2026-09-25 on a haiku implement model: the app called
`datasets-v2?datasetIds=ABC123_ADAE` — the Dataset's NAME — and the page caught the failed fetch
and `console.log`ged it, where the model cannot read it. The only channel back into the build loop
carried uncaught throws. Eight implement turns and ~131 model calls later the id was still the name
and the screen said "No governance tags assigned", over a Dataset that carries one.

The channel is the preview proxy's `on_platform_read`, wired to `record_platform_read_failure`,
which stamps the record the way `record_runtime_error` stamps a crash (#77): with the app the
preview was serving, because the person may have switched away from the one being built. The loop
reads it in the same window it reads a crash, and iterates once.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode

from .fake_opencode import Turn
from .test_an_approved_plan_runs_as_implement import _build
from .test_switch_app import _two_apps

PATH = "/v4/datasetrw/datasets-v2?datasetIds=ABC123_ADAE&includeTaxonomyTags=true"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The same two waits test_turn_path strips: a scripted turn can only spend them. The runtime
    wait is stubbed to "clean" so the block under test is the one that decides."""
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _direct(tmp: Path, turns: list[Turn]):
    orch, oc = _build(tmp, turns)
    project = orch.project(start_preview=False)
    project.record.write_settings({"skip_planning": True})
    project.control.set_mode(Mode.IMPLEMENT)
    return orch, oc, project


def _refused_during_the_first_send(orch: Orchestrator, oc, status: int = 404, path: str = PATH):
    """The preview reports while the turn runs: after `send_ts` is taken and before the loop looks.
    The fake dispatches inside `send_prompt`, so "during" is right after it returns."""
    send_prompt = oc.send_prompt
    sends: list[int] = []

    def capture(*args, **kwargs):
        out = send_prompt(*args, **kwargs)
        if not sends:
            orch.record_platform_read_failure(status, path)
        sends.append(1)
        return out

    oc.send_prompt = capture


def test_a_refused_read_gets_one_iterate_that_names_the_status_and_path(tmp_path: Path):
    orch, oc, project = _direct(tmp_path, [
        Turn(writes={"src/dominoApi.ts": "// by name\n"}),
        Turn(writes={"src/dominoApi.ts": "// by id\n"}),
    ])
    _refused_during_the_first_send(orch, oc)

    events = list(orch.build_stream("show the taxonomy tags this dataset has"))

    iterates = [e for e in events if e["type"] == "iterate"]
    assert len(iterates) == 1
    assert iterates[0]["reason"].startswith("platform read refused")
    assert "404" in iterates[0]["reason"] and "/v4/datasetrw/datasets-v2" in iterates[0]["reason"]
    # The nudge is the next prompt: the status, the path, and the two rules the eight turns broke.
    nudge = oc.prompts[1]["text"]
    assert "404" in nudge and PATH in nudge
    assert "platform id" in nudge and "do not substitute" in nudge
    assert events[-1]["type"] == "done" and events[-1]["ok"] is True
    assert project.platform_read_failure is None       # consumed, so a later turn starts clean


def test_no_refusal_means_no_iterate(tmp_path: Path):
    orch, oc, _project = _direct(tmp_path, [Turn(writes={"src/App.tsx": "// fine\n"})])

    events = list(orch.build_stream("build it"))

    assert [e for e in events if e["type"] == "iterate"] == []
    assert len(oc.prompts) == 1


def test_a_refusal_older_than_the_send_is_not_this_turns(tmp_path: Path):
    orch, oc, _project = _direct(tmp_path, [Turn(writes={"src/App.tsx": "// fine\n"})])
    orch.record_platform_read_failure(404, PATH)       # a stale one, from before the turn

    events = list(orch.build_stream("build it"))

    assert [e for e in events if e["type"] == "iterate"] == []
    assert len(oc.prompts) == 1


def test_a_refusal_from_another_app_is_ignored(tmp_path: Path):
    """The preview reports for the app it is serving. Once the person has switched, that is not the
    app the turn is fixing (#77) — the same rule `_await_runtime_error` applies to a crash."""
    orch, _oc, _root, first, second = _two_apps(tmp_path)
    orch.select_app(first)
    orch.record_platform_read_failure(404, PATH)
    project = orch.project(start_preview=False)

    assert project.platform_read_failure["app"] == first

    project.turn_app = orch._wm.app_workspace("Sage", second)      # a turn running in the other
    assert orch._fresh_platform_read_failure(project, since=0.0) is None

    project.turn_app = orch._wm.app_workspace("Sage", first)       # its own app's refusal lands
    assert orch._fresh_platform_read_failure(project, since=0.0)["status"] == 404


def test_a_report_with_no_project_is_dropped(tmp_path: Path):
    orch, _oc = _build(tmp_path, [])

    orch.record_platform_read_failure(404, PATH)

    assert orch._project is None


def test_the_control_app_hands_the_proxys_report_to_the_record(monkeypatch):
    """The two lines in app.py between the proxy and the orchestrator. A misspelt method there
    raises only when a refusal happens — exactly when no test is watching."""
    import sage.orchestrator.app as app_module

    class Stub:
        heard = None

        def record_platform_read_failure(self, status, path):
            self.heard = (status, path)

    stub = Stub()
    monkeypatch.setattr(app_module, "orchestrator", stub)

    app_module._preview_platform_read(404, PATH)

    assert stub.heard == (404, PATH)
