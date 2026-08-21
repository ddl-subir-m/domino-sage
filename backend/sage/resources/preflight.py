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
from .bindings import KIND_DATA_SOURCE, KIND_MODEL_API, Binding
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


def stale_bindings(bindings: list[Binding], listings: dict[str, list | None]) -> list[Binding]:
    """The recorded Bindings whose Resource has gone, in manifest order.

    `listings` maps a kind to the listing authoritative for it, or to None when that listing could not
    be fetched. Those two are deliberately different answers, and both differ from a kind that is
    absent entirely: only a listing that ARRIVED can prove that something missing from it is gone.
    That is the rule this always had — not being able to check something is not evidence that it is
    gone — now applied per kind rather than to everything that was not an Alias.

    One listing failing does not suppress the others. A gateway that is down says nothing about
    whether a Data Source still exists, and withholding that answer because a different call failed
    would lose the one thing Sage did learn.

    Matched on id OR name, because the manifest keeps both and they are authoritative for different
    things: the control plane keys on id, a model call keys on name, and `get_datasource` takes the
    name. A Binding written before a Resource was re-registered under a new id still names the same
    thing if the name resolves, and calling that stale would send a creator to remove something that
    works. `publish_guard._match` makes the same match for the same reason.
    """
    out: list[Binding] = []
    for b in bindings:
        rows = listings.get(b.kind)
        if rows is None:        # unchecked, or unlistable — either way, nothing was learned
            continue
        if not any(getattr(r, "id", None) == b.id or getattr(r, "name", None) == b.name
                   for r in rows):
            out.append(b)
    return out


def missing_credentials(bindings: list[Binding], held: set[str] | None) -> list[Binding]:
    """Model API Bindings this app can no longer call, because Sage holds no access token for them.

    A failure the other two kinds have no equivalent for. An LLM Alias is called with the viewer's own
    Domino session and a Data Source through the container's sidecar, but a Model API opens for
    nothing except a token someone pasted (#9) — so a token that has gone leaves a Binding reading as
    a working dependency and an app whose calls fail.

    `held` is None when the token store was not read, which is left alone for the reason a listing
    that did not arrive is.
    """
    if held is None:
        return []
    return [b for b in bindings if b.kind == KIND_MODEL_API and b.id not in held]


def stale_message(b: Binding) -> str:
    """What a creator reads about one stale Binding. Names the label the row showed when they made
    the choice, since that is the only version of the Resource they ever saw.

    One sentence per kind, because the three go missing for different reasons and lead to different
    places: an Alias is de-registered on the gateway, a Model API is undeployed from this project, and
    a Data Source is a grant the creator no longer holds. One message for all three would send two
    thirds of the people who read it to the wrong screen.
    """
    if b.kind == KIND_MODEL_API:
        return (
            f"This app is recorded as using the Model API {b.display_name}, which is no longer "
            f"deployed in this project. Its prediction calls will fail. Remove it, or pick a "
            f"different Model API, before you build on it."
        )
    if b.kind == KIND_DATA_SOURCE:
        # Says publishing too, because that refusal (#12) is the one a creator would otherwise meet
        # first, at the point where the app is already built.
        return (
            f"This app is recorded as reading the Data Source {b.display_name}, which is no longer "
            f"among the Data Sources you have permission on. Sage will not publish an app that reads "
            f"a store it cannot check. Remove it, or pick a different Data Source, before you build "
            f"on it."
        )
    return (
        f"This app is recorded as using the LLM Alias {b.display_name}, which the LLM Gateway no "
        f"longer offers. Remove it, or pick a different Alias, before you build on it."
    )


def credential_message(b: Binding) -> str:
    """What a creator reads about a Model API Binding whose access token has gone."""
    return (
        f"This app is recorded as using the Model API {b.display_name}, but Sage no longer holds an "
        f"access token for it, so the app cannot call it. Use it again in the Resources panel to "
        f"paste a new token, or remove it."
    )
