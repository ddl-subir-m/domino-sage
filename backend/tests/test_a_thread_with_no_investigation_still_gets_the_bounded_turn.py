"""The absence half of the gate, which is the load-bearing one: no grant, no exemption.

#364 bounds a Chat turn to the tools the question needs, and #386 carves one hole in that bounding.
The hole has to close again when the condition that opened it is gone, or the carve-out is not a
carve-out — it is the bounding removed, with a condition written beside it that never holds. A guard
gated on a value that is always true reads exactly like a guard that works.

So this is the other half of `test_an_investigation_in_progress_keeps_its_shell_on_a_data_answer`,
and the two only mean something together: the same question, in a Thread nobody granted anything,
still arms `arm_read_only("question")`.

TWO THREADS, not one, and the second is what catches the OFFER going too wide. A Thread with no
data on it cannot be offered an investigation at all, so it would go on passing if the trigger were
replaced with `True`. The second case binds a store and asks an ordinary question of it — if the
trigger fires on that, the turn yields a card and never reaches OpenCode, and `oc.snapshots` is
empty rather than read-only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from .fake_opencode import Turn
from .test_chat_turn import IntentGateway, ObservedControlOpenCode, _orch


@pytest.mark.parametrize("bind_a_store", [False, True], ids=["nothing bound", "a store bound"])
def test_a_data_answer_turn_without_an_investigation_is_still_armed_read_only(
    tmp_path: Path, bind_a_store: bool,
):
    turns = [Turn(text="They score highest.")]
    orch, oc = _orch(
        tmp_path, turns,
        gateway=IntentGateway({"label": "data_answer", "confidence": 0.91}),
        client=lambda ws: ObservedControlOpenCode(ws, list(turns)),
    )
    project = orch.project(start_preview=False)
    oc.control = project.control
    tid = orch.create_thread()["id"]
    if bind_a_store:
        orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                      "name": "Snowflake-Data-Warehouse"})
    assert orch.thread_context(tid).get("investigation") in (None, {})

    list(orch.chat_stream(tid, "so which of those accounts score highest?"))

    assert oc.snapshots
    assert oc.snapshots[0].read_only_turn
    assert oc.snapshots[0].read_only_reason == "question"
