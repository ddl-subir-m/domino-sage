"""A Chat turn that sent tools and got none back says so, naming the model that did not call (#469).

WHY THIS EXISTS. A person assigned an alias to the `ask` slot whose declared capabilities were
`chat, vision, responses` — no `tools`. Sage accepted it, drew it in the composer like any other,
and every Chat turn then handed it eleven tools. One measured turn streamed 426 chunks over 86
seconds and came back with no tool call in any of them: an essay where a data answer was asked for.
Nothing on screen, in the transcript or in the log said that had happened.

WHY THE FIX IS AN OBSERVATION AND NOT A REFUSAL, which is also why these tests never read a
capability list. The same alias declares no `streaming` and demonstrably streamed. The metadata is
incomplete, so it cannot answer "can this model call a tool" — and a hard refusal built on it would
lock a person out of a working model and remove the only witness that the metadata is wrong. What
the turn DID is not in doubt: `app.py` already counts every inference and flags the ones carrying a
`tool_calls` frame, for `build_stream`'s benefit. Chat reading the same two numbers is the change.

WHAT THESE TESTS ARE CAREFUL NOT TO ASSERT is the wording. Every assertion reads the emitted
`LogRecord`'s interpolation ARGUMENTS, never `getMessage()`, and the records are SELECTED by the
function that emitted them rather than by anything in them — see `_toolless`, which explains why
selecting on the model name would have made one of these tests unable to fail. A test that greps
`"model="` out of the rendered line passes just as happily with the name hardcoded into the format
string, which is the one defect that would make the change worthless. `RESOLVED` below is
deliberately not a plausible model name for the same reason: a real-looking one could be a literal
somebody typed, and this one could only have arrived by being threaded from what the shim recorded.

WHAT THE FAKE STANDS IN FOR, said plainly because it is the limit of this file. The two counters are
written by the real `/v1/chat/completions` route in `app.py`, which no fake-OpenCode test reaches —
`test_a_streaming_call_is_not_a_quiet_turn.py` drives that route for real and is the file that
covers the write. Here the fake plants the same two fields at the moment the route would have moved
them, while the prompt is being served. So these tests exercise the READ, the condition and the
per-turn reset over real `chat_stream` turns, and they take the write on trust.

THE PLANTS ARE THE POINT, and here is exactly how much they hold. Two conditions narrow the line
and each has its own test: a turn WITH a tool call stays quiet, and a turn that ended before the
model ran stays quiet, because "called no tool" is not a finding about a turn that called no model.

What neither plant holds, said here rather than left for a reader to discover: an ORDINARY
conversational turn answered from context runs one inference, calls no tool, and warns. That is
#469's decision, not a gap these tests failed to close — the alternative is asking whether a tool
was NEEDED, which nothing on this path can answer. `model_calls` is the whole of the noise guard.
Do not read the two plants below as evidence that the warning is rare.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator import service as svc
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

# Neither is a model anyone could have typed by accident, and they are different on purpose: the
# warning must name what the router RESOLVED to, not what the slot asked for.
RESOLVED = "resolved-model-under-test"
REQUESTED = "requested-model-under-test"

LOGGER = "sage.orchestrator"


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """Answers the read-only classifiers with one body, whatever they ask."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class CountingOpenCode(FakeOpenCode):
    """Stands in for the `/v1` route's bookkeeping: inferences, and which of them called a tool.

    `script` is one `(model_calls, tool_call_responses)` pair per turn, consumed in the same order
    as `FakeOpenCode`'s own turn script, so a test that wants "tool calls on turn one and none on
    turn two" writes exactly that. A turn past the end of the script plants nothing, which is the
    honest stand-in for a turn that never reached the route.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 script: list[tuple[int, int]] | None = None) -> None:
        super().__init__(workspace, turns)
        self.script = list(script or [])
        self.project = None
        self.planted = 0

    def send_prompt(self, session_id: str, text: str, **kwargs) -> None:
        nth = self.planted
        self.planted += 1
        if self.project is not None and nth < len(self.script):
            calls, tool_calls = self.script[nth]
            self.project.model_calls += calls
            self.project.tool_call_responses += tool_calls
            if calls:
                # What the shim reports through `on_resolved` on every inference it serves.
                self.project.note_resolved(RESOLVED, "ask", "test")
        super().send_prompt(session_id, text, **kwargs)


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch):
    """The Chat poll loop waits on `_EventTap`, which blocks for real seconds against a fake that
    never emits a session frame. Scripted here for the same reason #466's file scripts it."""
    real = time.monotonic
    offset = {"s": 0.0}

    def advance(s=0.0):
        offset["s"] += (s or 0.0)

    monkeypatch.setattr(time, "monotonic", lambda: real() + offset["s"])
    monkeypatch.setattr(svc._EventTap, "wait", lambda self, timeout, floor=0.0: bool(advance(timeout)))
    monkeypatch.setattr(svc._EventTap, "wait_any", lambda self, timeout, floor=0.0: bool(advance(timeout)))
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building the app\n")
    return t


def _orch(tmp: Path, oc: CountingOpenCode) -> Orchestrator:
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i",
                                             ask=REQUESTED),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    oc.project = orch.project(start_preview=False)
    return orch


def _toolless(caplog) -> list[logging.LogRecord]:
    """The warnings this change emits, selected by the FUNCTION that emitted them.

    Not by matching the message, for the reason the module docstring gives. Not by "every warning
    from the service logger" either — an unrelated warning on a failing turn would then read as
    this one, and a missing one would hide behind it.

    And deliberately NOT by the model name in `args`, which is the obvious selector and is circular:
    it would make `test_the_warning_names_the_resolved_model_...` unable to fail for its own reason.
    A run that logged the requested name would return an EMPTY list here, so that test would red on
    "no warning was emitted" and its named assertion could never run. `finish` holds exactly one
    `log` call, so the function is a precise handle that owes the assertions nothing.
    """
    return [r for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.WARNING and r.funcName == "finish"]


def _run(orch: Orchestrator, tid: str, prompt: str = "fit a regression on the event table"):
    return list(orch.chat_stream(tid, prompt))


# --- Condition 1: the bug. A turn that sent tools and saw no tool call says so. -----------------


def test_a_turn_whose_model_never_called_a_tool_warns_and_names_the_resolved_model(tmp_path, caplog):
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="A long essay about the event table.")],
                          script=[(4, 0)])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        events = _run(orch, tid)

    assert next(e for e in events if e.get("type") == "done")["ok"] is True, (
        "the turn under test has to be an ORDINARY one — a failing turn would be explained already")
    records = _toolless(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert RESOLVED in records[0].args
    # The count is an argument too, so "four inferences and not one tool call" is readable as data.
    assert 4 in records[0].args


def test_the_warning_names_the_resolved_model_and_not_the_one_the_slot_asked_for(tmp_path, caplog):
    """Condition 3. Four rules move a request off the alias a person picked, so the requested name
    answers a different question than the one this line asks."""
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An essay.")], script=[(1, 0)])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _run(orch, tid)

    records = _toolless(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    # Both halves, and both are live: `_toolless` selects on the emitting function, so a warning
    # that named the slot's model is still IN this list and fails on the second line rather than
    # disappearing from the list and failing on the first with the wrong reason.
    assert RESOLVED in (records[0].args or ())
    assert REQUESTED not in (records[0].args or ()), (
        "the warning named the slot's model, which is the derivation this ticket exists to avoid")


# --- Plant 1: a healthy turn. Without this the line is noise on every turn that works. ----------


def test_a_turn_that_did_call_a_tool_says_nothing(tmp_path, caplog):
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="Here you go.", tools=["read"])], script=[(4, 2)])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _run(orch, tid)

    assert _toolless(caplog) == [], "a healthy turn was reported as toolless"


# --- Plant 2: a turn that reached no model at all. ---------------------------------------------


def test_a_turn_ended_before_the_model_says_nothing(tmp_path, caplog):
    """"Called no tool" is not a finding about a turn that called no model.

    A REAL ending rather than a stand-in for one, and the distinction is the test. "build me a
    dashboard" matches `handoff.looks_like_build_request`, so `_explicit_handoff` offers Build and
    ends the turn through the same `finish()` seam without ever prompting OpenCode — which is the
    shape every door refusal and gate card also has.

    The script is deliberately NOT empty. Withholding the plant would pin `model_calls == 0` in a
    world where nothing could have incremented it, and the test would still pass if this ending
    ever started reaching OpenCode. Armed this way, a turn that reached the agent plants four
    inferences and no tool call, and the assertion below catches it.
    """
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="never said")], script=[(4, 0)])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        events = _run(orch, tid, "build me a dashboard")

    assert next(e for e in events if e.get("type") == "done")["decision"] == "handoff", (
        "this turn was meant to end at the handoff offer, before the agent ran")
    assert oc.prompts == [], "the ending under test prompted OpenCode, so it is not the one named"
    assert project.model_calls == 0
    assert _toolless(caplog) == [], (
        "a turn that reached no model was reported as one that called no tool")


# --- Condition 2: the counters are this turn's. ------------------------------------------------


def test_the_second_toolless_turn_in_a_row_still_warns(tmp_path, caplog):
    """The reset condition, and the reason it needed its own test.

    Nothing in Chat has ever zeroed these two — the only resets in the tree are `build_stream`'s.
    So without the clear at Chat's grant the second turn reads turn one's tool call and reports
    itself healthy, and the run of turns this ticket is about is exactly a run: the model that did
    not call a tool on one turn does not call one on the next either.
    """
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="Here you go.", tools=["read"]), Turn(text="An essay.")],
                          script=[(3, 2), (3, 0)])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _run(orch, tid, "read the event table")
        first = list(_toolless(caplog))
        _run(orch, tid, "now summarise it")

    assert first == [], "the first turn called a tool and should have said nothing"
    assert len(_toolless(caplog)) == 1, [r.getMessage() for r in _toolless(caplog)]


def test_a_chat_turn_clears_the_counts_it_inherits(tmp_path, caplog):
    """The other half of the same rule, from the other side: a count left by a previous lane.

    `build_stream` resets at its own grant, so what a Chat turn inherits is whatever the last Build
    turn ended on. A Chat turn that reaches no model must still report no model, not the Build
    turn's four.
    """
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="never said")], script=[])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.model_calls = 9          # a previous lane's, and nothing to do with this turn
    project.tool_call_responses = 9

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _run(orch, tid)

    assert project.model_calls == 0
    assert project.tool_call_responses == 0
