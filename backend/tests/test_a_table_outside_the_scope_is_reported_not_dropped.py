"""A table mention the app's Scope does not reach is reported, never dropped in silence.

WHAT WAS MISSING. Two lists answer "which tables can this app be pointed at", and they are not the
same list. The `@` menu offers the tables PINNED on the Project's row (`pinRow` in `js/api.js`);
a turn honours the ones inside the selected app's own Scope (`.sage/schema.json`). Bind a Data
Source to one table, pin a sibling of it, and the menu offers a table the turn will not carry —
`_resource_mention_note` drops the table clause, and `_unusable_mentions` said nothing at all,
because it compared Binding keys and never looked at the schema its neighbour reads.

So the agent was handed the store with the app's OWN table attached, built a chart from it, and the
build reported itself clean. That is the failure `_unusable_mentions` exists to remove, arriving
through the one door it left open.

WHAT THIS ASSERTS. The turn says which table it read instead, and hands back a row carrying
`table` — the field that sends the client to the Scope door rather than to the bind, since the
Binding is already on disk. And the quiet cases stay quiet: a table inside the Scope, and a Scope
that stopped above a table and therefore holds every table under it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator.service import Orchestrator
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport

        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "IMPLEMENT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    orch = Orchestrator(
        workspace_dir=ws, template=template, gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(),
        opencode_client=FakeOpenCode(ws, [Turn(text="done")]))
    orch.project(start_preview=False)
    return orch


def _ref(table: str) -> dict:
    """One table mention, in the shape `collectTurnRefs` sends. `name` is the word that was typed,
    which for a table row is the table's, and the store rides beside it as `sourceName`."""
    return {"kind": "data_source", "id": "ds-dwh", "name": table, "table": table,
            "sourceName": "Snowflake-Data-Warehouse"}


def _asked(orch: Orchestrator, table: str) -> tuple[str, list[dict], bool]:
    """What the turn says about `@table`, and whether the agent was told the table at all."""
    project = orch.project()
    line, rows = orch._unusable_mentions(project, None, [], [_ref(table)])
    note = orch._resource_mention_note(project, [_ref(table)])
    return line, rows, f"`{table}`" in note


def test_a_sibling_of_the_bound_table_is_named_and_not_swallowed(tmp_path):
    """The sentence names the table that was refused AND the one the app reads instead. Without the
    second half it says a thing is impossible without saying what is true instead, which is the
    dead end every line in this function is written to avoid."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")

    line, rows, reached_agent = _asked(orch, "DIM_ACCOUNT")

    assert not reached_agent, "the table clause is still dropped — this is the drop being reported"
    assert "@DIM_ACCOUNT" in line
    assert "DWH.MARTS.FCT_USAGE_DAILY" in line
    assert "Snowflake-Data-Warehouse" in line
    assert [(r["kind"], r["id"], r.get("table")) for r in rows] == [
        ("data_source", "ds-dwh", "DIM_ACCOUNT")]


def test_the_row_carries_the_table_so_the_client_offers_the_scope_and_not_the_bind(tmp_path):
    """`table` is the whole signal. The app HOLDS this Resource — a bind would rewrite the record
    with the values already in it and leave the gap where it was — so the field, not the kind, is
    what picks the act (`mentionFixes`)."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")

    _, rows, _ = _asked(orch, "DIM_ACCOUNT")

    assert rows[0]["table"] == "DIM_ACCOUNT"
    assert rows[0]["name"] == "DIM_ACCOUNT", "the row quotes the word that was typed"


def test_two_tables_of_one_store_are_one_row(tmp_path):
    """They share the Binding whose Scope the button opens, so two rows would offer one act twice —
    the rule the unbound rows above it already follow."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")
    project = orch.project()

    line, rows = orch._unusable_mentions(
        project, None, [], [_ref("DIM_ACCOUNT"), _ref("DIM_DATE")])

    assert len(rows) == 1
    assert "@DIM_ACCOUNT, @DIM_DATE" in line


def test_the_bound_table_says_nothing(tmp_path):
    """The case that must stay silent. A warning on a mention that worked is noise, and it would
    fire on nearly every turn."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "FCT_USAGE_DAILY")

    line, rows, reached_agent = _asked(orch, "FCT_USAGE_DAILY")

    assert reached_agent
    assert (line, rows) == ("", [])


def test_a_scope_that_stopped_above_a_table_holds_every_table_under_it(tmp_path):
    """A schema-level Scope enumerates the whole schema, so a sibling IS reachable and there is
    nothing to report. This is why the question is asked of the recorded schema rather than of the
    Binding's own `table` — the two answers differ exactly here."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "")

    for table in ("FCT_USAGE_DAILY", "DIM_ACCOUNT"):
        line, rows, reached_agent = _asked(orch, table)
        assert reached_agent, table
        assert (line, rows) == ("", []), table


def test_a_store_bound_with_no_scope_yet_says_so_in_words(tmp_path):
    """The ordinary way in, not an edge. The header's picker binds a Data Source in one argument
    and leaves the Scope as a second act (#142), so every store bound there sits in this state until
    somebody answers it — and `scope_shown` is "" for it, which would have made the sentence read
    "reads  inside Snowflake-Data-Warehouse"."""
    orch = _orch(tmp_path)
    orch.bind_data_source("ds-dwh")

    line, rows, reached_agent = _asked(orch, "DIM_ACCOUNT")

    assert not reached_agent
    assert line == ("Couldn't use @DIM_ACCOUNT. Draft app 1 hasn't chosen what it reads inside "
                    "Snowflake-Data-Warehouse.")
    assert rows[0]["table"] == "DIM_ACCOUNT"


def test_an_unbound_store_keeps_its_own_refusal(tmp_path):
    """The narrower sentence must not swallow the wider one: with no Binding at all there is no
    Scope to fall short of, and the act is still the bind."""
    orch = _orch(tmp_path)

    line, rows, _ = _asked(orch, "DIM_ACCOUNT")

    assert "doesn't use" in line
    assert rows[0].get("table") is None
