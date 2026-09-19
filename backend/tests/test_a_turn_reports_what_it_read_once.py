"""One "Data used" fold per turn, holding a section per operation (#447).

The card was written to describe a single data operation and was minted one per operation, so an
answer that read a table, computed on it and then analysed text stacked three collapsed dropdowns
under it — each saying the same kind of thing, none of them wrong, all of them the same disclosure
repeated. The grouping key was already in the data: `DataUse.record` stamps `turn_id` on every
event before it persists it, and the browser keyed on `operation_id` and ignored it.

`js/data_used_grouping_harness.mjs` drives the real store over a stubbed fetch and then renders
each block it produced through the real block dispatcher, because neither half settles the other:
the store can group correctly and the card can still drop every operation but the first. Counting
`<details>` elements and `sw-data-used-op` sections is the claim — a Python test grepping the JS
would pin the literal, not what a reader ends up looking at.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_JS = Path(__file__).resolve().parent / "js"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")


def _event(operation_id: str, turn_id: str | None, operation: str, source: str) -> dict:
    """One `dataUsed` event in the shape `DataUse.record` persists.

    `turn_id` of `None` is the restart population: `DataUse.restore` rebuilds operations from the
    transcript, and a row written before the stamp existed carries no turn.
    """
    event = {
        "operation_id": operation_id,
        "operation": operation,
        "source": source,
        "artifact": f"examples/thr_1/{operation_id}.table.json",
        "columns": ["region", "revenue"],
        "selected_fields": ["region", "total"],
        "coverage": {"total": 12, "processed": 12, "excluded": 0, "failed": 0, "unfinished": 0},
        "requests": [],
    }
    if turn_id is not None:
        event["turn_id"] = turn_id
    return event


def _open(*events: dict) -> dict:
    """Replay a Chat transcript carrying these events, one `data_used` row each, as the server
    writes them — `DataUse.record` persists one event per call, never a batch."""
    history: list[dict] = [{"type": "user", "text": "totals by region, and summarise the notes"}]
    history.extend({"type": "data_used", "dataUsed": [event]} for event in events)
    history.append({"type": "done", "text": "here you go"})
    payload = {"thread": {"id": "thr_1", "history": history}}
    out = subprocess.run(["node", str(_JS / "data_used_grouping_harness.mjs")],
                         input=json.dumps(payload), check=False, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_three_operations_on_one_turn_draw_one_card():
    result = _open(
        _event("du_1", "turn_a", "live_read", "sales.csv"),
        _event("du_2", "turn_a", "calculation", "sales.csv"),
        _event("du_3", "turn_a", "text_analysis", "notes.csv"),
    )
    assert result["cards"] == 1
    assert result["operations"] == [["du_1", "du_2", "du_3"]]
    drawn = result["rendered"][0]
    assert drawn["details"] == 1
    assert drawn["sections"] == 3
    # Grouping must not cost the reader anything the three separate cards told them. Each
    # operation still names its own source, its own Artifact and its own id.
    for operation_id in ("du_1", "du_2", "du_3"):
        assert f"Operation: {operation_id}" in drawn["words"]
        assert f"{operation_id}.table.json" in drawn["words"]
    assert "Analyzed through the LLM Gateway" in drawn["words"]
    assert "Calculated in Domino" in drawn["words"]
    # The fold is shut when a reader meets it, so the count is the only thing that can say it
    # holds more than one operation.
    assert "Data used (3 operations)" in drawn["words"]


@needs_node
def test_two_turns_keep_two_cards():
    result = _open(
        _event("du_1", "turn_a", "live_read", "sales.csv"),
        _event("du_2", "turn_a", "calculation", "sales.csv"),
        _event("du_3", "turn_b", "text_analysis", "notes.csv"),
    )
    assert result["cards"] == 2
    assert result["operations"] == [["du_1", "du_2"], ["du_3"]]
    assert [drawn["sections"] for drawn in result["rendered"]] == [2, 1]


@needs_node
def test_one_operation_still_draws_one_plain_card():
    result = _open(_event("du_1", "turn_a", "live_read", "sales.csv"))
    assert result["cards"] == 1
    drawn = result["rendered"][0]
    assert drawn["details"] == 1
    assert drawn["sections"] == 1
    assert "Data used (" not in drawn["words"]


@needs_node
def test_a_re_recorded_operation_updates_its_section_rather_than_adding_one():
    """`DataUse.observe` rewrites an event every time a gateway request of its own settles, and
    persists the whole event again. The card must show the later one, not both."""
    early = _event("du_1", "turn_a", "text_analysis", "notes.csv")
    late = {**early, "requests": [{"request_id": "req_1", "requested_alias": "policy-model",
                                   "state": "response_completed", "serving_model": "gpt-5.4",
                                   "provider_receipt": "rcpt_1", "decision_stage": "final",
                                   "delivery": "streamed", "cache": "miss", "fallback": "none"}]}
    result = _open(early, late)
    assert result["cards"] == 1
    assert result["operations"] == [["du_1"]]
    drawn = result["rendered"][0]
    assert drawn["sections"] == 1
    assert "Gateway response completed" in drawn["words"]
    assert "Gateway delivery: unknown" not in drawn["words"]


def _draw(block: dict) -> dict:
    """Render one hand-made block through the dispatcher, without the store."""
    out = subprocess.run(["node", str(_JS / "data_used_grouping_harness.mjs")],
                         input=json.dumps({"block": block}), check=False, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])["rendered"][0]


@needs_node
def test_a_card_with_no_operations_draws_nothing_rather_than_an_empty_promise():
    """An empty fold is a claim, not an error.

    A reader who opens "Data used" and finds no source, no coverage and no Artifact concludes the
    turn touched nothing. This is the last surface that should say that falsely, so it draws no
    card at all. Unreachable from `putDataUsed`, which always creates a block holding one event —
    which is why it is driven here directly rather than through a transcript.
    """
    drawn = _draw({"type": "data_used", "turnId": "turn_a", "events": []})
    assert drawn["details"] == 0
    assert drawn["sections"] == 0
    assert drawn["words"] == ""


@needs_node
def test_re_persists_multiply_by_read_not_by_model_call():
    """The trap in grouping on `turn_id`: it is not enough on its own.

    `DataUse.observe`'s `save()` re-persists the WHOLE event every time a gateway request touching
    it settles, so one read persists many rows. Group on `turn_id` and drop the `operation_id`
    dedupe and the turn's single card grows a section per RE-PERSIST — this ticket's own symptom
    moved inside the card, and scaling with model calls rather than with reads, so it reads worst
    on exactly the long investigations the card exists for.

    Two operations, five rows, interleaved the way a turn with several model calls emits them.
    """
    first = _event("du_1", "turn_a", "live_read", "sales.csv")
    second = _event("du_2", "turn_a", "text_analysis", "notes.csv")

    def touched(event: dict, request_id: str) -> dict:
        """The same operation, re-persisted after another gateway request settled on it."""
        request = {"request_id": request_id, "requested_alias": "policy-model",
                   "state": "response_completed", "serving_model": "gpt-5.4",
                   "provider_receipt": request_id, "decision_stage": "final",
                   "delivery": "streamed", "cache": "miss", "fallback": "none"}
        return {**event, "requests": [*event.get("requests", []), request]}

    once = touched(first, "req_1")
    result = _open(first, second, once, touched(second, "req_2"), touched(once, "req_3"))
    assert result["cards"] == 1
    # Two reads, five rows. Five sections would be the defect moved, not fixed.
    assert result["rendered"][0]["sections"] == 2
    # First-sighting order, not last-row order: `du_1` was read first and must stay first even
    # though the final re-persist in the stream is its own.
    assert result["operations"] == [["du_1", "du_2"]]
    # Latest copy wins on content — the re-persists are how `requests` accumulates, so keeping the
    # first copy would show a read with no gateway evidence at all.
    words = result["rendered"][0]["words"]
    assert "Data used (2 operations)" in words
    for request_id in ("req_1", "req_2", "req_3"):
        assert f"Request: {request_id}" in words
    assert "Gateway delivery: unknown" not in words


@needs_node
def test_events_carrying_no_turn_keep_their_own_cards():
    """Absence is not a value. Events that share only the ABSENCE of a turn must not be drawn as
    though they shared a turn.

    The population is `service.py`'s `self._data_use_turns.get(thread_id, "")` — an operation
    recorded before a turn was minted for its thread persists with an empty `turn_id`. It is NOT
    the restart replay: `DataUse.restore` deepcopies the persisted event and `record` stamps
    `turn_id` before persisting, so a restored event carries one.
    """
    result = _open(
        _event("du_1", None, "live_read", "sales.csv"),
        _event("du_2", None, "calculation", "sales.csv"),
    )
    assert result["cards"] == 2
    assert result["operations"] == [["du_1"], ["du_2"]]
