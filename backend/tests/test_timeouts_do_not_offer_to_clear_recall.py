"""Two idle timeouts in the earnings dashboard were drawn as content refusals."""
from pathlib import Path

import pytest

from sage.orchestrator import recall

from .test_a_dead_alias_stops_the_turn_before_it_starts import (
    BUILD,
    PLAN,
    _no_waiting,  # noqa: F401
    _orch,
)


@pytest.mark.parametrize("raw", [
    "Upstream idle timeout exceeded",
    "gateway returned 500: Internal Server Error",
    "gateway returned 404: Model 'missing' not found",
])
def test_transport_errors_never_enter_the_recall_ladder(raw):
    error = {"type": "error", "reason": recall.reason_key(raw)}
    assert recall.offer([error, error]) is None
    assert recall.offer_now([error]) is None
    after_clear = [error, {"type": recall.CLEARED, "scope": recall.EMPTY}, error]
    assert recall.terminal(after_clear) is False


def test_two_build_timeouts_keep_the_plan_without_claiming_a_content_refusal(tmp_path: Path):
    orch, oc = _orch(tmp_path, turns=[PLAN, BUILD, BUILD], break_on={2, 3})
    oc.break_message = "Upstream idle timeout exceeded"
    list(orch.build_stream("build me a dashboard"))
    list(orch.approve_stream())
    events = list(orch.build_stream("try again"))

    error = next(e for e in events if e["type"] == "error")
    assert "Upstream idle timeout exceeded" in error["message"]
    assert "try again" in error["message"]
    assert not any(e["type"] == recall.SUGGEST for e in events)
    assert next(e for e in events if e["type"] == "done")["ok"] is False
