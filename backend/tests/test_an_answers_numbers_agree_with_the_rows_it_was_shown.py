"""An answer's numbers agree with the rows the turn read, when the model WAS shown them (#747).

Demo rerun on `e10172cb`, prompt 1 (pipeline by stage and team, haiku): the table was right and the
prose was not. #729's check never ran, because it was keyed on a structure-only read and this read
was a GROUP BY of SUM and COUNT, which ADR-0058 hands to the model whole. Had it run, it would still
have passed "$9.0M" against a deal count of 9, and flagged a correct "$31.99M" because no single
row carries a total. Held here:

- a turn whose reads were all disclosed is checked too;
- a figure stated in millions is never matched against a whole count;
- a total, a group's total and a share of the total, worked from the disclosed rows, are carried.

The rows are a reconstruction: the ticket gives the totals ($31.99M, 103 deals, 66 with no amount,
FSI $10.0M, Life Sciences $14.0M, FSI $4.85M across its first three stages), not the rows.
"""

from __future__ import annotations

import re

from sage.liveread.held import HeldRead, unsupported_numbers
from sage.resources.provider import StatementRows
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn
from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call
from .test_chat_turn import IntentGateway, OkFeedback, _catalog

SOURCE = "Snowflake-Data-Warehouse"
SQL = ("SELECT STAGE, TEAM, SUM(AMOUNT) AS PIPELINE_USD, COUNT(*) AS DEALS, "
       "SUM(IFF(AMOUNT IS NULL, 1, 0)) AS NO_AMOUNT_DEALS FROM DWH.MARTS.OPEN_DEALS GROUP BY 1, 2")
COLUMNS = ["STAGE", "TEAM", "PIPELINE_USD", "DEALS", "NO_AMOUNT_DEALS"]
STAGES = ["Discovery", "Qualification", "Technical Validation", "Business Validation",
          "Negotiation"]
_BY_TEAM = {  # pipeline per stage, deals per stage, no-amount deals per stage
    "FSI": ([1_200_000, 1_650_000, 2_000_000, 2_900_000, 2_250_000], [5, 6, 7, 4, 3],
            [3, 4, 2, 1, 1]),
    "Life Sciences": ([2_100_000, 2_800_000, 3_300_000, 3_400_000, 2_400_000], [8, 9, 7, 6, 4],
                      [5, 4, 3, 2, 1]),
    "Manufacturing": ([500_000, 800_000, 1_100_000, 1_200_000, 600_000], [4, 5, 6, 3, 2],
                      [6, 5, 4, 3, 2]),
    "Public Sector": ([600_000, 900_000, 850_000, 790_000, 650_000], [6, 5, 5, 4, 4],
                      [6, 5, 4, 3, 2]),
}
ROWS = [[stage, team, pipeline[i], deals[i], blank[i]]
        for team, (pipeline, deals, blank) in _BY_TEAM.items() for i, stage in enumerate(STAGES)]
QUESTION = "What does open pipeline look like by stage and team?"

# Prompt 1's three sentences, as the ticket quotes them.
WRONG = ("FSI holds $9.0M across discovery, qualification, and technical validation. "
         "Life Sciences carries the most ($18M pipeline). "
         "115 deals across all teams lack pricing.")
RIGHT = ("Open pipeline is $31.99M across 103 deals, and 66 more have no amount. Life Sciences "
         "leads with $14.0M (about 44%), then FSI with $10.0M; Business Validation is the largest "
         "stage at $8.29M.")


def _read() -> HeldRead:
    return HeldRead(title="Pipeline by stage and team", slug="pipeline-by-stage-and-team",
                    columns=COLUMNS, rows=ROWS, disclosed=ROWS)


def test_the_replay_has_the_tickets_totals():
    assert sum(r[2] for r in ROWS) == 31_990_000 and sum(r[3] for r in ROWS) == 103
    assert sum(r[4] for r in ROWS) == 66 and len(ROWS) == 20


# --- the check itself -----------------------------------------------------------------------------

def test_prompt_ones_three_sentences_are_named():
    assert unsupported_numbers(WRONG, [_read()], QUESTION) == ["$9.0M", "$18M", "115"]


def test_totals_group_totals_and_shares_of_the_shown_rows_pass():
    assert unsupported_numbers(RIGHT, [_read()], QUESTION) == []


def test_a_figure_in_millions_is_not_a_count():
    """Life Sciences has 9 deals in Qualification, and Qualification has 18 with no amount. Neither
    is $9.0M or $18M."""
    read = HeldRead(title="Deals", slug="deals", columns=["TEAM", "DEALS"],
                    rows=[["FSI", 9], ["Life Sciences", 18]],
                    disclosed=[["FSI", 9], ["Life Sciences", 18]])
    assert unsupported_numbers("FSI has $9.0M and Life Sciences 18 million.", [read], "") == [
        "$9.0M", "18 million"]


def test_a_figure_already_in_millions_is_stated_in_millions():
    """`SUM(AMOUNT) / 1e6 AS PIPELINE_M` is a decimal, and "$9.0M" is how it is said."""
    read = HeldRead(title="Pipeline", slug="pipeline", columns=["TEAM", "PIPELINE_M"],
                    rows=[["FSI", 9.0], ["Life Sciences", "14.00"]],
                    disclosed=[["FSI", 9.0], ["Life Sciences", "14.00"]])
    assert unsupported_numbers("FSI has $9.0M and Life Sciences $14M.", [read], "") == []


# --- the turn ---------------------------------------------------------------------------------------

class _Store(Warehouse):
    def run_statement(self, source, sql, *, limit, timeout_s=30.0):
        return StatementRows(COLUMNS, ROWS, False)


class _ReadingOpenCode(FakeOpenCode):
    """Runs prompt 1's statement on the turn's first prompt, as a model relaying its token would."""

    orch = None

    def send_prompt(self, session_id, text, model=None, agent=None, attachments=None, chat=False):
        m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", text)
        if m and self.orch is not None:
            _call(self.orch, "live_read_query", {"token": m.group(1), "source": SOURCE, "sql": SQL,
                                                 "title": "Pipeline by stage and team"})
        return super().send_prompt(session_id, text, model, agent, attachments, chat)


def _answer(tmp_path, answers: list[str]):
    from sage.orchestrator.service import Orchestrator

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = _ReadingOpenCode(ws, [Turn(text=a) for a in answers])
    orch = Orchestrator(workspace_dir=ws, template=template,
                        gateway=IntentGateway({"label": "data_answer", "confidence": 0.92}),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=_Store())
    try:
        orch.project(start_preview=False)
        oc.orch = orch
        tid = orch.create_thread()["id"]
        orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1", "name": SOURCE})
        events = list(orch.chat_stream(tid, QUESTION))
        texts = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"]
        saved = [e["text"] for e in ThreadStore(orch.project(start_preview=False).record.path)
                 .read_history(tid) if e.get("type") == "agent" and e.get("kind") == "text"]
    finally:
        orch.shutdown()
    return oc, events, texts[-1] if texts else "", saved[-1] if saved else ""


def test_prose_that_disagrees_with_the_shown_rows_is_corrected(tmp_path):
    oc, events, shown, saved = _answer(tmp_path, [WRONG, RIGHT])

    assert len(oc.prompts) == 2, "one correction, on the turn's one recovery allowance"
    correction = oc.prompts[1]["text"]
    assert "$9.0M" in correction and "$18M" in correction and "115" in correction
    # The model WAS given these rows; telling it otherwise sends it to the wrong fix.
    assert "not given" not in correction
    assert shown == saved == RIGHT
    assert next(e for e in events if e.get("type") == "done")["ok"] is True


def test_prose_that_still_disagrees_after_the_correction_is_left_out(tmp_path):
    _oc, _events, shown, saved = _answer(tmp_path, [WRONG, "FSI holds $9.0M; 115 lack pricing."])

    assert shown == saved
    assert "9.0M" not in shown and "115" not in shown
    assert "wasn't given the values" not in shown
    assert "Pipeline by stage and team" in shown and "20 rows" in shown


def test_prose_that_agrees_with_the_shown_rows_passes_untouched(tmp_path):
    oc, _events, shown, saved = _answer(tmp_path, [RIGHT])

    assert len(oc.prompts) == 1
    assert shown == saved == RIGHT
