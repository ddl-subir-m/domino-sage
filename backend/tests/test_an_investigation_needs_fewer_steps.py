"""#606: the ARM investigation took 10-14 steps where 4 would do.

Measured 2026-09-29 on the fast run: two of its four exploratory reads counted the candidates the
judging call was about to count anyway, and two read columns for tables an earlier question in the
same session had already judged. A refused judging level also left the person to type the answer.
"""
from dataclasses import replace
from pathlib import Path

from sage.liveread import run
from sage.orchestrator.service import _elide_literals
from sage.workspace.threads import ThreadStore

from .test_a_card_decline_is_never_a_dead_end import _buttons, _node, _render, needs_node
from .test_an_investigation_is_told_the_tables_it_can_read import _investigating
from .test_analyze_text_judges_the_rows_a_statement_chose import CASES as ROWS
from .test_analyze_text_judges_the_rows_a_statement_chose import SOURCE, SQL, _args, _turn
from .test_chat_turn import Turn, _orch

SKILL = (Path(__file__).resolve().parents[2] / "template" / "skills" / "investigate-weak-signals"
         / "SKILL.md")


# ---- the judging level the model refused becomes a card ---------------------------------------

CHOICE = {"model": "mimo-v2.6-pro", "named": "minimal", "levels": ["none", "low", "high"]}


def test_a_refused_level_is_drawn_as_a_card_when_the_turn_ends(tmp_path):
    orch, oc = _orch(tmp_path, [Turn(text="That level needs choosing.")])
    tid = orch.create_thread()["id"]
    sent = oc.send_prompt

    def refused_mid_turn(*args, **kwargs):
        orch._effort_choices[tid] = dict(CHOICE)
        return sent(*args, **kwargs)

    oc.send_prompt = refused_mid_turn
    events = list(orch.chat_stream(tid, "judge the cases with mimo at minimal effort"))

    types = [e["type"] for e in events]
    (card,) = [e for e in events if e["type"] == "effort-choice"]
    assert types.index("effort-choice") < types.index("done")
    assert card["levels"] == ["none", "low", "high"]
    assert card["model"] == "mimo-v2.6-pro" and card["threadId"] == tid
    assert "“minimal”" in card["message"]
    history = ThreadStore(orch._chat_project().record.path).read_history(tid)
    kept = [{k: v for k, v in r.items() if k != "at"} for r in history
            if r.get("type") == "effort-choice"]
    assert kept == [card]
    assert tid not in orch._effort_choices


def test_a_choice_left_from_an_earlier_turn_draws_nothing(tmp_path):
    orch, _oc = _orch(tmp_path, [Turn(text="Answered.")])
    tid = orch.create_thread()["id"]
    orch._effort_choices[tid] = dict(CHOICE)

    events = list(orch.chat_stream(tid, "how many cases are there?"))

    assert "effort-choice" not in [e["type"] for e in events]


_CARD = {"type": "effort_choice", "message": "mimo-v2.6-pro can't judge text at “minimal”.",
         "model": "mimo-v2.6-pro", "levels": ["none", "low", "high"], "threadId": "t1"}


@needs_node
def test_the_card_has_one_button_per_level_and_each_sends_its_own():
    buttons = _buttons(_render({**_CARD, "live": True}))
    assert [b["text"] for b in buttons] == ["none", "low", "high"]
    assert [b["act"] for b in buttons] == [f"effort:mimo-v2.6-pro:{lv}" for lv in _CARD["levels"]]
    assert _buttons(_render({**_CARD, "live": False})) == []


@needs_node
def test_a_click_sends_the_level_as_the_persons_next_message():
    out = _node("card_decline_harness.mjs", {"mode": "effort", "model": "mimo-v2.6-pro",
                                             "level": "low"})
    assert out["prompts"] == ["Use mimo-v2.6-pro at reasoning effort low to analyze the text."]


@needs_node
def test_the_next_message_retires_the_card():
    out = _node("card_decline_harness.mjs", {"mode": "send", "messages": [
        {"id": "a1", "role": "assistant", "blocks": [
            {"type": "text", "value": "That level needs choosing."},
            {**_CARD, "live": True},
            {"type": "continue_offer", "message": "Continue?", "prompt": "p", "threadId": "t1",
             "live": True},
        ]},
    ]})
    assert out["otherLive"] == 1, "only the continue offer is left live"


def test_the_card_is_drawn_once(tmp_path):
    orch, _oc = _orch(tmp_path)
    tid = orch.create_thread()["id"]
    store = ThreadStore(orch._chat_project().record.path)
    orch._effort_choices[tid] = dict(CHOICE)

    assert [e["type"] for e in orch._chat_effort_choice_events(store, tid)] == ["effort-choice"]
    assert list(orch._chat_effort_choice_events(store, tid)) == []


# ---- a statement that judged text is remembered for the next question -------------------------

def test_a_statement_that_judged_records_is_remembered_and_one_that_did_not_is_not(tmp_path):
    remembered = []
    turn, *_ = _turn(tmp_path, ROWS, [], [])
    turn = replace(turn, remember_statement=lambda source, sql: remembered.append((source, sql)))

    run.perform("live_read_table", _args(), turn)
    assert remembered == [(SOURCE, SQL)]

    remembered.clear()
    empty, *_ = _turn(tmp_path / "empty", [], [], [])
    run.perform("live_read_table", _args(),
                replace(empty, remember_statement=lambda s, q: remembered.append((s, q))))
    assert remembered == []


def test_literals_are_elided_and_the_shape_is_kept():
    sql = ("SELECT c.CASE_ID, SUBSTR(c.BODY, 1, 1500) FROM DWH.MARTS.SFDC__CASE c "
           "JOIN DWH.MARTS.SFDC__ACCOUNT a ON a.ID = c.ACCOUNT_ID "
           "WHERE a.TYPE = 'Customer' AND c.BODY ILIKE '%it''s ARM%' "
           "AND REGEXP_LIKE(c.BODY, $$.*\\bARM\\b.*$$, 'is') AND c.ACCOUNT_ID IN (1234567, 7654321)")

    elided = _elide_literals(sql)

    for literal in ("Customer", "it''s ARM", "\\bARM\\b", "1234567", "7654321"):
        assert literal not in elided, literal
    for kept in ("DWH.MARTS.SFDC__CASE c", "a.ID = c.ACCOUNT_ID", "a.TYPE = '…'",
                 "SUBSTR(c.BODY, 1, 1500)", "IN (…, …)"):
        assert kept in elided, kept


def test_the_next_investigation_turn_starts_from_the_remembered_statement(tmp_path):
    orch, oc, tid = _investigating(tmp_path)
    remember = orch._live_read_turn_for(tid).remember_statement
    for sql in (SQL, "SELECT 1 FROM A", "SELECT 2 FROM B", "SELECT 3 FROM C", SQL):
        remember("Snowflake-Data-Warehouse", sql)
    orch.decide_thread_investigation(tid, "open")

    list(orch.chat_stream(tid, "Which accounts asked about GPUs?"))
    prompt = oc.prompts[-1]["text"]

    assert "Statements that judged text in Snowflake-Data-Warehouse earlier" in prompt
    assert _elide_literals(SQL) in prompt
    assert "'%ARM%'" not in prompt
    assert prompt.index(_elide_literals(SQL)) < prompt.index("SELECT 2 FROM B")
    assert "SELECT 1 FROM A" not in prompt
    assert prompt.count(_elide_literals(SQL)) == 1


# ---- the method no longer counts before it judges ---------------------------------------------

def test_the_prompt_and_the_skill_judge_without_counting_first(tmp_path):
    orch, oc, tid = _investigating(tmp_path)
    orch.decide_thread_investigation(tid, "open")
    list(orch.chat_stream(tid, "Which active customers asked for ARM support?"))
    prompt = oc.prompts[-1]["text"]
    skill = SKILL.read_text()

    for text in (prompt, skill):
        assert "count the candidates with live_read_query" not in text.lower()
        assert "Count candidates" not in text
    assert prompt.count("Do not count the candidates first") == 2
    assert "Do not count the candidates first" in skill
    assert "group_by" in skill.split("## 4.", 1)[1].split("## 5.", 1)[0]
