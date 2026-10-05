"""Verified route capabilities, bound to the gateway's current Alias identity."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from .protocol import Protocol


class RouteStatus(str, Enum):
    """Which of the four answers a capability is. `efforts == ()` is true of all four (#509, #647)."""
    VERIFIED = "verified"              # a proof matched this Alias identity; may still offer nothing
    UNMEASURED = "unmeasured"          # a gateway Alias with no matching proof; measuring repairs it
    MEASURING = "measuring"            # UNMEASURED, and this builder is measuring it now (#646)
    FALLBACK_CHAIN = "fallback_chain"  # refused before evidence is read; measuring cannot repair it
    NO_ROUTE = "no_route"              # not resolved against a gateway Alias: dev table, or unlisted


@dataclass(frozen=True)
class RouteCapability:
    protocol: Protocol = Protocol.CHAT
    native: bool = False
    efforts: tuple[str, ...] = ()
    efforts_with_tools: tuple[str, ...] = ()
    reason: str = "Reasoning settings have not been verified for this model's current route."
    identity: tuple[str, ...] = ()
    # Last, so `resolve`'s positional call is unaffected.
    status: RouteStatus = RouteStatus.NO_ROUTE

    @property
    def verified(self) -> bool:
        return self.status is RouteStatus.VERIFIED

    @property
    def unverified_route(self) -> bool:
        """A gateway Alias whose levels are not known — the case worth a warning and a note."""
        return self.status in (RouteStatus.UNMEASURED, RouteStatus.MEASURING,
                               RouteStatus.FALLBACK_CHAIN)

    def settings(self, effort: str | None, *, tools: bool) -> dict:
        if effort is None:
            return {}
        choices = self.efforts_with_tools if tools else self.efforts
        if effort not in choices:
            raise ValueError(f"Reasoning setting {effort!r} is unavailable. {self.reason}")
        if self.protocol is Protocol.MESSAGES:
            # `display` defaults to omitted on sonnet: its thinking streams empty, so the progress
            # line has nothing to narrate (#599, measured 2026-09-28).
            return ({"thinking": {"type": "disabled"}} if effort == "none" else
                    {"thinking": {"type": "adaptive", "display": "summarized"},
                     "output_config": {"effort": effort}})
        if self.protocol is Protocol.RESPONSES:
            return {"reasoning": {"effort": effort}}
        return {"reasoning_effort": effort}


_IDENTITY_FIELDS = ("id", "name", "provider_id", "provider_type", "provider_model", "updated_at")


def route_identity(root: str, row: dict) -> tuple[str, ...]:
    return (root.rstrip("/").removesuffix("/v1"),
            *(str(row.get(k) or "") for k in _IDENTITY_FIELDS))


def resolve(root: str, row: dict, evidence: list[dict]) -> RouteCapability:
    identity = route_identity(root, row)
    if row.get("fallback_chain"):
        return RouteCapability(reason="Reasoning settings are unavailable because this model has an unverified fallback route.",
                               identity=identity, status=RouteStatus.FALLBACK_CHAIN)
    for proof in evidence:
        if identity != route_identity(proof["gateway"], proof):
            continue
        return RouteCapability(Protocol(proof["protocol"]), proof.get("native", False),
                               tuple(proof["efforts"]), tuple(proof["efforts_with_tools"]),
                               proof.get("reason", ""), identity, status=RouteStatus.VERIFIED)
    # No proof for THIS identity. The protocol stays CHAT and the levels stay empty, which is the
    # conservative answer and is not the defect — the defect was that nobody could tell this apart
    # from a verified model that offers nothing, so a repointed alias went back to refusing every
    # tool-carrying turn in silence (#509). `status` is what the log and the person's error read.
    #
    # The repair belongs HERE rather than in the readers, because it is right only for this branch:
    # the fallback branch above can never be satisfied by measuring — `resolve` returns it without
    # consulting evidence at all — and a reader appending one repair line to every unverified
    # capability would tell somebody to run a command that cannot help them.
    name = str(row.get("name") or "").strip()
    return RouteCapability(
        reason=("No measurement matches this model's route on this deployment, so no reasoning "
                "setting can be offered for it and the turn takes the default wire."
                + (f" Repair: scripts/reasoning-evidence.py '{name}' --write" if name else "")),
        identity=identity, status=RouteStatus.UNMEASURED)


def identity_row(identity: tuple[str, ...]) -> dict:
    """The evidence row `route_identity` was read from, rebuilt from its answer, to measure into."""
    gateway, *fields = identity
    return {"gateway": gateway, **dict(zip(_IDENTITY_FIELDS, fields, strict=True)),
            "fallback_chain": []}


def legacy(model: str) -> RouteCapability:
    """The existing fake/development contract; never used for gateway discovery."""
    from ..router.models import reasoning_efforts_for, reasoning_efforts_with_tools
    return RouteCapability(efforts=reasoning_efforts_for(model),
                           efforts_with_tools=reasoning_efforts_with_tools(model), reason="")


@lru_cache(maxsize=1)
def evidence() -> list[dict]:
    return json.loads(Path(__file__).with_name("reasoning-evidence.json").read_text())
