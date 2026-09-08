"""Naming a Data Source with no table chosen gets candidates, not a refusal (#183, ADR-0038).

THE BUG THIS ENDS, observed live. Somebody added `Snowflake-Data-Warehouse` to their Project and
asked for a dashboard of daily Gong calls. Sage refused. They said "search for it, it will have Gong
in the table name". Sage refused again, and offered the two database names it happened to hold.

The refusal was correct and it was useless. `bound_schema`'s unscoped section wrote into the
assistant's own prompt that it could not query and could not choose, and the assistant obeyed the
rule we wrote. What was wrong was reading ADR-0010's "a Binding is never inferred" as "Sage must not
look". Looking and choosing are two acts: Sage reads the store's own catalog and offers what it
holds, and the click that answers the card is the declaration — the same one the panel's picker
makes, reached from where the person was already standing.

WHAT THE TESTS BELOW ARE CAREFUL NOT TO ASSERT is that a particular table ranks first. Ranking here
is name matching only, and it is known to be insufficient rather than assumed to be enough: `GONG`
matches 27 of the live warehouse's 602 tables, and `MARTS.GONG__CALLS` scores identically to
`STAGING.STG_GONG__CALLS` on any name matcher. What is a contract is that both are on the card, that
they are grouped by the schema that tells them apart, and that every other table stays reachable —
a ranking that puts the right name sixth has to cost a scroll and never a dead end.

Driven at the HTTP seam over `TestClient`, on the fake provider and the fake gateway, because that
is the only seam the whole act crosses: a prompt goes in, a card comes back, a click writes the
record, and the request the person already made is built against it.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

import sage.orchestrator.app as appmod
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE
from sage.resources.provider import FakeResourceProvider, ResourceUnavailable
from sage.router.models import ModelCatalog

PROMPT = "build me a dashboard of daily gong calls from Snowflake"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    """A real workspace on disk, with the agent turn itself stubbed out.

    `_build_stream` is what would open an OpenCode session. Everything under test happens before it
    or instead of it, and whether it was reached at all is itself an assertion below: the whole
    point is that the assistant is never asked to choose a table.
    """
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    return orch


def _gong_warehouse(orch: Orchestrator) -> None:
    """The live warehouse's shape, in miniature: one subject spread over the dbt layers.

    `GONG__CALLS` in `MARTS` is the modeled table a daily summary wants and `STG_GONG__CALLS` in
    `STAGING` is the raw one it does not. They are here because they are the pair a name matcher
    cannot separate, which is what the card exists to put in front of a person.
    """
    orch._resources.tree["ds-dwh"] = {
        "DWH": {
            "MARTS": ["GONG__CALLS", "GONG__CALL_PARTICIPANTS", "FCT_USAGE_DAILY", "DIM_ACCOUNT"],
            "STAGING": ["STG_GONG__CALLS", "STG_SALESFORCE__OPPORTUNITY"],
            "REPORTING": ["V_ARR_WATERFALL"],
        },
    }


def _client(orch: Orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setattr(appmod, "orchestrator", orch)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    return TestClient(appmod.control_app)


def _bind(client: TestClient) -> None:
    res = client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})
    assert res.status_code == 200, res.text


def _card(text: str) -> dict:
    """The one `table-candidates` frame out of an SSE body."""
    frames = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    cards = [f for f in frames if f.get("type") == "table-candidates"]
    assert len(cards) == 1, f"expected exactly one candidate card, got {[f['type'] for f in frames]}"
    return cards[0]


def _sources(client: TestClient) -> list[dict]:
    return [b for b in client.get("/api/bindings").json()["bindings"]
            if b["kind"] == KIND_DATA_SOURCE]


# ---- the card ----------------------------------------------------------------------------------


def test_naming_a_data_source_with_no_table_offers_candidates_instead_of_building(
        tmp_path: Path, monkeypatch):
    """The whole reversal, in one request. The prompt names the store in the words a person would
    use to a colleague — "from Snowflake" against `Snowflake-Data-Warehouse` — and what comes back
    is the store's own tables rather than a sentence handing the work back.

    The turn stops before the assistant is asked anything, which is the rule and not a side effect:
    an assistant that could pick a table would be inferring a Binding on the turn it felt confident,
    and the app it built would work in this session and break for every viewer of the published one.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    asked = []
    orch._build_stream = lambda *a, **k: asked.append(1) or iter([])  # type: ignore[method-assign]
    _bind(client)

    card = _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert card["sourceId"] == "ds-dwh"
    assert card["matched"] > 0
    assert asked == [], "the assistant was asked to build against a table nobody had chosen"


def test_the_candidates_are_grouped_by_schema_with_every_other_table_behind_them(
        tmp_path: Path, monkeypatch):
    """Grouped by schema because the schema is the difference being asked about, and capped at five
    because a card is read rather than scrolled.

    Both halves are here for the same reason. `GONG__CALLS` and `STG_GONG__CALLS` are two answers to
    one question and only their schemas say which is which — so a flat list of names would ask the
    person to pick blind. And a table that ranked seventh is still a table they may recognise on
    sight, so the full list travels with the card rather than costing them another prompt.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    _bind(client)

    card = _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert sum(len(g["tables"]) for g in card["groups"]) == 5
    assert card["total"] == 7
    assert {g["schema"] for g in card["groups"]} > {"MARTS"}
    for group in card["groups"] + card["allGroups"]:
        assert group["database"] == "DWH"
    everything = {(g["schema"], t) for g in card["allGroups"] for t in g["tables"]}
    assert everything == {
        ("MARTS", "GONG__CALLS"), ("MARTS", "GONG__CALL_PARTICIPANTS"),
        ("MARTS", "FCT_USAGE_DAILY"), ("MARTS", "DIM_ACCOUNT"),
        ("STAGING", "STG_GONG__CALLS"), ("STAGING", "STG_SALESFORCE__OPPORTUNITY"),
        ("REPORTING", "V_ARR_WATERFALL"),
    }
    # The pair a name matcher cannot separate are BOTH offered. Which of them ranks first is a
    # model's judgement and a later ticket; putting one of them on the card and not the other would
    # be this code making that judgement by omission.
    shortlisted = {t for g in card["groups"] for t in g["tables"]}
    assert {"GONG__CALLS", "STG_GONG__CALLS"} <= shortlisted


def test_a_request_no_table_name_matches_says_so_and_still_shows_the_list(
        tmp_path: Path, monkeypatch):
    """No invented table name, ever — and no dead end either.

    "Nothing matched" is a fact about the names, not about the warehouse: the person often knows the
    table by sight even when the words they used to ask are nowhere in it. So the sentence says
    plainly that nothing matched, and every table is still there to pick from.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    _bind(client)

    card = _card(client.post(
        "/api/project/build/stream",
        json={"prompt": "chart our helpdesk ticket backlog from Snowflake"}).text)

    assert card["matched"] == 0
    # Plain text, no Markdown: the card renders its sentence as a text node, as the four nudges
    # beside it do, so asterisks here would reach the person as asterisks.
    assert "Nothing in Snowflake-Data-Warehouse matched" in card["message"]
    assert "Pick a Table" in card["message"]
    assert card["total"] == 7
    assert sum(len(g["tables"]) for g in card["allGroups"]) == 7


def test_an_at_mention_names_the_data_source_without_the_prose_having_to(
        tmp_path: Path, monkeypatch):
    """A mention is an identity, not a guess: the person picked a row and the id came back with it.

    It matters because the prose match is a heuristic and this one is not. Somebody who @mentions
    the Data Source and then describes the data in words that name no store at all still gets the
    search, which is the case the prose path cannot answer.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    _bind(client)

    card = _card(client.post("/api/project/build/stream", json={
        "prompt": "a daily summary of gong calls",
        "resources": [{"kind": KIND_DATA_SOURCE, "id": "ds-dwh"}],
    }).text)

    assert card["sourceId"] == "ds-dwh"


def test_a_data_source_with_a_table_already_chosen_is_not_asked_about_again(
        tmp_path: Path, monkeypatch):
    """The gate is about the unanswered half of the question, and once it is answered it is gone.

    Also what makes the replay below terminate: the click writes the table, so the same prompt sent
    again reaches the build rather than the card that produced it.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    _bind(client)
    client.post("/api/bindings/data_source/ds-dwh/scope",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    body = client.post("/api/project/build/stream", json={"prompt": PROMPT}).text

    assert "table-candidates" not in body
    assert built == [1]


# ---- the click ---------------------------------------------------------------------------------


def test_the_click_writes_the_ordinary_record_and_not_a_second_parallel_one(
        tmp_path: Path, monkeypatch):
    """One manifest entry, written by the writer the panel's picker writes through.

    A search that produced its own kind of record would be a second answer to "what does this app
    read", and `publish`, `usedBy` and the AGENTS.md data block would each have to learn about it.
    The click is a declaration, and a declaration already has a shape.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    _bind(client)

    res = client.post("/api/bindings/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    assert res.status_code == 200, res.text
    rows = _sources(client)
    assert len(rows) == 1
    assert (rows[0]["database"], rows[0]["schema"], rows[0]["table"]) == (
        "DWH", "MARTS", "GONG__CALLS")


def test_the_chosen_table_is_looked_for_again_before_the_record_is_written(
        tmp_path: Path, monkeypatch):
    """A stale list costs a click. A stale CHOICE costs the first viewer of the published app.

    The catalog behind a card may have been read minutes ago and kept for the session, so the one
    table being recorded is looked for again in a query narrow enough to be worth it. Nothing is
    written when it is gone, and the sentence says which table and where it was.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    _bind(client)
    _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)
    # Dropped between the card and the click, which is exactly the window this exists to cover.
    orch._resources.tree["ds-dwh"]["DWH"]["MARTS"] = ["FCT_USAGE_DAILY", "DIM_ACCOUNT"]

    res = client.post("/api/bindings/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    assert res.status_code == 502
    assert res.json()["error"] == (
        "GONG__CALLS is no longer in MARTS, so Sage did not record it. Ask again to search "
        "Snowflake-Data-Warehouse as it is now."
    )
    assert "table" not in _sources(client)[0], "a dropped table reached the record"
    # And asking again is an instruction that can be followed. The check proved the remembered
    # catalog wrong about this database, so the next search reads the store — offering the same
    # dropped table forever would make the sentence above name the one act that cannot work.
    again = _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)
    assert "GONG__CALLS" not in {t for g in again["allGroups"] for t in g["tables"]}


def test_the_check_is_one_narrow_query_and_not_the_whole_walk_again(
        tmp_path: Path, monkeypatch):
    """The check has to be worth making, and it is a click the person is waiting behind.

    The test above already proves it reads the store rather than the session's cached list — the
    table was dropped after the card was built and the cache still held it. What this adds is the
    cost: the chosen schema is asked about, and the database-wide walk that filled the card is not
    run a second time to answer a question about one table.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    _bind(client)
    _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)
    narrow: list[tuple[str, str]] = []
    wide: list[str] = []
    tables, walk = orch._resources.list_tables, orch._resources.list_database_tables
    orch._resources.list_tables = (  # type: ignore[method-assign]
        lambda source, database, schema: narrow.append((database, schema)) or tables(
            source, database, schema))
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: wide.append(database) or walk(source, database))

    client.post("/api/bindings/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    # The first entry is the check. The fake's `list_columns` reaches this same method again while
    # recording what the newly chosen table holds, which is the fake's own shortcut — the real
    # provider answers columns with its own statement.
    assert narrow[0] == ("DWH", "MARTS")
    assert wide == []


def test_a_click_that_names_no_table_is_refused_rather_than_recorded_as_a_schema(
        tmp_path: Path, monkeypatch):
    """This path always lands on exactly one table, and never settles for the schema.

    "Somewhere in PUBLIC" does not answer "which table has the Gong data", and a record that said it
    did would leave the agent writing `FROM DWH.MARTS`. A schema-level record is a real choice and
    it stays available from the panel, which is the surface that owns that door (ADR-0021).
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    _bind(client)

    res = client.post("/api/bindings/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": "MARTS"})

    assert res.status_code == 400
    assert res.json()["error"] == "Pick one Table. Sage does not record a schema from here."
    assert "schema" not in _sources(client)[0]


def test_a_click_on_an_app_that_does_not_depend_on_the_source_records_nothing(
        tmp_path: Path, monkeypatch):
    """The same refusal the scope route makes, for the same reason: a Table is part of a dependency,
    so writing one where there is no Binding would record the dependency as a side effect."""
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)

    res = client.post("/api/bindings/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    assert res.status_code == 404
    assert _sources(client) == []


# ---- the replay --------------------------------------------------------------------------------


def test_the_answered_card_replays_the_original_prompt_against_the_written_record(
        tmp_path: Path, monkeypatch):
    """The person asked once. `skipTableGate` is the card being answered, and it is the whole seam
    between the click and the build — drop it from the route and the click hands back the same card
    forever, with every unit test still green.

    The build that follows sees the table on the record, because the click wrote it first.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    built: list[str] = []
    orch._build_stream = (  # type: ignore[method-assign]
        lambda prompt, *a, **k: built.append(prompt) or iter([]))
    _bind(client)
    _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)
    client.post("/api/bindings/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    replay = client.post("/api/project/build/stream",
                         json={"prompt": PROMPT, "skipTableGate": True})

    assert "table-candidates" not in replay.text
    assert built == [PROMPT]
    assert _sources(client)[0]["table"] == "GONG__CALLS"


def test_the_replay_is_a_new_turn_that_waits_for_the_turn_lock(tmp_path: Path, monkeypatch):
    """The turn that offered the card ENDED when it offered it, so this is not a paused turn
    resuming — it is an ordinary turn, and it queues behind whatever is running like any other.

    Worth pinning at this seam because the alternative is invisible until two people are in one
    Project: a replay that ran outside the lock would write the working tree under a build already
    holding it, which is the collision `_turn_lock` exists to prevent.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    _bind(client)
    client.post("/api/bindings/data_source/ds-dwh/candidate",
                json={"database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})
    body: list[str] = []

    def replay():
        body.append(client.post("/api/project/build/stream",
                                json={"prompt": PROMPT, "skipTableGate": True}).text)

    assert orch._turn_lock.acquire(blocking=False)
    caller = threading.Thread(target=replay, daemon=True)
    caller.start()
    try:
        time.sleep(0.3)
        assert caller.is_alive(), "the replay ran without waiting for the lock a turn was holding"
    finally:
        orch._turn_lock.release()
    caller.join(timeout=10)
    assert not caller.is_alive()
    # It waited on its own connection and then ran, which is what a queued turn does.
    assert '"type": "pending"' in body[0]


# ---- what the search reads ----------------------------------------------------------------------


def test_the_whole_catalog_is_read_once_and_a_second_question_costs_no_query(
        tmp_path: Path, monkeypatch):
    """Filtering buys tokens and never seconds — measured, on the live warehouse: the filtered query
    was SLOWER than the unfiltered scan, being the same scan (ADR-0038). So the catalog is pulled
    once and narrowed locally, and the second thing a person asks about a store is free."""
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    walks: list[str] = []
    walk = orch._resources.list_database_tables
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: walks.append(database) or walk(source, database))
    _bind(client)

    client.post("/api/project/build/stream", json={"prompt": PROMPT})
    client.post("/api/project/build/stream",
                json={"prompt": "and a chart of usage from Snowflake too"})

    assert walks == ["DWH"]


def test_a_database_already_chosen_narrows_the_walk_rather_than_being_asked_again(
        tmp_path: Path, monkeypatch):
    """A Binding that stopped at a database is somebody who answered part of the question. Re-asking
    it would be Sage forgetting what they already told it, and the level still unanswered is the one
    that decides whether a query can run."""
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {
        "DWH": {"MARTS": ["GONG__CALLS"]},
        "SANDBOX": {"PUBLIC": ["SCRATCH_GONG"]},
    }
    client = _client(orch, monkeypatch)
    walks: list[str] = []
    walk = orch._resources.list_database_tables
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: walks.append(database) or walk(source, database))
    _bind(client)
    client.post("/api/bindings/data_source/ds-dwh/scope", json={"database": "DWH"})

    card = _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert walks == ["DWH"]
    assert card["total"] == 1


def test_a_source_holding_more_databases_than_the_search_walks_asks_instead(
        tmp_path: Path, monkeypatch):
    """A search that takes a minute is slower than reading the data catalog by hand, which is the
    work it exists to save. One database-wide query is 3.84s, and this runs before anything streams,
    so the number of them is bounded.

    Refused rather than truncated, which is the half worth pinning. A card built from the first four
    databases would say "no name matched" about a warehouse it had not finished reading, and the
    table they wanted would be in the one nobody walked. So the turn falls through and the person is
    asked — and the database is a level they can settle from the panel.
    """
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {
        f"DB_{n}": {"PUBLIC": ["GONG__CALLS"]} for n in range(5)
    }
    client = _client(orch, monkeypatch)
    walks: list[str] = []
    walk = orch._resources.list_database_tables
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: walks.append(database) or walk(source, database))
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    _bind(client)

    body = client.post("/api/project/build/stream", json={"prompt": PROMPT}).text

    assert "table-candidates" not in body
    assert walks == [], "the walk started before the count was checked"
    assert built == [1]


def test_a_store_that_will_not_say_what_it_holds_leaves_the_turn_as_it_was(
        tmp_path: Path, monkeypatch):
    """A connector Sage cannot walk a whole database of — and a connector it has no dialect for at
    all — refuses by name rather than answering with an empty warehouse.

    Neither can be put on a card, so the turn goes on to the build it would have run before any of
    this existed: the assistant meets the unscoped section and asks. A worse answer than the card, a
    better one than a card with nothing on it, and not a regression on anybody.
    """
    orch = _orch(tmp_path)
    _gong_warehouse(orch)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: (_ for _ in ()).throw(ResourceUnavailable("no walk here")))
    _bind(client)

    body = client.post("/api/project/build/stream", json={"prompt": PROMPT}).text

    assert "table-candidates" not in body
    assert built == [1]


def test_a_data_source_holding_no_tables_is_not_offered_as_an_empty_card(
        tmp_path: Path, monkeypatch):
    """An empty card would ask a question with no answers on it. The store answered, and what it
    said was that there is nothing here to choose — which the unscoped section already tells the
    assistant to say out loud."""
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": []}}
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    _bind(client)

    body = client.post("/api/project/build/stream", json={"prompt": PROMPT}).text

    assert "table-candidates" not in body
    assert built == [1]
