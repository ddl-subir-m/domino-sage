"""ADR-0063 — a read that finds the way is not a read that answers.

An investigation spends most of its reads finding out where to read, and every one of them
published a `.table.json` that was drawn as a full card. Measured on the dogfood workspace on
2026-09-19: one Gong Thread held sixty table cards, about twenty-nine of them catalogue listings,
and the answer was among them.

The classification cannot happen where the card is made. Artifacts are DISCOVERED —
`new_artifact_paths` diffs the workspace and `record_artifact` is handed a path and nothing else —
so by then the statement that produced the file is gone. It happens at the operation, where the
statement is in hand, and only the verdict travels: `liveread/run.py` stores `source_sha256` and
never the SQL, because the event is persisted into a Thread's history and a statement carries
literals.

Three claims, in the order they would hurt if they broke:

- **The fallback is `'answer'`, in both directions.** A row written before this field existed and a
  model that never sets the flag both DRAW. A viewer who sees a step they did not need has a
  cluttered transcript; a viewer whose answer was folded away has been lied to with no way to find
  out. Most of this file is about that direction.
- **The catalogue rule is parsed, never matched.** A regex over SQL cannot tell the word
  `INFORMATION_SCHEMA` in a `FROM` clause from the same word in a string literal, and #450 is open
  against exactly that shape of reasoning over an Artifact path. `test_a_literal_…` is that plant.
- **The join really reaches the card.** The role is decided in `liveread`, carried on the event's
  `artifact` path, and joined at both publish sites. `test_a_catalogue_read_in_a_chat_turn_…` and
  its Build twin drive the whole length of that.

What the transcript then DOES with the role is `js/working_reads_fold_harness.mjs` and
`test_a_folded_read_is_never_fetched.py`.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from sage.liveread import mcp, run
from sage.liveread.data_use import DataUse, artifact_roles, fell_short
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode
from .test_a_live_read_reaches_the_person_end_to_end import (
    Warehouse,
    _bind,
    _build_orch,
    _orch,
)
from .test_csv_calculation_data_used import args as sum_args
from .test_csv_calculation_data_used import setup_turn as sum_turn
from .test_csv_text_analysis_data_used import analysis_args
from .test_csv_text_analysis_data_used import setup_turn as analysis_turn

REPO = Path(__file__).resolve().parents[2]


class FakeAnswer:
    def __init__(self, columns, rows, truncated=False):
        self.columns = columns
        self.rows = rows
        self.truncated = truncated


def _query_turn(tmp_path, answer=None, **over):
    """A turn that can run one composed statement and record what it made of it.

    `journal` is what `DataUse` PERSISTS — the row that goes into the Thread's history and is
    committed. Held apart from the in-memory event because "no event carries a statement" and "no
    history row carries one" are two claims, and only one of them reaches git.
    """
    data = DataUse()
    journal: list[dict] = []
    turn = run.Turn(
        thread_id="thr_role",
        examples_dir=tmp_path / "examples" / "thr_role",
        keep_rows=True,
        bound={"datasource": ("DWH",)},
        source_for=lambda n: object() if n == "DWH" else None,
        run_statement=lambda s, sql, limit: answer or FakeAnswer(["N"], [[41234]]),
        record_data_use=lambda ev, reply: data.record(ev, reply, journal.append, "turn1"),
    )
    return replace(turn, **over), data, journal


def _role_of(tmp_path, sql, **args):
    turn, data, _journal = _query_turn(tmp_path)
    run.perform("live_read_query", {"token": "t", "source": "DWH", "sql": sql, **args}, turn)
    events = data.events("turn1")
    assert len(events) == 1, f"one read, one event: {events}"
    return events[0]["role"]


# ---- the mechanical half: a catalogue read is working, from the statement alone ----------------

@pytest.mark.parametrize("sql", [
    "SELECT TABLE_NAME, ROW_COUNT FROM DWH.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'MARTS'",
    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'GONG_CALLS'",
    "SHOW TABLES",
    "SHOW SCHEMAS IN DATABASE DWH",
    "DESCRIBE TABLE DWH.MARTS.GONG_CALLS",
    "SELECT * FROM PG_CATALOG.PG_TABLES",
    "SELECT NAME FROM SQLITE_MASTER",
    # A CTE's own name parses as a table where it is selected from. It is not one, and reading it
    # as one would have made every wrapped catalogue read an answer.
    "WITH c AS (SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES) SELECT COUNT(*) AS N FROM c",
])
def test_a_catalogue_read_is_working_with_no_flag_from_the_model(tmp_path, sql):
    """The ~29. This is the half that needs no judgement and no trust, which is the whole reason
    ADR-0063 splits the rule in two."""
    assert _role_of(tmp_path, sql) == "working"


def test_a_literal_that_merely_says_information_schema_is_an_answer(tmp_path):
    """The plant against #450's shape, and the reason this rule parses instead of matching.

    A substring test over the statement folds this card away — a real read of real rows, hidden
    because a value in its own predicate happened to spell a catalogue name. ADR-0063 will not have
    a rule that can hide an answer, and the transcript gives a viewer no way to find out it
    happened: the fold's face counts rows, it does not say what was in them.
    """
    assert _role_of(
        tmp_path,
        "SELECT COUNT(*) AS N FROM DWH.MARTS.GONG_CALLS WHERE NOTE = 'information_schema'",
    ) == "answer"


def test_a_comment_naming_a_catalogue_is_an_answer(tmp_path):
    """The same claim one layer along: a parser drops a comment, a scanner cannot."""
    assert _role_of(
        tmp_path,
        "-- staged from INFORMATION_SCHEMA.TABLES\nSELECT COUNT(*) AS N FROM DWH.MARTS.GONG_CALLS",
    ) == "answer"


def test_a_join_between_the_catalogue_and_real_rows_is_an_answer(tmp_path):
    """Every table must be a catalogue one, not merely some of them. A statement that reaches real
    rows is measuring real rows, whatever else it also reads."""
    assert _role_of(tmp_path, (
        "SELECT COUNT(*) AS N FROM DWH.MARTS.GONG_CALLS c "
        "JOIN DWH.INFORMATION_SCHEMA.COLUMNS i ON i.TABLE_NAME = 'GONG_CALLS'"
    )) == "answer"


def test_a_statement_that_does_not_parse_is_an_answer(tmp_path):
    """Unreadable is not the same as catalogue. Everything here fails towards the card drawing."""
    assert _role_of(tmp_path, "SELECT COUNT(*) FROM ((( WHERE") == "answer"


# ---- the declared half: the same statement, two roles ------------------------------------------

def test_the_same_statement_is_a_step_or_the_answer_depending_on_who_asked(tmp_path):
    """The ~11, and the reason there is a flag at all.

    `SELECT COUNT(*) FROM GONG_CALLS` is working when the question is which customers use model
    monitoring and is the answer when the question is how many Gong calls there are. The statement
    is identical; only the caller knows which it is.
    """
    sql = "SELECT COUNT(*) AS N FROM DWH.MARTS.GONG_CALLS"
    assert _role_of(tmp_path, sql, step=True) == "working"
    assert _role_of(tmp_path, sql) == "answer"


@pytest.mark.parametrize("sent,expected", [
    (True, "working"),
    ("true", "working"),
    (False, "answer"),
    ("false", "answer"),
    (None, "answer"),
    ("yes", "answer"),
    (1, "answer"),
])
def test_only_a_true_flag_declares_a_step(tmp_path, sent, expected):
    """A model relaying a boolean as its word meant the flag, and dropping it silently would cost
    the half of the volume the catalogue rule cannot reach. Nothing else counts — an unreadable
    value is an answer, which draws."""
    assert _role_of(tmp_path, "SELECT COUNT(*) AS N FROM DWH.MARTS.GONG_CALLS",
                    step=sent) == expected


def test_a_flagged_read_still_writes_no_statement_anywhere(tmp_path):
    """ADR-0063's one hard constraint on the carrier, re-asserted on the lane that grew a field.

    The event is persisted into the Thread's history, which is committed, and a predicate carries
    literals. Only the verdict may travel.
    """
    sql = "SELECT COUNT(*) AS N FROM DWH.MARTS.GONG_CALLS WHERE EMAIL = 'person@example.invalid'"
    turn, data, journal = _query_turn(tmp_path)
    said = run.perform("live_read_query",
                       {"token": "t", "source": "DWH", "sql": sql, "step": True}, turn)
    assert journal, "nothing was persisted, so this pins nothing about the committed history"
    written = json.dumps(data.events("turn1")) + json.dumps(journal) + said
    for card in (tmp_path / "examples" / "thr_role").rglob("*"):
        if card.is_file():
            written += card.read_text()

    assert data.events("turn1")[0]["role"] == "working"
    assert "person@example.invalid" not in written
    assert "SELECT COUNT(*)" not in written


# ---- the other operation lanes always draw -----------------------------------------------------

def test_a_csv_calculation_records_the_answer_role(tmp_path):
    """`calculate` is something the turn was ASKED to do (ADR-0063, *What this does not decide*).
    The field is on every operation so the shape stays uniform; only the read lanes compute it."""
    turn, data, _journal = sum_turn(tmp_path / "up")
    run.perform("live_read_files", sum_args(), turn)

    assert [e["role"] for e in data.events("turn1")] == ["answer"]


def test_text_analysis_records_the_answer_role(tmp_path):
    """The other lane named in the same paragraph, driven rather than read."""
    turn, data, _journal, _src = analysis_turn(tmp_path / "up")
    run.perform("live_read_files", analysis_args(), turn)

    assert [e["role"] for e in data.events("turn1")] == ["answer"]


# ---- the publish-side join ---------------------------------------------------------------------

def test_the_join_keys_on_the_path_the_operation_wrote():
    events = [{"artifact": "examples/t/a.table.json", "role": "working"},
              {"artifact": "examples/t/b.table.json", "role": "answer"}]

    assert artifact_roles(events) == {"examples/t/a.table.json": "working",
                                      "examples/t/b.table.json": "answer"}


@pytest.mark.parametrize("event", [
    {"artifact": "p"},
    {"artifact": "p", "role": None},
    {"artifact": "p", "role": ""},
    {"artifact": "p", "role": "step"},
    {"artifact": "p", "role": "WORKING"},
])
def test_an_absent_or_unrecognised_role_draws(event):
    """The load-bearing direction. A row written before this shipped carries no role at all, and an
    unknown word must not be taken for a verdict — both draw, which is what makes the day this
    ships uneventful for every Thread that already exists."""
    assert artifact_roles([event]) == {"p": "answer"}


def test_an_event_with_no_artifact_path_joins_to_nothing():
    assert artifact_roles([{"role": "working"}, {"artifact": "", "role": "working"}]) == {}


@pytest.mark.parametrize("short", [
    {"coverage": {"excluded": 1}},
    {"coverage": {"failed": 2}},
    {"coverage": {"unfinished": 1}},
    {"requests": [{"request_id": "r", "state": "failed", "failure": "policy"}]},
    {"requests": [{"request_id": "r", "state": "interrupted", "failure": None}]},
])
def test_a_working_read_that_fell_short_is_never_folded(short):
    """Constraint 2, and the same rule ADR-0062 §2 settles for `data_used`: when the row carries a
    shortfall it draws. Hidden, a read that half-worked is indistinguishable from one that worked,
    and the fold is what made it so.

    Downgraded HERE rather than at the operation, because a request can fail after the read
    returned — the verdict is only as complete as the end of the turn.
    """
    event = {"artifact": "p", "role": "working", **short}

    assert fell_short(event) is True
    assert artifact_roles([event]) == {"p": "answer"}


def test_a_whole_read_is_not_a_short_one():
    """The other side of the same predicate: `attempted` and a settled response are not shortfalls,
    so an ordinary working read really does fold."""
    event = {"artifact": "p", "role": "working",
             "coverage": {"total": 9, "processed": 9, "excluded": 0, "failed": 0, "unfinished": 0},
             "requests": [{"request_id": "r", "state": "response_completed", "failure": None},
                          {"request_id": "r2", "state": "attempted", "failure": None}]}

    assert fell_short(event) is False
    assert artifact_roles([event]) == {"p": "working"}


def test_two_reads_that_wrote_one_file_are_described_by_the_second():
    """Two reads in a turn can slug to the same filename. What is on disk is the second one's
    table, so the second one's role is the one that describes it."""
    events = [{"artifact": "p", "role": "working"}, {"artifact": "p", "role": "answer"}]

    assert artifact_roles(events) == {"p": "answer"}


def test_the_two_shortfall_lists_say_the_same_thing_as_the_transcript():
    """`store.js` decides the same question for `data_used` and must not drift from this one.

    Read out of both files rather than asserted from memory: the lists are the rule, and a rule
    stated twice in two languages is a rule that goes quietly out of step.
    """
    js = (REPO / "backend" / "sage" / "workbench" / "js" / "store.js").read_text()
    py = (REPO / "backend" / "sage" / "liveread" / "data_use.py").read_text()

    def listed(source: str, name: str) -> list[str]:
        m = re.search(rf"{name} = [\[(]([^\])]*)[\])]", source)
        assert m, f"{name} is no longer a literal list in that file"
        return sorted(re.findall(r"['\"]([a-z_]+)['\"]", m.group(1)))

    assert listed(js, "FELL_SHORT") == listed(py, "FELL_SHORT")
    assert listed(js, "DID_NOT_SETTLE") == listed(py, "DID_NOT_SETTLE")


# ---- the row the card is drawn from ------------------------------------------------------------

def test_an_artifact_row_records_the_role_it_was_given(tmp_path):
    store = ThreadStore(tmp_path)
    tid = store.create()["id"]

    working = store.record_artifact(tid, path=f"examples/{tid}/probe.table.json", role="working")
    answer = store.record_artifact(tid, path=f"examples/{tid}/answer.table.json", role="answer")
    absent = store.record_artifact(tid, path=f"examples/{tid}/chart.png")

    assert (working["role"], answer["role"], absent["role"]) == ("working", "answer", "answer")
    assert [a["role"] for a in store.read_artifacts(tid)] == ["working", "answer", "answer"]


def test_an_unrecognised_role_is_never_written_onto_a_row(tmp_path):
    """The same refusal as the join, at the one place that writes the field: a word this decision
    does not know is not a verdict, and the field a viewer reads must hold one of two values."""
    store = ThreadStore(tmp_path)
    tid = store.create()["id"]

    assert store.record_artifact(tid, path="examples/x/a.table.json", role="step")["role"] == "answer"


# ---- the whole length of it --------------------------------------------------------------------

class QueryingOpenCode(FakeOpenCode):
    """An agent turn that relays its token into one composed statement, as a real one does.

    The same trick as `ReadingOpenCode` next door, against `live_read_query` — the lane that writes
    a role — so the Artifact this produces is picked up by the same end-of-turn scan that finds one
    the agent wrote itself, and the join is exercised where it actually runs.
    """

    orch = None
    sql = ""
    step = None

    def send_prompt(self, session_id, text, model=None, agent=None, attachments=None, chat=False):
        m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", text)
        if m and self.orch is not None:
            args = {"token": m.group(1), "source": "Snowflake-Data-Warehouse", "sql": self.sql,
                    "title": "Probe"}
            if self.step is not None:
                args["step"] = self.step
            self.said = self.orch.live_read_call({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "live_read_query", "arguments": args},
            })["result"]["content"][0]["text"]
        return super().send_prompt(session_id, text, model, agent, attachments, chat)


class StatementWarehouse(Warehouse):
    """A store that answers whichever statement it was told to expect."""

    def run_statement(self, source, sql, *, limit, timeout_s=30.0):
        from sage.resources.provider import StatementRows
        self.asked.append(("statement", sql))
        return StatementRows(["N"], [[41234]], False)


CATALOGUE = "SELECT TABLE_NAME FROM DWH.INFORMATION_SCHEMA.TABLES"
COUNT = "SELECT COUNT(*) AS N FROM DWH.MARTS.GONG_CALLS"


@pytest.mark.parametrize("sql,step,expected", [
    (CATALOGUE, None, "working"),
    (COUNT, True, "working"),
    (COUNT, None, "answer"),
])
def test_a_chat_turns_card_carries_the_role_its_read_decided(tmp_path: Path, sql, step, expected):
    """The join at the Chat publish, driven end to end.

    This is the claim `artifact_roles` alone cannot make: that the verdict decided inside
    `liveread` survives the trip through an event, an end-of-turn scan that knows only paths, and
    `record_artifact`, and lands on the row the transcript reads.
    """
    orch, oc = _orch(tmp_path, StatementWarehouse())
    oc.__class__ = QueryingOpenCode
    oc.orch, oc.sql, oc.step = orch, sql, step
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1",
                                  "name": "Snowflake-Data-Warehouse"})

    list(orch.chat_stream(tid, "which customers use model monitoring"))

    store = ThreadStore(orch.project(start_preview=False).record.path)
    rows = [a for a in store.read_artifacts(tid) if a["path"].endswith("probe.table.json")]
    assert rows, f"the read wrote no card: {[a['path'] for a in store.read_artifacts(tid)]}"
    assert [a["role"] for a in rows] == [expected]


def test_a_build_turns_card_carries_the_role_too(tmp_path: Path):
    """The other publish site. The field is on the row and a Build transcript draws the same
    blocks, so a lane that recorded a verdict and then lost it on the way to the card would be a
    half-wired field nobody could tell from a decision."""
    orch, oc = _build_orch(tmp_path, StatementWarehouse())
    _bind(orch)
    tid = orch.create_thread()["id"]
    oc.__class__ = QueryingOpenCode
    oc.orch, oc.sql, oc.step = orch, CATALOGUE, None

    events = list(orch.build_stream("which tables hold calls", conversation=tid))

    done = [e for e in events if e.get("type") == "done"][-1]
    rows = [a for a in (done.get("artifacts") or []) if a["path"].endswith("probe.table.json")]
    assert rows, f"the read wrote no card: {[a['path'] for a in (done.get('artifacts') or [])]}"
    assert [a["role"] for a in rows] == ["working"]


# ---- the two doors on one tool -----------------------------------------------------------------

def test_both_doors_offer_the_step_flag():
    """`liveread/mcp.py` and `tools/live_read.ts` are two doors on one tool and they have drifted
    before — the file's own header says so. A flag on one door only is a flag the model can set and
    nothing reads."""
    query = next(t for t in mcp.TOOLS if t["name"] == "live_read_query")
    ts = (REPO / "backend" / "sage" / "liveread" / "tools" / "live_read.ts").read_text()

    assert query["inputSchema"]["properties"]["step"]["type"] == "boolean"
    assert "step" not in query["inputSchema"]["required"], "a step flag is never required"
    assert re.search(r"^\s*step: \{ type: \[\"boolean\", \"null\"\]", ts, re.MULTILINE), (
        "the custom tool door does not offer `step`")


def test_a_catalogue_read_that_hit_the_row_cap_draws(tmp_path):
    """A COLLISION between two of this ticket's own requirements, pinned rather than papered over.

    Constraint 2 says a read that fell short is never folded, and points at ADR-0062 §2, whose
    `unfinished` count is the shortfall. For `live_read_query` the ONLY thing that sets
    `unfinished` is truncation: `coverage` is built as `{..., "unfinished": 1 if truncated}` and
    nothing else on this lane can raise it. And `_statement` runs every statement at
    `result.CAP_ROWS`, which is 500.

    So a catalogue read returning more than 500 rows is `working` by the mechanical rule and
    `answer` by the shortfall rule, and the shortfall rule wins — its card draws. That is the
    behaviour this ticket asked for, and it bites the very reads it was written about: the
    investigation skill's stage two asks `INFORMATION_SCHEMA.COLUMNS` for a shortlist of tables,
    and six tables of a hundred columns is over the cap.

    Pinned here so the collision is a line in the suite rather than a surprise on a large
    warehouse. Changing it is a change to ADR-0063 and ADR-0062 together, not to this test.
    """
    turn, data, _journal = _query_turn(
        tmp_path, answer=FakeAnswer(["COLUMN_NAME"], [["ID"]], truncated=True))
    run.perform("live_read_query", {"token": "t", "source": "DWH",
                                    "sql": "SELECT COLUMN_NAME FROM DWH.INFORMATION_SCHEMA.COLUMNS",
                                    "step": True}, turn)
    event = data.events("turn1")[0]

    # The operation still says what it is. It is the join that declines to fold it.
    assert event["role"] == "working"
    assert event["coverage"]["unfinished"] == 1
    assert artifact_roles([event]) == {event["artifact"]: "answer"}
