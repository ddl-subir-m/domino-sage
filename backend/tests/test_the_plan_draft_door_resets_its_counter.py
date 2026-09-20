"""The plan-draft door's `model_calls` reading is this plan's, not the conversation's (#473).

WHY THIS EXISTS. `_run_sage_plan` ends an empty plan with one log line:

    log.warning("sage-plan produced no text (session=%s, model_calls=%d)", sid, project.model_calls)

and that line exists to separate the two outcomes that reach it with the same shape — "no inference
reached the shim", which is 0, from "the model answered with nothing", which is not. `model_calls`
has exactly one increment (the `/v1/chat/completions` stream wrapper in `app.py`) and, after #471,
three resets: `chat_stream` at its grant, `_build_stream` per send, and now this door.

Before the third one the door passed through neither of the others. `draft_handoff_plan` takes the
turn lock through `_acquire_for_door`, never `_acquire_turn`, so the number carried: the count this
line logged was this plan's inferences ON TOP of whatever the last turn in the process left. 0 was
therefore unreachable once any turn had run, and the line could never report half of what it is for.

The second caller was worse. `_repair_plan_heading` runs `_run_sage_plan` a SECOND time, after the
draft has already run, so its reading carried the draft's own inferences too.

WHERE THE RESET IS, AND WHERE IT MUST NOT BE. At the top of `_run_sage_plan`, beside the
`last_gateway_error` clear that was already there — where the number is READ, by the thing that
reads it. Not hoisted into `_acquire_for_door`: that lock has ten call sites, most of which run no
inference at all, and a reset there would zero the counter on acts that never touch the model, so an
`/api/diag` read taken after one would report 0 for a turn that really ran inferences.
`test_a_door_that_runs_no_inference_leaves_the_count_alone` is that rejected fix's plant.

WHAT THESE TESTS OBSERVE. The log line itself, label and number together — `model_calls=N` as the
line renders it — because the line is the whole feature. Asserting on the attribute afterwards would
pass for a reset placed anywhere at all, including the one place this ticket rejects.

WHAT THE FAKE STANDS IN FOR. `CountingOpenCode` is imported rather than rebuilt; it plants the
increment at the moment the real `/v1` route would, which no fake-OpenCode test reaches. These tests
exercise the RESET and take the increment on trust, the same limit its own file records.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator

from .fake_opencode import Turn

# `_no_real_waiting` is imported, not redefined: it is autouse in the module it comes from, and an
# autouse fixture only applies where its name is bound, so the Chat poll loop in these tests waits
# on real seconds without it.
from .test_model_calls_answers_this_turn_on_chat import (
    CountingOpenCode,
    _chat_orch,
    _no_real_waiting,  # noqa: F401
)

_READING = re.compile(r"sage-plan produced no text \(session=.*, model_calls=(\d+)\)")

# A plan with no `# ` heading, which is what sends `_draft_handoff_plan` on to
# `_repair_plan_heading` — the second `_run_sage_plan` call, whose reading used to carry the first
# call's inferences.
HEADLESS_PLAN = "A dashboard of trades.\n\n## Plan\n\n- Show the rows.\n"


def _readings(caplog: pytest.LogCaptureFixture) -> list[int]:
    """Every `model_calls=` the door logged, in order, read off the rendered line.

    Off the MESSAGE rather than off `record.args`, so the number is bound to the label the reader
    actually sees: a line that logged the right integer under the wrong `%d` would still be wrong.
    """
    return [int(m.group(1))
            for rec in caplog.records
            for m in [_READING.search(rec.getMessage())] if m]


def _door(tmp_path: Path, *, chat_turns: int, per_send: list[int],
          plan_texts: tuple[str, ...] = ("",)) -> tuple[Orchestrator, str, CountingOpenCode]:
    """A Chat orchestrator, `chat_turns` Chat turns run, and the handoff door ready to click.

    `per_send` is read straight through to the fake, so a test says "this send ran the model N
    times" as exactly that, for the Chat turns and the plan sends alike. `plan_texts` is what each
    of the door's own sends writes back, in order: one entry for a draft that comes back empty, two
    when the draft needs its heading repaired and the repair comes back empty as well.
    """
    ws = tmp_path / "mnt" / "code"
    turns = [Turn(text="An answer.")] * chat_turns + [Turn(text=t) for t in plan_texts]
    oc = CountingOpenCode(ws, turns, per_send=per_send)
    orch = _chat_orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    for i in range(chat_turns):
        list(orch.chat_stream(tid, f"summarise the event table ({i})"))
    return orch, tid, oc


# ---- the defect ---------------------------------------------------------------------------------


def test_the_door_logs_this_plans_inferences_not_the_conversations(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """Two Chat turns of two inferences each, then a plan that ran the model once and wrote nothing.

    Without the reset the line reads 3 — this plan's one inference on top of the last Chat turn's
    two — and calls it "sage-plan produced no text (model_calls=3)", which is the reading that says
    the model answered. The plan ran the model once. One is the number.
    """
    orch, tid, _ = _door(tmp_path, chat_turns=2, per_send=[2, 2, 1])

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        with pytest.raises(ValueError):
            orch.draft_handoff_plan(tid)

    assert _readings(caplog) == [1], caplog.text


def test_zero_is_reachable_after_a_turn_has_run(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """The half of its job the line could never do.

    0 means "no inference reached the shim" — a bypassed shim, a dead OpenCode, a turn that never
    dialled out — and it is the one reading that makes this line worth logging. With the count
    carrying, a Chat turn earlier in the same process put it permanently out of reach: the same
    broken door read 2 here and named it as the model's own silence.
    """
    orch, tid, oc = _door(tmp_path, chat_turns=1, per_send=[2, 0])

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        with pytest.raises(ValueError):
            orch.draft_handoff_plan(tid)

    assert oc.seen_on_entry[-1] == 0, "the reset did not run before the plan's own send"
    assert _readings(caplog) == [0], caplog.text


def test_a_fresh_process_still_reads_zero(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """The control, and it is meant to stay green when the reset is removed.

    A door clicked before any turn has run reads 0 whether or not anything resets the counter,
    because the attribute starts at 0. If this test goes red alongside the two above when the reset
    comes out, it is not testing what it says it tests — it is testing that the line logs at all.
    """
    orch, tid, _ = _door(tmp_path, chat_turns=0, per_send=[0])

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        with pytest.raises(ValueError):
            orch.draft_handoff_plan(tid)

    assert _readings(caplog) == [0], caplog.text


def test_the_heading_repair_logs_its_own_count_not_the_drafts(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """The second caller, which was worse: it runs AFTER the draft, in the same door click.

    The draft comes back with a plan that has no `# ` heading, so `_repair_plan_heading` sends a
    second prompt through `_run_sage_plan`; that one writes nothing. Without a per-call reset the
    repair's line reads the Chat turn's two inferences, the draft's three and its own one, and
    reports 6 for a send that made one call. Resetting once at the door would still report 4.
    """
    orch, tid, _ = _door(tmp_path, chat_turns=1, per_send=[2, 3, 1],
                         plan_texts=(HEADLESS_PLAN, ""))

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        with pytest.raises(ValueError):
            orch.draft_handoff_plan(tid)

    assert _readings(caplog) == [1], caplog.text


def test_a_second_click_does_not_inherit_the_first_ones_count(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """The carrier that is the door itself, which no reset on a streaming lane could ever reach.

    Two clicks of Write plan on the same Thread, with no turn in between: the first runs the model
    twice and comes back empty, the second reaches the shim not at all. The second reading is 0.
    A fix put in `chat_stream` or `_build_stream` would leave this one reading 2 — which is why the
    reset belongs to `_run_sage_plan`, the call, rather than to any turn around it.
    """
    orch, tid, _ = _door(tmp_path, chat_turns=0, per_send=[2, 0], plan_texts=("", ""))

    with caplog.at_level(logging.WARNING, logger="sage.orchestrator"):
        for _ in range(2):
            with pytest.raises(ValueError):
                orch.draft_handoff_plan(tid)

    assert _readings(caplog) == [2, 0], caplog.text


# ---- the fix that must not be made --------------------------------------------------------------


def test_a_door_that_runs_no_inference_leaves_the_count_alone(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """The plant for hoisting the reset into `_acquire_for_door`.

    Nine of that lock's ten call sites are acts like this one — no prompt, no session, no model.
    Resetting there would zero the counter on them, and `/api/diag` reads `model_calls` whenever it
    is asked, not only during a turn: a read taken after this click would report 0 inferences for a
    Chat turn that really ran two, which is a worse lie than the carry this ticket fixes.
    """
    orch, tid, _ = _door(tmp_path, chat_turns=1, per_send=[2])

    orch.cross_chat_context(tid)

    assert orch.project(start_preview=False).model_calls == 2
