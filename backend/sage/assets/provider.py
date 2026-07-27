"""Asset explorer / dataset provider (SPEC C6, Step 6).

Lists the project's Domino datasets with their tags. Sensitivity = presence of a configurable
tag name (default "sensitive"); attaching such a dataset triggers the sovereign lock. Domino
dataset tags are freeform, so the sensitivity tag is a convention we read, not a built-in field.

Deep module, narrow interface: list_datasets(project_id) -> [Asset]. Two adapters:
  - DominoAssetProvider : real, via /api/datasetrw/v2/datasets (works in a Domino workspace)
  - FakeAssetProvider   : in-memory, for local Mac testing
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_SENSITIVITY_TAG = "sensitive"


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    tags: list[str] = field(default_factory=list)
    project: str | None = None  # owning project name, for grouping when listing across projects


def is_sensitive(asset: Asset, sensitivity_tag: str = DEFAULT_SENSITIVITY_TAG) -> bool:
    """Case-insensitive membership of the sensitivity tag in the dataset's tags."""
    want = sensitivity_tag.lower()
    return any(t.lower() == want for t in asset.tags)


class AssetProvider(Protocol):
    def list_datasets(self, project_id: str | None) -> list[Asset]: ...


@dataclass
class FakeAssetProvider:
    """In-memory datasets for local testing/demo (no Domino)."""

    assets: list[Asset] = field(
        default_factory=lambda: [
            Asset("ds_sales", "sales_2026", tags=["curated"], project="Revenue"),
            Asset("ds_pii", "customer_pii", tags=["sensitive"], project="Revenue"),  # sovereign lock
            Asset("ds_logs", "app_logs", tags=[], project="Platform"),
        ]
    )

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        return list(self.assets)


def parse_tags(raw: Any) -> list[str]:
    """Normalize Domino dataset tags to a list of tag names.

    The datasetrw v2 API returns tags as a map ``{tagName: snapshotId}`` — the tag NAMES are
    the keys. Older/other shapes (list of strings or {name|tag|tagName: ...} objects) are still
    accepted so callers and tests don't have to care which endpoint produced the data.
    """
    if isinstance(raw, dict):
        return [str(k) for k in raw.keys()]
    out: list[str] = []
    for t in raw or []:
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict):
            name = t.get("name") or t.get("tag") or t.get("tagName")
            if name:
                out.append(str(name))
    return out


class DominoAssetProvider:
    """Reads datasets via the Domino public datasetrw v2 API. Needs DOMINO_API_HOST + a token.

    Lists every dataset the caller can READ across all projects (minimumPermission scopes to
    what they have access to), so the builder can attach data from anywhere — not just the
    current project. Mirrors the AutoML extension's proven use of GET /api/datasetrw/v2/datasets.

    Each envelope item is ``{dataset: {id, name, tags{tagName: snapshotId}, projectId}, projectInfo:
    {name}}``; sensitivity is read from the tag-map keys. Parsing stays defensive against camelCase
    drift.
    """

    _PAGE = 100
    _MAX_PAGES = 100  # 10k-dataset backstop against a non-terminating pager

    def __init__(self, api_host: str, token_provider: Callable[[], str], timeout_s: float = 20.0) -> None:
        self._api_host = api_host.rstrip("/")
        self._token_provider = token_provider
        self._timeout_s = timeout_s

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        import httpx

        url = f"{self._api_host}/api/datasetrw/v2/datasets"
        headers = {"Authorization": f"Bearer {self._token_provider()}"}
        out: list[Asset] = []
        offset = 0
        for _ in range(self._MAX_PAGES):
            params = {
                "minimumPermission": "ReadDatasetRwV2",  # only datasets the user can access
                "includeProjectInfo": "true",
                "offset": offset,
                "limit": self._PAGE,
            }
            r = httpx.get(url, headers=headers, params=params, timeout=self._timeout_s)
            r.raise_for_status()
            data = r.json()
            items = data.get("datasets") or data.get("data") or []
            for item in items:
                ds = item.get("dataset") or item
                proj = (item.get("projectInfo") or {}).get("name")
                out.append(
                    Asset(
                        id=str(ds.get("id") or ""),
                        name=str(ds.get("name") or "unnamed"),
                        tags=parse_tags(ds.get("tags")),
                        project=str(proj) if proj else None,
                    )
                )
            total = (data.get("metadata") or {}).get("totalCount")
            offset += self._PAGE
            if not items or (total is not None and len(out) >= total):
                break
        return out
