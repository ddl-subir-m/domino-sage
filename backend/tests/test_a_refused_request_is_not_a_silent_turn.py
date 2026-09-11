"""A refusal of the REQUEST reaches the Thread, the way a refusal of a step already did.

Live (2026-09-10): a Chat turn attached two CSVs and asked for a summary of one of them. A gateway
guardrail refused it over the data in the file. The Thread showed the question and then nothing at
all — no answer, no status line, no reason — and the turn closed `ok: True`. Nothing anywhere on
screen said a policy had refused it, so the only way to learn that was to go and read the gateway.

`_chat_error_text` had been written for exactly this sentence and never got the chance to say it,
because Chat only ever learned that a turn failed from one frame:
`session.next.step.failed`, a STEP saying no. A provider that refuses the request rather than the
work does not fail a step — it ends the session (`session.error`, carrying ContentFilterError or
APIError) and stamps the failure on the assistant message itself. Chat's event mapper dropped that
frame for not being under the `session.next.` prefix, and its transcript read walked the message's
parts and never looked at the message. Between the two there was no path from a refused request to
a person, which is why the turn was silent rather than wrong.

Three halves, as it turns out. The frame is mapped (test_driver covers the mapper); the transcript
read reports the message's own error, which does not depend on a stream being up; and Chat now reads
`project.last_gateway_error` — Sage's OWN record of the 400, written by the shim before OpenCode has
decided what to call it, and the one witness that needs no cooperation from anybody.

Build had the third of those all along and neither of the first two, so it reported this refusal —
raw, as `model call failed:` followed by the whole nest. It says the Chat sentence now. One refusal,
one wording, whichever half of the Workbench met it.
"""
from __future__ import annotations

from pathlib import Path

from .fake_opencode import Turn
from .test_a_dead_alias_stops_the_turn_before_it_starts import BUILD, PLAN
from .test_a_dead_alias_stops_the_turn_before_it_starts import _done as _build_done
from .test_a_dead_alias_stops_the_turn_before_it_starts import _error as _build_error
from .test_a_dead_alias_stops_the_turn_before_it_starts import _orch as _build_orch
from .test_chat_turn import _orch

# The live nest, as it reaches the message: the gateway answers 400, the shim wraps that in a 502
# whose message quotes the body, and OpenCode records the shim's sentence.
BLOCKED = {
    "name": "APIError",
    "data": {"message": (
        'Provider request failed with HTTP 502: {"error":{"message":"gateway returned 400 for '
        'https://apps.cloud-dogfood.domino.tech/apps/llm_gateway/v1/chat/completions: '
        '{\\"detail\\":{\\"error\\":{\\"message\\":\\"Blocked by guardrail: Block phone numbers\\",'
        '\\"type\\":\\"guardrail_blocked\\"}}}","upstream_status":400}}'
    )},
}


def test_a_refused_request_says_which_guardrail_refused_it(tmp_path: Path):
    """The turn the report was filed about: no step failed, no text arrived, nothing was said."""
    orch, _ = _orch(tmp_path, [Turn(error=BLOCKED)])
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "summarize the data in @card_panel_transactions_RAW.csv"))

    err = next(e for e in out if e["type"] == "error")
    assert '"Blocked by guardrail: Block phone numbers"' in err["message"]
    # The transport wrapper is not the gateway's sentence and is not shown (ADR-0014).
    assert "502" not in err["message"]
    assert next(e for e in out if e["type"] == "done")["ok"] is False
    # And the Thread keeps it, so a reload still says why.
    assert any(e.get("type") == "error" for e in orch.get_thread(tid)["history"])


def test_narration_written_on_the_way_to_a_refusal_is_not_an_answer(tmp_path: Path):
    """A model says what it is about to do before it does it. That text is in the same message as
    the failure, so counting it as an answer would close a refused turn `ok: True` — the shape this
    file exists to stop, one step further along."""
    orch, _ = _orch(tmp_path, [Turn(text="Let me read that file.", error=BLOCKED)])
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "summarize it"))

    # Both: the half-answer is still worth reading, with the reason under it.
    assert [e["text"] for e in out if e.get("kind") == "text"] == ["Let me read that file."]
    assert "Blocked by guardrail" in next(e for e in out if e["type"] == "error")["message"]
    assert next(e for e in out if e["type"] == "done")["ok"] is False


def test_the_shim_saw_the_refusal_even_when_opencode_reports_nothing(tmp_path: Path):
    """The witness that needs nobody's cooperation. Sage's own /v1/chat/completions handler reads
    the gateway's 400 and writes it down, whatever OpenCode then decides to call it. Build has read
    that field after every turn since it was added; Chat never did."""
    orch, oc = _orch(tmp_path, [Turn()])
    project = orch.project(start_preview=False)
    tid = orch.create_thread()["id"]
    running = oc.is_running

    def refuse_mid_turn(session_id):
        # What the shim does while the turn runs: the gateway says 400, this gets written down.
        project.last_gateway_error = {"message": BLOCKED["data"]["message"]}
        return running(session_id)

    oc.is_running = refuse_mid_turn
    out = list(orch.chat_stream(tid, "summarize it"))

    assert "Blocked by guardrail" in next(e for e in out if e["type"] == "error")["message"]
    assert next(e for e in out if e["type"] == "done")["ok"] is False


def test_a_turn_that_was_refused_does_not_refuse_the_next_one(tmp_path: Path):
    """The refused message stays in the session, and `client.messages` returns it again on the next
    turn's first poll. Without a key of its own it would be read as this turn's failure — and it has
    no parts to be keyed by, because it failed before it wrote any."""
    orch, _ = _orch(tmp_path, [Turn(error=BLOCKED), Turn(text="Here is the summary.")])
    tid = orch.create_thread()["id"]
    list(orch.chat_stream(tid, "summarize it"))
    out = list(orch.chat_stream(tid, "try again"))

    assert not [e for e in out if e["type"] == "error"]
    assert [e["text"] for e in out if e.get("kind") == "text"] == ["Here is the summary."]
    assert next(e for e in out if e["type"] == "done")["ok"] is True


def test_build_says_the_same_sentence_about_the_same_refusal(tmp_path: Path):
    """Build always REPORTED a guardrail block — it reads the shim's record after every turn — but
    it reported it raw, as `model call failed:` followed by three levels of escaped JSON. One
    refusal should not read as two different products depending on which half of the Workbench
    met it."""
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD], break_on={2})
    oc.break_message = BLOCKED["data"]["message"]

    list(orch.build_stream("build me a consumption dashboard"))
    events = list(orch.approve_stream())

    said = _build_error(events)
    assert '"Blocked by guardrail: Block phone numbers"' in said
    assert "model call failed" not in said
    for noise in ("502", "400", "detail", "upstream_status", "chat/completions"):
        assert noise not in said
    # And the way out of an approved turn that died is still offered — this branch keeps the plan.
    assert "still here" in said and "try again" in said
    assert _build_done(events)["decision"] == "gateway error"


def test_every_other_gateway_failure_still_reads_exactly_as_it_did(tmp_path: Path):
    """The translation is for the one refusal that arrives unreadable. A missing model is already a
    sentence, and rewording it would only lose the model's name."""
    orch, _ = _build_orch(tmp_path, turns=[PLAN, BUILD], break_on={2})

    list(orch.build_stream("build me a consumption dashboard"))
    said = _build_error(list(orch.approve_stream()))

    assert "model call failed: gateway returned 404: Model 'GLM-5.2' not found" in said



# ---- Build's other two witnesses (#247) -----------------------------------------------------------
#
# Build reads `project.last_gateway_error` and, until now, nothing else. That witness is the shim's
# own, so it sees a refusal only when the refusal came back through the shim. A failure OpenCode
# classifies for itself never touches it, and `_build_stream` opens an `_EventTap` purely as a wake
# signal and never drains it — so the transcript was the only path left, and Build was not reading
# the message's own `error` off it. The turn closed `ok: True` with no `error` row, which is what
# #247 was filed about: 148 rows, 11 user turns, every `done` ok, and a guardrail block on screen.


def _guardrail_rows(events: list[dict]) -> list[dict]:
    return [e for e in events
            if e["type"] == "error" and "Blocked by guardrail" in str(e.get("message", ""))]


def test_a_build_turn_refused_by_the_gateway_leaves_the_reason_behind(tmp_path: Path):
    """The report's own shape: refused on screen, and the transcript said the build went fine."""
    orch, _ = _build_orch(tmp_path, turns=[Turn(error=BLOCKED)])
    out = list(orch.build_stream("add a chart"))

    assert _guardrail_rows(out), "a refused build turn left no error row"
    # The transport wrapper is not the gateway's sentence (ADR-0014), the same as Chat.
    assert "502" not in _build_error(out)
    assert _build_done(out)["ok"] is False


def test_a_step_that_fails_and_then_recovers_is_not_a_failed_build(tmp_path: Path):
    """The guard #247 asks for by name, and the reason it says not to fix this blind.

    The turn in that report RECOVERED — the person pressed continue and the build carried on — so a
    fix that fired on the first failed step would have turned a live turn into a dead end, kept a
    plan nobody could build from, and set `_turn_gave_up` on a turn that gave up nothing.

    What recovery looks like on the transcript is a later assistant message with no error on it, so
    the failure is re-read on every poll and the last message wins. Asserted rather than reasoned
    about, because it is the half of this change that can silently stop being true.
    """
    orch, oc = _build_orch(tmp_path, turns=[Turn(error=BLOCKED)])
    running = oc.is_running

    def retry_lands(session_id):
        # What OpenCode does when it retries a step for itself: the refused message stays where it
        # is, and a later one arrives above it.
        msgs = oc._by_session.get(session_id) or []
        if msgs and msgs[-1].get("error"):
            msgs.append({"id": "m-retry", "type": "assistant", "content": []})
        return running(session_id)

    oc.is_running = retry_lands
    out = list(orch.build_stream("add a chart"))

    assert not _guardrail_rows(out), "a recovered step was reported as a refusal"
    assert orch._turn_gave_up is False, "a turn that recovered was marked as having given up"


def test_a_refused_build_turn_does_not_refuse_the_next_one(tmp_path: Path):
    """The refused message stays in the session and comes back on the next turn's first poll. It has
    no parts to be keyed by — it failed before it wrote any — so `_message_error_key` is what keeps
    the previous turn's refusal from being read as this one's."""
    orch, _ = _build_orch(tmp_path, turns=[Turn(error=BLOCKED), PLAN, BUILD])
    list(orch.build_stream("add a chart"))
    out = list(orch.build_stream("try again"))

    assert not _guardrail_rows(out), "the previous turn's refusal was re-reported as this turn's"
