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
from sage.orchestrator.service import _PLAN_SHAPE, _PLAN_VOICE
from sage.workspace import plan_doc
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
    # Live, this one fell through to the classifier — which cannot run before a turn — so it burned
    # the whole turn timeout and a model call before answering "that is Build's job".
    "lets convert this into an app i can share",
    "convert it into an app",
    "can you make that an app",
])
def test_explicit_build_requests_skip_the_classifier(prompt):
    assert handoff.looks_like_build_request(prompt) is True


@pytest.mark.parametrize("prompt", [
    "what's our gross exposure by desk?",
    "put this on a dashboard colleagues can open",
    "show me a chart",
    "build a pivot of this CSV",
    # "convert" is a build verb only when what follows is an app. It is a very ordinary word.
    "convert the timestamps to dates",
    "turn this into a percentage",
    "which app category converts best?",
])
def test_analysis_is_not_an_explicit_build_request(prompt):
    assert handoff.looks_like_build_request(prompt) is False


def test_should_classify_only_while_the_newest_handoff_is_unresolved():
    assert handoff.should_classify(None) is True
    assert handoff.should_classify([]) is True
    assert handoff.should_classify([{"suggestedAt": "2026-08-25T18:20:00Z"}]) is False
    assert handoff.should_classify([{"suppressed": True}]) is False
    assert handoff.should_classify([{"status": "suggested"}]) is False
    assert handoff.should_classify([{"status": "planned"}]) is False


def test_a_bound_thread_is_eligible_again_and_a_declined_one_never_is():
    """Handoff spec §8, criterion 10. A conversation that built an app may drift toward another —
    `bound` is a step finishing. `Not now` is the person saying stop, and that answer stands however
    many handoffs come after it."""
    assert handoff.should_classify([{"status": "bound"}]) is True
    assert handoff.should_classify([{"status": "bound"}, {"status": "suggested"}]) is False
    assert handoff.should_classify([{"status": "bound"}, {"status": "bound"}]) is True
    assert handoff.should_classify([{"status": "suppressed"}]) is False
    assert handoff.should_classify([{"status": "suppressed"}, {"status": "bound"}]) is False


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


def _plan_prompt(digest: str = "Thread background.") -> str:
    return handoff.plan_prompt("thr_1", digest, voice=_PLAN_VOICE, shape=_PLAN_SHAPE)


def test_plan_prompt_points_at_examples_and_asks_for_a_plan():
    prompt = _plan_prompt()
    assert "examples/thr_1/" in prompt
    assert "Thread background." in prompt
    assert "Write a concrete build plan" in prompt


def test_plan_prompt_does_not_carry_the_implement_line():
    """The plan turn is the one WRITING the plan, so "the plan is what to build" points at nothing.

    Live, a planner given that sentence planned a page about the work rather than the app."""
    prompt = _plan_prompt()
    assert "The plan is what to build" not in prompt
    assert "no app has been built yet" in prompt


def test_plan_prompt_asks_for_the_headings_the_plan_document_is_parsed_from():
    """The handoff writes a plan document, and the document IS these headings.

    Asking only for "a concrete build plan" left the shape to the agent prompt alone, which did not
    hold: sage-plan answered the digest in narration. Prose has no headings, so every section parsed
    empty and the plan page showed a title over eight blank ones while the transcript held the whole
    plan. docs/workbench/handoff.md §5 has always asked for the sections; this asserts the prompt does.
    """
    prompt = _plan_prompt()
    for section in plan_doc.SECTIONS:
        assert f"## {section.label}" in prompt


def test_plan_prompt_pins_the_proposal_voice():
    """Same reason the gated turn pins it: the narration that broke the shape was past tense."""
    prompt = _plan_prompt()
    assert "future tense" in prompt


def test_implement_note_is_empty_without_a_digest(tmp_path: Path):
    assert handoff.implement_note(tmp_path) == ""


def test_implement_note_is_one_line_and_the_digest(tmp_path: Path):
    digest = handoff.confirm_digest(
        "Thread background.",
        artifacts=[{"path": "examples/thr_1/desk.table.json"}],
        context=[],
        include_artifacts=True,
        include_resources=False,
    )
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "handoff.md").write_text(digest)
    note = handoff.implement_note(tmp_path)
    assert "The plan is what to build" in note
    assert "Thread background." in note
    # The paths reach the model through the digest, which is the only copy of them.
    assert "examples/thr_1/desk.table.json" in note
    assert note.count("examples/thr_1/desk.table.json") == 1
    assert note.count("The plan is what to build") == 1


def test_implement_note_omits_artifacts_the_user_unchecked(tmp_path: Path):
    """Unchecking Artifacts on the sheet omits them from the digest (handoff.md §4). The note used
    to walk `examples/` itself, which listed them back to the model and undid the checkbox."""
    digest = handoff.confirm_digest(
        "Thread background.",
        artifacts=[{"path": "examples/thr_1/desk.table.json"}],
        context=[],
        include_artifacts=False,
        include_resources=False,
    )
    (tmp_path / ".sage").mkdir()
    (tmp_path / ".sage" / "handoff.md").write_text(digest)
    dest = tmp_path / "examples" / "thr_1"
    dest.mkdir(parents=True)
    (dest / "desk.table.json").write_text("[]")
    assert "desk.table.json" not in handoff.implement_note(tmp_path)


def test_confirm_digest_leaves_the_implement_line_to_the_note():
    digest = handoff.confirm_digest(
        "Thread background.", artifacts=[], context=[],
        include_artifacts=False, include_resources=False,
    )
    assert "The plan is what to build" not in digest


def test_binding_from_context_only_for_resources():
    b = handoff.binding_from_context({
        "kind": "data_source", "name": "trades",
        "bindingKey": ["data_source", "ds-1"],
    })
    assert b is not None
    assert b.kind == "data_source"
    assert b.id == "ds-1"
    table = handoff.binding_from_context({
        "kind": "data_source",
        "name": "DIM_ACCOUNT",
        "bindingKey": ["data_source", "ds-dwh"],
        "resourceId": "table:ds-dwh:DWH.MARTS.DIM_ACCOUNT",
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    assert table is not None
    assert table.id == "ds-dwh"
    assert table.table == "DIM_ACCOUNT"
    assert handoff.binding_from_context({"kind": "file", "name": "a.csv", "path": "public/data/a.csv"}) is None
    assert handoff.binding_from_context({"kind": "data_source", "id": "ctx_01", "name": "x"}) is None


def test_binding_a_handoff_keeps_the_plan_document_it_drafted(tmp_path):
    """`mark_handoff_bound` is told the app, not the plan, so the plan id has to survive on the
    entry rather than be re-supplied. Losing it would leave the Thread pointing at nothing."""
    from sage.workspace.threads import ThreadStore

    store = ThreadStore(tmp_path)
    thread_id = store.create("A thread")["id"]

    store.mark_handoff_planned(thread_id, "001")
    store.mark_handoff_bound(thread_id)

    assert store.read_handoff(thread_id)["planId"] == "001"
