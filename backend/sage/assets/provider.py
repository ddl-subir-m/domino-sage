"""Asset explorer / dataset provider (SPEC C6, Step 6).

Lists the project's Domino datasets with their tags. Sensitivity = presence of a configurable
tag name (default "sensitive"); attaching such a dataset triggers the sovereign lock. Domino
dataset tags are freeform, so the sensitivity tag is a convention we read, not a built-in field.

Deep module, narrow interface: list_datasets(project_id) -> [Asset]. Two adapters:
  - DominoAssetProvider : real, via /v4/datasetUi (works in a Domino workspace)
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
            Asset("ds_sales", "sales_2026", tags=["curated"]),
            Asset("ds_pii", "customer_pii", tags=["sensitive"]),  # triggers the sovereign lock
            Asset("ds_logs", "app_logs", tags=[]),
        ]
    )

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        return list(self.assets)


def parse_tags(raw: Any) -> list[str]:
    """Domino tags come as strings or {name|tag: ...} objects; normalize to names."""
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
    """Reads datasets via the Domino v4 platform API. Needs DOMINO_API_HOST + a token.

    TODO(workspace verify): confirm the exact /v4/datasetUi/collections/byProject response
    shape (field names for id/name/tags). Parsing is defensive.
    """

    def __init__(self, api_host: str, token_provider: Callable[[], str], timeout_s: float = 20.0) -> None:
        self._api_host = api_host.rstrip("/")
        self._token_provider = token_provider
        self._timeout_s = timeout_s

    def list_datasets(self, project_id: str | None) -> list[Asset]:
        import httpx

        url = f"{self._api_host}/v4/datasetUi/collections/byProject"
        headers = {"Authorization": f"Bearer {self._token_provider()}"}
        params = {"projectId": project_id} if project_id else {}
        r = httpx.get(url, headers=headers, params=params, timeout=self._timeout_s)
        r.raise_for_status()
        data = r.json()
        collections = data.get("data") or data if isinstance(data, (list, dict)) else []
        if isinstance(collections, dict):
            collections = collections.get("collections") or collections.get("datasets") or []
        out: list[Asset] = []
        for c in collections:
            out.append(
                Asset(
                    id=str(c.get("id") or c.get("datasetId") or c.get("collectionId") or ""),
                    name=str(c.get("name") or c.get("datasetName") or "unnamed"),
                    tags=parse_tags(c.get("tags")),
                )
            )
        return out
