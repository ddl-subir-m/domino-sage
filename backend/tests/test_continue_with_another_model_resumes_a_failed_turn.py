"""Continue with another model: a failed turn resumes on the model the person picks (#569).

ADR-0069 made the `done` row the eligibility promise: `cause` is written only when a turn is
terminal and the old session is confirmed idle, and `stage` says where a resume starts. Before
#569 a person who saw "Pick a different model and try again" had to work out which control to
move and what to retype; the model pickers, the failed-plan replay, the approved-plan retry and
the findings continuation each existed and nothing connected them to the row that said why.

The regression: an eligible failed row has no action route at all. Then one plant per acceptance
criterion:

1. Eligible `cause` rows answer available with their cause; rows without `cause`, a running turn,
   an unconfirmed stop and unrelated failures do not.
2. One click resends the exact original task to the chosen model and effort and records the
   resolved identity, on `chat`, and on `planning` and `implementation` for both stacks.
3. Duplicate and stale references, a moved conversation or app, a newer turn, a changed plan and
   a revoked model cannot start work or a second writer.
4. An approved failed Build enters the implementation model without re-planning; a failed plan
   uses the Plan model and still needs approval.
5. The failed turn's rows stay as they were; nothing it did is replayed.
6. A valid action survives a restart over the same record; a reference this process never saw
   answers `not_found` rather than pretending a claim survived.
7. The click's live `done` and its saved row agree.

The model is FakeOpenCode, so the shim never runs: the router's decision at the moment each prompt
goes out is what proves which model and effort the request would carry, and the shim's own
`note_resolved` is mimicked at that instant so the `done` row carries the identity the way #316
records it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import (
    _CONTINUE_CLICK_TEXT,
    _CONTINUE_REFUSALS,
    Orchestrator,
    _failed_plan_request,
)
from sage.router import llm_router
from sage.router.models import ModelCatalog
from sage.workspace.stack import STACKS
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn, execution_plan
from .test_a_no_action_plan_retry_is_task_focused import _no_action_on
from .test_a_prompt_naming_no_app_asks_what_to_build import ScriptedGateway
from .test_chat_turn import _no_waiting
from .test_chat_turn import _orch as _chat_orch

__all__ = ["_no_waiting"]  # the host's autouse fixture, so a poll never really sleeps here

_ASK = "Build a table of lab samples with a late flag."
_MODEL = "gpt-5.4"      # a fake alias that accepts exactly one effort beside tools: `none`
_EFFORT = "none"


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


_SLEEP = time.sleep   # the real one, read before the autouse fixture stubs it


def _done(rows: list[dict]) -> dict:
    return next(e for e in reversed(rows) if e.get("type") == "done")


def _settled(orch: Orchestrator) -> None:
    """Wait out the Chat save that follows every Chat turn. It holds the raw turn lock with no
    turn in it, and a ticket admitted meanwhile queues behind it (#79) rather than running."""
    for _ in range(500):
        timer = orch._chat_save_timer
        if timer is not None and timer.is_alive():
            timer.join(0.1)
            continue
        if not orch._turn_lock.locked() and orch._chat_savers == 0:
            return
        _SLEEP(0.01)
    raise AssertionError("the Chat save never settled")


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def _build_orch(tmp: Path, turns: list[Turn], *, stack: str) -> tuple[Orchestrator, FakeOpenCode]:
    """A Build Project on one of the two seeded stacks, ready to gate its first request."""
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.create_app(stack=stack)
    orch.project(start_preview=False)
    return orch, oc


def _watch_routing(orch: Orchestrator, oc: FakeOpenCode) -> list[dict]:
    """The router's decision at each send, and the shim's record of it (#316), so the row can say
    what ran. The fake never reaches `/v1`, so this is the one place the decision is observable."""
    seen: list[dict] = []
    original = oc.send_prompt

    def send(*args, **kwargs):
        original(*args, **kwargs)
        project = orch.project(start_preview=False)
        decision = llm_router.resolve(project.control.snapshot(), project.shim.catalog)
        seen.append({"model": decision.model, "effort": decision.effort})
        project.note_resolved(decision.model, "turn", decision.reason.value,
                              protocol="chat", effort=decision.effort, native=False)

    oc.send_prompt = send
    return seen


def _failed_chat(tmp: Path, *after: Turn):
    orch, oc = _chat_orch(tmp, [Turn(invalid_calls=["write"]), Turn(invalid_calls=["write"]),
                                *after])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "summarize the file"))
    done = _done(orch.thread_history(tid))
    assert done["cause"] == "invalid_tool_call" and done["stage"] == "chat"
    _settled(orch)
    return orch, oc, tid, done["turnId"]


def _failed_implementation(tmp: Path, stack: str, *after: Turn):
    """A plan approved and built on `stack`, whose build stopped twice in the same step."""
    entry = STACKS[stack].entry_file
    orch, oc = _build_orch(tmp, [Turn(text=execution_plan(files=entry)),
                                 Turn(invalid_calls=["write"]), Turn(invalid_calls=["write"]),
                                 *after], stack=stack)
    tid = orch.create_thread()["id"]
    assert _done(list(orch.build_stream(_ASK, conversation=tid)))["decision"] == "awaiting approval"
    done = _done(list(orch.approve_stream(conversation=tid)))
    assert done["cause"] == "invalid_tool_call" and done["stage"] == "implementation"
    app = orch.project(start_preview=False).app_for_turn()
    assert app.read_plan_retry_step() == 1, "the plan still owes a build"
    return orch, oc, tid, app.app_id, done["turnId"]


def _failed_planning(tmp: Path, stack: str, *after: Turn):
    """A gated first request on `stack` whose plan model produced nothing twice."""
    orch, oc = _build_orch(tmp, [Turn(), Turn(), *after], stack=stack)
    tid = orch.create_thread()["id"]
    _no_action_on(oc, orch.project(start_preview=False), 1, 2)
    done = _done(list(orch.build_stream(_ASK, conversation=tid)))
    assert done["cause"] == "model_no_action" and done["stage"] == "planning"
    app = orch.project(start_preview=False).app_for_turn()
    return orch, oc, tid, app.app_id, done["turnId"]


def _click(orch: Orchestrator, turn_id: str, conversation: str, app: str,
           model: str = _MODEL, effort: str | None = _EFFORT) -> list[dict]:
    return list(orch.continue_turn_stream(turn_id, conversation, app, model, effort))


def _sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines()
            if line.startswith("data: ")]


# --- the regression -----------------------------------------------------------------------------


def test_an_eligible_failed_row_has_an_action_and_a_route(tmp_path: Path, monkeypatch):
    """The regression. A saved `done` row that carries `cause` must answer an availability read
    and accept the click; before #569 neither the service nor the control app knew the turn."""
    from sage.orchestrator import app as appmod

    orch, _oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="never asked for"))
    assert hasattr(orch, "continue_availability"), "no eligibility reader on the service"
    monkeypatch.setattr(appmod, "orchestrator", orch)
    response = TestClient(appmod.control_app).get(
        "/api/project/turn/continue",
        params={"turnId": turn_id, "conversation": tid, "app": ""})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "available": True, "turnId": turn_id, "conversation": tid, "app": "",
        "stage": "chat", "cause": "invalid_tool_call", "reason": "", "message": ""}


# --- 1. eligibility is the `done` row ------------------------------------------------------------


def test_only_a_row_with_a_cause_is_eligible(tmp_path: Path):
    orch, _oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="An answer."))
    assert orch.continue_availability(turn_id, tid, "")["available"] is True

    # An ordinary ending with no `cause` on the next turn: not eligible, and it supersedes the
    # eligible one.
    list(orch.chat_stream(tid, "one more"))
    _settled(orch)
    later = _done(orch.thread_history(tid))
    assert later["ok"] is True and "cause" not in later
    answer = orch.continue_availability(later["turnId"], tid, "")
    assert (answer["available"], answer["reason"]) == (False, "not_eligible")
    assert answer["message"] == _CONTINUE_REFUSALS["not_eligible"]
    assert orch.continue_availability(turn_id, tid, "")["reason"] == "superseded"


def test_a_failed_row_without_a_cause_is_not_eligible(tmp_path: Path):
    """A refused idle confirmation ends the turn with no `cause` (#567); the row is failed and
    still not eligible, because the old session may still be writing."""
    orch, _oc, tid, _ = _failed_chat(tmp_path)
    store = ThreadStore(orch.project(start_preview=False).record.path)
    # Plant the shape the refused-stop path writes: failed, no `cause`, its own turn id.
    store.append_history(tid, {"type": "user", "text": "again"})
    store.append_history(tid, {"type": "done", "ok": False, "decision": "broken tool call",
                               "recoveries": 1, "turnId": "turn_refused_stop"})
    answer = orch.continue_availability("turn_refused_stop", tid, "")
    assert (answer["available"], answer["reason"], answer["stage"]) == (False, "not_eligible", "")


def test_a_running_turn_and_an_unconfirmed_stop_are_not_eligible(tmp_path: Path):
    orch, _oc, tid, turn_id = _failed_chat(tmp_path)
    # The failed turn's ticket is (by construction) the last one to run; a turn with no `done`
    # row yet whose ticket holds the lock is `active`, not `not_found`.
    ticket, state = orch.prepare_stream_turn("turn_live", kind="chat", conversation=tid)
    assert state == "running"
    try:
        assert orch.continue_availability("turn_live", tid, "")["reason"] == "active"
        assert orch.continue_availability(turn_id, tid, "")["reason"] == "busy"
    finally:
        orch.release_stream_turn(ticket)
    assert orch.continue_availability(turn_id, tid, "")["available"] is True
    orch._turn_wedged = True
    assert orch.continue_availability(turn_id, tid, "")["reason"] == "wedged"


def test_the_handoff_helper_plan_path_is_never_eligible(tmp_path: Path):
    """`_record_plan_refusal` writes an `error` row and no `done` row, so nothing there carries
    `cause` and no turn id names it: `not_found` is the honest answer, not a card."""
    orch, _oc, tid, _ = _failed_chat(tmp_path)
    store = ThreadStore(orch.project(start_preview=False).record.path)
    store.append_history(tid, {"type": "error", "reason": "plan", "message": "no plan"})
    assert orch.continue_availability("turn_helper_plan", tid, "")["reason"] == "not_found"


def test_a_bare_continue_after_a_stage_planning_row_replays_by_stage_alone():
    """ADR-0069: `stage` retires `_failed_plan_request`'s guess, which read a decision plus the
    planning error beside it. A gated broken-call stop matched neither."""
    rows = [{"type": "user", "text": _ASK},
            {"type": "done", "ok": False, "decision": "broken tool call", "turnId": "t1"}]
    assert _failed_plan_request(rows, "continue") is None
    rows[1]["stage"] = "planning"
    assert _failed_plan_request(rows, "continue") == _ASK


# --- 2 and 4. one click, the exact task, the chosen model and effort, each stage ---------------


def test_a_chat_click_resends_the_question_to_the_chosen_model_and_effort(tmp_path: Path):
    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="There are three columns."))
    routed = _watch_routing(orch, oc)
    before = list(orch.thread_history(tid))

    events = _click(orch, turn_id, tid, "")

    assert len(oc.prompts) == 3
    assert oc.prompts[2]["agent"] == "sage-chat"
    assert "summarize the file" in oc.prompts[2]["text"]
    assert _CONTINUE_CLICK_TEXT not in oc.prompts[2]["text"], "the click is not the task"
    assert routed == [{"model": _MODEL, "effort": _EFFORT}]
    done = _done(events)
    assert done["ok"] is True and done["decision"] == "answered"
    assert done["resolved"]["model"] == _MODEL and done["resolved"]["effort"] == _EFFORT
    assert [e["text"] for e in events if e.get("kind") == "text"] == ["There are three columns."]
    # The click's bubble opens the new Attempt; the failed turn's rows are exactly as they were.
    rows = orch.thread_history(tid)
    assert rows[:len(before)] == before
    assert rows[len(before)]["type"] == "user"
    assert rows[len(before)]["text"] == _CONTINUE_CLICK_TEXT
    # #487: the pick stands as a pair, on the standing Chat control.
    state = orch.project(start_preview=False).control.snapshot()
    assert (state.chat_model, state.reasoning_effort) == (_MODEL, _EFFORT)
    assert orch.continue_availability(turn_id, tid, "")["reason"] == "superseded"


def test_a_chat_click_resends_the_pending_question_not_the_reply(tmp_path: Path):
    """A source clarification keeps the question as `pendingTask` and the reply as the bubble
    (#566); the click resends the question."""
    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="Forty rows."))
    store = ThreadStore(orch.project(start_preview=False).record.path)
    store.update_context(tid, lambda ctx: {**ctx, "pendingTask": {
        "id": "task_1", "question": "how many rows are in the file", "awaiting": "",
        "sourceIds": [], "offerDecision": ""}})

    events = _click(orch, turn_id, tid, "")

    assert _done(events)["ok"] is True
    assert "how many rows are in the file" in oc.prompts[2]["text"]
    assert "summarize the file" not in oc.prompts[2]["text"]


@pytest.mark.parametrize("stack", ["react-vite", "fastapi-antd"])
def test_an_approved_failed_build_resumes_on_the_implement_path_without_replanning(
        tmp_path: Path, stack: str):
    entry = STACKS[stack].entry_file
    orch, oc, tid, app_id, turn_id = _failed_implementation(
        tmp_path, stack, Turn(text="Done.", writes={entry: "// continued\n"}))
    routed = _watch_routing(orch, oc)
    plan_id = orch.project(start_preview=False).app_for_turn().live_plan_doc_id()
    before = list(orch.project(start_preview=False).app_for_turn().read_history(tid))
    answer = orch.continue_availability(turn_id, tid, app_id)
    assert (answer["available"], answer["stage"], answer["cause"]) == (
        True, "implementation", "invalid_tool_call")

    events = _click(orch, turn_id, tid, app_id)

    done = _done(events)
    assert done["ok"] is True, done
    assert not _of(events, "plan-proposed"), "an approved plan is never re-planned (#498)"
    assert len(oc.prompts) == 4 and oc.prompts[3]["agent"] == "sage-implement"
    assert routed == [{"model": _MODEL, "effort": _EFFORT}]
    assert done["resolved"]["model"] == _MODEL and done["resolved"]["effort"] == _EFFORT
    app = orch.project(start_preview=False).app_for_turn()
    assert (app.path / entry).read_text() == "// continued\n"
    rows = app.read_history(tid)
    assert rows[:len(before)] == before
    assert rows[len(before)]["type"] == "user" and rows[len(before)]["text"] == _CONTINUE_CLICK_TEXT
    # The build consumed the plan this time: the same document, nothing left owing.
    assert app.read_plan_retry_step() == 0 and not app.read_plan()
    assert orch.project(start_preview=False).record.read_plan_doc(plan_id) is not None
    saved = _done(rows)
    assert {k: saved[k] for k in ("ok", "decision", "turnId", "resolved")} == {
        k: done[k] for k in ("ok", "decision", "turnId", "resolved")}


@pytest.mark.parametrize("stack", ["react-vite", "fastapi-antd"])
def test_a_failed_plan_replays_its_request_on_the_plan_model_and_still_needs_approval(
        tmp_path: Path, stack: str):
    entry = STACKS[stack].entry_file
    orch, oc, tid, app_id, turn_id = _failed_planning(
        tmp_path, stack, Turn(text=execution_plan(files=entry)))
    routed = _watch_routing(orch, oc)
    before = list(orch.project(start_preview=False).app_for_turn().read_history(tid))
    answer = orch.continue_availability(turn_id, tid, app_id)
    assert (answer["available"], answer["stage"], answer["cause"]) == (
        True, "planning", "model_no_action")

    events = _click(orch, turn_id, tid, app_id)

    done = _done(events)
    assert done["ok"] is True and done["decision"] == "awaiting approval", done
    assert _of(events, "plan-proposed")
    assert len(oc.prompts) == 3 and oc.prompts[2]["agent"] == "sage-plan"
    sent = oc.prompts[2]["text"] + (oc.prompts[2].get("tail") or "")
    assert _ASK in sent and _CONTINUE_CLICK_TEXT not in sent
    assert routed == [{"model": _MODEL, "effort": _EFFORT}]
    assert done["resolved"]["model"] == _MODEL and done["resolved"]["effort"] == _EFFORT
    app = orch.project(start_preview=False).app_for_turn()
    assert (app.read_plan() or "").strip(), "the replayed plan waits for approval"
    rows = app.read_history(tid)
    assert rows[:len(before)] == before
    assert rows[len(before)]["type"] == "user" and rows[len(before)]["text"] == _CONTINUE_CLICK_TEXT
    state = orch.project(start_preview=False).control.snapshot()
    assert (state.picked_model, state.picked_effort) == (_MODEL, _EFFORT)


# --- 3. what cannot start work or a second writer -----------------------------------------------


def test_a_duplicate_click_is_superseded_by_the_first(tmp_path: Path):
    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="ok"), Turn(text="never"))
    assert _done(_click(orch, turn_id, tid, ""))["ok"] is True
    _settled(orch)
    events = _click(orch, turn_id, tid, "")
    done = _done(events)
    assert done["ok"] is False and done["decision"] == "continue unavailable"
    assert done["reason"] == "superseded"
    assert _of(events, "error")[0]["message"] == _CONTINUE_REFUSALS["superseded"]
    assert len(oc.prompts) == 3, "the second click sent nothing"
    assert orch._turns.running() is None, "and released the lock"
    # A refusal under the lock writes nothing: the record is exactly the first click's.
    assert _done(orch.thread_history(tid))["decision"] == "answered"


def test_a_stale_or_moved_reference_cannot_start_the_wrong_work(tmp_path: Path):
    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="never"))
    other = orch.create_thread()["id"]
    assert orch.continue_availability(turn_id, other, "")["reason"] == "not_found"
    assert orch.continue_availability("turn_gone", tid, "")["reason"] == "not_found"
    assert orch.continue_availability(turn_id, "thr_nobody", "")["reason"] == "not_found"
    for reference in ((turn_id, other, ""), ("turn_gone", tid, "")):
        assert _done(_click(orch, *reference))["reason"] == "not_found"
    assert len(oc.prompts) == 2


def test_a_newer_turn_supersedes_the_click(tmp_path: Path):
    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="newer"), Turn(text="never"))
    list(orch.chat_stream(tid, "a newer question"))
    _settled(orch)
    assert orch.continue_availability(turn_id, tid, "")["reason"] == "superseded"
    assert _done(_click(orch, turn_id, tid, ""))["reason"] == "superseded"
    assert len(oc.prompts) == 3


def test_a_changed_or_archived_plan_refuses_the_click(tmp_path: Path):
    orch, oc, tid, app_id, turn_id = _failed_implementation(tmp_path, "react-vite")
    app = orch.project(start_preview=False).app_for_turn()
    app.write_plan(app.read_plan() + "\n### 2. Extra\n- Do — more\n")   # an edit resets the point
    assert orch.continue_availability(turn_id, tid, app_id)["reason"] == "plan_changed"
    app.archive_plan()
    assert orch.continue_availability(turn_id, tid, app_id)["reason"] == "plan_changed"
    assert _done(_click(orch, turn_id, tid, app_id))["reason"] == "plan_changed"
    assert len(oc.prompts) == 3


def test_moving_the_rail_to_another_app_refuses_the_click(tmp_path: Path):
    orch, oc, tid, app_id, turn_id = _failed_implementation(tmp_path, "react-vite")
    orch.create_app(stack="react-vite")
    assert orch.project(start_preview=False).workspace.app_id != app_id
    assert orch.continue_availability(turn_id, tid, app_id)["reason"] == "app_changed"
    assert _done(_click(orch, turn_id, tid, app_id))["reason"] == "app_changed"
    assert len(oc.prompts) == 3


def test_a_revoked_model_or_a_refused_effort_starts_nothing(tmp_path: Path):
    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="never"))
    accepted = orch.route_capability(_MODEL).efforts_with_tools
    bad_effort = next(e for e in ("high", "max", "xhigh", "low") if e not in accepted)
    assert orch.continue_availability(turn_id, tid, "", model="", effort=None)["reason"] == (
        "model_required")
    assert orch.continue_availability(turn_id, tid, "", model=_MODEL, effort=bad_effort)[
        "reason"] == "effort_unavailable"
    assert orch.continue_availability(turn_id, tid, "", model="no-such-alias", effort=None)[
        "reason"] == "model_unavailable"
    # The model was allowed when the card was drawn and is not when the click lands.
    orch._resources.aliases = [a for a in orch._resources.aliases if a.name != _MODEL]
    answer = orch.continue_availability(turn_id, tid, "", model=_MODEL, effort=_EFFORT)
    assert (answer["available"], answer["reason"]) == (False, "model_unavailable")
    assert _done(_click(orch, turn_id, tid, ""))["reason"] == "model_unavailable"
    assert len(oc.prompts) == 2
    state = orch.project(start_preview=False).control.snapshot()
    assert state.chat_model is None, "a refused click moves no control"


def test_the_route_refuses_a_second_writer_and_a_rewritten_task(tmp_path: Path, monkeypatch):
    from sage.orchestrator import app as appmod

    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="never"))
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)
    body = {"turnId": turn_id, "conversation": tid, "app": "", "model": _MODEL,
            "effort": _EFFORT}
    # A task from the browser is not a field this route has.
    response = client.post("/api/project/turn/continue", json={**body, "prompt": "do X instead"})
    assert response.status_code == 400
    response = client.post("/api/project/turn/continue", json={"turnId": turn_id})
    assert response.status_code == 400
    response = client.post("/api/project/turn/continue", json={**body, "conversation": "../x"})
    assert response.status_code == 400
    # While a turn holds the lock, the click is refused before any ticket is admitted.
    ticket, _ = orch.prepare_stream_turn("turn_live", kind="chat", conversation=tid)
    try:
        response = client.post("/api/project/turn/continue", json=body)
        assert response.status_code == 409
        assert response.json()["reason"] == "busy"
        assert orch._turns.depth() == 0, "nothing queued behind the running turn"
    finally:
        orch.release_stream_turn(ticket)
    orch._turn_wedged = True
    response = client.post("/api/project/turn/continue", json=body)
    assert response.status_code == 409 and response.json()["reason"] == "wedged"
    assert len(oc.prompts) == 2


def test_the_route_streams_the_new_attempt_with_its_own_turn_id(tmp_path: Path, monkeypatch):
    from sage.orchestrator import app as appmod

    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="Continued answer."))
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)
    response = client.post("/api/project/turn/continue", json={
        "turnId": turn_id, "conversation": tid, "app": "", "model": _MODEL, "effort": _EFFORT})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    new_turn = response.headers["X-Sage-Turn-Id"]
    assert new_turn.startswith("turn_") and new_turn != turn_id
    assert response.headers["X-Sage-Turn-State"] == "running"
    events = _sse(response.text)
    assert events[0] == {"type": "user", "text": _CONTINUE_CLICK_TEXT, "contextIds": [],
                         "context": []}
    done = _done(events)
    assert done["ok"] is True and done["turnId"] == new_turn
    assert len(oc.prompts) == 3
    assert orch._turns.running() is None
    # Availability now says why the card is gone.
    response = client.get("/api/project/turn/continue",
                          params={"turnId": turn_id, "conversation": tid, "app": ""})
    assert response.json()["reason"] == "superseded"
    response = client.get("/api/project/turn/continue", params={"turnId": "", "conversation": tid})
    assert response.status_code == 400


# --- 6. restart ---------------------------------------------------------------------------------


def test_a_valid_action_survives_a_restart_and_a_lost_claim_says_so(tmp_path: Path):
    orch, oc, tid, turn_id = _failed_chat(tmp_path)
    # A turn that was running when the process died: its ticket is gone and no `done` row was
    # ever written. Nothing pretends it survived.
    ticket, _ = orch.prepare_stream_turn("turn_in_flight", kind="chat", conversation=tid)
    assert orch.continue_availability("turn_in_flight", tid, "")["reason"] == "active"

    fresh = FakeOpenCode(oc.workspace, [Turn(text="After the restart.")])
    again = Orchestrator(workspace_dir=oc.workspace, template=tmp_path / "template",
                         gateway=ScriptedGateway(), project_id="Sage", feedback=OkFeedback(),
                         catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                              sovereign_ask="s", plan="p", implement="i", ask="a"),
                         opencode_client=fresh)
    again.project(start_preview=False)

    assert again.continue_availability("turn_in_flight", tid, "")["reason"] == "not_found"
    answer = again.continue_availability(turn_id, tid, "")
    assert (answer["available"], answer["stage"], answer["cause"]) == (
        True, "chat", "invalid_tool_call")
    events = _click(again, turn_id, tid, "")
    assert _done(events)["ok"] is True
    assert "summarize the file" in fresh.prompts[0]["text"]
    orch.release_stream_turn(ticket)
