"""Preflight — answer what a build will need before the build needs it (#17).

Two questions, and today they fail identically: an opaque error partway through a build, after the
user has already spent a turn on it. Does the LLM Alias filling each of Sage's own model slots
exist on this gateway, and does the Resource this app is recorded as using still exist?

Both are answered from ONE listing — the accessible-and-registered intersection the Resource
provider already computes — so each check costs one gateway call, not one per slot or per Binding.
That listing is the right authority for both: `/v1/models` is what a model call is actually
resolved against, so an Alias missing from it is an Alias that will 404 mid-turn.

Pure functions on purpose. Everything here takes an already-fetched alias list, so the decisions
are testable without a gateway and the two callers (startup, session open) each own their own I/O
and their own failure handling.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..router.models import ModelCatalog
from .bindings import KIND_LLM_ALIAS, Binding
from .provider import LlmAlias

# Every slot in ModelCatalog, in the order the model panel lists them. Written out rather than
# derived from the dataclass fields so a future non-model field on ModelCatalog cannot silently
# become something preflight tries to resolve as an Alias.
SLOTS: tuple[str, ...] = (
    "sovereign_plan", "sovereign_implement", "sovereign_ask", "plan", "implement", "ask",
)


@dataclass(frozen=True)
class SlotProblem:
    """A configured model slot naming an LLM Alias this gateway will not serve."""

    slot: str
    alias: str

    @property
    def message(self) -> str:
        """What the maintainer reads, in the log and in the builder. Names both halves — the slot
        alone does not say what to change it from, and the alias alone does not say what broke."""
        return (
            f"Sage's {self.slot} model is set to the LLM Alias {self.alias}, which this LLM Gateway "
            f"does not offer. Turns that route to {self.slot} will fail. Pick a different model for "
            f"that slot, or register {self.alias} in the LLM Gateway."
        )

    def to_dict(self) -> dict:
        return {"slot": self.slot, "alias": self.alias, "message": self.message}


def unresolved_slots(catalog: ModelCatalog, aliases: list[LlmAlias]) -> list[SlotProblem]:
    """The configured slots that name an Alias the gateway does not offer, in SLOTS order.

    Matched on `name`, not `id`: the name is what a request's `model` field carries, so it is the
    identity a slot has to resolve to. A provider-prefixed slot (`domino/sonnet`) is reduced to its
    bare id first, the same reduction the router already makes in `supports_vision`.

    A blank slot is not reported. `_build_catalog` promotes an empty environment variable back to
    its default before a catalog is ever built, so a blank here means a caller constructed one
    directly, and "the Alias '' is missing" is a worse sentence than the silence.
    """
    offered = {a.name for a in aliases}
    problems: list[SlotProblem] = []
    for slot in SLOTS:
        alias = (getattr(catalog, slot, "") or "").rsplit("/", 1)[-1]
        if alias and alias not in offered:
            problems.append(SlotProblem(slot, alias))
    return problems


def stale_bindings(bindings: list[Binding], aliases: list[LlmAlias]) -> list[Binding]:
    """The recorded Bindings whose Resource has gone, in manifest order.

    Matched on id OR name, because the manifest keeps both and they are authoritative for different
    things: the control plane keys on id, a model call keys on name. A Binding written before an
    Alias was re-registered under a new id still names the same Resource if the name resolves, and
    calling that stale would send a creator to remove something that works.

    Only kinds this Sage can check are judged. A Binding of an unknown kind — a newer Sage's record,
    or a Resource kind whose listing is not in this call — is left alone: not being able to check
    something is not evidence that it is gone.
    """
    known = {a.id for a in aliases} | {a.name for a in aliases}
    return [
        b for b in bindings
        if b.kind == KIND_LLM_ALIAS and b.id not in known and b.name not in known
    ]


def stale_message(b: Binding) -> str:
    """What a creator reads about one stale Binding. Names the label the row showed when they made
    the choice, since that is the only version of the Resource they ever saw."""
    return (
        f"This app is recorded as using the LLM Alias {b.display_name}, which the LLM Gateway no "
        f"longer offers. Remove it, or pick a different Alias, before you build on it."
    )
