"""Publish guards — the two refusals a Data Source Binding earns an app (#12).

ADR-0001 chose a live connection over a baked-in extract, and named the consequence: a published
app queries as its publisher, for whoever is looking at it, because per-viewer identity is an
admin-only setting Sage's users cannot reach. Two things are therefore safe while an app is being
built and stop being safe the moment it is shared:

- an `Individual` credential is one person's own access to the store, so an app published on it
  re-exports that access to every viewer;
- an app anyone can open puts whatever the app can query in front of whoever finds the URL.

Both are checked at publish and nowhere earlier. Building and previewing against an `Individual`
source stays allowed: the creator is querying with their own access, in their own session, which
is what a Data Source is for. Nothing has been re-exported until the app is published, and
refusing at pick time would take away a source the creator can legitimately use.

Pure functions on purpose, like `preflight`. The judgement takes an already-fetched Data Source
list and an already-read visibility string, so the sentence a creator reads is testable without a
Domino, and each caller — the builder and the hub, which publish by different routes — owns its
own I/O and its own failure handling.
"""
from __future__ import annotations

from dataclasses import dataclass

from .bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, Binding
from .provider import DataSource, LlmAlias

# The credential kind a published app may read a store with: one belonging to a service account,
# so every viewer reaches the store as the same principal the creator already saw named on the row.
SHARED = "Shared"

# Visibility values a Data Source app may be published at, normalised to upper snake case.
#
# An ALLOW list, not a guessed open one, and that is the whole design. Both entries that matter are
# verified live on cloud-dogfood 2026-08-20 (`sage.tools.app_visibility`): the detail response
# carries a top-level `visibility`, and Domino's sharing dropdown offers exactly two settings —
# "Restricted (project collaborators)" is `GRANT_BASED`, which is what Sage sets at create, and
# "Anyone in Domino" is `AUTHENTICATED`. `PRIVATE` is here because Domino spells the closed idea that
# way for projects and an app is cheap to be generous about in that direction.
#
# `AUTHENTICATED` is ALLOWED, which is the line ADR-0001's source research draws: "never publish a
# resource-querying app as PUBLIC — authenticated at minimum". Every viewer of such an app is a named
# Domino user who signed in, and since #13 what they can run is the set of named queries the creator
# declared rather than the warehouse. The thing that guard was written against is an anonymous app on
# the open internet, and that is what is still refused.
#
# Everything else refuses, including any anonymous or link-based setting a deployment offers that
# this list has not met. An unrecognised value is quoted verbatim in the refusal, so a deployment
# spelling one of the allowed settings differently costs one report and one entry, not a hole.
ALLOWED_VISIBILITY = frozenset({"GRANT_BASED", "PRIVATE", "AUTHENTICATED"})

# NOT guarded on: the separate top-level `discoverable` flag, which Domino describes as "All Domino
# users can find this App and request access to view", and which the live probe confirmed is an
# independent field. Finding an app and being able to read what it queries are different things — a
# request for access is still a request — so refusing on it would refuse an app nobody can read.

# Why a publish was refused. The message carries the whole explanation; this is for the caller that
# wants to count or group them, and for a test that wants to name a case without matching prose.
INDIVIDUAL_CREDENTIAL = "individual-credential"
UNLISTED_SOURCE = "unlisted-source"
UNCHECKED_SOURCE = "unchecked-source"
OPEN_APP = "open-visibility"
UNCHECKED_APP = "unchecked-visibility"
SENSITIVE_TO_VENDOR = "sensitive-rows-to-vendor-model"
UNCHECKED_ALIAS = "unchecked-alias"


@dataclass(frozen=True)
class PublishProblem:
    """One reason this app must not be published, and what to do about it."""

    reason: str
    message: str
    # The Binding the creator has to act on, so the UI can take them to the row rather than leave
    # them to find it. Empty for a problem about the app itself rather than about one Binding.
    kind: str = ""
    id: str = ""

    def to_dict(self) -> dict:
        return {"reason": self.reason, "message": self.message, "kind": self.kind, "id": self.id}


class PublishRefused(Exception):
    """Raised instead of publishing when a guard objects.

    Carries every problem rather than the first: a creator who removes the one Binding they were
    told about, publishes again, and is told about the next one has been made to discover their own
    app one refusal at a time.
    """

    def __init__(self, problems: list[PublishProblem]) -> None:
        super().__init__(" ".join(p.message for p in problems))
        self.problems = problems


def data_source_bindings(bindings: list[Binding]) -> list[Binding]:
    """The Bindings these guards have anything to say about, in manifest order.

    Callers check this first and skip the guard entirely when it is empty, which is what keeps an
    app that reads no store publishing exactly as it did before: no Data Source listing, no
    visibility read, and nothing new that can fail.
    """
    return [b for b in bindings if b.kind == KIND_DATA_SOURCE]


def open_visibility(raw: str) -> bool:
    """Whether a visibility value puts the app in front of people who never signed in.

    Anything not in `ALLOWED_VISIBILITY` counts, the empty string excepted: "" is what a caller
    passes when there is no published app to read a visibility FROM, and a first publish is one Sage
    sets `GRANT_BASED` on itself. `None` — the caller asked and could not get an answer — never
    reaches here; `publish_problems` refuses on it directly.

    On a deployment whose sharing offers only the two settings cloud-dogfood does, this is a guard
    for a setting that does not exist there yet, and it stops nothing. That is the honest outcome
    rather than a defect: the exposure it was written against is an app anyone on the internet can
    open, and the credential guard beside it is what carries the weight in the meantime.
    """
    return bool(raw.strip()) and raw.strip().upper().replace("-", "_").replace(" ", "_") not in ALLOWED_VISIBILITY


def publish_problems(
    bindings: list[Binding],
    sources: list[DataSource] | None,
    visibility: str | None,
) -> list[PublishProblem]:
    """Every reason not to publish an app holding these Data Source Bindings.

    `sources` is the Data Source listing the caller fetched, or `None` when it could not be
    fetched. `None` refuses, one problem per Binding: this function is only ever reached because
    the app reads a store, and "Sage could not check whether that store is shared" is not a reason
    to assume it is. The asymmetry with `open_visibility`, which fails open, is deliberate — the
    credential listing is the same public endpoint the Resource Browser already reads successfully,
    so a failure there is loud and transient, where an unverified visibility field would fail
    silently and permanently.

    `visibility` is the app's current sharing setting: "" when there is no published app to read one
    from, and `None` when the caller asked and could not get an answer. `None` refuses, for the
    reason `sources=None` does — an app that reads a store and whose sharing could not be read is
    not an app to assume anything about.
    """
    problems = [_credential_problem(b, sources) for b in bindings]
    out = [p for p in problems if p is not None]
    if not bindings:
        return out
    if visibility is None:
        out.append(PublishProblem(UNCHECKED_APP, (
            "Sage couldn't reach Domino to check who this app is shared with, and it won't publish "
            "an app that reads a store without knowing that. Try publishing again in a moment."
        )))
    elif open_visibility(visibility):
        out.append(PublishProblem(OPEN_APP, _open_message(bindings, visibility)))
    return out


def _credential_problem(b: Binding, sources: list[DataSource] | None) -> PublishProblem | None:
    """What is wrong with publishing on one Data Source Binding, or None when nothing is."""
    if sources is None:
        return PublishProblem(UNCHECKED_SOURCE, (
            f"Sage couldn't reach Domino to check whether the Data Source {b.display_name} uses a "
            f"shared credential, and it won't publish an app that reads a store it couldn't check. "
            f"Try publishing again in a moment."
        ), b.kind, b.id)
    source = _match(b, sources)
    if source is None:
        # Since #23 this is the SECOND line rather than the first: session-open preflight reports a
        # Data Source that has gone, so a creator normally meets it before building rather than at
        # publish. The guard stays because preflight is a warning and this is a refusal, and because
        # a listing that failed at session open leaves nothing for the creator to have seen.
        return PublishProblem(UNLISTED_SOURCE, (
            f"This app is recorded as reading the Data Source {b.display_name}, which isn't in the "
            f"Data Sources you have permission on, so Sage can't tell whether its credential is "
            f"shared. Check the Data Source in Domino, or remove it from this app's Resources, and "
            f"publish again."
        ), b.kind, b.id)
    if source.credential_type != SHARED:
        return PublishProblem(INDIVIDUAL_CREDENTIAL, (
            f"This app is recorded as reading the Data Source {b.display_name}, whose credential "
            f"belongs to one person rather than to a service account. A published app reaches the "
            f"store as its publisher, so publishing this would hand every viewer that person's "
            f"access. Remove it from this app's Resources, or bind a Data Source with a Shared "
            f"credential, and publish again."
        ), b.kind, b.id)
    return None


def _match(b: Binding, sources: list[DataSource]) -> DataSource | None:
    """The listed Data Source a Binding names, by id or by name.

    Both, for the reason `stale_bindings` matches both: the manifest keeps each because they are
    authoritative for different things, and a source re-registered under a new id is still the same
    source. Matching on id alone would refuse a publish for a Binding that works.
    """
    by_id = next((s for s in sources if s.id == b.id), None)
    return by_id or next((s for s in sources if s.name == b.name), None)


def _open_message(bindings: list[Binding], visibility: str) -> str:
    # The raw value is quoted for two readers. A creator sees which setting is in the way; whoever
    # gets the report of a wrongly-refused publish sees the spelling to add to ALLOWED_VISIBILITY,
    # which is the whole cost of failing closed on a value this list has not met.
    return (
        f"This app can be opened by people who are not signed in to Domino (its visibility is "
        f"{visibility}), and it reads {_names(bindings)}. A published app queries the store as its "
        f"publisher, so anyone who reached the app would be reading it. Share the app with Domino "
        f"users instead, on its settings page in Domino, then publish again."
    )


def _names(bindings: list[Binding]) -> str:
    """The bound Data Sources as one readable phrase, so a refusal names what it is about."""
    names = [b.display_name for b in bindings]
    if len(names) == 1:
        return f"the Data Source {names[0]}"
    return f"the Data Sources {', '.join(names[:-1])} and {names[-1]}"


# ---------------------------------------------------------------------------------------------
# Where the rows go (#35)
#
# A published app can read a store and call a language model in the same page load. The Alias it
# calls may be a Hosted GenAI Endpoint inside Domino, or a vendor model outside it — and until this,
# nothing looked. So `read the customer table, then summarise each row with @gpt-5.4` published
# quietly, and sent warehouse rows to a vendor for every viewer on every load.
#
# The judgement is the creator's own and Sage already asks for it. When they share sample rows they
# say whether the rows are sensitive, and that answer already locks SAGE's conversation to sovereign
# models (#16). It did nothing to the app Sage builds, which is the actual inconsistency: a creator
# who ticked that box would reasonably assume it covered both. So the same answer now governs both.
#
#   sensitive rows + a model outside Domino   -> refuse, here
#   rows nobody called sensitive + the same   -> say so, and let them publish (`vendor_model_warning`)
#
# Two things this deliberately does NOT do. It does not decide for itself that warehouse data is
# sensitive — `share_sample_rows` says why, and a rule that guessed would be Sage judging data it
# cannot see. And it does not fire for an app that binds an Alias and no store: a model call that
# reads nothing re-exports nothing, which is the same line #12 draws.


def vendor_model_problems(bindings: list[Binding], aliases: list[LlmAlias] | None) -> list[PublishProblem]:
    """Every reason not to publish an app that would send sensitive rows to a model outside Domino.

    Takes ALL the app's Bindings, not just the Data Source ones, because the question spans two
    kinds. Answers `[]` for the ordinary app, and costs an Alias listing only when there is a
    sensitive store to ask about — which is why the caller may pass `aliases=None` cheaply.

    `aliases` is the listing the caller fetched, or `None` when it could not be fetched. `None`
    refuses, for the reason a missing Data Source listing refuses: this function is only reached
    because the creator called a store's rows sensitive, and "Sage could not check where they would
    go" is not a reason to send them. The blast radius is narrow by construction — an app with no
    sensitive store never reaches the fetch, so a gateway wobble cannot block an ordinary publish.
    """
    sensitive = _sensitive_sources(bindings)
    alias_bindings = [b for b in bindings if b.kind == KIND_LLM_ALIAS]
    if not sensitive or not alias_bindings:
        return []
    if aliases is None:
        return [PublishProblem(UNCHECKED_ALIAS, (
            f"Sage couldn't reach the LLM Gateway to check whether the models this app calls run "
            f"inside Domino, and it won't publish an app that reads {_names(sensitive)} — whose rows "
            f"you marked sensitive — without knowing that. Try publishing again in a moment."
        ))]
    problems = [_vendor_problem(b, sensitive, aliases) for b in alias_bindings]
    return [p for p in problems if p is not None]


def vendor_model_warning(bindings: list[Binding], aliases: list[LlmAlias] | None) -> str | None:
    """The sentence for a creator whose app sends store rows to a vendor model, when nothing has
    said those rows are sensitive. `None` when there is nothing to say.

    Informs rather than refuses, and is therefore shaped like the broken-query check (#26, #27)
    rather than like a guard: it goes out on `publish_check`, and the creator publishes past it.
    Silent whenever `vendor_model_problems` speaks, so a creator never reads a warning and a refusal
    about the same two Resources.

    Fails open in every direction — no Alias listing, no sentence. A hint one route skips is a missed
    nudge; the refusal above is the thing that must not be skippable.
    """
    stores = [b for b in bindings if b.kind == KIND_DATA_SOURCE]
    if not stores or _sensitive_sources(bindings) or aliases is None:
        return None
    outside = [b for b in bindings if b.kind == KIND_LLM_ALIAS and _is_vendor(b, aliases)]
    if not outside:
        return None
    return (
        f"This app reads {_names(stores)} and sends what it reads to {_alias_names(outside)}, which "
        f"runs outside Domino. Every viewer's page load does it. If those rows shouldn't leave, share "
        f"sample rows from the store and mark them sensitive — Sage will then refuse this publish — "
        f"or bind an Alias whose model is hosted in Domino."
    )


def _sensitive_sources(bindings: list[Binding]) -> list[Binding]:
    """The bound Data Sources whose rows the creator called sensitive."""
    return [b for b in bindings if b.kind == KIND_DATA_SOURCE and b.sensitive]


def _vendor_problem(b: Binding, sensitive: list[Binding], aliases: list[LlmAlias]) -> PublishProblem | None:
    """What is wrong with publishing one Alias Binding beside sensitive rows, or None when nothing is."""
    alias = next((a for a in aliases if a.id == b.id), None) or next((a for a in aliases if a.name == b.name), None)
    if alias is None:
        # Same reading as UNLISTED_SOURCE: an Alias that is not in the listing is one Sage cannot say
        # anything about, and "cannot tell" is not "hosted in Domino".
        return PublishProblem(UNCHECKED_ALIAS, (
            f"This app is recorded as calling {b.display_name}, which isn't in the LLM Aliases you "
            f"have a grant for, so Sage can't tell whether it runs inside Domino. This app also reads "
            f"{_names(sensitive)}, whose rows you marked sensitive. Check the Alias in the LLM "
            f"Gateway, or remove it from this app's Resources, and publish again."
        ), b.kind, b.id)
    if alias.endpoint_url:
        return None
    return PublishProblem(SENSITIVE_TO_VENDOR, (
        f"This app reads {_names(sensitive)}, whose rows you marked sensitive, and calls "
        f"{b.display_name}, which runs outside Domino rather than on a Hosted GenAI Endpoint. "
        f"Publishing this would send those rows to that vendor for every viewer. Bind an Alias whose "
        f"model is hosted in Domino, or re-share the rows without marking them sensitive if they can "
        f"leave, and publish again."
    ), b.kind, b.id)


def _is_vendor(b: Binding, aliases: list[LlmAlias]) -> bool:
    """Whether this Alias Binding names a model with nothing on Domino behind it.

    `endpoint_url` is the discriminator `preflight.endpoint_status` already reads: an Alias without
    one is a vendor model. An Alias missing from the listing is NOT counted here — the warning path
    fails open, and the refusal path above is where an unanswerable Alias costs something.
    """
    alias = next((a for a in aliases if a.id == b.id), None) or next((a for a in aliases if a.name == b.name), None)
    return alias is not None and not alias.endpoint_url


def _alias_names(bindings: list[Binding]) -> str:
    names = [b.display_name for b in bindings]
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"
