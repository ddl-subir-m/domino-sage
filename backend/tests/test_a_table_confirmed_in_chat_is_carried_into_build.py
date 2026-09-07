"""A table found and confirmed in Chat reaches the Built App's Binding (#188, ADR-0038).

The mode somebody happens to be standing in must not decide whether Sage will go and look. The
search that #183 gave Build runs in Chat too, and the click that answers the card writes the choice
where Chat can hold it — on the Thread's own context row, because Chat has no Built App and so no
Binding.

THE ALTERNATIVE THIS RULES OUT is searching in Chat but refusing to confirm there, sending the
person to Build to pick again. That makes them answer the same question twice across the crossing,
which is the friction the whole feature exists to remove.

The carry itself is deliberately NOT a new path: `binding_from_context` already reads a Thread row's
`scope`, and `_bind_from_handoff` already records a scoped Binding in one call. So the last test
below is the one that matters most — it proves the table crosses through machinery that was already
there rather than through a second, parallel record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.orchestrator import app as appmod
from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn

PROMPT = "chart me the daily gong calls from Snowflake"

_PLAN = (
    "A gong call dashboard.\n\n"
    "## Plan\n"
    "1. **Daily calls** — Count calls by day.\n\n"
    "## Open questions\n"
    "None — ready to build.\n"
)


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """CHAT, so the handoff classifier does not turn these turns into Build offers."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _orch(tmp: Path, turns: list[Turn] | None = None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns or [Turn(text="Here it is.")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def _gong_warehouse(orch: Orchestrator) -> None:
    """The live warehouse's shape in miniature: one subject spread over dbt layers."""
    orch._resources.tree["ds-dwh"] = {
        "DWH": {
            "MARTS": ["GONG__CALLS", "GONG__CALL_PARTICIPANTS", "FCT_USAGE_DAILY", "DIM_ACCOUNT"],
            "STAGING": ["STG_GONG__CALLS", "STG_SALESFORCE__OPPORTUNITY"],
            "REPORTING": ["V_ARR_WATERFALL"],
        },
    }
    orch._resources.columns["GONG__CALLS"] = [("CALL_ID", "TEXT"), ("STARTED_AT", "TIMESTAMP")]


def _client(orch: Orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app)


def _thread_with_source(orch: Orchestrator) -> str:
    """A Thread using `Snowflake-Data-Warehouse`, with no table chosen on it."""
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source",
        "name": "Snowflake-Data-Warehouse",
        "bindingKey": ["data_source", "ds-dwh"],
    })
    return tid


def _card(text: str) -> dict:
    """The one `table-candidates` frame out of an SSE body."""
    frames = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    cards = [f for f in frames if f.get("type") == "table-candidates"]
    assert len(cards) == 1, f"expected one candidate card, got {[f['type'] for f in frames]}"
    return cards[0]


def _row(orch: Orchestrator, thread_id: str) -> dict:
    items = ThreadStore(orch.project(start_preview=False).record.path
                        ).read_context(thread_id)["items"]
    return next(i for i in items if i.get("kind") == "data_source")


def _ask(client: TestClient, thread_id: str, prompt: str, **body) -> str:
    return client.post(f"/api/threads/{thread_id}/chat/stream",
                       json={"prompt": prompt, **body}).text


# ---- the card, in Chat ------------------------------------------------------------------------


def test_naming_a_data_source_in_chat_gets_the_same_candidates_build_would_give(
        tmp_path: Path, monkeypatch):
    """Story 17 of #179: the mode does not decide whether Sage helps.

    The turn stops before sage-chat is asked anything, which is the same rule the Build gate keeps
    — an assistant that could choose a table would be inferring a Binding (ADR-0010), and here it
    would be inferring one that a handoff then writes onto a published app.
    """
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)

    card = _card(_ask(client, tid, PROMPT))

    assert card["sourceId"] == "ds-dwh"
    assert card["threadId"] == tid
    assert card["matched"] > 0
    assert oc.prompts == [], "the assistant was asked a question about a table nobody had chosen"


def test_the_candidates_are_grouped_by_schema_and_all_of_them_stay_reachable(
        tmp_path: Path, monkeypatch):
    """The card is the same card, so it owes the same two things: the schema that tells
    `MARTS.GONG__CALLS` from `STAGING.STG_GONG__CALLS`, and every other table behind "show all"."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)

    card = _card(_ask(client, tid, PROMPT))

    assert sum(len(g["tables"]) for g in card["groups"]) == 5
    assert card["total"] == 7
    assert {g["schema"] for g in card["allGroups"]} == {"MARTS", "STAGING", "REPORTING"}
    shortlisted = {t for g in card["groups"] for t in g["tables"]}
    assert {"GONG__CALLS", "STG_GONG__CALLS"} <= shortlisted


def test_the_card_is_on_the_thread_so_reopening_the_conversation_still_shows_it(
        tmp_path: Path, monkeypatch):
    """A Chat turn's record is the Thread's history, and a question whose card vanished on reload
    would read as a turn that answered nothing."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)

    _ask(client, tid, PROMPT)

    history = client.get(f"/api/threads/{tid}/history").json()
    kinds = [e.get("type") for e in history]
    assert kinds.count("user") == 1
    assert "table-candidates" in kinds


def test_a_thread_that_already_chose_a_table_is_not_asked_about_it_again(
        tmp_path: Path, monkeypatch):
    """The gate is about an unanswered half of a question. Once it is answered it is gone — which
    is also what makes the replay below terminate."""
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {
        "kind": "data_source", "name": "Snowflake-Data-Warehouse",
        "bindingKey": ["data_source", "ds-dwh"],
        "scope": {"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"},
    })

    body = _ask(client, tid, PROMPT)

    assert "table-candidates" not in body
    assert len(oc.prompts) == 1


# ---- the click --------------------------------------------------------------------------------


def test_confirming_a_candidate_in_chat_records_the_table_on_the_thread(
        tmp_path: Path, monkeypatch):
    """Chat has no Built App, so the Thread's own row is the record. The columns come with it for
    the reason the panel's picker reads them: the row is what the turn prompt renders from."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, PROMPT))

    res = client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    assert res.status_code == 200, res.text
    row = _row(orch, tid)
    assert row["scope"] == {"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"}
    assert row["sourceName"] == "Snowflake-Data-Warehouse"
    assert row["columns"], "the chosen table's columns were not read for the turn prompt"


def test_the_chosen_table_is_looked_for_again_before_the_thread_records_it(
        tmp_path: Path, monkeypatch):
    """A stale list costs a click. A stale CHOICE reaches a published app through the handoff, and
    by then nobody is watching — so the one table being recorded is looked for again."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, PROMPT))
    orch._resources.tree["ds-dwh"]["DWH"]["MARTS"] = ["FCT_USAGE_DAILY"]

    res = client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    assert res.status_code == 502
    assert res.json()["error"] == (
        "GONG__CALLS is no longer in MARTS, so Sage did not record it. Ask again to search "
        "Snowflake-Data-Warehouse as it is now."
    )
    assert "scope" not in _row(orch, tid), "a dropped table reached the record"


def test_a_click_that_names_no_table_is_refused_rather_than_recorded_as_a_schema(
        tmp_path: Path, monkeypatch):
    """This path always lands on exactly one table, in Chat as in Build. "Somewhere in MARTS" does
    not answer "which table has the Gong data"."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)

    res = client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": "MARTS"})

    assert res.status_code == 400
    assert "scope" not in _row(orch, tid)


def test_a_data_source_this_conversation_is_not_using_has_no_table_to_record(
        tmp_path: Path, monkeypatch):
    """The Chat-side twin of the Binding door's refusal, and the same fact underneath it: a table is
    part of a dependency, and there is no dependency here to make it part of."""
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = orch.create_thread()["id"]

    res = client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    assert res.status_code == 404



def test_the_click_answers_the_row_the_card_asked_about_not_a_table_already_pinned(
        tmp_path: Path, monkeypatch):
    """A Thread can hold two rows for one store: a table somebody pinned in the panel, and the bare
    store itself. The search only ever offers the row with no table on it, so the click has to write
    that same row.

    Taking whichever row came first moved a table the person had already chosen and left the one the
    card was about still unanswered — so the next question drew the same card again, for good.
    """
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = orch.create_thread()["id"]
    pinned = orch.add_thread_context(tid, {
        "kind": "data_source", "name": "DIM_ACCOUNT",
        "bindingKey": ["data_source", "ds-dwh"],
        "scope": {"database": "DWH", "schema": "MARTS", "table": "DIM_ACCOUNT"},
    })
    bare = orch.add_thread_context(tid, {
        "kind": "data_source", "name": "Snowflake-Data-Warehouse",
        "bindingKey": ["data_source", "ds-dwh"],
    })
    _card(_ask(client, tid, PROMPT))

    client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    items = {i["id"]: i for i in client.get(f"/api/threads/{tid}/context").json()["items"]}
    assert items[bare["id"]]["scope"]["table"] == "GONG__CALLS"
    assert items[pinned["id"]]["scope"]["table"] == "DIM_ACCOUNT"
    # And the question the card asked is now answered, so asking again does not draw it a second time.
    assert "table-candidates" not in _ask(client, tid, PROMPT)


def test_columns_the_store_will_not_re_read_leave_with_the_table_they_described(
        tmp_path: Path, monkeypatch):
    """Columns are dropped before they are read again, not overwritten only when the read works.

    A row that already carries one table's columns and is moved to a table the store will not
    describe would otherwise keep the old names beside the new scope — and `_chat_context_line`
    hands those to the agent as the chosen table's schema, which is the wrong column names for the
    right table.
    """
    orch, _ = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, PROMPT))
    client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})
    assert _row(orch, tid)["columns"], "the first pick did not record any columns to go stale"

    # `GONG__CALL_PARTICIPANTS` is in the warehouse and has no columns recorded for it, which is
    # the shape of a store that lists its tables and will not describe one of them.
    client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALL_PARTICIPANTS"})

    row = _row(orch, tid)
    assert row["scope"]["table"] == "GONG__CALL_PARTICIPANTS"
    assert not row.get("columns"), "GONG__CALLS's columns stayed beside another table"

# ---- the replay -------------------------------------------------------------------------------


def test_the_answered_card_replays_the_question_without_asking_it_twice(
        tmp_path: Path, monkeypatch):
    """The person asked once. `skipTableGate` is the card being answered rather than skipped —
    without it the same question meets the same gate and gets the same card back — and the question
    is already on the Thread, so the replay must not write a second bubble under the card."""
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, PROMPT))
    client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    body = _ask(client, tid, PROMPT, skipTableGate=True)

    assert "table-candidates" not in body
    assert len(oc.prompts) == 1
    history = client.get(f"/api/threads/{tid}/history").json()
    assert [e.get("type") for e in history].count("user") == 1


def test_the_turn_prompt_names_the_chosen_table_after_the_click(tmp_path: Path, monkeypatch):
    """What the click bought. The row that used to tell sage-chat to go and list the tables itself
    now names the one the person picked, so the answer is written against it."""
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, PROMPT))
    client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    _ask(client, tid, PROMPT, skipTableGate=True)

    assert "DWH.MARTS.GONG__CALLS" in oc.prompts[-1]["text"]


# ---- the crossing -----------------------------------------------------------------------------


def test_a_handoff_carries_the_confirmed_table_into_the_binding_without_asking_again(
        tmp_path: Path, monkeypatch):
    """The whole point of #188, and deliberately no new path: the Thread row's `scope` is what
    `binding_from_context` already reads, and `_bind_from_handoff` already records a scoped Binding
    in one call. So this proves the carry rather than a second, parallel record."""
    orch, _ = _orch(tmp_path, [Turn(text="Here it is."), Turn(text=_PLAN)])
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _card(_ask(client, tid, PROMPT))
    client.post(f"/api/threads/{tid}/context/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})
    _ask(client, tid, PROMPT, skipTableGate=True)
    orch.draft_handoff_plan(tid)

    orch.confirm_handoff(tid, {"resources": True, "artifacts": True, "transcript": False})

    bindings = orch.project(start_preview=False).workspace.read_bindings()
    bound = next(b for b in bindings if b.get("id") == "ds-dwh")
    assert bound["kind"] == KIND_DATA_SOURCE
    assert (bound["database"], bound["schema"], bound["table"]) == ("DWH", "MARTS", "GONG__CALLS")
    # The source's own name, not the table's: it is what a published app calls `get_datasource`
    # with, and the carry must not undo what `_bind_from_handoff` exists to get right.
    assert bound["name"] == "Snowflake-Data-Warehouse"


def test_a_thread_with_no_confirmed_table_hands_off_exactly_as_it_does_today(
        tmp_path: Path, monkeypatch):
    """The unanswered case is untouched: the Binding is still recorded, still with no table, and the
    Built App still asks the question from its own panel."""
    orch, _ = _orch(tmp_path, [Turn(text="Here it is."), Turn(text=_PLAN)])
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)
    _ask(client, tid, "tell me what you can see")
    orch.draft_handoff_plan(tid)

    orch.confirm_handoff(tid, {"resources": True, "artifacts": True, "transcript": False})

    bound = next(b for b in orch.project(start_preview=False).workspace.read_bindings()
                 if b.get("id") == "ds-dwh")
    assert not bound.get("table")
    assert bound["name"] == "Snowflake-Data-Warehouse"
