"""A model that never calls a tool is named after a streak, not after one turn (#469).

WHY THIS EXISTS. A person assigned an alias to the `ask` slot whose declared capabilities were
`chat, vision, responses` — no `tools`. Sage accepted it, drew it in the composer like any other,
and every Chat turn then handed it eleven tools. One measured turn streamed 426 chunks over 86
seconds and came back with no tool call in any of them: an essay where a data answer was asked for.
Nothing on screen, in the transcript or in the log said that had happened.

WHY THE WITNESS IS NOT A CAPABILITY CHECK, which is also why these tests never read a capability
list. The declared list is wrong in BOTH directions, measured live on 2026-09-20: `GLM 5.3 OR`
declared no `streaming` and demonstrably streamed, while `domino-gcp/claude-sonnet-5` and
`domino/gemini-3.7-flash` declare bare `chat` today and are the models most turns actually run on.
Warning only on models that declare `tools` silences the witness on those two; warning only on
models that do not would have suppressed the one true positive on record. #463 marks what the list
SAYS and is explicit that it is "a MARK and never a filter"; this file is about what the model DID.
The two are kept independent so they can contradict each other, because that contradiction is the
bug report.

WHY A STREAK AND NOT A TURN, which is the shape of the whole change. One turn cannot distinguish
"this model cannot call a tool" from "this turn did not need one" — a follow-up answered from
context is a perfectly good Chat turn that calls nothing. #467 landed the measurement that settles
it: the classifier on the same slot proved intermittent, two clean answers and then two unparseable
ones in one session. So a turn only ever COUNTS here, and the warning belongs to a model that has
reached `_TOOLLESS_TURNS` turns having never once returned a tool call in this process.

The clearing is monotonic, and that is what removes the noise by construction rather than by tuning:
a model that calls a tool is cleared PERMANENTLY and can never warn again, so ordinary conversation
contributes only while the model has never proved itself. Two properties invert the whole thing if
they break, and each has a test below: the counter is keyed PER MODEL, and clearing is IRREVERSIBLE.

WHAT THESE TESTS ARE CAREFUL NOT TO ASSERT is the wording. Every assertion reads the emitted
`LogRecord`'s interpolation ARGUMENTS, never `getMessage()`, and the records are SELECTED by the
function that emitted them rather than by anything in them — see `_toolless`, which explains why
selecting on the model name would have made one of these tests unable to fail. `TOOLLESS` and
`WORKING` below are deliberately not plausible model names: a real-looking one could be a literal
somebody typed, and these could only have arrived by being threaded from what the shim recorded.

WHAT THE FAKE STANDS IN FOR, said plainly because it is the limit of this file. `tool_call_responses`
is written by the real `/v1/chat/completions` route in `app.py`, which no fake-OpenCode test reaches
— `test_a_streaming_call_is_not_a_quiet_turn.py` drives that route for real. Here the fake plants
the same field, and the resolved model, at the moment the route would have. So these tests exercise
the READ, the streak, the clearing and the per-turn reset over real `chat_stream` turns, and they
take the write on trust.
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

# None of these is a model anyone could have typed by accident. TOOLLESS and REQUESTED differ on
# purpose: the warning must name what the router RESOLVED to, not what the slot asked for.
TOOLLESS = "toolless-model-under-test"
WORKING = "working-model-under-test"
REQUESTED = "requested-model-under-test"

LOGGER = "sage.orchestrator"

N = svc._TOOLLESS_TURNS


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """Answers the read-only classifiers with one body, whatever they ask."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class CountingOpenCode(FakeOpenCode):
    """Stands in for the `/v1` route: which model served the turn, and whether it called a tool.

    `script` is one `(model, tool_call_responses)` pair per turn, consumed in the same order as
    `FakeOpenCode`'s own turn script, so "model A calls a tool, then model B does not" is written
    as exactly that. A turn past the end of the script plants nothing, which is the honest stand-in
    for a turn that never reached the route.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 script: list[tuple[str, int]] | None = None) -> None:
        super().__init__(workspace, turns)
        self.script = list(script or [])
        self.project = None
        self.planted = 0

    def send_prompt(self, session_id: str, text: str, **kwargs) -> None:
        nth = self.planted
        self.planted += 1
        if self.project is not None and nth < len(self.script):
            model, tool_calls = self.script[nth]
            self.project.model_calls += 1
            self.project.tool_call_responses += tool_calls
            # What the shim reports through `on_resolved` on every inference it serves.
            self.project.note_resolved(model, "ask", "test")
        super().send_prompt(session_id, text, **kwargs)


@pytest.fixture(autouse=True)
def _reset_tool_use():
    """`_tool_use` is process-wide by design, and an unreset one carries a streak — or a clearing —
    from whichever test ran before into whichever runs next."""
    svc._tool_use.reset()
    yield
    svc._tool_use.reset()


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


def _turns(orch: Orchestrator, tid: str, n: int, prompt: str = "summarise the event table"):
    for i in range(n):
        list(orch.chat_stream(tid, f"{prompt} ({i})"))


# --- The streak. One turn counts; N turns speak. ------------------------------------------------


def test_a_model_that_never_calls_a_tool_is_named_once_it_reaches_the_streak(tmp_path, caplog):
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An essay.")] * N, script=[(TOOLLESS, 0)] * N)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, N)

    records = _toolless(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert TOOLLESS in records[0].args
    # The streak rides as its own argument, so "this is the Nth" is readable as data.
    assert N in records[0].args


def test_the_turns_before_the_streak_only_count(tmp_path, caplog):
    """The plant that catches a per-turn warning wearing a streak's clothes.

    One turn cannot tell a tool-blind model from a turn that needed no tool, so the first N-1 say
    nothing at all. Without this, `_TOOLLESS_TURNS` could be 1 and every test above still passes.
    """
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An essay.")] * (N - 1), script=[(TOOLLESS, 0)] * (N - 1))
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, N - 1)

    assert _toolless(caplog) == [], f"a model was accused after {N - 1} turns, short of the streak"


def test_a_model_past_the_streak_keeps_saying_so(tmp_path, caplog):
    """A tool-blind model does not get to go quiet once it has been named."""
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An essay.")] * (N + 2), script=[(TOOLLESS, 0)] * (N + 2))
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, N + 2)

    records = _toolless(caplog)
    assert len(records) == 3, [r.getMessage() for r in records]
    assert [r.args[1] for r in records] == [N, N + 1, N + 2]


# --- Monotonic clearing. The property that removes the noise. -----------------------------------


def test_a_model_that_has_called_a_tool_is_never_accused_again(tmp_path, caplog):
    """The ordinary-conversation case, and the reason there is no threshold to tune.

    One real data turn, then a long run of turns answered from context. Without irreversible
    clearing this is the noise the change would have shipped: a working model re-accused for
    holding a conversation.
    """
    ws = tmp_path / "mnt" / "code"
    script = [(WORKING, 2)] + [(WORKING, 0)] * (N + 3)
    oc = CountingOpenCode(ws, [Turn(text="Here you go.")] * len(script), script=script)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, len(script))

    assert _toolless(caplog) == [], "a model that had already called a tool was accused"


def test_a_tool_call_partway_through_a_streak_clears_it_for_good(tmp_path, caplog):
    """Clearing is irreversible, not a counter reset.

    A model two turns into a streak calls a tool, then goes quiet for longer than the streak. If
    clearing merely zeroed the count it would be re-accused here, which is exactly the re-accusation
    the monotonic set exists to prevent.
    """
    ws = tmp_path / "mnt" / "code"
    script = [(WORKING, 0)] * (N - 1) + [(WORKING, 1)] + [(WORKING, 0)] * (N + 2)
    oc = CountingOpenCode(ws, [Turn(text="...")] * len(script), script=script)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, len(script))

    assert _toolless(caplog) == [], "a cleared model was accused again after a later quiet run"


# --- Per-model keying. The property that inverts the whole thing if it breaks. ------------------


def test_one_model_calling_a_tool_does_not_clear_another(tmp_path, caplog):
    """The plant to write first: a process-global counter would let the working model clear the
    broken one, and the models most turns run on are not the one under suspicion.

    The two are interleaved rather than run in blocks, so a global counter cannot reach the streak
    by accident either — every toolless turn here is followed by a tool-calling one.
    """
    ws = tmp_path / "mnt" / "code"
    script = []
    for _ in range(N):
        script += [(TOOLLESS, 0), (WORKING, 2)]
    oc = CountingOpenCode(ws, [Turn(text="...")] * len(script), script=script)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, len(script))

    records = _toolless(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert TOOLLESS in records[0].args
    assert WORKING not in (records[0].args or ()), "the cleared model was named in the accusation"


def test_the_working_model_is_never_named(tmp_path, caplog):
    """The other direction of the same keying: a model that calls tools throughout, beside a model
    that never does, must not pick up the other's streak."""
    ws = tmp_path / "mnt" / "code"
    script = []
    for _ in range(N + 1):
        script += [(WORKING, 3), (TOOLLESS, 0)]
    oc = CountingOpenCode(ws, [Turn(text="...")] * len(script), script=script)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, len(script))

    named = {a for r in _toolless(caplog) for a in (r.args or ()) if isinstance(a, str)}
    assert WORKING not in named
    assert TOOLLESS in named


def test_two_quiet_models_do_not_pool_into_one_accusation(tmp_path, caplog):
    """The other half of per-model keying, and the direction the two tests above cannot see.

    Both of them have a CLEARED model in the script, so a pooled `called` flag is what they catch.
    This one has no cleared model at all: two different models, each one turn short of the streak,
    and neither has earned an accusation. A single pooled COUNTER reaches the streak here on turns
    belonging to two models and names one of them for the other's silence — a false positive, where
    the pooled-flag fault is a false negative. The same word "global" covers both, and they fail in
    opposite directions.
    """
    ws = tmp_path / "mnt" / "code"
    script = [(TOOLLESS, 0)] * (N - 1) + [(WORKING, 0)] * (N - 1)
    oc = CountingOpenCode(ws, [Turn(text="...")] * len(script), script=script)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, len(script))

    assert _toolless(caplog) == [], (
        "two models each short of the streak were pooled into one accusation")


# --- The name is the resolved one. --------------------------------------------------------------


def test_the_warning_names_the_resolved_model_and_not_the_one_the_slot_asked_for(tmp_path, caplog):
    """Four rules move a request off the alias a person picked, so the requested name answers a
    different question than the one this line asks."""
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An essay.")] * N, script=[(TOOLLESS, 0)] * N)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, N)

    records = _toolless(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    # Both halves, and both are live: `_toolless` selects on the emitting function, so a warning
    # that named the slot's model is still IN this list and fails on the second line rather than
    # disappearing from the list and failing on the first with the wrong reason.
    assert TOOLLESS in (records[0].args or ())
    assert REQUESTED not in (records[0].args or ()), (
        "the warning named the slot's model, which is the derivation this ticket exists to avoid")


# --- A turn that ended before any model ran is not evidence about a model. ----------------------


def test_turns_that_ended_before_the_model_never_reach_the_streak(tmp_path, caplog):
    """A REAL ending rather than a stand-in for one, and the distinction is the test.

    "build me a dashboard" matches `handoff.looks_like_build_request`, so `_explicit_handoff` offers
    Build and ends the turn through the same `finish()` seam without ever prompting OpenCode — the
    shape every door refusal and gate card also has. `_acquire_turn` clears `resolved_model` at the
    grant, so these turns carry no model, and a streak keyed on a placeholder would pool them.

    Run `N` times on purpose. One would pass with the guard removed, because one turn is short of
    the streak either way; `N` of them is what makes the guard's absence visible.

    The script is deliberately NOT empty, so a turn that ever did reach the agent would plant a
    resolved model and be caught by the assertion below rather than pass for the wrong reason.
    """
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="never said")] * N, script=[(TOOLLESS, 0)] * N)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for i in range(N):
            events = list(orch.chat_stream(tid, f"build me a dashboard ({i})"))
            assert next(e for e in events if e.get("type") == "done")["decision"] == "handoff", (
                "this turn was meant to end at the handoff offer, before the agent ran")

    assert oc.prompts == [], "the ending under test prompted OpenCode, so it is not the one named"
    assert project.resolved_model is None
    assert _toolless(caplog) == [], "turns that reached no model were counted against one"


# --- The per-turn counter is this turn's. -------------------------------------------------------


def test_a_tool_call_by_one_model_does_not_clear_the_next_turns_model(tmp_path, caplog):
    """Why `chat_stream` clears `tool_call_responses` at the grant, in the one direction that
    cannot be undone.

    The router moves a request off the picked model, so turn one can resolve to a model that calls
    a tool and turn two to one that does not. Without the clear, turn two reads turn one's count and
    CLEARS the second model permanently — and monotonic clearing means nothing later can take that
    back. The streak below would then never be reached.
    """
    ws = tmp_path / "mnt" / "code"
    script = [(WORKING, 2)] + [(TOOLLESS, 0)] * N
    oc = CountingOpenCode(ws, [Turn(text="...")] * len(script), script=script)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, len(script))

    records = _toolless(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert TOOLLESS in records[0].args


def test_a_chat_turn_clears_the_count_it_inherits(tmp_path, caplog):
    """The same rule from the other side: a count left by the Build lane is not this turn's."""
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="...")], script=[(TOOLLESS, 0)])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.tool_call_responses = 9      # a previous Build turn's

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _turns(orch, tid, 1)

    assert project.tool_call_responses == 0
    assert svc._tool_use.no_tool_call(TOOLLESS) == 2, (
        "the turn inherited a tool call it did not make, and cleared the model on it")
