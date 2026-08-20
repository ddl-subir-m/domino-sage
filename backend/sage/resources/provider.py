"""Resource provider — the Domino things a user can pick in the Resource Browser (#2, #5).

Deliberately NOT an extension of the Asset provider: an `Asset` is shaped around a Domino dataset
mounted into this container — mount path, freeform tags, snapshot ids, a list-files operation — and
none of that means anything for a model registration. Resource kinds with genuinely different
shapes get their own types rather than a widened `Asset`.

First kind: the LLM Alias. Listing one takes TWO gateway calls, not one:

    GET {gateway}/v1/models    -> the model ids THIS caller may use (already permission-filtered)
    GET {gateway}/api/aliases  -> the metadata a picker needs (display_name, capabilities, costs)

The listing is the intersection, so a registration the caller holds no grant for is never presented
as available. The two sets really do differ: verified live on cloud-dogfood 2026-08-18 (see
DOMINO-PRIMITIVES.md), one gateway reported 12 registered aliases and 6 accessible ones.

Auth is the existing Domino control-plane bearer path — a `token_provider` from
`sage.gateway.client` (the workspace sidecar in a container, a dgw_ PAT off-Domino). Nothing new.

Two adapters, as with assets:
  - DominoResourceProvider : real, against the LLM Gateway control plane (v2.0.11)
  - FakeResourceProvider   : in-memory, for local testing with no gateway
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class ResourceUnavailable(RuntimeError):
    """A Resource listing could not be produced. The message reaches the user unchanged, so it says
    what failed and what to do about it — and never carries a token or a response body."""


@dataclass(frozen=True)
class LlmAlias:
    id: str
    name: str  # what request["model"] must say — the alias is the only name Sage ever calls a model by
    display_name: str  # human label; the row's primary identifier
    description: str | None = None
    capabilities: list[str] = field(default_factory=list)  # "chat", "tools", "embeddings", …
    # `effective_costs` verbatim: {rate name -> number}. Live on Sage's gateway (2026-08-19) this is
    # a flat {"input": x, "output": y}, and the figures match the vendors' published per-1M-token USD
    # rates for sonnet (3/15), opus (5/25) and gpt-5.4 (2.5/15) — the API sends no unit, but the
    # figures are USD per 1M tokens. Nothing is normalised or relabelled here: six unrelated aliases all report
    # {1.0, 2.0}, which is the gateway falling back rather than a real price, and the gateway's own
    # Usage & cost dashboard stays the authority on what a call actually cost.
    costs: dict[str, float] = field(default_factory=dict)


class ResourceProvider(Protocol):
    def list_llm_aliases(self) -> list[LlmAlias]: ...


def records_of(payload: Any) -> list[dict]:
    """The record list out of a gateway payload.

    One helper for both calls because they disagree: `/v1/models` follows the OpenAI convention
    (`{"object": "list", "data": [...]}`) while `/api/aliases` returned a bare array. `items` is
    accepted too, as the probe that mapped these routes did (spikes/domino-probes).
    """
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data") or payload.get("items") or []
    else:
        items = []
    return [r for r in items if isinstance(r, dict)]


def accessible_ids(models_payload: Any) -> set[str]:
    """Model ids from a `/v1/models` body — the set this caller is permitted to call."""
    return {str(r["id"]) for r in records_of(models_payload) if r.get("id")}


def parse_capabilities(raw: Any) -> list[str]:
    """Capability modes as a list of strings. Guarded against a bare string, which would otherwise
    iterate into one chip per character."""
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, str)]


def parse_costs(raw: Any) -> dict[str, float]:
    """`effective_costs` as a flat {rate name -> number} map — the shape seen live.

    Keeps the numeric entries and drops everything else rather than guessing at a nesting: a row
    saying no rate was reported is honest, one showing an invented number is not. `bool` is excluded
    because it is an `int` in Python, so a flag would otherwise price at 1.
    """
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): float(v)
        for k, v in raw.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def join_aliases(accessible: set[str], records: list[dict]) -> list[LlmAlias]:
    """Intersect the accessible model ids with the alias metadata records.

    Matched on alias `name` OR `id`: `/v1/models` reports the name a call must use, which is the
    alias name on every gateway we have seen, but the control plane keys on the id and the recipe
    this follows joins on either.

    An accessible id with NO metadata record still gets a row, carrying only its name. Dropping it
    would have the panel deny a model the caller can actually use, and `/v1/models` is the authority
    on availability — a thin row is a smaller lie than a missing one.
    """
    out: list[LlmAlias] = []
    claimed: set[str] = set()
    for rec in records:
        name = str(rec.get("name") or "")
        rid = str(rec.get("id") or "")
        key = name if name in accessible else (rid if rid in accessible else "")
        if not key:
            continue
        claimed.add(key)
        out.append(
            LlmAlias(
                id=rid or name,
                name=name or rid,
                display_name=str(rec.get("display_name") or name or rid),
                description=str(rec["description"]) if rec.get("description") else None,
                capabilities=parse_capabilities(rec.get("capabilities")),
                costs=parse_costs(rec.get("effective_costs")),
            )
        )
    for extra in sorted(accessible - claimed):
        out.append(LlmAlias(id=extra, name=extra, display_name=extra))
    return out


class DominoResourceProvider:
    """LLM Aliases from the Domino LLM Gateway's control plane.

    `gateway_base_url` is the same OpenAI base Sage already routes model calls to (ending in `/v1`);
    the control plane sits at its root, so both calls come off one URL that is already configured
    and there is nothing new to set up.
    """

    def __init__(
        self,
        gateway_base_url: str,
        token_provider: Callable[[], str],
        timeout_s: float = 20.0,
    ) -> None:
        self._root = gateway_base_url.rstrip("/").removesuffix("/v1").rstrip("/")
        self._token_provider = token_provider
        self._timeout_s = timeout_s

    def list_llm_aliases(self) -> list[LlmAlias]:
        models = self._get("/v1/models")  # accessible set, already filtered for this caller
        aliases = self._get("/api/aliases")  # display name, capabilities, cost
        return join_aliases(accessible_ids(models), records_of(aliases))

    def _get(self, path: str) -> Any:
        import httpx  # local import so tests never need it on the path they don't take

        try:
            r = httpx.get(
                self._root + path,
                headers={"Authorization": f"Bearer {self._token_provider()}"},
                timeout=self._timeout_s,
            )
        except Exception as e:
            raise ResourceUnavailable(
                f"The LLM Gateway didn't answer at {path} ({type(e).__name__}). "
                "Resources will be listed once it responds."
            ) from e
        if r.status_code >= 400:
            raise ResourceUnavailable(f"The LLM Gateway answered {r.status_code} at {path}.")
        # An unauthenticated call to the gateway returns 200 carrying a Keycloak LOGIN PAGE, so the
        # status is not proof of an answer (verified — DOMINO-PRIMITIVES.md). Inspect the body.
        try:
            return r.json()
        except ValueError as e:
            raise ResourceUnavailable(
                f"The LLM Gateway returned a non-JSON body at {path}. That is what a signed-out "
                "session looks like, so this builder's gateway token may have expired."
            ) from e


# Mirrors what Sage's own gateway actually returns (probed 2026-08-19), the Domino-hosted sovereign
# alias included, so the rail can be exercised locally with no gateway. Kept faithful rather than
# tidy: every real record carries `streaming` and `responses` alongside the capabilities that
# actually tell aliases apart, and several report the gateway's fallback {1.0, 2.0} rate.
_FAKE_ALIASES = (
    LlmAlias("f-gpt54", "gpt-5.4", "gpt-5.4", "gpt-5.4",
             ["chat", "tools", "responses", "streaming", "vision"], {"input": 2.5, "output": 15.0}),
    LlmAlias("f-sonnet", "sonnet", "Claude Sonnet 4.6", None,
             ["chat", "responses", "tools", "streaming"], {"input": 3.0, "output": 15.0}),
    LlmAlias("f-opus", "opus", "Claude Opus 4.6", None,
             ["chat", "streaming", "responses", "tools"], {"input": 5.0, "output": 25.0}),
    LlmAlias("f-qwen3c", "bedrock-qwen3-coder", "bedrock-qwen3-coder", None,
             ["chat", "streaming", "tools", "responses"], {"input": 1.0, "output": 2.0}),
    LlmAlias("f-qwen25", "qwen-2-5", "Qwen 2.5 (Domino-hosted)",
             "Runs inside Domino, so calls never leave the platform.",
             ["chat", "tools"], {"input": 1.0, "output": 2.0}),
    LlmAlias("f-embed", "text-embedding-3-small", "Text Embedding 3 Small",
             "Turns text into vectors. Not a chat model.", ["embeddings"], {}),
)


@dataclass
class FakeResourceProvider:
    """In-memory Resources for local testing/demo (no gateway)."""

    aliases: list[LlmAlias] = field(default_factory=lambda: list(_FAKE_ALIASES))

    def list_llm_aliases(self) -> list[LlmAlias]:
        return list(self.aliases)
