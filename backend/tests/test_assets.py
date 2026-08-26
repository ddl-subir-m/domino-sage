"""Asset provider — tags, snapshots (Step 6), and reading a Dataset with no mount."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage.assets.provider import (
    Asset,
    DominoAssetProvider,
    FakeAssetProvider,
    UnconfiguredAssetProvider,
    dataset_unique_name,
    parse_tag_snapshots,
    parse_tags,
)
from sage.resources.provider import ResourceUnavailable


def test_parse_tags_handles_strings_and_objects():
    assert parse_tags(["a", {"name": "b"}, {"tag": "c"}, {"nope": 1}]) == ["a", "b", "c"]
    assert parse_tags(None) == []


def test_parse_tags_handles_datasetrw_v2_tag_map():
    # datasetrw v2 returns tags as {tagName: snapshotId} — the keys are the tag names.
    tags = parse_tags({"sensitive": "62313ce67a0af0281c01a6a5", "curated": "72313ce67a0af0281c01a6a5"})
    assert set(tags) == {"sensitive", "curated"}
    assert parse_tags({}) == []


def test_parse_tag_snapshots_keeps_the_snapshot_ids():
    # The {tagName: snapshotId} map is retained so we can tag an already-tagged dataset without a fetch.
    m = parse_tag_snapshots({"sensitive": "62313ce67a0af0281c01a6a5"})
    assert m == {"sensitive": "62313ce67a0af0281c01a6a5"}
    assert parse_tag_snapshots(["sensitive"]) == {}   # list shape carries no snapshot ids
    assert parse_tag_snapshots(None) == {}


def test_fake_provider_seeds_named_datasets():
    names = {a.name for a in FakeAssetProvider().list_datasets(None)}
    assert {"sales_2026", "customer_pii", "app_logs"} <= names


def test_unconfigured_provider_does_not_invent_datasets():
    with pytest.raises(ResourceUnavailable, match="not configured"):
        UnconfiguredAssetProvider().list_datasets(None)


def test_dataset_unique_name_carries_both_halves():
    # Domino registers each Dataset as a Data Source called `dataset-<name>-<id>`, and that
    # composite is the only thing get_dataset accepts — a bare name and a bare id are both
    # rejected, which is why two Datasets can share the name `prediction_data`.
    assert dataset_unique_name(Asset(id="abc123", name="Oil-and-Gas-Demo")) == \
        "dataset-Oil-and-Gas-Demo-abc123"


class _FakeDataset:
    def __init__(self, names):
        self._names = names
        self.downloaded = []

    def list_files(self):
        return [SimpleNamespace(name=n) for n in self._names]

    def download_file(self, rel, local):
        self.downloaded.append((rel, local))
        Path(local).write_bytes(b"x" * 7)


class _FakeDatasetClient:
    def __init__(self, dataset):
        self._dataset = dataset
        self.asked = []

    def get_dataset(self, unique_name):
        self.asked.append(unique_name)
        return self._dataset


def _provider(client):
    # mount_roots deliberately empty: this is the case the rail used to call unreadable.
    return DominoAssetProvider("http://domino", lambda: "t", mount_roots=[], dataset_client=client)


def test_unmounted_dataset_lists_its_files_through_the_data_library():
    client = _FakeDatasetClient(_FakeDataset(["raw/train.csv", "notes.md"]))
    files = _provider(client).list_files(Asset(id="i1", name="shared_ds"))
    assert [f.path for f in files] == ["raw/train.csv", "notes.md"]
    # The API listing carries no sizes; 0 means "not known from here", not "empty".
    assert [f.size for f in files] == [0, 0]
    # Both halves of the identifier, or Domino rejects the lookup.
    assert client.asked == ["dataset-shared_ds-i1"]


def test_a_mounted_dataset_still_reads_from_disk(tmp_path):
    # The mount stays a fast path: no data-library call when the files are already here.
    (tmp_path / "on_disk.csv").write_text("a,b\n")
    client = _FakeDatasetClient(_FakeDataset(["should-not-be-used"]))
    files = _provider(client).list_files(Asset(id="i1", name="local_ds", mount_path=str(tmp_path)))
    assert [f.path for f in files] == ["on_disk.csv"]
    assert client.asked == []


def test_listing_failure_is_reported_not_swallowed():
    class _Boom:
        def list_files(self):
            raise RuntimeError("proxy said no")

    with pytest.raises(ResourceUnavailable, match="did not answer"):
        _provider(_FakeDatasetClient(_Boom())).list_files(Asset(id="i1", name="shared_ds"))


def test_download_file_writes_the_bytes_and_reports_the_size(tmp_path):
    ds = _FakeDataset(["raw/train.csv"])
    dest = tmp_path / "out" / "train.csv"
    size = _provider(_FakeDatasetClient(ds)).download_file(
        Asset(id="i1", name="shared_ds"), "raw/train.csv", dest
    )
    assert size == 7 and dest.read_bytes() == b"x" * 7
    assert ds.downloaded == [("raw/train.csv", str(dest))]


def test_project_name_is_read_from_the_field_that_carries_it(monkeypatch):
    # DatasetRwProjectInfoDtoV1 is {projectId, projectName, projectOwnerUsername} — there is no
    # `name`, so reading one left every row's owning project blank.
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"datasets": [{"dataset": {"id": "i1", "name": "shared_ds"},
                                  "projectInfo": {"projectName": "Seabed-Object-Classifier"}}],
                    "metadata": {"totalCount": 1}}

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    got = DominoAssetProvider("http://domino", lambda: "t", mount_roots=[]).list_datasets(None)
    assert [a.project for a in got] == ["Seabed-Object-Classifier"]
