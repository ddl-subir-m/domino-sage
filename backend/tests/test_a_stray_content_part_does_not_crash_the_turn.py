"""A bare string in OpenCode's `content` array must not crash the turn.

WHAT WAS BROKEN. Both the chat loop and the build loop read each assistant message's content as
`for i, part in enumerate(m.get("content", [])): ... part.get("type", "")`, assuming every part is
a dict. OpenCode has been observed sending a plain string in that array instead of the usual
`{"type": ...}` shape. The first `.get()` call then raised `AttributeError: 'str' object has no
attribute 'get'`, which unwound the turn as an unhandled exception rather than a recognized failure
— so `_turn_gave_up` was never set, the plan's retry bookkeeping was left in whatever state the
crash happened to land in, and the next "continue" from the user was read as a brand-new request
instead of a resume.

WHAT THIS ASSERTS. A stray non-dict part is skipped, not fatal: the turn finishes normally and any
real parts around it (text, tool writes) are still read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orch(tmp: Path, turns: list[Turn]):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(
        workspace_dir=ws, template=template, gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def test_a_stray_string_part_is_skipped_and_the_build_still_finishes(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        Turn(text="Added the table.", writes={"src/Table.tsx": "export default function Table() {}"},
             stray_content=True),
    ])
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.IMPLEMENT)
    project.record.write_settings({"skip_planning": True})

    events = list(orch.build_stream("add a table of sales"))

    assert [e for e in events if e["type"] == "error"] == []
    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["ok"] is True
    texts = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"]
    assert "Added the table." in texts
