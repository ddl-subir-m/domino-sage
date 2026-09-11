"""The ladder out of a refused Conversation, on every path that can reach one (ADR-0022).

A gateway guardrail refuses what a turn CARRIES, and what a turn carries is the OpenCode session —
so the refusal outlives the turn. Removing the file does not help, removing the chip does not help,
and every later turn is refused on a value from an earlier one. Clearing Recall is the only scrub
there is, because it is the only thing that empties the session.

Chat could climb that ladder. Three paths that end in the same refusal could not:

  - The Chat -> Build handoff plans in the THREAD'S OWN session, so it is refused by the same
    poison — and it is the likeliest next click, since the offer card is on screen when the turn
    under it fails. It reported the raw transport nest, and it wrote nothing to the Thread, so
    `recall.offer` counted to one forever and the way out was never offered.
  - Build kept no `error` rows at all (`_PERSISTED_EVENTS`), so its transcript could not carry a
    refusal, let alone two identical ones. After an approved turn died it said "say try again to
    build it" — the one instruction guaranteed to fail.
  - A refusal Sage makes ITSELF, before the turn runs, streamed a sentence and kept no copy. The
    question stayed on screen with nothing under it, which is the silent turn again.

And the ladder's own last rung was never wired: `recall.terminal` shipped, was documented, and
nothing called it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator import recall
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import BUILD, PLAN
from .test_a_dead_alias_stops_the_turn_before_it_starts import _error as _build_error
from .test_a_dead_alias_stops_the_turn_before_it_starts import _orch as _build_orch
from .test_a_refused_request_is_not_a_silent_turn import BLOCKED
from .test_chat_turn import _orch
from .test_the_lock_follows_the_conversation import _bind_declared
from .test_the_lock_follows_the_conversation import _orch as _lock_orch

NEST = BLOCKED["data"]["message"]
KEY = "guardrail:Block phone numbers"
# The rendered reason, as `_guardrail_sentence` builds it. It names no file on any path.
_SENTENCE = ('the gateway refused it: "Blocked by guardrail: Block phone numbers". '
             "Guardrails read everything a turn carries, including the contents of files "
             "it opened and what earlier turns read, not only what you typed. The gateway "
             "does not say which part matched. Ask your administrator about the policy.")


def _history(orch, tid: str) -> list[dict]:
    return ThreadStore(orch.project(start_preview=False).record.path).read_history(tid)


def _build_history(orch) -> list[dict]:
    project = orch.project(start_preview=False)
    return project.app_for_turn().read_history(project.build_conversation)


class _GuardrailRefusesPlanning(FakeOpenCode):
    """The gateway refuses the planner, which is what a poisoned Thread session does to it.

    `last_gateway_error` is written by the shim's own route during the turn, which no fake reaches
    — so the fake writes it, at the moment the real one would.
    """

    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace, [Turn(text="Rates.")])
        self.project = None

    def send_prompt(self, session_id: str, text: str, model: dict | None = None,
                    agent: str | None = None, attachments: list[dict] | None = None,
                    chat: bool = False) -> None:
        super().send_prompt(session_id, text, model=model, agent=agent,
                            attachments=attachments, chat=chat)
        if agent == "sage-plan" and self.project is not None:
            self.project.last_gateway_error = {"message": NEST, "upstream_status": 502}


# ---- the handoff plans in the session that was refused -------------------------------------------


def test_the_handoff_plans_in_the_threads_own_session(tmp_path: Path):
    """The fact the other two tests rest on. If the planner opened a session of its own, a refused
    Chat Thread could still be handed over and none of this would matter."""
    orch, oc = _orch(tmp_path, [Turn(text="Rates."), Turn(text="# Plan\n\nA table.\n")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))

    orch.draft_handoff_plan(tid)

    chat_send, plan_send = oc.prompts[0], next(p for p in oc.prompts if p["agent"] == "sage-plan")
    assert plan_send["session"] == chat_send["session"]


def test_a_refused_handoff_plan_says_which_guardrail_refused_it(tmp_path: Path):
    """It reported `model call failed:` and three levels of escaped JSON — the exact sentence
    ADR-0014 exists to stop, on the one click a refused Conversation is most likely to make."""
    orch, oc = _orch(tmp_path, client=_GuardrailRefusesPlanning)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))

    with pytest.raises(ValueError) as caught:
        orch.draft_handoff_plan(tid)

    said = str(caught.value)
    assert '"Blocked by guardrail: Block phone numbers"' in said
    for noise in ("502", "400", "upstream_status", "chat/completions", "model call failed"):
        assert noise not in said


def test_a_refused_handoff_plan_names_nothing_the_conversation_carries(tmp_path: Path):
    """This path is the one that used to name the Thread's context as suspects, and it is the one
    where no withhold search runs afterwards to correct it — so a guess here stood as the last word.
    The planner reads no files itself; it replays a session whose refused value can be from any turn
    in it. Naming what happens to be attached NOW sends someone to look in the wrong file."""
    orch, oc = _orch(tmp_path, client=_GuardrailRefusesPlanning)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "file", "name": "claims.csv", "path": "claims.csv"})
    orch.add_thread_context(tid, {"kind": "data_source", "name": "Snowflake-Warehouse",
                                  "bindingKey": ["data_source", "ds-1"]})
    list(orch.chat_stream(tid, "which desk is largest?"))

    with pytest.raises(ValueError) as caught:
        orch.draft_handoff_plan(tid)

    said = str(caught.value)
    assert "claims.csv" not in said
    assert "Snowflake-Warehouse" not in said
    assert '"Blocked by guardrail: Block phone numbers"' in said


def test_a_refused_handoff_plan_is_written_on_the_thread(tmp_path: Path):
    """The route answers 502 and the click puts it in a toast, which is gone on the next render.
    A failure recorded nowhere is a click that did nothing, twice over: the person cannot read it
    back, and neither can the ladder."""
    orch, oc = _orch(tmp_path, client=_GuardrailRefusesPlanning)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))

    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)

    err = next(e for e in _history(orch, tid) if e.get("type") == "error")
    assert err["reason"] == KEY
    assert '"Blocked by guardrail: Block phone numbers"' in err["message"]


def test_the_reason_is_keyed_off_the_gateway_not_off_the_sentence(tmp_path: Path):
    """`reason_key` has to connect two refusals whose shown sentences differ — they name different
    Attachments, which is the whole reason the ladder exists (ADR-0022)."""
    orch, oc = _orch(tmp_path, client=_GuardrailRefusesPlanning)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "file", "name": "claims.csv", "path": "claims.csv"})
    list(orch.chat_stream(tid, "which desk is largest?"))
    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)

    assert [e["reason"] for e in _history(orch, tid) if e.get("type") == "error"] == [KEY]


def test_one_refused_handoff_plan_offers_the_clear(tmp_path: Path):
    """The handoff opens the ladder on ONE refusal, alone among the callers (`recall.offer_now`).

    Everywhere else a single failure may be a blip and the person has lost nothing by retrying.
    This is a deliberate click, on a card already on screen, into the Thread's own session — so the
    second click would only reach a rung this one already earned, and paying a failure for it is a
    wasted failure in front of somebody who has just had one.
    """
    orch, oc = _orch(tmp_path, client=_GuardrailRefusesPlanning)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))

    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)

    assert [e["scope"] for e in _history(orch, tid)
            if e.get("type") == recall.SUGGEST] == [recall.SUMMARY]


def test_a_second_refused_handoff_does_not_skip_a_rung(tmp_path: Path):
    """Opening the ladder earlier must not climb it faster. Nothing has been CLEARED yet, so the
    rung is still the seeded one — the escalation is bought by a clear that did not work, never by
    the count of refusals."""
    orch, oc = _orch(tmp_path, client=_GuardrailRefusesPlanning)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))
    for _ in range(2):
        with pytest.raises(ValueError):
            orch.draft_handoff_plan(tid)

    assert {e["scope"] for e in _history(orch, tid)
            if e.get("type") == recall.SUGGEST} == {recall.SUMMARY}


def test_a_refused_handoff_after_a_seeded_clear_offers_the_complete_one(tmp_path: Path):
    """The rung IS climbed by a clear that did not work. `offer_now` shares `offer`'s window for
    exactly this: which rung comes next is one rule, whatever opened the ladder."""
    orch, oc = _orch(tmp_path, client=_GuardrailRefusesPlanning)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))
    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)
    orch.clear_recall(tid, recall.SUMMARY)

    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)

    assert [e["scope"] for e in _history(orch, tid)
            if e.get("type") == recall.SUGGEST] == [recall.SUMMARY, recall.EMPTY]


def test_a_refused_handoff_after_a_complete_clear_says_why_it_stopped_offering(tmp_path: Path):
    """The ladder ends, and the silence has to be explained on this path too — it is where the
    person is clicking. Without it, the one click with no offer under it is also the one with no
    reason given."""
    orch, oc = _orch(tmp_path, client=_GuardrailRefusesPlanning)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))
    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)
    orch.clear_recall(tid, recall.EMPTY)
    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)

    rows = _history(orch, tid)
    assert [e["scope"] for e in rows if e.get("type") == recall.SUGGEST] == [recall.SUMMARY]
    last = [e["message"] for e in rows if e.get("type") == "error"][-1]
    assert "already been started over completely" in last


def test_a_single_refused_chat_turn_still_offers_nothing(tmp_path: Path):
    """NOT a general loosening. An ordinary turn that failed once may have hit a blip, and clearing
    costs the model everything it has been told."""
    orch, _ = _orch(tmp_path, [Turn(error=BLOCKED)])
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "summarize @claims.csv"))

    assert not [e for e in events if e["type"] == recall.SUGGEST]
    assert not [e for e in _history(orch, tid) if e.get("type") == recall.SUGGEST]


def test_clearing_recall_gets_the_handoff_a_fresh_session(tmp_path: Path):
    """What the offer promises, on the path that could not make it. The planner reads the session
    id back off `session.json` like every Chat turn, so dropping the file IS the clear."""
    orch, oc = _orch(tmp_path, [Turn(text="Rates."), Turn(text="# Plan\n\nA table.\n")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))
    poisoned = oc.prompts[0]["session"]

    orch.clear_recall(tid, recall.SUMMARY)
    orch.draft_handoff_plan(tid)

    assert next(p for p in oc.prompts if p["agent"] == "sage-plan")["session"] != poisoned


def test_a_planner_that_wrote_nothing_still_reads_as_an_empty_plan(tmp_path: Path):
    """The translation is for the refusal that arrives unreadable. A planner that simply produced
    no text is a different failure and keeps the sentence that names it."""
    orch, _ = _orch(tmp_path, [Turn(text="Rates."), Turn(text="")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))

    with pytest.raises(ValueError, match="didn't produce a plan"):
        orch.draft_handoff_plan(tid)


def test_a_planner_that_wrote_nothing_is_still_written_on_the_thread(tmp_path: Path):
    """The click that did nothing at all.

    `_run_sage_plan` turns a failure into a sentence only when the shim recorded a gateway error
    (`last_gateway_error`). That is one of three witnesses to a failed turn: a step that failed and
    a session error set nothing, and the planner then returns no text — the same shape as a planner
    that simply wrote nothing. `if not plan_md:` raised OUTSIDE the try/except that records a
    refusal, so the Thread got no row; the route's 502 became a toast, and a toast is gone on the
    next render. Live, a poisoned Thread's plan click was silence, every time, with nothing on the
    Conversation afterwards to say a click had happened.
    """
    orch, _ = _orch(tmp_path, [Turn(text="Rates."), Turn(text="")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))
    before = len(_history(orch, tid))

    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)

    written = _history(orch, tid)[before:]
    assert [e["type"] for e in written] == ["error"]
    assert "couldn't write a plan" in written[0]["message"]


def test_a_planner_that_wrote_nothing_offers_no_clear(tmp_path: Path):
    """The row, and NOT the ladder. Nothing was refused, so there is nothing for clearing Recall to
    reach — and the clear costs the model everything it has been told. `offer_now` opens on a single
    refusal precisely because a refusal is a fact about the Conversation; an empty plan is not one,
    and the code that raises it says a second try often lands."""
    orch, _ = _orch(tmp_path, [Turn(text="Rates."), Turn(text="")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "which desk is largest?"))

    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)

    assert not any(e.get("type") == recall.SUGGEST for e in _history(orch, tid))


# ---- a refusal Sage makes itself is still a thing that happened ----------------------------------


def test_a_sensitivity_refusal_is_written_down(tmp_path: Path, monkeypatch):
    """It streamed the sentence and kept no copy, so a reload showed the question alone — the
    silent turn, from the one direction that has nothing to do with the gateway."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", "no-such-group")
    orch, oc = _lock_orch(tmp_path)
    _bind_declared(orch)
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "what is in the claims data?"))

    said = next(e["message"] for e in events if e["type"] == "error")
    assert [e.get("type") for e in _history(orch, tid)] == ["user", "error", "done"]
    assert next(e for e in _history(orch, tid) if e["type"] == "error")["message"] == said
    assert oc.prompts == []


def test_a_sensitivity_refusal_settles_the_turn(tmp_path: Path, monkeypatch):
    """Every other exit from a Chat turn yields a `done`, and the client reads it as the turn
    settling. Two paths skipped it and asked every reader of this stream to special-case them."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", "no-such-group")
    orch, _ = _lock_orch(tmp_path)
    _bind_declared(orch)
    tid = orch.create_thread()["id"]

    events = list(orch.chat_stream(tid, "what is in the claims data?"))

    done = next(e for e in events if e["type"] == "done")
    assert done["ok"] is False
    assert done["decision"] == "refused"


def test_a_refused_turn_still_taints_nothing(tmp_path: Path, monkeypatch):
    """Writing the refusal down must not be mistaken for running it. A turn refused before it ran
    sent no transcript anywhere, so the sensitivity lock has nothing to hold (ADR-0043)."""
    monkeypatch.setenv("SAGE_SENSITIVE_MODEL_GROUP", "no-such-group")
    orch, _ = _lock_orch(tmp_path)
    _bind_declared(orch)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "what is in the claims data?"))

    assert orch.project(start_preview=False).record.session_ran_locked(tid) is False


# ---- Build keeps its refusals, and can climb out of them -----------------------------------------


def test_build_keeps_the_sentence_that_says_why_a_turn_failed(tmp_path: Path):
    """`error` was not in `_PERSISTED_EVENTS`, so a reload got back `done: gateway error` and
    nothing else — and a decision is not a reason anybody can act on."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD], break_on={2})
    oc.break_message = NEST
    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())

    err = next(e for e in _build_history(orch) if e.get("type") == "error")
    assert '"Blocked by guardrail: Block phone numbers"' in err["message"]
    assert err["reason"] == KEY


def test_builds_second_identical_refusal_offers_the_clear(tmp_path: Path):
    """The same two rungs Chat has, over Build's own transcript. Build reported gateway failures
    from the first day and keyed none of them, so a Conversation refused the same way ten times had
    ten unrelated errors and no way out offered from any of them."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD, BUILD], break_on={2, 3})
    oc.break_message = NEST
    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())

    second = list(orch.build_stream("try again"))

    assert [e["scope"] for e in second if e["type"] == recall.SUGGEST] == [recall.SUMMARY]
    assert [e["scope"] for e in _build_history(orch)
            if e.get("type") == recall.SUGGEST] == [recall.SUMMARY]


def test_builds_offer_arrives_before_the_turn_settles(tmp_path: Path):
    """A client reading the stream in order sees what failed before it is offered a way out of it
    — the order Chat already puts them in."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD, BUILD], break_on={2, 3})
    oc.break_message = NEST
    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())

    types = [e["type"] for e in orch.build_stream("try again")]

    assert types.index("error") < types.index(recall.SUGGEST) < types.index("done")


def test_one_refusal_in_build_offers_nothing(tmp_path: Path):
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD], break_on={2})
    oc.break_message = NEST

    list(orch.build_stream("build me a consumption dashboard"))
    events = list(orch.approve_stream())

    assert not [e for e in events if e["type"] == recall.SUGGEST]


def test_clearing_builds_recall_opens_a_fresh_session(tmp_path: Path):
    """Dropping the stored id IS the clear, here as in Chat. Build caches it in memory as well as
    on disk, so both have to go — leaving the cache hands the next turn the session this call
    exists to abandon."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD, BUILD], break_on={2})
    oc.break_message = NEST
    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())
    poisoned = oc.prompts[-1]["session"]

    orch.clear_build_recall(recall.SUMMARY)
    list(orch.build_stream("try again"))

    assert oc.prompts[-1]["session"] != poisoned


def test_clearing_builds_recall_keeps_the_app_and_the_transcript(tmp_path: Path):
    """What the offer promises. The app is files on disk and the transcript is `history.jsonl`;
    neither is what the gateway refused, and neither is touched."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD, BUILD], break_on={2})
    oc.break_message = NEST
    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())
    before = len(_build_history(orch))
    app = orch.project(start_preview=False).app_for_turn().path

    orch.clear_build_recall(recall.SUMMARY)

    assert (app / "src" / "App.tsx").exists()
    after = _build_history(orch)
    assert len(after) == before + 1
    # `app` and `at` are stamped by `append_history` on every row it writes.
    assert after[-1]["type"] == recall.CLEARED
    assert after[-1]["scope"] == recall.SUMMARY


def test_an_unknown_scope_is_refused(tmp_path: Path):
    orch, _ = _build_orch(tmp_path, turns=[PLAN, BUILD])
    with pytest.raises(ValueError, match="unknown scope"):
        orch.clear_build_recall("everything")


# ---- the last rung -------------------------------------------------------------------------------


def test_the_last_rung_says_where_the_value_must_be(tmp_path: Path):
    """`recall.terminal` shipped, was documented as the end of the ladder, and nothing ever called
    it. A Conversation started over COMPLETELY and refused again got the sentence a first blip gets
    and no offer under it — `recall.offer` is right to decline a third clear, and nothing said why
    the offer had stopped coming."""
    history = [
        {"type": "error", "reason": KEY},
        {"type": recall.CLEARED, "scope": recall.EMPTY},
    ]
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD], break_on={2})
    oc.break_message = NEST
    project = orch.project(start_preview=False)
    for row in history:
        project.app_for_turn().append_history(row, project.build_conversation)

    list(orch.build_stream("build me a consumption dashboard"))
    said = _build_error(list(orch.approve_stream()))

    assert "already been started over completely" in said
    assert "the message you just sent" in said


def test_the_last_rung_stops_saying_try_again(tmp_path: Path):
    """The plan IS still there, but it is no longer the thing in the way. Saying "try again" at the
    rung where trying again has already failed twice sends someone round the loop a third time."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD], break_on={2})
    oc.break_message = NEST
    project = orch.project(start_preview=False)
    for row in ({"type": "error", "reason": KEY},
                {"type": recall.CLEARED, "scope": recall.EMPTY}):
        project.app_for_turn().append_history(row, project.build_conversation)

    list(orch.build_stream("build me a consumption dashboard"))
    said = _build_error(list(orch.approve_stream()))

    assert "try again" not in said


def test_an_ordinary_refusal_still_says_the_plan_is_here(tmp_path: Path):
    """The sentence is right everywhere except the last rung, and it is the one thing that gets a
    person out of an approved turn that died — the Approve button was spent when the turn started."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD], break_on={2})
    oc.break_message = NEST

    list(orch.build_stream("build me a consumption dashboard"))
    said = _build_error(list(orch.approve_stream()))

    assert "The plan is still here" in said and "try again" in said
    assert "already been started over" not in said


def test_clearing_builds_recall_waits_for_a_running_turn(tmp_path: Path):
    """It pins the Project to a conversation and drops the cached session id, and a turn streaming
    in another one reads both. Chat's clear touches one file and needs no lock."""
    from sage.orchestrator.service import TurnBusy

    orch, _ = _build_orch(tmp_path, turns=[PLAN, BUILD])
    assert orch._turn_lock.acquire(blocking=False)
    try:
        with pytest.raises(TurnBusy):
            orch.clear_build_recall(recall.SUMMARY)
    finally:
        orch._release_turn()


# ---- the same refusal is explained once ----------------------------------------------------------
#
# The guardrail sentence is a paragraph: what was matched, that guardrails read file contents and
# not only what you typed, which file this turn read, and what to do. It earns that length once.
# On the handoff, twice in a row is the NORMAL case — the click that fails is the one made straight
# after the turn that failed — and the offer card underneath then says it a third time in its own
# words.


class _RefusesEverything(FakeOpenCode):
    """The gateway refuses the Chat turn AND the plan drafted from it. The real shape."""

    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace, [Turn(error=BLOCKED)])
        self.project = None

    def send_prompt(self, session_id: str, text: str, model: dict | None = None,
                    agent: str | None = None, attachments: list[dict] | None = None,
                    chat: bool = False) -> None:
        super().send_prompt(session_id, text, model=model, agent=agent,
                            attachments=attachments, chat=chat)
        if agent == "sage-plan" and self.project is not None:
            self.project.last_gateway_error = {"message": NEST, "upstream_status": 502}


def _refused_thread(tmp_path: Path, context: dict | None = None):
    orch, oc = _orch(tmp_path, client=_RefusesEverything)
    oc.project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, context or {"kind": "file", "name": "claims.csv",
                                             "path": "claims.csv"})
    list(orch.chat_stream(tid, "summarize @claims.csv"))
    with pytest.raises(ValueError):
        orch.draft_handoff_plan(tid)
    return orch, tid


def test_the_guardrail_paragraph_is_printed_once(tmp_path: Path):
    """The measured transcript before this: the same ~300 characters twice, back to back."""
    orch, tid = _refused_thread(tmp_path)

    said = [e["message"] for e in _history(orch, tid) if e.get("type") == "error"]
    assert len(said) == 2
    assert sum("Guardrails read everything a turn carries" in m for m in said) == 1


def test_the_second_row_adds_the_fact_the_first_could_not(tmp_path: Path):
    """A DIFFERENT request was refused the same way, so what was matched is in the Conversation
    rather than in the message — which is the evidence for the card below it."""
    orch, tid = _refused_thread(tmp_path)

    second = [e["message"] for e in _history(orch, tid) if e.get("type") == "error"][-1]
    assert "the same refusal, on a different request" in second
    assert "in this conversation, not in what you typed" in second
    assert "claims.csv" not in second


def test_the_short_row_still_counts_on_the_ladder(tmp_path: Path):
    """Shortening the prose must not cost the rung. The key is read off the shim's raw record, not
    off the sentence, which is exactly why it survives the sentence changing."""
    orch, tid = _refused_thread(tmp_path)

    rows = _history(orch, tid)
    assert [e["reason"] for e in rows if e.get("type") == "error"] == [KEY, KEY]
    assert [e["scope"] for e in rows if e.get("type") == recall.SUGGEST] == [recall.SUMMARY]


def test_a_second_refusal_on_a_different_file_is_still_shortened(tmp_path: Path):
    """The pair ADR-0022's ladder exists to connect: the same guardrail, a different Attachment.

    This used to be the one case where the full paragraph came back, because the paragraph named the
    Attachment and the two names were the only thing that differed. Nothing names a file any more,
    so the two paragraphs would be the same string twice — and repeating it says nothing the first
    copy did not. The short row carries the part that IS new: it is the same refusal, and the value
    is somewhere in the conversation rather than in what was just typed.
    """
    orch, tid = _refused_thread(tmp_path, context={"kind": "file", "name": "forecasts.json",
                                                   "path": "forecasts.json"})
    second = [e["message"] for e in _history(orch, tid) if e.get("type") == "error"][-1]
    assert "forecasts.json" not in second
    assert "the same refusal, on a different request" in second
    assert "in this conversation, not in what you typed" in second


def test_a_refusal_after_an_answer_is_said_in_full_again(tmp_path: Path):
    """"Directly above" is the whole rule. Anything said or answered since means the person has
    read other things, scrolled, possibly come back tomorrow."""
    orch, _ = _orch(tmp_path, client=_RefusesEverything)
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    store = ThreadStore(project.record.path)
    store.append_history(tid, {"type": "error", "reason": KEY,
                               "message": "Sage couldn't finish — " + _SENTENCE})
    store.append_history(tid, {"type": "user", "text": "what about last quarter?"})
    store.append_history(tid, {"type": "agent", "kind": "text", "text": "Up 4%."})

    orch._record_plan_refusal(store, tid, project, _SENTENCE)

    errors = [e["message"] for e in _history(orch, tid) if e.get("type") == "error"]
    assert "Guardrails read everything a turn carries" in errors[-1]


def test_build_says_it_once_too(tmp_path: Path):
    """Same noise, same rule. Build's is the rarer case — it takes a person retrying — but the
    ladder is about to offer the way out either way."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD, BUILD], break_on={2, 3})
    oc.break_message = NEST
    list(orch.build_stream("build me a consumption dashboard"))
    list(orch.approve_stream())

    list(orch.build_stream("try again"))

    said = [e["message"] for e in _build_history(orch) if e.get("type") == "error"]
    assert len(said) == 2
    assert sum("Guardrails read everything a turn carries" in m for m in said) == 1
    assert "was refused the same way again" in said[-1]
