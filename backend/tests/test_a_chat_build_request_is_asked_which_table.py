"""A Chat request that asks for an app AND names a Data Source is asked which table (#204).

The two gates were each correct on their own and left a crack between them. Chat's table gate sat
BELOW the explicit-handoff short-circuit on the deliberate ground that a request which is really
"build me an app" belongs in Build and should not spend seconds reading a warehouse first — which
assumed Build would ask instead. Build never gets the chance: the handoff crosses as
`kind: "approve"`, and `_approve_locked` returns above the whole gate block. So each surface
deferred to the other, `confirm_handoff` wrote an unscoped Binding into a brand-new app, and the
app was built against a table name the model invented.

Live on cloud-dogfood: "build me a dashboard from the gong table in @Snowflake-Data-Warehouse"
became `FROM GONG`, and every query failed. The fingerprint was a `kind: "chat"` turn of 3ms with
no model call — the regex short-circuit, and no gate.

So the ordering flips: the table gate runs FIRST and the nudge waits. The tests below pin both
halves of that, because flipping it is only half safe on its own — the nudge must still arrive on
the turn the click buys, or a build request that names a store would answer in Chat forever.

The second group pins the cost. The gate is only allowed to be first because it is free when it
declines: it reads the Thread's own rows, and a request naming no unscoped Data Source reaches no
warehouse at all. A gate that walked a catalog before deciding could not sit above a
short-circuit that exists to be fast.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn

# The live sentence, from the issue. `looks_like_build_request` matches it, and so does
# `named_source` — by the @mention id and by the prose word "snowflake" both. That overlap is the
# whole bug: it is the one prompt shape that trips the short-circuit and the gate at once.
PROMPT = "build me a dashboard from the gong table in @Snowflake-Data-Warehouse"


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """CHAT, so anything these tests see from the classifier came from the regex instead."""

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
                                             sovereign_ask="s",
                                             plan="p", implement="i", ask="a"),
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


def _count_reads(orch: Orchestrator) -> list[str]:
    """Every database the gate actually walked — the latency the ordering is allowed to cost."""
    read: list[str] = []
    inner = orch._resources.list_database_tables

    def counted(source, database):
        read.append(database)
        return inner(source, database)

    orch._resources.list_database_tables = counted
    return read


def _thread_with_source(orch: Orchestrator, scope: dict | None = None) -> str:
    """A Thread using `Snowflake-Data-Warehouse`, with no table chosen on it unless `scope` says."""
    tid = orch.create_thread()["id"]
    item = {"kind": "data_source", "name": "Snowflake-Data-Warehouse",
            "bindingKey": ["data_source", "ds-dwh"]}
    if scope:
        item["scope"] = scope
    orch.add_thread_context(tid, item)
    return tid


def _types(events: list[dict]) -> list[str]:
    return [str(e.get("type") or "") for e in events]


# ---- the crack ---------------------------------------------------------------------------------


def test_a_build_request_naming_a_data_source_is_asked_which_table_first(tmp_path: Path):
    """The card, on the turn that asked — not after the crossing and not never.

    This is the whole issue in one assertion. Before the reorder the same call answered with a
    lone `handoff-suggest` in three milliseconds, and the person crossed into Build carrying an
    unscoped Binding that nothing downstream would ever ask about.
    """
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    events = list(orch.chat_stream(tid, PROMPT))

    kinds = _types(events)
    assert "table-candidates" in kinds
    assert "handoff-suggest" not in kinds
    # The turn stops at the question, the way every other declared gate does: no agent ran, so
    # nothing was written against a table nobody has picked.
    assert kinds[-1] == "done"
    assert events[-1]["decision"] == "table candidates"
    assert oc.prompts == []


def test_the_card_names_the_tables_a_build_request_would_have_invented(tmp_path: Path):
    """The same search Build's gate runs, so the crossing carries a table somebody chose.

    `GONG__CALLS` is on the card because it is in the warehouse. `GONG` — what the model wrote
    when nobody asked — is not, and never was.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    card = next(e for e in orch.chat_stream(tid, PROMPT) if e["type"] == "table-candidates")

    assert card["sourceId"] == "ds-dwh"
    assert card["threadId"] == tid
    tables = [t for g in card["allGroups"] for t in g["tables"]]
    assert "GONG__CALLS" in tables
    assert "GONG" not in tables


def test_the_nudge_still_arrives_on_the_turn_the_click_buys(tmp_path: Path):
    """Deferred, not dropped.

    The gate going first is only safe if the request still reaches Build. The click replays the
    question with `skipTableGate`, and the nudge lands on that turn — after the table is on the
    Thread's row, which is where `binding_from_context` reads it on the way across. So the app is
    born scoped, which is the half of the damage `confirm_handoff` was writing silently.

    Pinned by POSITION, not by membership. The nudge arriving at all is not the property worth
    having — the model classifier already offers it after a turn, so "in the events somewhere" was
    true even when the regex was skipped. What the click buys is the nudge arriving INSTEAD of a
    turn, in the milliseconds the regex costs. Asserting membership let exactly that regression
    through once: `skip_table_gate` makes `asking` false, so guarding the explicit detect with it
    answered the click with a whole sage-chat turn and put the nudge after it.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    assert "table-candidates" in _types(list(orch.chat_stream(tid, PROMPT)))

    orch.confirm_thread_table_candidate(tid, "ds-dwh", "DWH", "MARTS", "GONG__CALLS")
    answered = list(orch.chat_stream(tid, PROMPT, skip_table_gate=True))

    kinds = _types(answered)
    assert "handoff-suggest" in kinds
    # Before the turn, which means instead of one: no assistant text and no `done` ahead of it.
    assert kinds.index("handoff-suggest") == 0
    assert "agent" not in kinds
    row = next(i for i in ThreadStore(orch.project(start_preview=False).record.path)
               .read_context(tid)["items"] if i.get("kind") == "data_source")
    assert row["scope"]["table"] == "GONG__CALLS"


# ---- what the reorder must not cost --------------------------------------------------------


def test_a_build_request_naming_no_data_source_is_nudged_without_reading_anything(tmp_path: Path):
    """Unchanged, and still free.

    A Thread with an unscoped Data Source on it whose request names no store keeps the fast
    short-circuit: the gate declines off the Thread's own rows, before any catalog call.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    read = _count_reads(orch)

    events = list(orch.chat_stream(tid, "build me a dashboard of last quarter's headcount"))

    assert "handoff-suggest" in _types(events)
    assert "table-candidates" not in _types(events)
    assert read == []


def test_a_thread_already_scoped_to_a_table_is_nudged_without_reading_anything(tmp_path: Path):
    """The gate is about an unanswered half of a question. Answered, it costs nothing again."""
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch, scope={"database": "DWH", "schema": "MARTS",
                                           "table": "GONG__CALLS"})
    read = _count_reads(orch)

    events = list(orch.chat_stream(tid, PROMPT))

    assert "handoff-suggest" in _types(events)
    assert "table-candidates" not in _types(events)
    assert read == []


# ---- the silence -------------------------------------------------------------------------------


def test_a_gate_that_declines_says_which_branch_declined(tmp_path: Path, caplog):
    """Six hypotheses, because a gate that does not fire says nothing at all.

    `/api/diag/log` is the instrument for this — `/api/diag/timing` says only that a turn was
    three milliseconds long — so each decline names itself there.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    with caplog.at_level(logging.INFO, logger="sage"):
        list(orch.chat_stream(tid, "build me a dashboard of last quarter's headcount"))

    lines = [r.getMessage() for r in caplog.records if "table gate" in r.getMessage()]
    assert lines, "a declined table gate left no trace"
    assert any("no unscoped Data Source" in ln for ln in lines)
