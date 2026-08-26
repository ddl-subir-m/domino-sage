"""Asset explorer / dataset provider (SPEC C6, Step 6).

Lists the project's Domino datasets with their tags. Domino dataset tags are freeform.

Deep module, narrow interface: list_datasets(project_id) -> [Asset]. Two adapters:
  - DominoAssetProvider : real, via /api/datasetrw/v2/datasets
  - FakeAssetProvider   : in-memory, for tests
  - UnconfiguredAssetProvider : no Domino API host — the rail reports a reason, not fake rows
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..resources.provider import ResourceUnavailable

# Where Domino mounts a project's datasets in the running container. DFS projects use
# /domino/datasets/local; git-based projects use /mnt/data (local) and /mnt/imported/data (shared).
# DOMINO_DATASET_MOUNT_PATH / DOMINO_MOUNT_PATHS override (os.pathsep- or comma-separated).
DEFAULT_DATASET_MOUNT_ROOTS = ("/domino/datasets/local", "/mnt/data", "/mnt/imported/data")
# Backstop so a pathological dataset (millions of files) can't wedge a file listing.
_MAX_FILES = 5000


def resolve_mount_roots(env: dict[str, str] | None = None) -> list[str]:
    """Dataset mount roots to probe, env overrides first, then the Domino defaults (deduped)."""
    env = env if env is not None else dict(os.environ)
    roots: list[str] = []
    for key in ("DOMINO_DATASET_MOUNT_PATH", "DOMINO_MOUNT_PATHS"):
        raw = env.get(key)
        if raw:
            roots += [p.strip() for p in raw.replace(os.pathsep, ",").split(",") if p.strip()]
    roots += list(DEFAULT_DATASET_MOUNT_ROOTS)
    seen: set[str] = set()
    return [r for r in roots if not (r in seen or seen.add(r))]


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    tags: list[str] = field(default_factory=list)
    project: str | None = None  # owning project name
    mount_path: str | None = None  # absolute in-container path where this dataset is mounted
    # {tagName: snapshotId} from the datasetrw v2 map. Tagging attaches to a snapshot, so this lets
    # us tag an already-tagged dataset without a snapshot fetch (an untagged one still needs one).
    tag_snapshots: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetFile:
    path: str  # POSIX path relative to the dataset's mount root (e.g. "raw/train.csv")
    size: int  # bytes


def walk_files(root: Path) -> list[DatasetFile]:
    """List regular files under a dataset mount, relative + sized. Skips dotfiles and caps count."""
    out: list[DatasetFile] = []
    for p in sorted(root.rglob("*")):
        if len(out) >= _MAX_FILES:
            break
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        out.append(DatasetFile(p.relative_to(root).as_posix(), size))
    return out


class AssetProvider(Protocol):
    def list_datasets(self, project_id: str | None) -> list[Asset]: ...
    def list_files(self, asset: Asset) -> list[DatasetFile]: ...


class UnconfiguredAssetProvider:
    """No DOMINO_API_HOST. Raises rather than inventing datasets, so the rail cannot look populated."""

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        raise ResourceUnavailable(
            "Sage lists Datasets from the Domino API, and it is not configured to reach one, "
            "so it cannot tell which Datasets you have."
        )

    def list_files(self, asset: Asset) -> list[DatasetFile]:
        return []


# name -> (tags, project, {filename: contents}). Seeded to a temp dir so the file-attach flow
# (which symlinks real bytes into the workspace) works end-to-end on a local Mac with no Domino.
_FAKE_SPEC = {
    "sales_2026": (["curated"], "Revenue", {"train.csv": "month,revenue\n2026-01,120000\n2026-02,138500\n", "README.md": "Monthly revenue.\n"}),
    "customer_pii": ([], "Revenue", {"customers.csv": "id,email,ssn\n1,a@x.com,000-00-0001\n"}),
    "app_logs": ([], "Platform", {"2026-07.log": "INFO boot ok\nWARN slow query 812ms\n"}),
}


@dataclass
class FakeAssetProvider:
    """In-memory datasets for local testing/demo (no Domino). Seeds sample files under a temp
    mount root so attaching (symlinking) real bytes into the workspace works off-Domino."""

    root: Path | None = None
    assets: list[Asset] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.root is None:
            import tempfile

            self.root = Path(tempfile.mkdtemp(prefix="sage-fake-datasets-"))
        seeded: list[Asset] = []
        for name, (tags, proj, files) in _FAKE_SPEC.items():
            d = self.root / name
            d.mkdir(parents=True, exist_ok=True)
            for fn, content in files.items():
                fp = d / fn
                if not fp.exists():
                    fp.write_text(content)
            seeded.append(Asset(f"ds_{name}", name, tags=tags, project=proj, mount_path=str(d)))
        self.assets = seeded

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        return list(self.assets)

    def list_files(self, asset: Asset) -> list[DatasetFile]:
        return walk_files(Path(asset.mount_path)) if asset.mount_path else []


def parse_tags(raw: Any) -> list[str]:
    """Normalize Domino dataset tags to a list of tag names.

    The datasetrw v2 API returns tags as a map ``{tagName: snapshotId}`` — the tag NAMES are
    the keys. Older/other shapes (list of strings or {name|tag|tagName: ...} objects) are still
    accepted so callers and tests don't have to care which endpoint produced the data.
    """
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    out: list[str] = []
    for t in raw or []:
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict):
            name = t.get("name") or t.get("tag") or t.get("tagName")
            if name:
                out.append(str(name))
    return out


def parse_tag_snapshots(raw: Any) -> dict[str, str]:
    """Extract the ``{tagName: snapshotId}`` map from the datasetrw v2 tags field.

    Tagging attaches to a snapshot, so keeping this map lets us tag an already-tagged dataset
    without a separate snapshot fetch. Only the v2 dict shape carries snapshot ids; other shapes
    (list of strings/objects) have none, so we return an empty map for them.
    """
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v}
    return {}


class DominoAssetProvider:
    """Reads datasets via the Domino public datasetrw v2 API.

    Needs DOMINO_API_HOST + a token. Every dataset this caller can read is listed; `mount_path` is
    set only when this container actually has the files (a Domino workspace). Off-Domino the rail
    can still name them; attaching a file from one still needs the mount.
    """

    _PAGE = 100
    _MAX_PAGES = 100  # 10k-dataset backstop against a non-terminating pager

    def __init__(
        self,
        api_host: str,
        token_provider: Callable[[], str],
        timeout_s: float = 20.0,
        mount_roots: list[str] | None = None,
    ) -> None:
        self._api_host = api_host.rstrip("/")
        self._token_provider = token_provider
        self._timeout_s = timeout_s
        self._mount_roots = mount_roots if mount_roots is not None else resolve_mount_roots()

    def _mount_path_for(self, name: str) -> str | None:
        for root in self._mount_roots:
            p = Path(root) / name
            if p.is_dir():
                return str(p)
        return None

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        import httpx

        if not self._api_host:
            raise ResourceUnavailable(
                "Sage lists Datasets from the Domino API, and it is not configured to reach one, "
                "so it cannot tell which Datasets you have."
            )
        url = f"{self._api_host}/api/datasetrw/v2/datasets"
        found: list[Asset] = []
        offset = 0
        for _ in range(self._MAX_PAGES):
            params = {
                "minimumPermission": "ReadDatasetRwV2",  # only datasets the user can access
                "includeProjectInfo": "true",
                "offset": offset,
                "limit": self._PAGE,
            }
            try:
                headers = {"Authorization": f"Bearer {self._token_provider()}"}
                r = httpx.get(url, headers=headers, params=params, timeout=self._timeout_s)
            except ResourceUnavailable:
                raise
            except Exception as e:
                raise ResourceUnavailable(
                    f"The Domino API didn't answer at /api/datasetrw/v2/datasets ({type(e).__name__})."
                ) from e
            if r.status_code >= 400:
                raise ResourceUnavailable(
                    f"The Domino API answered {r.status_code} at /api/datasetrw/v2/datasets."
                )
            try:
                data = r.json()
            except ValueError as e:
                raise ResourceUnavailable(
                    "The Domino API returned a non-JSON body listing Datasets. "
                    "That is what a signed-out session looks like, so this builder's token for it "
                    "may have expired."
                ) from e
            items = data.get("datasets") or data.get("data") or []
            for item in items:
                ds = item.get("dataset") or item
                name = str(ds.get("name") or "unnamed")
                proj = (item.get("projectInfo") or {}).get("name")
                found.append(
                    Asset(
                        id=str(ds.get("id") or ""),
                        name=name,
                        tags=parse_tags(ds.get("tags")),
                        project=str(proj) if proj else None,
                        mount_path=self._mount_path_for(name),
                        tag_snapshots=parse_tag_snapshots(ds.get("tags")),
                    )
                )
            total = (data.get("metadata") or {}).get("totalCount")
            offset += self._PAGE
            if not items or (total is not None and offset >= total):
                break
        return found

    def list_files(self, asset: Asset) -> list[DatasetFile]:
        return walk_files(Path(asset.mount_path)) if asset.mount_path else []
