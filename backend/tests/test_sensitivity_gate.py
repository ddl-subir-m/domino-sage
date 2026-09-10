"""The sensitivity gate: is this Project declared, and what may it call (ADR-0043).

No Domino and no clock. Both readers are injected, so every case here — including the two failure
shapes that matter most, an unreadable Dataset listing and an unreadable gateway — is a plain
function raising.
"""
from __future__ import annotations

import pytest

from sage.assets.provider import Asset
from sage.resources.bindings import KIND_DATASET, KIND_LLM_ALIAS, Binding
from sage.resources.provider import LlmAlias
from sage.resources.sensitivity import (
    APPROVED_TTL_S,
    DECLARED_TTL_S,
    UNDECLARED_TTL_S,
    SensitivityGate,
    declared_keys,
    group_name,
)

ON = {"SAGE_SENSITIVE_MODEL_GROUP": "sensitive-approved"}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _asset(name, tags=(), aid=None):
    return Asset(id=aid or f"ds-{name}", name=name, tags=list(tags))


def _dataset(name, bid=None):
    return Binding(kind=KIND_DATASET, id=bid or f"ds-{name}", name=name, display_name=name)


def _alias(name, groups=()):
    return LlmAlias(id=f"id-{name}", name=name, display_name=name, groups=list(groups))


def _gate(assets=(), aliases=(), groups=None, env=ON, clock=None, taxonomy=None):
    return SensitivityGate(
        lambda: list(assets), lambda: list(aliases),
        (lambda: list(groups)) if groups is not None else None,
        taxonomy,
        env=env, clock=clock or Clock(),
    )


# --- Off by default ------------------------------------------------------------------------------

def test_the_feature_is_off_without_the_group_env_var():
    """A pre-existing `sensitive` tag must not lock a deployment that never opted in."""
    def boom():
        raise AssertionError("an opted-out deployment must not read anything")

    gate = SensitivityGate(boom, boom, env={})
    assert gate.enabled is False
    assert gate.declared([_dataset("pii")]) == []
    assert gate.approved() is None


def test_group_name_is_trimmed_and_empty_means_off():
    assert group_name({"SAGE_SENSITIVE_MODEL_GROUP": "  g  "}) == "g"
    assert group_name({"SAGE_SENSITIVE_MODEL_GROUP": "   "}) == ""
    assert group_name({}) == ""


# --- The declaration -----------------------------------------------------------------------------

def test_declared_keys_carries_both_id_and_name():
    """The Binding and the listing do not always key the same way; matching one only would miss."""
    keys = declared_keys([_asset("pii", ["sensitive"], aid="a1"), _asset("logs")], {"sensitive"})
    assert keys == frozenset({"a1", "pii"})


def test_declared_keys_takes_every_tag_that_declares():
    """A deployment can name several synonyms, and any one of them is a declaration (ADR-0043)."""
    assets = [_asset("pii", ["PII"], aid="a1"), _asset("hr", ["confidential"], aid="a2"),
              _asset("logs", ["curated"], aid="a3")]
    keys = declared_keys(assets, {"pii", "confidential"})
    assert keys == frozenset({"a1", "pii", "a2", "hr"})


def test_the_declaring_tags_are_read_from_the_environment_as_a_list():
    """The gate reads `SAGE_SENSITIVE_DATASET_TAG` itself, so the list has to survive the trip."""
    env = {**ON, "SAGE_SENSITIVE_DATASET_TAG": "pii, confidential"}
    gate = _gate(assets=[_asset("hr", ["Confidential"]), _asset("logs", ["sensitive"])], env=env)
    assert [b.name for b in gate.declared([_dataset("hr"), _dataset("logs")])] == ["hr"]


def test_only_a_tagged_dataset_is_declared():
    gate = _gate(assets=[_asset("pii", ["sensitive"]), _asset("logs")])
    assert [b.name for b in gate.declared([_dataset("pii"), _dataset("logs")])] == ["pii"]


def test_a_binding_that_is_not_a_dataset_is_never_declared():
    gate = _gate(assets=[_asset("pii", ["sensitive"])])
    alias = Binding(kind=KIND_LLM_ALIAS, id="a1", name="gpt-5.4", display_name="gpt-5.4")
    assert gate.declared([alias]) == []


def test_an_unreadable_dataset_listing_treats_the_project_as_declared():
    """The safe answer, not the convenient one. 'Sage could not check' is not 'not sensitive'."""
    def boom():
        raise RuntimeError("domino down")

    gate = SensitivityGate(boom, list, env=ON)
    bound = [_dataset("pii"), _dataset("logs")]
    assert gate.declared(bound) == bound


# --- The Taxonomy API: a second, unrelated tag system (ADR-0043) --------------------------------
#
# LIVE-VERIFIED 2026-09-10: a Dataset tagged through the UI's own Tags panel never reaches the
# datasetrw tag map `list_assets` reads — it lands in Domino's Taxonomy API instead. `declared()`
# has to check both.


def test_a_taxonomy_declared_dataset_is_declared_even_when_the_old_tag_map_is_not():
    gate = _gate(assets=[_asset("card_txn_raw")], taxonomy=lambda dsid: ["sensitive"])
    assert [b.name for b in gate.declared([_dataset("card_txn_raw")])] == ["card_txn_raw"]


def test_a_dataset_neither_system_tags_is_not_declared():
    gate = _gate(assets=[_asset("logs")], taxonomy=lambda dsid: [])
    assert gate.declared([_dataset("logs")]) == []


def test_the_taxonomy_lookup_is_skipped_once_the_old_system_already_declares():
    """No reason to make a second call about a Dataset the first system already settled."""
    def boom(dataset_id):
        raise AssertionError("already declared by the old system; taxonomy must not be asked")

    gate = _gate(assets=[_asset("pii", ["sensitive"])], taxonomy=boom)
    assert [b.name for b in gate.declared([_dataset("pii")])] == ["pii"]


def test_an_unreadable_taxonomy_answer_treats_only_that_dataset_as_declared():
    """The same fail-safe as an unreadable listing, but scoped to the one Dataset the lookup
    happened to fail on — a wobble there must not lock every Dataset in scope."""
    def taxonomy(dataset_id):
        if dataset_id == "ds-flaky":
            raise RuntimeError("taxonomy down")
        return []

    gate = _gate(assets=[_asset("flaky", aid="ds-flaky"), _asset("clean", aid="ds-clean")],
                 taxonomy=taxonomy)
    declared = gate.declared([_dataset("flaky", bid="ds-flaky"), _dataset("clean", bid="ds-clean")])
    assert [b.name for b in declared] == ["flaky"]


def test_taxonomy_labels_match_case_insensitively():
    """Matched on `namespaceLabel` (the category), same case-insensitive rule as the old tag map."""
    gate = _gate(assets=[_asset("pii")], taxonomy=lambda dsid: ["Sensitive"])
    assert [b.name for b in gate.declared([_dataset("pii")])] == ["pii"]


def test_the_taxonomy_verdict_is_cached_on_the_same_asymmetric_ttl():
    clock, reads = Clock(), []

    def taxonomy(dataset_id):
        reads.append(clock.t)
        return ["sensitive"] if reads[-1] < 2000 else []

    gate = _gate(assets=[_asset("pii")], taxonomy=taxonomy, clock=clock)
    assert gate.declared([_dataset("pii")]) != []
    clock.t += DECLARED_TTL_S - 1
    assert gate.declared([_dataset("pii")]) != []
    assert len(reads) == 1, "a declared verdict is held"

    clock.t = 2000.0                       # past the declared TTL; the tag has since been removed
    assert gate.declared([_dataset("pii")]) == []
    clock.t += UNDECLARED_TTL_S + 1
    gate.declared([_dataset("pii")])
    assert len(reads) == 3, "an undeclared verdict expires quickly and is re-read"


# --- The cache is asymmetric, which is the point ------------------------------------------------

def test_a_declared_verdict_is_held_and_an_undeclared_one_is_not():
    """Holding 'declared' too long over-restricts. Holding 'not declared' too long is the leak."""
    clock, reads = Clock(), []

    def listing():
        reads.append(clock.t)
        return [_asset("pii", ["sensitive"])] if reads[-1] < 2000 else [_asset("pii")]

    gate = SensitivityGate(listing, list, env=ON, clock=clock)
    assert gate.declared([_dataset("pii")]) != []
    clock.t += DECLARED_TTL_S - 1
    assert gate.declared([_dataset("pii")]) != []
    assert len(reads) == 1, "a declared verdict is held"

    clock.t = 2000.0                       # past the declared TTL; the tag has since been removed
    assert gate.declared([_dataset("pii")]) == []
    clock.t += UNDECLARED_TTL_S + 1
    gate.declared([_dataset("pii")])
    # Three reads, not four: the second call inside the declared TTL was served from cache, and
    # both later ones had to go back to Domino. That gap is the asymmetry doing its job.
    assert len(reads) == 3, "an undeclared verdict expires quickly and is re-read"


def test_the_undeclared_ttl_is_shorter_than_the_declared_one():
    """If this ever inverts, the leak is back. Stated as an assertion, not as a comment."""
    assert UNDECLARED_TTL_S < DECLARED_TTL_S


# --- The approved set ----------------------------------------------------------------------------

def test_the_approved_set_resolves_from_the_alias_groups_field():
    gate = _gate(aliases=[_alias("qwen-2-5", ["sensitive-approved"]), _alias("gpt-5.4")])
    approved = gate.approved()
    assert approved.names == frozenset({"qwen-2-5"})
    assert approved.usable and approved.group_found and approved.members == 1


def test_the_approved_set_resolves_from_the_reverse_source_too():
    """The LIVE-VERIFY hedge: a redacted `groups` field still resolves via /api/alias-groups."""
    gate = _gate(
        aliases=[_alias("qwen-2-5"), _alias("gpt-5.4")],
        groups=[{"name": "sensitive-approved", "aliases": [{"id": "id-qwen-2-5", "name": "qwen-2-5"}]}],
    )
    assert gate.approved().names == frozenset({"qwen-2-5"})


def test_the_administrators_ordering_survives_the_read():
    """`names` is a set and the group is a list somebody wrote in an order. The router prefers by
    that order, so it has to travel — deriving it back by sorting the set would be the alphabet."""
    gate = _gate(
        aliases=[_alias("alpha"), _alias("zeta"), _alias("gpt-5.4")],
        groups=[{"name": "sensitive-approved",
                 "aliases": [{"id": "id-zeta"}, {"id": "id-alpha"}]}],
    )
    approved = gate.approved()
    assert approved.order == ("zeta", "alpha")
    assert approved.names == frozenset({"zeta", "alpha"})


def test_the_reverse_source_leads_the_ordering_and_the_forward_source_follows():
    """Only /api/alias-groups carries an ordering; `groups` on an alias is whatever /api/aliases
    listed. The union is unchanged — a redacted field still resolves — but the members the
    administrator ordered come first."""
    gate = _gate(
        aliases=[_alias("forward-only", ["sensitive-approved"]), _alias("zeta"), _alias("alpha")],
        groups=[{"name": "sensitive-approved",
                 "aliases": [{"id": "id-zeta"}, {"id": "id-alpha"}]}],
    )
    approved = gate.approved()
    assert approved.order == ("zeta", "alpha", "forward-only")
    assert approved.names == frozenset({"zeta", "alpha", "forward-only"})


def test_a_group_member_the_caller_cannot_call_is_left_out_of_the_ordering_too():
    """The permission filter is what makes `no-approved-model-access` a designed refusal rather than
    a dead turn, and an unreachable name in the preference order would walk straight past it."""
    gate = _gate(
        aliases=[_alias("alpha")],
        groups=[{"name": "sensitive-approved",
                 "aliases": [{"id": "id-haiku", "name": "haiku"}, {"id": "id-alpha"}]}],
    )
    assert gate.approved().order == ("alpha",)


def test_an_unreachable_gateway_is_not_an_approval():
    def boom():
        raise RuntimeError("gateway down")

    approved = SensitivityGate(list, boom, env=ON).approved()
    assert approved.reachable is False
    assert approved.usable is False


def test_a_group_nothing_claims_reads_as_missing_not_empty():
    """A misspelt SAGE_SENSITIVE_MODEL_GROUP looks exactly like this, and 'create the group' is
    the actionable sentence for it."""
    approved = _gate(aliases=[_alias("gpt-5.4")]).approved()
    assert approved.group_found is False and approved.members == 0


def test_the_reverse_source_can_prove_a_group_is_present_but_empty():
    approved = _gate(
        aliases=[_alias("gpt-5.4")], groups=[{"name": "sensitive-approved", "aliases": []}]
    ).approved()
    assert approved.group_found is True and approved.members == 0


def test_a_group_with_members_the_caller_cannot_call_is_told_apart():
    """`/api/aliases` lists every registration; `/v1/models` lists what this caller may use. The
    join is what makes this case real, and it is the one that will actually happen."""
    approved = _gate(
        aliases=[_alias("gpt-5.4")],
        groups=[{"name": "sensitive-approved",
                 "aliases": [{"id": "id-secret-a"}, {"id": "id-secret-b"}, {"id": "id-secret-c"}]}],
    ).approved()
    assert approved.usable is False
    assert approved.group_found is True and approved.members == 3


def test_a_failing_group_listing_falls_back_to_the_forward_source():
    """The reverse source is a hedge, never a dependency."""
    def boom():
        raise RuntimeError("no")

    gate = SensitivityGate(
        list, lambda: [_alias("qwen-2-5", ["sensitive-approved"])], boom, env=ON
    )
    assert gate.approved().names == frozenset({"qwen-2-5"})


def test_the_approved_set_is_cached_then_re_read():
    clock, reads = Clock(), []

    def aliases():
        reads.append(1)
        return [_alias("qwen-2-5", ["sensitive-approved"])]

    gate = SensitivityGate(list, aliases, env=ON, clock=clock)
    gate.approved()
    gate.approved()
    assert len(reads) == 1
    clock.t += APPROVED_TTL_S + 1
    gate.approved()
    assert len(reads) == 2, "an administrator's correction lands inside a session"


def test_an_unreachable_gateway_is_never_cached():
    """A wobble must not lock the Project for a minute after the gateway comes back."""
    clock, state = Clock(), {"fail": True}

    def aliases():
        if state["fail"]:
            raise RuntimeError("down")
        return [_alias("qwen-2-5", ["sensitive-approved"])]

    gate = SensitivityGate(list, aliases, env=ON, clock=clock)
    assert gate.approved().reachable is False
    state["fail"] = False
    assert gate.approved().names == frozenset({"qwen-2-5"})


@pytest.mark.parametrize("group", ["SENSITIVE-APPROVED", "sensitive-approved", "Sensitive-Approved"])
def test_the_group_name_matches_case_insensitively(group):
    """Typed by a person twice — once in configuration, once on the gateway."""
    gate = _gate(aliases=[_alias("qwen-2-5", [group])])
    assert gate.approved().names == frozenset({"qwen-2-5"})


# --- The turn refusal (ADR-0043) -----------------------------------------------------------------

def test_the_turn_refusal_says_what_to_do_and_never_says_publish():
    """A sibling of the publish sentences, not a reuse: 'publish again' is wrong advice to somebody
    who was asking a question. Same four reasons, different closing act."""
    from sage.resources.provider import ApprovedModels
    from sage.resources.sensitivity import declared_turn_refusal

    declared = [_dataset("customer_pii")]
    shapes = [
        ApprovedModels(group_name="sensitive-approved", reachable=False),
        ApprovedModels(group_name="sensitive-approved", group_found=False),
        ApprovedModels(group_name="sensitive-approved", members=0),
        ApprovedModels(group_name="sensitive-approved", members=3),
    ]
    messages = [declared_turn_refusal(a, declared) for a in shapes]
    assert len({*messages}) == 4, "four reasons, four sentences"
    for m in messages:
        assert "customer_pii" in m
        assert "publish" not in m.lower(), "this refuses a turn, not a publish"
    # The three a person fixes name who to ask; the transient one says to wait instead.
    assert "in a moment" in messages[0]
    for m in messages[1:]:
        assert "administrator" in m


def test_the_turn_refusal_names_every_declared_dataset():
    from sage.resources.provider import ApprovedModels
    from sage.resources.sensitivity import declared_turn_refusal

    message = declared_turn_refusal(
        ApprovedModels(group_name="g", members=2), [_dataset("pii"), _dataset("payroll")]
    )
    assert "pii" in message and "payroll" in message and " and " in message
