"""The sensitivity gate — is this Project declared, and what may it call (ADR-0043).

Two questions, one module, because they are only ever asked together and neither is useful alone:

    declared(bindings) -> the bound Datasets carrying the sensitivity tag
    approved()         -> the models an administrator approved, or None when the feature is off

Deep module, narrow interface. The router stays pure (it is handed a frozenset), the publish guard
stays pure (it is handed an `ApprovedModels`), and every network read and every cache lives here.

OFF BY DEFAULT. With `SAGE_SENSITIVE_MODEL_GROUP` unset, `approved()` answers None and `declared()`
answers empty without a single call — Domino dataset tags are freeform and pre-existing, so a
customer who tagged something `sensitive` years ago for their own reasons must not find their
Projects locked by a Sage upgrade they did not ask for.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Collection

from ..assets.provider import Asset, is_sensitive, sensitivity_tags
from ..orchestrator import brand
from .bindings import KIND_DATASET, Binding
from .provider import ApprovedModels, LlmAlias, approved_aliases

log = logging.getLogger("sage.resources.sensitivity")

# How long a verdict is held, and the asymmetry is the whole point. Holding "declared" too long
# over-restricts a Project, which is annoying. Holding "not declared" too long sends rows to a
# vendor model after somebody tagged the Dataset to stop exactly that, which is the leak this
# exists to prevent. So the safe answer is cached and the unsafe one is barely cached at all.
DECLARED_TTL_S = 300.0
UNDECLARED_TTL_S = 5.0
# The approved set changes when an administrator edits a group — rare, and re-read often enough that
# a correction lands inside a session.
APPROVED_TTL_S = 60.0


def group_name(env: dict[str, str] | None = None) -> str:
    """The alias group approving models for sensitive work. Empty means the feature is off."""
    env = env if env is not None else dict(os.environ)
    return env.get("SAGE_SENSITIVE_MODEL_GROUP", "").strip()


def declared_keys(assets: list[Asset], tags: Collection[str]) -> frozenset[str]:
    """Ids AND names of the Datasets carrying the declaration — the cacheable half of the question.

    Both, because a Binding records what the creator picked and the listing is what carries the
    tags, and the two do not always key the same way. Matching on either is the join surviving a
    disagreement; matching on only one is a declared Dataset silently reading as undeclared.

    `tags` is every tag that declares, not one: a deployment can name several synonyms, and any one
    of them is a declaration (ADR-0043).
    """
    declared = [a for a in assets if is_sensitive(a, tags)]
    return frozenset({a.id for a in declared} | {a.name for a in declared})


class SensitivityGate:
    """Reads the declaration and the approved set, and caches them asymmetrically.

    Both readers are injected rather than a provider being held, because this module has no business
    knowing how a Dataset listing or an alias listing is fetched — and because that keeps every test
    here free of a Domino.
    """

    def __init__(
        self,
        list_assets: Callable[[], list[Asset]],
        list_aliases: Callable[[], list[LlmAlias]],
        list_alias_groups: Callable[[], list[dict]] | None = None,
        *,
        env: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._list_assets = list_assets
        self._list_aliases = list_aliases
        self._list_alias_groups = list_alias_groups
        self._env = env
        self._clock = clock
        # The cached fact is the listing's verdict — which Dataset ids and names are declared — not
        # one call's Bindings, so a second call with a different Binding set reads it correctly.
        # One gate per Project, because `list_assets` is Project-scoped.
        self._declared: tuple[float, frozenset[str]] | None = None
        self._approved: tuple[float, ApprovedModels] | None = None

    @property
    def enabled(self) -> bool:
        return bool(group_name(self._env))

    def declared(self, bindings: list[Binding]) -> list[Binding]:
        """The declared Datasets this Project binds. Empty when the feature is off.

        A listing failure answers the SAFE way, not the convenient one: if Sage cannot tell whether
        a bound Dataset is declared, it treats the Project as declared and lets `approved()` decide
        what that costs. The alternative is answering "not sensitive" because the network wobbled.
        """
        if not self.enabled:
            return []
        dataset_bindings = [b for b in bindings if b.kind == KIND_DATASET]
        if not dataset_bindings:
            return []
        cached = self._fresh(self._declared, self._ttl_for(self._declared))
        if cached is None:
            try:
                assets = self._list_assets()
            except Exception:
                log.exception("sensitivity: couldn't list Datasets; treating this Project as declared")
                return dataset_bindings
            cached = declared_keys(assets, sensitivity_tags(self._env))
            self._declared = (self._clock(), cached)
        return [b for b in dataset_bindings if b.id in cached or b.name in cached]

    def approved(self) -> ApprovedModels | None:
        """The approved models, or None when the deployment never opted in.

        Never raises. A gateway that will not answer becomes `reachable=False`, which the publish
        guard turns into `unchecked-alias` and the turn gate turns into a refusal — both of which
        say "try again", because an unreadable approval is not an approval.
        """
        name = group_name(self._env)
        if not name:
            return None
        cached = self._fresh(self._approved, APPROVED_TTL_S)
        if cached is not None:
            return cached
        try:
            aliases = self._list_aliases()
        except Exception:
            log.exception("sensitivity: couldn't list LLM Aliases to resolve the approved group")
            return ApprovedModels(group_name=name, reachable=False)
        records = self._group_records()
        found, members = self._group_shape(name, records, aliases)
        approved = approved_aliases(name, aliases, records)
        result = ApprovedModels(
            names=frozenset(approved), order=approved,
            group_name=name, group_found=found, members=members,
        )
        self._approved = (self._clock(), result)
        return result

    def _group_records(self) -> list[dict]:
        """The /api/alias-groups listing, or []. The reverse source is a hedge, never a dependency:
        a deployment whose gateway refuses it still resolves from `groups` on the alias records."""
        if self._list_alias_groups is None:
            return []
        try:
            return self._list_alias_groups()
        except Exception:
            log.exception("sensitivity: couldn't list alias groups; falling back to alias `groups`")
            return []

    @staticmethod
    def _group_shape(name: str, records: list[dict], aliases: list[LlmAlias]) -> tuple[bool, int]:
        """Whether the group exists, and how many members it declares before permissions.

        This is what separates `missing-model-group` from `empty-model-group` from
        `no-approved-model-access`, and each of the three is a different thing to ask an
        administrator for. The reverse source settles it outright. When only the forward source
        answered, a group is "found" only if some alias claims it: an alias listing cannot report an
        empty group, so a configured name that nothing claims reads as missing — which is the likelier
        cause anyway, since a misspelt SAGE_SENSITIVE_MODEL_GROUP looks exactly like this.
        """
        want = name.lower()
        for rec in records:
            if isinstance(rec, dict) and str(rec.get("name") or "").lower() == want:
                return True, len(rec.get("aliases") or [])
        claimed = sum(1 for a in aliases if any(g.lower() == want for g in a.groups))
        return bool(claimed), claimed

    def _ttl_for(self, entry: tuple[float, frozenset[str]] | None) -> float:
        return DECLARED_TTL_S if entry and entry[1] else UNDECLARED_TTL_S

    def _fresh(self, entry, ttl: float):
        if entry is None or self._clock() - entry[0] >= ttl:
            return None
        return entry[1]


def declared_turn_refusal(approved: ApprovedModels, declared: list[Binding]) -> str:
    """The sentence refusing a turn whose approved set resolves to nothing (ADR-0043).

    Named for the declaration rather than for the turn because `preflight.turn_refusal` already
    holds the plain name, and two functions called the same thing refusing turns for unrelated
    reasons is exactly the confusion a glossary exists to stop.

    A sibling of `publish_guard._unusable`, not a reuse of it: the four reasons are the same four,
    but the thing to do afterwards is not. "Publish again" is wrong advice to a person who was
    trying to ask a question, so the reason is shared and the closing act is written for the turn.
    """
    names = _dataset_phrase(declared)
    if not approved.reachable:
        return brand.text(
            "{assistantName} couldn't reach the {llmGateway} to check which models are approved for "
            "sensitive data, and this project uses {names}. Try again in a moment.", names=names)
    if not approved.group_found:
        return brand.text(
            "No {llmAlias} group named {group} exists on the {llmGateway}, so nothing is approved "
            "for sensitive data and {assistantName} can't work with {names}. Ask your "
            "{platformName} administrator to create the group.",
            group=approved.group_name, names=names)
    if not approved.members:
        return brand.text(
            "The {llmAlias} group {group} has no models in it, so nothing is approved for sensitive "
            "data and {assistantName} can't work with {names}. Ask your {platformName} "
            "administrator to add a model to the group.",
            group=approved.group_name, names=names)
    return brand.text(
        "You don't have access to any of the {count} models in the {llmAlias} group {group}, which "
        "are the only ones approved for sensitive data. {assistantName} can't work with {names} "
        "until you do. Ask your {platformName} administrator for access to one of them.",
        count=str(approved.members), group=approved.group_name, names=names)


def _dataset_phrase(declared: list[Binding]) -> str:
    names = [b.display_name or b.name for b in declared]
    if len(names) == 1:
        return brand.text("the {dataset} {name}", name=names[0])
    return brand.text("the {datasetPlural} {names}",
                      names=f"{', '.join(names[:-1])} and {names[-1]}")


def declared_turn_refusal_for_model(model: str, approved: frozenset[str]) -> str:
    """The sentence refusing ONE named model, for the previewed app's own call (ADR-0043).

    Separate from `declared_turn_refusal` because the failure is different: there the approved set
    was unusable and nobody could do anything in Sage about it, here the set is fine and the app is
    simply pointed at the wrong model — which the creator fixes themselves, in the picker, now.
    """
    return brand.text(
        "{model} isn't approved for sensitive data, and this project uses a {dataset} that is "
        "declared sensitive. Bind an approved {llmAlias} in {assistantName} — {options} — then "
        "reload the preview.",
        model=model, options=", ".join(sorted(approved)))
