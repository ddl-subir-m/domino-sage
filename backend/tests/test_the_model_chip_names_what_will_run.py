"""The composer's model chip must name the model that will RUN, not the one that was picked.

FOUND IN LIVE QA (2026-09-09), not by any test written from the ADR. With a declared Dataset bound
and `gpt-5.4` picked, the lock moved the session to `opus` and said so in a notice — but the notice
carries a "Got it", and the chip went on reading `gpt-5.4`. Dismiss the notice and the one control a
person reads to know what is running named a model that could not run.

The rule is deliberately narrow: name a model only where there is no routing rule left to apply
(exactly one approved), because `llm_router._nearest_approved` depends on mode and phase and a
second copy of it here would be a confident label that is wrong on the turn where it matters.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "sensitivity_chip_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

OFF = {"enabled": False, "locked": False, "approved": [], "datasets": [], "group": ""}
ONE = {"enabled": True, "locked": True, "approved": ["opus"],
       "datasets": ["sage-subir-mansukhani-66a821b1-2"], "group": "sensitive-approved"}
MANY = {**ONE, "approved": ["haiku", "opus"]}


def _labels(cases: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"cases": cases}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_an_unapproved_pick_is_replaced_by_the_one_approved_model():
    """THE regression. `gpt-5.4` picked, `opus` the only approved model: the chip says `opus`."""
    got = _labels([{"sensitivity": ONE, "picked": "gpt-5.4"}])[0]
    assert got["label"] == "opus"
    assert got["locked"] is True and got["approved"] is False


@needs_node
def test_an_approved_pick_keeps_its_own_name():
    """The lock narrows; it does not relabel. A turn already on an approved model reads normally."""
    assert _labels([{"sensitivity": ONE, "picked": "opus"}])[0]["label"] == "opus"


@needs_node
def test_no_lock_never_touches_the_label():
    """Off by default has to be invisible: an opted-out deployment sees the pick it made."""
    got = _labels([{"sensitivity": OFF, "picked": "gpt-5.4"},
                   {"sensitivity": None, "picked": "gpt-5.4"}])
    assert [g["label"] for g in got] == ["gpt-5.4", "gpt-5.4"]


@needs_node
def test_several_approved_models_are_not_guessed_between():
    """With more than one approved, WHICH one runs depends on mode and phase — a rule that lives in
    `llm_router._nearest_approved` and must not be reimplemented here. So the chip stops short of a
    name rather than risk a confident wrong one, and still refuses to show the barred pick."""
    label = _labels([{"sensitivity": MANY, "picked": "gpt-5.4"}])[0]["label"]
    assert label != "gpt-5.4", "the chip must never name a model that cannot run"
    assert label not in ("haiku", "opus"), "it cannot know which, so it must not claim one"
    assert label.strip() != "", "and silence is not an option either"


@needs_node
def test_the_rail_marks_a_barred_alias_the_way_the_picker_greys_it():
    """FOUND IN LIVE QA (2026-09-09) alongside the chip: the picker greyed `gpt-5.4` and said why,
    while the resources rail listed the same bound Alias as an ordinary row. One Binding must not
    read as usable in one surface and refused in another."""
    got = _labels([{"sensitivity": ONE, "picked": "gpt-5.4"},
                   {"sensitivity": ONE, "picked": "opus"},
                   {"sensitivity": OFF, "picked": "gpt-5.4"}])
    assert [g["barred"] for g in got] == [True, False, False]


@needs_node
def test_only_an_alias_is_ever_barred():
    """The mark belongs to a model. A Dataset carrying the same name as an unapproved Alias must
    not inherit its refusal — the rail draws both kinds in one column."""
    got = _labels([{"sensitivity": ONE, "picked": "gpt-5.4", "kind": "dataset"},
                   {"sensitivity": ONE, "picked": "gpt-5.4", "kind": "datasource"}])
    assert [g["barred"] for g in got] == [False, False]
