"""#435: a NULL in a sampled cell wrote a card nothing could read, and failed the turn that read it.

Measured live on `78e9223`, Thread `thr_1a0b72cb25a309defe984`: a turn sampled
`DWH.MARTS.MIXPANEL__EVENT` (3 rows, 106 columns), computed its answer with `live_read_query`,
published a correct card — and ended `ok:false, decision="table generation failed"`. Sage's own
`chat.table_validation` span named the file and the reason:

    path=examples/<thread>/mixpanel-event.table.json  reason=invalid JSON  repair_ran=True

The sample held 222 bare `NaN` tokens. `json.dumps` writes a non-finite float that way by default,
and every reader downstream rejects it: the browser's, which paints the card, and `ChatTables.check`,
which reads each `.table.json` back with `parse_constant` set. So one NULL cost the person a card
AND told them the turn had failed, beside a correct number.

Both halves are pinned here, and the fixtures that stand in for the live turn come through the real
producer — `result.record` for the bytes, `ChatTables.check` for the verdict. A hand-written
`{"rows": [[null]]}` would go green against a producer that still writes `NaN`. The one exception
is named where it stands: the nested-container test feeds `record` a shape no caller can send.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sage.liveread import result, run
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import DataSource, FakeResourceProvider, SampleRows
from sage.workspace.chat_tables import ChatTables, failed_table_name

from .fake_opencode import FakeOpenCode, Turn
from .test_a_chat_turn_works_a_number_out_in_sql import FakeAnswer, turn_for
from .test_chat_turn import OkFeedback, ScriptedGateway, _catalog


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


class NullingWarehouse(FakeResourceProvider):
    """A store that answers with a NULL, the way a real connector hands one back.

    `float("nan")` is not a choice this test made: it is what a NULL becomes on the way out of a
    warehouse read, and the value that produced the 222 bare tokens in the live measurement.
    """

    def __init__(self):
        super().__init__()
        self.data_sources = [DataSource(id="ds1", name="Snowflake-Data-Warehouse",
                                        connector="Snowflake", credential_type="Individual")]

    def sample_rows(self, source, database, schema, table, limit=5):
        return SampleRows(table, ["EVENT_ID", "EMAIL"], [[7, float("nan")]][:limit])


def _orch(tmp: Path, resources):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="ok")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=resources)
    orch.project(start_preview=False)
    return orch, oc


def _sample_through_the_real_producer(tmp_path: Path, slug: str) -> tuple[Path, str]:
    """One card, written by the code that wrote the live one. Returns its dir and the bytes."""
    examples = tmp_path / "examples" / "thr_x"
    result.record(examples, slug, "MIXPANEL__EVENT sample rows",
                  ["EVENT_ID", "EMAIL"], [[7, float("nan")]], keep_rows=True)
    return examples, (examples / f"{slug}.table.json").read_text()


def test_a_null_cell_is_written_as_json_null_and_never_as_a_bare_token(tmp_path: Path):
    _, raw = _sample_through_the_real_producer(tmp_path, "mixpanel-event")

    # The token itself, because that is what the browser and the validator both choke on. Asserting
    # only that `json.loads` succeeds would pass on a producer that wrote the string "nan".
    assert "NaN" not in raw
    assert json.loads(raw)["rows"] == [[7, None]]


def test_the_validator_that_failed_the_live_turn_accepts_the_card_now(tmp_path: Path):
    """The link the two halves hang on: the real writer against the real reader.

    `ChatTables.check` is the function whose verdict became `ok:false`. Running it here rather than
    re-reading the file with a plain `json.loads` is the difference between pinning the defect and
    pinning a restatement of the fix — `check` rejects what `json.loads` accepts by default.
    """
    _sample_through_the_real_producer(tmp_path, "mixpanel-event")
    rel = "examples/thr_x/mixpanel-event.table.json"

    tables = ChatTables(tmp_path, "thr_x", before={})
    assert tables.check("") == {}, "the sample is valid, so the turn that read it did not fail"
    assert rel in tables.candidates, "and it was actually looked at, not skipped"


def test_a_non_finite_float_that_skips_the_coercion_raises_instead_of_writing_a_dead_card(
        tmp_path: Path, monkeypatch):
    """`allow_nan=False` is a check, so it is planted rather than asserted about.

    With `json_safe` total, this guard cannot fire in production — which is exactly why it needs a
    test that puts it in the state it exists for. A later writer adding a field that skips the
    coercion gets a raise here, not another unreadable card.
    """
    monkeypatch.setattr(result, "json_safe", lambda v: v)
    examples = tmp_path / "examples" / "thr_x"

    # Matched, not bare: `record` could grow another `ValueError` later, and this test would go
    # green on it while saying nothing about the guard it is named for.
    with pytest.raises(ValueError, match="not JSON compliant"):
        result.record(examples, "mixpanel-event", "sample", ["EMAIL"], [[float("nan")]],
                      keep_rows=True)


def test_the_infinities_are_covered_by_the_same_rule(tmp_path: Path):
    examples = tmp_path / "examples" / "thr_x"
    result.record(examples, "wide", "t", ["A", "B"], [[float("inf"), float("-inf")]],
                  keep_rows=True)
    raw = (examples / "wide.table.json").read_text()

    assert "Infinity" not in raw
    assert json.loads(raw)["rows"] == [[None, None]]


def test_a_null_nested_in_a_container_is_covered_although_no_producer_sends_one(tmp_path: Path):
    """The one test here whose input no caller can currently produce, said out loud.

    `provider.sample_value` stringifies a dict or a list one layer above `record`, so this shape
    cannot arrive today and this test cannot tell you the coercion is reached in production. It
    pins the branch against the day that flattening moves or stops, and nothing more.
    """
    examples = tmp_path / "examples" / "thr_x"
    result.record(examples, "variant", "t", ["PROPS"],
                  [[{"score": float("nan"), "tags": [1, float("nan")]}]], keep_rows=True)
    raw = (examples / "variant.table.json").read_text()

    assert "NaN" not in raw
    assert json.loads(raw)["rows"] == [[{"score": None, "tags": [1, None]}]]


def test_a_turn_holding_a_sampled_null_and_an_answer_reports_that_it_answered(tmp_path: Path):
    """The live shape, end to end: two tables this turn, the sample built by the real producer.

    The sample's bytes are not typed out here. They are whatever `result.record` writes for a NULL,
    so this test follows the producer: if it goes back to writing `NaN`, the turn fails again and
    this reds — which is the whole point of not hand-rolling the fixture.
    """
    orch, oc = _orch(tmp_path, NullingWarehouse())
    tid = orch.create_thread()["id"]
    project = orch.project(start_preview=False)
    project.record.set_kept_rows(True)

    sample = f"examples/{tid}/mixpanel-event.table.json"
    answer = f"examples/{tid}/distinct-users.table.json"
    _, sample_bytes = _sample_through_the_real_producer(tmp_path / "staging", "mixpanel-event")
    oc.turns = [Turn(text="There are 1,373,861 distinct users.",
                     writes={sample: sample_bytes,
                             answer: json.dumps({"columns": ["DISTINCT_USERS"],
                                                 "rows": [[1373861]]})})]

    events = list(orch.chat_stream(tid, "count the distinct users in DWH.MARTS.MIXPANEL__EVENT"))

    done = next(e for e in events if e["type"] == "done")
    assert done["ok"] is True, "a turn that answered does not report failure (#435)"
    assert done["decision"] == "answered"
    assert not [e for e in events if e.get("reason") == "table generation failed"]
    assert {a["path"] for a in orch.get_thread(tid)["artifacts"]} == {sample, answer}
    assert len(oc.prompts) == 1, "and no repair attempt was spent on a card that was fine"


def test_a_failed_table_is_named_so_it_can_be_told_from_the_card_that_worked(tmp_path: Path):
    """#435's second half: the sentence stood beside a correct card and named neither.

    The failure here is an empty file, not a NULL — deliberately a cause the producer fix does not
    touch, so this pins the naming and not the coercion a second time.
    """
    orch, oc = _orch(tmp_path, NullingWarehouse())
    tid = orch.create_thread()["id"]
    orch.project(start_preview=False).record.set_kept_rows(True)

    broken = f"examples/{tid}/mixpanel-event.table.json"
    good = f"examples/{tid}/distinct-users.table.json"
    good_bytes = json.dumps({"columns": ["DISTINCT_USERS"], "rows": [[1373861]]})
    oc.turns = [Turn(text="There are 1,373,861 distinct users.",
                     writes={broken: "", good: good_bytes}),
                Turn(text="Could not repair.", writes={broken: ""})]

    events = list(orch.chat_stream(tid, "count the distinct users"))

    failed = next(e for e in events if e.get("reason") == "table generation failed")
    assert "mixpanel event" in failed["message"], failed["message"]
    assert "distinct users" not in failed["message"], "the card that worked is not accused too"
    assert [a["path"] for a in orch.get_thread(tid)["artifacts"]] == [good]


def test_the_name_in_the_sentence_is_the_name_on_the_card():
    """One word in common with the caption, and no Thread id in a sentence a person reads."""
    assert failed_table_name("examples/thr_1a0b/mixpanel-event.table.json") == "mixpanel event"
    assert failed_table_name("examples/thr_1a0b/distinct_users.table.json") == "distinct users"
    # Not a shape this repo writes, and the point is that it does not slice the name to pieces.
    assert failed_table_name("examples/thr_1a0b/odd.json") == "odd.json"


def _token(oc) -> str:
    m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", oc.prompts[-1]["text"])
    assert m, f"no token in the turn prompt:\n{oc.prompts[-1]['text'][:400]}"
    return m.group(1)


def test_a_real_sampling_read_writes_a_card_its_own_validator_accepts(tmp_path: Path):
    """The read path itself, not a card staged beside it.

    The turn test above builds its sample out of band, because `FakeOpenCode` never calls back into
    the tool server mid-turn. This one drives `live_read_table` for real — `NullingWarehouse` ->
    `run._table` -> `result.record` — so a regression anywhere along that path reds here rather than
    hiding behind bytes a test wrote itself.
    """
    orch, oc = _orch(tmp_path, NullingWarehouse())
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})
    list(orch.chat_stream(tid, "show me sample rows"))
    orch.set_kept_rows(True)

    orch.live_read_call({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "live_read_table", "arguments": {
            "token": _token(oc), "source": "Snowflake-Data-Warehouse",
            "database": "DWH", "schema": "MARTS", "table": "MIXPANEL__EVENT", "limit": 1}},
    })

    root = orch.project(start_preview=False).record.path
    rel = f"examples/{tid}/mixpanel-event.table.json"
    assert "NaN" not in (root / rel).read_text()
    assert ChatTables(root, tid, before={}).check("") == {}, "the read wrote a card that validates"


def test_the_query_lane_hands_the_model_a_null_and_never_a_bare_nan(tmp_path: Path):
    """`live_read_query` reads `answer.rows` again rather than the list it wrote.

    That lane leaves `binding` and `table` empty on purpose, so `receipt.values` is always None and
    the card's coercion never touched what the model is told. A `nan` in that sentence is a token
    the model cannot re-serialise, and the table it writes next fails validation the way #435's did.
    """
    turn, recorded = turn_for(tmp_path, keep_rows=True,
                              answer=FakeAnswer(["AVG_X"], [[float("nan")]]))
    said = run.perform("live_read_query",
                       {"source": "DWH", "sql": "SELECT AVG(X) AS AVG_X FROM E"}, turn)

    # Word-bounded: a bare `nan` token, not the substring — a column named FINANCE would
    # satisfy a plain `in` check and make this assertion useless.
    assert not re.search(r"\bnan\b", said, re.IGNORECASE), said
    assert "Result: [[None]]" in said
    _, reply = recorded[-1]
    assert reply["selected"]["rows"] == [[None]]


def test_two_failed_tables_sharing_a_basename_are_named_once(tmp_path: Path):
    """`check` globs recursively, so one Thread can hold two `moves.table.json` in two folders.

    Naming them from the basename makes the pair collide, and "moves, moves" tells the person there
    were two failures without telling them which two. A set is the whole fix; this is what arms it.
    """
    orch, oc = _orch(tmp_path, NullingWarehouse())
    tid = orch.create_thread()["id"]
    orch.project(start_preview=False).record.set_kept_rows(True)

    oc.turns = [Turn(text="Done.", writes={f"examples/{tid}/a/moves.table.json": "",
                                           f"examples/{tid}/b/moves.table.json": ""}),
                Turn(text="Could not repair.", writes={})]

    events = list(orch.chat_stream(tid, "make two tables"))

    failed = next(e for e in events if e.get("reason") == "table generation failed")
    assert failed["message"].count("moves") == 1, failed["message"]
    assert "some tables" in failed["message"], "and still says there was more than one"
