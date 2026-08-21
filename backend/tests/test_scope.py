"""Scope classifier: the one model-decided step in the turn path.

Every test drives a stub gateway, so the contract under test is what the classifier does with an
answer — not what any model would say. The failure paths matter more than the happy one: this thing
sits in front of every Auto turn on a built project, so "broken" must degrade to "builds exactly as
it did before" rather than to "nothing builds".
"""
from __future__ import annotations

import json

import pytest

from sage.orchestrator import scope
from sage.orchestrator.service import _scope_gate_applies
from sage.router.models import Mode, ModelCatalog

CATALOG = ModelCatalog(
    sovereign_plan="sov-plan", sovereign_implement="sov-implement", sovereign_ask="sov-ask",
    plan="plan-model", implement="implement-model", ask="ask-model",
)


class StubGateway:
    """Answers with a scripted verdict, and records what it was asked."""

    def __init__(self, verdict: str = "BUILD", *, sse: bool = True, raises: Exception | None = None):
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


def _ask(gateway, prompt="add scheduled retraining", **kw):
    return scope.wants_a_plan(prompt, gateway=gateway, catalog=CATALOG, locked=False, **kw)


@pytest.fixture(autouse=True)
def _fresh_health():
    """The unreadable-answer streak is process-wide (see scope._Health), so without this a test that
    leaves the breaker tripped would silently turn every later test into an assertion about the
    breaker instead of about the verdict it scripted."""
    scope._health.reset()
    yield
    scope._health.reset()


# --- the verdict -------------------------------------------------------------------------------

def test_plan_verdict_gates_and_build_verdict_does_not():
    assert _ask(StubGateway("PLAN")) is True
    assert _ask(StubGateway("BUILD")) is False


@pytest.mark.parametrize("verdict", ["plan", "  PLAN\n", "Plan."])
def test_the_verdict_is_read_leniently(verdict):
    # A one-word contract that fails on a trailing newline would fail open constantly and look like
    # the feature simply not working.
    assert _ask(StubGateway(verdict)) is True


def test_a_whole_json_body_parses_like_a_stream():
    # The Domino client streams SSE; a non-streaming endpoint and the test fake answer with one plain
    # object. The classifier shouldn't care which it's talking to.
    assert _ask(StubGateway("PLAN", sse=False)) is True


# --- failing open ------------------------------------------------------------------------------

def test_an_upstream_failure_builds_instead_of_blocking():
    # The whole point: a classifier that is down must not become a classifier that stops builds.
    assert _ask(StubGateway(raises=RuntimeError("gateway 502"))) is False


def test_a_timeout_builds_instead_of_hanging_the_turn():
    import time

    class Hanging:
        def route(self, request, labels):
            time.sleep(30)
            yield b""

    started = time.monotonic()
    assert _ask(Hanging(), timeout_s=0.2) is False
    # The bound has to be real: an executor shut down with wait=True would block here for the full
    # 30s and the timeout above it would buy nothing. This assertion is the whole test.
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("verdict", ["MAYBE", "", "I think you should plan this one"])
def test_an_answer_outside_the_vocabulary_plans_rather_than_builds(verdict):
    # #29: this returned False, which is not "no signal" — it is a guess, and it guesses the one
    # outcome that writes to the user's app. The call ARRIVED here, so the classifier isn't down;
    # its contract broke, and the safe side of a broken contract is the pause, not the diff.
    #
    # Still no substring matching: the third verdict CONTAINS "plan" and is unreadable all the same,
    # because a rule that read it would turn every chatty refusal into a deliberate-looking gate.
    assert _ask(StubGateway(verdict)) is True


def test_three_unreadable_answers_in_a_row_trip_the_breaker(caplog):
    gw = StubGateway("")
    assert [_ask(gw) for _ in range(scope.MAX_UNREADABLE)] == [True] * (scope.MAX_UNREADABLE - 1) + [False]

    # Declared broken, and it says so at ERROR — this is the line a maintainer finds in
    # /api/diag/log when Sage starts wanting to plan everything. Criterion: surfaced, not only
    # logged per-turn.
    assert any(r.levelname == "ERROR" and "BROKEN" in r.message for r in caplog.records)

    # And it stops being called. A classifier this broken must not cost a gateway round trip per
    # turn, and must not leave every Auto turn behind an approval wall it can never lift: from here
    # the turn builds ungated, exactly as it did before the classifier existed.
    calls = len(gw.seen)
    assert _ask(gw) is False
    assert len(gw.seen) == calls


def test_a_readable_verdict_clears_the_streak():
    # Consecutive is the whole rule. A classifier that is merely flaky — one bad answer between good
    # ones — must never reach the breaker, or a healthy install eventually declares itself broken.
    for _ in range(10):
        assert _ask(StubGateway("")) is True
        assert _ask(StubGateway("BUILD")) is False
    assert scope._health.broken is False


def test_errors_and_timeouts_do_not_count_towards_broken():
    # Down and broken are different failures with different right answers (see the module
    # docstring). A gateway outage must keep failing OPEN however long it lasts — it is not evidence
    # about the model's answers, because no answer arrived.
    for _ in range(scope.MAX_UNREADABLE * 2):
        assert _ask(StubGateway(raises=RuntimeError("gateway 502"))) is False
    assert scope._health.broken is False
    assert _ask(StubGateway("PLAN")) is True


def test_an_empty_prompt_never_calls_the_gateway():
    gw = StubGateway("PLAN")
    assert _ask(gw, prompt="   ") is False
    assert gw.seen == []


# --- what gets sent ----------------------------------------------------------------------------

def test_a_locked_project_classifies_on_the_sovereign_model():
    # The sensitivity lock is the product's central promise; a classification is not an exception to
    # it. Routing this call to a vendor model would leak the prompt off the sovereign path.
    gw = StubGateway("BUILD")
    scope.wants_a_plan("add auth", gateway=gw, catalog=CATALOG, locked=True)
    assert gw.seen[0][0]["model"] == "sov-ask"


def test_an_unlocked_project_classifies_on_the_cheap_ask_model():
    gw = StubGateway("BUILD")
    _ask(gw)
    request, labels = gw.seen[0]
    assert request["model"] == "ask-model"
    # One word out, but the ceiling has to clear a thinking budget: at 8 tokens a route with
    # extended thinking on returns a successful response whose content is "" every single time,
    # which is #29. Still small enough that this can't cost what a build costs.
    assert 8 < request["max_tokens"] <= 512
    assert request["temperature"] == 0
    # Tagged as its own component so orchestration overhead stays separable from build inference.
    assert labels.component == "scope"


def test_a_long_prompt_is_truncated_not_refused():
    gw = StubGateway("BUILD")
    _ask(gw, prompt="x" * 10_000)
    assert len(gw.seen[0][0]["messages"][-1]["content"]) == scope.MAX_PROMPT_CHARS


# --- the app listing ---------------------------------------------------------------------------

def _app(tmp_path, files: dict[str, str]):
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def test_the_listing_names_source_files_with_their_size(tmp_path):
    root = _app(tmp_path, {"src/App.tsx": "a\nb\nc\n", "src/components/Table.tsx": "x\n"})
    ctx = scope.app_context(root)
    assert "src/App.tsx (3 lines)" in ctx
    assert "src/components/Table.tsx (1 lines)" in ctx


def test_scaffolding_is_left_out_of_the_listing(tmp_path):
    # Every project carries an identical template — package.json, the tsconfigs, dist/, public/. It
    # costs tokens and says nothing about THIS app's size, which is the only thing being judged.
    root = _app(tmp_path, {
        "src/App.tsx": "x\n", "package.json": "{}", "dist/index.html": "<html>",
        "public/favicon.svg": "<svg/>", "tsconfig.json": "{}",
    })
    ctx = scope.app_context(root)
    assert "src/App.tsx" in ctx
    for noise in ("package.json", "dist/", "public/", "tsconfig"):
        assert noise not in ctx


def test_a_long_listing_says_how_much_it_left_out(tmp_path):
    # A silently truncated listing would make a large app read as a medium one — the exact
    # misjudgement the context exists to correct.
    root = _app(tmp_path, {f"src/c{i}.tsx": "x\n" for i in range(scope.MAX_FILES + 5)})
    ctx = scope.app_context(root)
    assert ctx.count("\n  src/") == scope.MAX_FILES
    assert "and 5 more files" in ctx


def test_a_missing_or_empty_app_yields_no_context(tmp_path):
    # No context is strictly better than partial context, and the caller can't act on the difference.
    assert scope.app_context(None) == ""
    assert scope.app_context(tmp_path) == ""
    assert scope.app_context(tmp_path / "does-not-exist") == ""


def test_an_unreadable_file_is_still_listed_without_a_count(tmp_path):
    # Its existence is scope signal even when its size isn't; dropping the row would undercount the app.
    root = _app(tmp_path, {"src/big.bin": "x" * (scope.MAX_FILE_BYTES + 1)})
    ctx = scope.app_context(root)
    assert "src/big.bin" in ctx and "lines" not in ctx.split("src/big.bin")[1]


def test_the_listing_rides_the_system_prompt_not_the_users_message(tmp_path):
    # Background the model judges against. Pasted in front of the request it would read as part of
    # what the user typed — and it would break the truncation contract on the user message.
    gw = StubGateway("BUILD")
    root = _app(tmp_path, {"src/App.tsx": "x\n"})
    _ask(gw, prompt="add a settings page", root=root)
    system, user = gw.seen[0][0]["messages"]
    assert "src/App.tsx" in system["content"]
    assert user["content"] == "add a settings page"


def test_classifying_without_a_root_still_works(tmp_path):
    # The listing is an improvement to the judgement, never a precondition for making one.
    gw = StubGateway("PLAN")
    assert _ask(gw, root=None) is True
    assert gw.seen[0][0]["messages"][0]["content"] == scope._SYSTEM


# --- when it runs at all -----------------------------------------------------------------------

def test_the_classifier_runs_only_where_it_can_change_the_outcome():
    applies = {"mode": Mode.AUTO, "has_built": True, "gate": False, "answer_only": False,
               "skip_planning": False}
    assert _scope_gate_applies(**applies) is True
    # Already gating, so there is nothing to ask.
    assert _scope_gate_applies(**{**applies, "gate": True}) is False
    # Answers and stops — covers questions and approvals, which never gate anyway.
    assert _scope_gate_applies(**{**applies, "answer_only": True}) is False
    # The first-build gate already has this turn; the hole opens only after it.
    assert _scope_gate_applies(**{**applies, "has_built": False}) is False
    # skip_planning opted out of the automatic gate, and this is that gate one turn later.
    assert _scope_gate_applies(**{**applies, "skip_planning": True}) is False


@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.IMPLEMENT, Mode.ASK])
def test_an_explicit_mode_is_never_second_guessed(mode: Mode):
    # Plan gates every turn already, Implement is "just build it", Ask never builds. Auto is the only
    # mode carrying no explicit instruction, which is why it's the only one that needs one inferred.
    assert _scope_gate_applies(mode=mode, has_built=True, gate=False, answer_only=False,
                               skip_planning=False) is False
