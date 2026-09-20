"""A slot says when its model never claimed it could do the slot's job (#463).

WHY THIS EXISTS. Measured live 2026-09-20, `sage_rev 1cd3572`: the `ask` slot was assigned a model
whose capability list named `chat`, `vision` and `responses` and not `tools`, while every Chat turn
sends tools. `GET /api/project/model/assignments` answered `problem=null` for that row. **The slot
reported itself healthy while its assigned model could not do the slot's job**, and the three
read-only classifiers on it fell back in silence on the same turns.

WHY IT IS A MARK AND NEVER A FILTER, which is the half of this that is easy to get backwards. The
same live read found `domino-gcp/claude-sonnet-5` and `domino/gemini-3.7-flash` both declaring
`chat` alone, and `qwen-2-5` declaring no `streaming` while streaming daily. A maintainer then wired
tool calling for GLM 5.3 by hand and its row changed under Sage with nothing recording the
transition in either direction. **Both fields anyone has ever checked against reality were wrong, in
both directions.** So a filter keyed on this metadata would today hide Sonnet 5, and #296 already
wrote the rule these tests hold: capabilities are "last-known filters, not proof of the provider's
current verdict". The plant in `test_a_pick_of_an_unmarked_model_still_succeeds` is the one that
catches a future reader turning the mark into a refusal.

WHY IT IS NOT IN `problem`. `problem` is one string with a ranked precedence over four kinds, and
the panel gate at `model-assignments.js` drops it whenever a row is shadowed and the pin did not
decide it — the precedence comment in `service.model_assignments` records that cost in its own
words. A pinned, locked slot is the row most likely to carry a capability mark, so a warning placed
there would render as nothing on exactly the rows it was written for. The browser half of that split
is `test_the_model_panel_lets_a_person_choose`, which drives the real component.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator
from sage.resources.preflight import tool_capability_note
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import ModelCatalog

# `tools` on the first and not on the second, which is the live shape rather than an invented one:
# on the gateway read for this ticket, one alias had been corrected by hand and its neighbours had
# not. `no-caps` is the third case and the one an over-eager mark gets wrong — an empty list means
# the provider said nothing, not that the model can do nothing.
ALIASES = [
    LlmAlias("id-toolful", "toolful", "Tool-carrying model", None, ["chat", "tools"], {}),
    LlmAlias("id-chatonly", "chat-only", "Chat-only model", None, ["chat"], {}),
    LlmAlias("id-nocaps", "no-caps", "Model that declared nothing", None, [], {}),
]

CATALOG = ModelCatalog(
    sovereign_plan="toolful", sovereign_implement="toolful", sovereign_ask="toolful",
    plan="toolful", implement="toolful", ask="toolful",
)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("placeholder")
    (t / "package.json").write_text("{}")
    (t / "AGENTS.md").write_text("# Template rules\n")
    return t


def _orch(tmp_path: Path) -> Orchestrator:
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=_template(tmp_path),
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES)),
    )
    orch.project(start_preview=False)
    return orch


def _slot(orch: Orchestrator, slot: str) -> dict:
    return next(r for r in orch.model_assignments()["slots"] if r["slot"] == slot)


def _alias_row(orch: Orchestrator, name: str) -> dict:
    return next(a for a in orch.model_assignments()["aliases"] if a["name"] == name)


# ---- the rule itself, on the three inputs it has to tell apart ---------------------------------


def test_a_model_that_declares_chat_without_tools_is_marked():
    note = tool_capability_note(["chat", "vision", "responses"])
    assert note is not None
    # Worded as the uncertainty it is. The assertion is on what the sentence must NOT claim, because
    # that is the part a rewrite is most likely to lose: "cannot" is a promise this metadata has
    # never once been able to keep.
    assert "cannot" not in note
    assert "advertise" in note


def test_the_sentence_names_the_model_it_is_about():
    """#467's lesson, one ticket on: a line that names no model cannot be acted on.

    It matters most where the sentence and the control above it are about different models — the
    panel substitutes what a slot RUNS into its select when the pin or the lock has moved the row,
    while this note is about the model the slot is ASSIGNED. "This model" under that control points
    at whichever of the two the reader happens to think it means.
    """
    named = tool_capability_note(["chat"], "chat-only")
    assert named.startswith("chat-only doesn't")
    # And it still says something true with no name, which is the shape a caller with nothing to
    # name gets rather than a crash or a blank subject.
    assert tool_capability_note(["chat"]).startswith("This model doesn't")


def test_a_model_that_declares_tools_is_not_marked():
    assert tool_capability_note(["chat", "tools"]) is None


def test_an_empty_capability_list_is_not_marked():
    """An empty list means "not known", and an unknown must not be reported as a fault.

    The same reading the chat-model guard beside it takes of the same list — it is written
    `if caps and ...` for this reason — so the two cannot drift into disagreeing about what an
    unanswered provider said.
    """
    assert tool_capability_note([]) is None
    assert tool_capability_note(None) is None


def test_a_model_that_does_not_claim_chat_is_not_judged_on_a_chat_turn():
    """An embeddings row is not being asked what it would do in a conversation it never enters."""
    assert tool_capability_note(["embeddings"]) is None


# ---- the payload carries it, on both kinds of row ----------------------------------------------


def test_the_slot_row_carries_the_mark_for_the_model_it_runs(tmp_path):
    orch = _orch(tmp_path)
    assert _slot(orch, "ask")["capability_note"] is None
    orch.set_catalog(ask="chat-only")
    # Truthy AND equal. `== tool_capability_note(["chat"])` alone is satisfied by two Nones, so a
    # rule that had stopped marking anything would pass it — the plant on this test found exactly
    # that and this line is what closed it.
    note = _slot(orch, "ask")["capability_note"]
    assert note
    # The model NAMED is the one the row shows, which is what makes the sentence readable on a
    # row whose select has been substituted by the pin or the lock.
    assert note == tool_capability_note(["chat"], "chat-only")


def test_a_slot_holding_a_prefixed_model_string_is_still_marked(tmp_path):
    """A slot holds one of TWO strings and only one of them is an Alias name.

    `domino/gemini-3.7-flash` is an Alias name in full; `sage-gateway/sonnet` is an OpenCode model
    id whose first segment is a provider no Alias carries. `preflight.slot_alias` exists for exactly
    this and records six of six slots carrying a slash on a real deployment. This change looked the
    raw catalog string up in the alias listing until review caught it, which left the mark dead on
    those deployments while every bare-name fixture here stayed green — `problem` resolved (it goes
    through `slot_alias`) and the mark beside it did not.
    """
    orch = _orch(tmp_path)
    orch.set_catalog(ask="sage-gateway/chat-only")
    row = _slot(orch, "ask")
    assert row["model"] == "sage-gateway/chat-only"
    assert row["capability_note"]
    # Named as the ROW spells it, not as the alias listing does: the string in the sentence has to
    # be the one the reader can find and change.
    assert row["capability_note"].startswith("sage-gateway/chat-only doesn't")


def test_a_slot_on_a_tool_carrying_model_says_nothing(tmp_path):
    orch = _orch(tmp_path)
    orch.set_catalog(ask="toolful")
    assert _slot(orch, "ask")["capability_note"] is None


def test_a_slot_on_a_model_that_declared_nothing_says_nothing(tmp_path):
    orch = _orch(tmp_path)
    orch.set_catalog(ask="no-caps")
    assert _slot(orch, "ask")["capability_note"] is None


def test_the_mark_is_a_field_of_its_own_and_never_reaches_problem(tmp_path):
    """The whole reason it is a new field. A rank inside `problem` is eaten by the panel's gate.

    Asserted on the payload as well as in the browser harness, because the two halves fail
    independently: a server that folded the sentence into `problem` would pass every rendering test
    in `test_the_model_panel_lets_a_person_choose` and still go silent on a pinned, locked row.
    """
    orch = _orch(tmp_path)
    orch.set_catalog(ask="chat-only")
    row = _slot(orch, "ask")
    assert row["capability_note"]
    assert row["problem"] is None


def test_the_alias_rows_carry_the_mark_without_closing_the_row(tmp_path):
    """A marked alias is still offered and still serving. Mark it; do not hide it (#296)."""
    orch = _orch(tmp_path)
    marked = _alias_row(orch, "chat-only")
    assert marked["capability_note"]
    assert marked["capability_note"] == tool_capability_note(["chat"], "chat-only")
    assert marked["serving"] is True
    assert marked["problem"] is None
    assert _alias_row(orch, "toolful")["capability_note"] is None


def test_the_composer_rows_carry_the_mark_so_it_is_visible_at_pick_time(tmp_path):
    """Manage is not the only place a model is chosen, and it is not where most picks happen.

    Computed on read like the two effort lists beside it, never written to the membership file: the
    note is derived from `capabilities`, which IS persisted and can outlive a provider changing its
    mind — which is exactly what happened to GLM 5.3 while this ticket was open.
    """
    orch = _orch(tmp_path)
    orch.add_project_resource({"id": "id-chatonly", "kind": "model_llm", "name": "Chat-only model",
                               "alias": "chat-only", "capabilities": ["chat"]})
    orch.add_project_resource({"id": "id-toolful", "kind": "model_llm", "name": "Tool-carrying",
                               "alias": "toolful", "capabilities": ["chat", "tools"]})
    rows = {r["alias"]: r for r in orch.list_project_resources()
            if r.get("kind") == "model_llm"}
    assert rows["chat-only"]["capability_note"]
    assert rows["chat-only"]["capability_note"] == tool_capability_note(["chat"], "chat-only")
    assert rows["toolful"]["capability_note"] is None


# ---- the plant: marking must not become refusing ------------------------------------------------


def test_a_pick_of_an_unmarked_model_still_succeeds(tmp_path, caplog):
    """THE PLANT. A pick that raises is a FAILURE of this test, not a pass.

    The guard beside this one raises for an embeddings-only model, so `set_chat_pick` is a place a
    later reader will reach for when asked to "stop people choosing models that cannot use tools".
    That change would, on the metadata measured for this ticket, refuse Sonnet 5 and Gemini — both
    of which work — and leave nobody able to report that the list is wrong.
    """
    orch = _orch(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="sage.orchestrator.service"):
        orch.set_chat_pick("chat-only", None)
    assert orch.project().control.snapshot().chat_model == "chat-only"
    # And it is on the record, at WARNING, naming the model. This is the one moment the model
    # somebody chose and the slot they chose it for are both in hand, which no later panel read can
    # reconstruct for a session that has ended.
    picked = [r for r in caplog.records
              if r.levelno == logging.WARNING and "chat pick" in r.getMessage()]
    assert len(picked) == 1, [r.getMessage() for r in caplog.records]
    assert "chat-only" in picked[0].args


def test_a_pick_of_a_tool_carrying_model_says_nothing(tmp_path, caplog):
    """The second plant. A warning on every pick buries the one worth reading."""
    orch = _orch(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="sage.orchestrator.service"):
        orch.set_chat_pick("toolful", None)
    assert not [r for r in caplog.records if "chat pick" in r.getMessage()]


def test_the_embeddings_guard_beside_it_still_refuses(tmp_path):
    """The mark did not soften the one rule that was always a refusal."""
    orch = _orch(tmp_path)
    orch._resources = FakeResourceProvider(
        [*ALIASES, LlmAlias("id-embed", "embed-3", "Embeddings", None, ["embeddings"], {})])
    with pytest.raises(ValueError, match="not a chat model"):
        orch.set_chat_pick("embed-3", None)
