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

from ..orchestrator import brand
from ..router.models import ModelCatalog
from .bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, KIND_MODEL_API, Binding
from .provider import HostedEndpoint, LlmAlias

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
        return brand.text(
            "{assistantName}'s {slot} model is set to the {llmAlias} {alias}, which this LLM "
            "Gateway does not offer. Turns that route to {slot} will fail. Pick a different model "
            "for that slot, or register {alias} in the LLM Gateway.",
            slot=self.slot,
            alias=self.alias,
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


# What each endpoint status means for a build about to start. Keyed on Domino's own words
# (`ModelEndpointStatusV1`), grouped by the remedy rather than by the word, because the remedies are
# what differ: an endpoint someone stopped gets started again, a broken one gets replaced, and one
# mid-transition just needs a minute. `Running` is absent because it is the case with nothing to say.
#
# `Unknown` is absent DELIBERATELY, and so is every status not listed here. Domino ships `Unknown` as
# its own word for "we do not know", and a status we do not recognise is the same answer from a
# newer platform. Both must stay silent: reporting either as a problem would turn "we could not
# check" into "this is broken", which is the one thing #21's fourth criterion forbids.
_NOT_SERVING: dict[str, str] = {
    "Stopped": "stopped",
    "Failed": "broken",
    "BuildFailed": "broken",
    "Building": "changing",
    "Starting": "changing",
    "Stopping": "changing",
}


def endpoint_status(alias_name: str, aliases: list[LlmAlias],
                    endpoints: list[HostedEndpoint] | None) -> tuple[str, str] | None:
    """The endpoint behind this Alias and its status, or None when there is nothing to report.

    None covers four different silences that must not be told apart by the caller, because all four
    mean the same thing to a creator — no sentence:

      - the Alias is not offered at all (a missing Alias is `unresolved_slots`' sentence, not this
        one, and saying both would put two warnings on one slot)
      - the Alias has no `endpoint_url`, so it is a vendor model with nothing on Domino behind it.
        This is the COMMON case, not an edge one: 12 of 14 aliases on cloud-dogfood (2026-08-21)
      - the endpoints listing did not arrive, so nothing was learned. Same rule `stale_bindings`
        applies to a listing that is None, for the same reason
      - the endpoint is Running, has no status at all, or reports one we do not recognise

    Joined on the endpoint's `url` after dropping the alias url's trailing `/v1` — measured live, and
    it is `url` rather than `id` or `vanityUrl` (DOMINO-PRIMITIVES.md). Both sides are stripped of a
    trailing slash so a gateway that stores one does not silently miss every join.
    """
    if endpoints is None:
        return None
    alias = next((a for a in aliases if a.name == alias_name), None)
    if alias is None or not alias.endpoint_url:
        return None
    target = alias.endpoint_url.rstrip("/").removesuffix("/v1").rstrip("/")
    endpoint = next((e for e in endpoints if e.url.rstrip("/") == target), None)
    if endpoint is None or not endpoint.status:
        return None
    if endpoint.status not in _NOT_SERVING:
        return None
    return endpoint.name or alias_name, endpoint.status


def endpoint_remedy(status: str, alternative: str) -> str:
    """The half of the sentence that says what to do, which is the half that differs.

    #21's second criterion turns on this: "start the endpoint" and "pick a different Alias" are
    opposite instructions, and one message covering both would send half the readers the wrong way.

    `alternative` is the fallback in the reader's own vocabulary. A slot is changed on the model
    panel and a Binding is changed in the Resources rail, so the same status has to end in a
    different noun depending on which screen the reader is being sent to.
    """
    kind = _NOT_SERVING[status]
    if kind == "stopped":
        return f"Start that endpoint, or {alternative}"
    if kind == "broken":
        return f"That endpoint needs its owner to fix it, so {alternative}"
    return f"It should come back on its own, so wait for it or {alternative}"


@dataclass(frozen=True)
class EndpointProblem:
    """A model slot whose Alias resolves, but whose endpoint is not serving (#21).

    Deliberately the same shape as `SlotProblem`, and reported in the same list: to a creator this is
    the same event — the model behind this slot will not answer — and #21's first criterion asks for
    it to be "reported the same way a missing Alias is". The UI keys its warnings on `slot`, and the
    two can never collide on one slot: an Alias that is missing has no record to carry an
    `endpoint_url`, so `unresolved_slots` and this check are mutually exclusive by construction.
    """

    slot: str
    alias: str
    endpoint: str
    status: str  # Domino's own word, so the log says which of the six it was

    @property
    def message(self) -> str:
        return brand.text(
            "{assistantName}'s {slot} model is set to the {llmAlias} {alias}, whose Hosted GenAI "
            "Endpoint {endpoint} is {status}. Turns that route to {slot} will fail. {remedy}.",
            slot=self.slot,
            alias=self.alias,
            endpoint=self.endpoint,
            status=self.status,
            remedy=endpoint_remedy(self.status, "pick a different model for that slot"),
        )

    def to_dict(self) -> dict:
        return {"slot": self.slot, "alias": self.alias, "endpoint": self.endpoint,
                "status": self.status, "message": self.message}


def alias_problem(alias_name: str, aliases: list[LlmAlias],
                  endpoints: list[HostedEndpoint] | None) -> str | None:
    """Why picking this Alias would not work, or None when there is nothing to say.

    The same join `slots_on_dead_endpoints` makes, asked one Alias at a time and phrased for someone
    who is choosing rather than someone who already chose: a slot is not named, because the reader is
    looking at a menu row and has not picked a slot for it yet.

    Prevention rather than a good error message. `/v1/models` filters on permission alone, so a
    granted Alias whose Hosted GenAI Endpoint is stopped is offered anyway (#21) — and assigning one
    is exactly how a build comes to fail opaquely mid-turn.
    """
    found = endpoint_status(alias_name, aliases, endpoints)
    if found is None:
        return None
    endpoint, status = found
    return brand.text(
        "Its Hosted GenAI Endpoint {endpoint} is {status}, so turns using it will fail. {remedy}.",
        endpoint=endpoint,
        status=status,
        remedy=endpoint_remedy(status, "pick a different model"),
    )


def slots_on_dead_endpoints(catalog: ModelCatalog, aliases: list[LlmAlias],
                            endpoints: list[HostedEndpoint] | None) -> list[EndpointProblem]:
    """The configured slots whose Alias resolves but whose endpoint will not answer, in SLOTS order.

    Runs over the same slots as `unresolved_slots` and answers the question that one cannot: an Alias
    pointing at a stopped endpoint is still offered by `/v1/models`, because that listing filters on
    permission alone (verified live 2026-08-21). So the slot resolves, preflight passes, the turn
    routes, and the build fails partway through on a gateway error — which is the failure this moves
    earlier.
    """
    problems: list[EndpointProblem] = []
    for slot in SLOTS:
        alias = (getattr(catalog, slot, "") or "").rsplit("/", 1)[-1]
        if not alias:
            continue
        found = endpoint_status(alias, aliases, endpoints)
        if found:
            problems.append(EndpointProblem(slot, alias, found[0], found[1]))
    return problems


def bindings_on_dead_endpoints(bindings: list[Binding], aliases: list[LlmAlias],
                               endpoints: list[HostedEndpoint] | None) -> list[tuple[Binding, str, str]]:
    """The LLM Alias Bindings whose endpoint will not answer: the Binding, the sentence, the status.

    Returned as triples because neither of the other two facts can be derived from the Binding —
    unlike `stale_message`, which can. The sentence needs the endpoint's name, and the **status has
    to travel separately** even though the sentence already contains it: the rail badges the row with
    a short chip, and a chip that said "Gone" here would send the creator to remove an Alias that is
    registered, granted and offered, whose endpoint is merely stopped. That is the exact confusion
    this issue exists to prevent, so the chip gets Domino's own word rather than a guess made by
    pattern-matching the prose.
    """
    out: list[tuple[Binding, str, str]] = []
    for b in bindings:
        if b.kind != KIND_LLM_ALIAS:
            continue
        found = endpoint_status(b.name, aliases, endpoints)
        if not found:
            continue
        endpoint, status = found
        out.append((b, brand.text(
            "This app is recorded using the {llmAlias} {name}, whose Hosted GenAI Endpoint "
            "{endpoint} is {status}. Its calls will fail. {remedy}, before you build on it.",
            name=b.display_name,
            endpoint=endpoint,
            status=status,
            remedy=endpoint_remedy(status, "pick a different Alias"),
        ), status))
    return out


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
        return brand.text(
            "This app is recorded as using the {modelApi} {name}, which is no longer deployed in "
            "this project. Its prediction calls will fail. Remove it, or pick a different "
            "{modelApi}, before you build on it.",
            name=b.display_name,
        )
    if b.kind == KIND_DATA_SOURCE:
        # Says publishing too, because that refusal (#12) is the one a creator would otherwise meet
        # first, at the point where the app is already built.
        return brand.text(
            "This app is recorded as reading the {dataSource} {name}, which is no longer among the "
            "{dataSourcePlural} you have permission on. {assistantName} will not publish an app "
            "that reads a store it cannot check. Remove it, or pick a different {dataSource}, "
            "before you build on it.",
            name=b.display_name,
        )
    return brand.text(
        "This app is recorded as using the {llmAlias} {name}, which the LLM Gateway no longer "
        "offers. Remove it, or pick a different Alias, before you build on it.",
        name=b.display_name,
    )


def credential_message(b: Binding) -> str:
    """What a creator reads about a Model API Binding whose access token has gone."""
    return brand.text(
        "This app is recorded as using the {modelApi} {name}, but {assistantName} no longer holds "
        "an access token for it, so the app cannot call it. Use it again in the Resources panel to "
        "paste a new token, or remove it.",
        name=b.display_name,
    )
