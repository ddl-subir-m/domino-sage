"""The turn half, on BOTH surfaces: a refused turn names the carrier and writes it down.

The event order is the contract — `error`, then the search, then the recall offer, then `done` —
because a client reading the stream in order must see what failed before it is offered a way out.
Chat and Build run the SAME generator; the only thing that differs is which transcript the rows
land in, and these tests assert that by making the same assertions twice.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator import recall
from sage.workspace.threads import ThreadStore

from .fake_opencode import Turn
from .test_a_refused_request_is_not_a_silent_turn import BLOCKED
from .test_chat_turn import _orch

REFUSED = ('{"detail":{"error":{"message":"Blocked by guardrail: Block PII",'
           '"type":"guardrail_blocked"}}}')
POISON = "222-33-4444"


def _payload() -> list[dict]:
    return [
        {"role": "system", "content": "You are Sage."},
        {"role": "user", "content": "read the files"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "read", "arguments": '{"filePath": "clean.csv"}'}},
            {"id": "t2", "type": "function",
             "function": {"name": "read", "arguments": '{"filePath": "raw.csv"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "ticker,week\nVLTA,2026-01-02"},
        {"role": "tool", "tool_call_id": "t2", "content": f"name,ssn\nJ Doe,{POISON}"},
    ]


class _Guardrail:
    """A gateway that refuses any payload still carrying the poison, and counts the probes.

    It counts the SEARCH's calls, not every call that reaches a gateway. A failed Chat turn also
    runs the handoff classifier (`handoff.py`), which is an ordinary model call and has nothing to
    do with this feature — counting it made "never searches" fail or pass depending on which other
    tests ran first, because that classifier's arming is not this test's business.

    The discriminator is the model. A probe re-asks the alias that refused, which the test itself
    plants in `last_refused`; nothing else in a turn has a reason to speak to that alias.
    """

    def __init__(self):
        self.probes = 0

    def route(self, request, labels):
        if str(request.get("model") or "") == "gpt-5.4":
            self.probes += 1
        blob = "\n".join(str(m.get("content") or "") for m in request.get("messages") or [])
        if POISON in blob:
            from sage.gateway.client import GatewayUpstreamError
            raise GatewayUpstreamError(400, "https://gw/v1/chat/completions", REFUSED)
        yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'

    def guardrail_events(self):
        raise NotImplementedError


def _history(orch, tid: str) -> list[dict]:
    return ThreadStore(orch.project(start_preview=False).record.path).read_history(tid)


def _run(tmp_path: Path):
    gw = _Guardrail()
    orch, _ = _orch(tmp_path, [Turn(error=BLOCKED)], gateway=gw)
    project = orch.project(start_preview=False)
    project.last_refused = ("gpt-5.4", _payload())
    tid = orch.create_thread()["id"]
    return orch, tid, list(orch.chat_stream(tid, "chart the panel spend")), gw


def test_the_search_names_the_file_and_leaves_the_others_alone(tmp_path: Path):
    _orch, _tid, out, _gw = _run(tmp_path)
    found = next(e for e in out if e["type"] == recall.FOUND)
    assert [c["label"] for c in found["carriers"]] == ["raw.csv"]
    assert found["complete"] is True
    assert found["surviving"] >= 1, "the other file survives, so the turn is worth re-running"


def test_the_rows_arrive_in_the_order_a_person_can_read(tmp_path: Path):
    _orch, _tid, out, _gw = _run(tmp_path)
    order = [e["type"] for e in out if e["type"] in
             ("error", recall.SEARCH, recall.FOUND, recall.SUGGEST, "done")]
    assert order[0] == "error"
    assert order.index(recall.SEARCH) < order.index(recall.FOUND) < order.index("done")


def test_the_search_survives_a_reload(tmp_path: Path):
    orch, tid, _out, _gw = _run(tmp_path)
    kinds = [r["type"] for r in _history(orch, tid)]
    assert recall.SEARCH in kinds and recall.FOUND in kinds


def test_a_turn_that_failed_for_another_reason_never_searches(tmp_path: Path):
    """`step_reason` has to start with `guardrail:` — the search is not a general error explainer."""
    gw = _Guardrail()
    orch, _ = _orch(tmp_path, [Turn(error={"name": "Error", "data": {"message": "disk full"}})],
                    gateway=gw)
    orch.project(start_preview=False).last_refused = ("gpt-5.4", _payload())
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "chart it"))
    assert not [e for e in out if e["type"] in (recall.SEARCH, recall.FOUND)]
    assert gw.probes == 0, "not one probe spent on a failure that is not a guardrail's"


def test_the_captured_payload_never_outlives_the_search(tmp_path: Path):
    """It holds what a policy just refused to move. One search, then gone."""
    orch, _tid, _out, _gw = _run(tmp_path)
    assert orch.project(start_preview=False).last_refused is None


def test_nothing_captured_means_nothing_claimed(tmp_path: Path):
    """Without a payload there is no honest answer, so the ladder is left to do its job."""
    gw = _Guardrail()
    orch, _ = _orch(tmp_path, [Turn(error=BLOCKED)], gateway=gw)
    tid = orch.create_thread()["id"]
    out = list(orch.chat_stream(tid, "chart it"))
    assert not [e for e in out if e["type"] == recall.SEARCH]
    assert [e for e in out if e["type"] == "error"], "the refusal is still reported"


def test_the_withheld_set_is_armed_from_the_transcript(tmp_path: Path):
    """A restart must not un-withhold: the poison comes back off disk, so the set has to as well."""
    orch, _ = _orch(tmp_path, [Turn(text="fine")])
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch.project(start_preview=False).record.path)
    store.append_history(tid, {"type": recall.WITHHELD, "keys": ["file:raw.csv"],
                               "labels": ["raw.csv"]})
    assert recall.withheld(store.read_history(tid)) == frozenset({"file:raw.csv"})


# ---- Build: the same generator, the app's transcript --------------------------------------------
# The 2x2 this feature has to satisfy is two surfaces by two carrier kinds. Chat's cells are above;
# these are Build's. The assertions are deliberately the same sentences — if either half ever needs
# its own branch in the search, the filter or the card, the seam is in the wrong place.

from .test_a_dead_alias_stops_the_turn_before_it_starts import BUILD, PLAN
from .test_a_dead_alias_stops_the_turn_before_it_starts import _orch as _build_orch
from .test_a_refused_request_is_not_a_silent_turn import BLOCKED as _BLOCKED

_NEST = _BLOCKED["data"]["message"]


def _build_history(orch) -> list[dict]:
    project = orch.project(start_preview=False)
    return project.app_for_turn().read_history(project.build_conversation)


def _run_build(tmp_path: Path, payload: list[dict] | None = None):
    orch, oc = _build_orch(tmp_path, turns=[PLAN, BUILD], break_on={2})
    oc.break_message = _NEST
    list(orch.build_stream("build me a consumption dashboard"))
    # The Build helper hardcodes a gateway that answers everything, and the search needs one that
    # refuses the way a guardrail does. Swapped after the plan turn so only the probes meet it.
    orch._gateway = _Guardrail()
    orch.project(start_preview=False).last_refused = ("gpt-5.4", payload or _payload())
    return orch, list(orch.approve_stream())


def test_build_names_the_file_the_same_way_chat_does(tmp_path: Path):
    _orch, out = _run_build(tmp_path)
    found = next(e for e in out if e["type"] == recall.FOUND)
    assert [c["label"] for c in found["carriers"]] == ["raw.csv"]
    assert found["complete"] is True


def test_build_puts_the_rows_in_the_same_order(tmp_path: Path):
    _orch, out = _run_build(tmp_path)
    order = [e["type"] for e in out if e["type"] in
             ("error", recall.SEARCH, recall.FOUND, recall.SUGGEST, "done")]
    assert order[0] == "error"
    assert order.index(recall.SEARCH) < order.index(recall.FOUND) < order.index("done")


def test_build_rows_survive_a_reload(tmp_path: Path):
    """Chat's store takes anything; Build drops whatever is not in `_PERSISTED_EVENTS`, and a
    withheld row that vanished would un-withhold the Conversation on the next restart."""
    orch, _out = _run_build(tmp_path)
    kinds = [r["type"] for r in _build_history(orch)]
    assert recall.SEARCH in kinds and recall.FOUND in kinds


def test_build_finds_pasted_text_too(tmp_path: Path):
    """The fourth cell: Build turns carry a typed prompt, so they can be poisoned without a file."""
    payload = [{"role": "system", "content": "You are Sage."},
               {"role": "user", "content": f"use ssn {POISON} as the sample row"}]
    _orch, out = _run_build(tmp_path, payload)
    found = next(e for e in out if e["type"] == recall.FOUND)
    assert [c["label"] for c in found["carriers"]] == ["the message you sent"]
    assert found["carriers"][0]["is_file"] is False
    assert found["surviving"] == 0, "nothing else was carried, so re-running would answer nothing"


def test_the_withheld_row_reaches_the_build_transcript(tmp_path: Path):
    orch, _out = _run_build(tmp_path)
    orch.withhold_build_content(["file:raw.csv"], ["raw.csv"])
    assert recall.withheld(_build_history(orch)) == frozenset({"file:raw.csv"})


def test_a_turn_the_local_scan_can_place_pays_for_two_probes_not_seven(tmp_path: Path):
    """The fast path, end to end. `refusal_scan` reads the captured payload for free and names the
    file; the search spends one call asking the gateway whether withholding it clears the refusal,
    and stops on CLEAN.

    MEASURED live 2026-09-11 against the real gateway, four files with one poisoned: 7 probes / 8.9s
    without the hint, 2 probes / 3.0s with it, the same carrier both ways. The probes ARE the wait —
    each one re-sends the whole conversation — so calls are the budget and bytes are not.
    """
    _orch, _tid, out, gw = _run(tmp_path)
    found = next(e for e in out if e["type"] == recall.FOUND)
    assert [c["label"] for c in found["carriers"]] == ["raw.csv"]
    assert found["complete"] is True
    assert gw.probes == 2, f"the scan placed it and the search still spent {gw.probes}"
