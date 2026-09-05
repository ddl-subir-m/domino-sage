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

from ..orchestrator import brand
from ..resources.provider import ResourceUnavailable

# Where Domino mounts a project's datasets in the running container. DFS projects use
# /domino/datasets/local; git-based projects use /mnt/data (local) and /mnt/imported/data (shared).
# DOMINO_DATASET_MOUNT_PATH / DOMINO_MOUNT_PATHS override (os.pathsep- or comma-separated).
DEFAULT_DATASET_MOUNT_ROOTS = ("/domino/datasets/local", "/mnt/data", "/mnt/imported/data")
# How many files a listing returns, and how far it will stat to build one. It does NOT bound the
# directory traversal: `walk_files` sorts the whole mount before taking a prefix, because the
# prefix has to be the sorted one (ADR-0029) and nothing can know which names sort first without
# seeing all of them. A pathological mount still costs one full traversal per listing.
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


def dataset_unique_name(asset: Asset) -> str:
    """The identifier the Domino data library answers to for one Dataset.

    Domino registers every Dataset as a Data Source named `dataset-<name>-<id>`, and that composite
    is the only thing `DatasetClient.get_dataset` accepts — a bare name and a bare id are both
    rejected. Verified against every Dataset on a live deployment, including two that share the name
    `prediction_data` and are told apart only by id.
    """
    return f"dataset-{asset.name}-{asset.id}"


@dataclass(frozen=True)
class DatasetFile:
    path: str  # POSIX path relative to the dataset's mount root (e.g. "raw/train.csv")
    size: int  # bytes, real on both paths: stat() when mounted, the API rows when not


@dataclass(frozen=True)
class FileListing:
    """A Dataset's files, and whether the listing stopped short of the end of them.

    The mounted walk is sorted, so its cap cuts the tail: early folders are whole, late ones are
    cut or missing, and nothing downstream can tell which is which (ADR-0029). `truncated` is what
    lets a caller refuse an act it cannot prove the scope of, rather than act on a silent partial
    answer.
    """

    files: list[DatasetFile]
    truncated: bool = False


def walk_files(root: Path) -> FileListing:
    """List regular files under a dataset mount, relative + sized. Skips dotfiles and caps count."""
    out: list[DatasetFile] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        # Measured after the filters, so the cap is reported cut only by a file it would have
        # listed. A `.ipynb_checkpoints` directory sorting last is not a lost tail.
        if len(out) >= _MAX_FILES:
            return FileListing(out, truncated=True)
        out.append(DatasetFile(p.relative_to(root).as_posix(), size))
    return FileListing(out)


class AssetProvider(Protocol):
    def list_datasets(self, project_id: str | None) -> list[Asset]: ...
    def list_files(self, asset: Asset) -> FileListing: ...
    def download_file(self, asset: Asset, rel_path: str, dest: Path) -> int: ...


class UnconfiguredAssetProvider:
    """No DOMINO_API_HOST. Raises rather than inventing datasets, so the rail cannot look populated."""

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        raise ResourceUnavailable(brand.text(
            "{assistantName} lists {datasetPlural} from the {platformName} API, and it is not "
            "configured to reach one, so it cannot tell which {datasetPlural} you have."
        ))

    def list_files(self, asset: Asset) -> FileListing:
        return FileListing([])

    def download_file(self, asset: Asset, rel_path: str, dest: Path) -> int:
        raise ResourceUnavailable(brand.text(
            "{assistantName} reads {dataset} files through the {platformName} API, and it is not "
            "configured to reach one."
        ))


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

    def list_files(self, asset: Asset) -> FileListing:
        return walk_files(Path(asset.mount_path)) if asset.mount_path else FileListing([])

    def download_file(self, asset: Asset, rel_path: str, dest: Path) -> int:
        import shutil

        src = Path(asset.mount_path or "") / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest.stat().st_size


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

    Needs DOMINO_API_HOST + a token. Every dataset this caller can read is listed, and every one of
    them can be read: `mount_path` is set only when this container happens to have the files on
    disk, and that is a fast path, not a gate. A mount covers one project and is fixed when the
    execution starts, so most Datasets a person can read — including every Dataset shared with them
    from another project — are never mounted here. Those are listed straight off the datasetrw API
    and read a file at a time through `domino_data`.
    """

    _PAGE = 100
    _MAX_PAGES = 100  # 10k-dataset backstop against a non-terminating pager

    def __init__(
        self,
        api_host: str,
        token_provider: Callable[[], str],
        timeout_s: float = 20.0,
        mount_roots: list[str] | None = None,
        dataset_client: Any | None = None,
    ) -> None:
        self._api_host = api_host.rstrip("/")
        self._token_provider = token_provider
        self._timeout_s = timeout_s
        self._mount_roots = mount_roots if mount_roots is not None else resolve_mount_roots()
        self._dataset_client = dataset_client  # injected in tests; built per call otherwise

    def _sdk_dataset(self, asset: Asset) -> Any:
        """A `domino_data` handle for one Dataset, whether or not it is mounted here.

        `DatasetClient()` is built with no arguments on purpose. It reads DOMINO_API_PROXY — the
        same localhost:8899 sidecar Sage already mints its own tokens from — and exchanges it for
        the JWT the datasource-proxy wants. Passing an account API key as `token=` instead is
        rejected with "Your role does not authorize you to perform this action", so the no-argument
        form is not a shortcut, it is the working one. `DataSourceClient()` is constructed the same
        way in `resources/provider.py`, and per call for the same reason: these tokens expire.
        """
        if self._dataset_client is not None:
            return self._dataset_client.get_dataset(dataset_unique_name(asset))
        try:
            from domino_data.datasets import DatasetClient
        except ImportError as e:
            raise ResourceUnavailable(brand.text(
                "{assistantName} reads {dataset} files through the {platformName} data library, "
                "which is not installed here. {datasetPlural} will still list, but "
                "{assistantName} cannot look inside one."
            )) from e
        return DatasetClient().get_dataset(dataset_unique_name(asset))

    def _mount_path_for(self, name: str) -> str | None:
        for root in self._mount_roots:
            p = Path(root) / name
            if p.is_dir():
                return str(p)
        return None

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        import httpx

        if not self._api_host:
            raise ResourceUnavailable(brand.text(
                "{assistantName} lists {datasetPlural} from the {platformName} API, and it is not "
                "configured to reach one, so it cannot tell which {datasetPlural} you have."
            ))
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
                    brand.text(
                        "The {platformName} API didn't answer at /api/datasetrw/v2/datasets ({err}).",
                        err=type(e).__name__,
                    )
                ) from e
            if r.status_code >= 400:
                raise ResourceUnavailable(
                    brand.text(
                        "The {platformName} API answered {code} at /api/datasetrw/v2/datasets.",
                        code=r.status_code,
                    )
                )
            try:
                data = r.json()
            except ValueError as e:
                raise ResourceUnavailable(brand.text(
                    "The {platformName} API returned a non-JSON body listing {datasetPlural}. "
                    "That is what a signed-out session looks like, so this builder's token for it "
                    "may have expired."
                )) from e
            items = data.get("datasets") or data.get("data") or []
            for item in items:
                ds = item.get("dataset") or item
                name = str(ds.get("name") or "unnamed")
                # `projectName`, not `name`: DatasetRwProjectInfoDtoV1 is
                # {projectId, projectName, projectOwnerUsername}. Reading `name` here silently left
                # every row's owning project blank, which is the one label that explains why a
                # Dataset from someone else's project is not on this container's disk.
                proj = (item.get("projectInfo") or {}).get("projectName")
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

    def _files_api_json(self, path: str, params: dict[str, Any], asset: Asset) -> Any:
        """GET one datasetrw endpoint about a Dataset's files, or refuse naming that Dataset.

        The prefix is `/v4`, not the `/api` that `list_datasets` uses: `swagger.json` declares
        `"servers": [{"url": "/v4"}]`, and `/api/datasetrw/snapshots/{id}` answers 404 where
        `/v4/...` answers 200. The provider calling two different prefixes is correct, not a
        tidy-up waiting to happen.
        """
        import httpx

        try:
            headers = {"Authorization": f"Bearer {self._token_provider()}"}
            r = httpx.get(f"{self._api_host}/v4/{path}", headers=headers, params=params,
                          timeout=self._timeout_s)
            data = r.json() if r.status_code < 400 else None
        except ResourceUnavailable:
            raise
        except Exception as e:
            raise ResourceUnavailable(
                brand.text(
                    "{platformName} did not answer for the files in {dataset} {name} ({err}).",
                    name=asset.name, err=type(e).__name__,
                )
            ) from e
        if r.status_code >= 400:
            raise ResourceUnavailable(
                brand.text(
                    "{platformName} answered {code} for the files in {dataset} {name}.",
                    name=asset.name, code=r.status_code,
                )
            )
        return data

    def _latest_snapshot_id(self, asset: Asset) -> str | None:
        """The snapshot whose files a listing should describe: the highest Active version.

        `Asset.tag_snapshots` cannot answer this — it holds a snapshot id only for a Dataset
        somebody tagged, and an untagged one is the common case. `snapshots/{datasetId}` answers for
        both: a bare JSON array of every snapshot, no envelope and no paging. `None` means the
        Dataset has nothing committed yet, which is an empty listing rather than a failure.
        """
        rows = self._files_api_json(f"datasetrw/snapshots/{asset.id}", {}, asset) or []
        active = [s for s in rows if s.get("lifecycleStatus") == "Active"]
        if not active:
            return None
        return str(max(active, key=lambda s: s.get("version") or 0).get("id") or "") or None

    def list_files(self, asset: Asset) -> FileListing:
        """The files in a Dataset, sized: from the mount when this container has one, from the
        datasetrw API when it does not.

        The API path used to go through `domino_data`, whose listing endpoint returns names and
        nothing else, so every file in an unmounted Dataset reported 0 bytes. `files/recursive`
        carries the sizes the platform already knows, and it returns the whole tree in one response
        — no `page_size`, no continuation token — so the cap is applied here and `truncated` is
        measured against the real count instead of inferred from a full page.
        """
        if asset.mount_path:
            return walk_files(Path(asset.mount_path))
        snapshot_id = self._latest_snapshot_id(asset)
        if not snapshot_id:
            return FileListing([])
        # `path` is a required query parameter and must be EMPTY for the root: `?path=/` answers
        # 400 "Invalid path input", `?path=` answers 200.
        body = self._files_api_json(
            f"datasetrw/snapshot/{snapshot_id}/files/recursive", {"path": ""}, asset)
        files: list[DatasetFile] = []
        for row in (body or {}).get("rows") or []:
            name = row.get("name") or {}
            # Directories come back as rows of their own, with a null size. Kept, a folder would
            # read as a 0-byte file — the very thing this listing is here to stop reporting.
            if name.get("isDirectory"):
                continue
            # `fileName` is the full path relative to the Dataset root and already POSIX, so it
            # agrees with what `walk_files` produces for the same tree. `label` is only the
            # basename, and reading it would collapse every nested file onto its siblings.
            path = str(name.get("fileName") or name.get("label") or "")
            if not path:
                continue
            files.append(DatasetFile(path, int((row.get("size") or {}).get("sizeInBytes") or 0)))
        # Sorted before the cut, because the cap has to take the sorted prefix (ADR-0029) and the
        # rows do not arrive in that order. Counted before it too: the whole tree is in hand, so
        # `truncated` is a fact here rather than the inference the paged SDK listing had to make.
        files.sort(key=lambda f: f.path)
        return FileListing(files[:_MAX_FILES], truncated=len(files) > _MAX_FILES)

    def download_file(self, asset: Asset, rel_path: str, dest: Path) -> int:
        """Copy one file out of a Dataset this container has no mount for. Returns bytes written."""
        dataset = self._sdk_dataset(asset)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dataset.download_file(rel_path, str(dest))
        except Exception as e:
            raise ResourceUnavailable(
                brand.text(
                    "{platformName} did not send {path} from {dataset} {name} ({err}).",
                    path=rel_path, name=asset.name, err=type(e).__name__,
                )
            ) from e
        return dest.stat().st_size
