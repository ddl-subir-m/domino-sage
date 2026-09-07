"""The build poll asks for the newest messages, not for all of them.

`messages()` grew a `limit` because the loop polls once a second and, on a long turn, the whole
transcript came back every time — cost grows with the conversation rather than with the question
(see OpenCodeClient.messages). Chat passes it. Build did not, so a build paid to re-serialize its
own entire session every second, out of the same single-threaded Node server that was running the
agent. The longer the build, the slower it got.

The assertion is on the ARGUMENT, not on a timing: a test that measured the cost would need a
session big enough to be slow, which is the thing this stops anyone from having to build.
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


class CountingOpenCode(FakeOpenCode):
    """Records the `limit` every caller asked for, so a test can name the unbounded ones."""

    def __init__(self, workspace: Path, turns: list[Turn] | None = None) -> None:
        super().__init__(workspace, turns)
        self.limits: list[int | None] = []

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        self.limits.append(limit)
        return super().messages(session_id, limit=limit)


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="p", implement="i", ask="a")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def test_a_build_turn_never_asks_for_the_whole_transcript(tmp_path: Path):
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="Built it.", writes={"src/App.tsx": "export default () => null\n"})])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=None, catalog=_catalog(),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).control.set_mode(Mode.IMPLEMENT)

    oc.limits.clear()
    list(orch.build_stream("add a chart"))

    unbounded = [i for i, lim in enumerate(oc.limits) if lim is None]
    assert not unbounded, (
        f"{len(unbounded)} of {len(oc.limits)} messages() calls in a build turn asked for the "
        f"ENTIRE session. Every poll re-serializes the whole transcript; pass a limit."
    )
