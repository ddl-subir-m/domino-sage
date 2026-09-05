"""A build the model cut in half by sending arguments that never parsed.

The live shape (2026-09-05): a model emitted invalid JSON for a `write`, OpenCode put the raw
arguments string where the input dict belongs and failed the session with "Invalid JSON input for
openai-chat tool call write", and Sage crashed reading that string for an action-card label.

Fixing only the crash makes the failure quieter, not better. OpenCode drops the session, so the
poll loop sees the turn stop running and leaves by its ordinary exit; the files written before the
break are orphans nothing imports yet, so the typecheck goes green. The person is handed a
finished-looking build of an app that never changed. What these tests pin is that the turn says so
instead — and that it only says so when the work really was cut off.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator, _tool_detail, _unparsed_tool_input
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building an app\n")
    return t


def _orch(tmp: Path, turns: list[Turn]) -> tuple[Orchestrator, FakeOpenCode]:
    oc = FakeOpenCode(tmp / "mnt" / "code", turns)
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch, oc


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def test_a_tool_call_whose_arguments_never_parsed_is_recognised():
    # The shape the traceback proves: a part whose state.input is the raw arguments text.
    running = {"status": "running", "input": '{"filePath": "src/Dashboard.tsx", "conte'}
    assert _unparsed_tool_input({"state": running})
    # And the shapes that are not it: a real input, an empty one, no state, a state that is a
    # string itself. None of these may raise a false alarm on an ordinary turn.
    assert not _unparsed_tool_input({"state": {"input": {"filePath": "src/App.tsx"}}})
    assert not _unparsed_tool_input({"state": {"input": ""}})
    assert not _unparsed_tool_input({"state": {"status": "completed"}})
    assert not _unparsed_tool_input({})
    assert not _unparsed_tool_input({"state": "completed"})


def test_a_tool_call_with_unparsed_arguments_yields_no_label():
    # The crash itself: reading that string for a label raised AttributeError and took down the
    # whole stream. A label is not worth a build.
    part = {"state": {"status": "running", "input": '{"filePath": "src/Dashboard.tsx", "conte'}}
    assert _tool_detail("write", part) == ""
    assert _tool_detail("bash", part) == ""
    assert _tool_detail("todowrite", part) == ""
    assert _tool_detail("write", {}) == ""
    assert _tool_detail("write", {"state": "completed"}) == ""
    assert _tool_detail("write", {"state": {"input": {"filePath": "src/App.tsx"}}}) == "src/App.tsx"


def test_a_build_cut_off_by_a_broken_call_does_not_report_success(tmp_path: Path):
    """Seven files in, the eighth call arrives unparsed and the session goes. Say it."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/MetricCard.tsx": "card\n"},
                                      broken_write=True)])

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["ok"] is False
    assert done["decision"] == "broken tool call"
    # And it names what happened in words the person can act on, rather than leaving the failure
    # to be inferred from an app that did not change.
    message = _of(events, "error")[0]["message"]
    assert "broken write call" in message
    assert "Try the same request again." in message


def test_the_file_written_before_the_break_is_still_there(tmp_path: Path):
    """The turn failed; the work it finished did not. The message promises this, so pin it."""
    orch, oc = _orch(tmp_path, [Turn(writes={"src/MetricCard.tsx": "card\n"}, broken_write=True)])

    events = list(orch.build_stream("build me a dashboard"))

    assert _of(events, "done")[0]["ok"] is False
    # Where the Build session stood, which is the Built App rather than the workspace root.
    written = Path(oc.sessions[0]["directory"]) / "src" / "MetricCard.tsx"
    assert written.exists() and written.read_text() == "card\n"


def test_an_ordinary_build_is_not_accused_of_a_broken_call(tmp_path: Path):
    """The flag must be unreachable on a turn where every call parsed."""
    orch, _oc = _orch(tmp_path, [Turn(text="Added the chart.",
                                      writes={"src/chart.tsx": "chart\n"})])

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "done")[0]["ok"] is True
    assert _of(events, "done")[0]["decision"] != "broken tool call"
