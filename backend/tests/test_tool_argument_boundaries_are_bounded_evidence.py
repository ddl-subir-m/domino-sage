"""The tool-argument boundary observer keeps facts, never arguments, and changes nothing (#560).

Three surfaces, read in order: the installed codec (`provider.mjs` through `@ai-sdk/openai`), so
the expected result of every fixture is what the pinned dependency actually does; `StreamEvents`,
where Sage counts and compares; and `build_diagnostics.snapshot`, where the facts are admitted
into the download. The real-OpenCode half is `test_fragmented_tool_arguments_reach_real_opencode`.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage import build_diagnostics as diagnostics
from sage import timing
from sage.gateway.events import MAX_ARGUMENT_LANES, StreamEvents
from sage.gateway.protocol import Protocol

BACKEND = Path(__file__).resolve().parents[1]
SECRET = "SYNTHETIC-PRIVATE-ARGUMENT-560"
ARGS = {"label": "a", "text": f'says "hi"\n{SECRET} é \U0001f642'}
TEXT = json.dumps(ARGS, ensure_ascii=False)
CONTRACT = {"nonce": "n-560", "effort": None}


def frames(*, deltas, done_text=None, item_done=True, arguments_done=True, completed=True,
           item_id="fc_1", call_id="call_1", index=0):
    """A Responses stream for one `probe` call, with the fixture's own ids."""
    envelope = {"id": "resp_1", "store": False, "metadata": {"sage_route_check": "n-560"}}
    out = [{"type": "response.created", "response": envelope},
           {"type": "response.output_item.added", "output_index": index,
            "item": {"id": item_id, "type": "function_call", "call_id": call_id,
                     "name": "probe", "arguments": "", "status": "in_progress"}}]
    for delta in deltas:
        out.append({"type": "response.function_call_arguments.delta", "output_index": index,
                    "item_id": item_id, "delta": delta})
    done_text = "".join(deltas) if done_text is None else done_text
    if arguments_done:
        out.append({"type": "response.function_call_arguments.done", "output_index": index,
                    "item_id": item_id, "arguments": done_text})
    if item_done:
        out.append({"type": "response.output_item.done", "output_index": index,
                    "item": {"id": item_id, "type": "function_call", "call_id": call_id,
                             "name": "probe", "arguments": done_text, "status": "completed"}})
    if completed:
        out.append({"type": "response.completed", "response": {**envelope, "usage": {
            "input_tokens": 1, "output_tokens": 1}}})
    return out


def wire(events: list[dict]) -> bytes:
    return b"".join(b"data: " + json.dumps(e, ensure_ascii=False).encode() + b"\n\n" for e in events)


def fed(events: list[dict], *, size: int = 5, protocol=Protocol.RESPONSES,
        contract=CONTRACT) -> tuple[StreamEvents, bytes]:
    parser = StreamEvents(protocol, response_contract=contract)
    raw = wire(events)
    forwarded = b""
    for at in range(0, len(raw), size):
        forwarded += b"".join(parser.feed(raw[at:at + size]))
    return parser, forwarded


def pieces(text: str, n: int) -> list[str]:
    return [text[i:i + n] for i in range(0, len(text), n)]


def codec(events: list[dict], *, chunk: int | None = None) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the installed native codecs")
    run = subprocess.run([node, str(BACKEND / "tests/js/native_codec_argument_boundary_harness.mjs"),
                          str(BACKEND / "sage/driver/provider.mjs")],
                         input=json.dumps({"frames": events, "chunk": chunk}),
                         capture_output=True, text=True, timeout=30, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


# --- the pinned codec: what "expected" means ---------------------------------------------------

@pytest.mark.parametrize("chunk", [None, 1, 3, 7])
def test_the_installed_codec_executes_the_completion_once_whatever_the_byte_split(chunk):
    """Criterion (a), codec half. The deltas reach OpenCode as UI text and the `tool-call` carries
    `output_item.done`'s arguments once: byte and frame boundaries change nothing."""
    report = codec(frames(deltas=pieces(TEXT, 4)), chunk=chunk)
    assert report["error"] is None, report
    calls = [e for e in report["events"] if e["type"] == "tool-call"]
    assert calls == [{"type": "tool-call", "id": "call_1", "tool": "probe", "input": TEXT,
                      "itemId": "fc_1"}]
    deltas = [e["delta"] for e in report["events"] if e["type"] == "tool-input-delta"]
    assert "".join(deltas) == TEXT and deltas == pieces(TEXT, 4)
    assert [e["type"] for e in report["events"]][:2] == ["tool-input-start", "tool-input-delta"]


def test_the_installed_codec_runs_the_completion_not_the_deltas_when_they_disagree():
    """Criterion (c), codec half: the join and the completion are two different strings and the
    codec's `tool-call` is the completion. Sage records that; it does not choose."""
    other = json.dumps({"label": "b", "text": "other"})
    report = codec(frames(deltas=pieces(TEXT, 6), done_text=other))
    [call] = [e for e in report["events"] if e["type"] == "tool-call"]
    assert call["input"] == other
    assert "".join(e["delta"] for e in report["events"] if e["type"] == "tool-input-delta") == TEXT


def test_the_installed_codec_emits_no_tool_call_without_output_item_done():
    """A stream that never reaches `output_item.done` announces and streams but executes
    nothing, whether or not `arguments.done` arrived. So an incomplete call cannot run."""
    for arguments_done in (False, True):
        report = codec(frames(deltas=pieces(TEXT, 6), item_done=False,
                              arguments_done=arguments_done, completed=False))
        kinds = [e["type"] for e in report["events"]]
        assert "tool-call" not in kinds and "tool-input-start" in kinds, report


# --- StreamEvents: facts, not arguments --------------------------------------------------------

def test_the_observer_forwards_the_wire_bytes_unchanged_and_keeps_the_contract():
    """Criterion (d): the frames handed on are the bytes that came in, and the nonce/store check
    still refuses a translated fallback with lanes open."""
    events = frames(deltas=pieces(TEXT, 3))
    parser, forwarded = fed(events, size=4)
    assert forwarded == wire(events)
    parser.finish()
    assert parser.argument_boundaries()["lanes"][0]["boundary"] == "match"
    bad = copy.deepcopy(events)
    bad[-1]["response"]["metadata"] = {"sage_route_check": "someone-else"}
    with pytest.raises(ValueError, match="did not preserve"):
        fed(bad)


@pytest.mark.parametrize("size", [1, 2, 3, 5, 11, 4096])
def test_counts_and_the_comparison_do_not_depend_on_where_bytes_are_cut(size):
    parser, _ = fed(frames(deltas=["", *pieces(TEXT, 7), ""]), size=size)
    parser.finish()
    [lane] = parser.argument_boundaries()["lanes"]
    assert lane == {
        "lane": "fc_1", "providerId": "call_1", "name": "probe", "outputIndex": 0,
        "deltaJoin": {"count": len(pieces(TEXT, 7)) + 2, "empty": 2, "bytes": len(TEXT.encode())},
        "done": {"source": "both", "bytes": len(TEXT.encode()), "jsonValidity": "valid_object",
                 "agreement": "agree"},
        "boundary": "match", "terminal": "closed"}
    assert parser.argument_boundaries()["responseId"] == "resp_1"


def test_no_argument_text_survives_in_the_observer_or_the_record():
    """Criterion (e), private data: a valid call, a mismatching completion, and an invalid one
    that carries the secret inside broken JSON. None of it is in the parser, the recorder or the
    diagnostics record. Only the fixture knows the text."""
    cases = [frames(deltas=pieces(TEXT, 5)),
             frames(deltas=pieces(TEXT, 5), done_text=json.dumps({"x": SECRET + "-done"})),
             frames(deltas=pieces('{"text": "' + SECRET, 5), done_text='{"text": "' + SECRET)]
    for events in cases:
        parser, _ = fed(events)
        parser.finish()
        assert SECRET not in repr(parser) and SECRET not in json.dumps(parser.argument_boundaries())
    parser, _ = fed(cases[2])
    parser.finish()
    [lane] = parser.argument_boundaries()["lanes"]
    assert lane["done"]["jsonValidity"] == "invalid" and lane["boundary"] == "match"
    parser, _ = fed(cases[1])
    parser.finish()
    assert parser.argument_boundaries()["lanes"][0]["boundary"] == "mismatch"
    record = _snapshot(parser)
    assert SECRET not in json.dumps(record)
    assert record["toolArgumentBoundaries"]["calls"][0]["lanes"][0]["boundary"] == "mismatch"


def test_two_lanes_never_share_a_count_and_an_orphan_delta_is_counted_not_attributed():
    """Criterion (e), cross-call contamination: interleaved deltas for two items land on their
    own lanes by item id, a delta for an unannounced item is an orphan, and re-announcing an
    item id opens no second lane."""
    text_a, text_b = json.dumps({"a": 1}), json.dumps({"bb": 22})
    envelope = {"id": "resp_2", "store": False, "metadata": {"sage_route_check": "n-560"}}
    events = [{"type": "response.created", "response": envelope}]
    for index, item, call in ((0, "fc_a", "call_a"), (1, "fc_b", "call_b")):
        events.append({"type": "response.output_item.added", "output_index": index,
                       "item": {"id": item, "type": "function_call", "call_id": call, "name": "probe"}})
    events.append(copy.deepcopy(events[-1]))  # the same announcement twice
    for da, db in zip(pieces(text_a, 3), pieces(text_b, 4)):
        events.append({"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_a", "delta": da})
        events.append({"type": "response.function_call_arguments.delta", "output_index": 1, "item_id": "fc_b", "delta": db})
    events.append({"type": "response.function_call_arguments.delta", "output_index": 7, "item_id": "fc_ghost", "delta": "{}"})
    for index, item, call, text in ((0, "fc_a", "call_a", text_a), (1, "fc_b", "call_b", text_b)):
        events.append({"type": "response.output_item.done", "output_index": index,
                       "item": {"id": item, "type": "function_call", "call_id": call, "name": "probe",
                                "arguments": text}})
    events.append({"type": "response.completed", "response": envelope})
    parser, _ = fed(events)
    parser.finish()
    facts = parser.argument_boundaries()
    assert [(l["lane"], l["providerId"], l["deltaJoin"]["bytes"], l["boundary"]) for l in facts["lanes"]] == [
        ("fc_a", "call_a", len(text_a), "match"), ("fc_b", "call_b", len(text_b), "match")]
    assert facts["orphanDeltas"] == 1 and len(facts["lanes"]) == 2


def test_a_call_id_is_scoped_to_its_own_call_and_a_duplicate_done_is_seen():
    """Criterion (e), id reuse and duplicate events: the same `call_1` in two model calls gives
    two rows keyed by `n`; a repeated `output_item.done` is compared, not double-counted."""
    events = frames(deltas=pieces(TEXT, 5))
    events.insert(-1, copy.deepcopy(events[-2]))  # output_item.done twice
    parser, _ = fed(events)
    parser.finish()
    [lane] = parser.argument_boundaries()["lanes"]
    assert lane["done"] == {"source": "both", "bytes": len(TEXT.encode()),
                            "jsonValidity": "valid_object", "agreement": "agree"}
    assert lane["deltaJoin"]["count"] == len(pieces(TEXT, 5))
    disagreeing = frames(deltas=pieces(TEXT, 5))
    disagreeing.insert(-1, copy.deepcopy(disagreeing[-2]))
    disagreeing[-2]["item"]["arguments"] = "{}"
    parser, _ = fed(disagreeing)
    parser.finish()
    assert parser.argument_boundaries()["lanes"][0]["done"]["agreement"] == "disagree"
    record = _snapshot(fed(frames(deltas=["{}"]))[0], fed(frames(deltas=["{}"]))[0])
    rows = record["toolArgumentBoundaries"]["calls"]
    assert [(r["n"], r["lanes"][0]["providerId"], r["lanes"][0]["boundary"]) for r in rows] == [
        (1, "call_1", "match"), (2, "call_1", "match")]


def test_lanes_are_capped_per_call_and_per_turn_and_the_cap_is_said():
    """Criterion (e), memory: forty lanes per call, two hundred per turn, and a megabyte of
    argument costs an integer. Truncation is reported in the section and the capture."""
    envelope = {"id": "resp_3", "store": False, "metadata": {"sage_route_check": "n-560"}}
    events = [{"type": "response.created", "response": envelope}]
    for i in range(MAX_ARGUMENT_LANES + 5):
        events.append({"type": "response.output_item.added", "output_index": i,
                       "item": {"id": f"fc_{i}", "type": "function_call", "call_id": f"call_{i}", "name": "probe"}})
    events.append({"type": "response.completed", "response": envelope})
    parser, _ = fed(events, size=4096)
    parser.finish()
    facts = parser.argument_boundaries()
    assert len(facts["lanes"]) == MAX_ARGUMENT_LANES and facts["lanesTruncated"] is True
    big = "x" * (512 * 1024)  # two of these in one call; one event stays under MAX_EVENT_BYTES
    parser, _ = fed(frames(deltas=[big] * 2, done_text=big * 2), size=1 << 20)
    parser.finish()
    assert parser.argument_boundaries()["lanes"][0]["deltaJoin"]["bytes"] == 2 * len(big)
    assert len(json.dumps(parser.argument_boundaries())) < 600
    parsers = [fed(events, size=4096)[0] for _ in range(6)]
    for p in parsers:
        p.finish()
    record = _snapshot(*parsers)
    section = record["toolArgumentBoundaries"]
    assert sum(len(r["lanes"]) for r in section["calls"]) == diagnostics.MAX_BOUNDARY_LANES
    assert section["truncated"] is True
    assert record["capture"]["upstreamTruncated"]["toolArgumentBoundaries"] is True
    assert record["capture"]["complete"] is False


def test_the_record_admits_only_the_fixed_shape():
    """A lane with a foreign key, a text where a count belongs, or a word outside the closed
    vocabulary is dropped whole and the drop is said; a call with no lanes has no row."""
    good = fed(frames(deltas=["{}"]))[0]
    good.finish()
    raw = timing.as_dict(_record(good))
    raw["calls"][0]["toolArgumentBoundaries"]["lanes"][0]["arguments"] = SECRET
    raw["calls"][0]["toolArgumentBoundaries"]["lanes"].append(
        {**copy.deepcopy(raw["calls"][0]["toolArgumentBoundaries"]["lanes"][0]), "boundary": SECRET})
    raw["calls"][0]["toolArgumentBoundaries"]["lanes"].append(
        {**copy.deepcopy(raw["calls"][0]["toolArgumentBoundaries"]["lanes"][0]),
         "deltaJoin": {"count": SECRET, "empty": 0, "bytes": 0}})
    section = diagnostics._tool_argument_boundaries(raw["calls"])
    assert len(section["calls"][0]["lanes"]) == 1 and section["calls"][0]["lanesTruncated"] is True
    assert SECRET not in json.dumps(section)
    assert diagnostics._tool_argument_boundaries([{"n": 1, "toolArgumentBoundaries": None}]) is None


@pytest.mark.parametrize("protocol", [Protocol.MESSAGES, Protocol.CHAT])
def test_the_other_lanes_count_and_close_without_a_completion_to_compare(protocol):
    """Messages and Chat carry no completion event, so their lanes close as `no_done`: counted,
    never compared. Criterion (f)'s observer half; the codec smoke is in the transport test."""
    if protocol is Protocol.MESSAGES:
        events = [{"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
                  {"type": "content_block_start", "index": 0,
                   "content_block": {"type": "tool_use", "id": "toolu_1", "name": "probe"}},
                  *({"type": "content_block_delta", "index": 0,
                     "delta": {"type": "input_json_delta", "partial_json": p}} for p in pieces(TEXT, 5)),
                  {"type": "content_block_stop", "index": 0}, {"type": "message_stop"}]
    else:
        events = [{"choices": [{"index": 0, "delta": {"tool_calls": [
                      {"index": 0, "id": "call_1", "function": {"name": "probe", "arguments": ""}}]}}]},
                  *({"choices": [{"index": 0, "delta": {"tool_calls": [
                      {"index": 0, "function": {"arguments": p}}]}}]} for p in pieces(TEXT, 5)),
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
    parser, forwarded = fed(events, protocol=protocol, contract=None)
    assert forwarded == wire(events)
    parser.finish()
    [lane] = parser.argument_boundaries()["lanes"]
    assert (lane["providerId"], lane["boundary"], lane["terminal"]) == (
        "toolu_1" if protocol is Protocol.MESSAGES else "call_1", "no_done", "closed")
    assert lane["deltaJoin"]["bytes"] == len(TEXT.encode())
    assert lane["deltaJoin"]["count"] == len(pieces(TEXT, 5)) + (protocol is Protocol.CHAT)
    assert SECRET not in repr(parser)


# --- helpers ------------------------------------------------------------------------------------

def _record(*parsers: StreamEvents) -> timing.TurnRecord:
    record = timing.TurnRecord(kind="build", started_at=0.0, t0=0.0)
    for n, parser in enumerate(parsers, 1):
        call = timing.ModelCall(n=n, t0=0.0, protocol=parser.protocol.value, call_id=f"c{n}")
        call.tool_argument_boundaries = parser.argument_boundaries()
        record.calls.append(call)
    return record


def _snapshot(*parsers: StreamEvents) -> dict:
    return diagnostics.snapshot(_record(*parsers), {"turnId": "t", "appId": "a", "conversationId": "c",
                                                    "kind": "build"}, terminal=True)
