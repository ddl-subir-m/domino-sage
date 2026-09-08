"""An app that cannot read its data says so, and does not draw the control that would fix it.

THE BUG THIS ENDS, observed live. Somebody bound `Snowflake-Data-Warehouse`, asked for a Gong
dashboard, and approved the plan. No table had been chosen, and the catalog walk had already failed
before it could offer one — so the agent met the unscoped section and built anyway. What shipped was
a dashboard with four metric cards reading 4,498 calls / 1,912 meetings / 86 reps / 342 accounts,
every number invented, each hedged with a caption like "Example layout only"; and two primary
buttons, "Choose a Gong table" and "Review source scope", neither of which had anything to call.

Three separate holes let that through, and each is closed here.

1. The section told the agent that choosing a Scope happens "on this Built App's own surface". It
   meant Sage's panel, beside the conversation. An agent WRITING the Built App reads its own surface
   as the page in front of it, so it put the picker there — which is the dead button, arrived at by
   following the instructions rather than ignoring them (ADR-0021: each scope's door belongs on the
   surface that owns it).

2. "Do not invent rows" was read as "do not CLAIM invented rows are real", and a caption saying
   "example" was taken to settle it. The numbers still render at the size of a measurement.

3. The Dataset half (#195) inherited both gaps: it forbids the claim and says nothing about the
   control, and for a Dataset the template's empty-state rule actively asks for a button.

The fourth is the silence that started it: a walk that died before its first query returned False
with nothing but a log line, so the person was never told the search had been tried.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import sage.orchestrator.app as appmod
from sage.assets.provider import FakeAssetProvider
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE, Binding
from sage.resources.bound_schema import BoundSource, Inside, agents_block
from sage.resources.provider import FakeResourceProvider, ResourceUnavailable
from sage.router.models import ModelCatalog

UNSCOPED = Binding(KIND_DATA_SOURCE, "ds-dwh", "warehouse", "warehouse",
                   None, None, None, "SnowflakeConfig")


def _unscoped_block() -> str:
    """The section an agent meets when a Data Source is bound and no table is chosen."""
    return agents_block([BoundSource(UNSCOPED, [], [], Inside("database", ["DWH"]))],
                        [], 5000, samples=())


# ---- the Data Source half ------------------------------------------------------------------------


def test_the_picker_is_named_as_sages_surface_and_not_the_app_being_built():
    """The sentence that produced the dead button. "This Built App's own surface" is the page the
    agent is writing, and it built the picker into it. The act is Sage's, so the sentence has to
    say Sage."""
    block = _unscoped_block()

    assert "Built App's own surface" not in block
    assert "no tool that picks one" in block


def test_the_agent_is_told_not_to_build_the_picker_into_the_app():
    """Saying whose act it is was not enough: an agent that may not DO the act can still draw the
    button for it, and a button with nothing to call makes the app look broken to whoever opens
    it."""
    block = _unscoped_block()

    assert "dead control" in block
    assert "leave the button out" in block


def test_a_caption_calling_the_numbers_an_example_does_not_license_them():
    """The live app hedged every invented figure — "Example layout only" — and shipped them at the
    size of a measurement. The rule has to name that move, because the agent plainly believed it
    was complying."""
    block = _unscoped_block()

    assert "Do not invent rows" in block
    assert "placeholder" in block and "example" in block


# ---- the Dataset half (#195, ADR-0039) -----------------------------------------------------------


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Building apps in this workspace\n\nTemplate body here.\n")
    return t


def _ready(tmp: Path) -> tuple[Orchestrator, Path]:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
        gateway=object(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", assets=FakeAssetProvider(root=tmp / "mounts"),
    )
    return orch, orch.project(start_preview=False).workspace.path


def _line(ws: Path) -> str:
    return next(ln for ln in (ws / "AGENTS.md").read_text().splitlines()
                if "has no files attached from it" in ln)


def test_a_dataset_with_nothing_attached_forbids_the_button_too(tmp_path: Path):
    """The Dataset half had the same two gaps, and one it did not share: a Dataset holding no files
    IS an empty collection, so the template's empty-state rule asks for a button on its own terms.
    Nothing contradicted it."""
    orch, ws = _ready(tmp_path)
    orch.bind_dataset(next(a["id"] for a in orch.list_assets() if a["name"] == "sales_2026"))

    line = _line(ws)

    assert "Do not invent rows for it" in line
    assert "placeholder" in line
    assert "nothing to call" in line


# ---- the silence in front of all of it -----------------------------------------------------------


def _orch(tmp_path: Path) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code", template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage", resources=FakeResourceProvider(),
    )
    orch.project(start_preview=False)
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": ["GONG__CALLS"]}}
    return orch


def test_a_walk_that_dies_before_its_first_query_says_so_instead_of_going_quiet(
        tmp_path: Path, monkeypatch):
    """The failure that started the live build. Every walk that dies AFTER streaming begins already
    takes its own card back with a message; this one returned False on a log line, so the turn fell
    through to a build and the person was never told the search had been tried at all.

    It still falls through — a store that will not answer cannot be put on a card, and that is
    settled (#183). What changes is that it stops being invisible.

    Failed at the store, not at the method under test: what a suspended warehouse actually does is
    refuse `SHOW DATABASES`, and the refusals Sage decides for itself raise the same class one
    level up. Patching the method would have proved the frame goes out without proving it goes out
    for the right one.
    """
    orch = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    orch._resources.list_databases = (  # type: ignore[method-assign]
        lambda source: (_ for _ in ()).throw(ResourceUnavailable("warehouse suspended")))
    client = TestClient(appmod.control_app)
    client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})

    body = client.post("/api/project/build/stream", json={
        "prompt": "build me a dashboard from the gong data in Snowflake-Data-Warehouse"}).text
    frames = [json.loads(ln[6:]) for ln in body.splitlines() if ln.startswith("data: ")]

    ended = [f for f in frames if f.get("type") == "table-search-ended"]
    assert ended, f"the walk failed in silence; frames were {[f.get('type') for f in frames]}"
    assert "couldn't read" in ended[0]["message"]
    assert built == [1], "the turn must still fall through to the build it would have run"


def test_a_refusal_sage_decided_for_itself_still_says_nothing(tmp_path: Path, monkeypatch):
    """The other half of the distinction, and the reason `StoreWentQuiet` is its own class.

    A source holding more databases than one search walks is refused by counting — nothing failed,
    nothing was read, and the turn reads exactly as it did before streaming existed (#186). Saying
    "reading it now" and taking it back would be a lie about a store that answered fine. Only the
    store going quiet earns the interruption.
    """
    orch = _orch(tmp_path)
    orch._resources.tree["ds-dwh"] = {f"DB_{n}": {"PUBLIC": ["GONG__CALLS"]} for n in range(5)}
    monkeypatch.setattr(appmod, "orchestrator", orch)
    built = []
    orch._build_stream = lambda *a, **k: built.append(1) or iter([])  # type: ignore[method-assign]
    client = TestClient(appmod.control_app)
    client.post("/api/bindings", json={"kind": KIND_DATA_SOURCE, "id": "ds-dwh"})

    body = client.post("/api/project/build/stream", json={
        "prompt": "build me a dashboard from the gong data in Snowflake-Data-Warehouse"}).text

    assert "table-search" not in body
    assert built == [1]
