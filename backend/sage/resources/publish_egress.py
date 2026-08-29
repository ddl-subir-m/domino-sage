"""What leaves Domino when this app calls a model, said before it is published (#35).

ADR-0012 decided that no Data Source + LLM Alias combination is refused: a Domino administrator
registered that Alias and configured that store, the rows already went to that model during the
build under the creator's own eyes, and 12 of the 14 Aliases on cloud-dogfood are vendor-backed, so
a refusal would refuse the mainline. What publishing changes is the VOLUME and the ATTENDEDNESS —
both real, and both worth a sentence rather than a veto.

So this module holds the whole of the protection: one sentence a person reads on the way out. It is
not a guard, it produces no `PublishProblem`, and nothing here can stop a publish. `publish_guard`
next door refuses; this one only ever tells.

Two things it deliberately cannot say:

- **which vendor.** An Alias record carries no vendor field. The only signal is the absence of an
  `endpoint_url`, which means nothing Domino-hosted is behind it. "Not hosted on Domino" is the
  honest ceiling, and naming a vendor from a display name would be a guess printed as a fact.
- **an Alias the app calls but never declared.** This reads the manifest, so #94's undeclared call
  is invisible to it and makes the sentence understate. ADR-0012 records that; it is not closed
  here.

Pure functions, like `publish_guard` and `preflight`: the judgement takes an already-fetched Alias
listing, so the sentence a creator reads is testable without a gateway, and the caller owns its own
I/O and its own failure handling.
"""
from __future__ import annotations

from ..orchestrator import brand
from .bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, Binding
from .provider import LlmAlias


def needs_listing(bindings: list[Binding]) -> bool:
    """Whether these Bindings could earn the sentence at all — the LOCAL half of the join.

    One rule, one place, and two readers: `egress_notice` starts here, and the caller asks it BEFORE
    fetching anything, so an app holding one kind of Binding or neither makes no request. That is
    most apps, and the whole reason this read costs the common publish nothing.
    """
    kinds = {b.kind for b in bindings}
    return KIND_DATA_SOURCE in kinds and KIND_LLM_ALIAS in kinds


def egress_notice(bindings: list[Binding], aliases: list[LlmAlias] | None) -> str | None:
    """The sentence to show before publishing these Bindings, or None when there is none.

    `aliases` is the Alias listing the caller fetched, or `None` when it could not be fetched.
    `None` says NOTHING — the deliberate opposite of `publish_problems`, which refuses when it
    cannot check. An unverified credential is a hole; an unwritten notice is not, and "Sage could
    not check where your data goes" is a sentence that costs a creator attention and buys them
    nothing they can act on.

    Three silences, and they are all the same silence to the reader:

    - no Data Source Binding. A vendor-backed Alias with no store bound is not this decision's
      subject, and firing on every vendor Alias would fire on nearly every app and be tuned out
      inside a week.
    - every bound Alias is Domino-hosted. The call stays inside the platform, so nothing left it.
    - a bound Alias is not in the listing at all. There is no record left to carry an
      `endpoint_url`, so its hosting is unknown rather than vendor — `endpoint_status` draws the
      same line, and `stale_bindings` is what reports a Binding that has gone.
    """
    if aliases is None or not needs_listing(bindings):
        return None
    offsite = [b for b in bindings if b.kind == KIND_LLM_ALIAS and _is_offsite(b, aliases)]
    if not offsite:
        return None
    sources = [b for b in bindings if b.kind == KIND_DATA_SOURCE]
    several = len(offsite) > 1
    # Each phrase is resolved before it travels as a value: a value is not scanned again, so the
    # noun has to be its own literal rather than a token inside `_phrase`'s answer.
    stores = _phrase(sources, brand.text("the {dataSource}"), brand.text("the {dataSourcePlural}"))
    models = _phrase(offsite, brand.text("the {llmAlias}"), brand.text("the {llmAliasPlural}"))
    return brand.text(
        "This app reads {stores} and calls {models}, which {run} outside {platformName}. Anything "
        "the app sends {them} leaves {platformName} — once this is published, for every viewer, "
        "and with nobody watching. This doesn't stop the publish.",
        stores=stores,
        models=models,
        run="run" if several else "runs",
        them="those models" if several else "that model",
    )


def _is_offsite(b: Binding, aliases: list[LlmAlias]) -> bool:
    """Whether this Alias Binding names a model with nothing Domino-hosted behind it.

    Matched by id OR by name, for the reason `publish_guard._match` matches both: the manifest keeps
    each because they are authoritative for different things, and an Alias re-registered under a new
    id is still the same Alias. Matching on id alone would go quiet about a model really being
    called. An Alias in neither is not offsite but UNKNOWN — see `egress_notice`'s third silence.
    """
    alias = (next((a for a in aliases if a.id == b.id), None)
             or next((a for a in aliases if a.name == b.name), None))
    return alias is not None and not alias.endpoint_url


def _phrase(bindings: list[Binding], one: str, many: str) -> str:
    """The bound Resources as one readable phrase, so the notice names what it is about.

    `publish_guard._names`'s shape, parameterised because this sentence carries two of these lists
    where a refusal carries one.
    """
    names = [b.display_name for b in bindings]
    if len(names) == 1:
        return f"{one} {names[0]}"
    return f"{many} {', '.join(names[:-1])} and {names[-1]}"
