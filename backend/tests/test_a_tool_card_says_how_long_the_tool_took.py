"""A tool card carries the duration OpenCode measured, or none at all.

`_tool_duration_ms` read `state.time = {start, end}`. OpenCode 1.18.4 does not put it there — a
completed tool part carries its own top-level `time = {created, ran, completed}` and its `state`
has no `time` at all. So the lookup missed on every part ever seen, `durationMs` was never set, and
every tool row in every build rendered without a duration. Measured live against the gateway on
2026-09-07: 0 occurrences of durationMs across a whole build's event stream.

The failure was invisible because the function is already allowed to answer None, and None is what
it answered — "nobody measured it" is indistinguishable from "we looked in the wrong place".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def test_a_build_puts_the_duration_on_the_tool_event(tmp_path: Path):
    """The unit test (test_turn_path) and the double can agree with each other and still both be
    wrong about the wire — which is exactly how this shipped broken. This is the seam that catches
    it: a real turn, read through the real poll loop, has to put a duration on the card the person
    sees."""
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=None,
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(),
                        opencode_client=FakeOpenCode(ws, [
                            Turn(text="Built it.",
                                 writes={"src/App.tsx": "export default () => null\n"})]))
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.IMPLEMENT)
    project.record.write_settings({"skip_planning": True})

    tools = [e for e in orch.build_stream("add a chart")
             if e.get("type") == "agent" and e.get("kind") == "tool"]
    assert tools, "the turn emitted no tool cards at all"
    assert all("durationMs" in t for t in tools), (
        "a tool card went out with no duration — _tool_duration_ms is reading a field OpenCode "
        f"does not send: {tools}"
    )
    assert tools[0]["durationMs"] == 250
