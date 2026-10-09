"""A read that a later read in the same turn replaced is working, not an answer table (#732).

Demo rerun on `e9bfaea`, prompt 1: the first read used the wrong period (FY2026 Q4, one row), the
second read the quarters asked for (56 rows), and both carried `role: "answer"`, so a stale table
for a period nobody asked about sat under the real one.

The rule, from what Sage knows: an earlier answer read is replaced when a LATER answer read in the
same turn, from the same source, is one the answer is drawn from — Sage charted it, the answer
names its card, or the answer states a number only it carries — and the earlier one is none of
those. Anything less certain shows both (ADR-0063: when unsure, show).
"""

from __future__ import annotations

import re

from sage.liveread.held import HeldRead, replaced
from sage.resources.provider import StatementRows
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn
from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call
from .test_chat_turn import IntentGateway, OkFeedback, _catalog

SOURCE = "Snowflake-Data-Warehouse"
WRONG = HeldRead(title="Open pipeline FY2026 Q4", slug="open-pipeline-fy2026-q4",
                 columns=["STAGE", "OPEN_AMOUNT"], rows=[["Negotiate", 812345]],
                 disclosed=[["Negotiate", 812345]])
RIGHT = HeldRead(title="Open pipeline FY2027 Q3 and Q4", slug="open-pipeline-fy2027-q3-and-q4",
                 columns=["STAGE", "OPEN_AMOUNT"],
                 rows=[["Negotiate", 1250000], ["Proposal", 640000]],
                 disclosed=[["Negotiate", 1250000], ["Proposal", 640000]])


def _path(read: HeldRead) -> str:
    return f"examples/thr_a/{read.slug}.table.json"


def _events(*pairs) -> list[dict]:
    return [{"artifact": _path(read), "source": source, "role": "answer"} for read, source in pairs]


def _roles(events) -> dict[str, str]:
    return {e["artifact"]: "answer" for e in events}


def _replaced(text, *, charted=frozenset(), sources=(SOURCE, SOURCE), reads=(WRONG, RIGHT)):
    events = _events(*zip(reads, sources, strict=True))
    return replaced(events, _roles(events), list(reads), set(charted), text,
                    "Chart open pipeline by stage for this quarter and next")


# --- the rule -------------------------------------------------------------------------------------

def test_the_later_read_the_chart_is_drawn_from_replaces_the_earlier_one():
    assert _replaced("Negotiate holds most of the open pipeline.",
                     charted={RIGHT.slug}) == {_path(WRONG)}


def test_the_later_read_whose_numbers_the_answer_states_replaces_the_earlier_one():
    assert _replaced("Negotiate holds $1.25M, Proposal $640K.") == {_path(WRONG)}


def test_two_reads_the_answer_both_draws_on_are_both_answers():
    assert _replaced("Negotiate went from 812,345 last quarter to $1.25M.") == set()


def test_an_earlier_read_the_answer_names_is_kept():
    said = f"Negotiate holds $1.25M; last quarter is in {WRONG.slug}.table.json."
    assert _replaced(said) == set()


def test_an_earlier_read_that_was_charted_is_kept():
    assert _replaced("Negotiate holds $1.25M.", charted={WRONG.slug, RIGHT.slug}) == set()


def test_reads_from_different_sources_never_replace_each_other():
    assert _replaced("Negotiate holds $1.25M.", charted={RIGHT.slug},
                     sources=(SOURCE, "Salesforce")) == set()


def test_when_the_answer_draws_on_neither_both_are_shown():
    assert _replaced("Here is the open pipeline.") == set()


def test_a_later_read_never_replaces_by_being_unused():
    """Only the EARLIER read is demoted: a read the answer did not use that came after is not
    a replaced read, it is a read the rule knows nothing about."""
    assert _replaced("Negotiate holds 812,345.") == set()


def test_a_number_both_reads_carry_proves_neither():
    shared = HeldRead(title="Earlier", slug="earlier", columns=["STAGE", "N"],
                      rows=[["Negotiate", 640000]], disclosed=[["Negotiate", 640000]])
    assert _replaced("Proposal holds $640K.", reads=(shared, RIGHT)) == set()


def test_a_read_sage_did_not_hold_is_never_demoted():
    events = _events((WRONG, SOURCE), (RIGHT, SOURCE))
    assert replaced(events, _roles(events), [RIGHT], {RIGHT.slug}, "Negotiate holds $1.25M.",
                    "") == set()


def test_a_working_read_is_not_counted_as_the_replacement():
    events = _events((WRONG, SOURCE), (RIGHT, SOURCE))
    roles = {**_roles(events), _path(RIGHT): "working"}
    assert replaced(events, roles, [WRONG, RIGHT], {RIGHT.slug}, "", "") == set()


# --- the turn -------------------------------------------------------------------------------------

WRONG_SQL = ("SELECT STAGE, SUM(AMOUNT) AS OPEN_AMOUNT FROM DWH.MARTS.PIPELINE "
             "WHERE FISCAL_QUARTER = 'FY2026-Q4' GROUP BY 1")
RIGHT_SQL = ("SELECT STAGE, SUM(AMOUNT) AS OPEN_AMOUNT FROM DWH.MARTS.PIPELINE "
             "WHERE FISCAL_QUARTER IN ('FY2027-Q3', 'FY2027-Q4') GROUP BY 1")


class _Store(Warehouse):
    def run_statement(self, source, sql, *, limit, timeout_s=30.0):
        if sql == WRONG_SQL:
            return StatementRows(["STAGE", "OPEN_AMOUNT"], WRONG.rows, False)
        return StatementRows(["STAGE", "OPEN_AMOUNT"], RIGHT.rows, False)


class _ReadingOpenCode(FakeOpenCode):
    """Reads the wrong period, then the right one, and charts the right one when told to."""

    orch = None
    chart = False

    def send_prompt(self, session_id, text, model=None, agent=None, attachments=None, chat=False):
        m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", text)
        if m and self.orch is not None:
            for sql, read in ((WRONG_SQL, WRONG), (RIGHT_SQL, RIGHT)):
                _call(self.orch, "live_read_query", {"token": m.group(1), "source": SOURCE,
                                                     "sql": sql, "title": read.title})
            if self.chart:
                tid = re.search(r"Thread id: (\S+)", text).group(1)
                self.orch.write_chat_artifact({
                    "thread_id": tid, "path": f"examples/{tid}/pipeline.png",
                    "table": RIGHT.title, "x": "STAGE", "y": ["OPEN_AMOUNT"]})
        return super().send_prompt(session_id, text, model, agent, attachments, chat)


def _tables(tmp_path, answer: str, *, label="data_answer", chart=False) -> dict[str, str]:
    from sage.orchestrator.service import Orchestrator

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = _ReadingOpenCode(ws, [Turn(text=answer)])
    oc.chart = chart
    orch = Orchestrator(workspace_dir=ws, template=template,
                        gateway=IntentGateway({"label": label, "confidence": 0.92}),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=_Store())
    try:
        orch.project(start_preview=False)
        oc.orch = orch
        tid = orch.create_thread()["id"]
        orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1", "name": SOURCE})
        list(orch.chat_stream(tid, "Open pipeline by stage for this quarter and next"))
        artifacts = ThreadStore(orch.project(start_preview=False).record.path).read_artifacts(tid)
    finally:
        orch.shutdown()  # a chat turn arms the idle-save timer; this cancels it
    return {a["name"]: a["role"] for a in artifacts}


def test_a_turn_whose_first_read_was_replaced_shows_only_the_second(tmp_path):
    roles = _tables(tmp_path, "Negotiate holds $1.25M of open pipeline and Proposal $640K.")
    assert roles == {f"{WRONG.slug}.table.json": "working",
                     f"{RIGHT.slug}.table.json": "answer"}


def test_a_turn_that_charts_the_second_read_shows_only_the_second(tmp_path):
    roles = _tables(tmp_path, "Negotiate holds most of the open pipeline.",
                    label="data_artifact", chart=True)
    assert roles[f"{WRONG.slug}.table.json"] == "working"
    assert roles["pipeline.png"] == "answer"


def test_a_turn_whose_answer_uses_both_reads_shows_both(tmp_path):
    roles = _tables(tmp_path, "Negotiate went from 812,345 in FY2026 Q4 to $1.25M now.")
    assert roles == {f"{WRONG.slug}.table.json": "answer",
                     f"{RIGHT.slug}.table.json": "answer"}
