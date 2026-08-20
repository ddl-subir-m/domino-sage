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

from .bindings import KIND_DATA_SOURCE, Binding
from .provider import DataSource

# The credential kind a published app may read a store with: one belonging to a service account,
# so every viewer reaches the store as the same principal the creator already saw named on the row.
SHARED = "Shared"

# Visibility values that mean "reachable without a Domino grant", normalised to upper snake case.
#
# UNVERIFIED against a live deployment. Sage only ever SETS `GRANT_BASED`, and `publish_app` is
# verified live doing so; what has never been seen is what `GET /api/apps/beta/apps/{id}` calls the
# field on the way back, or which names Domino's own sharing settings carry. Until it has been,
# `open_visibility` treats an unrecognised value as not-open — see the reasoning there.
OPEN_VISIBILITY = frozenset({
    "PUBLIC", "ANYONE", "ANYONE_CAN_ACCESS", "ANYONE_WITH_LINK", "ANONYMOUS",
})

# Why a publish was refused. The message carries the whole explanation; this is for the caller that
# wants to count or group them, and for a test that wants to name a case without matching prose.
INDIVIDUAL_CREDENTIAL = "individual-credential"
UNLISTED_SOURCE = "unlisted-source"
UNCHECKED_SOURCE = "unchecked-source"
OPEN_APP = "open-visibility"


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
    """Whether a visibility value means the app is reachable without a Domino grant.

    Matched against named values rather than as "anything that is not GRANT_BASED", because here
    the unknown value is the likely one, not the exotic one: Sage sets GRANT_BASED itself and reads
    it back only to catch a change made afterwards on Domino's own sharing page — the page Publish
    links to as "Manage settings in Domino". A renamed field or a grown enum would then read as
    not-GRANT_BASED for every app and refuse every republish, including the apps still exactly as
    Sage left them. A guard that fires on everything protects nothing, because it gets turned off.

    So an unrecognised value, and the empty string a caller passes when it could not ask, are both
    reported as not-open. That is a real gap until the field is verified live. It is the smaller
    one: the credential guard beside it still stands, and an app that was already open was already
    open — this guard failing means a re-publish it should have stopped goes through, not that Sage
    opened anything.
    """
    return raw.strip().upper().replace("-", "_").replace(" ", "_") in OPEN_VISIBILITY


def publish_problems(
    bindings: list[Binding],
    sources: list[DataSource] | None,
    visibility: str,
) -> list[PublishProblem]:
    """Every reason not to publish an app holding these Data Source Bindings.

    `sources` is the Data Source listing the caller fetched, or `None` when it could not be
    fetched. `None` refuses, one problem per Binding: this function is only ever reached because
    the app reads a store, and "Sage could not check whether that store is shared" is not a reason
    to assume it is. The asymmetry with `open_visibility`, which fails open, is deliberate — the
    credential listing is the same public endpoint the Resource Browser already reads successfully,
    so a failure there is loud and transient, where an unverified visibility field would fail
    silently and permanently.

    `visibility` is the app's current sharing setting, or "" when there is no published app yet or
    the caller could not read it.
    """
    problems = [_credential_problem(b, sources) for b in bindings]
    out = [p for p in problems if p is not None]
    if bindings and open_visibility(visibility):
        out.append(PublishProblem(OPEN_APP, _open_message(bindings)))
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


def _open_message(bindings: list[Binding]) -> str:
    # The remedy names the page rather than the control on it. Sage sets this value once and has
    # never read Domino's own label for it, and sending a creator to look for a word that is not
    # there is worse than sending them to the right page.
    return (
        f"This app can be opened by anyone, and it reads {_names(bindings)}. A published app "
        f"queries the store as its publisher, so anyone who reached the app would be reading it. "
        f"Change the app's sharing on its settings page in Domino so that only people you grant "
        f"access can open it, then publish again."
    )


def _names(bindings: list[Binding]) -> str:
    """The bound Data Sources as one readable phrase, so a refusal names what it is about."""
    names = [b.display_name for b in bindings]
    if len(names) == 1:
        return f"the Data Source {names[0]}"
    return f"the Data Sources {', '.join(names[:-1])} and {names[-1]}"
