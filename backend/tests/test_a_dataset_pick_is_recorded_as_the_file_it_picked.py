"""What the transcript says a Dataset file pick was (#196, ADR-0039 — #208 one card over).

`_picked_source_text` names the store a click chose and `_picked_table_text` names the table, and
both exist because `Build it.` threw the choice away and put a sentence nobody typed in the person's
own voice. The Dataset card shipped after both and got neither. So the click that decides what the
app reads — the whole point of the card — left a record naming nothing, and the card itself retires
on reload: the rows go, `live` is false, and the only thing left is `Build it.` The file the person
picked survives nowhere in the conversation.

WHY THIS ONE READS THE REQUEST, where the two beside it deliberately do not. Their clicks write a
Binding, which is a keyed record the server can look up and be sure of. An attach is not: the
manifest is an ordered list of one entry PER FILE (ADR-0029), so a folder click of thirty-one files
appends thirty-one entries and "the newest" would name whichever file sorted last — a sentence about
a file the person never picked, which is the fault this fixes, restated. The label therefore comes
from the click, and the manifest is read to CONFIRM it: same "did the write actually land?" property
the other two get for free from the Binding.

The live half is the harness at the bottom. The bubble is drawn by `store.js` before the server has
answered, so a fix in Python alone would leave `Build it.` on screen until the page was reloaded —
and then change under the reload, which is the one thing a record of a decision must not do.
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
from sage.orchestrator.service import Orchestrator, _dataset_entry
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog

PROMPT = "build me a dashboard from the data in revenue_2026"


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


def _client(orch: Orchestrator, monkeypatch, said: list[str | None]) -> TestClient:
    """The app, with the build itself replaced by what it was told the person said.

    `user_text` rather than the history file, for `_picked_table_text`'s reason: it is the one
    argument these tests are about, and reading it here means nothing depends on a real agent turn
    running to the end before the sentence it was handed is written down.
    """
    monkeypatch.setattr(appmod, "orchestrator", orch)
    orch._build_stream = (  # type: ignore[method-assign]
        lambda *a, **k: said.append(k.get("user_text")) or iter([]))
    return TestClient(appmod.control_app)


def _attach(orch: Orchestrator, *files: str) -> None:
    """The manifest record the click writes, seeded directly.

    `attach_file` itself needs a mount or a download, and neither says anything about the sentence
    under test — what the turn reads is the entry, so the entry is what the test writes. One per
    file, which is the shape ADR-0029 fixed and the reason the folder case below needs a rule.
    """
    project = orch.project()
    for f in files:
        project.attached.append(
            _dataset_entry("ds_revenue", "revenue_2026", f, f"public/data/revenue_2026/{f}", 2048))


def _build(client: TestClient, **body) -> str:
    return client.post("/api/project/build/stream", json={"prompt": PROMPT, **body}).text


def test_the_replayed_turn_says_which_file_was_picked(tmp_path: Path, monkeypatch):
    """The line the click leaves names the file it chose.

    `Build it.` was true of the button and false about everything the click meant: the file is what
    the card asked for, what the person answered, and what decides the app that gets built.
    """
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _attach(orch, "calls_daily.csv")
    _build(client, skipDatasetGate=True, datasetPick="calls_daily.csv")

    assert said == ["Use calls_daily.csv."]


def test_a_folder_pick_names_the_folder_and_not_a_file_inside_it(tmp_path: Path, monkeypatch):
    """The folder is the unit of the ACT and the file is the unit of the RECORD (ADR-0029).

    So the manifest cannot answer this on its own: thirty-one entries land and not one of them is
    the thing that was clicked. A sentence naming `raw/2026/01/day_31.csv` would be a record of a
    choice nobody made.
    """
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _attach(orch, "raw/2026/01/day_01.csv", "raw/2026/01/day_31.csv")
    _build(client, skipDatasetGate=True, datasetPick="raw/2026/01")

    assert said == ["Use raw/2026/01."]


def test_a_pick_whose_attach_never_landed_claims_no_file(tmp_path: Path, monkeypatch):
    """No record, no sentence about one — `_picked_source_text`'s rule, for the same reason.

    The card's click attaches before it builds, so this is the attach having failed. What the turn
    must not do is leave a sentence on screen claiming a file is being read when nothing in the tree
    came from it: it says what it would have said for any other card being answered.
    """
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _build(client, skipDatasetGate=True, datasetPick="calls_daily.csv")

    assert said == ["Build it."]


def test_the_way_past_the_card_still_says_build_it(tmp_path: Path, monkeypatch):
    """The other button, which attaches nothing on purpose.

    `Build it.` is accurate here and it stays: the person was asked which file and answered that
    they did not want one. Naming a file would invert the only thing they said.
    """
    orch = _orch(tmp_path)
    said: list[str | None] = []
    client = _client(orch, monkeypatch, said)

    _attach(orch, "calls_daily.csv")
    _build(client, skipDatasetGate=True, datasetDismissed="ds_revenue")

    assert said == ["Build it."]


# ---- the live half ------------------------------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "dataset_files_harness.mjs"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH (it is in the Sage image)"
)

FILE_ROWS = [{"kind": "file", "path": "calls_daily.csv", "size": 2048}]

HISTORY = [
    {"type": "user", "text": PROMPT},
    {"type": "dataset-files", "prompt": PROMPT, "live": True,
     "message": "Nothing in revenue_2026 matched. Pick a file, or continue without attaching one.",
     "datasetId": "ds_revenue", "datasetName": "revenue_2026", "answered": {},
     "rows": FILE_ROWS, "allRows": FILE_ROWS, "total": 1, "listed": 1, "matched": 0},
    {"type": "done", "ok": False, "decision": "dataset files"},
]


def _run(click: dict) -> dict:
    out = subprocess.run(["node", str(_HARNESS)],
                         input=json.dumps({"mode": "build", "history": HISTORY, "prompt": PROMPT,
                                           "answered": {}, "click": click}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_the_bubble_the_click_draws_is_the_line_the_server_records():
    """What the person sees the moment they click, which is where they saw `Build it.`

    The server's `user` event is written to the transcript and never streamed, so this bubble is
    `store.js`'s own and the two ends have to write the same sentence — otherwise the transcript
    changes under a page reload.
    """
    out = _run({"kind": "file", "path": "calls_daily.csv"})

    assert out["bubbles"] == [PROMPT, "Use calls_daily.csv."]
    assert out["replay"]["datasetPick"] == "calls_daily.csv"


@needs_node
def test_the_way_past_draws_no_file_it_did_not_attach():
    """Both ends again: nothing was attached, so neither end may name a file."""
    out = _run({"kind": "past"})

    assert out["bubbles"] == [PROMPT, "Build it."]
    assert not out["replay"].get("datasetPick")
