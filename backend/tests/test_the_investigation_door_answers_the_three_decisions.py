"""The route behind the card's buttons and the bar's (#386, ADR-0056).

One door for all three answers, because a Thread is on exactly one of them: open, declined, closed.
Three doors would be three ways to end up on two states at once.

An unknown word is a 400 rather than a silent no-op. A decision door that accepted anything and did
nothing would leave the card clicked, the bar unchanged and nothing on screen saying why.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import app as app_module

from .test_chat_turn import _orch


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    orch, _ = _orch(tmp_path)
    monkeypatch.setattr(app_module, "orchestrator", orch)
    return orch, TestClient(app_module.control_app)


@pytest.mark.parametrize("decision,state", [("open", "open"), ("decline", "declined")])
def test_a_decision_is_recorded_and_read_back_off_the_thread(client, decision: str, state: str):
    orch, http = client
    tid = orch.create_thread()["id"]

    answer = http.post(f"/api/threads/{tid}/investigation", json={"decision": decision})

    assert answer.status_code == 200
    assert answer.json()["investigation"]["state"] == state
    # Read back through the door the transcript reads, so a reload draws the same state.
    assert http.get(f"/api/threads/{tid}/context").json()["investigation"]["state"] == state
    assert orch.get_thread(tid)["context"]["investigation"]["state"] == state


def test_opening_twice_keeps_one_grant_and_one_row(client):
    """Two tabs on one conversation each hold a clickable card. The second click must not move the
    grant's own timestamp, or say in the transcript that it opened twice."""
    orch, http = client
    tid = orch.create_thread()["id"]
    first = http.post(f"/api/threads/{tid}/investigation",
                      json={"decision": "open"}).json()["investigation"]

    again = http.post(f"/api/threads/{tid}/investigation", json={"decision": "open"})

    assert again.status_code == 200 and again.json()["investigation"] == first
    assert [r["state"] for r in orch.thread_history(tid)
            if r.get("type") == "investigation-state"] == ["open"]


def test_closing_follows_opening(client):
    orch, http = client
    tid = orch.create_thread()["id"]
    http.post(f"/api/threads/{tid}/investigation", json={"decision": "open"})

    answer = http.post(f"/api/threads/{tid}/investigation", json={"decision": "close"})

    assert answer.status_code == 200
    assert answer.json()["investigation"]["state"] == "closed"


@pytest.mark.parametrize("body", [{}, {"decision": "reopen"}, {"decision": ""}])
def test_a_word_this_door_does_not_know_is_refused(client, body: dict):
    orch, http = client
    tid = orch.create_thread()["id"]

    answer = http.post(f"/api/threads/{tid}/investigation", json=body)

    assert answer.status_code == 400
    assert http.get(f"/api/threads/{tid}/context").json().get("investigation") in (None, {})


@pytest.mark.parametrize("content", [b"", b"not json"])
def test_a_body_that_is_not_json_is_refused_rather_than_a_500(client, content: bytes):
    """The word this door reads lives in the body, so a body that cannot be read is bad input of
    exactly the shape the 400 above is for — not a server fault."""
    orch, http = client
    tid = orch.create_thread()["id"]

    answer = http.post(f"/api/threads/{tid}/investigation", content=content,
                       headers={"Content-Type": "application/json"})

    assert answer.status_code == 400
    assert answer.json()["error"]


def test_an_unknown_thread_is_a_404(client):
    _, http = client
    assert http.post("/api/threads/thr_nope/investigation",
                     json={"decision": "open"}).status_code == 404
