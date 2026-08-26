#!/usr/bin/env python3
"""Fetch the data files the dataset mounts could not provide, using the committed manifest.

Second of two rehydrate steps. `rehydrate-data.mjs` runs first and links everything this App's
hardware already has on disk. What it leaves behind is not missing data — it is data in a Dataset
this container has no mount for, which is the ordinary case for a Dataset shared from another
project and for one added after the execution started. Those files are downloaded here through the
Domino data library, the same way the builder read them in the first place.

Runs BEFORE `vite build` (see app.sh) so the bytes are baked into dist/. Never fails the publish:
an App whose data cannot be fetched still builds and serves, and says so in one line.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MANIFEST = Path(".sage/attachments.json")
DATA_DIR = Path("public/data")


def _entries(manifest: Path) -> list[dict]:
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _unique_name(entry: dict) -> str:
    """`dataset-<name>-<id>`, the only identifier the data library accepts.

    The manifest records the id as the builder received it, which may still carry the rail's
    `dataset:` prefix, so strip that before rebuilding the composite. Both halves are required:
    a bare name and a bare id are each rejected.
    """
    name = str(entry.get("dataset") or "")
    ds_id = str(entry.get("dataset_id") or "").removeprefix("dataset:")
    return f"dataset-{name}-{ds_id}" if name and ds_id else ""


def _safe_dest(root: Path, rel: str) -> Path | None:
    """The file this entry names, or None if it points anywhere but public/data/.

    The manifest is Sage's own and travels in the app's repo, but this step writes bytes to disk
    from it, so the destination is checked rather than trusted.
    """
    if not rel.startswith("public/data/"):
        return None
    dest = (root / rel).resolve()
    base = (root / DATA_DIR).resolve()
    return dest if dest.is_relative_to(base) else None


def rehydrate(root: Path, get_dataset=None) -> tuple[int, int]:
    """Download every manifest entry not already on disk. Returns (fetched, unavailable)."""
    wanted = []
    for entry in _entries(root / MANIFEST):
        dest = _safe_dest(root, str(entry.get("path") or ""))
        ds_rel = str(entry.get("dataset_rel_path") or entry.get("file") or "")
        if dest is None or not ds_rel or dest.exists():
            continue                      # no destination, or the mount already answered for it
        wanted.append((dest, ds_rel, _unique_name(entry)))
    if not wanted:
        return 0, 0

    if get_dataset is None:
        try:
            from domino_data.datasets import DatasetClient
        except ImportError:
            print(f"[rehydrate] {len(wanted)} data file(s) are in Datasets this App has not "
                  "mounted, and the Domino data library is not installed here to fetch them")
            return 0, len(wanted)
        get_dataset = DatasetClient().get_dataset

    fetched = unavailable = 0
    datasets: dict[str, object] = {}      # one handle per Dataset, not per file
    for dest, ds_rel, unique in wanted:
        if not unique:
            unavailable += 1
            print(f"[rehydrate] {ds_rel}: the manifest records no Dataset id for this file")
            continue
        try:
            if unique not in datasets:
                datasets[unique] = get_dataset(unique)
            dest.parent.mkdir(parents=True, exist_ok=True)
            datasets[unique].download_file(ds_rel, str(dest))
            fetched += 1
        except Exception as e:            # one unreachable Dataset must not cost the others
            unavailable += 1
            print(f"[rehydrate] {ds_rel}: {type(e).__name__}: {e}")
    return fetched, unavailable


def main() -> int:
    fetched, unavailable = rehydrate(Path.cwd())
    if fetched or unavailable:
        tail = f", {unavailable} unavailable" if unavailable else ""
        print(f"[rehydrate] fetched {fetched} data file(s){tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
