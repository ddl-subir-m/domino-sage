"""A Dataset with nothing attached from it is asked about, in Build and in Chat (#196, ADR-0039).

THE DEAD END, in one sentence: `bind_dataset` writes a real Binding and attaches nothing, and
ADR-0020 keeps the working set out of the prompt — so the agent is told the app uses a Dataset and
handed no path it can read. It builds on data it invented, and the result looks finished. That is
the failure ADR-0038 exists to stop, reached through the other door.

BOTH MODES IN ONE FILE, deliberately. #183 shipped Build's table card and #188 shipped Chat's a
round later, and once both were on main the same question put a different table first in each for no
reason a person could learn — which cost #193 a whole ticket. The fix there was one shared method;
the guard that keeps it shared is a test that fails when only one surface moves, and that test has
to live where both surfaces are visible at once.

Driven at the HTTP seam over `TestClient` with the fake asset provider standing in for the platform.
No test here needs a mounted Dataset, a live platform or a warehouse.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.assets.provider import Asset, AssetProvider, DatasetFile, FakeAssetProvider, FileListing, walk_files
from sage.orchestrator import app as appmod
from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATASET
from sage.router.models import ModelCatalog
from sage.workspace.threads import ThreadStore

from .fake_opencode import FakeOpenCode, Turn

PROMPT = "build me a daily summary of calls"


class ScriptedGateway:
    """CHAT, so the handoff classifier does not turn a Chat turn into a Build offer."""

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


def _dataset(tmp: Path, name: str, files: dict[str, str]) -> FakeAssetProvider:
    """One Dataset on disk, with exactly the files a test needs and nothing seeded beside it."""
    root = tmp / "datasets"
    where = root / name
    for rel, body in files.items():
        p = where / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    assets = FakeAssetProvider(root=root)
    assets.assets = [Asset(f"ds_{name}", name, project="Revenue", mount_path=str(where))]
    return assets


def _calls_dataset(tmp: Path) -> FakeAssetProvider:
    """A Dataset holding one file the request names and two it does not."""
    return _dataset(tmp, "revenue_2026", {
        "calls_daily.csv": "day,calls\n2026-01-01,7\n",
        "README.md": "notes\n",
        "accounts.csv": "id,name\n1,Acme\n",
    })


def _partitioned(tmp: Path, days: int = 40) -> FakeAssetProvider:
    """A Dataset partitioned to the day — the shape that makes a file row a 200-click answer."""
    return _dataset(tmp, "calls_raw", {
        f"raw/2026/{d // 28 + 1:02d}/{d % 28 + 1:02d}/part.csv": "a,b\n1,2\n" for d in range(days)
    })


class _Unmounted:
    """A Dataset the platform lists but this container has no mount for (#197).

    Listing works without a mount, so the card is drawn from real file names. Attaching works too,
    as a download rather than a symlink, which is what `_download_attachment` exists for.

    `measured` is the half that varies, and BOTH shapes are real. Since #153 the platform weighs an
    unmounted Dataset and the listing carries true sizes; a listing that came back without them
    reports zeros nothing measured. Keying the card on the mount instead would collapse the two and
    throw away the sizes the platform did give, so both are staged here.
    """

    def __init__(self, root: Path, asset: Asset, *, measured: bool = False) -> None:
        self.root, self.asset, self.measured = root, asset, measured

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        return [self.asset]

    def list_files(self, asset: Asset) -> FileListing:
        walked = walk_files(self.root).files
        if self.measured:
            return FileListing(walked)
        return FileListing([DatasetFile(f.path, 0) for f in walked], measured=False)

    def download_file(self, asset: Asset, rel_path: str, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.root / rel_path, dest)
        return dest.stat().st_size


def _unmounted(tmp: Path, name: str, files: dict[str, str], *, measured: bool = False) -> _Unmounted:
    """The same Dataset as `_dataset`, with no mount for this container to walk."""
    where = tmp / "unmounted" / name
    for rel, body in files.items():
        p = where / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return _Unmounted(where, Asset(f"ds_{name}", name, project="Revenue", mount_path=""),
                      measured=measured)


def _orch(tmp: Path, assets: AssetProvider, turns: list[Turn] | None = None):
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")
    ws = tmp / "mnt" / "code"
    oc = FakeOpenCode(ws, turns or [Turn(text="Here it is.")])
    orch = Orchestrator(workspace_dir=ws, template=template, gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", assets=assets, feedback=OkFeedback(),
                        opencode_client=oc)
    orch.project(start_preview=False)
    return orch, oc


def _client(orch: Orchestrator, monkeypatch) -> TestClient:
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app)


def _frames(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _card(text: str) -> dict:
    cards = [f for f in _frames(text) if f.get("type") == "dataset-files"]
    assert len(cards) == 1, f"expected one Dataset card, got {[f['type'] for f in _frames(text)]}"
    return cards[0]


def _build(client: TestClient, prompt: str = PROMPT, **body) -> str:
    return client.post("/api/project/build/stream", json={"prompt": prompt, **body}).text


def _ask(client: TestClient, thread_id: str, prompt: str = PROMPT, **body) -> str:
    return client.post(f"/api/threads/{thread_id}/chat/stream",
                       json={"prompt": prompt, **body}).text


def _thread_with_dataset(orch: Orchestrator, dataset_id: str, name: str) -> str:
    """A Thread using one Dataset, with no file pinned from it."""
    tid = orch.create_thread()["id"]
    orch.add_thread_context(tid, {"kind": "dataset", "name": name, "resourceId": dataset_id})
    return tid


def _paths(card: dict) -> list[str]:
    return [r["path"] for r in card["allRows"]]


# ---- the card, in Build -------------------------------------------------------------------------


def test_a_bound_dataset_with_nothing_attached_is_asked_about_instead_of_built_on(
        tmp_path: Path, monkeypatch):
    """The reversal, in one request: a question with the Dataset's own files attached to it.

    The assistant is never asked, which is the rule rather than a side effect — a turn that reached
    it would be a turn building a daily summary out of rows nobody has.
    """
    orch, oc = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert card["datasetId"] == "ds_revenue_2026"
    assert card["prompt"] == PROMPT
    assert set(_paths(card)) == {"calls_daily.csv", "README.md", "accounts.csv"}
    assert oc.prompts == [], "the assistant was asked to build on a Dataset it could not read"


def test_the_card_fires_even_though_the_request_names_no_dataset_at_all(
        tmp_path: Path, monkeypatch):
    """Story 4 of #194. "A daily summary of calls" names nothing, and it is exactly the request
    that invents data — so the gate is about the state of the app, not about the words."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    card = _card(_build(client, "make me a page with three tiles on it"))

    assert card["datasetId"] == "ds_revenue_2026"
    assert card["matched"] == 0, "nothing in that sentence names a file, and the card should say so"
    # Still offered, because "no name matched" is a fact about the names and not about the Dataset:
    # the person very often knows the file by sight.
    assert len(card["allRows"]) == 3


def test_the_one_dataset_an_app_records_is_still_asked_about(tmp_path: Path, monkeypatch):
    """#185's rule, one Asset over: silence is not an answer. Using the only Dataset silently is
    the same inference through a side door, and it would change under the person the day a second
    one is bound."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    assert [b for b in orch.list_bindings() if b["kind"] == KIND_DATASET]
    client = _client(orch, monkeypatch)

    assert _card(_build(client))["datasetId"] == "ds_revenue_2026"


def test_the_file_the_request_names_is_offered_first(tmp_path: Path, monkeypatch):
    """The ordering is name matching and nothing else (ADR-0039). It is a convenience — every file
    stays reachable — but a card that led with `README.md` for a request about calls would be
    making the person hunt for the answer they already described."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert card["rows"][0]["path"] == "calls_daily.csv"
    assert card["matched"] == 1


def test_an_app_that_already_attached_a_file_from_the_dataset_is_not_asked_again(
        tmp_path: Path, monkeypatch):
    """The gate is about an unanswered half of a question. Once a file is attached it is gone —
    which is also what makes the click's replay terminate."""
    orch, oc = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    orch.attach_file("ds_revenue_2026", "calls_daily.csv")
    client = _client(orch, monkeypatch)

    assert [f for f in _frames(_build(client)) if f.get("type") == "dataset-files"] == []
    assert oc.prompts, "the turn stopped for a question that had already been answered"


# ---- the rows are the tree's rows -----------------------------------------------------------


def test_a_partitioned_dataset_offers_folders_rather_than_a_row_per_file(
        tmp_path: Path, monkeypatch):
    """Story 3 of #194. Forty files partitioned to the day give forty folders under the immediate
    parent, so answering the card would be forty clicks — which is the cost ADR-0029 removed for
    the tree and must not come back through the card.

    The roll-up is `_by_folder`'s, handed the destination paths an attach would write. This asserts
    the ANSWER rather than the rule: rows a person can click, well under the file count.
    """
    orch, _ = _orch(tmp_path, _partitioned(tmp_path))
    orch.bind_dataset("ds_calls_raw")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert card["total"] < 40
    assert {r["kind"] for r in card["allRows"]} == {"folder"}
    assert sum(r["count"] for r in card["allRows"]) == 40


def test_a_folder_row_says_how_many_files_its_click_will_actually_attach(
        tmp_path: Path, monkeypatch):
    """The row's count is what the ACT carries, which is not what the grouping holds.

    `_by_folder` groups on the immediate parent and rolls up from the deepest level, so its groups
    sit at mixed depths and one key can enclose another; `attach_folder` takes everything under the
    prefix. A count read off the group would say "2 files" over a click that carries fourteen —
    the understatement the folder act exists to avoid, and a way to trip the size cap on an act
    somebody was told was small.
    """
    files = {f"raw/2026/{d:02d}/part.csv": "a\n1\n" for d in range(1, 13)}
    files["raw/notes.md"] = "notes\n"
    files["raw/schema.json"] = "{}\n"
    orch, _ = _orch(tmp_path, _dataset(tmp_path, "mixed", files))
    orch.bind_dataset("ds_mixed")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))
    row = next(r for r in card["allRows"] if r["path"] == "raw")

    assert row["count"] == 14
    done = client.post("/api/project/assets/ds_mixed/files/attach-folder",
                       json={"folder": "raw"})
    assert done.json()["attached"] == row["count"]


def test_a_flat_dataset_above_the_threshold_still_offers_a_file_to_pick(
        tmp_path: Path, monkeypatch):
    """One folder row is not a choice. Twelve loose files roll up to a single group standing for the
    whole Dataset, and a card offering that alone would answer "which files should this read?" with
    one all-or-nothing button."""
    orch, _ = _orch(tmp_path, _dataset(tmp_path, "flat", {
        f"file_{n:02d}.csv": "a\n1\n" for n in range(12)
    }))
    orch.bind_dataset("ds_flat")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert {r["kind"] for r in card["allRows"]} == {"file"}
    assert card["total"] == 12


def test_a_small_dataset_keeps_the_file_as_the_row(tmp_path: Path, monkeypatch):
    """Below the threshold the file is the row, which is the same rule the `@` menu follows
    (ADR-0030). Two surfaces, one number, so they cannot disagree about what a Dataset looks like."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    assert {r["kind"] for r in _card(_build(client))["allRows"]} == {"file"}


def test_a_dataset_whose_folder_act_is_withheld_falls_back_to_its_files(
        tmp_path: Path, monkeypatch):
    """One source of truth, no second opinion (ADR-0029). `_folder_act_reason` already decides
    whether a subtree can be taken whole — a cut tail, or no mount here — and a card that offered a
    folder button the route would refuse could not exist. What is left to click is the files."""
    assets = _partitioned(tmp_path)
    whole = FakeAssetProvider.list_files
    monkeypatch.setattr(type(assets), "list_files",
                        lambda self, asset: FileListing(whole(self, asset).files, truncated=True))
    orch, _ = _orch(tmp_path, assets)
    orch.bind_dataset("ds_calls_raw")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert {r["kind"] for r in card["allRows"]} == {"file"}
    assert card["total"] == 40
    assert card["truncated"] is True


def test_only_the_shortlist_is_offered_up_front_and_the_rest_stay_reachable(
        tmp_path: Path, monkeypatch):
    """A bad ordering has to cost a scroll and never be a dead end, which is the rule the table
    card keeps for the same reason."""
    orch, _ = _orch(tmp_path, _dataset(tmp_path, "wide", {
        f"file_{n:02d}.csv": "a\n1\n" for n in range(9)
    }))
    orch.bind_dataset("ds_wide")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert len(card["rows"]) == 5
    assert len(card["allRows"]) == 9
    assert card["total"] == 9


# ---- the click ----------------------------------------------------------------------------------


def test_clicking_a_file_attaches_it_and_the_replayed_request_then_builds(
        tmp_path: Path, monkeypatch):
    """The click is two acts: it writes the record, and it sends the request the person already
    made. This is the second half seen from the server — the replay carries `skipDatasetGate`, and
    with the file attached the turn reaches the assistant instead of the card."""
    orch, oc = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)
    _card(_build(client))

    attached = client.post("/api/project/assets/ds_revenue_2026/files/attach",
                           json={"path": "calls_daily.csv"})
    assert attached.status_code == 200, attached.text
    body = _build(client, skipDatasetGate=True)

    assert [f for f in _frames(body) if f.get("type") == "dataset-files"] == []
    assert oc.prompts, "the replayed request never reached the assistant"
    assert [e["file"] for e in orch.project(start_preview=False).attached] == ["calls_daily.csv"]


def test_clicking_a_folder_attaches_the_whole_folder_rather_than_one_file(
        tmp_path: Path, monkeypatch):
    """Story 3 again, from the other end: the folder is the unit of the act (ADR-0029), so one
    click carries every file below it and the file stays the unit of the record."""
    orch, _ = _orch(tmp_path, _partitioned(tmp_path))
    orch.bind_dataset("ds_calls_raw")
    client = _client(orch, monkeypatch)
    row = _card(_build(client))["allRows"][0]

    done = client.post("/api/project/assets/ds_calls_raw/files/attach-folder",
                       json={"folder": row["path"]})

    assert done.status_code == 200, done.text
    assert done.json()["attached"] == row["count"]


def test_the_way_past_the_card_builds_with_nothing_attached(tmp_path: Path, monkeypatch):
    """Story 6 of #194. An app that holds its own data is a real case, and a listing outage must
    not take somebody's build hostage — so the card offers a way past itself and taking it builds."""
    orch, oc = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    body = _build(client, skipDatasetGate=True)

    assert [f for f in _frames(body) if f.get("type") == "dataset-files"] == []
    assert oc.prompts, "building past the card did not build"
    assert orch.project(start_preview=False).attached == []


def test_the_way_past_the_card_stays_past_it_on_the_next_request(tmp_path: Path, monkeypatch):
    """Story 6, on the turn after. The gate reads the app's STATE rather than the request's words,
    so a Dataset somebody chose to work without would meet them again on "make the button blue" —
    and on every sentence after that. Answering it has to outlive the request that answered it."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)
    _card(_build(client))

    _build(client, skipDatasetGate=True, datasetDismissed="ds_revenue_2026")
    later = _build(client, "make the button blue")

    assert [f for f in _frames(later) if f.get("type") == "dataset-files"] == []


def test_the_same_way_past_holds_in_chat(tmp_path: Path, monkeypatch):
    """Against the Thread rather than the app, because a Thread is where Chat keeps its account of
    what a conversation is about."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    client = _client(orch, monkeypatch)
    tid = _thread_with_dataset(orch, "ds_revenue_2026", "revenue_2026")
    _card(_ask(client, tid))

    _ask(client, tid, skipDatasetGate=True, datasetDismissed="ds_revenue_2026")
    later = _ask(client, tid, "what can you do?")

    assert [f for f in _frames(later) if f.get("type") == "dataset-files"] == []


def test_the_card_carries_the_gates_this_turn_had_already_answered(tmp_path: Path, monkeypatch):
    """#185's loop, inherited rather than rediscovered. "Start over and summarise my calls" answers
    the reset offer and then reaches this card; a replay that dropped `skipResetGate` would offer to
    throw the app away a second time, and the two cards would trade the turn back and forth."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    card = _card(_build(client, skipResetGate=True, skipSourceGate=True))

    assert card["answered"]["skipResetGate"] is True
    assert card["answered"]["skipSourceGate"] is True


# ---- failing open -------------------------------------------------------------------------------


def test_a_dataset_listing_that_fails_builds_anyway(tmp_path: Path, monkeypatch):
    """Story 13 of #194. A platform outage is not a wall. The block still tells the agent this app
    records a Dataset it cannot read, so failing open does not mean failing silently (#195)."""
    from sage.resources.provider import ResourceUnavailable

    assets = _calls_dataset(tmp_path)

    def _refuse(self, asset):
        raise ResourceUnavailable("the data library is not answering")

    monkeypatch.setattr(type(assets), "list_files", _refuse)
    orch, oc = _orch(tmp_path, assets)
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    body = _build(client)

    assert [f for f in _frames(body) if f.get("type") == "dataset-files"] == []
    assert oc.prompts, "a listing outage stopped a build it should only have gone quiet for"


def test_an_empty_dataset_is_not_a_question(tmp_path: Path, monkeypatch):
    """There is nothing to pick, so a card would stop a build over a fact the block already
    states."""
    orch, oc = _orch(tmp_path, _dataset(tmp_path, "empty", {}))
    orch.bind_dataset("ds_empty")
    client = _client(orch, monkeypatch)

    assert [f for f in _frames(_build(client)) if f.get("type") == "dataset-files"] == []
    assert oc.prompts


# ---- a listing that could not say everything (#197) ----------------------------------------------


def _truncate(monkeypatch, assets: FakeAssetProvider) -> None:
    """The provider's cap, reached: the same files back, flagged as a sorted prefix."""
    whole = FakeAssetProvider.list_files
    monkeypatch.setattr(type(assets), "list_files",
                        lambda self, asset: FileListing(whole(self, asset).files, truncated=True))


def test_a_truncated_listing_still_draws_a_card_from_the_prefix_that_came_back(
        tmp_path: Path, monkeypatch):
    """#191's pattern, one Asset over: a partial answer names the gap instead of discarding what it
    found. Every row here is a real file somebody can see and click, so withholding the card over a
    fact about the tail would cost them the answer and put them back in front of an app built on
    invented rows."""
    assets = _partitioned(tmp_path)
    _truncate(monkeypatch, assets)
    orch, oc = _orch(tmp_path, assets)
    orch.bind_dataset("ds_calls_raw")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert card["truncated"] is True
    assert len(card["allRows"]) == 40
    assert oc.prompts == []


def test_a_truncated_listing_says_the_list_is_partial_rather_than_reading_as_the_whole_dataset(
        tmp_path: Path, monkeypatch):
    """The sentence is the whole point: a prefix presented without one is read as the Dataset."""
    assets = _partitioned(tmp_path)
    _truncate(monkeypatch, assets)
    orch, _ = _orch(tmp_path, assets)
    orch.bind_dataset("ds_calls_raw")
    client = _client(orch, monkeypatch)

    assert "Only part of calls_raw could be listed" in _card(_build(client))["message"]


def test_no_folder_row_on_a_truncated_listing_offers_the_folder_act(
        tmp_path: Path, monkeypatch):
    """A sorted prefix cuts the tail, so early folders are whole and late ones are cut with nothing
    able to tell which (ADR-0029). The card reads `_folder_act_reason`'s answer rather than holding
    a second opinion about it, and falls back to the files — which is what the reason it reads has
    been telling people to do since #189."""
    assets = _partitioned(tmp_path)
    _truncate(monkeypatch, assets)
    orch, _ = _orch(tmp_path, assets)
    orch.bind_dataset("ds_calls_raw")
    client = _client(orch, monkeypatch)

    assert {r["kind"] for r in _card(_build(client))["allRows"]} == {"file"}


def test_the_partial_listing_is_named_in_chat_in_the_same_words(tmp_path: Path, monkeypatch):
    """One question asked in two places, so one sentence about the gap in it. A person who gets the
    card in Chat and the card in Build must be told about the same half-read Dataset in the same
    words, which is why the note is composed once."""
    assets = _partitioned(tmp_path)
    _truncate(monkeypatch, assets)
    orch, _ = _orch(tmp_path, assets)
    client = _client(orch, monkeypatch)
    tid = _thread_with_dataset(orch, "ds_calls_raw", "calls_raw")

    assert "Only part of calls_raw could be listed" in _card(_ask(client, tid))["message"]


def test_a_complete_listing_says_nothing_about_being_partial(tmp_path: Path, monkeypatch):
    """The other half of the same fact. A note on every card would say nothing about any of them."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert card["truncated"] is False
    assert "Only part of" not in card["message"]


# ---- a Dataset with no mount here (#197) ---------------------------------------------------------


def test_a_dataset_this_workspace_has_no_mount_for_still_draws_a_card_of_files(
        tmp_path: Path, monkeypatch):
    """Story 7 of #194: "not mounted here" stops being a dead end. Listing works without a mount, so
    there is a card to draw; only the folder act stays withheld, and it is withheld for a reason
    about reporting an unbounded serial download rather than about reach."""
    orch, oc = _orch(tmp_path, _unmounted(tmp_path, "revenue_2026", {
        "calls_daily.csv": "day,calls\n2026-01-01,7\n",
        "accounts.csv": "id,name\n1,Acme\n",
    }))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    card = _card(_build(client))

    assert set(_paths(card)) == {"calls_daily.csv", "accounts.csv"}
    assert {r["kind"] for r in card["allRows"]} == {"file"}
    assert oc.prompts == []


def test_a_file_the_listing_never_measured_carries_no_size_at_all(tmp_path: Path, monkeypatch):
    """A row reading "0 bytes" is a lie about the file; no size at all is the truth about the
    listing. The absence is the honest answer, and it is what keeps the card from asserting
    something the platform never told it."""
    orch, _ = _orch(tmp_path, _unmounted(tmp_path, "revenue_2026", {
        "calls_daily.csv": "day,calls\n2026-01-01,7\n",
        "accounts.csv": "id,name\n1,Acme\n",
    }))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    assert [r for r in _card(_build(client))["allRows"] if "size" in r] == []


def test_a_measured_listing_still_carries_every_size_including_a_real_zero(
        tmp_path: Path, monkeypatch):
    """What is missing is a fact about the LISTING, never about the number. A mounted walk stats
    every file, so an empty file there is genuinely empty and the card says so."""
    orch, _ = _orch(tmp_path, _dataset(tmp_path, "revenue_2026", {
        "empty.csv": "", "calls_daily.csv": "day,calls\n2026-01-01,7\n",
    }))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    rows = {r["path"]: r for r in _card(_build(client))["allRows"]}

    assert rows["empty.csv"]["size"] == 0
    assert rows["calls_daily.csv"]["size"] == 23


def test_an_unmounted_listing_that_did_measure_keeps_every_size_the_platform_gave(
        tmp_path: Path, monkeypatch):
    """The absence is read off the LISTING, never off the mount. Since #153 the platform weighs an
    unmounted Dataset, so a card keying on `mount_path` would blank sizes it had been handed — and
    would blank them inconsistently, hiding the one row where "0 bytes" is the fact somebody
    needed while every row beside it kept its number."""
    orch, _ = _orch(tmp_path, _unmounted(tmp_path, "revenue_2026", {
        "placeholder.csv": "", "calls_daily.csv": "day,calls\n2026-01-01,7\n",
    }, measured=True))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)

    rows = {r["path"]: r for r in _card(_build(client))["allRows"]}

    assert rows["calls_daily.csv"]["size"] == 23
    assert rows["placeholder.csv"]["size"] == 0


def test_clicking_a_file_on_an_unmounted_dataset_attaches_it_and_then_the_request_builds(
        tmp_path: Path, monkeypatch):
    """The same click, the same replay, the same build. Only the attach itself differs — the bytes
    come down through the data library rather than off a mount — and nothing above it can tell."""
    orch, oc = _orch(tmp_path, _unmounted(tmp_path, "revenue_2026", {
        "calls_daily.csv": "day,calls\n2026-01-01,7\n",
    }))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)
    _card(_build(client))

    attached = client.post("/api/project/assets/ds_revenue_2026/files/attach",
                           json={"path": "calls_daily.csv"})
    body = _build(client, skipDatasetGate=True)

    assert attached.status_code == 200, attached.text
    assert [f for f in _frames(body) if f.get("type") == "dataset-files"] == []
    assert oc.prompts, "the request the card was asked about never reached the assistant"
    # The real bytes, in the app's own tree, so the preview and the published app serve them.
    landed = orch.project(start_preview=False).workspace.path / attached.json()["path"]
    assert landed.read_text() == "day,calls\n2026-01-01,7\n"


# ---- the same question, in Chat -----------------------------------------------------------------


def test_a_dataset_on_a_thread_with_no_file_pinned_is_asked_about_in_chat(
        tmp_path: Path, monkeypatch):
    """ADR-0038's rule, unchanged: the mode somebody happens to be standing in must not decide
    whether Sage goes and looks."""
    orch, oc = _orch(tmp_path, _calls_dataset(tmp_path))
    client = _client(orch, monkeypatch)
    tid = _thread_with_dataset(orch, "ds_revenue_2026", "revenue_2026")

    card = _card(_ask(client, tid))

    assert card["datasetId"] == "ds_revenue_2026"
    assert card["threadId"] == tid
    assert oc.prompts == [], "the assistant was asked about data nobody had pointed it at"


def test_the_chat_card_is_on_the_thread_so_reopening_the_conversation_still_shows_it(
        tmp_path: Path, monkeypatch):
    """A Chat turn's record is the Thread's history, and a question whose card vanished on reload
    would read as a turn that answered nothing."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    client = _client(orch, monkeypatch)
    tid = _thread_with_dataset(orch, "ds_revenue_2026", "revenue_2026")

    _ask(client, tid)

    kinds = [e.get("type") for e in client.get(f"/api/threads/{tid}/history").json()]
    assert kinds.count("user") == 1
    assert "dataset-files" in kinds


def test_the_chat_click_writes_a_chip_and_neither_a_binding_nor_an_attachment(
        tmp_path: Path, monkeypatch):
    """Chat has no Built App, so the record it writes is the one Chat already has: a `dsfile:` pin
    in Session context, which a handoff turns into `App.requires`. Nothing parallel to it — a
    Binding written here would be a declaration on an app that does not exist yet."""
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    client = _client(orch, monkeypatch)
    tid = _thread_with_dataset(orch, "ds_revenue_2026", "revenue_2026")
    _card(_ask(client, tid))

    done = client.post(f"/api/threads/{tid}/context/dataset/ds_revenue_2026/file",
                       json={"path": "calls_daily.csv"})

    assert done.status_code == 200, done.text
    items = ThreadStore(orch.project(start_preview=False).record.path).read_context(tid)["items"]
    pin = next(i for i in items if i.get("kind") == "file")
    assert pin["resourceId"] == "dsfile:ds_revenue_2026:calls_daily.csv"
    assert pin["datasetRelPath"] == "calls_daily.csv"
    assert orch.list_bindings() == []
    assert orch.project(start_preview=False).attached == []


def test_a_thread_that_already_pinned_a_file_is_not_asked_again(tmp_path: Path, monkeypatch):
    """The same terminating condition Build has, which is what stops the replay looping."""
    orch, oc = _orch(tmp_path, _calls_dataset(tmp_path))
    client = _client(orch, monkeypatch)
    tid = _thread_with_dataset(orch, "ds_revenue_2026", "revenue_2026")
    client.post(f"/api/threads/{tid}/context/dataset/ds_revenue_2026/file",
                json={"path": "calls_daily.csv"})

    assert [f for f in _frames(_ask(client, tid)) if f.get("type") == "dataset-files"] == []
    assert oc.prompts, "a Thread that had answered the question was asked it again"


def test_listing_a_dataset_does_not_put_its_tree_into_the_prompt(tmp_path: Path, monkeypatch):
    """ADR-0020, and `docs/workbench/chat.md`'s rule for the `@` menu. The card is a turn's answer,
    not context: the forty paths it drew are on screen and none of them is in the sentence the
    assistant is sent when the person builds past it."""
    orch, oc = _orch(tmp_path, _partitioned(tmp_path))
    orch.bind_dataset("ds_calls_raw")
    client = _client(orch, monkeypatch)
    _card(_build(client))

    _build(client, skipDatasetGate=True)

    assert oc.prompts
    assert "part.csv" not in "\n".join(p["text"] for p in oc.prompts)


# ---- the two surfaces cannot drift --------------------------------------------------------------


def test_build_and_chat_offer_the_same_files_in_the_same_order(tmp_path: Path, monkeypatch):
    """THE GUARD, and the reason this file holds both modes (#193).

    Build's card and Chat's card are one question asked in two places. When they were built a round
    apart they came to order their candidates differently for no reason a person could learn, and
    the fix was to make one method both surfaces call. This is what fails when a future change moves
    one of them without the other.
    """
    orch, _ = _orch(tmp_path, _calls_dataset(tmp_path))
    orch.bind_dataset("ds_revenue_2026")
    client = _client(orch, monkeypatch)
    tid = _thread_with_dataset(orch, "ds_revenue_2026", "revenue_2026")

    built = _card(_build(client))
    asked = _card(_ask(client, tid))

    assert _paths(built) == _paths(asked)
    assert built["matched"] == asked["matched"] == 1
    assert built["rows"][0]["path"] == asked["rows"][0]["path"] == "calls_daily.csv"
