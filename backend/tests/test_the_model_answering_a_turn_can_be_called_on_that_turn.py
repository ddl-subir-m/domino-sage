"""#424: Sage said it had no language model on a turn a language model was answering.

A person picked Sonnet in the Chat picker and asked for something needing a delegated pass. The
Thread carried a `data_source` chip and no `llm_alias` chip, so `turn.aliases` was empty, and
`delegated.perform` refused with *"No language model is in this conversation, so Sage has none to
call."* — while the turn itself was being answered by `sonnet`, recorded on its own `done` row as
`resolved: {model: "sonnet", reason: "chat-default"}`.

ADR-0057's bind-is-consent rule is right about the case it was written for: delegating to a
DIFFERENT model from the one the person chose is a new disclosure to a new party. It stops applying
to the model already receiving the whole conversation, which is the picker's choice by definition.
Binding it records a decision the person took rather than taking one for them.

WHY THESE TESTS ARM THE CHAT PIN BY HAND. `FakeOpenCode` emits tool PARTS into the event stream; it
never calls back into `/mcp/delegated`. So every delegated test in
`test_a_chat_turn_can_call_a_model_the_person_bound.py` runs `orch.delegated_model_call(...)` after
`chat_stream` has drained, with the Chat pin already down — a state production never reaches, since
a real delegated call arrives mid-turn while the pin is up. Measured on this tree: post-drain,
`project.control.snapshot().chat_thread_id` is `None` and `llm_router.resolve` falls through to
`_resolve_build` and answers `p`. Arming the pin is what makes the fixture model the turn the
person is actually sitting in front of, and `test_the_pin_down_is_not_a_chat_turn` below pins the
difference rather than leaving it implicit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sage.resources.provider import ApprovedModels

from .test_a_chat_turn_can_call_a_model_the_person_bound import (
    SONNET_LABEL,
    Aliases,
    AnswerGateway,
    _ask,
    _chip,
    _orch,
    _token,
)

SONNET, OPUS = "sonnet", "opus"
NONE_REFUSAL = "No language model is in this conversation"


def _mid_turn(tmp: Path, picked: str | None = SONNET, resources=None, gateway=None):
    """A Conversation with one turn run and the Chat pin back up, as it is during a real turn."""
    orch, oc = _orch(tmp, gateway=gateway or AnswerGateway(), resources=resources or Aliases())
    tid = orch.create_thread()["id"]
    project = orch._chat_project()
    project.control.pick_chat(picked)
    list(orch.chat_stream(tid, "classify these support cases"))
    project.control.pick_chat(picked)
    project.control.arm_chat(tid)
    return orch, oc, tid, project


def test_the_model_answering_the_turn_can_be_called_without_anybody_binding_a_chip(tmp_path: Path):
    """The reported symptom, and the whole of #424.

    No `llm_alias` chip anywhere in this Thread. Before this change the sentence below was what a
    person got back while Sonnet was visibly answering them.
    """
    orch, oc, _tid, _ = _mid_turn(tmp_path)
    said = _ask(orch, _token(oc), alias=SONNET, prompt="Classify this")
    assert NONE_REFUSAL not in said, said
    assert "REFUND REQUEST" in said, said


def test_the_picker_label_is_the_name_the_person_reads(tmp_path: Path):
    """`_resolve` matches the call name OR the label, and the person only ever saw the label.

    A person who types what the picker shows them must not be told it is not in the conversation.
    """
    orch, oc, _tid, _ = _mid_turn(tmp_path)
    said = _ask(orch, _token(oc), alias=SONNET_LABEL, prompt="Classify this")
    assert NONE_REFUSAL not in said, said
    assert "REFUND REQUEST" in said, said


def test_a_chip_naming_the_same_model_keeps_the_persons_own_wording(tmp_path: Path):
    """One entry, not two, and the person's label wins.

    `add` de-duplicates on the call name and the first entry wins, which is why the routed model is
    appended LAST. Prepending it would push the label the person chose off the sentence.
    """
    orch, _oc = _orch(tmp_path, gateway=AnswerGateway(), resources=Aliases())
    tid = orch.create_thread()["id"]
    _chip(orch, tid, alias_id="f-sonnet", label="my classifier")
    project = orch._chat_project()
    project.control.pick_chat(SONNET)
    list(orch.chat_stream(tid, "classify these"))
    project.control.pick_chat(SONNET)
    project.control.arm_chat(tid)

    aliases, labels, _ = orch._delegated_aliases(project, tid)
    assert [n for n, _ in aliases].count(SONNET) == 1, aliases
    assert labels[SONNET] == "my classifier", labels


def test_under_a_lock_the_bound_model_is_the_one_substituted_not_the_one_picked(tmp_path: Path):
    """The premise correction #424 needs, and the reason this reads the model POST-lock.

    The ticket said to compute the routed model as `_resolve_chat` does — `state.chat_model or
    catalog.ask`. That is the PRE-lock answer. `llm_router.resolve` wraps it in `_lock_sensitivity`,
    which under a sensitivity lock does not raise: it SUBSTITUTES, via `_nearest_approved`. The shim
    then sends `resolve(...).model`, so on a locked turn the picked model is not the one answering.

    Binding the picked one would name a model in "This conversation has: …" that this conversation
    cannot call — the same false sentence #424 exists to end, moved one line over.
    """
    orch, _oc, tid, project = _mid_turn(tmp_path, picked=OPUS)
    project.control.arm_sensitivity(frozenset({SONNET}), (SONNET,))

    aliases, _, _ = orch._delegated_aliases(project, tid)
    names = [n for n, _ in aliases]
    assert SONNET in names, f"the substituted model is the one answering: {aliases}"
    assert OPUS not in names, f"the picked model is not answering this turn: {aliases}"


def test_a_set_narrowed_since_the_turn_started_still_refuses_the_model_by_name(tmp_path: Path):
    """The negative control: this rule is NOT applied unconditionally.

    Two sets are in play and they can diverge. `state.approved_models` is what was ARMED when the
    turn started, and is what the router and the shim are using right now. `turn.approved` is
    re-read from the records per call, because a Dataset can be pinned mid-turn. So a Dataset pinned
    since the turn began narrows the second set under a turn still running on the first — and the
    call is refused at `delegated.perform` by name, while the turn keeps running on the model it
    started on. That is correct, and it is the condition a plant has to be able to redden.
    """
    orch, oc, _tid, project = _mid_turn(tmp_path)
    project.control.arm_sensitivity(frozenset({SONNET}), (SONNET,))
    orch._sensitivity_for_turn = lambda *_a, **_k: (ApprovedModels(frozenset({OPUS}), (OPUS,)), "")

    said = _ask(orch, _token(oc), alias=SONNET, prompt="Classify this")
    assert "isn't approved for the data in this conversation" in said, said
    assert "REFUND REQUEST" not in said, said


def test_the_pin_down_is_not_a_chat_turn_and_binds_nothing(tmp_path: Path):
    """No pin means no turn, and a Build model must not arrive wearing a Chat turn's name.

    Without the pin `snapshot().chat_thread_id` is None, `resolve` goes down `_resolve_build`, and
    the answer is a Build slot that nothing in this Conversation is running on. Skipping is the
    honest outcome: after the turn there is no model answering, so there is none to bind.
    """
    orch, _oc = _orch(tmp_path, gateway=AnswerGateway(), resources=Aliases())
    tid = orch.create_thread()["id"]
    project = orch._chat_project()
    project.control.pick_chat(SONNET)
    list(orch.chat_stream(tid, "classify these"))

    assert project.control.snapshot().chat_thread_id is None, "fixture no longer models post-turn"
    aliases, _, _ = orch._delegated_aliases(project, tid)
    assert aliases == (), f"nothing is answering this turn: {aliases}"


def test_a_listing_that_will_not_answer_costs_the_label_and_not_the_model(tmp_path: Path):
    """The one read on this path that may fail open, and why that is not a hole.

    Nothing about whether a call is allowed depends on the label: `turn.approved` holds gateway
    alias names and `_resolve` matches the call name too. So a gateway that will not list must cost
    the picker's wording, not the model the person is already talking to. A name with no label known
    travels as itself, which is what `delegated.perform` already does for the same reason.
    """
    resources = Aliases()
    orch, _oc, tid, project = _mid_turn(tmp_path, resources=resources)
    orch._alias_listing_at = None
    resources.listing_fails = True

    aliases, labels, _ = orch._delegated_aliases(project, tid)
    assert (SONNET, SONNET) in aliases, aliases
    assert labels[SONNET] == SONNET, labels


@pytest.mark.parametrize("boom", [ValueError("empty approved set"), RuntimeError("catalog gone")])
def test_a_router_that_cannot_answer_refuses_rather_than_leaving_the_generator(tmp_path, boom):
    """`_nearest_approved` raises on an empty approved set on purpose.

    That read happens here, before `_sensitivity_for_turn` — so a raise would walk out of the
    generator instead of refusing. The model drops out of the set the way a chip that will not
    resolve does, and the refusal names what did resolve.
    """
    from sage.router import llm_router

    orch, _oc, tid, project = _mid_turn(tmp_path)
    _chip(orch, tid, alias_id="f-opus", label="Claude Opus 4.6")

    def _raise(*_a, **_k):
        raise boom

    original, llm_router.resolve = llm_router.resolve, _raise
    try:
        aliases, _, _ = orch._delegated_aliases(project, tid)
    finally:
        llm_router.resolve = original
    assert [n for n, _ in aliases] == [OPUS], f"the chip survives, the routed model does not: {aliases}"
