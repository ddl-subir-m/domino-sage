"""Two Datasets and an uploaded shell in one Build: each Dataset is settled before it runs (#591).

Measured live: a single-file Dataset still stopped the build to ask which file, "Nothing matched"
despite `@ABC123_ADAE` in the prompt, the second Dataset was never asked about because the first
card's replay skipped the gate outright, and the `@` mentions narrowed the turn so the uploaded
shell dropped out of both the plan turn and Approve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sage.assets.provider import Asset, DatasetFile, FakeAssetProvider
from sage.resources import dataset_files

from .fake_opencode import Turn
from .test_a_bound_dataset_with_no_files_asks_which_ones import _build, _frames, _orch
from .test_a_bound_dataset_with_no_files_asks_which_ones import _client as _open_client
from .test_plan_reference_persistence import PLAN

PROMPT = ("Build an app that reproduces the attached TFL shell table using the two data files in "
          "this project. Show enrollment by treatment arm from @ABC123_ADSL and, for a selected "
          "subject, list that subject's AEs. from @ABC123_ADAE")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    from sage.orchestrator.service import Orchestrator
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


@pytest.fixture
def open_client(monkeypatch):
    """Opens a TestClient on an orchestrator, and closes every one it opened at teardown."""
    opened = []

    def open_for(orch):
        opened.append(_open_client(orch, monkeypatch))
        return opened[-1]

    yield open_for
    for c in opened:
        c.close()


def _assets(tmp: Path, datasets: dict[str, dict[str, str]]) -> FakeAssetProvider:
    """The seeded provider, so an upload has its default Dataset, plus the Datasets a test names."""
    assets = FakeAssetProvider()
    for name, files in datasets.items():
        where = tmp / "datasets" / name
        for rel, body in files.items():
            (where / rel).parent.mkdir(parents=True, exist_ok=True)
            (where / rel).write_text(body)
        assets.assets.append(Asset(f"ds_{name}", name, project="Sage", mount_path=str(where)))
    return assets


SINGLE = {"ABC123_ADSL": {"adsl.csv": "USUBJID,ARM\n01,Active\n"},
          "ABC123_ADAE": {"adae.csv": "USUBJID,AETERM\n01,Headache\n"}}
MULTI = {"ABC123_ADSL": {"adsl.csv": "USUBJID,ARM\n01,Active\n", "adsl_old.csv": "a\n1\n"},
         "ABC123_ADAE": {"adae.csv": "USUBJID,AETERM\n01,Headache\n", "adae_old.csv": "a\n1\n"}}


def _cards(body: str) -> list[dict]:
    return [f for f in _frames(body) if f.get("type") == "dataset-files"]


def _attached_files(orch) -> set[str]:
    return {e["file"] for e in orch.project(start_preview=False).attached}


# ---- an explicit @Dataset mention names the Dataset ---------------------------------------------


def test_an_explicit_dataset_mention_counts_as_naming_the_dataset():
    files = [DatasetFile("adae.csv", 1), DatasetFile("adae_old.csv", 1)]
    assert dataset_files.rank("list AEs from @ABC123_ADAE", "ABC123_ADAE", files).named is True


def test_the_dataset_name_without_an_at_still_matches_no_file():
    """The rule the name removal exists for stays: "from the sales Dataset" is where to look, and
    every file under the Dataset's folder must not score as a match on the shared prefix."""
    files = [DatasetFile("ABC123_ADAE/adae.csv", 1), DatasetFile("ABC123_ADAE/other.csv", 1)]
    ranking = dataset_files.rank("list AEs from ABC123_ADAE", "ABC123_ADAE", files)
    assert ranking.named is False
    assert ranking.matched == 0


def test_a_card_for_a_mentioned_dataset_does_not_say_nothing_matched(tmp_path: Path, open_client):
    orch, _ = _orch(tmp_path, _assets(tmp_path, {"ABC123_ADAE": MULTI["ABC123_ADAE"]}))
    orch.bind_dataset("ds_ABC123_ADAE")
    client = open_client(orch)

    [card] = _cards(_build(client, "list the AEs from @ABC123_ADAE"))

    assert "Nothing" not in card["message"]


# ---- a single-file Dataset attaches without a card ----------------------------------------------


def test_a_single_file_dataset_attaches_its_file_without_asking(tmp_path: Path, open_client):
    orch, oc = _orch(tmp_path, _assets(tmp_path, {"ABC123_ADAE": SINGLE["ABC123_ADAE"]}))
    orch.bind_dataset("ds_ABC123_ADAE")
    client = open_client(orch)

    body = _build(client, "list the AEs from @ABC123_ADAE")

    assert _cards(body) == []
    assert _attached_files(orch) == {"adae.csv"}
    assert oc.prompts, "the build did not start"
    history = orch.project(start_preview=False).workspace.read_history(
        orch.project(start_preview=False).build_conversation)
    notes = [e["message"] for e in history if e.get("type") == "dataset-attached"]
    assert notes == ["Using adae.csv from ABC123_ADAE."]


# ---- every unattached Dataset is asked about, one card each, in turn -----------------------------


def test_answering_one_datasets_card_asks_about_the_next(tmp_path: Path, open_client):
    orch, oc = _orch(tmp_path, _assets(tmp_path, MULTI))
    orch.bind_dataset("ds_ABC123_ADSL")
    orch.bind_dataset("ds_ABC123_ADAE")
    client = open_client(orch)
    prompt = "build me an enrollment and adverse events app"

    [first] = _cards(_build(client, prompt))
    assert first["datasetId"] == "ds_ABC123_ADSL"
    assert first["message"].startswith("ABC123_ADSL, 1 of 2 Datasets.")

    orch.attach_file("ds_ABC123_ADSL", "adsl.csv")
    [second] = _cards(_build(client, prompt, skipDatasetGate=True, datasetPick="adsl.csv"))
    assert second["datasetId"] == "ds_ABC123_ADAE"
    assert second["message"].startswith("ABC123_ADAE, 2 of 2 Datasets. Using adsl.csv from "
                                        "ABC123_ADSL.")
    assert oc.prompts == [], "the build started before every Dataset was settled"

    orch.attach_file("ds_ABC123_ADAE", "adae.csv")
    body = _build(client, prompt, skipDatasetGate=True, datasetPick="adae.csv")
    assert _cards(body) == []
    assert oc.prompts, "the build did not start once both Datasets were settled"
    assert _attached_files(orch) == {"adsl.csv", "adae.csv"}


def test_continuing_without_one_dataset_still_asks_about_the_next(tmp_path: Path, open_client):
    orch, oc = _orch(tmp_path, _assets(tmp_path, MULTI))
    orch.bind_dataset("ds_ABC123_ADSL")
    orch.bind_dataset("ds_ABC123_ADAE")
    client = open_client(orch)
    prompt = "build me an enrollment and adverse events app"
    _build(client, prompt)

    [second] = _cards(_build(client, prompt, skipDatasetGate=True,
                             datasetDismissed="ds_ABC123_ADSL"))

    assert second["datasetId"] == "ds_ABC123_ADAE"
    assert "Continuing without ABC123_ADSL." in second["message"]
    assert oc.prompts == []


# ---- the reported scenario, end to end -----------------------------------------------------------


def test_two_single_file_datasets_and_an_uploaded_shell_all_reach_the_plan_and_approve(
        tmp_path: Path, open_client):
    """No card at all, and the plan turn and Approve both carry all three files."""
    orch, oc = _orch(tmp_path, _assets(tmp_path, SINGLE), [
        Turn(text=PLAN), Turn(writes={"src/App.tsx": "export default () => null\n"})])
    orch.bind_dataset("ds_ABC123_ADSL")
    orch.bind_dataset("ds_ABC123_ADAE")
    shell = orch.upload_file("TFL_shell_Table_14_3_1_1.md", b"# Table 14.3.1.1\n")["path"]
    client = open_client(orch)

    body = _build(client, PROMPT)

    assert _cards(body) == []
    plan_id = next(f["planId"] for f in _frames(body) if f["type"] == "plan-proposed")
    carried = {e["path"] for e in orch.project(start_preview=False).attached}
    assert {Path(p).name for p in carried} == {"adsl.csv", "adae.csv", Path(shell).name}
    assert {a["path"] for a in oc.prompts[0]["attachments"]} == carried

    list(orch.approve_stream(plan_id=plan_id))

    assert {a["path"] for a in oc.prompts[1]["attachments"]} == carried


def _three_attached(tmp_path: Path):
    orch, oc = _orch(tmp_path, _assets(tmp_path, SINGLE))
    orch.bind_dataset("ds_ABC123_ADSL")
    orch.bind_dataset("ds_ABC123_ADAE")
    adsl = orch.attach_file("ds_ABC123_ADSL", "adsl.csv")["path"]
    adae = orch.attach_file("ds_ABC123_ADAE", "adae.csv")["path"]
    shell = orch.upload_file("TFL_shell_Table_14_3_1_1.md", b"# Table 14.3.1.1\n")["path"]
    return orch, oc, adsl, adae, shell


def test_a_dataset_mention_keeps_the_other_attachments(tmp_path: Path):
    """What `@ABC123_ADAE` sends once the Dataset's file is attached: the Dataset as a Resource and
    its file as a mention. The file adds; it never takes the shell or the other Dataset out."""
    orch, oc, adsl, adae, shell = _three_attached(tmp_path)

    list(orch.build_stream("Build the enrollment table", [adae],
                           [{"kind": "dataset", "id": "ds_ABC123_ADAE", "name": "ABC123_ADAE"}]))

    assert {a["path"] for a in oc.prompts[0]["attachments"]} == {adsl, adae, shell}


def test_an_explicit_file_mention_still_narrows_the_turn(tmp_path: Path):
    """The reference policy stands: `@adae.csv` names that file, and the others stay out."""
    orch, oc, _, adae, _ = _three_attached(tmp_path)

    list(orch.build_stream("Build the enrollment table", [adae]))

    assert [a["path"] for a in oc.prompts[0]["attachments"]] == [adae]
