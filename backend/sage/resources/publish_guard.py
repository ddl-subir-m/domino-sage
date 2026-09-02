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
Domino, and the builder (the only remaining publish route) owns its own I/O and its own failure
handling.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator import brand
from .bindings import KIND_DATA_SOURCE, Binding
from .provider import DataSource

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
# Not a sibling of the two above but of the whole guard: this one is about the App the Built App
# publishes TO, and it refuses an app that reads no store just as readily (#80). It is here because
# this module is where the vocabulary of a refused publish lives, and because the answer a caller
# gets is the same shape — a `PublishProblem` about the app itself, with no Binding to point at.
#
# `UNCHECKED_APP` is deliberately NOT this. "Sage couldn't read this App" and "this App isn't there"
# were the same sentence before #80, and they lead opposite ways: the first is waited out, the
# second is only ever fixed by publishing a new App.
MISSING_APP = "missing-app"


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
        out.append(PublishProblem(UNCHECKED_APP, brand.text(
            "{assistantName} couldn't reach {platformName} to check who this app is shared with, "
            "and it won't publish an app that reads a store without knowing that. Try publishing "
            "again in a moment."
        )))
    elif open_visibility(visibility):
        out.append(PublishProblem(OPEN_APP, _open_message(bindings, visibility)))
    return out


def missing_app_problem(display_name: str) -> PublishProblem:
    """The refusal for a Built App whose Domino App has been deleted outside Sage (#80).

    Named for the Built App, not for the app id. The id is Domino's, was minted by the first
    publish and never shown to anybody, so "app-68f3…" is not a thing the person reading this can
    go and look at; the name in the rail is. The id stays out of the sentence for the same reason
    the rail row carries `published` rather than the id itself.

    The sentence has to earn the one irreversible bit in it: publishing a new App means a new URL,
    and whoever was given the old link is not getting it back either way, because the App that
    served it is gone.
    """
    return PublishProblem(MISSING_APP, brand.text(
        "The {platformName} App that {name} publishes to has been deleted, so there is no App to "
        "publish a new version of. Publish it as a new App to put it back — that gives it a new "
        "URL, and the old link stays dead whatever you do.",
        name=display_name,
    ))


def _credential_problem(b: Binding, sources: list[DataSource] | None) -> PublishProblem | None:
    """What is wrong with publishing on one Data Source Binding, or None when nothing is."""
    if sources is None:
        return PublishProblem(UNCHECKED_SOURCE, brand.text(
            "{assistantName} couldn't reach {platformName} to check whether the {dataSource} {name} "
            "uses a shared credential, and it won't publish an app that reads a store it couldn't "
            "check. Try publishing again in a moment.",
            name=b.display_name,
        ), b.kind, b.id)
    source = _match(b, sources)
    if source is None:
        # Since #23 this is the SECOND line rather than the first: session-open preflight reports a
        # Data Source that has gone, so a creator normally meets it before building rather than at
        # publish. The guard stays because preflight is a warning and this is a refusal, and because
        # a listing that failed at session open leaves nothing for the creator to have seen.
        return PublishProblem(UNLISTED_SOURCE, brand.text(
            "This app is recorded as reading the {dataSource} {name}, which isn't in the "
            "{dataSourcePlural} you have permission on, so {assistantName} can't tell whether its "
            "credential is shared. Check the {dataSource} in {platformName}, or remove it from "
            "this app's {resourcePlural}, and publish again.",
            name=b.display_name,
        ), b.kind, b.id)
    if source.credential_type != SHARED:
        return PublishProblem(INDIVIDUAL_CREDENTIAL, brand.text(
            "This app is recorded as reading the {dataSource} {name}, whose credential "
            "belongs to one person rather than to a service account. A published app reaches the "
            "store as its publisher, so publishing this would hand every viewer that person's "
            "access. Remove it from this app's {resourcePlural}, or bind a {dataSource} whose "
            "credential is shared, and publish again.",
            name=b.display_name,
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
    return brand.text(
        "This app can be opened by people who are not signed in to {platformName} (its visibility "
        "is {visibility}), and it reads {sources}. A published app queries the store as its "
        "publisher, so anyone who reached the app would be reading it. Share the app with "
        "{platformName} users instead, on its settings page in {platformName}, then publish again.",
        visibility=visibility,
        sources=_names(bindings),
    )


def _names(bindings: list[Binding]) -> str:
    """The bound Data Sources as one readable phrase, so a refusal names what it is about."""
    names = [b.display_name for b in bindings]
    if len(names) == 1:
        return brand.text("the {dataSource} {name}", name=names[0])
    return brand.text("the {dataSourcePlural} {names}",
                      names=f"{', '.join(names[:-1])} and {names[-1]}")
