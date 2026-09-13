"""The turn's record of which model actually ran, and which rule put it there (#316).

Four rules move a request off the model a person picked — an Ask-mode pin, the signing pin, the
sensitivity lock and the unsigned veto — and before this the only witness was a log line inside the
Builder. The panel showed the PICK, so "the guardrail tripped and I was not on gpt-5.4" could not be
answered from anything the product kept: three code routes land on gpt-5.4 and the screen named
whichever model the person had chosen.

The record is the LAST decision of the turn, because that is the one whose behaviour the person is
asking about, and because a phased build resolves per phase.

Two halves, tested apart because they fail apart: the shim knows the reason and hands it over
(`on_resolved`), and the turn's terminal row carries what was handed over. The wiring between them
is the third test — it is the only place both ends are real at once.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import FakeOpenCode, Turn


def _catalog(**over) -> ModelCatalog:
    base = {"sovereign_plan": "sovereign-8b", "sovereign_implement": "sovereign-8b",
            "sovereign_ask": "sovereign-8b", "plan": "gpt-5.4",
            "implement": "bedrock-qwen3-coder", "ask": "gpt-5.4"}
    return ModelCatalog(**{**base, **over})


def _unsigned_history() -> list[dict]:
    """A transcript that already holds a tool call carrying no `thought_signature`.

    This is the shape that makes a signing model a hard 400 on the WHOLE request (#155), so it is
    the shape that fires the veto. Written out rather than borrowed from a helper because the
    absence of the signature IS the fixture — a helper that grew a signature would silently stop
    testing the veto and go on passing.
    """
    return [{"role": "user", "content": "hi"},
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"}]


def _resolved(control: ModelControl, catalog: ModelCatalog, messages=None) -> tuple[str, str, str]:
    """Run one request through the real shim and report what it told the caller ran."""
    seen: list[tuple[str, str, str]] = []
    gw = FakeGatewayClient()
    shim = EnforcementShim(control, catalog, gw)
    list(shim.handle({"model": "whatever-opencode-had", "messages": messages or []},
                     project="p", on_resolved=lambda *a: seen.append(a)))
    assert seen, "the shim resolved a model and told nobody"
    return seen[-1]


# The five cases from #316, as measured at the router seam. Each is a rule that moves the request
# off the pick, plus the one case where nothing moves it — which is here because a record that only
# appears when something went wrong cannot be read as "nothing went wrong".
def _chat(c: ModelControl) -> ModelControl:
    c.arm_chat("t1")
    return c


@pytest.mark.parametrize("case,setup,catalog,messages,model,reason", [
    (
        "a Chat pick is honoured",
        lambda c: _chat(c).pick_chat("sonnet"),
        _catalog(), None, "sonnet", "chat-override",
    ),
    (
        "Ask mode ignores the pick",
        lambda c: (c.set_mode(Mode.ASK), c.pick("sonnet")),
        _catalog(), None, "gpt-5.4", "ask-pinned",
    ),
    (
        "one signing assignment pins the whole Build session",
        lambda c: (c.set_mode(Mode.AUTO), c.set_phase(Phase.PLAN)),
        _catalog(implement="gemini-3.7-flash"), None, "gemini-3.7-flash", "signing-pin",
    ),
    (
        "the sensitivity lock outranks the pick",
        lambda c: (_chat(c).pick_chat("sonnet"), c.arm_sensitivity(frozenset({"haiku"}))),
        _catalog(), None, "haiku", "sensitivity",
    ),
    (
        "the veto drops the person's own pick",
        lambda c: _chat(c).pick_chat("gemini-3.7-flash"),
        _catalog(), _unsigned_history(), "gpt-5.4", "signing-veto",
    ),
])
def test_the_shim_says_which_model_ran_and_which_rule_chose_it(case, setup, catalog, messages,
                                                               model, reason):
    c = ModelControl()
    setup(c)
    got_model, _phase, got_reason = _resolved(c, catalog, messages)
    assert got_model == model, f"{case}: ran on {got_model}, expected {model}"
    assert got_reason == reason, f"{case}: reason was {got_reason!r}, expected {reason!r}"


# Every kind of turn, against a control an Auto build just left in IMPLEMENT — the only setup in
# which a stale phase can show itself. A fresh `ModelControl` defaults to PLAN, so the same
# assertions written against a fresh one pin the DEFAULT and pass whether the phase was computed for
# this turn or inherited from the last one.
#
# Both halves matter. Chat and Ask have no phase and must report none; the pinned modes have one
# their own `set_mode` just made true (`ModelControl._sync_phase`) and must keep it. A fix that
# blanked all four would pass the first two lines and quietly throw away the second two.
@pytest.mark.parametrize("case,setup,phase", [
    ("Chat has no phases", lambda c: (_chat(c), c.pick_chat("sonnet")), ""),
    ("Ask mode has none either", lambda c: c.set_mode(Mode.ASK), ""),
    ("Plan mode's own phase is a fact", lambda c: c.set_mode(Mode.PLAN), "plan"),
    ("Implement mode's likewise", lambda c: c.set_mode(Mode.IMPLEMENT), "implement"),
    ("Auto reclassifies per step", lambda c: c.set_mode(Mode.AUTO), "plan"),
])
def test_a_turn_reports_a_phase_only_where_one_is_a_fact_about_it(case, setup, phase):
    c = ModelControl(mode=Mode.AUTO, phase=Phase.IMPLEMENT)
    setup(c)
    _model, got, _reason = _resolved(c, _catalog())
    assert got == phase, f"{case}: reported phase {got!r}, expected {phase!r}"


def test_the_veto_is_told_apart_from_the_fallback_it_lands_on():
    """#317 writes "your pick of X can't run on this conversation" off this one value.

    The trap it has to survive: the model the veto lands on is the model an ordinary Chat turn with
    NO pick would have run anyway, and the router's own `resolve_unsigned` returns that path's
    reason (`chat-default`). Two turns then run gpt-5.4 for completely different reasons and the
    record cannot tell #317 which sentence to write. The shim rebinds the reason at the veto, and
    this is the test that keeps it rebound.
    """
    picked = ModelControl()
    picked.arm_chat("t1")
    picked.pick_chat("gemini-3.7-flash")
    vetoed_model, _, vetoed_reason = _resolved(picked, _catalog(), _unsigned_history())

    plain = ModelControl()
    plain.arm_chat("t1")
    plain_model, _, plain_reason = _resolved(plain, _catalog(), _unsigned_history())

    assert vetoed_model == plain_model == "gpt-5.4", "the two cases must land on the same model"
    assert vetoed_reason == "signing-veto", f"the veto is invisible: {vetoed_reason!r}"
    assert plain_reason == "chat-default", f"an ordinary turn reads as a veto: {plain_reason!r}"


def test_the_turns_ledger_names_the_model_that_ran_not_the_one_asked_for(monkeypatch):
    """The /v1 wiring: one inference, and both readers of "which model ran" hear the same answer.

    Both, in one test, because they are fed by one callback. A test per reader would pass with the
    callback wired to only one of them, which is the exact shape of a reader lagging one at a time.
    """
    from fastapi.testclient import TestClient

    from sage import timing
    from sage.orchestrator import app as orchmod
    from sage.shim import keepalive as ka

    monkeypatch.setattr(ka, "FIRST_BYTE_BUDGET_S", 0.05)
    monkeypatch.setattr(ka, "KEEPALIVE_INTERVAL_S", 0.05)

    def handle(body, project, session=None, on_resolved=None, **_):
        if on_resolved is not None:
            on_resolved("gpt-5.4", "implement", "signing-veto")

        def gen():
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'

        return gen()

    # The real `Project.note_resolved`, bound to a stand-in: the point of this test is that BOTH
    # readers hear the callback, so the recording half has to be the real one or it proves nothing.
    from sage.orchestrator.service import Project
    proj = types.SimpleNamespace(
        id="p", session_id="s", active_session_id=None,
        shim=types.SimpleNamespace(handle=handle),
        model_calls=0, tool_call_responses=0, last_gateway_error=None,
        resolved_model=None,
    )
    proj.note_resolved = lambda *a: Project.note_resolved(proj, *a)
    monkeypatch.setattr(orchmod, "orchestrator", types.SimpleNamespace(project=lambda: proj))
    timing.start_turn("build", "who ran this")
    TestClient(orchmod.control_app).post("/v1/chat/completions",
                                         json={"model": "gemini-3.7-flash", "messages": []})
    timing.finish_turn(ok=True, decision="-")
    rec = timing.recent(1)[0]

    call = timing.as_dict(rec)["calls"][0]
    assert call["model"] == "gpt-5.4", "the ledger kept the model OpenCode asked for"
    assert call["reason"] == "signing-veto", f"the ledger has no reason: {call!r}"

    assert proj.resolved_model is not None, "nothing was recorded on the project"
    assert proj.resolved_model.model == "gpt-5.4"
    assert proj.resolved_model.reason == "signing-veto"
    assert proj.resolved_model.phase == "implement"


# --- the turn's terminal row ---------------------------------------------------------------------

class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def __init__(self, verdict: str = "CHAT"):
        self.verdict = verdict
        self.seen: list = []

    def route(self, request, labels):
        self.seen.append((request, labels))
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class ResolvingOpenCode(FakeOpenCode):
    """A fake that calls back the way the real one does: an inference, mid-turn, through the shim.

    `FakeOpenCode` never reaches `/v1/chat/completions`, so nothing in the harness would ever record
    a resolved model and every assertion below would pass against an empty record. Sending the
    callback from `send_prompt` puts it where the real one happens — after the turn has taken the
    lock, which is the ordering the staleness test depends on.
    """

    resolve_to: tuple[str, str, str] | None = None
    project = None

    def send_prompt(self, *a, **k):
        if self.project is not None and self.resolve_to is not None:
            self.project.note_resolved(*self.resolve_to)
        return super().send_prompt(*a, **k)


def _orch(tmp: Path, turns: list[Turn], catalog: ModelCatalog | None = None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = ResolvingOpenCode(ws, turns)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=catalog or _catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc)
    oc.project = orch.project(start_preview=False)
    return orch, oc


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    from sage.orchestrator import handoff
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def test_a_chat_turn_ends_on_a_row_that_names_the_model_that_ran(tmp_path: Path):
    """And the same row after a reload, because the transcript is what a person comes back to.

    Asserted off the file rather than the live stream: the stream is stamped by the object the
    generator yields and the history by the bytes already on disk, and stamping the first without
    the second is a record that exists only while the page stays open.
    """
    orch, oc = _orch(tmp_path, [Turn(text="an answer")])
    oc.resolve_to = ("gpt-5.4", "", "signing-veto")
    thread = orch.create_thread()["id"]

    live = [ev for ev in orch.chat_stream(thread, "why is it slow?") if ev["type"] == "done"]
    assert live and live[-1].get("resolved") == {
        "model": "gpt-5.4", "reason": "signing-veto", "phase": ""}

    from sage.workspace.threads import ThreadStore
    rows = [r for r in ThreadStore(orch.project().record.path).read_history(thread)
            if r["type"] == "done"]
    assert rows, "the Chat turn wrote no terminal row"
    assert rows[-1].get("resolved") == {
        "model": "gpt-5.4", "reason": "signing-veto", "phase": ""}, (
        f"a reload shows no model: {rows[-1]!r}")


def test_a_build_turn_ends_on_a_row_that_names_the_model_that_ran(tmp_path: Path):
    orch, oc = _orch(tmp_path, [Turn(text="built", writes={"src/App.tsx": "export default 1\n"})])
    oc.resolve_to = ("bedrock-qwen3-coder", "implement", "auto-implement")

    done = [ev for ev in orch.build_stream("make a chart") if ev["type"] == "done"]
    assert done, "the Build turn wrote no terminal row"
    assert done[-1].get("resolved") == {
        "model": "bedrock-qwen3-coder", "reason": "auto-implement", "phase": "implement"}


def test_a_build_turn_that_ran_no_model_names_none_rather_than_the_last_one(tmp_path: Path):
    """A turn admitted straight to the lock forgets the previous turn's model.

    This case and the Chat one below look like the same test written twice and are not: a turn
    reaches the clear by whichever of the two routes `_acquire_turn` grants it on, and Build is
    admitted with no queue while a second Chat turn waits behind the first turn's own save. Only one
    of the two call sites runs in either test, so the pair is what makes deleting either one red.
    """
    orch, oc = _orch(tmp_path, [Turn(text="first"), Turn(text="second")])
    oc.resolve_to = ("bedrock-qwen3-coder", "implement", "auto-implement")
    list(orch.build_stream("build me one"))

    oc.resolve_to = None                       # the next turn reaches no model at all
    done = [ev for ev in orch.build_stream("and another") if ev["type"] == "done"]
    assert done, "the second Build turn wrote no terminal row"
    assert "resolved" not in done[-1], (
        f"the second turn inherited a model it never ran: {done[-1]!r}")


def test_a_chat_turn_that_ran_no_model_names_none_rather_than_the_last_one(tmp_path: Path):
    """The lie both of these prevent: a turn that never reached the gateway inheriting the previous
    turn's model and reading, on the transcript, exactly like a turn that ran on it.

    A missing key is the honest answer and #317 must read it as "nothing ran", never as a standing
    value — so this asserts absence, not an empty string.
    """
    orch, oc = _orch(tmp_path, [Turn(text="first"), Turn(text="second")])
    oc.resolve_to = ("gpt-5.4", "plan", "chat-override")
    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "a first question"))

    oc.resolve_to = None                       # the next turn reaches no model at all
    list(orch.chat_stream(thread, "a second question"))

    from sage.workspace.threads import ThreadStore
    rows = [r for r in ThreadStore(orch.project().record.path).read_history(thread)
            if r["type"] == "done"]
    assert len(rows) == 2, f"expected two terminal rows, got {len(rows)}"
    assert rows[0].get("resolved", {}).get("model") == "gpt-5.4"
    assert "resolved" not in rows[1], f"the second turn inherited a model it never ran: {rows[1]!r}"
