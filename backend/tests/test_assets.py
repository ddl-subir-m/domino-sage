"""Asset provider — tags, snapshots (Step 6), and reading a Dataset with no mount."""
from pathlib import Path

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
    """The `domino_data` handle, which is now only what `download_file` reads a file through."""

    def __init__(self):
        self.downloaded = []

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


def _provider(client=None):
    # mount_roots deliberately empty: this is the case the rail used to call unreadable.
    return DominoAssetProvider("http://domino", lambda: "t", mount_roots=[], dataset_client=client)


# --- the datasetrw file listing (#153) ----------------------------------------------------------
#
# Every shape below was probed against a live deployment on 2026-09-04, and three of them are not
# what the swagger schema reads like at a glance: `name` and `size` are objects rather than a
# string and a number, directories arrive as rows, and `fileName` — not `label` — is the path.


def _snapshot(sid, version, status="Active"):
    """A `DatasetRwSnapshotDto`. `storageSize` is deliberately wrong-looking: the live one is stale
    (469 for a snapshot whose two files total 1409 bytes), so nothing may read a total off it."""
    return {"id": sid, "datasetId": "i1", "version": version, "lifecycleStatus": status,
            "storageSize": 469, "isPartialSize": False, "isReadWrite": True}


def _row(file_name, size_in_bytes, is_directory=False):
    """A `DatasetRwFileDetailsRowDto` as the deployment really sends it."""
    return {"name": {"isDirectory": is_directory, "label": file_name.rsplit("/", 1)[-1],
                     "sortableName": file_name, "fileName": file_name},
            "size": {"label": "?", "sizeInBytes": size_in_bytes},
            "lastModified": 1722552360177}


class _FakeApi:
    """The two `/v4` GETs a listing makes, answered from canned rows.

    Routed on the URL rather than on call order, so a test can assert WHICH endpoint was asked and
    with what — the `/v4` prefix and the empty `path` are both requirements, not incidentals.
    """

    def __init__(self, snapshots=(), rows=(), status=200):
        self.snapshots, self.rows, self.status = list(snapshots), list(rows), status
        self.asked: list[tuple[str, dict]] = []

    def get(self, url, **kw):
        import httpx

        self.asked.append((url, dict(kw.get("params") or {})))
        if self.status >= 400:
            return httpx.Response(self.status, text="denied")
        if "/files/recursive" in url:
            return httpx.Response(200, json={"directorySize": "1.3 K", "rows": self.rows})
        return httpx.Response(200, json=self.snapshots)


def _install(monkeypatch, api):
    import httpx

    monkeypatch.setattr(httpx, "get", api.get)
    return api


def test_an_unmounted_dataset_reports_the_real_byte_size_of_every_file(monkeypatch):
    """The bug: these rows all read 0 bytes, because the endpoint the SDK called carries no sizes."""
    api = _install(monkeypatch, _FakeApi(
        snapshots=[_snapshot("s1", 0)],
        rows=[_row("all_series.json", 940), _row("forecasts.csv", 469)],
    ))
    files = _provider().list_files(Asset(id="i1", name="shared_ds")).files

    assert [(f.path, f.size) for f in files] == [("all_series.json", 940), ("forecasts.csv", 469)]
    # Asked under /v4 (where /api answers 404), and with an EMPTY path (where `/` answers 400).
    assert api.asked == [
        ("http://domino/v4/datasetrw/snapshots/i1", {}),
        ("http://domino/v4/datasetrw/snapshot/s1/files/recursive", {"path": ""}),
    ]


def test_a_nested_file_keeps_the_path_a_mount_would_have_given_it(monkeypatch):
    """`fileName`, not `label`: the basename would collapse siblings across folders together.

    The two paths have to agree, because the Workbench re-nests one tree from whichever produced
    it. `docs/a.docx` and `notes/a.docx` both read as `a.docx` off `label`.
    """
    _install(monkeypatch, _FakeApi(
        snapshots=[_snapshot("s1", 3)],
        rows=[_row("docs/model_docs.docx", 109014), _row("notes/model_docs.docx", 12)],
    ))
    files = _provider().list_files(Asset(id="i1", name="autodoc")).files

    assert [f.path for f in files] == ["docs/model_docs.docx", "notes/model_docs.docx"]


def test_the_two_paths_describe_the_same_tree_the_same_way(tmp_path, monkeypatch):
    """Whether a Dataset is mounted here is an accident of which Project this workspace belongs to,
    and it must not change what its tree looks like. The Workbench re-nests folders out of these
    paths and the app manifest keys on them, so a Dataset that gains or loses a mount between two
    listings has to produce the same rows both times.
    """
    tree = {"README.md": 12, "raw/train.csv": 400, "raw/2024/part-0.csv": 7}
    for rel, size in tree.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * size)

    mounted = _provider().list_files(Asset(id="i1", name="ds", mount_path=str(tmp_path)))
    _install(monkeypatch, _FakeApi(snapshots=[_snapshot("s1", 0)],
                                   rows=[_row(rel, size) for rel, size in tree.items()]))
    unmounted = _provider().list_files(Asset(id="i1", name="ds"))

    assert [(f.path, f.size) for f in unmounted.files] == [(f.path, f.size) for f in mounted.files]


def test_a_directory_row_is_not_reported_as_an_empty_file(monkeypatch):
    """Directories come back as rows of their own, carrying a null size.

    Kept, a folder reads as a 0-byte file — the exact symptom this change exists to end, and worse
    than before, because now it is a thing that is not a file at all.
    """
    _install(monkeypatch, _FakeApi(
        snapshots=[_snapshot("s1", 0)],
        rows=[_row("autodoc_mrm/autodoc_output", None, is_directory=True),
              _row("autodoc_mrm/autodoc_output/report.docx", 8)],
    ))
    files = _provider().list_files(Asset(id="i1", name="autodoc")).files

    assert [(f.path, f.size) for f in files] == [("autodoc_mrm/autodoc_output/report.docx", 8)]


def test_the_listing_describes_the_highest_active_snapshot(monkeypatch):
    """`tag_snapshots` cannot answer this: it is empty for an untagged Dataset, which is the norm.

    A Pending snapshot has a higher version and no files to show yet, so version alone is not the
    rule — the highest ACTIVE one is.
    """
    api = _install(monkeypatch, _FakeApi(
        snapshots=[_snapshot("s0", 0), _snapshot("s2", 2), _snapshot("s1", 1),
                   _snapshot("s3", 3, status="Pending"), _snapshot("sx", 9, status="Deleted")],
        rows=[_row("f.csv", 1)],
    ))
    _provider().list_files(Asset(id="i1", name="shared_ds"))

    assert api.asked[1][0].endswith("/datasetrw/snapshot/s2/files/recursive")


def test_a_dataset_with_nothing_committed_yet_lists_nothing_and_asks_no_further(monkeypatch):
    """No Active snapshot is an empty Dataset, not a failure: there is nothing to have listed."""
    api = _install(monkeypatch, _FakeApi(snapshots=[_snapshot("s0", 0, status="Pending")]))
    listing = _provider().list_files(Asset(id="i1", name="fresh_ds"))

    assert listing.files == [] and listing.truncated is False
    assert len(api.asked) == 1


def test_a_mounted_dataset_still_reads_from_disk(tmp_path, monkeypatch):
    # The mount stays a fast path: no API call at all when the files are already here.
    (tmp_path / "on_disk.csv").write_text("a,b\n")
    api = _install(monkeypatch, _FakeApi(snapshots=[_snapshot("s1", 0)], rows=[_row("nope", 1)]))
    files = _provider().list_files(
        Asset(id="i1", name="local_ds", mount_path=str(tmp_path))).files

    assert [f.path for f in files] == ["on_disk.csv"]
    assert api.asked == []


def test_listing_failure_is_reported_not_swallowed(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("proxy said no")))
    with pytest.raises(ResourceUnavailable, match="did not answer"):
        _provider().list_files(Asset(id="i1", name="shared_ds"))


def test_a_refused_listing_reports_the_status_rather_than_an_empty_dataset(monkeypatch):
    """403 on a Dataset shared read-only has to read as refused, never as "it has no files"."""
    _install(monkeypatch, _FakeApi(status=403))

    with pytest.raises(ResourceUnavailable, match="answered 403 for the files"):
        _provider().list_files(Asset(id="i1", name="shared_ds"))


def test_download_file_writes_the_bytes_and_reports_the_size(tmp_path):
    ds = _FakeDataset()
    dest = tmp_path / "out" / "train.csv"
    provider = _provider(_FakeDatasetClient(ds))
    size = provider.download_file(Asset(id="i1", name="shared_ds"), "raw/train.csv", dest)

    assert size == 7 and dest.read_bytes() == b"x" * 7
    assert ds.downloaded == [("raw/train.csv", str(dest))]
    # Reading one file stays on the SDK, and it needs both halves of the identifier or Domino
    # rejects the lookup.
    assert provider._dataset_client.asked == ["dataset-shared_ds-i1"]


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


# --- the brand overlay (#110) -------------------------------------------------------------------
#
# The Asset provider carries the canonical actor string, and its sentences put three roles side by
# side: the platform that did something, the noun the platform provisions, and text Sage did not
# write — the Dataset name the person chose. Each gets its own treatment inside the one string.


@pytest.fixture
def _oem_pack(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr("sage.orchestrator.brand._BAKED", tmp_path / "none.json")
    pack = tmp_path / "brand.json"
    pack.write_text(json.dumps({
        "productName": "Acme", "assistantName": "Ada", "platformName": "Acme Cloud",
        "nouns": {"dataset": {"singular": "Cube", "plural": "Cubes"}},
    }))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(pack))


def test_a_listing_failure_renames_the_platform_and_the_noun_but_not_the_users_name(
        _oem_pack, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("proxy said no")))

    with pytest.raises(ResourceUnavailable) as e:
        _provider().list_files(Asset(id="i1", name="domino_shared"))

    # The platform renamed, the noun renamed, and the name the person gave their Dataset left
    # exactly as they typed it — including the word it happens to contain.
    assert str(e.value) == "Acme Cloud did not answer for the files in Cube domino_shared (RuntimeError)."


def test_a_download_failure_leaves_the_path_the_creator_asked_for_alone(_oem_pack, tmp_path):
    class _Boom:
        def download_file(self, rel, local):
            raise OSError("gone")

    with pytest.raises(ResourceUnavailable) as e:
        _provider(_FakeDatasetClient(_Boom())).download_file(
            Asset(id="i1", name="domino_shared"), "domino/train.csv", tmp_path / "out" / "train.csv",
        )

    assert str(e.value) == "Acme Cloud did not send domino/train.csv from Cube domino_shared (OSError)."


def test_the_unconfigured_providers_refusals_name_the_packs_brands(_oem_pack):
    with pytest.raises(ResourceUnavailable) as listing:
        UnconfiguredAssetProvider().list_datasets(None)
    with pytest.raises(ResourceUnavailable) as reading:
        UnconfiguredAssetProvider().download_file(Asset(id="i1", name="ds"), "a.csv", Path("a.csv"))

    assert str(listing.value) == (
        "Ada lists Cubes from the Acme Cloud API, and it is not configured to reach one, "
        "so it cannot tell which Cubes you have."
    )
    assert str(reading.value) == (
        "Ada reads Cube files through the Acme Cloud API, and it is not configured to reach one."
    )


def test_the_api_paths_survive_the_rename(_oem_pack, monkeypatch):
    """The sentence renames the platform; the endpoint it names is a literal and does not move."""
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    provider = DominoAssetProvider("http://domino", lambda: "t", mount_roots=[])

    with pytest.raises(ResourceUnavailable) as e:
        provider.list_datasets(None)

    assert str(e.value) == (
        "The Acme Cloud API didn't answer at /api/datasetrw/v2/datasets (OSError)."
    )


def test_a_non_json_listing_body_renames_the_platform_and_the_noun(_oem_pack, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, text="<html>signed out"))
    provider = DominoAssetProvider("http://domino", lambda: "t", mount_roots=[])

    with pytest.raises(ResourceUnavailable) as e:
        provider.list_datasets(None)

    assert str(e.value).startswith("The Acme Cloud API returned a non-JSON body listing Cubes.")
