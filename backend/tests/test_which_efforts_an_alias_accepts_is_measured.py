"""#280 — which `reasoning_effort` values an alias accepts is measured, never read off its name.

The old answer was a name match: anything spelled `gpt-5` or `o1`/`o3`/`o4` accepted Low/Medium/
High, everything else accepted nothing. Probed live on 2026-09-12 (scripts/reasoning-probe.py) that
is wrong in both directions — `domino/gemini-3.7-flash` honours the field, and is the only alias
that accepts it alongside tools, yet its name carries no tell. These tests hold the table to what
was measured and hold the shape filter wide enough to let a legal spelling through.
"""
from __future__ import annotations

from sage.resources.provider import (
    _EFFORT_VALUES,
    alias_reasoning_efforts,
    join_aliases,
    parse_reasoning_efforts,
)
from sage.router.models import REASONING_EFFORTS, reasoning_efforts_for


def test_the_alias_that_honours_the_field_alongside_tools_is_offered_one():
    # The whole reason the name heuristic had to go: gemini honours `reasoning_effort` and no name
    # match could have said so.
    assert reasoning_efforts_for("gemini-3.7-flash") == ("low", "medium", "high", "max")
    # The gateway offers it provider-prefixed; only the bare id is meaningful.
    assert reasoning_efforts_for("domino/gemini-3.7-flash") == ("low", "medium", "high", "max")


def test_a_level_the_backing_model_refuses_is_not_offered():
    # Gemini's own 400 names 'high','low','max','medium','minimal' — and `minimal` then 400s at
    # Vertex ("Thinking level unsupported: THINKING_LEVEL_MINIMAL"). The table publishes what runs,
    # not what is advertised.
    assert "minimal" not in reasoning_efforts_for("gemini-3.7-flash")
    # `max` is the other half of the same measurement: legal, and previously dropped by Sage.
    assert "max" in _EFFORT_VALUES
    assert parse_reasoning_efforts({"reasoning_effort": ["low", "max"]}) == ["low", "max"]


def test_a_level_the_probe_found_is_offered_even_though_nothing_advertised_it():
    # The name match published low/medium/high for gpt-5.4 and was never checked against the alias.
    # It also takes `none` and `xhigh` — and `none` is the one level it accepts alongside function
    # tools, which is the gateway's own advice in the refusal enforcement.apply guards against.
    assert reasoning_efforts_for("gpt-5.4") == ("none", "low", "medium", "high", "xhigh")


def test_an_alias_nobody_measured_is_offered_nothing():
    # sonnet, opus-4.8, haiku and both Gemmas all answered 200 to a nonsense value: they discard the
    # field. An alias that was never probed is treated the same way — a missing control costs a
    # person one choice, a wrong guess costs a hard 400 that kills the turn.
    for name in ("sonnet", "Opus-4.8", "haiku", "gemma-4-31b", "qwen-2-5"):
        assert reasoning_efforts_for(name) == ()
    # gpt-5.4-nano was never probed, and the name it shares with gpt-5.4 is not evidence.
    assert reasoning_efforts_for("gpt-5.4-nano") == ()


def test_the_measured_alias_reaches_the_picker_through_the_join():
    (gemini,) = join_aliases({"gemini-3.7-flash"}, [])
    assert gemini.reasoning_efforts == ["low", "medium", "high", "max"]
    (sonnet,) = join_aliases({"sonnet"}, [])
    assert sonnet.reasoning_efforts == []


def test_gateway_metadata_still_chooses_which_levels_an_alias_offers():
    # Metadata is the live answer and the table is a snapshot, so an advertised enum still decides
    # the offer. Today every alias publishes `inference_params: {}` (#284), so in practice the
    # table answers alone.
    rec = {"id": "x", "name": "gemini-3.7-flash",
           "inference_params": {"reasoning_effort": ["low", "high"]}}
    (a,) = join_aliases({"gemini-3.7-flash"}, [rec])
    assert a.reasoning_efforts == ["low", "high"]
    # An alias nobody probed has no row to narrow by, so its enum passes through whole.
    assert alias_reasoning_efforts("sonnet", {"reasoning_effort": ["medium"]}) == ["medium"]


def test_an_advertised_level_the_probe_proved_broken_never_reaches_the_picker():
    # The half of #284 that would otherwise re-open the trap: the day the gateway starts publishing
    # `inference_params`, gemini's own enum arrives carrying `minimal`, and a turn that picks it
    # hard-400s at Vertex. A probed alias has had every spelling tried, so its row may narrow.
    # The enum as the gateway spells it: alphabetical, and carrying the level Vertex refuses.
    advertised = {"reasoning_effort": ["high", "low", "max", "medium", "minimal"]}
    # `minimal` gone, and the order is the table's — this list is rendered straight into the effort
    # menu, so obeying the enum's order would read High / Low / Max / Medium.
    assert alias_reasoning_efforts("gemini-3.7-flash", advertised) == ["low", "medium", "high", "max"]
    # Narrowing only, never inventing: a level the table holds but the gateway stopped offering
    # goes away with the gateway's word for it.
    assert alias_reasoning_efforts("gemini-3.7-flash", {"reasoning_effort": ["low"]}) == ["low"]


def test_every_effort_in_the_table_is_a_legal_spelling():
    # The table says what an alias accepts; `_EFFORT_VALUES` says what the spelling may be. A level
    # in one and not the other means the gateway could advertise it and Sage would silently drop it,
    # which is exactly how `max` was lost.
    for efforts in REASONING_EFFORTS.values():
        assert set(efforts) <= _EFFORT_VALUES
