"""Verified route capabilities, bound to the gateway's current Alias identity."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .protocol import Protocol


@dataclass(frozen=True)
class RouteCapability:
    protocol: Protocol = Protocol.CHAT
    native: bool = False
    efforts: tuple[str, ...] = ()
    efforts_with_tools: tuple[str, ...] = ()
    reason: str = "Reasoning settings have not been verified for this model's current route."
    identity: tuple[str, ...] = ()
    # Did a proof actually match, or is this the fallback? No other field answers that (#509).
    # A MEASURED row can be empty — one deployment's `haiku` is verified to offer no levels at
    # all — so `not efforts` reads True for "offers nothing" and for "nothing is known" alike,
    # and those two need opposite responses. Last, so `resolve`'s positional call is unaffected.
    verified: bool = False

    def settings(self, effort: str | None, *, tools: bool) -> dict:
        if effort is None:
            return {}
        choices = self.efforts_with_tools if tools else self.efforts
        if effort not in choices:
            raise ValueError(f"Reasoning setting {effort!r} is unavailable. {self.reason}")
        if self.protocol is Protocol.MESSAGES:
            return ({"thinking": {"type": "disabled"}} if effort == "none" else
                    {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}})
        if self.protocol is Protocol.RESPONSES:
            return {"reasoning": {"effort": effort}}
        return {"reasoning_effort": effort}


def route_identity(root: str, row: dict) -> tuple[str, ...]:
    return (root.rstrip("/").removesuffix("/v1"), *(str(row.get(k) or "") for k in
            ("id", "name", "provider_id", "provider_type", "provider_model", "updated_at")))


def resolve(root: str, row: dict, evidence: list[dict]) -> RouteCapability:
    identity = route_identity(root, row)
    if row.get("fallback_chain"):
        return RouteCapability(reason="Reasoning settings are unavailable because this model has an unverified fallback route.",
                               identity=identity)
    for proof in evidence:
        if identity != route_identity(proof["gateway"], proof):
            continue
        return RouteCapability(Protocol(proof["protocol"]), proof.get("native", False),
                               tuple(proof["efforts"]), tuple(proof["efforts_with_tools"]),
                               proof.get("reason", ""), identity, verified=True)
    # No proof for THIS identity. The protocol stays CHAT and the levels stay empty, which is the
    # conservative answer and is not the defect — the defect was that nobody could tell this apart
    # from a verified model that offers nothing, so a repointed alias went back to refusing every
    # tool-carrying turn in silence (#509). `verified` is what the log and the person's error read.
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
        identity=identity)


def legacy(model: str) -> RouteCapability:
    """The existing fake/development contract; never used for gateway discovery."""
    from ..router.models import reasoning_efforts_for, reasoning_efforts_with_tools
    return RouteCapability(efforts=reasoning_efforts_for(model),
                           efforts_with_tools=reasoning_efforts_with_tools(model), reason="")


@lru_cache(maxsize=1)
def evidence() -> list[dict]:
    return json.loads(Path(__file__).with_name("reasoning-evidence.json").read_text())
