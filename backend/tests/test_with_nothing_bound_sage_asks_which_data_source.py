"""With no Data Source recorded, the request is answered with a question about which one (#185).

THE HALF #183 COULD NOT REACH. That ticket answers a person who added a Data Source and stopped;
this one answers the person who never added one. They ask for a dashboard of their warehouse, the
app records no store, and the table search has nothing to search — so the turn used to reach the
assistant, which had no store either and built the dashboard on rows it invented. That looks
finished and is worthless, which is worse than a refusal and much worse than a question.

TWO CONFIRMATIONS, and the tests below pin both halves of that. Sage asks which Data Source, and
then asks which table (#183) — even for a caller who owns exactly one Data Source, because using
the only one silently is the same inference through a side door (ADR-0038) and would change under
them the day a second one appears.

Driven at the HTTP seam over `TestClient`, on the fake provider and the fake gateway, for the
reason #183's tests are: the act crosses the whole stack. A prompt goes in, a card of stores comes
back, a click records one, and the request the person already made walks into the table search.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import sage.orchestrator.app as appmod
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE
from sage.resources.provider import DataSource, FakeResourceProvider
from sage.router.models import ModelCatalog

PROMPT = "build me a dashboard of daily gong calls from Snowflake"


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp_path: Path, resources: FakeResourceProvider | None = None) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=resources or FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    return orch


def _client(orch: Orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setattr(appmod, "orchestrator", orch)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    return TestClient(appmod.control_app)


def _frames(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _card(text: str) -> dict:
    """The one `source-candidates` frame out of an SSE body."""
    cards = [f for f in _frames(text) if f.get("type") == "source-candidates"]
    assert len(cards) == 1, f"expected one source card, got {[f['type'] for f in _frames(text)]}"
    return cards[0]


def _build(client: TestClient, **body) -> str:
    return client.post("/api/project/build/stream", json={"prompt": PROMPT, **body}).text


def _bindings(client: TestClient) -> list[dict]:
    return [b for b in client.get("/api/bindings").json()["bindings"]
            if b["kind"] == KIND_DATA_SOURCE]


def _users(client: TestClient) -> list[str]:
    rows = client.get("/api/project/history").json()["history"]
    return [r.get("text") or "" for r in rows if r.get("type") == "user"]


# ---- the card ----------------------------------------------------------------------------------


def test_a_prompt_with_nothing_bound_offers_the_sources_the_caller_can_reach(
        tmp_path: Path, monkeypatch):
    """The reversal, in one request: a question with the answers attached, not invented rows.

    The assistant is never asked, which is the rule rather than a side effect. One that could pick
    a store would be inferring a Binding on the turn it felt confident (ADR-0010), and the app it
    built would read in this session and read nothing for any viewer of the published one.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)
    asked = []
    orch._build_stream = lambda *a, **k: asked.append(1) or iter([])  # type: ignore[method-assign]

    card = _card(_build(client))

    assert [s["name"] for s in card["sources"]][:2] == ["Snowflake-Data-Warehouse", "test"]
    assert len(card["sources"]) == 5, "a source the caller can reach was left off the card"
    assert card["named"] == 1
    assert card["prompt"] == PROMPT
    assert asked == [], "the assistant was asked to build against a store nobody had chosen"
    assert _bindings(client) == [], "the card recorded a Binding nobody had clicked"


def test_a_request_naming_a_store_by_its_own_name_asks_about_it_first(
        tmp_path: Path, monkeypatch):
    """A store is named by its own name as well as by the word for what it is.

    `reporting-replica` is no kind of warehouse to a word list, and it is the only thing this
    request could mean. So the listing is read for names before the words are: the row the person
    named leads the card, and the ordering is the whole of what the match buys — every other store
    they can reach is still there, because being wrong about the order costs a glance and dropping
    a row costs them the store they meant.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)

    card = _card(client.post("/api/project/build/stream", json={
        "prompt": "chart the daily calls sitting in reporting-replica"}).text)

    assert [s["name"] for s in card["sources"]] == [
        "reporting-replica", "Snowflake-Data-Warehouse", "test", "AWS_MSSQL", "billing-oracle"]
    assert card["named"] == 1


def test_a_card_that_named_nothing_says_so_rather_than_recommending_its_first_row(
        tmp_path: Path, monkeypatch):
    """Where the request named no store, no store is put forward as the answer.

    "The warehouse" says what kind of thing to look for and not which one, so the order on the card
    is the listing's own — and a first row drawn as the recommended one would be a recommendation
    made out of nothing, in the one place this feature has no evidence at all. The count says which
    card this is, so the surface can tell an answer from a list.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)

    card = _card(client.post("/api/project/build/stream", json={
        "prompt": "chart last quarter's premiums from the warehouse"}).text)

    assert card["named"] == 0
    assert [s["name"] for s in card["sources"]] == [
        "Snowflake-Data-Warehouse", "test", "AWS_MSSQL", "reporting-replica", "billing-oracle"]


def test_a_caller_with_exactly_one_data_source_is_still_asked(tmp_path: Path, monkeypatch):
    """Two confirmations, even where the first one looks like it has a single answer.

    Using the only one silently is the same inference through a side door, and it is the kind that
    hides: the app would read the store nobody named, and the day a second Data Source appears the
    behaviour changes under a person who never chose the first.
    """
    only = DataSource("ds-dwh", "Snowflake-Data-Warehouse", "Snowflake", "Shared", None, True,
                      connector_type="SnowflakeConfig")
    orch = _orch(tmp_path, FakeResourceProvider(data_sources=[only]))
    client = _client(orch, monkeypatch)
    asked = []
    orch._build_stream = lambda *a, **k: asked.append(1) or iter([])  # type: ignore[method-assign]

    card = _card(_build(client))

    assert [s["id"] for s in card["sources"]] == ["ds-dwh"]
    assert asked == []
    assert _bindings(client) == []


def test_a_caller_with_no_data_sources_at_all_is_told_so_in_a_plain_sentence(
        tmp_path: Path, monkeypatch):
    """Said out loud, rather than discovered halfway through a dashboard of invented rows.

    The card is the same card with nothing on it: the sentence names what is missing and what to do
    about it, and the one button left is the way past a question this request may not have been
    asking.
    """
    orch = _orch(tmp_path, FakeResourceProvider(data_sources=[]))
    client = _client(orch, monkeypatch)
    asked = []
    orch._build_stream = lambda *a, **k: asked.append(1) or iter([])  # type: ignore[method-assign]

    card = _card(_build(client))

    assert card["sources"] == []
    assert card["message"] == (
        "You don't have any Data Sources yet. Add one in Domino, or continue without data."
    )
    assert asked == []


def test_a_request_about_no_store_at_all_builds_exactly_as_it_did(tmp_path: Path, monkeypatch):
    """The gate has to be invisible to everybody it is not for.

    Most requests in most Projects name no store and want none, and a card in front of those would
    be this gate taxing every build for the sake of the few it helps. The words that reach it are
    words that name a store — never "data", never "table", which is ordinary app-building English.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]

    body = client.post("/api/project/build/stream", json={
        "prompt": "build me a to-do list app that saves its data as I type"}).text

    assert "source-candidates" not in body
    assert built == [1]


def test_one_word_out_of_a_stores_name_is_not_a_request_about_that_store(
        tmp_path: Path, monkeypatch):
    """A name is matched whole, which is what makes a name safe to trigger on.

    `billing-oracle` is a real store and "add a billing page" is a real request about a screen, and
    a matcher that read one word of the first in the second would put a question about warehouses
    in front of everybody who ever builds a billing page. So every distinctive word of the name has
    to be there — a request that says all of them is naming that store, and a request that says one
    of them is speaking English.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]

    body = client.post("/api/project/build/stream",
                       json={"prompt": "add auth, orgs and a billing page"}).text

    assert "source-candidates" not in body
    assert built == [1]


def test_a_store_named_out_of_ordinary_words_is_not_reached_by_one_of_them(
        tmp_path: Path, monkeypatch):
    """`test` is a real Data Source name and "a test page" is a real request about a screen.

    A name made entirely of words that name any store is kept as a handle elsewhere, so that a
    source somebody called `test` can still be named once they have bound it (#183). Here that
    fallback would hand the words back to the gate the short word list exists to keep them out of.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]

    body = client.post("/api/project/build/stream",
                       json={"prompt": "add a test page for the login flow"}).text

    assert "source-candidates" not in body
    assert built == [1]


def test_an_app_that_already_records_a_data_source_is_not_asked_which(tmp_path: Path, monkeypatch):
    """One question, asked once. A recorded Data Source is a person who has answered this."""
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": ["GONG__CALLS"]}}
    client = _client(orch, monkeypatch)
    client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})

    body = _build(client)

    assert "source-candidates" not in body
    # And the question after it is the one that gets asked, which is #183's card.
    assert "table-candidates" in body


# ---- the click ---------------------------------------------------------------------------------


def test_picking_one_records_it_and_the_replay_lands_in_the_table_search(
        tmp_path: Path, monkeypatch):
    """Both halves of the point: the click writes the ordinary Binding, and the turn goes on.

    Goes on rather than ends — a person who has answered "which store" is one question further into
    the request they made, not back at the composer with it. What the replay meets is #183's card,
    against the store they just named, and the record it writes is the record the panel's own row
    writes.
    """
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": ["GONG__CALLS", "DIM_ACCOUNT"]}}
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    _card(_build(client))

    res = client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})
    assert res.status_code == 200, res.text
    replay = _build(client, chosenSource="ds-dwh")

    rows = _bindings(client)
    assert [(r["id"], r.get("table", "")) for r in rows] == [("ds-dwh", "")]
    tables = [f for f in _frames(replay) if f.get("type") == "table-candidates"]
    assert len(tables) == 1 and tables[0]["sourceId"] == "ds-dwh"
    assert built == [], "the assistant was asked to build before a table was chosen"


def test_the_pick_is_taken_as_the_answer_and_not_checked_against_the_prose(
        tmp_path: Path, monkeypatch):
    """Somebody who clicked one row under a request naming another chose the row.

    The prose match is a heuristic and the click is not. Here the request names no store this
    listing carries — it says "warehouse", which is what got it the card — and the store picked off
    that card is one no word in the request reaches. Matching would find nothing and the search
    would never run, which would make the card a question whose answer did nothing.
    """
    orch = _orch(tmp_path)
    orch._resources.tree["ds-test"] = {"SANDBOX": {"PUBLIC": ["GONG__CALLS"]}}
    client = _client(orch, monkeypatch)
    prompt = "a dashboard of daily gong calls from the warehouse"
    client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-test"})

    replay = client.post("/api/project/build/stream",
                         json={"prompt": prompt, "chosenSource": "ds-test"}).text
    without = client.post("/api/project/build/stream", json={"prompt": prompt}).text

    card = [f for f in _frames(replay) if f.get("type") == "table-candidates"]
    assert len(card) == 1 and card[0]["sourceId"] == "ds-test"
    # The same prompt, the same records, and no pick: nothing in the words reaches `test`, so the
    # search does not run. That is what makes the assertion above about the click and not the prose.
    assert "table-candidates" not in without


def test_the_replayed_turn_says_which_store_was_picked_rather_than_asking_twice(
        tmp_path: Path, monkeypatch):
    """The transcript reads as a conversation: a request, a question, an answer, a question.

    A click is not a second typing of the request. The request is already a bubble above the card,
    and repeating it there would say the person asked for the same thing twice — which is what the
    three offers beside this one avoid with their own short line.
    """
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": ["GONG__CALLS"]}}
    client = _client(orch, monkeypatch)
    _card(_build(client))
    client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})

    _build(client, chosenSource="ds-dwh")

    assert _users(client) == [PROMPT, "Use Snowflake-Data-Warehouse."]


def test_building_without_one_answers_the_card_rather_than_meeting_it_again(
        tmp_path: Path, monkeypatch):
    """The way past a question this request was never asking.

    The words that reach this card are a heuristic, so being wrong has to cost a click. Answered
    rather than skipped, for the reason the reset offer's third button is: without it the same
    words meet the same card on the next turn, and the turn after that.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]

    body = _build(client, skipSourceGate=True)

    assert "source-candidates" not in body
    assert built == [1]
    assert _bindings(client) == [], "building without a store recorded one anyway"


def test_the_card_names_the_store_the_request_named_back_to_them(tmp_path: Path, monkeypatch):
    """Naming one store gets it named back, because the highlight cannot say it on its own.

    The first row is already the filled button here and only here — the surface draws it that way
    off `named` — but a filled button is mute about why it is filled. "You named this" and "this
    ranked first" render identically, so somebody who spent an `@mention` on the answer reads a
    neutral list and concludes Sage did not hear them (#206, reported verbatim: "it felt repetitive
    since I already had at-mentioned it in the prompt").

    The card still has to be answered — ADR-0010 is untouched and the sentence says why rather than
    apologising for it. What changes is that the reason is on screen instead of inferred.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)

    card = _card(client.post("/api/project/build/stream", json={
        "prompt": "chart the daily calls sitting in reporting-replica"}).text)

    assert card["named"] == 1
    assert "reporting-replica" in card["message"]
    assert "Confirm it, then pick a Table" in card["message"]


def test_a_card_naming_two_stores_says_they_are_first_rather_than_naming_one(
        tmp_path: Path, monkeypatch):
    """Two named stores leave no single one to name back, so the card says what the order means.

    Naming one of them would be this code choosing after all, in the one case where the request
    gave it two answers and no way to rank them. What is still true — and still worth saying — is
    that the rows in front came out of their own sentence rather than out of Sage's ordering.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)

    card = _card(client.post("/api/project/build/stream", json={
        "prompt": "chart reporting-replica against billing-oracle"}).text)

    assert card["named"] == 2
    assert "named more than one" in card["message"]
    assert "reporting-replica" not in card["message"]


def test_a_card_that_named_nothing_still_asks_the_plain_question(tmp_path: Path, monkeypatch):
    """Unchanged where nothing was named — there is nothing to name back and nothing to explain."""
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)

    card = _card(client.post("/api/project/build/stream", json={
        "prompt": "chart last quarter's premiums from the warehouse"}).text)

    assert card["named"] == 0
    assert card["message"].startswith("Which Data Source should this Built App read?")
