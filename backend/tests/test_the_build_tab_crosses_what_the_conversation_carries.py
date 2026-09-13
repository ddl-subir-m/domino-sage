"""The Build tab's own door for a Conversation's chips (#275).

The state this fixes: chips added in Chat are still drawn over the Build composer, and the selected
app holds none of them — no Binding, no bytes. The first Build turn names one, is refused, and the
person attaches the file a second time by hand.

Two doors open onto a Built App and only one of them crossed anything. This is the second: the same
per-chip crossing the handoff sheet makes — a Binding, a Dataset file, an Upload — and none of the
rest of it.

THE ALTERNATIVE THIS RULES OUT: calling `_write_crossing` as it stands. It is documented as "two
doors call it", so a third would be in keeping — but it also writes `.sage/handoff.md` and UNLINKS
`.sage/handoff-transcript.md` when `include.transcript` is false. This door would then silently
destroy the transcript a real handoff wrote, for a handoff it is not part of.

Crossing is deliberately not all-or-nothing (`_cross_chat_upload` returns a refusal, and
`_bind_from_handoff` records an unresolved Binding), so every chip reports for itself. Nothing may
claim a chip moved that did not.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.assets.provider import FakeAssetProvider
from sage.orchestrator import app as appmod
from sage.orchestrator.service import Orchestrator, TurnBusy
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    return t


def _orch(tmp: Path, assets=None) -> Orchestrator:
    return Orchestrator(workspace_dir=tmp / "mnt" / "code", template=_template(tmp),
                        gateway=object(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", assets=assets)


def _a_conversation_with_three_chips(orch: Orchestrator) -> str:
    """One chip of each kind the crossing knows how to move.

    A Data Source that will not resolve, on purpose: an unresolved Binding is still a Binding, and
    the app naming a store it cannot open is the state `_bind_from_handoff` chooses over losing the
    row. A Dataset file, whose bytes the app has to end up holding. And a Chat Upload, the only one
    of the three that can be refused outright.
    """
    thread_id = orch.create_thread()["id"]
    scratch = orch.upload_scratch("note.csv", b"x")
    ThreadStore(orch.project(start_preview=False).record.path).write_context(thread_id, {"items": [
        {"id": "ctx_1", "kind": "data_source", "name": "trades",
         "bindingKey": ["data_source", "ds-1"]},
        {"id": "ctx_2", "kind": "file", "name": "train.csv",
         "datasetId": "ds_sales_2026", "datasetRelPath": "train.csv"},
        {"id": "ctx_3", "kind": "file", "name": "note.csv", "path": scratch["path"]},
    ]})
    return thread_id


def _client(orch: Orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app)


# ---- the door crosses what the handoff sheet crosses ------------------------------------------


def test_the_build_tab_writes_the_bindings_and_the_bytes(tmp_path: Path):
    """Criterion 2. One click performs the same three acts the handoff sheet performs: the Binding
    is recorded, the Dataset file is attached, and the Upload lands on a Dataset."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    tid = _a_conversation_with_three_chips(orch)

    res = orch.cross_chat_context(tid)

    project = orch.project(start_preview=False)
    # The Dataset is bound as well as the Data Source, by `attach_file` rather than by the chip: an
    # app that ships a file out of a Dataset depends on that Dataset to rehydrate it.
    assert [b["id"] for b in project.workspace.read_bindings()] == ["ds-1", "ds_sales_2026"]
    attached = {e["dataset_rel_path"] for e in project.attached}
    assert attached == {"train.csv", "uploads/note.csv"}
    assert res["appId"] == project.workspace.app_id
    # Per chip, not per act: `train.csv` binds its Dataset and attaches its bytes, and is one chip.
    assert sorted(res["crossed"]) == ["note.csv", "trades", "train.csv"]
    assert res["refused"] == []


def test_the_door_writes_no_digest_and_no_transcript(tmp_path: Path):
    """Criterion 2's other half. A digest and a transcript are a handoff's documents — they are
    what the plan gives a reader — and there is no plan behind this door to read them against."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    tid = _a_conversation_with_three_chips(orch)

    orch.cross_chat_context(tid)

    sage_dir = orch.project(start_preview=False).workspace.path / ".sage"
    assert not (sage_dir / "handoff.md").exists()
    assert not (sage_dir / "handoff-transcript.md").exists()


def test_a_real_handoffs_transcript_survives_this_door(tmp_path: Path):
    """Criterion 7, and the reason this is not `_write_crossing`. That function unlinks the
    transcript whenever `include.transcript` is false, so a door that reused it would destroy the
    document a handoff into this same app had written."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    tid = _a_conversation_with_three_chips(orch)
    transcript = orch.project(start_preview=False).workspace.path / ".sage" / "handoff-transcript.md"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("# What was asked\n\nThe handoff wrote this.\n")

    orch.cross_chat_context(tid)

    assert transcript.read_text().startswith("# What was asked")


# ---- a half-failed crossing reports for each chip ---------------------------------------------


def test_a_refused_upload_is_named_and_everything_beside_it_still_crosses(tmp_path: Path):
    """Criterion 3. With no writable Dataset mounted the Upload cannot cross, and the receipt says
    so in its own words — while the Binding beside it is recorded anyway. A crossing that gave up
    on the first refusal would leave the person with neither."""
    prov = FakeAssetProvider()
    prov.assets = []
    orch = _orch(tmp_path, assets=prov)
    thread_id = orch.create_thread()["id"]
    scratch = orch.upload_scratch("note.csv", b"x")
    ThreadStore(orch.project(start_preview=False).record.path).write_context(thread_id, {"items": [
        {"id": "ctx_1", "kind": "data_source", "name": "trades",
         "bindingKey": ["data_source", "ds-1"]},
        {"id": "ctx_2", "kind": "file", "name": "note.csv", "path": scratch["path"]},
    ]})

    res = orch.cross_chat_context(thread_id)

    assert res["crossed"] == ["trades"]
    assert res["refused"] == [
        {"name": "note.csv",
         "reason": "note.csv stayed in Chat — no writable Dataset is mounted here"}]
    assert [b["id"] for b in orch.project(start_preview=False).workspace.read_bindings()] == ["ds-1"]


def test_a_dataset_file_that_will_not_attach_is_named_too(tmp_path: Path):
    """The third act's refusal had nowhere to land: `_promote_chat_file` logged a warning and
    returned nothing, so a Dataset file that stayed behind was invisible to the door offering to
    move it. The bar must not count it as moved."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    thread_id = orch.create_thread()["id"]
    ThreadStore(orch.project(start_preview=False).record.path).write_context(thread_id, {"items": [
        {"id": "ctx_1", "kind": "file", "name": "gone.csv",
         "datasetId": "ds_sales_2026", "datasetRelPath": "gone.csv"},
    ]})

    res = orch.cross_chat_context(thread_id)

    # Its Dataset was bound on the way past, which is an act rather than the chip: a chip whose bytes
    # stayed behind has not crossed, and must not be counted as though it had.
    assert res["crossed"] == []
    assert [r["name"] for r in res["refused"]] == ["gone.csv"]
    assert res["refused"][0]["reason"]


# ---- what is already bound is left alone -------------------------------------------------------


def test_a_scope_somebody_set_by_hand_survives_the_crossing(tmp_path: Path):
    """The door ADDS what is missing. It must not rewrite what is there.

    A re-bind replaces the Binding in place (`_record`), and a store chip with no table chosen
    carries no Scope — so crossing one into an app somebody has already scoped would take the table
    away and leave an app that cannot query. Worse than the loss: the bar never offered that chip,
    because `chipsNotInApp` counts a bound Resource as held, so the click would have destroyed
    something nobody was told about.
    """
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    thread_id = orch.create_thread()["id"]
    orch._resources.tree["ds-dwh"] = {"DWH": {"MARTS": ["ORDERS"]}}
    orch.bind_data_source("ds-dwh", "DWH", "MARTS", "ORDERS")
    scratch = orch.upload_scratch("q3.csv", b"x")
    ThreadStore(orch.project(start_preview=False).record.path).write_context(thread_id, {"items": [
        # The same store, mentioned whole: no table on the row at all.
        {"id": "ctx_1", "kind": "data_source", "name": "warehouse",
         "bindingKey": ["data_source", "ds-dwh"]},
        {"id": "ctx_2", "kind": "file", "name": "q3.csv", "path": scratch["path"]},
    ]})

    res = orch.cross_chat_context(thread_id)

    bound = next(b for b in orch.project(start_preview=False).workspace.read_bindings()
                 if b["id"] == "ds-dwh")
    assert (bound.get("database"), bound.get("schema"), bound.get("table")) \
        == ("DWH", "MARTS", "ORDERS")
    # And the Upload beside it still crossed — leaving the Binding alone is not skipping the chip.
    assert res["crossed"] == ["q3.csv"]


def test_two_chips_with_one_name_keep_their_own_outcomes(tmp_path: Path):
    """An Upload and a Dataset file can both be called `data.csv` — different rows out of different
    stores — so an outcome is folded against the CHIP and never against its name. Folded by name, one
    chip's refusal reports over the other chip's bytes, which crossed: the person is told the file
    stayed in Chat while it sits under `public/data/`."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    thread_id = orch.create_thread()["id"]
    scratch = orch.upload_scratch("data.csv", b"x")
    ThreadStore(orch.project(start_preview=False).record.path).write_context(thread_id, {"items": [
        {"id": "ctx_1", "kind": "file", "name": "data.csv", "path": scratch["path"]},
        {"id": "ctx_2", "kind": "file", "name": "data.csv",
         "datasetId": "ds_sales_2026", "datasetRelPath": "gone.csv"},
    ]})

    res = orch.cross_chat_context(thread_id)

    assert res["crossed"] == ["data.csv"], "the Upload crossed and must be reported as crossed"
    assert [r["name"] for r in res["refused"]] == ["data.csv"]
    attached = {e["dataset_rel_path"] for e in orch.project(start_preview=False).attached}
    assert "uploads/data.csv" in attached


def test_a_store_that_will_not_open_is_named_rather_than_reported_as_added(tmp_path: Path):
    """A Binding is recorded whether or not Domino resolved the store (#204), so "added" is true of
    an app that now names a store it cannot open. Said plainly, because the fix is a re-bind in the
    Data panel and no amount of crossing again will do it."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    thread_id = orch.create_thread()["id"]
    ThreadStore(orch.project(start_preview=False).record.path).write_context(thread_id, {"items": [
        {"id": "ctx_1", "kind": "data_source", "name": "trades",
         "bindingKey": ["data_source", "ds-1"]},
    ]})

    res = orch.cross_chat_context(thread_id)

    assert res["crossed"] == ["trades"]
    assert res["unresolved"] == ["trades"]
    assert res["refused"] == []


# ---- the two locks ---------------------------------------------------------------------------


def test_the_door_is_refused_while_a_turn_holds_the_lock(tmp_path: Path):
    """`confirm_handoff` takes `_acquire_for_door` and `_app_lock` before it crosses. A door
    without them races an in-flight turn, which is the class behind the discarded-work bug (#233):
    this writes Bindings and bytes into a working tree a turn is reading."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    tid = _a_conversation_with_three_chips(orch)
    orch._turn_lock.acquire()
    try:
        with pytest.raises(TurnBusy):
            orch.cross_chat_context(tid)
    finally:
        orch._turn_lock.release()

    assert orch.project(start_preview=False).workspace.read_bindings() == []


# ---- the route ------------------------------------------------------------------------------


def test_the_route_crosses_and_answers_what_it_moved(tmp_path: Path, monkeypatch):
    """The bar's one click. It answers what moved and what was refused, because the bar must not
    report a chip it did not move."""
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    tid = _a_conversation_with_three_chips(orch)
    client = _client(orch, monkeypatch)

    res = client.post(f"/api/threads/{tid}/crossing")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert sorted(body["crossed"]) == ["note.csv", "trades", "train.csv"]
    # The app the crossing wrote into, named in the answer. The toast reads this rather than the
    # selection it captured before the request, because the server resolves the target from the live
    # selection — a switch that lands first crosses somewhere else.
    project = orch.project(start_preview=False)
    assert body["appId"] == project.workspace.app_id
    assert body["appName"] == project.workspace.display_name()
    assert json.loads(
        (orch.project(start_preview=False).workspace.path / ".sage" / "bindings.json").read_text())


def test_the_route_refuses_a_conversation_it_does_not_have(tmp_path: Path, monkeypatch):
    orch = _orch(tmp_path, assets=FakeAssetProvider())
    client = _client(orch, monkeypatch)

    assert client.post("/api/threads/conv_nope/crossing").status_code == 404
