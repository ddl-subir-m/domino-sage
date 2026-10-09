"""An answer states a number only from a read whose values were disclosed to the model (#729).

Demo rerun on `e9bfaea`: three of six Chat answers stated figures no read produced, and every one
of them sat on a read disclosed as **Structure only** (`disclosed_rows: 0`). Where the values WERE
disclosed (prompt 4), the text matched the table exactly. So the model invents when it cannot see,
and nothing stopped it. Held here:

- the tool result for a structure-only read says plainly that no number may come from it;
- a bounded turn whose answer states a number no disclosed read carries is corrected once
  (the repair path), and if the correction still states one, the text is replaced by what was
  read and its shape (the refuse path);
- prompt 4's shape — disclosed values, matching text — goes through untouched.
"""

from __future__ import annotations

import re

import pytest

from sage.liveread import run
from sage.liveread.held import HeldRead, unsupported_numbers
from sage.resources.provider import StatementRows
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn
from .test_a_live_read_reaches_the_person_end_to_end import Warehouse, _call
from .test_chat_turn import IntentGateway, OkFeedback, _catalog

SOURCE = "Snowflake-Data-Warehouse"
# Stored values in both columns: ADR-0058 keeps them on the card, so this read is structure only.
WITHHELD_SQL = "SELECT COMPETITOR, CALLS FROM DWH.MARTS.COMPETITOR_CALLS"
# A group-by label and a count: ADR-0058 hands both to the model.
DISCLOSED_SQL = "SELECT ACCOUNT, COUNT(*) AS WAU FROM DWH.MARTS.EVENTS GROUP BY 1"
CALLS = [["Build In-House", 47], ["Lakehouse", 29], ["Cloud ML", 25], ["Low-Code", 14]]


def _withheld(rows=CALLS) -> HeldRead:
    return HeldRead(title="Competitor calls", slug="competitor-calls",
                    columns=["COMPETITOR", "CALLS"], rows=rows, disclosed=[])


# --- the check itself -----------------------------------------------------------------------------

def test_an_invented_figure_over_a_structure_only_read_is_named():
    said = "Build In-House led with 118 calls and a 60% win rate; Lakehouse had 94."
    assert unsupported_numbers(said, [_withheld()], "Which competitors come up most?") == [
        "118", "60%", "94"]


def test_a_structure_only_read_can_still_be_described_by_its_shape():
    said = "I read 4 rows across 2 columns: each competitor and its call count. See the table."
    assert unsupported_numbers(said, [_withheld()], "Which competitors come up most?") == []


def test_numbers_a_disclosed_read_carries_pass_at_the_precision_they_are_stated():
    disclosed = HeldRead(title="WAU", slug="wau", columns=["ACCOUNT", "WAU", "CHANGE"],
                         rows=[["gsk.com", 1446, -0.1247]],
                         disclosed=[["gsk.com", 1446, -0.1247]])
    said = "gsk.com had 1,446 weekly users (about 1.4K), down 12.5% — roughly 12%."
    assert unsupported_numbers(said, [disclosed, _withheld()], "WAU for gsk?") == []


def test_dates_years_list_markers_and_the_persons_own_numbers_are_not_claims():
    said = ("1. Since 2026-07-01, over the last 30 days\n"
            "2. Through Oct 9 in 2026, the top 3 are on the table.")
    assert unsupported_numbers(said, [_withheld()], "Top 3 for the last 30 days?") == []


# --- what the model is told -------------------------------------------------------------------

def _turn(tmp_path, rows, held: list) -> run.Turn:
    return run.Turn(
        thread_id="thr_a", examples_dir=tmp_path / "examples" / "thr_a",
        bound={"datasource": (SOURCE,)},
        source_for=lambda n: type("S", (), {"connector_type": "SnowflakeConfig", "name": SOURCE})(),
        run_statement=lambda source, sql, *, limit: rows,
        hold=held.append,
    )


def test_a_structure_only_result_tells_the_model_it_has_no_numbers_to_state(tmp_path):
    held: list = []
    said = run.perform("live_read_query", {"source": SOURCE, "sql": WITHHELD_SQL,
                                           "title": "Competitor calls"},
                       _turn(tmp_path, StatementRows(["COMPETITOR", "CALLS"], CALLS, False), held))

    assert "You were not given this result's values" in said
    assert "do not state, estimate or work out any number from them" in said
    assert "47" not in said
    # The rows are held for this turn — for a chart Sage draws, never for the model.
    [read] = held
    assert read.title == "Competitor calls" and read.rows == CALLS and read.disclosed == []


def test_a_disclosed_result_is_held_with_what_the_model_was_given(tmp_path):
    held: list = []
    rows = StatementRows(["ACCOUNT", "WAU"], [["gsk.com", 1446]], False)
    said = run.perform("live_read_query", {"source": SOURCE, "sql": DISCLOSED_SQL, "title": "WAU"},
                       _turn(tmp_path, rows, held))

    assert "1446" in said
    assert [r.disclosed for r in held] == [[["gsk.com", 1446]]]


# --- the turn ---------------------------------------------------------------------------------

class _Store(Warehouse):
    def run_statement(self, source, sql, *, limit, timeout_s=30.0):
        if sql == DISCLOSED_SQL:
            return StatementRows(["ACCOUNT", "WAU"], [["gsk.com", 1446]], False)
        return StatementRows(["COMPETITOR", "CALLS"], CALLS, False)


class _ReadingOpenCode(FakeOpenCode):
    """Runs its statements on the turn's first prompt, the way a model relaying its token would."""

    orch = None
    sqls: tuple[str, ...] = (WITHHELD_SQL,)

    def send_prompt(self, session_id, text, model=None, agent=None, attachments=None, chat=False):
        m = re.search(r"Read token: (lrt_[A-Za-z0-9_-]+)", text)
        if m and self.orch is not None:
            for sql in self.sqls:
                _call(self.orch, "live_read_query", {
                    "token": m.group(1), "source": SOURCE, "sql": sql,
                    "title": "Competitor calls" if sql == WITHHELD_SQL else "Weekly users"})
        return super().send_prompt(session_id, text, model, agent, attachments, chat)


def _answer(tmp_path, answers: list[str], *, sqls: tuple[str, ...] = (WITHHELD_SQL,),
            question: str = "Which competitors come up most on calls?"):
    from sage.orchestrator.service import Orchestrator

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = _ReadingOpenCode(ws, [Turn(text=a) for a in answers])
    oc.sqls = sqls
    orch = Orchestrator(workspace_dir=ws, template=template,
                        gateway=IntentGateway({"label": "data_answer", "confidence": 0.92}),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=_Store())
    orch.project(start_preview=False)
    oc.orch = orch
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "id": "ds1", "name": SOURCE})
    events = list(orch.chat_stream(tid, question))
    texts = [e["text"] for e in events if e.get("type") == "agent" and e.get("kind") == "text"]
    saved = [e["text"] for e in ThreadStore(orch.project(start_preview=False).record.path)
             .read_history(tid) if e.get("type") == "agent" and e.get("kind") == "text"]
    return oc, events, texts[-1] if texts else "", saved[-1] if saved else ""


def test_a_stated_number_no_disclosed_read_carries_is_repaired(tmp_path):
    invented = "Build In-House leads with 118 calls, ahead of Lakehouse at 94."
    honest = "Four competitors come up on calls; the counts are on the table."
    oc, events, shown, saved = _answer(tmp_path, [invented, honest])

    assert len(oc.prompts) == 2, "one correction, on the turn's one recovery allowance"
    correction = oc.prompts[1]["text"]
    assert "118" in correction and "94" in correction
    assert "not given" in correction
    assert shown == saved == honest
    assert next(e for e in events if e.get("type") == "done")["ok"] is True


def test_a_stated_number_that_survives_the_correction_is_refused(tmp_path):
    invented = "Build In-House leads with 118 calls, ahead of Lakehouse at 94."
    oc, _events, shown, saved = _answer(tmp_path, [invented, "Still 118 calls for Build In-House."])

    assert len(oc.prompts) == 2
    assert shown == saved
    assert "118" not in shown and "94" not in shown
    assert "wasn't given the values" in shown
    # What may be said about a structure-only read: what was read, its shape and its row count.
    assert "Competitor calls" in shown and "4 rows" in shown and "COMPETITOR, CALLS" in shown


def test_disclosed_values_and_matching_text_pass_untouched(tmp_path):
    """Prompt 4's shape. No correction is sent and the words are the model's own — on a turn
    that ALSO had a read withheld, so the check runs and the disclosed number has to pass it."""
    said = "gsk.com had 1,446 weekly active users; the competitor table has 4 rows."
    oc, _events, shown, saved = _answer(tmp_path, [said], sqls=(DISCLOSED_SQL, WITHHELD_SQL),
                                        question="What is gsk's WAU?")

    assert len(oc.prompts) == 1
    assert shown == saved == said


@pytest.mark.parametrize("label", ["data_answer"])
def test_a_turn_with_no_read_is_not_checked(tmp_path, label):
    """The rule is about reads: a turn that read nothing has no withheld result to invent from."""
    from sage.orchestrator.service import Orchestrator

    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, [Turn(text="Compound interest at 5% doubles money in about 14 years.")])
    orch = Orchestrator(workspace_dir=ws, template=template,
                        gateway=IntentGateway({"label": label, "confidence": 0.92}),
                        catalog=_catalog(), project_id="Sage", feedback=OkFeedback(),
                        opencode_client=oc, resources=_Store())
    tid = orch.create_thread()["id"]
    events = list(orch.chat_stream(tid, "How fast does compound interest double money?"))

    assert len(oc.prompts) == 1
    assert any("14 years" in (e.get("text") or "") for e in events)
