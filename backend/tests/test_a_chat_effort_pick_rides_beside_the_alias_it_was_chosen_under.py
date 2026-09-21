"""#487: picking a reasoning effort in Chat put "Model default" straight back on the button.

The effort menu is built from the alias the chip shows — the Ask alias when nobody has pinned a
Chat model — but the click sent the store's `model`, which is `''` in exactly that case. The
server read an empty alias as "clear the pick", dropped the level with it, answered 200, and the
picker's read-back of that 200 drew `effortLabel(null)`. No error anywhere; a save that "worked".

Three sites, one per test, so a revert of any one of them reds its own test and no other:

- the composer sends the level beside `effectiveModel` (the JS harness);
- `set_chat_pick` refuses a level with no alias instead of shrugging (the service);
- the route turns that refusal into a 400 the client can restore from (the route).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import app as appmod

from .test_a_build_pick_carries_its_own_effort import _client
from .test_build_says_which_model_it_will_run import _drawn
from .test_orchestrator import _orch

# The alias the chip shows when nothing is pinned. NOT the harness catalog's own `ask`, which
# equals its `plan` — a test about which alias went out needs an answer only one path gives.
ASK = "deepseek/deepseek-v3"


def _chat_effort_pick(level: str, chat_model: str = "") -> dict:
    # `chatModel: ''` is the state the defect lives in — no explicit Chat pick, chip on the Ask
    # alias. The harness seeds `state.model` with exactly what it is given, so the empty string
    # reaches the composer the way the store holds it after a fresh load.
    (row,) = _drawn([{"mode": "plan", "chat": True, "chatModel": chat_model, "catalogAsk": ASK,
                      "chatPickEffort": level}])
    return row


def test_a_level_picked_under_the_ask_alias_is_sent_beside_that_alias():
    row = _chat_effort_pick("high")
    (wrote,) = row["wrote"]
    assert wrote == {"chat_model": ASK, "reasoning_effort": "high"}, wrote
    # The round trip, not just the write: what came back is what the button draws next.
    assert row["serverChatModel"] == ASK
    assert row["serverChatEffort"] == "high"
    assert row["afterEffortSelected"] == ["high"]
    assert "High" in row["afterEffortLabel"], row["afterEffortLabel"]


def test_model_default_with_nothing_pinned_stays_unpinned():
    # The one click that means "follow the slot": no alias goes out, so nothing is pinned, and the
    # server's clear is a clear rather than a refusal.
    row = _chat_effort_pick("default")
    (wrote,) = row["wrote"]
    assert wrote == {"chat_model": None, "reasoning_effort": None}, wrote
    assert row["serverChatModel"] is None
    assert row["afterEffortSelected"] == ["default"]


def test_the_open_menu_marks_the_level_it_holds():
    # `selectedKeys` was missing from this one menu, so even a stored level drew no mark. Read on
    # the mount BEFORE any click — the seeded level is what a reload hands the composer.
    (row,) = _drawn([{"mode": "plan", "chat": True, "chatModel": ASK, "chatEffort": "low"}])
    assert row["chatEffortSelected"] == ["low"]


def test_the_service_refuses_a_level_with_no_alias(tmp_path: Path):
    orch = _orch(tmp_path)
    for empty in (None, "", "auto"):
        with pytest.raises(ValueError, match="rides beside a chat_model"):
            orch.set_chat_pick(empty, "high")
    # A clear is still a clear.
    orch.set_chat_pick("auto", None)
    orch.set_chat_pick("", "default")
    m = orch.project(start_preview=False).status()["model"]
    assert m["chat_model"] is None and m["reasoning_effort"] is None


def test_the_route_answers_400_not_a_200_that_drops_the_level(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    client = TestClient(appmod.control_app, raise_server_exceptions=False)
    response = client.post("/api/project/model", json={"chat_model": None, "reasoning_effort": "high"})
    assert response.status_code == 400, response.text
    assert "rides beside a chat_model" in response.json()["error"]
