"""A plan adds nothing the request did not ask for, and its sections agree (#594).

Measured live on Haiku: a request for an AE table, enrollment by arm and a subject drill-down came
back as a plan whose What it does and Screens offered an arm filter, and whose Not doing ruled out
interactive filtering. The person approves a contract that contradicts itself.

Deciding that "a Control to filter arms" conflicts with "no interactive filtering" is a semantic
judgment, so there is no validator here. The fix is two sentences in `_PLAN_DOC_SECTIONS`, and
these tests pin that both reach each of the two sends that carry it: the gated plan turn and the
Chat handoff.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import _PLAN_DOC_SECTIONS, Orchestrator

from .fake_opencode import Turn

NO_EXTRAS = "Add no capability, screen or control the request did not ask for.\n"
SECTIONS_AGREE = ("Nothing under 'Not doing' may exclude anything named in 'What it does' or "
                  "'Screens'.\n")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def test_the_plan_sections_state_both_rules():
    assert NO_EXTRAS in _PLAN_DOC_SECTIONS
    assert SECTIONS_AGREE in _PLAN_DOC_SECTIONS


def test_the_gated_plan_turn_sends_both_rules(tmp_path: Path):
    from .test_a_prompt_naming_no_app_asks_what_to_build import _REFUSAL, _build, _run

    orch, oc = _build(tmp_path, [Turn(text=_REFUSAL)])
    _run(orch, "build me an adverse events table")

    sent = oc.prompts[0]["text"]
    assert NO_EXTRAS in sent and SECTIONS_AGREE in sent


def test_the_chat_handoff_plan_sends_both_rules(tmp_path: Path):
    from .test_a_plan_document_captions_without_naming import NAMED, _orch

    orch, _gateway, _root = _orch(tmp_path, [Turn(text="A table, then."), Turn(text=NAMED)])
    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "build me an adverse events table"))
    orch.draft_handoff_plan(thread)

    client = orch._oc_client
    sent = client.prompts[-1]["text"]
    assert client.prompts[-1]["agent"] == "sage-plan"
    assert NO_EXTRAS in sent and SECTIONS_AGREE in sent
