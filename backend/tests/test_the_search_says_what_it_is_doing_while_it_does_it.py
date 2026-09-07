"""The table search streams, and it reads a store once a session (#186, ADR-0038).

THE BUG THIS ENDS is a silence. One database-wide query is 3.84s on the live warehouse and the
search walks up to four of them, so a person who asked for a dashboard and named their store sat
looking at nothing for up to fifteen seconds before the card appeared. A wait nobody is told about
is a hang: the composer is idle, the transcript is empty, and the only thing to do is send the
request again — which queues a second turn behind the walk that had not finished.

So the search says what it is doing before its first query, and says what it has found after each
database lands. The names are readable while the rest of the warehouse is still being read, which
is the point: the table somebody wanted is usually in the first database, and seeing it there is
what makes the remaining seconds a wait rather than a failure.

WHAT THE STREAMING FRAMES DELIBERATELY DO NOT CARRY is the prompt. That is what makes the settled
card answerable and a half-read one only readable, and it is a rule about turns rather than about
taste: a click writes a record and sends the request again, and a request sent while the walk is
still running would queue behind the turn that is walking — which would then finish by drawing a
card onto a transcript that had already answered it. Picking waits for the walk. Reading does not.

The second half is the cache, and it is the same measurement the whole search rests on: the
filtered query was SLOWER than the unfiltered scan, being the same scan. Filtering buys tokens and
never seconds, so the catalog is pulled once and narrowed locally — and the databases a store holds
are read once beside the tables in them, because a search that re-asks what it already knows pays a
query to learn nothing.

Driven at the HTTP seam over `TestClient`, on the fake provider, because the frame order is the
contract: the card being drawn is one function, the searching frames another, and a stream that
emitted them in the wrong order would still pass every unit test under it.
"""
from __future__ import annotations

import json
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


def _client(orch: Orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setattr(appmod, "orchestrator", orch)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    return TestClient(appmod.control_app)


def _bind(client: TestClient) -> None:
    res = client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})
    assert res.status_code == 200, res.text


def _frames(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _two_databases(orch: Orchestrator) -> None:
    """Two databases, so there is a moment where the search knows half of what it will know.

    The subject is split across them on purpose. `GONG__CALLS` lands with the first database and
    `GONG_CALL_SUMMARY` only with the second, so the shortlist a person is reading really does
    change under them — which is the case a card that only ever appeared finished cannot show.
    """
    orch._resources.tree["ds-dwh"] = {
        "DWH": {"MARTS": ["GONG__CALLS", "DIM_ACCOUNT"]},
        "SANDBOX": {"PUBLIC": ["GONG_CALL_SUMMARY", "SCRATCH"]},
    }


# ---- the stream ----------------------------------------------------------------------------------


def test_the_search_says_it_is_reading_the_store_before_it_walks_a_database(
        tmp_path: Path, monkeypatch):
    """The first frame goes out before the first database is walked, which is the fix for the
    silence.

    Not after the first database: the first database IS the wait. A frame that arrived once the
    names did would leave the same 3.84 seconds of nothing at the front of the search, and that is
    the stretch a person reads as a hang.

    Before the first walk, and not before every query: the search asks which databases there are
    first, and refuses in silence if there are too many to search. That is one metadata round trip
    against 3.84 seconds a database, and it buys the property the test below pins — a store nobody
    was ever going to search does not flash a card that has to be taken back.

    Driven off the generator rather than off a buffered response, because ordering is the claim: a
    `TestClient` hands back the whole body at once and cannot tell an event that went out first
    from one that was merely written first.
    """
    orch = _orch(tmp_path)
    _two_databases(orch)
    client = _client(orch, monkeypatch)
    _bind(client)
    produced: list[dict] = []
    when: list[int] = []
    walk = orch._resources.list_database_tables
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: when.append(len(produced)) or walk(source, database))

    # `list(...)` would be the same events and none of the evidence: the loop is what makes
    # `produced` grow AS the generator runs, so the count taken inside the query says how much had
    # already gone out when it ran.
    for ev in orch.build_stream(PROMPT):
        produced.append(ev)  # noqa: PERF402

    assert when[0] == 1, "the warehouse was queried before anything was said about querying it"
    assert produced[0]["type"] == "table-search"
    assert produced[0]["sourceName"] == "Snowflake-Data-Warehouse"
    assert produced[0]["total"] == 0, "the search claimed to have found something before it looked"


def test_the_names_are_readable_while_the_rest_of_the_warehouse_is_still_being_read(
        tmp_path: Path, monkeypatch):
    """What the person watches: names, arriving, in the order the ranking reaches them.

    The count is what makes the frames legible as progress rather than as redraws — it climbs, and
    the last searching frame holds everything the settled card holds.
    """
    orch = _orch(tmp_path)
    _two_databases(orch)
    client = _client(orch, monkeypatch)
    _bind(client)

    frames = _frames(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    searching = [f for f in frames if f["type"] == "table-search"]
    assert [f["total"] for f in searching] == [0, 2, 4]
    assert "GONG__CALLS" in {t for g in searching[1]["groups"] for t in g["tables"]}
    # And every one of them arrived before the card, which is the ordering the whole ticket is
    # about: a stream that settled first and narrated afterwards is a slower search that also lies.
    card = next(i for i, f in enumerate(frames) if f["type"] == "table-candidates")
    assert all(frames.index(f) < card for f in searching)
    assert frames[card]["total"] == 4


def test_a_half_read_catalog_offers_no_way_to_pick_a_table_from_it(tmp_path: Path, monkeypatch):
    """Readable, not answerable — and the seam that enforces it is the absent prompt.

    A click is two acts: it writes the record and it sends the request again. Sent while the walk
    is still running, that second act queues behind the turn doing the walking, and that turn ends
    by drawing its settled card onto a transcript which has already answered one. So the frames
    that stream carry no prompt to replay and no gates to carry, and only the settled card does.
    """
    orch = _orch(tmp_path)
    _two_databases(orch)
    client = _client(orch, monkeypatch)
    _bind(client)

    frames = _frames(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    for frame in [f for f in frames if f["type"] == "table-search"]:
        assert "prompt" not in frame
        assert "answered" not in frame
    card = next(f for f in frames if f["type"] == "table-candidates")
    assert card["prompt"] == PROMPT


# ---- what the stream takes back --------------------------------------------------------------


def test_a_store_that_answers_none_of_its_databases_takes_the_card_back(
        tmp_path: Path, monkeypatch):
    """Refused rather than truncated, in the one case that is still a refusal after #191.

    A walk that read SOME database keeps what it found and names what it could not read — that
    card is covered next door, in `test_one_unreadable_database...`. A walk that read none of
    them is not a partial answer, it is no answer: the card would have nothing true to put on it,
    so it is taken back and the turn goes on to the build it would have run before any of this
    existed, where the assistant meets the unscoped section and asks.

    The message is the point of the assertion. "Holds no Tables" is a fact about the warehouse and
    would be a lie here — nobody managed to read it — so the two ways of finding nothing must not
    come out saying the same thing.
    """
    orch = _orch(tmp_path)
    _two_databases(orch)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: (_ for _ in ()).throw(
            ResourceUnavailable("the warehouse stopped answering")))
    _bind(client)

    frames = _frames(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    # The frame sequence and not just the absence of a card: a database that will not answer is
    # skipped now rather than ending the walk, so the frames go on going out — one before the
    # first query and one after each database — and stopping that is a regression this file is
    # the only place to catch.
    assert [f["type"] for f in frames if f["type"].startswith("table-")] == [
        "table-search", "table-search", "table-search", "table-search-ended"]
    ended = next(f for f in frames if f["type"] == "table-search-ended")
    assert ended["sourceId"] == "ds-dwh"
    # And it says so on the way out. Names appearing and then vanishing is the one thing streaming
    # can do that silence could not, and the assistant's "which table?" a moment later does not say
    # whether the store failed or held nothing — which are different facts about their warehouse.
    assert ended["message"] == (
        "Sage could not finish reading Snowflake-Data-Warehouse, so it has no Tables to offer for "
        "this request.")
    assert built == [1]


def test_a_stop_pressed_during_the_walk_ends_this_turn_and_not_the_next_one(
        tmp_path: Path, monkeypatch):
    """Streaming is what makes this reachable: the search is now a visible wait, and a visible wait
    is one somebody sits through and then decides against.

    `stop_build` sets a flag and leaves it for the running turn to trip over, and until #186 no gate
    ever polled one — so a Stop pressed while the warehouse was being read was accepted, ignored,
    and left standing. The card would land on a transcript that had already said the turn stopped,
    and the flag would still be set when the person asked their NEXT question, which would then be
    killed before it ran a step. That is the failure `stop_build`'s own docstring says was fixed.

    Nothing has been written by this point — no files, and the streaming frames are never persisted
    — so stopping here costs nothing to unwind. The turn ends, the card goes, and the flag is clear.
    """
    orch = _orch(tmp_path)
    _two_databases(orch)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    walk = orch._resources.list_database_tables
    read: list[str] = []

    def stop_while_reading(source, database):
        # Pressed while the first database is being read, which is the window that exists at all
        # only because the search takes seconds and says so.
        read.append(database)
        orch.project().stop_requested = True
        return walk(source, database)

    orch._resources.list_database_tables = stop_while_reading  # type: ignore[method-assign]
    _bind(client)

    frames = _frames(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    types = [f["type"] for f in frames]
    assert [t for t in types if t.startswith("table-")] == [
        "table-search", "table-search", "table-search-ended"]
    assert "stopped" in types
    assert built == [], "the build ran through a Stop"
    # The half that poisons the next question rather than this one, and the half no screenshot
    # would show: the flag is down again, so the request after this one is not killed by it.
    assert orch.project().stop_requested is False
    # And only the first database was read — a walk that ran to the end and reported a stop
    # afterwards would spend the seconds the person pressed Stop to get back.
    assert read == ["DWH"]


def test_a_store_that_turns_out_to_hold_nothing_takes_the_card_back_too(
        tmp_path: Path, monkeypatch):
    """The search cannot know there is nothing to offer until it has looked, and by then it has
    already said it is looking. An empty card would ask a question with no answers on it; a
    searching card left up forever would be worse, because it would never stop saying "reading"."""
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": []}}
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    _bind(client)

    frames = _frames(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert [f["type"] for f in frames if f["type"].startswith("table-")] == [
        "table-search", "table-search", "table-search-ended"]
    assert next(f for f in frames if f["type"] == "table-search-ended")["message"] == (
        "Snowflake-Data-Warehouse holds no Tables Sage can offer for this request.")
    assert built == [1]


def test_a_store_that_cannot_be_walked_at_all_never_says_it_is_reading_one(
        tmp_path: Path, monkeypatch):
    """Nothing is said before the questions that cost no query are answered.

    A connector with no database-wide statement, and a source holding more databases than one
    search walks, are both settled before the first query — so neither flashes a card that has to
    be taken back. The turn reads exactly as it did before streaming existed.
    """
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {f"DB_{n}": {"PUBLIC": ["GONG__CALLS"]} for n in range(5)}
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    _bind(client)

    body = client.post("/api/project/build/stream", json={"prompt": PROMPT}).text

    assert "table-search" not in body
    assert built == [1]


# ---- what the session remembers ----------------------------------------------------------------


def test_the_databases_a_store_holds_are_read_once_beside_the_tables_in_them(
        tmp_path: Path, monkeypatch):
    """A second search against the same store issues no catalog query at all — not one for the
    tables and not one for the databases holding them.

    The table walk was already read once a session. Asking "what databases are there" before every
    walk left the cheaper half of the same question being paid for again, which is the same
    mistake one level up: the answer had not changed, and the session bounds how far it can drift.

    Counted from after the Binding is written, because the bind itself reads the level below the
    Scope to draw the panel's door — a cascade somebody is walking now, which is the one caller
    that has to keep asking the store.
    """
    orch = _orch(tmp_path)
    _two_databases(orch)
    client = _client(orch, monkeypatch)
    _bind(client)
    databases: list[str] = []
    walks: list[str] = []
    listing, walk = orch._resources.list_databases, orch._resources.list_database_tables
    orch._resources.list_databases = (  # type: ignore[method-assign]
        lambda source: databases.append(source.id) or listing(source))
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: walks.append(database) or walk(source, database))

    client.post("/api/project/build/stream", json={"prompt": PROMPT})
    client.post("/api/project/build/stream",
                json={"prompt": "and a chart of scratch usage from Snowflake too"})

    assert databases == ["ds-dwh"]
    assert walks == ["DWH", "SANDBOX"]
