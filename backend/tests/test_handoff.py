"""Chat → Build detect-once classifier.

Every test drives a stub gateway. The failure paths matter more than the happy one: a wrong
suggestion every few messages is the failure the spec calls intolerable, so timeout and error
must stay silent, and APP is the only hit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.router.models import ModelCatalog

CATALOG = ModelCatalog(
    sovereign_plan="sov-plan", sovereign_implement="sov-implement", sovereign_ask="sov-ask",
    plan="plan-model", implement="implement-model", ask="ask-model",
)


class StubGateway:
    def __init__(self, verdict: str = "CHAT", *, sse: bool = True, raises: Exception | None = None):
        self.verdict = verdict
        self.sse = sse
        self.raises = raises
        self.seen: list[tuple[dict, object]] = []

    def route(self, request, labels):
        self.seen.append((request, labels))
        if self.raises is not None:
            raise self.raises
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield (f"data: {body}\n\ndata: [DONE]\n\n" if self.sse else body).encode()


def _ask(gateway, user="put this on a dashboard colleagues can open", **kw):
    return handoff.wants_an_app(
        title="Gross exposure by desk",
        user=user,
        assistant="Here is notional by desk.",
        gateway=gateway,
        catalog=CATALOG,
        **kw,
    )


@pytest.fixture(autouse=True)
def _fresh_health():
    handoff._health.reset()
    yield
    handoff._health.reset()


def test_app_verdict_suggests_and_chat_verdict_does_not():
    assert _ask(StubGateway("APP")) is True
    assert _ask(StubGateway("CHAT")) is False


@pytest.mark.parametrize("verdict", ["app", "  APP\n", "App."])
def test_the_verdict_is_read_leniently(verdict):
    assert _ask(StubGateway(verdict)) is True


def test_a_whole_json_body_parses_like_a_stream():
    assert _ask(StubGateway("APP", sse=False)) is True


def test_an_upstream_failure_does_not_suggest():
    assert _ask(StubGateway(raises=RuntimeError("gateway 502"))) is False


def test_a_timeout_does_not_hang_the_turn():
    import threading
    import time

    released = threading.Event()

    class Hanging:
        def route(self, request, labels):
            released.wait(30)
            yield b""

    started = time.monotonic()
    try:
        assert _ask(Hanging(), timeout_s=0.2) is False
        assert time.monotonic() - started < 5
    finally:
        released.set()


@pytest.mark.parametrize("verdict", ["MAYBE", "", "I think this is an app"])
def test_an_answer_outside_the_vocabulary_suggests(verdict):
    assert _ask(StubGateway(verdict)) is True


def test_three_unreadable_answers_in_a_row_trip_the_breaker(caplog):
    gw = StubGateway("")
    n = handoff.MAX_UNREADABLE
    assert [_ask(gw) for _ in range(n)] == [True] * (n - 1) + [False]
    assert any(r.levelname == "ERROR" and "BROKEN" in r.message for r in caplog.records)
    calls = len(gw.seen)
    assert _ask(gw) is False
    assert len(gw.seen) == calls


def test_a_readable_verdict_clears_the_streak():
    for _ in range(10):
        assert _ask(StubGateway("")) is True
        assert _ask(StubGateway("CHAT")) is False
    assert handoff._health.broken is False


def test_errors_and_timeouts_do_not_count_towards_broken():
    for _ in range(handoff.MAX_UNREADABLE * 2):
        assert _ask(StubGateway(raises=RuntimeError("gateway 502"))) is False
    assert handoff._health.broken is False


def test_the_prompt_is_title_plus_last_turn_not_the_whole_history():
    gw = StubGateway("CHAT")
    _ask(gw, user="put this on a dashboard colleagues can open")
    user_content = gw.seen[0][0]["messages"][1]["content"]
    assert "Thread title: Gross exposure by desk" in user_content
    assert "User: put this on a dashboard colleagues can open" in user_content
    assert "Assistant: Here is notional by desk." in user_content
    assert gw.seen[0][0]["messages"][0]["content"].count("Default to CHAT") == 1


@pytest.mark.parametrize("prompt", [
    "build me a dashboard",
    "Build me an app",
    "open this in the builder",
    "open it in Build",
    "turn this into an app",
    "make it an app",
])
def test_explicit_build_requests_skip_the_classifier(prompt):
    assert handoff.looks_like_build_request(prompt) is True


@pytest.mark.parametrize("prompt", [
    "what's our gross exposure by desk?",
    "put this on a dashboard colleagues can open",
    "show me a chart",
    "build a pivot of this CSV",
])
def test_analysis_is_not_an_explicit_build_request(prompt):
    assert handoff.looks_like_build_request(prompt) is False


def test_should_classify_only_when_handoff_is_absent():
    assert handoff.should_classify(None) is True
    assert handoff.should_classify({}) is True
    assert handoff.should_classify({"suggestedAt": "2026-08-25T18:20:00Z"}) is False
    assert handoff.should_classify({"suppressed": True}) is False
    assert handoff.should_classify({"status": "suggested"}) is False
    assert handoff.should_classify({"status": "bound"}) is False


def test_digest_is_one_paragraph_with_names_not_bytes():
    text = handoff.draft_digest(
        title="Gross exposure",
        asked=["what's our exposure?", "put this on a dashboard colleagues can open"],
        context=[{"kind": "data_source", "name": "trades"}],
        artifacts=[{"title": "By desk", "path": "examples/thr_1/desk.table.json",
                    "png": "this-must-not-appear"}],
    )
    assert "Gross exposure" in text
    assert "trades" in text
    assert "examples/thr_1/desk.table.json" in text
    assert "this-must-not-appear" not in text
    assert "\n\n" not in text


def test_plan_prompt_points_at_digest_and_examples():
    prompt = handoff.plan_prompt("thr_1", "Thread background.")
    assert "examples/thr_1/" in prompt
    assert ".sage/handoff.md" in prompt
    assert "The plan is what to build" in prompt


def test_implement_note_is_empty_without_a_digest(tmp_path: Path):
    assert handoff.implement_note(tmp_path) == ""


def test_implement_note_includes_digest_and_example_paths(tmp_path: Path):
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "handoff.md").write_text("Thread background.\n")
    dest = tmp_path / "examples" / "thr_1"
    dest.mkdir(parents=True)
    (dest / "desk.table.json").write_text("[]")
    note = handoff.implement_note(tmp_path)
    assert "The plan is what to build" in note
    assert "Thread background." in note
    assert "examples/thr_1/desk.table.json" in note


def test_binding_from_context_only_for_resources():
    b = handoff.binding_from_context({
        "kind": "data_source", "name": "trades",
        "bindingKey": ["data_source", "ds-1"],
    })
    assert b is not None
    assert b.kind == "data_source"
    assert b.id == "ds-1"
    assert handoff.binding_from_context({"kind": "file", "name": "a.csv", "path": "public/data/a.csv"}) is None
    assert handoff.binding_from_context({"kind": "data_source", "id": "ctx_01", "name": "x"}) is None
