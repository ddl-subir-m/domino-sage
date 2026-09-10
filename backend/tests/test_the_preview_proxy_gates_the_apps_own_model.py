"""The preview proxy's half of the sensitivity gate (ADR-0043).

The only place the app's OWN model call can be stopped: once published it calls the gateway from the
viewer's browser with no Sage hop in the path. So this is a real enforcement point with no test
behind it, and it reads the same `_sensitivity_for_turn` the turn gate and the lock state read —
which makes it the one that breaks quietly when that answer changes shape.
"""
from __future__ import annotations

import sage.orchestrator.app as app_module
from sage.resources.provider import ApprovedModels

APPROVED = ApprovedModels(names=frozenset({"opus", "haiku"}), order=("haiku", "opus"),
                          group_name="sensitive-approved", members=2)


class _Stub:
    def __init__(self, answer):
        self._project = object()
        self._answer = answer
        self.asked_about = "unasked"

    def _sensitivity_for_turn(self, project, conversation):
        self.asked_about = conversation
        return self._answer


def _refusal(monkeypatch, answer, model):
    monkeypatch.setattr(app_module, "orchestrator", _Stub(answer))
    return app_module._preview_approve_model(model)


def test_the_preview_names_no_conversation(monkeypatch):
    """`None` and not `""` (ADR-0043). Half the lock is a conversation's own — once one has run a
    turn under the lock its transcript keeps the rows — but the previewed app's own model call
    carries the app's CURRENT Bindings and no transcript, so no conversation is in the question.
    The empty string is a different answer: it names the unscoped Build turn, which has one.
    """
    stub = _Stub((APPROVED, ""))
    monkeypatch.setattr(app_module, "orchestrator", stub)

    app_module._preview_approve_model("opus")

    assert stub.asked_about is None


def test_an_approved_model_is_allowed_through(monkeypatch):
    assert _refusal(monkeypatch, (APPROVED, ""), "opus") is None


def test_an_unapproved_model_is_refused_by_name(monkeypatch):
    """Its own sentence and not the turn's: the approved set is fine here and the app is simply
    pointed at the wrong model, which the creator fixes themselves in the picker."""
    message = _refusal(monkeypatch, (APPROVED, ""), "gpt-5.4")
    assert "gpt-5.4" in message
    assert "haiku" in message and "opus" in message


def test_the_turn_refusal_is_passed_straight_through(monkeypatch):
    """An unusable approved set refuses everything, including this."""
    assert _refusal(monkeypatch, (None, "nothing is approved"), "opus") == "nothing is approved"


def test_no_lock_allows_everything(monkeypatch):
    """The ordinary answer for a deployment that never opted in, and for a Project with no declared
    Dataset bound. Neither reads anything to say so."""
    assert _refusal(monkeypatch, (None, ""), "gpt-5.4") is None


def test_a_gate_that_raises_leaves_the_preview_up(monkeypatch):
    """A preview call is not a turn, and Sage failing to read its own gate must not take the preview
    down. The publish guard still refuses, so nothing ships on this path."""
    class Boom(_Stub):
        # The same arity the caller uses. With the old one-argument signature this test went on
        # passing after the caller grew a parameter — but it was proving that the `except` catches a
        # TypeError from an arity mismatch, and the raise below was never reached.
        def _sensitivity_for_turn(self, project, conversation):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(app_module, "orchestrator", Boom((None, "")))
    assert app_module._preview_approve_model("gpt-5.4") is None
