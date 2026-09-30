"""Build's indicator line follows the model's thought as it streams, the way Chat's does.

OpenCode 1.18.4 publishes a reasoning delta as an event and nothing else: `updatePartDelta` does not
write the stored part, so the transcript Build polls holds no reasoning text until the part closes.
A Build loop that reads thoughts only off the transcript therefore changes its line once per closed
thought, while Chat, reading the stream, changes it as the thought is written. And each delta it
ignored still cost a transcript read, because a reasoning delta counted as worth one.
"""
from __future__ import annotations

import time

from sage.driver.agent_driver import AgentEvent
from sage.orchestrator.service import _EventTap

from .fake_opencode import Turn
from .test_a_finished_step_does_not_wait_for_the_next_poll import _Client, _orch, _Streaming


def _thought(text: str, part: str = "r1") -> AgentEvent:
    return AgentEvent(kind="reasoning", payload={"delta": text, "part": part, "final": False})


def test_a_reasoning_delta_is_not_worth_a_transcript_read():
    """The stored part does not move on a delta, so a read would find it exactly as it was."""
    tap = _EventTap(_Client([_thought("I will "), _thought("draw it.")]), "s1")
    t0 = time.monotonic()
    woke = tap.wait(0.4)
    elapsed = time.monotonic() - t0
    tap.close()
    assert woke is False
    assert elapsed >= 0.4, "a reasoning delta ended the wait; a thought is now a read storm"


def test_the_end_of_a_thought_is_worth_a_read():
    final = AgentEvent(kind="reasoning", payload={"text": "I will draw it.", "part": "r1",
                                                  "final": True})
    tap = _EventTap(_Client([_thought("I will "), final]), "s1")
    woke = tap.wait(2.0)
    tap.close()
    assert woke is True


def test_the_wait_hands_over_every_queued_thought_at_once():
    """Deltas arrive dozens a second. One per wake, at the floor, would fall behind the model."""
    tap = _EventTap(_Client([_thought(str(i)) for i in range(50)]), "s1")
    thoughts: list = []
    for _ in range(20):
        if tap.seen_any:
            break
        time.sleep(0.02)
    t0 = time.monotonic()
    woke = tap.wait(2.0, thoughts=thoughts)
    elapsed = time.monotonic() - t0
    tap.close()
    assert woke is False
    assert elapsed < 0.5, f"waited {elapsed:.2f}s for thoughts that had already arrived"
    assert [e.payload["delta"] for e in thoughts] == [str(i) for i in range(50)]


def test_the_build_line_shows_a_thought_the_transcript_does_not_have_yet(tmp_path):
    orch, _ = _orch(tmp_path, [Turn(text="done", writes={"src/App.tsx": "x"})], cls=_Streaming,
                    frames=[_thought("I will draw the "), _thought("sales chart first.")])
    said = [e["text"] for e in orch.build_stream("make a chart") if e.get("type") == "narration"]
    assert said == ["I will draw the sales chart first."]


def test_a_thought_storm_is_not_a_read_storm(tmp_path):
    orch, oc = _orch(tmp_path, [Turn(text="done", writes={"src/App.tsx": "x"})], cls=_Streaming,
                     frames=[_thought("x ") for _ in range(200)])
    list(orch.build_stream("make a chart"))
    assert oc.reads < 10, f"{oc.reads} transcript reads for one thought"
