"""One database that will not answer must not discard the databases that did (#191, ADR-0038).

THE BUG THIS ENDS is a whole search thrown away by one bad database. The walk let
`list_database_tables` raise straight through, both callers caught it around the entire loop, and
the tables already read from every other database went with it — the turn fell through and the
person got no card at all rather than the candidates Sage had already found.

Databricks is the case that made it visible: a Unity Catalog workspace lists `hive_metastore`
beside the real catalogs, `hive_metastore` has no `information_schema`, so the walk errored there
every time. The bug is not Databricks's, which is why nothing below is a Databricks test and why
none of it needs a warehouse. Any Trino catalog whose backing store is down does the same, and so
does any store holding one database the proxy user cannot read.

WHY THIS IS NOT THE DATABASE BUDGET REVERSED. `_databases_to_walk` refuses rather than truncates
when a store holds more databases than one search reads, and the reason it gives is that the card
would say what it found, the table wanted would be in the database nobody walked, and "no name
matched" would be a lie about the warehouse. The objection there is the LIE, not the partiality.
A card that NAMES the database it could not read tells no lie — the person sees the gap and can
act on it — so skipping and saying so honours that rule rather than reversing it. The budget
refusal itself is untouched, and it fires before any query runs, so nothing has been read and
there is nothing partial to report.

Both modes are driven here on purpose. The skip lives in `_database_candidates`, which Build and
Chat share, so the mode somebody happens to be standing in cannot decide whether they are told a
database went unread.
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
from sage.resources.provider import FakeResourceProvider, ResourceUnavailable
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

PROMPT = "build me a dashboard of daily gong calls from Snowflake"
# The same question without the word that makes Chat offer a handoff instead of answering.
CHAT_PROMPT = "chart me the daily gong calls from Snowflake"


class ScriptedGateway:
    """CHAT, so the handoff classifier does not turn the Chat turn into a Build offer."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "CHAT"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text("{}")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=ScriptedGateway(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
        resources=FakeResourceProvider(),
        opencode_client=FakeOpenCode(tmp_path / "mnt" / "code", [Turn(text="Here it is.")]),
        feedback=OkFeedback(),
    )
    orch.project(start_preview=False)
    return orch


def _client(orch: Orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app)


def _bind(client: TestClient) -> None:
    res = client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})
    assert res.status_code == 200, res.text


def _frames(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _card(text: str) -> dict:
    cards = [f for f in _frames(text) if f.get("type") == "table-candidates"]
    assert len(cards) == 1, f"expected one candidate card, got {[f['type'] for f in _frames(text)]}"
    return cards[0]


def _three_databases(orch: Orchestrator) -> None:
    """One readable database and two that stand in for `hive_metastore`.

    The wanted table is in the readable one, which is the whole point: the search HAD the answer
    and used to throw it away. Two unreadable databases rather than one, so the sentence naming
    them is exercised in the number that needs a conjunction.
    """
    orch._resources.tree["ds-dwh"] = {
        "DWH": {"MARTS": ["GONG__CALLS", "DIM_ACCOUNT"]},
        "hive_metastore": {"default": ["LEGACY_CALLS"]},
        "sandbox": {"PUBLIC": ["SCRATCH"]},
    }


def _only_dwh_answers(orch: Orchestrator) -> None:
    walk = orch._resources.list_database_tables
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: walk(source, database) if database == "DWH" else (
            _ for _ in ()).throw(ResourceUnavailable("no information_schema in this catalog")))


def _thread_with_source(orch: Orchestrator) -> str:
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "data_source", "name": "Snowflake-Data-Warehouse",
                                  "bindingKey": ["data_source", "ds-dwh"]})
    return tid


# ---- Build ------------------------------------------------------------------------------------


def test_the_candidates_from_the_databases_that_answered_still_reach_the_card(
        tmp_path: Path, monkeypatch):
    """The whole ticket in one assertion: the card exists, and it holds what was read.

    Driven at the HTTP seam rather than over the helper, because the claim is about what the
    person ends up looking at. The gate returning True with candidates in hand is only the answer
    if the card carrying them actually leaves the turn.
    """
    orch = _orch(tmp_path)
    _three_databases(orch)
    _only_dwh_answers(orch)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    _bind(client)

    card = _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert "GONG__CALLS" in {t for g in card["allGroups"] for t in g["tables"]}
    assert card["matched"] > 0
    assert built == [], "the turn fell through to a build with candidates already in hand"


def test_the_card_names_every_database_it_could_not_read(tmp_path: Path, monkeypatch):
    """The gap is visible rather than silent, which is what makes the partial card honest.

    A short list with no account of why it is short is the failure #182 refused, and it is the
    only thing separating this from the truncation the database budget still will not do.
    """
    orch = _orch(tmp_path)
    _three_databases(orch)
    _only_dwh_answers(orch)
    client = _client(orch, monkeypatch)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    _bind(client)

    card = _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert card["message"].endswith(
        "Sage could not read hive_metastore and sandbox, so any Tables they hold are not on "
        "this list.")


def test_a_walk_that_read_everything_says_nothing_about_databases_it_skipped(
        tmp_path: Path, monkeypatch):
    """The ordinary search is unchanged, sentence for sentence.

    A note that appears when there is nothing to note trains a person to stop reading the card,
    which costs more than it ever buys on the walk that did go wrong.
    """
    orch = _orch(tmp_path)
    _three_databases(orch)
    client = _client(orch, monkeypatch)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    _bind(client)

    card = _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert "could not read" not in card["message"]
    assert "LEGACY_CALLS" in {t for g in card["allGroups"] for t in g["tables"]}


def test_a_walk_where_every_database_fails_still_offers_no_card(tmp_path: Path, monkeypatch):
    """No answer is not a partial answer, so this one still falls through as it did.

    Nothing was read, so a card would carry nothing true — not the candidates it does not have,
    and not "no name matched" either, which is a claim about names nobody ever saw.
    """
    orch = _orch(tmp_path)
    _three_databases(orch)
    client = _client(orch, monkeypatch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: (_ for _ in ()).throw(ResourceUnavailable("nothing answers")))
    _bind(client)

    frames = _frames(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert "table-candidates" not in {f["type"] for f in frames}
    assert built == [1]


def test_a_database_that_failed_is_read_again_on_the_next_question(tmp_path: Path, monkeypatch):
    """A failure is not cached, on the same ground the database-budget refusal is not remembered.

    Every other cached listing costs a click when it drifts. This one costs the search: a database
    that happened to be down when the session started would otherwise stay skipped until the
    workspace restarts, with no way back but a restart.
    """
    orch = _orch(tmp_path)
    _three_databases(orch)
    client = _client(orch, monkeypatch)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    _bind(client)
    _only_dwh_answers(orch)
    client.post("/api/project/build/stream", json={"prompt": PROMPT})

    walked: list[str] = []
    walk = FakeResourceProvider.list_database_tables
    orch._resources.list_database_tables = (  # type: ignore[method-assign]
        lambda source, database: walked.append(database) or walk(  # type: ignore[func-returns-value]
            orch._resources, source, database))
    card = _card(client.post("/api/project/build/stream", json={"prompt": PROMPT}).text)

    assert walked == ["hive_metastore", "sandbox"], "DWH was re-read, or the skip was remembered"
    assert "LEGACY_CALLS" in {t for g in card["allGroups"] for t in g["tables"]}


# ---- Chat -------------------------------------------------------------------------------------


def test_chat_keeps_the_partial_walk_and_names_the_gap_in_the_same_words(
        tmp_path: Path, monkeypatch):
    """Both paths get this through the shared helper, so neither can drift from the other.

    Asserting the same sentence rather than merely "some note" is the point: two cards about the
    same warehouse that describe the same gap differently is the disagreement the shared helper
    exists to prevent.
    """
    orch = _orch(tmp_path)
    _three_databases(orch)
    _only_dwh_answers(orch)
    client = _client(orch, monkeypatch)
    tid = _thread_with_source(orch)

    card = _card(client.post(f"/api/threads/{tid}/chat/stream",
                             json={"prompt": CHAT_PROMPT}).text)

    assert card["threadId"] == tid
    assert "GONG__CALLS" in {t for g in card["allGroups"] for t in g["tables"]}
    assert card["message"].endswith(
        "Sage could not read hive_metastore and sandbox, so any Tables they hold are not on "
        "this list.")
