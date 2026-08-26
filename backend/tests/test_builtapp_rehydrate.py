"""Published-app data rehydrate — the half a dataset mount cannot answer for.

`scripts/rehydrate-data.mjs` links what this App's hardware already has on disk. Everything else —
a Dataset shared from another project, or one added after the execution started — is downloaded by
`scripts/rehydrate_data.py`, which ships IN the app's repo and so is loaded by path here, the same
way test_builtapp_serve.py loads serve.py.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "template" / "react-vite" / "scripts" / "rehydrate_data.py"


def _load():
    spec = importlib.util.spec_from_file_location("builtapp_rehydrate", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # `from __future__ import annotations`, as in serve.py
    spec.loader.exec_module(mod)
    return mod


rehydrate_data = _load()


class _FakeDataset:
    def __init__(self, payload: bytes = b"a,b\n1,2\n"):
        self.payload = payload
        self.downloaded: list[str] = []

    def download_file(self, rel: str, local: str) -> None:
        self.downloaded.append(rel)
        Path(local).write_bytes(self.payload)


def _manifest(root: Path, entries: list[dict]) -> None:
    (root / ".sage").mkdir(parents=True, exist_ok=True)
    (root / ".sage" / "attachments.json").write_text(json.dumps(entries))


def _entry(**over) -> dict:
    base = {
        "dataset_id": "ds1",
        "dataset": "Oil-and-Gas-Demo",
        "file": "raw/wells.csv",
        "dataset_rel_path": "raw/wells.csv",
        "path": "public/data/oil_and_gas_demo/raw/wells.csv",
        "size": 8,
        "source": "dataset",
    }
    base.update(over)
    return base


def test_no_manifest_is_a_no_op(tmp_path: Path):
    assert rehydrate_data.rehydrate(tmp_path) == (0, 0)


def test_a_file_the_mount_already_linked_is_left_alone(tmp_path: Path):
    _manifest(tmp_path, [_entry()])
    dest = tmp_path / "public" / "data" / "oil_and_gas_demo" / "raw" / "wells.csv"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"from the mount")

    ds = _FakeDataset()
    assert rehydrate_data.rehydrate(tmp_path, get_dataset=lambda _n: ds) == (0, 0)
    assert ds.downloaded == []                       # the mount answered; nothing to fetch
    assert dest.read_bytes() == b"from the mount"


def test_an_unmounted_file_is_downloaded_by_its_composite_name(tmp_path: Path):
    _manifest(tmp_path, [_entry()])
    ds, asked = _FakeDataset(), []

    fetched, unavailable = rehydrate_data.rehydrate(
        tmp_path, get_dataset=lambda n: (asked.append(n), ds)[1]
    )

    assert (fetched, unavailable) == (1, 0)
    assert asked == ["dataset-Oil-and-Gas-Demo-ds1"]
    assert ds.downloaded == ["raw/wells.csv"]
    dest = tmp_path / "public" / "data" / "oil_and_gas_demo" / "raw" / "wells.csv"
    assert dest.read_bytes() == b"a,b\n1,2\n"


def test_the_rails_dataset_prefix_is_stripped_from_the_id(tmp_path: Path):
    # The manifest records the id as the builder received it, and the rail sends `dataset:<id>`.
    _manifest(tmp_path, [_entry(dataset_id="dataset:ds1")])
    asked = []
    rehydrate_data.rehydrate(tmp_path, get_dataset=lambda n: (asked.append(n), _FakeDataset())[1])
    assert asked == ["dataset-Oil-and-Gas-Demo-ds1"]


def test_one_unreachable_dataset_does_not_cost_the_others(tmp_path: Path):
    _manifest(tmp_path, [
        _entry(dataset="Broken", dataset_id="bad", path="public/data/broken/x.csv"),
        _entry(),
    ])

    def get_dataset(name):
        if name.startswith("dataset-Broken"):
            raise RuntimeError("proxy said no")
        return _FakeDataset()

    assert rehydrate_data.rehydrate(tmp_path, get_dataset=get_dataset) == (1, 1)
    assert (tmp_path / "public" / "data" / "oil_and_gas_demo" / "raw" / "wells.csv").is_file()


def test_an_entry_without_a_dataset_id_is_reported_not_guessed(tmp_path: Path):
    # A bare name is rejected by the data library, so there is nothing useful to try.
    _manifest(tmp_path, [_entry(dataset_id="")])
    called = []
    assert rehydrate_data.rehydrate(
        tmp_path, get_dataset=lambda n: called.append(n)
    ) == (0, 1)
    assert called == []


def test_a_manifest_path_outside_public_data_is_refused(tmp_path: Path):
    _manifest(tmp_path, [
        _entry(path="public/data/../../escaped.csv"),
        _entry(path="src/App.tsx"),
    ])
    assert rehydrate_data.rehydrate(tmp_path, get_dataset=lambda _n: _FakeDataset()) == (0, 0)
    assert not (tmp_path / "escaped.csv").exists()
