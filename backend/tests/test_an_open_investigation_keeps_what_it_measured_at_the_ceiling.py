"""An investigation labelled bounded still writes down what it measured at the ceiling (#601).

The investigation card is drawn only for `data_answer`, `data_artifact` or `build_app`, so the
question it replays is almost always labelled bounded. The arming reads `unbounded` and hands that
turn the full lane, shell and write; the findings slice read the label alone and never opened. The
person was told "nothing it measured was written down" under fifteen measurements (#600).

The helpers, and the autouse `_no_waiting` fixture with them, come from the slice's own file.
Without the fixture, real one-second sleeps outlast the 0.6s test slice and a correct gate still
reads red.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage.orchestrator import chat_intent
from sage.workspace.threads import ThreadStore, findings_file

from .fake_opencode import Turn
from .test_a_turn_stopped_at_the_ceiling_keeps_what_it_measured import (
    MEASURED,
    QUESTION,
    WorksUntilStopped,
    _findings_write,
    _no_waiting,  # noqa: F401
    _orch,
    _short_ceiling,
)


def _labelled(monkeypatch, label: str) -> None:
    class Classified:
        def result(self):
            return chat_intent.Intent(label=label, confidence=0.95)

    monkeypatch.setattr(chat_intent, "start", lambda *a, **k: Classified())


def _kept_it(orch, oc, tid: str, out: list[dict]) -> None:
    assert len(oc.prompts) == 2, "a lane armed to write findings is asked for them"
    root = orch.project(start_preview=False, seed_app=False).record.path
    assert findings_file(root, tid).read_text() == MEASURED
    offer = next(e for e in out if e["type"] == "continue-offer")
    assert offer["prompt"] == QUESTION
    said = next(e for e in out if e["type"] == "error")["message"]
    assert "nothing it measured was written down" not in said


@pytest.mark.parametrize("label", ["data_answer", "data_artifact"])
def test_an_open_investigation_opens_the_slice_whatever_the_label(tmp_path: Path, monkeypatch,
                                                                  label: str):
    _short_ceiling(monkeypatch)
    _labelled(monkeypatch, label)
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    orch.decide_thread_investigation(tid, "open")
    oc.turns.append(Turn(writes=_findings_write(tid)))

    out = list(orch.chat_stream(tid, QUESTION))

    _kept_it(orch, oc, tid, out)


def test_a_calculation_run_under_the_other_lane_grant_opens_the_slice(tmp_path: Path, monkeypatch):
    """The other way to `unbounded`, one calculation rather than a standing grant (#411). The
    Thread declines an investigation first so the investigation card cannot answer instead."""
    _short_ceiling(monkeypatch)
    _labelled(monkeypatch, "data_answer")
    oc = WorksUntilStopped(tmp_path / "mnt" / "code", [Turn(text="working on it")])
    orch = _orch(tmp_path, oc)
    tid = orch.create_thread()["id"]
    orch.decide_thread_investigation(tid, "decline")
    store = ThreadStore(orch._chat_project().record.path)
    orch._statements_tried[tid] = 1
    grant = next(iter(orch._chat_other_lane_offer(store, tid, QUESTION, claimed=True)))["grant"]
    oc.turns.append(Turn(writes=_findings_write(tid)))

    out = list(orch.chat_stream(tid, QUESTION, other_lane_grant=grant))

    _kept_it(orch, oc, tid, out)
