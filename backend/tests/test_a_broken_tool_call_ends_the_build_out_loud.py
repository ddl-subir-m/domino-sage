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
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from sage import build_diagnostics
from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import (
    InvalidToolCall,
    Orchestrator,
    _invalid_tool_call,
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
    project = orch.project(start_preview=False)
    intents = []
    send_prompt = oc.send_prompt

    def capture(*args, **kwargs):
        intents.append(project.active_build_intent)
        return send_prompt(*args, **kwargs)

    oc.send_prompt = capture

    events = list(orch.build_stream("build me a dashboard"))

    assert _of(events, "done")[0]["ok"] is True
    assert not _of(events, "error")
    # The retry is visible. A build that silently takes twice as long is its own kind of wrong.
    assert any("arrived broken" in e.get("reason", "") for e in _of(events, "iterate"))
    # In a NEW session: the broken call is in the old one's history and OpenCode replays history
    # into every later request, so re-sending there risks a turn that cannot start at all.
    assert len(oc.sessions) == 2
    assert oc.prompts[1]["session"] != oc.prompts[0]["session"]
    # It is the same canonical request, not a new task. The new session reuses the in-memory intent.
    assert len(intents) == 2 and intents[0] is intents[1]
    assert intents[0].source_requests == ("build me a dashboard",)
    # The blocks that ride the first send only — attachments and support notes — are restored for
    # the fresh session. The exact task stays outside OpenCode's stored prompt.
    first, retry = oc.prompts[0]["text"], oc.prompts[1]["text"]
    assert "build me a dashboard" not in first and "build me a dashboard" not in retry
    assert "Existing source paths (JSON array" in retry, "so is the source listing"
    # This used to be `retry.startswith(first)`, and it stopped being true on purpose (#496). One
    # of those blocks is not something the person said — it is a fact about the disk, and the disk
    # MOVED: the call that broke landed `src/MetricCard.tsx` first. Restoring the string built
    # before the attempt handed the retry a listing that did not mention the file the previous
    # attempt had just written, in the one session with nothing else to go on. So the listing is
    # rebuilt here, and the retry is no longer a byte-extension of the first send.
    assert "src/MetricCard.tsx" in retry, "the retry was told a listing built before the write"
    assert "src/MetricCard.tsx" not in first, "nothing had been written when the first send went"
    # A fresh session was told nothing about the one it replaces, so the retry also names what
    # broke and that the app on disk is mid-change. What it must NOT name is a cause the evidence
    # does not support — see test_a_cut_stream_is_named_for_what_cut_it.py, which pins that half.
    assert "write call arrived with arguments that did not parse" in retry
    assert "read it before you change it" in retry


def test_the_retry_note_rides_the_retry_only(tmp_path: Path):
    """It answers the break, so a turn with no break must never carry it."""
    orch, oc = _orch(tmp_path, [Turn(text="Added the chart.", writes={"src/chart.tsx": "c\n"})])

    list(orch.build_stream("add a chart"))

    assert oc.prompts and all("did not parse" not in p["text"] for p in oc.prompts)


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
    # Not "the model sent a broken call": that named a JSON fault the capture behind #207
    # disproves. The person is told the build stopped and the model stopped responding.
    assert "stopped twice in the same step" in message
    assert "The model sent a broken" not in message
    # The advice used to be "again in a few minutes", on the reading that the gateway was under
    # load. It is not (2026-09-08): the gateway buffers a tool call's argument deltas instead of
    # streaming them, so the connection goes quiet and a 60s idle limit ends it — 13 reproductions
    # out of 13, at concurrency 1, 4 and 8 alike, following the alias rather than the hour
    # (gateway-questions.md bug 3). Waiting is not a fix and asking for it spends the person's
    # afternoon, so the message asks for the one thing that is.
    assert "Pick a different model" in message
    assert "few minutes" not in message, "the give-up still tells the person to wait it out"
    # Size was the first wrong cause. The gateway is named in the log and the retry note, not
    # here — see test_a_cut_stream_is_named_for_what_cut_it.py.
    for claim in ("too big", "smaller"):
        assert claim not in message, f"the give-up still blames size: {claim!r}"
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


# --- The completed `invalid` wrapper (#565) ---------------------------------------------------
#
# The shape OpenCode 1.18.4 really emits for a malformed call, pinned against the binary in
# test_an_invalid_tool_call_never_ran_in_the_pinned_opencode.py: the raw-string shape above is the
# 2026-09-05 one, and the repaired #557 trials recorded seventeen of THIS one and none of that.
# The session is not dropped. The call is rewritten to the built-in `invalid` tool, which runs and
# completes with `{tool, error}` as its input, and the intended tool never executes.


def test_a_completed_invalid_wrapper_is_recovered_from_once(tmp_path: Path):
    """Invalid then valid: one correction, a finished build, and the row says one was spent."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]),
                                Turn(text="Added the dashboard.", writes={"src/App.tsx": "app\n"})])

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["ok"] is True
    assert done["recoveries"] == 1
    assert done["turnId"]
    assert "cause" not in done, "a turn that recovered is not a failed one"
    assert not _of(events, "error")
    assert any("arrived broken" in e.get("reason", "") for e in _of(events, "iterate"))
    # A fresh session, exactly as the raw-string shape gets, and one correction in it.
    assert len(oc.sessions) == 2
    assert oc.prompts[1]["session"] != oc.prompts[0]["session"]
    assert "write call" in oc.prompts[1]["text"]
    assert "read it before you change it" in oc.prompts[1]["text"]


def _wrapper(intended: str = "write", *, status: str = "completed", call_id: str = "call-1",
             error: str = "Invalid input for tool write: Type validation failed") -> dict:
    return {"id": "p1", "callID": call_id, "type": "tool", "tool": "invalid",
            "state": {"status": status, "input": {"tool": intended, "error": error},
                      "output": "The arguments provided to the tool are invalid: " + error}}


def test_the_classifier_reads_both_shapes_by_tool_and_part_identity():
    """Criterion 1, the unit half: the two shapes, keyed on identity and structured state."""
    legacy = {"id": "p9", "tool": "write",
              "state": {"status": "running", "input": '{"filePath": "src/Dashboard.tsx", "conte'}}
    assert _invalid_tool_call(legacy) == InvalidToolCall("write", "p9", "unparsed", completed=False)
    assert _invalid_tool_call(_wrapper()) == InvalidToolCall(
        "write", "call-1", "invalid_arguments", completed=True)
    # The SDK's other message, for a name it does not offer. A prefix read, never a search.
    unknown = _wrapper("wrtie", error="Model tried to call unavailable tool 'wrtie'. Available tools: bash")
    assert _invalid_tool_call(unknown).category == "unknown_tool"
    # The identity is OpenCode's callID when there is one, the part id when there is not.
    no_call = _wrapper(call_id="")
    assert _invalid_tool_call(no_call).call_id == "p1"
    # What may be said: an allowlisted name as itself, anything else as "tool".
    assert InvalidToolCall("write", "c", "invalid_arguments", True).named == "write"
    assert InvalidToolCall('write"; rm -rf /', "c", "invalid_arguments", True).named == "tool"
    assert InvalidToolCall("sage-live-read_live_read_table", "c", "unknown_tool", True).named \
        == "sage-live-read_live_read_table"


def test_shapes_that_are_not_a_missed_execution_are_not_one():
    """Criterion 2, the unit half: nothing here may trigger a replay."""
    # A call in flight with a partial input dict: `{}` and then the whole thing (#497).
    assert _invalid_tool_call({"tool": "write", "state": {"status": "running", "input": {}}}) is None
    assert _invalid_tool_call({"tool": "write", "state": {"status": "pending",
                                                         "input": {"filePath": "src/App.tsx"}}}) is None
    # A real tool that executed and failed: its arguments were fine, its answer was not.
    assert _invalid_tool_call({"tool": "bash", "state": {"status": "error",
                                                        "input": {"command": "npm test"},
                                                        "error": "exit 1"}}) is None
    assert _invalid_tool_call({"tool": "sage-live-read_live_read_query",
                               "state": {"status": "error", "input": {"sql": "select 1"},
                                         "error": "403 Forbidden"}}) is None
    # A quoted error, in prose and in a tool's output: text is never scanned.
    assert _invalid_tool_call({"type": "text", "text": "Invalid input for tool write"}) is None
    assert _invalid_tool_call({"tool": "read", "state": {
        "status": "completed", "input": {"filePath": "log.txt"},
        "output": "Invalid input for tool write: Type validation failed"}}) is None
    # An unknown wrapper: some other tool whose input happens to carry `tool` and `error`.
    assert _invalid_tool_call({"tool": "repair", "state": {
        "status": "completed", "input": {"tool": "write", "error": "x"}}}) is None
    # Uncertain execution: the wrapper itself still running, or with no readable intended tool.
    assert _invalid_tool_call(_wrapper(status="running")) is None
    assert _invalid_tool_call(_wrapper(status="pending")) is None
    assert _invalid_tool_call({"tool": "invalid", "state": {"status": "completed",
                                                           "input": {"error": "x"}}}) is None
    assert _invalid_tool_call({"tool": "invalid", "state": {"status": "completed",
                                                           "input": {"tool": "", "error": "x"}}}) is None
    assert _invalid_tool_call({"tool": "invalid", "state": "completed"}) is None
    assert _invalid_tool_call({"tool": "invalid"}) is None


def _append_parts(oc: FakeOpenCode, extra: list[dict]):
    """Add hand-built parts to the NEXT scripted message, after the fake has built it."""
    send_prompt = oc.send_prompt

    def send(session_id, *args, **kwargs):
        send_prompt(session_id, *args, **kwargs)
        oc._by_session[session_id][-1]["content"].extend(extra)
        oc.send_prompt = send_prompt

    oc.send_prompt = send


def test_shapes_that_are_not_a_missed_execution_do_not_replay_the_turn(tmp_path: Path):
    """Criterion 2, end to end: a turn that carried every look-alike ends as an ordinary one."""
    orch, oc = _orch(tmp_path, [Turn(text="Invalid input for tool write — looked into that.",
                                     writes={"src/App.tsx": "app\n"},
                                     invalid_calls=["write"], invalid_status="running")])
    _append_parts(oc, [
        {"id": "x1", "callID": "c-x1", "type": "tool", "tool": "bash",
         "state": {"status": "error", "input": {"command": "npm test"}, "error": "exit 1"}},
        {"id": "x2", "callID": "c-x2", "type": "tool", "tool": "repair",
         "state": {"status": "completed", "input": {"tool": "write", "error": "x"}}},
        {"id": "x3", "callID": "c-x3", "type": "tool", "tool": "read",
         "state": {"status": "running", "input": {}}},
    ])

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["ok"] is True and "recoveries" not in done and "cause" not in done
    assert not _of(events, "iterate") and len(oc.sessions) == 1


def test_invalid_twice_stops_with_the_cause_on_the_live_and_the_saved_row(tmp_path: Path):
    """Criterion 3, the failing half: one correction, then the ADR-0069 fields, on both rows."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]), Turn(invalid_calls=["write"])])
    project = orch.project(start_preview=False)

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["ok"] is False and done["decision"] == "broken tool call"
    assert done["cause"] == "invalid_tool_call"
    assert done["stage"] == "implementation"
    assert done["recoveries"] == 1
    assert done["turnId"]
    saved = [r for r in project.app_for_turn().read_history(project.build_conversation)
             if r.get("type") == "done"][-1]
    for key in ("decision", "cause", "stage", "recoveries", "turnId"):
        assert saved[key] == done[key], key
    # One correction was sent, in a fresh session; the third scripted turn was never asked for.
    assert len(oc.sessions) == 2 and len(oc.prompts) == 2
    assert "write call arrived with arguments that did not validate" in oc.prompts[1]["text"]
    message = _of(events, "error")[0]["message"]
    assert "stopped twice in the same step" in message
    assert "write call arrived with arguments that did not validate" in message
    assert "Pick a different model" in message
    for blame in ("gateway", "stopped responding"):
        assert blame not in message, f"the give-up still guesses a cause: {blame!r}"


def test_a_re_delivered_wrapper_is_one_fault(tmp_path: Path, caplog):
    """Criterion 4a: the same call under two part ids — a stream re-delivery — is charged once."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]),
                                Turn(text="Added.", writes={"src/App.tsx": "app\n"})])
    _append_parts(oc, [{**_wrapper(call_id="call-m1-i0"), "id": "p-again"}])

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["ok"] is True and done["recoveries"] == 1
    assert len(oc.sessions) == 2
    assert caplog.text.count("rewritten to OpenCode's invalid tool") == 1


def test_an_unrelated_completion_does_not_retire_the_fault(tmp_path: Path):
    """Criterion 4b: a read landing and prose arriving after the wrapper leave it standing."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"], tools_after=["read", "grep"],
                                     text="Let me look at that file."),
                                Turn(text="Added.", writes={"src/App.tsx": "app\n"})])

    events = list(orch.build_stream("build me a dashboard"))

    assert _of(events, "done")[0]["recoveries"] == 1
    assert len(oc.sessions) == 2


def test_the_intended_tool_landing_later_is_a_proven_recovery(tmp_path: Path):
    """The one completion that DOES retire the fault: the model retried its own write and it ran.

    OpenCode goes on after the wrapper and the model usually tries again. When that lands there is
    nothing for Sage to correct, and a fresh session would cost the turn its context for nothing.
    """
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"], text="Added.",
                                     writes={"src/App.tsx": "app\n"})])

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["ok"] is True and "recoveries" not in done
    assert len(oc.sessions) == 1 and not _of(events, "iterate")


def test_a_replaced_session_does_not_bring_a_second_allowance(tmp_path: Path):
    """Criterion 4c: the fresh session the retry opened is a replacement, not a new turn."""
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]), Turn(invalid_calls=["write"]),
                                Turn(text="Added.", writes={"src/App.tsx": "app\n"})])

    events = list(orch.build_stream("build me a dashboard"))

    assert _of(events, "done")[0]["decision"] == "broken tool call"
    assert len(oc.sessions) == 2 and len(oc.prompts) == 2, "a third send would be a second allowance"


# --- Criterion 5: what beats the recovery, and none of it writes a cause -----------------------


def _no_cause(events: list[dict]) -> None:
    for e in events:
        assert "cause" not in e, e


def test_a_user_stop_beats_the_recovery(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]),
                                Turn(text="Added.", writes={"src/App.tsx": "app\n"})])
    project = orch.project(start_preview=False)
    send_prompt = oc.send_prompt

    def send_then_stop(*args, **kwargs):
        send_prompt(*args, **kwargs)
        project.stop_requested = True

    oc.send_prompt = send_then_stop

    events = list(orch.build_stream("build me a dashboard"))

    assert events[-1]["type"] == "stopped" and not _of(events, "done")
    _no_cause(events)
    assert len(oc.sessions) == 1, "no fresh session was opened over a Stop"
    assert not orch._turn_lock.locked()


def test_an_exhausted_quiet_window_beats_the_recovery(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"]),
                                Turn(text="Added.", writes={"src/App.tsx": "app\n"})])
    orch._build_policy = replace(orch._build_policy, quiet_timeout_seconds=0.01,
                                 open_tool_quiet_timeout_seconds=0.01)
    send_prompt = oc.send_prompt

    def send_then_hang(*args, **kwargs):
        send_prompt(*args, **kwargs)
        oc.stay_running = True

    oc.send_prompt = send_then_hang

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["decision"] == "stalled" and done["turnId"]
    _no_cause(events)
    assert len(oc.sessions) == 1
    assert not orch._turn_lock.locked()


def test_a_provider_terminal_error_beats_the_recovery(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(invalid_calls=["write"],
                                     error={"name": "Error", "data": {"message": "gateway unavailable"}}),
                                Turn(text="Added.", writes={"src/App.tsx": "app\n"})])

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["decision"] == "gateway error" and done["turnId"]
    _no_cause(events)
    assert len(oc.sessions) == 1
    assert not orch._turn_lock.locked()


class _WillNotStop(FakeOpenCode):
    """Runs once, reads idle once so the poll loop leaves, then reads busy for good.

    The reading a recovery cannot accept: the poll loop's exit said idle, the second look says
    writing, and the interrupt changes nothing. Whether the session let go of the tree is unknown,
    and unknown is "still writing".
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.readings = 0

    def is_running(self, session_id: str) -> bool:
        self.readings += 1
        return self.readings != 2

    def interrupt(self, session_id: str) -> None:
        self.interrupted += 1


def test_a_refused_session_stop_keeps_the_lock_and_writes_no_cause(tmp_path: Path):
    """The guard #569 depends on: `cause` present means a fresh attempt may start. Not here."""
    oc = _WillNotStop(tmp_path / "mnt" / "code", [Turn(invalid_calls=["write"]),
                                                  Turn(text="never sent")])
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp_path),
                        gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    orch._build_policy = replace(orch._build_policy, stop_grace_seconds=0.01)

    events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["decision"] == "wedged" and done["ok"] is False and done["turnId"]
    _no_cause(events)
    assert oc.interrupted == 1
    assert len(oc.sessions) == 1 and len(oc.prompts) == 1, "no fresh session over a session that may still write"
    assert orch._turn_wedged is True
    assert orch._turn_lock.locked() is True


# --- Criterion 6: nothing the model emitted reaches a person, the next prompt, or the record ----


def test_nothing_the_model_emitted_reaches_a_person_or_the_record(tmp_path: Path, caplog):
    secret = "sk-live-4f9a"
    # The SDK's message carries the model's raw arguments whole: a nested error payload inside
    # them, and a string cut mid-token where the stream stopped.
    error = ('Invalid input for tool write: Type validation failed: Value: '
             '{"filePath":"src/Secret.tsx","nested":{"error":"Invalid input for tool bash"},'
             '"content":"const TOKEN = \\"' + secret)
    intended = 'write"; DROP TABLE users; --'
    turns = [Turn(invalid_calls=[intended], invalid_error=error),
             Turn(invalid_calls=[intended], invalid_error=error)]
    orch, oc = _orch(tmp_path, turns)
    project = orch.project(start_preview=False)

    with caplog.at_level(logging.INFO):
        events = list(orch.build_stream("build me a dashboard"))

    done = _of(events, "done")[0]
    assert done["decision"] == "broken tool call" and done["cause"] == "invalid_tool_call"
    message = _of(events, "error")[0]["message"]
    assert "The model's tool call arrived with arguments that did not validate" in message
    assert "Your last tool call arrived with arguments that did not validate" in oc.prompts[1]["text"]
    exposed = [json.dumps(events), json.dumps(oc.prompts), caplog.text,
               json.dumps(project.app_for_turn().read_history(project.build_conversation))]
    exposed += [f.read_text(errors="replace") for f in project.record.path.rglob("*")
                if f.is_file() and "node_modules" not in f.parts]
    for token in (secret, "Secret.tsx", "DROP TABLE", "Invalid input for tool bash",
                  "const TOKEN", "Type validation failed"):
        for text in exposed:
            assert token not in text, f"{token!r} leaked"


# --- Criterion 7: every Build `done` row names its turn, capture or no capture ------------------


@pytest.mark.parametrize("capture", [True, False])
def test_every_build_done_row_carries_the_turn_id(tmp_path: Path, monkeypatch, capture: bool):
    if not capture:
        # No diagnostics capture at all, so `history_metadata` has no turn to stamp and the row's
        # `turnId` can only come from the `done` writer itself.
        monkeypatch.setattr(build_diagnostics, "begin", lambda *args, **kwargs: None)
    orch, _oc = _orch(tmp_path, [Turn(text="Added.", writes={"src/App.tsx": "app\n"}),
                                 Turn(invalid_calls=["write"]), Turn(invalid_calls=["write"])])
    project = orch.project(start_preview=False)

    clean = _of(list(orch.build_stream("add a chart")), "done")
    broken = _of(list(orch.build_stream("build me a dashboard")), "done")
    early = _of(list(orch.approve_stream(conversation=project.build_conversation)), "done")

    assert [d["decision"] for d in clean + broken + early] == [
        "typecheck clean", "broken tool call", "no plan to approve"]
    ids = [d["turnId"] for d in clean + broken + early]
    assert all(ids) and len(set(ids)) == 3, ids
    saved = [r for r in project.app_for_turn().read_history(project.build_conversation)
             if r.get("type") == "done"]
    assert [r["turnId"] for r in saved] == ids[:2], "the saved rows carry the same ids"
