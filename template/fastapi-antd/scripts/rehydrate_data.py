#!/usr/bin/env python3
"""Rebuild `public/data/` in a published App from the committed manifest.

`public/data/` is gitignored — attached and uploaded data never ships in git, so sensitive rows
cannot leak into the app's repo. The builder records every attachment in the committed manifest
`.sage/attachments.json`; this script recreates the files under `public/data/` on the App hardware,
so `data/...` resolves in the PUBLISHED app. Runs from `app.sh` before the server starts. Never
fails the publish: an App whose data cannot be rehydrated still serves, and says so in one line.

Two steps, in order. First the dataset MOUNTS: a mount covers this project and is fixed when the
execution starts, so what the App's hardware already has on disk is linked, not copied. Then the
Domino data library, for what the mounts could not provide — a Dataset shared from another project,
or one added after the execution started — downloaded the same way the builder read them.

fastapi-antd is the one stack Sage seeds (#490, one-app pivot) and it is Python end to end, so both
steps live here in one script.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MANIFEST = Path(".sage/attachments.json")
DATA_DIR = Path("public/data")
# Mirror backend/sage/assets/provider.py resolve_mount_roots(): env overrides first, then defaults.
DEFAULT_ROOTS = ("/domino/datasets/local", "/mnt/data", "/mnt/imported/data")


def _entries(manifest: Path) -> list[dict]:
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _safe_dest(root: Path, rel: str) -> Path | None:
    """The file this entry names, or None if it points anywhere but public/data/.

    The manifest is Sage's own and travels in the app's repo, but this step writes to disk from it,
    so the destination is checked rather than trusted.
    """
    if not rel.startswith("public/data/"):
        return None
    dest = (root / rel).resolve()
    base = (root / DATA_DIR).resolve()
    return dest if dest.is_relative_to(base) else None


# ---- step one: the mounts ------------------------------------------------------------------------


def mount_roots(environ: dict | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    roots: list[str] = []
    for key in ("DOMINO_DATASET_MOUNT_PATH", "DOMINO_MOUNT_PATHS"):
        raw = env.get(key)
        if raw:
            roots.extend(p.strip() for p in raw.replace(",", ":").split(":") if p.strip())
    roots.extend(DEFAULT_ROOTS)
    return list(dict.fromkeys(roots))


def mount_for(dataset: str, roots: list[str]) -> Path | None:
    for root in roots:
        candidate = Path(root) / dataset
        if candidate.is_dir():
            return candidate
    return None


def link_mounts(root: Path, roots: list[str] | None = None) -> tuple[int, int]:
    """Symlink every manifest entry the App's dataset mounts hold. Returns (linked, left)."""
    roots = mount_roots() if roots is None else roots
    linked = left = 0
    for entry in _entries(root / MANIFEST):
        dest = _safe_dest(root, str(entry.get("path") or ""))
        dataset = str(entry.get("dataset") or "")
        ds_rel = str(entry.get("dataset_rel_path") or entry.get("file") or "")
        if dest is None or not dataset or not ds_rel:
            continue
        mount = mount_for(dataset, roots)
        src = mount / ds_rel if mount else None
        if src is None or not src.exists():
            left += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            dest.symlink_to(src)
            linked += 1
        except OSError as e:
            print(f"[rehydrate] {dest.relative_to(root)}: {e}")
    return linked, left


# ---- step two: the data library ------------------------------------------------------------------


def _unique_name(entry: dict) -> str:
    """`dataset-<name>-<id>`, the only identifier the data library accepts.

    The manifest records the id as the builder received it, which may still carry the rail's
    `dataset:` prefix, so strip that before rebuilding the composite. Both halves are required:
    a bare name and a bare id are each rejected.
    """
    name = str(entry.get("dataset") or "")
    ds_id = str(entry.get("dataset_id") or "").removeprefix("dataset:")
    return f"dataset-{name}-{ds_id}" if name and ds_id else ""


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
        except Exception as e:            # one unreachable Dataset must not cost the others  # noqa: BLE001
            unavailable += 1
            print(f"[rehydrate] {ds_rel}: {type(e).__name__}: {e}")
    return fetched, unavailable


def main() -> int:
    root = Path.cwd()
    linked, left = link_mounts(root)
    if linked or left:
        # "left to fetch", not "unavailable": the download step runs next and usually answers.
        print(f"[rehydrate] linked {linked} data file(s)" + (f", {left} left to fetch" if left else ""))
    fetched, unavailable = rehydrate(root)
    if fetched or unavailable:
        tail = f", {unavailable} unavailable" if unavailable else ""
        print(f"[rehydrate] fetched {fetched} data file(s){tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
