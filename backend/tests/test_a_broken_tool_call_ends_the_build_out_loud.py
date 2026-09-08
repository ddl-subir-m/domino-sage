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

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import (
    Orchestrator,
    _tool_detail,
    _unparsed_tool_evidence,
    _unparsed_tool_input,
)
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


def test_a_broken_call_is_sent_again_before_anybody_is_told(tmp_path: Path):
    """The fault is in one response, not in the request, so send the request again.

    This is the retry the give-up message used to ask the person to type. It is worth doing for
    them: the second attempt is what actually built the app in the live run of 2026-09-05.
    """
    orch, oc = _orch(tmp_path, [Turn(writes={"src/MetricCard.tsx": "card\n"}, broken_write=True),
                                Turn(text="Added the dashboard.", writes={"src/App.tsx": "app\n"})])

    events = list(orch.build_stream("build me a dashboard"))

    assert _of(events, "done")[0]["ok"] is True
    assert not _of(events, "error")
    # The retry is visible. A build that silently takes twice as long is its own kind of wrong.
    assert any("arrived broken" in e.get("reason", "") for e in _of(events, "iterate"))
    # In a NEW session: the broken call is in the old one's history and OpenCode replays history
    # into every later request, so re-sending there risks a turn that cannot start at all.
    assert len(oc.sessions) == 2
    assert oc.prompts[1]["session"] != oc.prompts[0]["session"]
    # It is the same request, not a nudge. The blocks that ride the first send only — the user's
    # attachments and the Resource/Chat notes — are cleared after it, and a fresh session heard
    # none of them, so the retry has to carry them again.
    assert oc.prompts[1]["text"].startswith(oc.prompts[0]["text"])
    # But not byte-identical. A fresh session was told nothing about the one it replaces, so the
    # retry names what broke and that the app on disk is mid-change. What it must NOT name is a
    # cause the evidence does not support — see
    # test_a_cut_stream_is_named_for_what_cut_it.py, which pins that half.
    note = oc.prompts[1]["text"][len(oc.prompts[0]["text"]):]
    assert "write call arrived with arguments that did not parse" in note
    assert "read it before you change it" in note


def test_the_retry_note_rides_the_retry_only(tmp_path: Path):
    """It answers the break, so a turn with no break must never carry it."""
    orch, oc = _orch(tmp_path, [Turn(text="Added the chart.", writes={"src/chart.tsx": "c\n"})])

    list(orch.build_stream("add a chart"))

    assert all("did not parse" not in p["text"] for p in oc.prompts)


def test_the_broken_arguments_are_described_for_the_log():
    """The tool name alone cannot separate an output cap from a bad escape. Head and tail can."""
    cut = {"state": {"status": "running", "input": '{"filePath": "src/App.tsx", "content": "cons'}}
    ev = _unparsed_tool_evidence(cut)
    assert "len=" in ev and "src/App.tsx" in ev and "cons" in ev
    # Head and tail only — the whole string is the file the model was writing.
    long_input = {"state": {"status": "running", "input": "x" * 90_000}}
    assert len(_unparsed_tool_evidence(long_input)) < 500
    assert "len=90000" in _unparsed_tool_evidence(long_input)
    # And nothing to say about the shapes that are not a break.
    assert _unparsed_tool_evidence({"state": {"input": {"filePath": "src/App.tsx"}}}) == ""
    assert _unparsed_tool_evidence({}) == ""
    assert _unparsed_tool_evidence({"state": "completed"}) == ""


def test_a_build_cut_off_twice_does_not_report_success(tmp_path: Path):
    """Seven files in, the eighth call arrives unparsed — and so does the retry's. Say it."""
    orch, oc = _orch(tmp_path, [Turn(writes={"src/MetricCard.tsx": "card\n"}, broken_write=True),
                                Turn(broken_write=True)])

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["ok"] is False
    assert done["decision"] == "broken tool call"
    # And it names what happened in words the person can act on, rather than leaving the failure
    # to be inferred from an app that did not change.
    message = _of(events, "error")[0]["message"]
    # Not "the model sent a broken call": the capture behind #207 is a gateway that stopped
    # sending, so the first sentence must not hand the fault to the model that the second sentence
    # then takes back off it.
    assert "part-way through a write step, twice" in message
    assert "The model sent a broken" not in message
    # And it says what the log says: the gateway stopped mid-answer, twice. What it must NOT say is
    # that one step was too big — see test_a_cut_stream_is_named_for_what_cut_it.py.
    assert "the model gateway stopped responding" in message
    # The advice used to be "again in a few minutes", on the reading that the gateway was under
    # load. It is not (2026-09-08): the gateway buffers a tool call's argument deltas instead of
    # streaming them, so the connection goes quiet and a 60s idle limit ends it — 13 reproductions
    # out of 13, at concurrency 1, 4 and 8 alike, following the alias rather than the hour
    # (gateway-questions.md bug 3). Waiting is not a fix and asking for it spends the person's
    # afternoon, so the message asks for the one thing that is.
    assert "Pick a different model and send the same request again." in message
    assert "few minutes" not in message, "the give-up still tells the person to wait it out"
    # Exactly one retry. A model that breaks every time must not spend the whole build proving it.
    assert len(oc.sessions) == 2


def test_the_file_written_before_the_break_is_still_there(tmp_path: Path):
    """The turn failed; the work it finished did not. The message promises this, so pin it."""
    orch, oc = _orch(tmp_path, [Turn(writes={"src/MetricCard.tsx": "card\n"}, broken_write=True),
                                Turn(broken_write=True)])

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
