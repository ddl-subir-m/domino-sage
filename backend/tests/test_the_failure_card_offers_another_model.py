"""The failure card's Continue with another model action, as the Workbench draws and sends it (#570).

ADR-0069 made `cause` on a failed `done` row the eligibility promise, and #569 built the route:
`GET /api/project/turn/continue` says whether the action is available and why not, and the POST
takes the server-owned reference (`turnId`, `conversation`, `app`) plus the model and effort the
person picked, and streams the new Attempt. This file is the browser half. The store derives a
`continue_model` block from a `cause` row on both transcripts, asks the GET once per reload, and
the card lets the person pick a currently allowed model and a compatible effort from the same
capability source the pickers read, says what the pick's scope is, and sends the reference and
the pick, never a task.

The regression: a `cause` row draws no action at all before #570. Then one plant per acceptance
criterion, named in each test's docstring. The harness is `continue_model_card_harness.mjs`; the
route checks drive #569's real route with the exact body the harness recorded, so what the browser
sends and what the server does with it are one chain rather than two claims.

The real-Chromium pass was NOT run here: this session cannot boot Sage. The owner's click path is
in the WORKER report on #570.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator.service import _CONTINUE_CLICK_TEXT, _CONTINUE_REFUSALS
from sage.workspace.stack import STACKS

from .fake_opencode import Turn
from .test_a_shape_only_table_artifact_renders_as_a_receipt import _node, needs_node
from .test_continue_with_another_model_resumes_a_failed_turn import (
    _EFFORT,
    _MODEL,
    _done,
    _failed_chat,
    _failed_implementation,
    _settled,
    _sse,
    _watch_routing,
)

HARNESS = "continue_model_card_harness.mjs"
QUESTION = "summarize the file"
FAILED = "This turn ended after two tool calls that could not be read."

A_FAILED_CHAT_TURN = [
    {"type": "user", "text": QUESTION},
    {"type": "error", "message": FAILED},
    {"type": "done", "ok": False, "decision": "broken tool call", "recoveries": 1,
     "cause": "invalid_tool_call", "stage": "chat", "turnId": "turn_a"},
]
A_FAILED_TURN_WITHOUT_A_CAUSE = [
    {"type": "user", "text": QUESTION},
    {"type": "error", "message": "The turn was stopped."},
    {"type": "done", "ok": False, "decision": "stopped", "turnId": "turn_b"},
]
APP = {"id": "app_1", "name": "Lab samples", "stack": "react-vite"}
A_FAILED_BUILD = [
    {"type": "user", "text": "Build a table of lab samples.", "app": "app_1",
     "conversation": "thr_1"},
    {"type": "error", "message": FAILED, "app": "app_1", "conversation": "thr_1"},
    {"type": "done", "ok": False, "decision": "broken tool call", "recoveries": 1,
     "cause": "invalid_tool_call", "stage": "implementation", "turnId": "turn_c",
     "app": "app_1", "conversation": "thr_1"},
]
AVAILABLE = {"available": True, "turnId": "turn_a", "conversation": "thr_1", "app": "",
             "stage": "chat", "cause": "invalid_tool_call", "reason": "", "message": ""}
OPEN = "Continue with another model"


def _refusal(reason: str, **scope) -> dict:
    return {"available": False, "turnId": "turn_a", "conversation": "thr_1", "app": "",
            "stage": "chat", "cause": "invalid_tool_call", "reason": reason,
            "message": _CONTINUE_REFUSALS[reason], **scope}


def _run(**payload) -> dict:
    return _node(HARNESS, payload)


def _continue_calls(result: dict) -> list[dict]:
    return [c for c in result["calls"] if "turn/continue" in c["url"]]


# --- the regression -----------------------------------------------------------------------------


@needs_node
def test_a_cause_row_draws_the_card_and_a_row_without_one_draws_nothing():
    """The regression, and criterion 1: the card appears only for `cause` rows. Before #570 the
    Chat reader had no branch for a failed `done` at all, so the row fell on the floor."""
    with_cause = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE)
    assert [c["turnId"] for c in with_cause["cards"]] == ["turn_a"]
    assert with_cause["cards"][0]["cause"] == "invalid_tool_call"
    # Drawn beside the existing detailed failure, not instead of it.
    assert any(b == f"status:{FAILED}" for m in with_cause["transcript"] for b in m["blocks"])

    without = _run(history=A_FAILED_TURN_WITHOUT_A_CAUSE, get=AVAILABLE)
    assert without["cards"] == []
    assert not any("turn/continue" in r for r in without["allRoutes"])


# --- what the card says and offers ---------------------------------------------------------------


@needs_node
def test_the_card_names_the_cause_neutrally_and_offers_one_action():
    """Criterion 4 (copy): sentence case, no exclamation point, and no blame — the cause text
    names what happened to the turn and not whose fault it was."""
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE)
    (card,) = result["before"]
    text = " ".join(card["text"])
    assert "tool call" in text.lower()
    assert "!" not in text
    for word in ("gateway", "vendor", "provider", "Sage", "Domino"):
        assert word not in text, text
    for sentence in card["text"]:
        assert sentence[:1] == sentence[:1].upper()
    assert card["buttons"] == [OPEN]


@needs_node
def test_the_picker_offers_allowed_models_and_the_efforts_the_model_accepts():
    """Criterion 2: the existing capability source. Chat-capable aliases only, a row that is not
    serving is offered but closed, and the effort list is the alias's own measured one."""
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="open")
    picker = result["picker"]
    (model,) = [s for s in picker["selects"] if s["label"] == "Model"]
    offered = {o["value"]: o["disabled"] for o in model["options"]}
    assert offered == {"gpt-5.4": False, "claude-x": True}, "embeddings-only rows are not models"
    (effort,) = [s for s in picker["selects"] if s["label"] == "Reasoning effort"]
    assert [o["value"] for o in effort["options"]] == ["default", "none"]
    # The scope of the pick, said before the click: it is the standing pick, not a one-off.
    assert any("later turns" in t for t in picker["text"])
    # And what will run, named from the pick, on the card itself.
    assert any("gpt-5.4" in t for t in picker["text"])
    assert [b["label"] for b in picker["buttons"]] == ["Continue", "Cancel"]


@needs_node
def test_a_model_without_levels_offers_no_effort_control():
    aliases = [{"id": "llm_9", "kind": "model_llm", "alias": "plain-1", "name": "Plain",
                "capabilities": ["chat"], "reasoning_efforts_with_tools": [], "serving": True}]
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="open", aliases=aliases,
                  pick={"model": "plain-1"})
    assert [s["label"] for s in result["picker"]["selects"]] == ["Model"]


# --- the click -----------------------------------------------------------------------------------


@needs_node
def test_a_cancelled_picker_posts_nothing():
    """Criterion 1: cancelled picker. The picker closes and no request leaves the browser."""
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="cancel")
    assert result["calls"] == []
    assert result["pickerState"] is None
    assert result["after"][0]["buttons"] == [OPEN], "the action is still there to take"


@needs_node
@pytest.mark.parametrize("app", [None, APP], ids=["chat", "build"])
def test_the_click_sends_the_reference_and_the_pick_and_never_a_task(app):
    """Criterion 3: #569's server-owned reference and the chosen model and effort, and nothing
    else — the route refuses a `prompt`, and the browser never has one to send."""
    history = A_FAILED_BUILD if app else A_FAILED_CHAT_TURN
    turn = "turn_c" if app else "turn_a"
    answer = {**AVAILABLE, "turnId": turn, "app": app["id"] if app else "",
              "stage": "implementation" if app else "chat"}
    result = _run(history=history, app=app, get=answer, act="continue",
                  post={"status": 200, "frames": [
                      {"type": "agent", "kind": "text", "text": "Done."},
                      {"type": "done", "ok": True, "decision": "answered", "turnId": "turn_new",
                       "resolved": {"model": "gpt-5.4", "effort": "none"}}]})
    (posted,) = [c for c in _continue_calls(result) if c["method"] == "POST"]
    assert posted["url"] == "api/project/turn/continue"
    assert posted["body"] == {"turnId": turn, "conversation": "thr_1",
                              "app": app["id"] if app else "", "model": "gpt-5.4",
                              "effort": "none"}
    # The chip names what will run: the standing pick moved with the click (#569 sets it through
    # `set_chat_pick` / `control.pick`, and the store mirrors that the way the pickers do).
    if app:
        assert (result["buildModel"], result["buildEffort"]) == ("gpt-5.4", "none")
    else:
        assert (result["model"], result["reasoningEffort"]) == ("gpt-5.4", "none")
    # The click's bubble is on screen in the server's words, and the answer under it.
    users = [b for m in result["transcript"] if m["role"] == "user" for b in m["blocks"]]
    assert f"text:{_CONTINUE_CLICK_TEXT}" in users
    assert any("text:Done." in b for m in result["transcript"] for b in m["blocks"])


@needs_node
def test_a_stale_action_renders_the_servers_reason_and_reads_no_stream():
    """Criterion 1: stale action. A 409 `superseded` is drawn as the server's sentence, with the
    reason word kept, and the action goes."""
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="continue",
                  post={"status": 409, "body": _refusal("superseded")})
    assert [c["method"] for c in _continue_calls(result)] == ["POST"]
    assert not any("chat/stream" in c["url"] for c in result["calls"])
    (card,) = result["after"]
    assert card["reason"] == "superseded"
    assert _CONTINUE_REFUSALS["superseded"] in card["text"]
    assert card["buttons"] == []


@needs_node
def test_a_refused_model_save_submits_nothing_and_moves_no_pick():
    """Criterion 1: denied save. The route refuses the pick before any turn is admitted; the
    browser sends one POST, reads no stream, and the standing pick stays where it was."""
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="continue",
                  post={"status": 409, "body": _refusal("model_unavailable")})
    assert [c["method"] for c in _continue_calls(result)] == ["POST"]
    assert (result["model"], result["reasoningEffort"]) == ("", None)
    (card,) = result["after"]
    assert card["reason"] == "model_unavailable"
    assert _CONTINUE_REFUSALS["model_unavailable"] in card["text"]
    # No second bubble: the server recorded nothing, so the click's echo came back off.
    users = [b for m in result["transcript"] if m["role"] == "user" for b in m["blocks"]]
    assert users == [f"text:{QUESTION}"]


@needs_node
@pytest.mark.parametrize("post", [{"status": 500}, {"throw": True}], ids=["500", "no-reply"])
def test_a_request_error_renders_its_explained_state_and_keeps_the_action(post):
    """Criterion 1: request error. A reply that is not the route's own is said in one sentence
    on the card, and the action stays, because nothing has changed about the turn."""
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="continue", post=post)
    (card,) = result["after"]
    assert card.get("reason") is None
    assert any("did not go through" in t for t in card["text"])
    assert OPEN in card["buttons"]
    assert (result["model"], result["reasoningEffort"]) == ("", None)


# --- refresh, navigation, restart ---------------------------------------------------------------


@needs_node
def test_a_reload_asks_the_route_once_and_restores_the_action():
    """Criterion 3: refresh and navigation. The card is rebuilt from the saved row and the GET
    is asked once, on the read, not on a poll."""
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE)
    gets = [r for r in result["allRoutes"] if r == "GET api/project/turn/continue"]
    assert gets == ["GET api/project/turn/continue"]
    (card,) = result["before"]
    assert card["buttons"] == [OPEN]
    assert result["offers"]["turn_a"]["available"] is True


@needs_node
def test_a_lost_claim_after_a_restart_says_why():
    """Criterion 3: the after-restart answer. A reference this process never saw answers
    `not_found`, and the card says so instead of pretending a claim survived."""
    result = _run(history=A_FAILED_CHAT_TURN, get=_refusal("not_found"))
    (card,) = result["before"]
    assert card["reason"] == "not_found"
    assert _CONTINUE_REFUSALS["not_found"] in card["text"]
    assert card["buttons"] == []


@needs_node
def test_an_older_cause_row_is_a_record_and_only_the_newest_is_asked_about():
    """Two failed turns: the older row keeps its cause text as a record with no action and no
    GET of its own. The server would answer `superseded`; the transcript already says so."""
    history = A_FAILED_CHAT_TURN + [
        {"type": "user", "text": "again"},
        {"type": "error", "message": FAILED},
        {"type": "done", "ok": False, "decision": "broken tool call", "recoveries": 1,
         "cause": "invalid_tool_call", "stage": "chat", "turnId": "turn_a2"},
    ]
    result = _run(history=history, get={**AVAILABLE, "turnId": "turn_a2"})
    assert [c["turnId"] for c in result["cards"]] == ["turn_a", "turn_a2"]
    assert [c["record"] for c in result["cards"]] == [True, False]
    older, newer = result["before"]
    assert older["buttons"] == [] and newer["buttons"] == [OPEN]
    assert result["allRoutes"].count("GET api/project/turn/continue") == 1
    assert set(result["offers"]) == {"turn_a2"}


@needs_node
def test_a_failed_availability_read_is_said_on_the_card():
    result = _run(history=A_FAILED_CHAT_TURN, get="error")
    (card,) = result["before"]
    assert card["buttons"] == []
    assert any("could not be checked" in t for t in card["text"])


# --- the live stream -----------------------------------------------------------------------------


@needs_node
def test_a_live_cause_row_draws_the_card_without_asking_the_route():
    """The `done` that arrived over SSE is the eligibility promise itself; the GET is for a
    reload."""
    result = _run(history=[], live=[
        {"type": "error", "message": FAILED},
        {"type": "done", "ok": False, "decision": "broken tool call", "recoveries": 1,
         "cause": "invalid_tool_call", "stage": "chat", "turnId": "turn_live"}])
    (card,) = result["before"]
    assert card["buttons"] == [OPEN]
    assert not any(r.startswith("GET api/project/turn/continue") for r in result["allRoutes"])


@needs_node
def test_a_continue_unavailable_stream_renders_an_explained_state():
    """Criterion 6: the live-only `decision: "continue unavailable"`. The refusal found again
    under the lock streams an `error` and a `done`; the card takes the reason and the transcript
    is not left blank or marked as a platform fault."""
    result = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="continue",
                  post={"status": 200, "frames": [
                      {"type": "error", "message": _CONTINUE_REFUSALS["superseded"],
                       "reason": "superseded"},
                      {"type": "done", "ok": False, "decision": "continue unavailable",
                       "reason": "superseded", "turnId": "turn_new"}]})
    (card,) = result["after"]
    assert card["reason"] == "superseded"
    assert _CONTINUE_REFUSALS["superseded"] in card["text"]
    assert card["buttons"] == []
    # Nothing ran, so nothing was written: the click's bubble is not on screen.
    users = [b for m in result["transcript"] if m["role"] == "user" for b in m["blocks"]]
    assert users == [f"text:{QUESTION}"]


def test_the_store_knows_the_live_only_decision():
    """Criterion 6, pinned on the tables themselves: the decision is not a platform fault and
    needs no status line of its own beside the `error` frame that carries the sentence."""
    store = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "store.js").read_text()
    gate = re.search(r"const GATE_DECISIONS = \{(.*?)\n  \};", store, re.DOTALL).group(1)
    fault = re.search(r"const NO_PLATFORM_FAULT = \{(.*?)\};", store, re.DOTALL).group(1)
    assert "'continue unavailable': true" in gate
    assert "'continue unavailable': true" in fault


# --- the route, with the body the browser sends ---------------------------------------------------


def _client(orch, monkeypatch) -> TestClient:
    from sage.orchestrator import app as appmod
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app)


def _recorded_post(history: list[dict], app: dict | None, turn_id: str, conversation: str,
                   app_id: str) -> dict:
    """What the browser would send for this failed turn: the harness's POST body, with the
    identity the real record carries put in place of the fixture's."""
    rows = [dict(r) for r in history]
    for row in rows:
        if row.get("type") == "done":
            row["turnId"] = turn_id
        if app:
            row["app"] = app_id
    answer = {"available": True, "turnId": turn_id, "conversation": conversation, "app": app_id,
              "stage": "implementation" if app else "chat", "cause": "invalid_tool_call",
              "reason": "", "message": ""}
    result = _run(history=rows, app=app, get=answer, act="continue",
                  post={"status": 200, "frames": [{"type": "done", "ok": True, "turnId": "t"}]})
    (posted,) = [c for c in _continue_calls(result) if c["method"] == "POST"]
    body = dict(posted["body"])
    body["conversation"] = conversation
    return body


@needs_node
def test_a_chat_failure_continues_on_the_model_the_card_sent(tmp_path: Path, monkeypatch):
    """Criterion 2 (route check, Chat): failure, choose another model, resume, real result. The
    body is the one the harness recorded; the server is #569's with a scripted model."""
    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="There are three columns."))
    routed = _watch_routing(orch, oc)
    client = _client(orch, monkeypatch)
    body = _recorded_post(A_FAILED_CHAT_TURN, None, turn_id, tid, "")
    assert body == {"turnId": turn_id, "conversation": tid, "app": "", "model": _MODEL,
                    "effort": _EFFORT}
    response = client.post("/api/project/turn/continue", json=body)
    assert response.status_code == 200, response.text
    events = _sse(response.text)
    done = _done(events)
    assert done["ok"] is True and done["decision"] == "answered"
    # The model the card sent is the model the router resolved for the new Attempt (#316).
    assert routed == [{"model": _MODEL, "effort": _EFFORT}]
    assert done["resolved"]["model"] == _MODEL and done["resolved"]["effort"] == _EFFORT
    assert [e["text"] for e in events if e.get("kind") == "text"] == ["There are three columns."]
    assert len(oc.prompts) == 3 and QUESTION in oc.prompts[2]["text"]
    _settled(orch)
    # And the card's next reload says why it is gone.
    stale = client.get("/api/project/turn/continue",
                       params={"turnId": turn_id, "conversation": tid, "app": ""}).json()
    assert stale["reason"] == "superseded"
    result = _run(history=A_FAILED_CHAT_TURN, get=stale)
    assert result["before"][0]["reason"] == "superseded"
    assert result["before"][0]["buttons"] == []


@needs_node
@pytest.mark.parametrize("stack", ["react-vite", "fastapi-antd"])
def test_an_approved_failed_build_continues_on_the_model_the_card_sent(
        tmp_path: Path, monkeypatch, stack: str):
    """Criterion 2 (route check, Build): an approved failed Build resumes on the implementation
    path with the body the card sent, and never re-plans."""
    entry = STACKS[stack].entry_file
    orch, oc, tid, app_id, turn_id = _failed_implementation(
        tmp_path, stack, Turn(text="Done.", writes={entry: "// continued\n"}))
    routed = _watch_routing(orch, oc)
    client = _client(orch, monkeypatch)
    body = _recorded_post(A_FAILED_BUILD, {**APP, "id": app_id, "stack": stack}, turn_id, tid,
                          app_id)
    assert body == {"turnId": turn_id, "conversation": tid, "app": app_id, "model": _MODEL,
                    "effort": _EFFORT}
    response = client.post("/api/project/turn/continue", json=body)
    assert response.status_code == 200, response.text
    events = _sse(response.text)
    assert not [e for e in events if e.get("type") == "plan-proposed"]
    done = _done(events)
    assert done["ok"] is True, done
    assert done["turnId"] == response.headers["X-Sage-Turn-Id"]
    assert len(oc.prompts) == 4 and oc.prompts[3]["agent"] == "sage-implement"
    assert routed == [{"model": _MODEL, "effort": _EFFORT}]
    app = orch.project(start_preview=False).app_for_turn()
    assert (app.path / entry).read_text() == "// continued\n"


@needs_node
def test_a_denied_save_and_a_stale_click_are_refused_by_the_route_and_drawn_by_the_card(
        tmp_path: Path, monkeypatch):
    """Criteria 1 and 2 (route checks): a model this person cannot run is refused before any turn
    is admitted, and the refusal body is what the card draws; a click after a newer turn is
    `superseded` the same way."""
    orch, oc, tid, turn_id = _failed_chat(tmp_path, Turn(text="never asked for"))
    client = _client(orch, monkeypatch)
    body = _recorded_post(A_FAILED_CHAT_TURN, None, turn_id, tid, "")
    denied = client.post("/api/project/turn/continue", json={**body, "model": "no-such-alias"})
    assert denied.status_code == 409 and denied.json()["reason"] == "model_unavailable"
    assert len(oc.prompts) == 2, "a refused pick submits nothing"
    drawn = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="continue",
                 post={"status": 409, "body": denied.json()})
    assert drawn["after"][0]["reason"] == "model_unavailable"
    assert _CONTINUE_REFUSALS["model_unavailable"] in drawn["after"][0]["text"]

    list(orch.chat_stream(tid, "one more"))
    _settled(orch)
    stale = client.post("/api/project/turn/continue", json=body)
    assert stale.status_code == 409 and stale.json()["reason"] == "superseded"
    assert len(oc.prompts) == 3
    drawn = _run(history=A_FAILED_CHAT_TURN, get=AVAILABLE, act="continue",
                 post={"status": 409, "body": stale.json()})
    assert drawn["after"][0]["reason"] == "superseded"


def test_the_copy_uses_the_routes_sentences_and_no_words_of_its_own_for_a_refusal():
    """Criterion 4: the unavailable states are the route's one sentence each, keyed on its
    `reason` word; the card carries no second table of reasons to drift from it."""
    blocks = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "components"
              / "message-blocks.js").read_text()
    card = blocks[blocks.index("function ContinueWithAnotherModel"):]
    card = card[:card.index("\n  }\n") + 4]
    for reason in _CONTINUE_REFUSALS:
        assert f"'{reason}'" not in card and f'"{reason}"' not in card, reason
    for sentence in re.findall(r"'([^'\n]{12,})'", card):
        assert "!" not in sentence, sentence
        # Sentence case: a sentence starts with a capital, and the capitals after it are names.
        if sentence.endswith("."):
            assert sentence[0].isupper(), sentence
    assert json.dumps(_CONTINUE_REFUSALS)  # the vocabulary this file keys on is the route's
