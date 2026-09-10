"""The sensitivity gate — is this Project declared, and what may it call (ADR-0043).

Two questions, one module, because they are only ever asked together and neither is useful alone:

    declared(scope)    -> the Datasets in scope carrying the sensitivity tag
    approved()         -> the models an administrator approved, or None when the feature is off

`scope` is a Binding list and is deliberately NOT the Binding manifest. A Dataset reaches a turn
three ways — bound to the app, attached into its tree, or pinned to a Conversation — and only the
caller knows which of those it can see. Reading the manifest here made the other two invisible,
which is a lock that never fired for the two doors people actually use. The caller assembles the
list (`Orchestrator._datasets_in_scope`) and this module still decides which of them are declared.

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

    def declared(self, scope: list[Binding]) -> list[Binding]:
        """The declared Datasets among the ones in scope. Empty when the feature is off.

        `scope` is every Dataset this turn can reach, however it got there — see the module note.
        Non-Dataset kinds are ignored rather than refused, so a caller can hand over a whole
        manifest without filtering it first.

        A listing failure answers the SAFE way, not the convenient one: if Sage cannot tell whether
        a Dataset in scope is declared, it treats the Project as declared and lets `approved()`
        decide what that costs. The alternative is answering "not sensitive" because the network
        wobbled. That fail-safe covers all three doors, because it is keyed on the listing rather
        than on how the Dataset arrived.
        """
        if not self.enabled:
            return []
        datasets = [b for b in scope if b.kind == KIND_DATASET]
        if not datasets:
            return []
        keys = self._keys()
        if keys is None:
            return datasets
        return [b for b in datasets if b.id in keys or b.name in keys]

    def _keys(self) -> frozenset[str] | None:
        """The declared ids and names, or None when the listing would not answer.

        None rather than an empty set, because the two mean opposite things and every caller has to
        tell them apart: empty is "nothing is declared", unreadable is "assume it is".
        """
        cached = self._fresh(self._declared, self._ttl_for(self._declared))
        if cached is not None:
            return cached
        try:
            assets = self._list_assets()
        except Exception:
            log.exception("sensitivity: couldn't list Datasets; treating this Project as declared")
            return None
        keys = declared_keys(assets, sensitivity_tags(self._env))
        self._declared = (self._clock(), keys)
        return keys

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

    An EMPTY `declared` is the sticky lock: this conversation read declared rows on an earlier turn
    and the Binding has since gone. The four reasons are unchanged — they are about the approved
    group, which never knew why the lock was on — but the sentence may no longer name a Dataset,
    because there is none left on the panel to go and look at, and it gains a second way out. Both
    are true there: an administrator can still fix the group, and a new chat is not waiting on one.
    """
    sentence = _unusable_reason(approved, declared)
    return sentence if declared else f"{sentence} {session_lock_way_out()}"


def _unusable_reason(approved: ApprovedModels, declared: list[Binding]) -> str:
    """Which of the four ways the approved set came to be unusable, said out loud. Split from the
    sentence above only so the sticky lock's extra way out is appended in one place rather than
    four."""
    names = _dataset_phrase(declared)
    if not approved.reachable:
        return brand.text(
            "{assistantName} couldn't check which models are allowed for {names}. Try again in a "
            "moment.", names=names)
    if not approved.group_found:
        return brand.text(
            "Nothing is approved for {names}: no {llmAlias} group named {group} exists. Ask a "
            "{platformName} administrator to create the group.",
            group=approved.group_name, names=names)
    if not approved.members:
        return brand.text(
            "Nothing is approved for {names}: the {llmAlias} group {group} is empty. Ask a "
            "{platformName} administrator to add a model.",
            group=approved.group_name, names=names)
    return brand.text(
        "You don't have access to any of the {count} models approved for {names}. Ask a "
        "{platformName} administrator for access to one of them.",
        count=str(approved.members), names=names)


def _dataset_phrase(declared: list[Binding]) -> str:
    """What the lock is holding for, as an object a sentence can refuse to work with.

    Empty means the lock is the conversation's own and no Dataset is bound any more, so the phrase
    names the reading rather than the Dataset. Naming one that has been unbound would send the
    creator to a panel row that is no longer there.
    """
    names = [b.display_name or b.name for b in declared]
    if not names:
        return brand.text("sensitive data")
    if len(names) == 1:
        return brand.text("the {dataset} {name}", name=names[0])
    return brand.text("the {datasetPlural} {names}",
                      names=f"{', '.join(names[:-1])} and {names[-1]}")


def session_lock_way_out() -> str:
    """The way out of a lock the conversation is carrying rather than the Bindings (ADR-0043).

    Said wherever the sticky lock is explained, because it is the ONLY way out and it is not the one
    a creator will guess: unbinding the Dataset is the obvious move and it is deliberately the wrong
    one — the rows are already in the transcript, which is re-sent on every turn after them.
    """
    return brand.text(
        "Removing the {dataset} won't unlock this chat. Start a new chat to use any model.")


def unrecorded_lock_refusal() -> str:
    """The sentence refusing a turn whose lock could not be written down (ADR-0043).

    The lock has to outlive the turn, because what it protects is the transcript the turn is about
    to write. A turn that ran without recording it would put declared rows into a conversation that
    the next turn — and every turn after a restart — reads as clean. So the write failing stops the
    turn, and this says so in the one register that fits: nothing the creator did is wrong, and
    nothing they can do in Sage fixes it.
    """
    return brand.text(
        "{assistantName} couldn't save that this chat is using sensitive data, so it stopped. "
        "Try again, and tell a {platformName} administrator if it keeps happening.")


def declared_turn_refusal_for_model(model: str, approved: frozenset[str]) -> str:
    """The sentence refusing ONE named model, for the previewed app's own call (ADR-0043).

    Separate from `declared_turn_refusal` because the failure is different: there the approved set
    was unusable and nobody could do anything in Sage about it, here the set is fine and the app is
    simply pointed at the wrong model — which the creator fixes themselves, in the picker, now.
    """
    return brand.text(
        "{model} isn't allowed with sensitive data. Switch to {options}, then reload the preview.",
        model=model, options=", ".join(sorted(approved)))
