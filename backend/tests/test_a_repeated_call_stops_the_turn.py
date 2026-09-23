"""A turn that repeats one failing call is stopped, and told what it repeated (#246).

Live: `ls -R public/data/<project>/uploads` five times in a row, on a folder that was not there.
Every tool event refreshes the quiet clock, so a turn that shells out every few seconds is never
quiet — only the wall-clock ceiling could end it, and it ended it with "this took too long", which
is the one thing that was not wrong with it.

Three in a ROW rather than three anywhere in the turn: a Build turn re-reads a file it has just
edited and the arguments are identical every time, so counting those would brake ordinary work.
Two identical calls with nothing in between have no such reading.
"""
from __future__ import annotations

import json
from dataclasses import replace as _replace
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator import service as svc
from sage.orchestrator.service import (
    Orchestrator,
    _call_fingerprint,
    _repeat_answer,
    _repeat_fingerprint,
    _repeat_message,
    _RepeatBrake,
)
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

# ---- the counter ------------------------------------------------------------------------------

def _fp(tool: str, **inp) -> str:
    return _call_fingerprint(tool, inp)


def test_the_same_call_three_times_running_trips_the_brake():
    brake = _RepeatBrake()
    probe = _fp("bash", command="ls -R public/data/x/uploads")
    assert brake.saw(probe, "bash (ls -R …)") is False
    assert brake.saw(probe, "bash (ls -R …)") is False
    assert brake.saw(probe, "bash (ls -R …)") is True
    assert brake.label == "bash (ls -R …)"


def test_anything_else_in_between_clears_the_run():
    """The whole of why this counts a RUN and not a total. An agent that reads a file, edits it and
    reads it back is working; the read's arguments are identical both times."""
    brake = _RepeatBrake()
    read = _fp("read", filePath="src/App.tsx")
    edit = _fp("edit", filePath="src/App.tsx", oldString="a", newString="b")
    for _ in range(4):
        assert brake.saw(read, "read (src/App.tsx)") is False
        assert brake.saw(edit, "edit (src/App.tsx)") is False


def test_two_writes_to_one_file_are_not_the_same_call():
    """Why the key is the whole input and not `_tool_label`, which reads a named subset: both of
    these are labelled `write (src/App.tsx)`."""
    brake = _RepeatBrake()
    first = _fp("write", filePath="src/App.tsx", content="one")
    second = _fp("write", filePath="src/App.tsx", content="two")
    third = _fp("write", filePath="src/App.tsx", content="three")
    assert brake.saw(first, "write (src/App.tsx)") is False
    assert brake.saw(second, "write (src/App.tsx)") is False
    assert brake.saw(third, "write (src/App.tsx)") is False


def test_marker_shell_metadata_variants_are_one_executable_repeat_family():
    brake = _RepeatBrake()
    calls = [
        {"command": "true", "description": "local data withheld A"},
        {"command": "true", "description": "local data withheld B"},
        {"command": "true", "description": "local data withheld A"},
    ]
    answers = [brake.saw(_repeat_fingerprint("bash", args), "bash (true)",
                         tool="bash", arguments=args) for args in calls]
    assert answers == [False, False, True]


def test_marker_repeat_family_keeps_commands_and_legitimate_read_ranges_distinct():
    brake = _RepeatBrake()
    shells = [
        {"command": "true", "description": "local data withheld A"},
        {"command": "false", "description": "local data withheld B"},
        {"command": "true", "description": "local data withheld A"},
    ]
    assert all(not brake.saw(_repeat_fingerprint("bash", args), "bash", arguments=args)
               for args in shells)
    reads = [{"filePath": "same.csv", "offset": n, "limit": 10} for n in (0, 10, 0)]
    assert all(not brake.saw(_repeat_fingerprint("read", args), "read", arguments=args)
               for args in reads)


def test_ordinary_shell_metadata_keeps_full_input_identity():
    brake = _RepeatBrake()
    calls = [
        {"command": "true", "description": "step A"},
        {"command": "true", "description": "step B"},
        {"command": "true", "description": "step A"},
    ]
    assert all(not brake.saw(_repeat_fingerprint("bash", args), "bash", arguments=args)
               for args in calls)


def test_the_key_never_carries_what_it_keyed_on():
    """A `write`'s input is the file's contents, and a counter is not a place to keep them."""
    key = _fp("write", filePath="src/App.tsx", content="SECRET_ROWS = [1, 2, 3]")
    assert "SECRET_ROWS" not in key
    assert "App.tsx" not in key


def test_a_call_with_no_arguments_yet_neither_counts_nor_clears():
    """Chat sees a bash call twice: `tool.called` with its input, `shell.started` with none."""
    brake = _RepeatBrake()
    probe = _fp("bash", command="ls -R uploads")
    assert _call_fingerprint("bash", None) == ""
    assert brake.saw(probe, "bash (ls -R uploads)") is False
    assert brake.saw("", "bash (ls -R uploads)") is False
    assert brake.saw(probe, "bash (ls -R uploads)") is False
    assert brake.saw("", "bash (ls -R uploads)") is False
    assert brake.saw(probe, "bash (ls -R uploads)") is True


def test_a_nameless_tool_keys_nothing():
    assert _call_fingerprint("", {"command": "ls"}) == ""


def test_a_call_with_no_arguments_still_breaks_a_run():
    """A tool that takes no arguments is still a call. A run an argument-less call could not break
    would read `bash X` / `todoread` / `bash X` / `todoread` / `bash X` as three in a row."""
    brake = _RepeatBrake()
    probe = _fp("bash", command="ls -R uploads")
    bare = _call_fingerprint("todoread", {})
    assert bare != ""
    for _ in range(3):
        assert brake.saw(probe, "bash (ls -R uploads)") is False
        assert brake.saw(bare, "todoread") is False


# ---- the answer -------------------------------------------------------------------------------

def _transcript(*states: dict, tool: str = "bash") -> list[dict]:
    return [{"id": f"m{i}", "type": "assistant",
             "content": [{"id": f"m{i}-t", "type": "tool", "tool": tool, "state": s}]}
            for i, s in enumerate(states)]


def test_a_failed_file_call_answers_with_its_error():
    probe = {"filePath": "/mnt/data/combined.txt"}
    msgs = _transcript({"status": "error", "input": probe,
                        "error": "File not found: /mnt/data/combined.txt"}, tool="read")
    assert _repeat_answer(msgs, _call_fingerprint("read", probe)) == (
        "File not found: /mnt/data/combined.txt")


def test_a_shells_error_is_never_quoted():
    """"Authored" is a property of the TOOL, not the field. A file tool reports failure in words
    somebody wrote; a shell reports it by PRINTING, so its error carries whatever the program put
    on stderr — the person's rows as surely as its output would be, on their way into a committed
    `history.jsonl`. #246's own example pays this: it looped through bash."""
    probe = {"command": "python -c 'print(df.head())' && exit 1"}
    msgs = _transcript({"status": "error", "input": probe,
                        "error": "name,ssn\nA,123-45-6789\nTraceback (most recent call last):"})
    assert _repeat_answer(msgs, _call_fingerprint("bash", probe)) == ""


def test_an_unlisted_tool_is_never_quoted():
    """An unknown tool has made no promise about what its error carries."""
    probe = {"query": "select * from patients"}
    msgs = _transcript({"status": "error", "input": probe, "error": "row 1: A, 123-45-6789"},
                       tool="live_read_table")
    assert _repeat_answer(msgs, _call_fingerprint("live_read_table", probe)) == ""


def test_a_calls_output_is_never_quoted_however_short_it_is():
    """The answer is the failure sentence a tool wrote, never the answer itself. `history.jsonl`
    is committed and travels to anyone who pulls the Project, so a slice of a `read`'s output in
    this sentence is rows at rest in the repo — the thing `_tool_label` refuses two functions up.
    A short output is not a safe one: `head -2 patients.csv` fits in a tweet."""
    probe = {"filePath": "patients.csv"}
    msgs = _transcript({"status": "completed", "input": probe,
                        "output": "name,ssn\nA,123-45-6789"}, tool="read")
    assert _repeat_answer(msgs, _call_fingerprint("read", probe)) == ""


def test_a_long_error_is_clipped_rather_than_dropped():
    """Authored does not mean short — a refusal arrives as a whole nested body — and those are the
    noisy cases where the first two hundred characters are most worth having."""
    probe = {"filePath": "patients.csv"}
    msgs = _transcript({"status": "error", "input": probe, "error": "x" * 400}, tool="read")
    said = _repeat_answer(msgs, _call_fingerprint("read", probe))
    assert len(said) == svc._REPEAT_ANSWER_MAX
    assert said.endswith("…")


def test_an_answer_that_is_not_there_is_not_invented():
    probe = {"command": "ls"}
    assert _repeat_answer(_transcript({"status": "completed", "input": probe}),
                          _call_fingerprint("bash", probe)) == ""
    assert _repeat_answer([], "abc") == ""
    assert _repeat_answer(None, "abc") == ""


def test_the_answer_comes_from_the_repeated_call_and_not_its_neighbour():
    mine = {"filePath": "uploads/a.csv"}
    theirs = {"filePath": "README"}
    msgs = _transcript({"status": "error", "input": mine, "error": "no such directory"},
                       {"status": "error", "input": theirs, "error": "is a directory"},
                       tool="read")
    assert _repeat_answer(msgs, _call_fingerprint("read", mine)) == "no such directory"


def test_the_sentence_names_the_call_and_its_answer():
    said = _repeat_message("bash (ls -R uploads)", "no such directory")
    assert "bash (ls -R uploads)" in said
    assert "no such directory" in said
    # The number the brake actually uses, not a word that drifts away from it.
    assert str(svc._REPEAT_LIMIT) in said
    assert "stopped making progress" not in said
    bare = _repeat_message("bash (ls -R uploads)", "")
    assert "bash (ls -R uploads)" in bare
    assert "answered" not in bare


# ---- the two turn loops -----------------------------------------------------------------------

class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    return t


def _orch(tmp: Path, oc: FakeOpenCode, verdict: str) -> Orchestrator:
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(verdict),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch


_REPEATS_IN_ONE_BATCH = 4
_PROBE = {"command": "ls -R public/data/sage-x/uploads"}
_ANSWER = "ls: public/data/sage-x/uploads: No such file or directory"


def _looping_part(n: int) -> dict:
    return {"id": f"loop-{n}", "type": "tool", "tool": "bash",
            "state": {"status": "error", "input": dict(_PROBE), "error": _ANSWER}}


class LoopingOpenCode(FakeOpenCode):
    """A session that answers every poll with one more identical shell call.

    `poll_cap` is what makes this a test rather than a hang: before the brake existed this loop
    ran to the wall-clock ceiling, and a test that never returns reports nothing at all.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 stops: bool = True) -> None:
        super().__init__(workspace, turns)
        self.stay_running = True
        self.emitted = 0
        self.polls = 0
        self.stops = stops
        self.orch: Orchestrator | None = None
        self.locked_at_interrupt: bool | None = None
        self.interrupt_hook = None

    def is_running(self, session_id: str) -> bool:
        self.polls += 1
        assert self.polls <= 200, "the build loop never braked on a repeating call"
        return self.stay_running

    def interrupt(self, session_id: str) -> None:
        # Read the lock as the stop happens: a looping session is a BUSY one, and the whole
        # ordering rule is that the stop runs while the turn still owns the tree.
        if self.orch is not None:
            self.locked_at_interrupt = self.orch.turn_busy()
        if self.interrupt_hook is not None:
            self.interrupt_hook()
        if self.stops:
            super().interrupt(session_id)   # counts the call and goes idle
        else:
            self.interrupted += 1           # accepted, and nothing changes

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        if self._next > 0:
            self.emitted += 1
            self._by_session.setdefault(session_id, []).append(
                {"id": f"loop-m{self.emitted}", "type": "assistant",
                 "content": [_looping_part(self.emitted)]})
        return super().messages(session_id, limit=limit)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    # A second of grace rather than thirty. `time.sleep` is a no-op above, so the stop's wait for
    # an idle reading spins on the real clock — the rule under test is whether a stop that never
    # confirms releases the tree, and that is the same rule at either scale.
    monkeypatch.setattr(svc, "_BUILD_STOP_GRACE_S", 1.0)


def test_a_build_turn_that_repeats_one_call_is_stopped_and_told_what_repeated(tmp_path: Path):
    oc = LoopingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart the uploads"))

    card = [e for e in events if e.get("type") == "build-stalled"]
    assert len(card) == 1
    assert "ls -R public/data/sage-x/uploads" in card[0]["message"]
    # Named, not quoted: #246 looped through bash, whose error is whatever the program printed.
    assert _ANSWER not in card[0]["message"]
    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    # The session is stopped rather than left running against the working tree.
    assert oc.interrupted == 1
    # It braked on the third, not after twenty.
    assert oc.emitted <= svc._REPEAT_LIMIT + 1
    # Nothing was built, and this turn owns the build's outcome, so the plan it came from is kept
    # rather than archived under the person's Try again. A PHASE owns no such thing — hence the
    # `owns_turn` gate this pins the other side of.
    assert orch._turn_gave_up is True


def test_a_build_turn_that_is_not_repeating_itself_is_left_alone(tmp_path: Path):
    """One call per poll, each with different arguments — the healthy long turn."""

    class BusyOpenCode(LoopingOpenCode):
        def is_running(self, session_id: str) -> bool:
            return self._next > 0 and self.emitted < 8

        def messages(self, session_id: str, *, limit: int | None = None):
            if self._next > 0 and self.emitted < 8:
                self.emitted += 1
                n = self.emitted
                self._by_session.setdefault(session_id, []).append(
                    {"id": f"busy-m{n}", "type": "assistant",
                     "content": [{"id": f"busy-{n}", "type": "tool", "tool": "bash",
                                  "state": {"status": "completed",
                                            "input": {"command": f"head -2 file{n}.csv"},
                                            "output": "a,b"}}]})
            return FakeOpenCode.messages(self, session_id, limit=limit)

    oc = BusyOpenCode(tmp_path / "mnt" / "code",
                      [Turn(text="done", writes={"src/chart.tsx": "chart\n"})])
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert [e for e in events if e.get("type") == "build-stalled"] == []
    assert next(e for e in events if e.get("type") == "done")["ok"] is True


def test_a_build_marker_metadata_cycle_stops_before_a_fourth_true_call(tmp_path: Path):
    class MarkerLoopOpenCode(LoopingOpenCode):
        def messages(self, session_id: str, *, limit: int | None = None):
            if self._next > 0:
                self.emitted += 1
                variant = "A" if self.emitted % 2 else "B"
                self._by_session.setdefault(session_id, []).append({
                    "id": f"marker-{self.emitted}", "type": "assistant", "content": [{
                        "id": f"marker-{self.emitted}-tool", "type": "tool", "tool": "bash",
                        "state": {"status": "completed", "input": {
                            "command": "true",
                            "description": f"local data withheld {variant}",
                        }, "output": ""},
                    }],
                })
            return FakeOpenCode.messages(self, session_id, limit=limit)

    oc = MarkerLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart the uploads"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    assert oc.emitted == svc._REPEAT_LIMIT
    assert oc.interrupted == 1


# ---- Chat -------------------------------------------------------------------------------------

class _RepeatingStream:
    """A live /event stream that opens the same shell call over and over, then stays open.

    A bash call arrives twice — `tool.called` carrying its command inside `input`, and
    `shell.started` carrying it at the top level — and both reach this loop as `called` under one
    call id. Scripted here so the brake has to survive the shape it actually meets.
    """

    def __init__(self, repeats: int = 6, *, shell_first: bool = False,
                 closes: int | None = None) -> None:
        import threading

        from sage.driver.agent_driver import AgentEvent
        closes = repeats if closes is None else closes
        self._events = []
        for i in range(repeats):
            opens = [
                AgentEvent(kind="tool_run", payload={
                    "tool": "bash", "call_id": f"c{i}", "status": "called",
                    "input": dict(_PROBE)}),
                AgentEvent(kind="tool_run", payload={
                    "tool": "bash", "call_id": f"c{i}", "status": "called",
                    "command": _PROBE["command"]}),
            ]
            self._events.extend(reversed(opens) if shell_first else opens)
            if i < closes:
                self._events.append(AgentEvent(kind="tool_run", payload={
                    "tool": "", "call_id": f"c{i}", "status": "success"}))
        self.delivered = threading.Event()
        self._closed = threading.Event()

    def __iter__(self):
        for ev in self._events:
            if self._closed.is_set():
                break
            yield ev
        self.delivered.set()
        self._closed.wait(5)

    def close(self):
        self._closed.set()


class ChatLoopOpenCode(FakeOpenCode):
    """A Chat session that streams the repeat and whose transcript carries what it answered."""

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, **stream) -> None:
        super().__init__(workspace, turns)
        self.stay_running = True
        self.stops = True
        self.stop_hook = None
        self.stream = _RepeatingStream(**stream)

    def interrupt(self, session_id: str) -> None:
        if self.stop_hook is not None:
            self.stop_hook()
        if self.stops:
            super().interrupt(session_id)
        else:
            self.interrupted += 1       # accepted, and nothing changes

    def session_events(self, session_id, *, directory=None):
        return self.stream

    def messages(self, session_id: str, *, limit: int | None = None):
        return [{"id": "m1", "type": "assistant", "content": [_looping_part(1)]}]


def test_a_chat_turn_that_repeats_one_call_is_stopped_and_told_what_repeated(tmp_path: Path):
    oc = ChatLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "whats in my uploads"))

    err = next(e for e in events if e.get("type") == "error")
    assert "ls -R public/data/sage-x/uploads" in err["message"]
    assert _ANSWER not in err["message"]    # bash is not a tool whose error is quoted
    # The sentence that sent somebody after the wrong thing, on a turn that had plenty to say.
    assert "stopped making progress" not in err["message"]
    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    assert oc.interrupted == 1


def test_a_stopped_chat_turn_says_what_a_read_refused_before_it_stuck(tmp_path: Path):
    """#488. The turn asked `live_read_query` for "gong" — a system, not the bound store — was
    refused by name, went to the shell for it, and ran `bash` three times. The brake said
    "stopped: bash (true)", which the person cannot act on. The refusal named the thing to add.

    The refused read is made the real way, through `live_read_call` with the token this turn
    minted — read out of the prompt the moment `send_prompt` receives it, which is after the mint
    and before the first repeated call closes. What is asserted is the sentence the person is
    handed at the end: the refusal rides on it.
    """
    import re

    class _RefusedFirst(ChatLoopOpenCode):
        def send_prompt(self, *args, **kwargs):
            super().send_prompt(*args, **kwargs)
            token = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", self.prompts[-1]["text"]).group(1)
            reply = orch.live_read_call({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "live_read_query",
                           "arguments": {"token": token, "source": "gong", "sql": "SELECT 1"}}})
            self.refused_with = reply["result"]["content"][0]["text"]

    oc = _RefusedFirst(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "report of active DMM users across gong and sfdc"))

    assert "gong isn't in this conversation" in oc.refused_with
    err = next(e for e in events if e.get("type") == "error")
    assert "ran the same step" in err["message"]
    assert "Before that, a read was refused: gong isn't in this conversation" in err["message"]
    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"


def test_a_stopped_chat_turn_with_no_refusal_carries_none(tmp_path: Path):
    """The other half of the plant: the sentence is added only when a read was refused. A turn
    that simply looped says what it said before #488, and nothing about reads."""
    oc = ChatLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "whats in my uploads"))

    err = next(e for e in events if e.get("type") == "error")
    assert "a read was refused" not in err["message"]


def test_the_refusal_record_is_about_one_turn(tmp_path: Path):
    """Reset when the next token is minted, the way the read count is — a refusal from an earlier
    turn must not be pinned on a later one that never asked for that table."""
    oc = ChatLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]
    orch._live_read_refused[tid] = "gong isn't in this conversation (stale)"

    events = list(orch.chat_stream(tid, "whats in my uploads"))

    err = next(e for e in events if e.get("type") == "error")
    assert "stale" not in err["message"]


def test_chat_counts_calls_that_answered_not_calls_that_started(tmp_path: Path, monkeypatch):
    """Three opens and two closes is two repeats, not three.

    Counting the open killed the third call in flight, and a turn stopped before its third answer
    has no third answer to read back — which loses the half of the sentence that is worth stopping
    for. The turn below still ends, on the quiet window it was always going to end on.
    """
    monkeypatch.setattr(svc, "_CHAT_QUIET_TIMEOUT_S", 0.1)
    monkeypatch.setattr(svc, "_CHAT_TOOL_QUIET_TIMEOUT_S", 0.5)
    oc = ChatLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")],
                          repeats=3, closes=2)
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "whats in my uploads"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "timeout"


def test_the_brake_counts_the_same_either_way_a_bash_call_opens(tmp_path: Path):
    """`shell.started` carries a top-level command, `tool.called` OpenCode's whole input, and the
    two hash differently — so whichever lands first decides the key. Counting compares those keys
    with each other and never with the transcript's, so the run is the same length either way."""
    oc = ChatLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")], shell_first=True)
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "whats in my uploads"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    err = next(e for e in events if e.get("type") == "error")
    assert "ls -R public/data/sage-x/uploads" in err["message"]


def test_a_call_that_has_not_answered_yet_is_walked_past():
    """The newest matching call is the one Sage just stopped. Its answer is in the repeats before
    it, and giving up at the first match threw away the whole reason for reading at all."""
    probe = {"filePath": "uploads/a.csv"}
    msgs = _transcript({"status": "error", "input": probe, "error": "no such directory"},
                       {"status": "running", "input": probe}, tool="read")
    assert _repeat_answer(msgs, _call_fingerprint("read", probe)) == "no such directory"


def test_a_part_with_no_tool_name_is_keyed_the_way_the_build_loop_keys_it():
    """Build falls back to the part's type, and reading it back falls back the same way — so the
    two agree about which part a fingerprint belongs to. Nothing is quoted from it either way: an
    unnamed tool is not on the allowlist, which is the point of the allowlist."""
    probe = {"command": "ls"}
    msgs = [{"id": "m0", "type": "assistant",
             "content": [{"id": "p0", "type": "tool", "state": {
                 "status": "error", "input": probe, "error": "nothing here"}}]}]
    assert _repeat_answer(msgs, _call_fingerprint("tool", probe)) == ""


def test_a_looping_session_that_will_not_stop_keeps_the_working_tree(tmp_path: Path):
    """The stop, not the interrupt call returning, is what releases the tree.

    A looping session is a BUSY session, so it is the one least likely to honour a posted interrupt
    promptly — and a lock handed on under a session that may still be writing is two turns on one
    working tree, which is the thing the lock exists to prevent. So a stop that will not confirm
    keeps the lock, keeps the read-only and web pins armed, and says the workspace needs a restart.
    """
    oc = LoopingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")], stops=False)
    orch = _orch(tmp_path, oc, "BUILD")
    oc.orch = orch

    events = list(orch.build_stream("chart the uploads"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "wedged"
    assert "wouldn't stop" in next(
        e for e in events if e.get("type") == "build-stalled")["message"]
    assert orch._turn_lock.locked() is True
    # The stop ran while the turn still owned the tree, never after handing it on.
    assert oc.locked_at_interrupt is True


def test_a_turn_that_already_finished_is_not_braked_on_its_last_batch(tmp_path: Path):
    """The poll that ends a turn is also the one that delivers the last of its parts.

    Three identical calls arriving together in that final batch is a turn OpenCode had already
    finished — reporting it as a loop would turn a build that worked into a dead end.
    """

    class FinishedOpenCode(LoopingOpenCode):
        def is_running(self, session_id: str) -> bool:
            self.polls += 1
            assert self.polls <= 200
            return self.polls <= 1      # appeared, then done

        def messages(self, session_id: str, *, limit: int | None = None):
            if self._next > 0 and self.polls > 1 and not self.emitted:
                self.emitted = 1
                self._by_session.setdefault(session_id, []).append(
                    {"id": "final", "type": "assistant",
                     "content": [_looping_part(n) for n in range(_REPEATS_IN_ONE_BATCH)]})
            return FakeOpenCode.messages(self, session_id, limit=limit)

    oc = FinishedOpenCode(tmp_path / "mnt" / "code",
                          [Turn(text="done", writes={"src/chart.tsx": "chart\n"})])
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert [e for e in events if e.get("type") == "build-stalled"] == []
    assert next(e for e in events if e.get("type") == "done")["ok"] is True


# ---- a loop of DIFFERENT shell calls -------------------------------------------------------
# Measured 2026-09-21: 434 `bash` calls in one approve turn, 398 of them `echo w1` … `echo w252`,
# 0 writes, 23 minutes. Every call differed, so the brake above never saw a repeat. The count is
# the only thing that sees this shape.

_ECHOES_IN_ONE_BATCH = 10


class CountingOpenCode(LoopingOpenCode):
    """A session whose every shell call is different: `echo w1`, `echo w2`, … — the measured loop.

    Ten per poll, for the reason `_REPEATS_IN_ONE_BATCH` batches: the build loop waits a real
    second between polls, and one call per poll would make the forty-call test forty seconds.
    `stop_after` ends the turn cleanly at that many calls, so the same session stands in for a
    long turn that finishes. No part here is ever an edit tool: whether the app changed is the
    Turn's `writes`, which the fake lands at prompt time the way a `printf > file` would — a
    write no tool reported, the case the cap must not mistake for a loop.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 stop_after: int | None = None) -> None:
        super().__init__(workspace, turns)
        self.calls = 0
        self.stop_after = stop_after

    def _finished(self) -> bool:
        return self.stop_after is not None and self.calls >= self.stop_after

    def is_running(self, session_id: str) -> bool:
        self.polls += 1
        assert self.polls <= 200, "the build loop never capped a counting session"
        return self.stay_running and not self._finished()

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        if self._next > 0 and not self._finished():
            parts = []
            while len(parts) < _ECHOES_IN_ONE_BATCH and not self._finished():
                self.calls += 1
                parts.append({"id": f"echo-{self.calls}", "type": "tool", "tool": "bash",
                              "state": {"status": "completed",
                                        "input": {"command": f"echo w{self.calls}"},
                                        "output": f"w{self.calls}"}})
            self.emitted += 1
            self._by_session.setdefault(session_id, []).append(
                {"id": f"count-m{self.emitted}", "type": "assistant", "content": parts})
        return FakeOpenCode.messages(self, session_id, limit=limit)


def _shell_cap_fired(events: list[dict]) -> list[dict]:
    return [e for e in events
            if e.get("type") == "build-stalled" and "shell commands" in e.get("message", "")]


def test_a_build_turn_that_runs_forty_different_shell_calls_and_changes_nothing_is_stopped(
        tmp_path: Path):
    oc = CountingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart the uploads"))

    card = _shell_cap_fired(events)
    assert len(card) == 1
    # The number is the finding — it is the one thing the person could not see.
    assert str(svc._BASH_CALLS_MAX) in card[0]["message"]
    # Its own word on the status line: nothing repeated, so "Stopped — repeated" would be false.
    assert next(e for e in events if e.get("type") == "done")["decision"] == "looped"
    assert oc.interrupted == 1
    # Stopped in the poll that reached the cap, not some polls later.
    assert svc._BASH_CALLS_MAX <= oc.calls < svc._BASH_CALLS_MAX + _ECHOES_IN_ONE_BATCH
    assert orch._turn_gave_up is True


def test_a_build_turn_one_short_of_the_shell_cap_is_not_stopped_for_it(tmp_path: Path,
                                                                       monkeypatch):
    """Thirty-nine calls and nothing changed is the same turn as forty, one poll earlier. Only
    the count separates them, so this is the plant that proves the count is what fires."""
    # A turn that ends with no edit is nudged to implement, and each nudge against a session
    # with nothing left to say costs the poll loop's never-appeared deadline. Not what this
    # plant is about, so no nudges — the same switch test_phased_build.py throws.
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    oc = CountingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")],
                          stop_after=svc._BASH_CALLS_MAX - 1)
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert _shell_cap_fired(events) == []
    assert next(e for e in events if e.get("type") == "done")["decision"] != "looped"
    assert oc.calls == svc._BASH_CALLS_MAX - 1


def test_a_build_turn_whose_app_has_changed_is_not_stopped_for_its_shell_calls(tmp_path: Path):
    """A turn that writes, then builds, then fixes runs many shell commands, and all of them are
    work. The write here is one no edit tool reported — the fake lands it the way a shell
    heredoc would — because that is how the healthy build measured on 2026-09-21 wrote its files,
    and a cap keyed on the edit tools alone would have stopped it."""
    oc = CountingOpenCode(tmp_path / "mnt" / "code",
                          [Turn(text="done", writes={"src/chart.tsx": "chart\n"})],
                          stop_after=svc._BASH_CALLS_MAX + 5)
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert _shell_cap_fired(events) == []
    assert [e for e in events if e.get("type") == "build-stalled"] == []
    assert next(e for e in events if e.get("type") == "done")["ok"] is True
    assert oc.calls == svc._BASH_CALLS_MAX + 5       # it really did go past the cap


def test_a_chat_turn_with_no_stream_is_braked_off_the_transcript(tmp_path: Path, monkeypatch):
    """A tap that opens and then delivers nothing all turn reads as `ok` for ever — it is how a
    wrong session directory shows up at all — so it is the blindest turn Chat has, and the one
    most able to ride to the ceiling unreported. The transcript is the only source there."""

    class SilentStream:
        ok = True
        seen_any = False

        def __init__(self) -> None:
            import threading
            self.delivered = threading.Event()
            self.delivered.set()
            self._closed = threading.Event()

        def __iter__(self):
            self._closed.wait(5)
            return iter(())

        def close(self):
            self._closed.set()

    class SilentTapOpenCode(LoopingOpenCode):
        def __init__(self, workspace: Path, turns=None) -> None:
            super().__init__(workspace, turns)
            self.stream = SilentStream()

        def session_events(self, session_id, *, directory=None):
            return self.stream

        def messages(self, session_id: str, *, limit: int | None = None):
            if self._next > 0 and self.emitted < _REPEATS_IN_ONE_BATCH:
                self.emitted += 1
                self._by_session.setdefault(session_id, []).append(
                    {"id": f"silent-{self.emitted}", "type": "assistant",
                     "content": [_looping_part(self.emitted)]})
            return FakeOpenCode.messages(self, session_id, limit=limit)

    monkeypatch.setattr(svc, "_CHAT_QUIET_TIMEOUT_S", 30.0)
    monkeypatch.setattr(svc, "_CHAT_TOOL_QUIET_TIMEOUT_S", 30.0)
    oc = SilentTapOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "whats in my uploads"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    err = next(e for e in events if e.get("type") == "error")
    assert "ls -R public/data/sage-x/uploads" in err["message"]


def test_a_looping_chat_session_that_will_not_stop_is_still_cleaned_up_after(tmp_path: Path,
                                                                             caplog, monkeypatch):
    """The turn's `finally` commits and pushes the tree whatever this block decides, so a withhold
    that does not run is not a withhold deferred — it is table rows committed into the Project
    repo. Racing a session that may still be writing risks missing a LATE write; not running loses
    every write there is. It is said in the log, and then it runs."""
    import logging

    ran: list[str] = []
    monkeypatch.setattr(svc, "withhold_table_rows",
                        lambda *a, **k: ran.append("withhold") or [])
    monkeypatch.setattr(svc, "revert_denied_writes",
                        lambda *a, **k: ran.append("revert") or [])
    oc = ChatLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    oc.stops = False
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.ERROR, logger="sage.orchestrator"):
        events = list(orch.chat_stream(tid, "whats in my uploads"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    assert any("would not confirm it stopped" in r.getMessage() for r in caplog.records)
    assert ran == ["revert", "withhold"]


def test_a_stop_pressed_as_the_brake_trips_does_not_outlive_the_turn(tmp_path: Path):
    """`stop_build` can set the flag after this iteration's top-of-loop check, and the turn that
    would have consumed it in `handle_stop` is the one the brake is ending. Left standing, the
    NEXT turn answers "stopped" without running a step."""
    oc = LoopingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "BUILD")
    project = orch.project(start_preview=False)
    # Pressed in the window the comment names: after this iteration's top-of-loop check, while the
    # brake is stopping the session. The turn that would have consumed it never comes back.
    oc.interrupt_hook = lambda: setattr(project, "stop_requested", True)

    events = list(orch.build_stream("chart the uploads"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    assert project.stop_requested is False


def test_a_repeating_file_read_reaches_the_card_with_what_it_answered(tmp_path: Path):
    """The other half of the allowlist. A file tool's failure is a sentence somebody wrote, and it
    is most of the value of stopping: "it ran the same step 3 times" says Sage noticed, and
    "File not found: …" says the folder is not there."""
    missing = "/mnt/data/sage-x/uploads/tickets.csv"

    class ReadLoopOpenCode(LoopingOpenCode):
        def messages(self, session_id: str, *, limit: int | None = None):
            if self._next > 0:
                self.emitted += 1
                self._by_session.setdefault(session_id, []).append(
                    {"id": f"r{self.emitted}", "type": "assistant",
                     "content": [{"id": f"r{self.emitted}-t", "type": "tool", "tool": "read",
                                  "state": {"status": "error", "input": {"filePath": missing},
                                            "error": f"File not found: {missing}"}}]})
            return FakeOpenCode.messages(self, session_id, limit=limit)

    oc = ReadLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart the tickets"))

    said = next(e for e in events if e.get("type") == "build-stalled")["message"]
    assert f"File not found: {missing}" in said
    assert "read (" in said


def test_a_stop_pressed_while_chat_brakes_does_not_outlive_the_turn(tmp_path: Path):
    """The window is wider here than in Build: the stop above polls for a grace period, and
    somebody watching Chat repeat itself has all of it to press Stop."""
    oc = ChatLoopOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "CHAT")
    project = orch.project(start_preview=False)
    oc.stop_hook = lambda: setattr(project, "stop_requested", True)
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "whats in my uploads"))

    assert next(e for e in events if e.get("type") == "done")["decision"] == "repeat_brake"
    assert project.stop_requested is False


def test_the_stream_taking_over_does_not_inherit_the_transcripts_count(tmp_path: Path,
                                                                      monkeypatch):
    """A drain can come back empty at the top of a poll whose transcript read then counts a call
    whose own close frame is still in the queue. Counting that call again when it arrives would
    stop a healthy turn after two repeats, not three.

    Deterministic, not timed: the stream is held shut until the transcript has been read once.
    """
    import threading

    from sage.driver.agent_driver import AgentEvent

    read_once = threading.Event()

    class LateStream:
        """Silent until the transcript has been read, then the close of the call it counted."""

        ok = True

        def __init__(self) -> None:
            self.seen_any = False
            self.delivered = threading.Event()
            self._closed = threading.Event()

        def __iter__(self):
            read_once.wait(5)
            self.seen_any = True
            # Call A IN FULL — the queue held its open as well as its close while the drain at
            # the top of that poll came back empty — and then one more identical call. Two real
            # calls; without the handover the stream re-counts A and this reads as three.
            yield AgentEvent(kind="tool_run", payload={
                "tool": "bash", "call_id": "A", "status": "called", "input": dict(_PROBE)})
            yield AgentEvent(kind="tool_run", payload={
                "tool": "", "call_id": "A", "status": "success"})
            yield AgentEvent(kind="tool_run", payload={
                "tool": "bash", "call_id": "B", "status": "called", "input": dict(_PROBE)})
            yield AgentEvent(kind="tool_run", payload={
                "tool": "", "call_id": "B", "status": "success"})
            self.delivered.set()
            self._closed.wait(5)

        def close(self):
            self._closed.set()

    class LateStreamOpenCode(LoopingOpenCode):
        def __init__(self, workspace: Path, turns=None) -> None:
            super().__init__(workspace, turns)
            self.stream = LateStream()

        def session_events(self, session_id, *, directory=None):
            return self.stream

        def messages(self, session_id: str, *, limit: int | None = None):
            if self._next > 0 and not self.emitted:
                self.emitted = 1
                self._by_session.setdefault(session_id, []).append(
                    {"id": "A", "type": "assistant", "content": [_looping_part(1)]})
                read_once.set()
            return FakeOpenCode.messages(self, session_id, limit=limit)

    monkeypatch.setattr(svc, "_CHAT_QUIET_TIMEOUT_S", 30.0)
    monkeypatch.setattr(svc, "_CHAT_TOOL_QUIET_TIMEOUT_S", 30.0)
    oc = LateStreamOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "CHAT")
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "whats in my uploads"))

    assert [e for e in events if e.get("decision") == "repeat_brake"] == []


# --- THE THIRD SHAPE: the writes themselves keep being refused (#494) ---------------------------
# Measured 2026-09-21, gemini-3.7-flash on BOTH model slots: 32 `apply_patch` calls in 13 minutes,
# 25 refused by OpenCode's parser, 7 landed. The tree hash moved the whole time, so neither cap
# above could see it — `agent_wrote()` was true — and the rescue's model swap was a no-op because
# the model it swaps TO is the model that is failing. Only the ratio separates this from a build.

_REFUSAL = ("apply_patch verification failed: Error: Invalid patch format: missing Begin/End "
            "markers")
_WRITES_IN_ONE_BATCH = 5


class PatchRefusingOpenCode(LoopingOpenCode):
    """Emits `apply_patch` parts: `refused` of them error, `landed` of them completed.

    Both counts matter, so both are scripted. The parts carry DIFFERENT patch text every time —
    `_RepeatBrake` keys on the arguments, and a loop that would trip the brake proves nothing about
    a cap. Same reason `CountingOpenCode` numbers its echoes.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 refused: int = 0, landed: int = 0) -> None:
        super().__init__(workspace, turns)
        self.refused_left = refused
        self.landed_left = landed
        self.calls = 0

    def _finished(self) -> bool:
        return not self.refused_left and not self.landed_left

    def is_running(self, session_id: str) -> bool:
        self.polls += 1
        assert self.polls <= 200, "the build loop never capped a turn whose writes were refused"
        return self.stay_running and not self._finished()

    def _part(self) -> dict:
        self.calls += 1
        n = self.calls
        # Alternate, so a cap that fired on a run of refusals rather than on the count would not
        # fire here — and so `landed > refused` cases really interleave.
        refuse = self.refused_left and (not self.landed_left or n % 2)
        if refuse:
            self.refused_left -= 1
            return {"id": f"p{n}", "type": "tool", "tool": "apply_patch",
                    "state": {"status": "error", "input": {"patchText": f"*** Begin Patch {n}"},
                              "error": _REFUSAL}}
        self.landed_left -= 1
        return {"id": f"p{n}", "type": "tool", "tool": "apply_patch",
                "state": {"status": "completed", "input": {"patchText": f"*** Begin Patch {n}"},
                          "output": f"Success. Updated the following files:\nM app{n}.py"}}

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        if self._next > 0 and not self._finished():
            parts = []
            while len(parts) < _WRITES_IN_ONE_BATCH and not self._finished():
                parts.append(self._part())
            self.emitted += 1
            self._by_session.setdefault(session_id, []).append(
                {"id": f"patch-m{self.emitted}", "type": "assistant", "content": parts})
        return FakeOpenCode.messages(self, session_id, limit=limit)


def _write_cap_fired(events: list[dict]) -> list[dict]:
    return [e for e in events
            if e.get("type") == "build-stalled" and "edits refused" in e.get("message", "")]


def _one_model_orch(tmp: Path, oc: FakeOpenCode) -> Orchestrator:
    orch = _orch(tmp, oc, "BUILD")
    cat = orch.project(start_preview=False).shim.catalog
    orch.project(start_preview=False).shim.set_catalog(
        _replace(cat, plan="flashy-vendor", implement="flashy-vendor"))
    return orch


def test_a_build_turn_whose_edits_keep_being_refused_is_stopped(tmp_path: Path):
    oc = PatchRefusingOpenCode(tmp_path / "mnt" / "code", [Turn(text="patching")],
                               refused=svc._FAILED_WRITES_MAX)
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    card = _write_cap_fired(events)
    assert len(card) == 1
    assert str(svc._FAILED_WRITES_MAX) in card[0]["message"]
    # Never the refusal itself: a tool's error text is not this sentence's to quote, same rule as
    # the brake's — and `apply_patch` is not on `_REPEAT_ANSWER_TOOLS` anyway.
    assert "Begin/End markers" not in card[0]["message"]
    assert next(e for e in events if e.get("type") == "done")["decision"] == "looped"
    assert oc.interrupted == 1
    assert orch._turn_gave_up is True


def test_a_turn_one_refusal_short_of_the_cap_is_not_stopped_for_it(tmp_path: Path, monkeypatch):
    """Nine refusals is the same turn as ten, one part earlier. Only the count separates them."""
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    oc = PatchRefusingOpenCode(tmp_path / "mnt" / "code", [Turn(text="patching")],
                               refused=svc._FAILED_WRITES_MAX - 1)
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert _write_cap_fired(events) == []
    assert next(e for e in events if e.get("type") == "done")["decision"] != "looped"


def test_a_turn_that_refuses_and_lands_in_turns_is_left_alone(tmp_path: Path, monkeypatch):
    """The plant for the RATIO, and it is the one that shaped the rule.

    Written first as "ten refused beside twelve landed", it stopped the turn — because the counts
    are read at the INSTANT the tenth refusal arrives, and a build alternating refuse/land has only
    nine landed by then. A build fixing its own mistakes looks exactly like that, so `more refused
    than landed` was the wrong key: the measured loop lost more than three for every one it landed
    (25 against 7), and the bar is now twice.
    """
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    oc = PatchRefusingOpenCode(tmp_path / "mnt" / "code", [Turn(text="patching")],
                               refused=svc._FAILED_WRITES_MAX, landed=svc._FAILED_WRITES_MAX + 2)
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert _write_cap_fired(events) == []
    assert next(e for e in events if e.get("type") == "done")["decision"] != "looped"


def test_a_turn_losing_more_than_twice_what_it_lands_is_stopped(tmp_path: Path):
    """The other side of the same bar: the measured shape, scaled down. Four landed against ten
    refused is not a build fixing its own mistakes."""
    oc = PatchRefusingOpenCode(tmp_path / "mnt" / "code", [Turn(text="patching")],
                               refused=svc._FAILED_WRITES_MAX, landed=4)
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert len(_write_cap_fired(events)) == 1


def test_the_sentence_names_the_model_when_both_slots_hold_it(tmp_path: Path):
    """#494's own case, and the only one where the person has something specific to change."""
    oc = PatchRefusingOpenCode(tmp_path / "mnt" / "code", [Turn(text="patching")],
                               refused=svc._FAILED_WRITES_MAX)
    orch = _one_model_orch(tmp_path, oc)

    events = list(orch.build_stream("chart them"))

    said = _write_cap_fired(events)[0]["message"]
    assert "flashy-vendor" in said
    assert "Both model slots" in said


def test_the_sentence_invents_nothing_when_the_slots_hold_two_models(tmp_path: Path):
    oc = PatchRefusingOpenCode(tmp_path / "mnt" / "code", [Turn(text="patching")],
                               refused=svc._FAILED_WRITES_MAX)
    orch = _orch(tmp_path, oc, "BUILD")   # plan="p", implement="i"

    events = list(orch.build_stream("chart them"))

    said = _write_cap_fired(events)[0]["message"]
    assert "Both model slots" not in said
    assert "assign a different model to the build slot" in said


# --- A REFUSED edit is not a write (#508) --------------------------------------------------------
# `made_edits` used to trip on any part named `edit`/`write` whatever its status, and it never goes
# back down inside a turn. So one refused edit made `agent_wrote()` true for the rest of the turn,
# and six decisions read that — the shell cap above among them, which is keyed on `not
# agent_wrote()` and so was disarmed by precisely the turn it exists for.


class _EditOnce(FakeOpenCode):
    """One turn, one `edit` part carrying the status the test names, and nothing else.

    `heredoc` writes a file and reports NO tool for it — the `printf > file` hole that the
    working-tree half of `agent_wrote()` exists for. `Turn.writes` cannot stand in for it: the fake
    emits a COMPLETED `write` part beside every one of those, which is the other half, and a test
    built on it would pass whichever half was doing the work.
    """

    def __init__(self, workspace: Path, *, status: str, heredoc: bool = False) -> None:
        super().__init__(workspace, [Turn(text="that's done")])
        self.status = status
        self.heredoc = heredoc

    def send_prompt(self, session_id: str, *a, **k) -> None:
        super().send_prompt(session_id, *a, **k)
        if self.heredoc:
            path = self._session_dir(session_id) / "src" / "Chart.tsx"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("export const Chart = () => null\n")
        # Ahead of the turn's text, where a real one sits: the loop walks parts in order and the
        # text part is what ends the turn.
        self._by_session[session_id][-1]["content"].insert(0, {
            "id": "edit-1", "type": "tool", "tool": "edit",
            "state": {"status": self.status, "input": {"filePath": "src/App.tsx"},
                      "error": "oldString not found in file"}})


def test_a_turn_whose_only_edit_was_refused_did_not_write(tmp_path: Path, monkeypatch):
    """No nudges, so the turn's verdict is the one thing under test rather than the retry loop."""
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    oc = _EditOnce(tmp_path / "mnt" / "code", status="error")
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    done = next(e for e in events if e.get("type") == "done")
    assert done["ok"] is False
    assert "didn't change any files" in done["decision"] \
        or "couldn't get past planning" in done["decision"]
    # And nothing told the person their app had changed, because it had not.
    assert [e for e in events if e.get("type") == "app-change"] == []


def test_a_turn_whose_edit_landed_still_wrote(tmp_path: Path, monkeypatch):
    """The other side of the same key, and the one that stops the fix being `made_edits = False`.

    Nothing reaches the disk here, so the tree hash cannot answer this one — only the tool part can.
    """
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    oc = _EditOnce(tmp_path / "mnt" / "code", status="completed")
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert next(e for e in events if e.get("type") == "done")["ok"] is True


def test_the_working_tree_answers_for_a_write_no_tool_reported(tmp_path: Path, monkeypatch):
    """The refused edit says no and the disk says yes, so `agent_wrote()` must still say yes."""
    monkeypatch.setenv("SAGE_MAX_NUDGES", "0")
    oc = _EditOnce(tmp_path / "mnt" / "code", status="error", heredoc=True)
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert next(e for e in events if e.get("type") == "done")["ok"] is True


class RefusedThenCountingOpenCode(CountingOpenCode):
    """The measured 2026-09-22 shape: a write refused early, then nothing but different shell calls.

    One refusal is all it takes — `made_edits` never resets — so the plant is deliberately a single
    part at the front rather than a stream of them.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None) -> None:
        super().__init__(workspace, turns)
        self.refused = False

    def messages(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        if self._next > 0 and not self.refused:
            self.refused = True
            self._by_session.setdefault(session_id, []).append(
                {"id": "refused-m", "type": "assistant",
                 "content": [{"id": "refused-1", "type": "tool", "tool": "edit",
                              "state": {"status": "error",
                                        "input": {"filePath": "src/App.tsx"},
                                        "error": "oldString not found in file"}}]})
        return super().messages(session_id, limit=limit)


def test_the_shell_cap_fires_on_a_turn_whose_write_was_refused(tmp_path: Path):
    """The whole point of #508. Before the status check this turn could not be stopped at all: the
    fake asserts on its own poll count rather than running to a wall clock, so the red is the
    counting session never being capped."""
    oc = RefusedThenCountingOpenCode(tmp_path / "mnt" / "code", [Turn(text="looking")])
    orch = _orch(tmp_path, oc, "BUILD")

    events = list(orch.build_stream("chart them"))

    assert len(_shell_cap_fired(events)) == 1
    assert next(e for e in events if e.get("type") == "done")["decision"] == "looped"
    assert oc.interrupted == 1


# --- What a refused patch looked like (#494) -----------------------------------------------------
# The evidence that was missing when this issue was filed: the refusal text is the tool's REPLY, and
# nothing recorded the REQUEST, so a malformed envelope, an answer cut off at the output cap and
# escaped newlines all read the same from the ring.

def _patch_part(text: str, status: str = "error") -> dict:
    return {"id": "p1", "type": "tool", "tool": "apply_patch",
            "state": {"status": status, "input": {"patchText": text}}}


def test_a_refused_patch_is_described_by_its_shape_and_never_by_its_lines():
    body = ("*** Begin Patch\n*** Update File: app.py\n@@\n-    secret = 'hunter2'\n"
            "+    secret = load()\n*** End Patch\n")
    said = svc._tool_detail("apply_patch", _patch_part(body))

    assert said.startswith("refused: ")
    assert "begin=y end=y" in said
    assert f"lines={len(body.splitlines())}" in said
    # The patch body is the person's source, and this reaches a log ring and a UI card.
    assert "hunter2" not in said and "secret" not in said and "app.py" not in said


def test_the_shape_tells_the_three_causes_apart():
    envelope_missing = svc._tool_detail("apply_patch", _patch_part("@@\n-a\n+b\n"))
    assert "begin=n end=n" in envelope_missing

    # One line to the parser, whichever way the newlines arrived: both produce the same refusal.
    escaped = svc._tool_detail(
        "apply_patch", _patch_part("*** Begin Patch\\n*** Update File: a.py\\n*** End Patch"))
    # Two escapes, not three: the fixture's trailing `*** End Patch` has none after it, and the
    # count is the evidence the shape line exists to give — so it is asserted exactly.
    assert "begin=n" in escaped and "lines=1" in escaped and "esc=2" in escaped

    crlf = svc._tool_detail("apply_patch", _patch_part("*** Begin Patch\r\n@@\r\n*** End Patch\r\n"))
    assert "crlf=3" in crlf

    fenced = svc._tool_detail(
        "apply_patch", _patch_part("```\n*** Begin Patch\n@@\n*** End Patch\n```\n"))
    assert "fenced=y" in fenced

    # The mistake the implement prompt now warns about by name.
    numbered = svc._tool_detail(
        "apply_patch", _patch_part("*** Begin Patch\n@@\n-00012|    old\n+00012|    new\n*** End Patch\n"))
    assert "numbered=2" in numbered


def test_a_patch_that_landed_is_labelled_with_the_file_it_changed():
    for header in ("*** Update File: src/App.tsx", "*** Add File: src/App.tsx",
                   "*** Delete File: src/App.tsx"):
        said = svc._tool_detail(
            "apply_patch", _patch_part(f"*** Begin Patch\n{header}\n@@\n-a\n+b\n*** End Patch\n",
                                       status="completed"))
        assert said == "src/App.tsx", header


def test_a_patch_call_with_no_patch_text_is_not_described():
    # Same rule as every other tool here: a label is never worth raising over.
    assert svc._tool_detail("apply_patch", {"state": {"status": "error", "input": {}}}) == ""
    assert svc._tool_detail("apply_patch", {"state": {"status": "error", "input": "half-sent"}}) == ""
    assert svc._tool_detail("apply_patch", _patch_part("*** Begin Patch\n", status="completed")) == ""
