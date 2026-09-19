"""ADR-0061 — the turn prompt names the sources earlier turns in this Thread already read.

Measured live (#443, #442): an accepted investigation ran 10 turns and 53 live reads, turns 4-10
re-reported byte-identical numbers, and the answer was on screen the whole time in a card turn 2
published. Three instruction layers told the model to keep its measurements in `findings.md` and
the file was a 404 after ten turns. Instruction was not the missing thing; the RECORD was, and
Sage already keeps it twice.

So the block below renders what is already on disk — `data_used` events in the Thread's history —
and writes nothing new for it. The rows here come through the real producers for that reason:
`run.perform` composes the event (`liveread/run.py`) and `DataUse.record` stamps the `turn_id` and
writes the row. A hand-typed `{"type": "data_used", ...}` would pin this test to a shape the
producer could stop emitting, and it is `turn_id` in particular that a fixture gets wrong for
free — it is stamped by `record`, not by the caller that builds the event.

Two properties are load-bearing and neither is obvious from the block on screen:

- it is gated on THERE BEING READS, never on `investigating`. A flag gate would decide the
  audience for this by a piece of state that can be mis-set, which is #443's own defect one level
  up: the turns that need it most would be the ones the flag excludes;
- it is bounded at a COMPLETE clear only. ADR-0055 deletes `findings.md` on that same event, and a
  summary-scoped clear trims talk — a measurement log is not talk, and neither is a read.
"""
from __future__ import annotations

from pathlib import Path

from sage.liveread import run
from sage.liveread.data_use import DataUse
from sage.orchestrator import recall

from .test_a_chat_turn_works_a_number_out_in_sql import FakeAnswer, turn_for
from .test_chat_turn import _orch

TID = "thr_r1"

# The heading, spelled once. Every assertion about presence and absence goes through it, so a
# rename moves this test with the product instead of leaving it green against dead words.
BLOCK = "Already read in this Thread:"


def _read(rows: list[dict], tmp_path: Path, *, source: str, turn_id: str, sql: str) -> dict:
    """One live read, recorded the way a real turn records it.

    `run.perform` builds the event — including the artifact path, which is `result.record`'s to
    name — and `DataUse.record` is what stamps `turn_id` and persists the row. `rows.append` stands
    in for the ThreadStore the orchestrator passes as `persist`.
    """
    turn, recorded = turn_for(tmp_path, keep_rows=True,
                              answer=FakeAnswer(["N"], [[41234]]),
                              bound={"datasource": (source,)},
                              source_for=lambda _n: object(),
                              binding_for={("datasource", source): "bnd_1"})
    said = run.perform("live_read_query", {"source": source, "sql": sql}, turn)
    assert recorded, f"the read did not happen, so there is no event to record: {said}"
    event, reply = recorded[-1]
    DataUse().record(event, reply, rows.append, turn_id)
    return rows[-1]


def _prompt(orch, history: list[dict] | None = None) -> str:
    return orch._chat_prompt(TID, "and now the accounts?", {"items": []}, history=history)


# ---- present, absent, and what each line says --------------------------------------------------


def test_a_first_turn_is_told_nothing_about_reads(tmp_path: Path):
    """Self-limiting, and this is the half that makes it so: a Thread that has read nothing pays
    nothing. The same reason `_findings_note` beside it is conditional on the file."""
    orch, _ = _orch(tmp_path)

    assert BLOCK not in _prompt(orch)
    assert BLOCK not in _prompt(orch, history=[{"type": "user", "text": "how many accounts?"}])


def test_each_source_gets_one_line_naming_its_turns_and_its_latest_result(tmp_path: Path):
    """The unit is the SOURCE, not the read. Turns 4-10 of the live Thread re-read the same
    shortlist, so a list that grew a line per read would bury the fact under its own repetition."""
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 1")
    _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t2", sql="SELECT 2")
    last = _read(rows, tmp_path, source="GONG__ACCOUNT_TRACKER_DAILY", turn_id="t2",
                 sql="SELECT 3")

    prompt = _prompt(orch, history=rows)

    assert BLOCK in prompt
    lines = [ln for ln in prompt.splitlines() if ln.startswith("- SFDC__ACCOUNT")]
    assert len(lines) == 1, f"one line per source, got {lines}"
    assert "read on 2 turns" in lines[0], lines[0]
    assert "- GONG__ACCOUNT_TRACKER_DAILY — read on 1 turn," in prompt
    # The path the model can open, taken from the event rather than spelled here, so this follows
    # `result.record`'s naming instead of agreeing with a copy of it.
    assert last["dataUsed"][0]["artifact"] in prompt


def test_one_turns_many_model_calls_are_not_counted_as_turns(tmp_path: Path):
    """`DataUse.observe` re-persists an operation every time a model call touches it, so one read
    is many rows. Counting rows would report the live Thread's 25 model calls as turns."""
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    row = _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 1")
    rows.append({"type": "data_used", "dataUsed": [dict(row["dataUsed"][0])]})
    rows.append({"type": "data_used", "dataUsed": [dict(row["dataUsed"][0])]})

    assert "- SFDC__ACCOUNT — read on 1 turn," in _prompt(orch, history=rows)


def test_one_turns_several_reads_of_one_source_are_not_counted_as_turns(tmp_path: Path):
    """A turn can read the same source twice, and each read is its own operation with its own id.

    Separate from the test above, because `turn_id` and `operation_id` agree whenever a turn reads
    once — so a Thread of one-read turns cannot tell the two counts apart, and the count that is
    wanted is turns.
    """
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 1")
    _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 2")

    assert "- SFDC__ACCOUNT — read on 1 turn," in _prompt(orch, history=rows)


def test_a_re_persisted_old_read_does_not_become_the_newest(tmp_path: Path):
    """Recency is when a source was READ, not when it was last sent to a model.

    This is the dedupe's real job. `turn_id` already absorbs the re-persists for the COUNT, so the
    only thing that notices a missing dedupe is the ordering — and ordering is what decides which
    sources survive the cap.
    """
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    old = _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 1")
    _read(rows, tmp_path, source="GONG__CALLS", turn_id="t2", sql="SELECT 2")
    # Turn two's model call touched turn one's operation, so `observe` saves it again.
    rows.append({"type": "data_used", "dataUsed": [dict(old["dataUsed"][0])]})

    named = [ln for ln in _prompt(orch, history=rows).splitlines() if ln.startswith("- ")]

    assert named[0].startswith("- GONG__CALLS "), named


def test_the_line_does_not_carry_the_handoffs_column_list(tmp_path: Path):
    """`data_use_summaries` walks the same rows and is the wrong formatter for this reader:
    `_data_use_line` builds up to twelve column names into every line for the Build handoff."""
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 1")

    block = [ln for ln in _prompt(orch, history=rows).splitlines() if ln.startswith("- SFDC__")]

    assert block and "Columns:" not in block[0], block


def test_the_block_is_capped_and_newest_first(tmp_path: Path):
    """Twenty lines, and the recent ones survive the cap: the sources a stalled investigation keeps
    returning to are the ones it touched last."""
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    for i in range(25):
        _read(rows, tmp_path, source=f"TABLE_{i:02d}", turn_id=f"t{i}", sql=f"SELECT {i}")

    named = [ln for ln in _prompt(orch, history=rows).splitlines() if ln.startswith("- TABLE_")]

    assert len(named) == 20
    assert named[0].startswith("- TABLE_24 "), named[0]
    assert not any(ln.startswith("- TABLE_04 ") for ln in named), "the oldest five were dropped"


# ---- the clear -------------------------------------------------------------------------------


def test_a_summary_scoped_clear_leaves_the_read_log_whole(tmp_path: Path):
    """The softer rung trims talk and seeds what was said. A read is not talk, and throwing the
    measurement log away there would lose work nobody asked to lose (ADR-0055's own reasoning)."""
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 1")
    rows.append({"type": recall.CLEARED, "scope": recall.SUMMARY})

    assert "- SFDC__ACCOUNT " in _prompt(orch, history=rows)


def test_a_complete_clear_takes_the_read_log(tmp_path: Path):
    """The person said START OVER and it has to mean it — the same event that deletes
    `findings.md`."""
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 1")
    rows.append({"type": recall.CLEARED, "scope": recall.EMPTY})

    assert BLOCK not in _prompt(orch, history=rows)


def test_a_read_after_a_complete_clear_starts_the_log_again(tmp_path: Path):
    """The bound is the clear, not the Thread. What was read since is still what was read."""
    orch, _ = _orch(tmp_path)
    rows: list[dict] = []
    _read(rows, tmp_path, source="SFDC__ACCOUNT", turn_id="t1", sql="SELECT 1")
    rows.append({"type": recall.CLEARED, "scope": recall.EMPTY})
    _read(rows, tmp_path, source="GONG__CALLS", turn_id="t2", sql="SELECT 2")

    prompt = _prompt(orch, history=rows)

    assert "- GONG__CALLS " in prompt
    assert "SFDC__ACCOUNT" not in prompt


# ---- the Artifact sentence beside it -----------------------------------------------------------


def test_the_artifact_sentence_says_reading_is_free_and_still_forbids_changing(tmp_path: Path):
    """The permission that was missing, not a weakening of the prohibition.

    The measured failure is a turn that was TOLD a card existed and never opened it — the answer
    was in `examples/<threadId>/` for eight turns. The sentence said "change one only if asked",
    which is about writing and reads as leave alone. Both halves are pinned because dropping the
    prohibition while adding the permission is the plausible way to break this.
    """
    orch, _ = _orch(tmp_path)
    root = orch.project(start_preview=False).record.path
    card = root / "examples" / TID / "accounts.table.json"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text('{"columns": ["N"], "rows": [[1]]}')

    prompt = orch._chat_prompt(
        TID, "and now the accounts?", {"items": []},
        artifacts=[{"path": f"examples/{TID}/accounts.table.json", "title": "accounts",
                    "kind": "table"}])

    assert "reading one is always free" in prompt
    assert "change one only if asked" in prompt
    assert f"examples/{TID}/accounts.table.json" in prompt
