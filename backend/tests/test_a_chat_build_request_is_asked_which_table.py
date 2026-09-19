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

The second group pins the cost, and #445 moved where that line falls. The gate was allowed to be
first because it was free when it declined — it read the Thread's own rows, and a request naming
no unscoped Data Source reached no warehouse at all. A sole attached store is now inferred rather
than named, and whether the turn is about that store is a fact only its catalog holds, so that
population walks before it declines. What stays free is what the rows can still settle: two
unscoped stores, or none.

The third group is #445 itself. Every "no card" assertion in it is paired with a positive control
on the same prompt shape, because the two ways to draw no card are indistinguishable from the
events — the gate ruling the store out, and the gate never running at all. #426 was the second one
passing as the first for a day.
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


def _second_source(orch: Orchestrator, tid: str) -> None:
    """A second unscoped Data Source on the Thread, which is a real question with a real answer.

    `test` rather than a second warehouse, and the name is the point: `_handles("test")` is
    `{"test"}` — a name made entirely of generic words keeps them rather than reducing to nothing
    — so no prompt in this file reaches it by accident. A decline under two stores therefore
    cannot be the prose quietly matching one of them and the count never being read.

    It holds `GONG__CALLS` too, so the mention test below is choosing between two stores that
    could both answer rather than between one that can and one that cannot.
    """
    orch._resources.tree["ds-test"] = {"SANDBOX": {"PUBLIC": ["GONG__CALLS"]}}
    orch.add_thread_context(tid, {"kind": "data_source", "name": "test",
                                  "bindingKey": ["data_source", "ds-test"]})


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


def test_a_build_request_with_no_store_to_infer_is_nudged_without_reading_anything(tmp_path: Path):
    """Still free where the gate can decline off the Thread's own rows.

    NARROWED BY #445, and the narrowing is the cost this whole group exists to pin. This used to
    say "a Thread with an unscoped Data Source whose request names no store", and that population
    is now exactly the one the gate goes and looks at — a sole attached store is the one the
    request means, and whether the request means a store at all is a question only the catalog can
    answer. What is left free is what can still be settled from the rows: two unscoped stores, a
    real ambiguity that naming one is the answer to.

    See `test_the_only_store_is_walked_before_it_is_ruled_out` for what the other half now pays.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    _second_source(orch, tid)
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

    BOTH BRANCHES, since #445 added one. The two are a Thread apart and read identically from the
    outside: no card, and a turn that answers. Telling them apart is the only thing that says
    whether the gate weighed this store or never had one to weigh, and a shared line would put
    them back where #204 found them.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    sole = _thread_with_source(orch)
    two = _thread_with_source(orch)
    _second_source(orch, two)
    headcount = "build me a dashboard of last quarter's headcount"

    with caplog.at_level(logging.INFO, logger="sage"):
        list(orch.chat_stream(two, headcount))
        list(orch.chat_stream(sole, headcount))

    lines = [r.getMessage() for r in caplog.records if "table gate" in r.getMessage()]
    assert lines, "a declined table gate left no trace"
    # Nothing to infer, so the rows settled it and no catalog was read.
    assert any("no unscoped Data Source" in ln for ln in lines)
    # One store, walked, and ruled out by what it holds — which names the store, because the
    # question "was it this one?" is the one a reader of this line is asking.
    assert any("Snowflake-Data-Warehouse is the only store bound" in ln for ln in lines)


# ---- the store nobody had to name (#445) ----------------------------------------------------

# The live sentence, from the measurement. It names the SUBJECT and never the store:
# `_handles("Snowflake-Data-Warehouse")` is `{"snowflake"}`, because `_GENERIC_SOURCE` strips
# "data" and "warehouse", so every word of this reached nothing. Sage's reply on that turn named
# the Data Source it had just declined to look in, which is the contradiction the ticket is about.
#
# Its matching words are "gong" and "calls", which `_gong_warehouse` holds — and that is the whole
# discriminator below, so it is stated here rather than left to be inferred from a green.
UNNAMED = "give me a bar graph of number of gong calls per day for the last 30 days"


def test_the_only_data_source_on_the_thread_is_the_one_the_request_means(tmp_path: Path):
    """The card is drawn for a store the sentence never named (#445).

    There was nothing to disambiguate. The person answered "which store" by attaching it, and the
    turn that refused asked them to answer it a second time in prose — a question with exactly one
    possible answer, whose cost was the whole first data turn.
    """
    orch, oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    card = next(e for e in orch.chat_stream(tid, UNNAMED) if e["type"] == "table-candidates")

    assert card["sourceId"] == "ds-dwh"
    assert "GONG__CALLS" in [t for g in card["groups"] for t in g["tables"]]
    # The turn stops at the question, as the named path's does: nothing was built against a table
    # nobody picked.
    assert oc.prompts == []


def test_a_second_unscoped_store_makes_it_a_question_again(tmp_path: Path):
    """Two stores is a real ambiguity and #445 does not touch it — with the control the trap needs.

    THE CONTROL IS THE SAME SENTENCE on a Thread carrying one store. Without it this test passes
    on the broken code too, and for the wrong reason: nothing in `UNNAMED` reaches either store,
    so the old gate declined before any card logic ran (#426) and drew no card either way. The
    pair says the decline came from the COUNT.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    two = _thread_with_source(orch)
    _second_source(orch, two)
    one = _thread_with_source(orch)

    with_two = _types(list(orch.chat_stream(two, UNNAMED)))
    with_one = _types(list(orch.chat_stream(one, UNNAMED)))

    assert "table-candidates" not in with_two
    assert "table-candidates" in with_one


def test_a_mention_still_picks_one_store_out_of_several(tmp_path: Path):
    """An @mention is identity, not a guess, so it answers where the count cannot.

    Both stores hold `GONG__CALLS`, so the only thing separating them here is the mention. The
    control is the line above it: the same Thread and the same subject with nothing mentioned
    draws no card at all, which is what makes the card below attributable to the `@`.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    _second_source(orch, tid)

    silent = _types(list(orch.chat_stream(tid, UNNAMED)))
    card = next(e for e in orch.chat_stream(tid, UNNAMED + " from @test")
                if e["type"] == "table-candidates")

    assert "table-candidates" not in silent
    assert card["sourceId"] == "ds-test"


def test_the_only_store_is_not_asked_about_a_request_that_asks_nothing_it_holds(tmp_path: Path):
    """The second test an inferred store owes, and the reason the fix is not the ticket's one line.

    A Thread keeps its chip for the whole of its life. So "which store" being answered by the
    attachment must not turn every later turn into a table question — without this, "make the
    header blue" draws a picker for as long as no table is chosen. `ranking.matched` is what asks
    it: the request has to name something the store actually holds.

    THE CONTROL IS THE SAME THREAD AND THE SAME STORE, only the sentence changes. That is what
    separates "the ranking ruled it out" from "the gate never ran" — the two look identical from
    the events, and the whole of #426 was the second one passing as the first.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)

    unrelated = _types(list(orch.chat_stream(tid, "make the header blue")))
    asked = _types(list(orch.chat_stream(tid, UNNAMED)))

    assert "table-candidates" not in unrelated
    assert "table-candidates" in asked


def test_a_thread_with_no_data_source_at_all_is_unchanged(tmp_path: Path):
    """Nothing attached is nothing to infer, and #445 leaves it exactly where it was.

    The control is the same sentence on a Thread that has one, because "no card" is the answer on
    both sides of this fix for a Thread with nothing on it — the pair is what says the zero case
    was reached rather than the whole gate being dead.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    bare = orch.create_thread()["id"]
    attached = _thread_with_source(orch)

    without = _types(list(orch.chat_stream(bare, UNNAMED)))
    with_one = _types(list(orch.chat_stream(attached, UNNAMED)))

    assert "table-candidates" not in without
    assert "table-candidates" in with_one


def test_the_only_store_is_walked_before_it_is_ruled_out(tmp_path: Path):
    """What #445 costs, said out loud rather than discovered later.

    This gate sits ABOVE the explicit-handoff short-circuit because it was free when it declined,
    and for a Thread with one unscoped store it no longer is: `matched` is a fact about the
    store's catalog, so the catalog has to be read before the turn can be let go. The walk is kept
    for the session underneath, so this is a first-turn cost and not a per-turn one — but it is a
    cost, and the group above this one pins the population that still pays nothing.
    """
    orch, _oc = _orch(tmp_path)
    _gong_warehouse(orch)
    tid = _thread_with_source(orch)
    read = _count_reads(orch)

    events = list(orch.chat_stream(tid, "make the header blue"))

    assert "table-candidates" not in _types(events)
    assert read == ["DWH"]
