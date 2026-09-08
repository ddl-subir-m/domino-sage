"""What the transcript says a table pick was (#208).

The click on a candidate card answered one question — which table — and the turn it started wrote
`Build it.` in the person's own voice. Two faults in one line, and the second is the one that
matters: the table the card existed to settle appears nowhere in the record, and a sentence nobody
typed is attributed to the person who did not type it.

The record is the whole point of the card, so it is pinned at the seam where it is composed: what
the turn hands `_build_stream` as `user_text` is what `.sage/history.jsonl` keeps and what the
transcript reads back. The Data Source pick beside it already writes `Use <name>.` there (#185), and
these tests hold that one still, along with the two cards that say `Build it.` truthfully.

The live half is the harness at the bottom. The bubble is drawn by `store.js` before the server has
answered — the server's `user` event is never streamed back — so a fix that changed only Python
would leave the person looking at `Build it.` until they reloaded the page.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sage.orchestrator.app as appmod
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE
from sage.resources.provider import FakeResourceProvider
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
    # Two schemas holding a table each, because the schema is the distinction the card exists to
    # settle: `MARTS.GONG__CALLS` and `STAGING.STG_GONG__CALLS` are two different apps.
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": ["GONG__CALLS"],
                                              "STAGING": ["STG_GONG__CALLS"]}}
    return orch


def _client(orch: Orchestrator, monkeypatch, said: list[str | None]) -> TestClient:
    """The app, with the build itself replaced by what it was told the person said.

    `user_text` rather than the history file: it is the one argument this ticket is about, and
    reading it here means the assertions do not depend on a real agent turn running to the end
    before the sentence it was handed is written down.
    """
    monkeypatch.setattr(appmod, "orchestrator", orch)
    orch._build_stream = (  # type: ignore[method-assign]
        lambda *a, **k: said.append(k.get("user_text")) or iter([]))
    return TestClient(appmod.control_app)


def _build(client: TestClient, **body) -> str:
    return client.post("/api/project/build/stream", json={"prompt": PROMPT, **body}).text


def _bind(client: TestClient) -> None:
    """Record the store, which is what stops the Data Source card asking about it (#185). Every
    test here is about the card one level down, so all of them start past that question."""
    res = client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})
    assert res.status_code == 200, res.text


def _pick(client: TestClient, schema: str, table: str) -> None:
    """And click a candidate on it — the write half of the card's two-act click."""
    _bind(client)
    res = client.post("/api/bindings/data_source/ds-dwh/candidate",
                      json={"database": "DWH", "schema": schema, "table": table})
    assert res.status_code == 200, res.text


def test_the_replayed_turn_says_which_table_was_picked(tmp_path: Path, monkeypatch):
    """The line the click leaves names the table it chose.

    `Build it.` was true of the button and false about everything the click meant: the table is what
    the card asked for, what the person answered, and what decides the app that gets built.
    """
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _pick(client, "MARTS", "GONG__CALLS")
    _build(client, skipTableGate=True)

    assert said == ["Use MARTS.GONG__CALLS."]


def test_the_table_is_named_with_the_schema_that_tells_it_from_the_other_one(
        tmp_path: Path, monkeypatch):
    """Qualified, because the bare name is the half that does not decide anything.

    `GONG__CALLS` and `STG_GONG__CALLS` are one search's two candidates and the live failure this
    came from shipped against neither of them. A record saying only which table name was clicked
    leaves the reader to guess which of the two layers the app reads.
    """
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _pick(client, "STAGING", "STG_GONG__CALLS")
    _build(client, skipTableGate=True)

    assert said == ["Use STAGING.STG_GONG__CALLS."]


def test_a_pick_whose_record_never_landed_claims_no_table(tmp_path: Path, monkeypatch):
    """No record, no sentence about one — `_picked_source_text`'s rule, for the same reason.

    The card's click writes before it builds, so this is the write having failed: the store is
    recorded and no table on it is. What the turn must not do is put a table on the transcript that
    nothing depends on — it says what it would have said for any other card being answered, and the
    record stays empty because it is empty.
    """
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _bind(client)
    _build(client, skipTableGate=True)

    assert said == ["Build it."]


def test_a_data_source_pick_still_says_which_store(tmp_path: Path, monkeypatch):
    """The card one level up is untouched (#185). It answers a different question — which store —
    and it already answers it in its own words."""
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _bind(client)
    # The store is bound and unscoped, so this replay walks into the table card rather than a build:
    # the sentence is the one that card writes for the turn that drew it, which is why it is read
    # off the transcript here and off `user_text` everywhere else.
    _build(client, chosenSource="ds-dwh")

    rows = client.get("/api/project/history").json()["history"]
    assert [r.get("text") for r in rows if r.get("type") == "user"] == [
        "Use Snowflake-Data-Warehouse."]


def test_the_other_cards_still_say_build_it(tmp_path: Path, monkeypatch):
    """`Build it.` is accurate for the offers whose button says exactly that, and it stays.

    Reset and incoming changes ask whether to build anyway; the person clicked "build anyway". They
    are not picks, so there is nothing else for the line to name.

    A table IS recorded here, which is the point of picking one first: the sentence follows the card
    that was answered this turn and not whatever the manifest happens to hold.
    """
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _pick(client, "MARTS", "GONG__CALLS")
    _build(client, skipResetGate=True)
    _build(client, skipIncomingGate=True)

    assert said == ["Build it.", "Build it."]


def test_a_typed_prompt_is_still_echoed_once(tmp_path: Path, monkeypatch):
    """Nobody clicked anything, so the turn writes the sentence they typed and nothing beside it.
    `user_text` of None is what leaves the ordinary echo alone."""
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _pick(client, "MARTS", "GONG__CALLS")
    _build(client)

    assert said == [None]


# ---- the live half ------------------------------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "table_candidate_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

HISTORY = [
    {"type": "user", "text": PROMPT},
    {"type": "table-candidates", "prompt": PROMPT,
     "message": "Sage read what **Snowflake-Data-Warehouse** holds.",
     "sourceId": "ds-dwh", "sourceName": "Snowflake-Data-Warehouse",
     "groups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]}],
     "allGroups": [{"database": "DWH", "schema": "MARTS", "tables": ["GONG__CALLS"]}],
     "total": 1, "matched": 1},
    {"type": "done", "ok": False, "decision": "table candidates"},
]


@needs_node
def test_the_bubble_the_click_draws_is_the_line_the_server_records():
    """What the person sees the moment they click, which is where they saw `Build it.`.

    The server's `user` event is written to the transcript and never streamed, so this bubble is
    `store.js`'s own and the two ends have to write the same sentence — otherwise the transcript
    changes under a page reload, which is the one thing a record of a decision must not do.
    """
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"history": HISTORY, "prompt": PROMPT, "answered": {}}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    said = json.loads(out.stdout.strip().splitlines()[-1])

    assert said["bubbles"] == [PROMPT, "Use MARTS.GONG__CALLS."]
