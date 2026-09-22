"""A model call that is still streaming is not a turn that stopped (#466).

The live shape: a Chat turn asked a data question, one model call streamed 426 chunks over 86
seconds, and at 91s Chat tore it down and told the person "Sage stopped making progress." The model
had not stopped. Both quiet windows measure silence from what OpenCode sends, and OpenCode sends
nothing at all while one model call runs — so the only turns those windows could see as alive were
the ones punctuated by tool steps.

The evidence was already in the process. OpenCode's `/v1` endpoint is served by the same ASGI app as
the turn loop, and its per-chunk hook runs on every chunk of every stream. What was missing was a
wire from there to here: `Project.last_stream_chunk_at`.

WHAT EACH TEST CAN AND CANNOT TELL, because that is the trap in this ticket:

* The write (`sniff` in app.py) is exercised through the REAL route, against the real response
  generator. Nothing below fakes it.
* The read (both poll loops) is exercised through real turns, with the stamp advanced by the fake
  agent's poll — which is the fake standing in for "the gateway is delivering chunks to OpenCode
  while OpenCode itself says nothing". `FakeOpenCode` dispatches `send_prompt` synchronously where
  the real driver is async, so nothing here asserts on timing; every assertion is on emitted events.
* `test_a_stamp_from_before_the_turn_does_not_resurrect_a_wedge` pins the BEHAVIOUR and does not
  discriminate the `>= started` clause — see its own docstring. It is written down rather than
  quietly counted as a plant.
"""
from __future__ import annotations

import json
import logging
import time
import types
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator import service as svc
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """One scripted word per routed request. The classifiers are the only callers here, and both
    lanes are happy with a verdict that keeps the turn on the lane it started on."""

    def __init__(self, verdict: str = "CHAT") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


# --- the write: the shim stamps the Project ------------------------------------------------------


def _shim_project():
    """A Project-shaped stand-in carrying exactly the fields the `/v1` route writes."""
    return types.SimpleNamespace(
        id="p", session_id="s", active_session_id=None,
        model_calls=0, tool_call_responses=0, last_gateway_error=None,
        resolved_model=None, last_refused=None,
        note_resolved=lambda *a: None,
        last_stream_chunk_at=0.0,
    )


def _post(monkeypatch, proj, gen_factory):
    from fastapi.testclient import TestClient

    from sage.orchestrator import app as orchmod

    def handle(body, project, session=None, on_resolved=None, **_):
        return gen_factory()

    proj.shim = types.SimpleNamespace(handle=handle)
    monkeypatch.setattr(orchmod, "orchestrator", types.SimpleNamespace(project=lambda: proj))
    return TestClient(orchmod.control_app).post(
        "/v1/chat/completions", json={"model": "gpt-5.4", "messages": []})


def test_the_shim_stamps_the_project_on_a_gateway_chunk(monkeypatch):
    """The write site, through the real route, with the ledger switched OFF.

    SAGE_TIMING=0 is the assertion and not the setting: the obvious place to hang this was
    `timing.model_call()`, which no-ops behind that flag — and whether a person's turn survives must
    not be switchable by a performance flag. With the stamp written there, this test reads 0.0.
    """
    monkeypatch.setenv("SAGE_TIMING", "0")
    proj = _shim_project()

    def gen():
        yield b'data: {"choices":[{"delta":{"content":"one"}}]}\n\n'
        yield b'data: {"choices":[{"delta":{"content":"two"}}]}\n\n'

    before = time.monotonic()
    _post(monkeypatch, proj, gen)
    after = time.monotonic()

    assert before <= proj.last_stream_chunk_at <= after, (
        f"nothing was stamped for this stream: {proj.last_stream_chunk_at!r}")


def test_a_stream_that_never_finishes_has_already_stamped(monkeypatch):
    """Per chunk, not per response — which is the whole point.

    The turn this ticket is about was killed while its stream was still open, so a stamp written
    when a stream COMPLETES would arrive after the turn it was meant to save. Here the gateway
    generator breaks mid-response: the stream reaches no clean end, and the stamp is there anyway
    because the first chunk wrote it on the way past.
    """
    monkeypatch.setenv("SAGE_TIMING", "0")
    proj = _shim_project()

    def gen():
        yield b'data: {"choices":[{"delta":{"content":"one"}}]}\n\n'
        raise RuntimeError("the gateway dropped the connection")

    before = time.monotonic()
    _post(monkeypatch, proj, gen)

    assert proj.last_stream_chunk_at >= before, (
        "a stream that broke mid-response left no stamp, so the stamp is written at the END of a "
        f"stream rather than per chunk: {proj.last_stream_chunk_at!r}")


# --- the read: both poll loops ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _scripted_clock(monkeypatch):
    """A poll costs the turn a second and the suite nothing.

    The poll WAITS are what is patched, not `time.sleep`. Chat parks on `_EventTap.wait_any` and
    Build on `_EventTap.wait`, and with no stream behind the tap each of those blocks for a real
    second — so a turn that has to outlive several quiet windows would cost the suite several real
    seconds per window. Advancing a monotonic offset there instead makes the clock scripted end to
    end, which is also what lets these tests name an exact number of quiet windows.

    `time.sleep` is deliberately left alone, and that is not a detail. Chat hands its save to a
    background thread that takes the turn lock, and the leak check in `conftest` gives that thread a
    five-second grace built out of `time.sleep` — stub the sleep and the grace collapses to nothing,
    so every Chat test here reports a leaked turn lock that is really a thread nobody waited for.

    Patched on the stdlib module rather than on `service`, which imports `time` function-locally.
    """
    real = time.monotonic
    offset = {"s": 0.0}

    def advance(s=0.0):
        offset["s"] += (s or 0.0)

    monkeypatch.setattr(time, "monotonic", lambda: real() + offset["s"])
    # False: nothing arrived. These fakes have no `session_events` at all, which IS the condition
    # under test — a turn whose only sign of life is the gateway stream.
    monkeypatch.setattr(svc._EventTap, "wait",
                        lambda self, timeout, floor=0.0: bool(advance(timeout)))
    monkeypatch.setattr(svc._EventTap, "wait_any",
                        lambda self, timeout, floor=0.0: bool(advance(timeout)))
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _short_windows(monkeypatch):
    """Five polls of silence rather than ninety, at the same ratio as the real pairs (#98). The rule
    under test is which polls reset the clock, and that is the same rule at either scale."""
    monkeypatch.setattr(svc, "_CHAT_QUIET_TIMEOUT_S", 5.0)
    monkeypatch.setattr(svc, "_CHAT_TOOL_QUIET_TIMEOUT_S", 20.0)
    monkeypatch.setattr(svc, "_BUILD_QUIET_TIMEOUT_S", 5.0)
    monkeypatch.setattr(svc, "_BUILD_TOOL_QUIET_TIMEOUT_S", 20.0)
    monkeypatch.setattr(svc, "_BUILD_STOP_GRACE_S", 5.0)


class StreamingOpenCode(FakeOpenCode):
    """A session that is running, says nothing, and is being fed by the gateway all the while.

    This is the live shape. OpenCode has dispatched one model call and will send no session frame
    until it comes back, so the poll loop's own witness stays silent for the whole call; meanwhile
    the `/v1` route in this same process is stamping the Project on every chunk. Stamping from
    `is_running` puts one stamp on each poll, which is what a stream delivering steadily looks like
    from the loop's side.

    `chunks_for` polls of that, then the call returns and the session goes idle — so a turn that
    survives reaches its ordinary ending and a turn that does not is cut in the middle of one.
    `poll_cap` is what makes this a test rather than a hang.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 chunks_for: int = 12, poll_cap: int = 400) -> None:
        super().__init__(workspace, turns)
        self.stay_running = True
        self.chunks_for = chunks_for
        self.poll_cap = poll_cap
        self.polls = 0
        self.project = None

    def is_running(self, session_id: str) -> bool:
        self.polls += 1
        assert self.polls <= self.poll_cap, "the poll loop never ended this turn"
        if self.polls <= self.chunks_for:
            if self.project is not None:
                self.project.last_stream_chunk_at = time.monotonic()
            return True
        self.stay_running = False
        return super().is_running(session_id)


class SilentOpenCode(FakeOpenCode):
    """Running for ever, and nothing anywhere: no session frame and no gateway chunk. The wedge the
    windows exist to catch (#39), and the test that goes red if someone "fixes" #466 by raising the
    constant instead."""

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 poll_cap: int = 400) -> None:
        super().__init__(workspace, turns)
        self.stay_running = True
        self.poll_cap = poll_cap
        self.polls = 0

    def is_running(self, session_id: str) -> bool:
        self.polls += 1
        assert self.polls <= self.poll_cap, "the poll loop never gave up on a wedged turn"
        # `stay_running` until the interrupt clears it, so giving up can confirm the session
        # stopped and hand the turn lock back — the wedge under test is the silence, not a
        # session that also refuses to stop (that one is test_wedged_turn's).
        return super().is_running(session_id)


class StaleStampOpenCode(SilentOpenCode):
    """Wedged, but with one stamp on the Project bearing a time from before this turn began."""

    project = None

    def send_prompt(self, session_id: str, text: str, **kwargs) -> None:
        if self.project is not None:
            self.project.last_stream_chunk_at = time.monotonic() - 3600.0
        super().send_prompt(session_id, text, **kwargs)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building the app\n")
    return t


def _orch(tmp: Path, oc: FakeOpenCode) -> Orchestrator:
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    if hasattr(oc, "project"):
        oc.project = orch.project(start_preview=False)
    return orch


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


# --- Chat -----------------------------------------------------------------------------------------


def test_a_chat_turn_whose_model_is_still_streaming_is_not_stopped(tmp_path: Path):
    """Condition 1: the bug, reproduced. Twelve polls of no session frame at all, against a
    five-poll idle window — and the turn survives all of them, because the gateway is talking.

    Plant for this test: revert the read in `_chat_stream` and it goes red on the `error` assertion
    with "stopped making progress", which is the sentence the person was shown live.
    """
    ws = tmp_path / "mnt" / "code"
    oc = StreamingOpenCode(ws, [Turn(text="The answer.")], chunks_for=12)
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "fit a regression on the event table"))

    assert _of(events, "error") == [], (
        f"a streaming turn was reported as stopped: {_of(events, 'error')}")
    assert _of(events, "done")[0]["ok"] is True
    assert oc.interrupted == 0, "a turn that was still streaming had its session interrupted"
    assert oc.polls > 12, "the turn never reached the polls the gateway was keeping it alive through"


def test_a_chat_turn_with_nothing_arriving_anywhere_still_stops(tmp_path: Path):
    """Condition 2: the wedge still dies, and still says so.

    This is the test that fails if #466 is ever "fixed" by raising `_CHAT_QUIET_TIMEOUT_S`: the
    window here is five scripted seconds and the session is silent for ever, so any constant large
    enough to have covered the live 86-second call would walk this turn to the wall clock instead.
    """
    ws = tmp_path / "mnt" / "code"
    oc = SilentOpenCode(ws, [Turn(text="never said")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "fit a regression on the event table"))

    assert "stopped making progress" in _of(events, "error")[0]["message"]
    assert _of(events, "done")[0]["ok"] is False
    assert oc.interrupted == 1


def test_a_stamp_from_before_the_turn_does_not_resurrect_a_wedge(tmp_path: Path):
    """Condition 3: a stale stamp is not a sign of life.

    A warm OpenCode can call `/v1` outside a turn, so the loop must never read a stamp older than
    the turn it is deciding about — a wedged turn kept alive by a stranger's chunk is exactly the
    fault the window exists to catch (#39).

    WHAT THIS TEST DOES NOT PROVE, and it is the reason the sentence is here rather than in a
    report nobody reads: it does not discriminate the `chunk_at >= started` clause. `last_activity`
    is seeded at `started` and only ever moves forward, so a stamp older than `started` is already
    older than `last_activity` and loses the `max()` with or without the clause. Deleting the clause
    leaves this test green. It pins the behaviour, which is worth pinning; it is not evidence that
    the clause is armed, and the clause is kept as the half of the rule that survives a change to
    that seeding — the other half being the reset at the grant.
    """
    ws = tmp_path / "mnt" / "code"
    oc = StaleStampOpenCode(ws, [Turn(text="never said")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    oc.project = project

    events = list(orch.chat_stream(tid, "fit a regression on the event table"))

    assert project.last_stream_chunk_at > 0.0, "the fake never planted a stale stamp"
    assert "stopped making progress" in _of(events, "error")[0]["message"]
    assert _of(events, "done")[0]["ok"] is False


def test_a_chat_turn_clears_the_stamp_it_inherits(tmp_path: Path):
    """The other half of that rule: the stamp starts empty, so nothing a previous turn or a warm
    OpenCode left behind is in scope for this one."""
    ws = tmp_path / "mnt" / "code"
    oc = SilentOpenCode(ws, [Turn(text="never said")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.last_stream_chunk_at = time.monotonic() + 10_000.0   # a stranger's, and in the future

    events = list(orch.chat_stream(tid, "fit a regression on the event table"))

    assert "stopped making progress" in _of(events, "error")[0]["message"]
    assert _of(events, "done")[0]["ok"] is False


# --- Build ----------------------------------------------------------------------------------------


def test_a_build_turn_whose_model_is_still_streaming_is_not_stopped(tmp_path: Path):
    """Condition 4, first half. Build has never been seen failing this way — 120s is more headroom,
    and a build's model calls are punctuated by tool steps that do produce frames — but it reads the
    same field through the same shape, and an unguarded reader is how the next person concludes the
    split was deliberate.

    Plant: revert the read in `_build_stream` and this goes red on `build-stalled`.
    """
    ws = tmp_path / "mnt" / "code"
    oc = StreamingOpenCode(ws, [Turn(text="done", writes={"src/chart.tsx": "chart\n"})],
                           chunks_for=12)
    orch = _orch(tmp_path, oc)

    events = list(orch.build_stream("add a chart"))

    assert _of(events, "build-stalled") == [], (
        f"a streaming build was given up on: {_of(events, 'build-stalled')}")
    assert _of(events, "done")[0]["ok"] is True
    assert oc.interrupted == 0
    assert oc.polls > 12


def test_a_build_turn_with_nothing_arriving_anywhere_still_stops(tmp_path: Path):
    """Condition 4, second half: the wedge Build's window was written for is untouched."""
    ws = tmp_path / "mnt" / "code"
    oc = SilentOpenCode(ws, [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)

    events = list(orch.build_stream("add a chart"))

    assert len(_of(events, "build-stalled")) == 1
    assert _of(events, "done")[0]["decision"] == "stalled"
    assert oc.interrupted == 1


def test_a_cut_off_answer_says_so_on_the_path_a_workspace_runs(monkeypatch, caplog):
    """The live route never read `finish_reason`, so only the standalone shim said this (#494).

    A provider that ends an answer early leaves no other trace: the stream closes cleanly, `[DONE]`
    arrives, and the symptom shows up a layer away as OpenCode failing the session on a tool call
    whose arguments stopped mid-token. Without this line a patch truncated at the output cap and a
    patch the model wrote malformed are the same event from the log ring — which is why the six
    "missing Begin/End markers" refusals in the 2026-09-21 ring have no cause. `shim/app.py` has
    logged it since `cut_off_finish_reason` was written; this is the path a workspace runs.
    """
    proj = _shim_project()

    def gen():
        yield (b'data: {"choices":[{"delta":{"content":"{\\"patchText\\": \\"*** Beg"},'
               b'"finish_reason":"length"}]}\n\n')
        yield b"data: [DONE]\n\n"

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        _post(monkeypatch, proj, gen)

    said = [r.getMessage() for r in caplog.records if "ended the answer early" in r.getMessage()]
    assert said, [r.getMessage() for r in caplog.records]
    assert "finish_reason='length'" in said[0]
    # The chunk carried a fragment of the model's patch. A log line is not the place for it —
    # same rule as the repeat brake's, which quotes no shell output.
    assert "patchText" not in said[0] and "*** Beg" not in said[0]


def test_an_ordinary_stream_says_nothing_about_being_cut(monkeypatch, caplog):
    """`finish_reason: null` on every chunk and a healthy `stop` are not cut-offs. A warning on
    those would be a line on every turn, which is a line nobody reads."""
    proj = _shim_project()

    def gen():
        yield b'data: {"choices":[{"delta":{"content":"one"},"finish_reason":null}]}\n\n'
        yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        _post(monkeypatch, proj, gen)

    assert not [r for r in caplog.records if "ended the answer early" in r.getMessage()]
