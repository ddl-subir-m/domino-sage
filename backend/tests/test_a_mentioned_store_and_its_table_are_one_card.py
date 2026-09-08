"""One card, not two, when the composer's own menu already named the store (#206).

The live complaint: `build me a dashboard from the gong table in @Snowflake-Data-Warehouse` drew a
list of every reachable Data Source with that one highlighted, then a list of tables, then a build.
Four interactions, the first of which asked for something the `@mention` had already supplied —
reported verbatim as *"it felt repetitive since I already had at-mentioned it in the prompt."*

What is NOT folded away is the declaration. The click still records the Binding; it records the
Scope in the same act, which is what makes one card enough (ADR-0010, ADR-0038). And the merge is
keyed on the MENTION rather than on `named`, because `named` also counts stores matched on their
name in prose and a prose match can be the wrong store — see the two tests at the bottom.
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

PROMPT = "build me a dashboard from the gong table"
MENTION = [{"kind": KIND_DATA_SOURCE, "id": "ds-dwh"}]


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
    orch._resources.tree["ds-dwh"] = {
        "DWH": {
            "MARTS": ["GONG__CALLS", "FCT_USAGE_DAILY"],
            "STAGING": ["STG_GONG__CALLS"],
        },
    }
    return orch


def _client(orch: Orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setattr(appmod, "orchestrator", orch)
    orch._build_stream = lambda *a, **k: iter([])  # type: ignore[method-assign]
    return TestClient(appmod.control_app)


def _frames(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _build(client: TestClient, **body) -> list[dict]:
    return _frames(client.post("/api/project/build/stream",
                               json={"prompt": PROMPT, **body}).text)


def _kinds(frames: list[dict]) -> list[str]:
    return [f.get("type") for f in frames]


def _sources(client: TestClient) -> list[dict]:
    return [b for b in client.get("/api/bindings").json()["bindings"]
            if b["kind"] == KIND_DATA_SOURCE]


def test_a_mentioned_store_goes_straight_to_its_tables(tmp_path: Path, monkeypatch):
    """No Data Source card at all: the mention answered that half, so the card asks the other.

    The store list is not merely reordered or pre-selected — it is not drawn. A card that asks a
    question already answered is the whole complaint, and highlighting a row inside it does not
    stop it being asked.
    """
    client = _client(_orch(tmp_path), monkeypatch)

    frames = _build(client, resources=MENTION)

    assert "source-candidates" not in _kinds(frames)
    cards = [f for f in frames if f.get("type") == "table-candidates"]
    assert len(cards) == 1
    assert cards[0]["sourceId"] == "ds-dwh"
    assert cards[0]["bindFirst"] is True


def test_the_merged_card_binds_nothing_until_it_is_answered(tmp_path: Path, monkeypatch):
    """Drawing the card reads the store's catalog. Reading is not choosing (ADR-0038).

    This is the guarantee the merge could plausibly have cost, so it is pinned on its own: the walk
    that fills the card happens against a store that is still not recorded anywhere, and stays that
    way until somebody clicks.
    """
    orch = _orch(tmp_path)
    client = _client(orch, monkeypatch)
    built: list[int] = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]

    _build(client, resources=MENTION)

    assert _sources(client) == []
    assert built == []


def test_the_click_records_the_store_and_the_table_together(tmp_path: Path, monkeypatch):
    """One click, both halves, through the door that is allowed to write both."""
    client = _client(_orch(tmp_path), monkeypatch)
    _build(client, resources=MENTION)

    res = client.post("/api/bindings/data_source/ds-dwh/candidate", json={
        "database": "DWH", "schema": "MARTS", "table": "GONG__CALLS", "bindFirst": True})

    assert res.status_code == 200, res.text
    recorded = _sources(client)
    assert len(recorded) == 1
    assert (recorded[0]["id"], recorded[0]["schema"], recorded[0]["table"]) == (
        "ds-dwh", "MARTS", "GONG__CALLS")


def test_the_ordinary_door_still_refuses_a_scope_with_no_binding_under_it(
        tmp_path: Path, monkeypatch):
    """`bindFirst` is a claim the card makes, never something the server infers from an empty
    manifest.

    ADR-0021 split binding from scoping so that a Scope write could not record a dependency as a
    side effect of narrowing one. If this route read "nothing is bound" as "so bind it", that guard
    would be gone for every caller, not just the merged card's click.
    """
    client = _client(_orch(tmp_path), monkeypatch)

    res = client.post("/api/bindings/data_source/ds-dwh/candidate", json={
        "database": "DWH", "schema": "MARTS", "table": "GONG__CALLS"})

    assert res.status_code == 404
    assert _sources(client) == []


def test_a_store_matched_from_prose_still_gets_both_cards(tmp_path: Path, monkeypatch):
    """The merge is keyed on the mention, and this is why.

    `named` also counts a store matched on its own name in prose, and that match can be wrong. Were
    the merge keyed on it, a guessed store would sit behind a click on a table with no card left to
    correct it on — trading the repetition this fixes for a wrong Binding, which is worse. An id out
    of the composer's menu cannot be the wrong store; a word in a sentence can.
    """
    client = _client(_orch(tmp_path), monkeypatch)

    frames = _build(client, prompt="chart the daily calls in reporting-replica")

    assert "source-candidates" in _kinds(frames)
    assert "table-candidates" not in _kinds(frames)


def test_a_store_that_cannot_be_walked_falls_back_to_the_store_list(tmp_path: Path, monkeypatch):
    """A search with nothing to offer leaves the plain question, not silence.

    The merged card is an attempt: the connector may not be walkable in one query, the store may
    stop answering, it may hold nothing. Every one of those leaves the person still needing to say
    which store — and one of them, a store that cannot be read, is a reason to show the others they
    can reach.
    """
    unwalkable = DataSource("ds-dwh", "Snowflake-Data-Warehouse", "Snowflake", "Shared", None,
                            True, connector_type="")
    other = DataSource("ds-2", "billing-oracle", "Oracle", "Shared", None, True,
                       connector_type="OracleConfig")
    client = _client(_orch(tmp_path, FakeResourceProvider(data_sources=[unwalkable, other])),
                     monkeypatch)

    frames = _build(client, resources=MENTION)

    assert "table-candidates" not in _kinds(frames)
    assert "source-candidates" in _kinds(frames)
