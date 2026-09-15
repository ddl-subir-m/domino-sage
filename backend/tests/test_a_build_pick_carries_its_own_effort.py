"""#295 / ADR-0049: the Build model menu's pick carries a level, and that level reaches the turn.

#283 shipped the assignments drawer's half — a level per slot, persisted with the Project. This is
the transient half: the override on the Build chip, which lives in one Sage Builder and dies with
it, and which until now could name a model and nothing else.

The deliverable is not the submenu. A submenu that draws correctly and sends nothing passes a
rendering test and fails the ticket, so what is pinned here is the path a level travels:

    POST /api/project/model → ModelControl.pick → SessionState.picked_effort
      → llm_router PLAN_OVERRIDE/IMPLEMENT_OVERRIDE → ModelDecision.effort
      → EnforcementShim → the `reasoning_effort` on the wire

and, coming back, `status()` → the browser, which is the only thing a reload can restore the
control's selection from.

The menu's own half — which rows offer levels and which stay plain — is in
`test_build_says_which_model_it_will_run.py`, beside the rest of that control.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage.feedback.runner import FeedbackReport
from sage.gateway.client import FakeGatewayClient
from sage.orchestrator import app as appmod
from sage.orchestrator.service import Orchestrator
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.resources.provider import alias_reasoning_efforts as _alias_efforts
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import FakeOpenCode, Turn
from .test_build_says_which_model_it_will_run import _children
from .test_build_says_which_model_it_will_run import _drawn as _composer_drawn
from .test_the_model_panel_lets_a_person_choose import _drawn as _drawer_drawn
from .test_the_model_panel_lets_a_person_choose import _effort_row

# Real alias names, for the reason `test_an_effort_follows_the_model_that_runs` gives: the tables
# that decide whether a level survives to the wire are keyed on the name the gateway spells, so a
# catalog of placeholders can only ever prove the field is dropped.
#
# `gemini-3.7-flash` is the one probed alias that takes a level alongside tools, which every Build
# turn carries — so it is the only alias on this gateway that can show a Build pick's level
# arriving rather than being dropped on the way (ADR-0049's measured table).
CATALOG = ModelCatalog(
    sovereign_plan="sovereign-8b",
    sovereign_implement="sovereign-8b",
    sovereign_ask="sovereign-8b",
    plan="gpt-5.4",
    implement="sonnet",
    ask="gpt-5.4",
)

# Real measured levels rather than a hand-written list: `alias_reasoning_efforts` is what the live
# provider fills this field with, so a fixture that hardcoded it could drift from the table these
# tests are about. Gemini carries levels and sonnet carries none, which is the pair that tells
# "this alias offers nothing" apart from "nobody said".
ALIASES = [
    LlmAlias("id-gemini", "gemini-3.7-flash", "Gemini 3.7 Flash", None, ["chat"], {}, None,
             _alias_efforts("gemini-3.7-flash")),
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {}, None,
             _alias_efforts("sonnet")),
]

TOOLS = [{"function": {"name": "read"}}]


@pytest.mark.parametrize("model", ["gpt-5.4", "gemini-3.7-flash", "sonnet", "unprobed"])
@pytest.mark.parametrize("defaults", [{}, {"reasoning_effort": "medium"},
    {"reasoning_effort": {"enum": ["minimal", "high"]}}])
def test_local_choices_agree_across_three_controls_save_and_send(tmp_path, monkeypatch, model, defaults):
    from sage.resources.provider import join_aliases
    from sage.router.models import REASONING_EFFORTS, reasoning_efforts_with_tools

    client, orch = _client(tmp_path, monkeypatch)
    aliases = join_aliases({model}, [{"id": model, "name": model, "capabilities": ["chat"],
                                    "inference_params": defaults}])
    orch._resources = FakeResourceProvider(aliases)
    listed = orch.list_llm_aliases()
    expected = list(reasoning_efforts_with_tools(model))
    assert aliases[0].reasoning_efforts == list(REASONING_EFFORTS.get(model, ()))
    assert aliases[0].reasoning_efforts_with_tools == expected
    assert listed[0]["reasoning_efforts"] == list(REASONING_EFFORTS.get(model, ()))
    drawer_aliases = orch.model_assignments()["aliases"]
    (drawer,) = _drawer_drawn([{"aliases": drawer_aliases,
                              "seed": {"plan": {"model": model, "effort": None}}}])
    control = _effort_row(drawer, "Plan")
    assert ([o["value"] for o in control["options"][1:]] if control else []) == expected

    browser_rows = [{**a, "alias": a["name"]} for a in listed]
    (chat, build) = _composer_drawn([
        {"mode": "plan", "chat": True, "chatModel": model, "aliases": browser_rows},
        {"mode": "plan", "aliases": browser_rows,
         "slots": {"implement": model}},
    ])
    assert ([i["key"] for i in chat["chatEffortItems"][1:]] if chat["chatEffortItems"] else []) == expected
    children = _children(build, model) or []
    assert [c["key"].split("::", 1)[1] for c in children[1:]] == expected

    # Save has only tool-carrying surfaces; no-tools listing and send retain the wider choices.
    for effort in [None, *REASONING_EFFORTS.get(model, ()), "minimal"]:
        if effort is None or effort in expected:
            _post(client, pick=None)
            orch.set_catalog(plan={"model": model, "effort": effort})
            sent = _sent(orch.project().control, orch.project().shim.catalog, tools=TOOLS)
            assert sent["model"] == model
            assert sent.get("reasoning_effort") == effort
            orch.set_chat_pick(model, effort)
            _post(client, pick=model, pick_effort=effort)
            assert orch.project().control.snapshot().picked_effort == effort
            sent = _sent(orch.project().control, orch.project().shim.catalog, tools=TOOLS)
            assert sent["model"] == model
            assert sent.get("reasoning_effort") == effort
            chat_control = orch.project().control
            token = chat_control.arm_chat("test-local-choices")
            try:
                # Chat's existing low floor still applies when the person selected no override.
                chat_expected = effort if effort is not None else ("low" if "low" in expected else None)
                assert _sent(chat_control, orch.project().shim.catalog, tools=TOOLS).get("reasoning_effort") == chat_expected
            finally:
                chat_control.disarm_chat(token)
        else:
            with pytest.raises(ValueError, match="does not accept"):
                orch.set_catalog(plan={"model": model, "effort": effort})
            with pytest.raises(ValueError, match="invalid reasoning_effort"):
                orch.set_chat_pick(model, effort)
            answer = client.post("/api/project/model", json={"pick": model, "pick_effort": effort})
            assert answer.status_code == 400
            assert "does not accept the reasoning effort" in answer.json()["error"]
        control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
        control.pick(model, effort)
        sent = _sent(control, CATALOG)
        assert sent.get("reasoning_effort") == (effort if effort in REASONING_EFFORTS.get(model, ()) else None)


@pytest.mark.parametrize("echo_effort", [False, True])
def test_a_shared_measured_effort_survives_a_cross_model_assignment(tmp_path, monkeypatch, echo_effort):
    from sage.router import models

    # The shipped tool rows are disjoint; inject a second measured row to test the shared-level case.
    alias = "another-measured-alias"
    monkeypatch.setitem(models.REASONING_EFFORTS, alias, ("max",))
    monkeypatch.setitem(models.EFFORTS_WITH_TOOLS, alias, ("max",))
    client, orch = _client(tmp_path, monkeypatch)
    _post(client, pick=None)
    orch.set_catalog(plan={"model": "gemini-3.7-flash", "effort": "max"})
    orch.set_catalog(plan={"model": alias, "effort": "max"} if echo_effort else alias)
    catalog = orch.project().shim.catalog
    assert (catalog.plan, catalog.plan_effort) == (alias, "max")
    assert orch.project().record.read_catalog_overrides()["plan"] == {"model": alias, "effort": "max"}
    sent = _sent(orch.project().control, catalog, tools=TOOLS)
    assert sent["model"] == alias
    assert sent["reasoning_effort"] == "max"


@pytest.mark.parametrize("kind", ["llm_alias", "model_llm"])
def test_persisted_choices_are_resolved_locally_without_losing_the_model(tmp_path, monkeypatch, kind):
    from sage.resources.provider import join_aliases

    client, orch = _client(tmp_path, monkeypatch)
    orch._resources = FakeResourceProvider(join_aliases({"gpt-5.4"}, [
        {"id": "gpt", "name": "gpt-5.4", "capabilities": ["chat"], "inference_params": {}}]))
    _post(client, pick=None)
    orch.set_catalog(plan={"model": "gpt-5.4", "effort": "none"})
    orch.set_chat_pick("gpt-5.4", "none")
    assert _sent(orch.project().control, orch.project().shim.catalog, tools=TOOLS)["reasoning_effort"] == "none"
    orch.add_project_resource({"id": "llm_alias:stale", "kind": kind, "name": "GPT",
        "alias": "gpt-5.4", "capabilities": ["chat"],
        "reasoning_efforts": ["high"], "reasoning_efforts_with_tools": ["high"]})
    from sage.router import models
    monkeypatch.setitem(models.REASONING_EFFORTS, "gpt-5.4", ())
    monkeypatch.setitem(models.EFFORTS_WITH_TOOLS, "gpt-5.4", ())
    # The persisted producer must work even when the alias listing is unavailable.
    def offline():
        raise AssertionError("persisted choices must not read the gateway")
    monkeypatch.setattr(orch._resources, "list_llm_aliases", offline)
    (row,) = orch.list_project_resources()
    assert row["alias"] == "gpt-5.4"
    assert row["capabilities"] == ["chat"]
    assert row["reasoning_efforts"] == row["reasoning_efforts_with_tools"] == []
    (chat,) = _composer_drawn([{"mode": "plan", "chat": True, "chatModel": row["alias"],
                               "chatEffort": "none", "aliases": [row]}])
    assert [i["key"] for i in chat["chatEffortItems"]] == ["default", "__stranded__"]
    assert chat["chatEffortItems"][-1]["disabled"] is True
    assert "not accepted" in chat["chatEffortLabel"]
    assert "reasoning_effort" not in _sent(orch.project().control, orch.project().shim.catalog, tools=TOOLS)
    token = orch.project().control.arm_chat("narrowed-effort")
    try:
        assert "reasoning_effort" not in _sent(orch.project().control, orch.project().shim.catalog, tools=TOOLS)
    finally:
        orch.project().control.disarm_chat(token)
    assert orch.set_catalog(plan={"model": "gpt-5.4"}).plan_effort is None
    assert orch.project().record.read_project_resources()[0]["reasoning_efforts_with_tools"] == ["high"]


@pytest.mark.parametrize("effort", ["high", "default"])
def test_stale_chat_effort_is_visible_as_unavailable(tmp_path, monkeypatch, effort):
    _, orch = _client(tmp_path, monkeypatch)
    orch._resources = FakeResourceProvider([LlmAlias("gpt", "gpt-5.4", "GPT", None, ["chat"], {})])
    rows = [{**a, "alias": a["name"]} for a in orch.list_llm_aliases()]
    (chat,) = _composer_drawn([{"mode": "plan", "chat": True, "chatModel": "gpt-5.4",
                              "chatEffort": effort, "aliases": rows}])
    assert [i["key"] for i in chat["chatEffortItems"]] == ["default", "none", "__stranded__"]
    assert chat["chatEffortItems"][-1]["disabled"] is True
    assert chat["chatEffortItems"][-1]["label"].endswith(" — not accepted")
    assert "not accepted" in chat["chatEffortLabel"]
    control = orch.project().control
    control.pick_chat("gpt-5.4", effort)
    token = control.arm_chat("stale-effort")
    try:
        assert "reasoning_effort" not in _sent(control, CATALOG, tools=TOOLS)
    finally:
        control.disarm_chat(token)


def test_local_save_validation_does_not_read_gateway_aliases(tmp_path, monkeypatch):
    client, orch = _client(tmp_path, monkeypatch)
    def offline():
        raise AssertionError("local save validation must not read the gateway")
    monkeypatch.setattr(orch._resources, "list_llm_aliases", offline)
    assert orch.set_catalog(plan={"model": "gpt-5.4", "effort": "none"}).plan_effort == "none"
    with pytest.raises(ValueError, match="does not accept"):
        orch.set_catalog(plan={"effort": "high"})
    assert client.post("/api/project/model", json={"pick": "gpt-5.4", "pick_effort": "none"}).status_code == 200
    assert client.post("/api/project/model", json={"pick": "gpt-5.4", "pick_effort": "high"}).status_code == 400


def _sent(control: ModelControl, catalog: ModelCatalog, **request) -> dict:
    """What the shim actually puts on the wire for one request. The same helper #282 uses, because
    the question is the same one: not what the router decided, but what the gateway was told."""
    gw = FakeGatewayClient()
    body = {"model": "opencode-default", "messages": [], **request}
    list(EnforcementShim(control, catalog, gw).handle(body, project="p"))
    return gw.seen[-1][0]


def _template(tmp: Path) -> Path:
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    (template / "AGENTS.md").write_text("# agents")
    return template


def _client(tmp: Path, monkeypatch) -> tuple[TestClient, Orchestrator]:
    orch = Orchestrator(
        workspace_dir=tmp / "mnt" / "code",
        template=_template(tmp),
        gateway=FakeGatewayClient(),
        catalog=CATALOG,
        project_id="Sage",
        resources=FakeResourceProvider(list(ALIASES)),
    )
    orch.project(start_preview=False)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app), orch


def _post(client: TestClient, **body) -> dict:
    answer = client.post("/api/project/model", json={"mode": "plan", "phase": "plan", **body})
    assert answer.status_code == 200, answer.text
    return answer.json()["model"]


# ---------------------------------------------------------------- the route carries both halves


def test_the_route_takes_a_level_beside_the_pick(tmp_path: Path, monkeypatch):
    """Criterion 3's first link. The control sends one body with both halves, because they are one
    act — so the route reads `pick_effort` from the same request that set `pick`."""
    client, orch = _client(tmp_path, monkeypatch)

    _post(client, pick="gemini-3.7-flash", pick_effort="high")

    state = orch.project().control.snapshot()
    assert state.picked_model == "gemini-3.7-flash"
    assert state.picked_effort == "high"


def test_a_pick_with_no_level_leaves_the_alias_on_its_own_default(tmp_path: Path, monkeypatch):
    """Every pick made before this shipped, and every pick of an alias that advertises no levels.
    `None` is not "fall back to the slot's" — that is a number chosen for a model the person just
    moved off, which is the stale pairing ADR-0049 is about."""
    client, orch = _client(tmp_path, monkeypatch)

    _post(client, pick="gemini-3.7-flash")

    assert orch.project().control.snapshot().picked_effort is None


def test_clearing_the_pick_clears_the_level_with_it(tmp_path: Path, monkeypatch):
    """A level with no model under it belongs to nothing. Cleared in `ModelControl.pick` rather
    than by each caller, so the pair cannot come apart at a door nobody looked at — and the way
    back is the slot, whose own assigned effort then applies."""
    client, orch = _client(tmp_path, monkeypatch)
    _post(client, pick="gemini-3.7-flash", pick_effort="high")

    _post(client, pick=None)

    state = orch.project().control.snapshot()
    assert state.picked_model is None
    assert state.picked_effort is None


def test_the_status_reports_the_level_beside_the_model(tmp_path: Path, monkeypatch):
    """Criterion 2. The status poll is the only thing the browser restores the control's selection
    from after a reload, so a level it does not carry is a setting that looks dropped on every
    refresh — which is the failure this ticket exists to prevent, one field further along."""
    client, _orch = _client(tmp_path, monkeypatch)

    model = _post(client, pick="gemini-3.7-flash", pick_effort="high")
    assert model["picked_model"] == "gemini-3.7-flash"
    assert model["picked_effort"] == "high"

    # And on a plain read, which is what the poll actually calls — not only on the write's answer.
    # A route that echoed the write and forgot on the next GET would pass the first assertion and
    # still lose the mark on every reload.
    polled = client.get("/api/project").json()["model"]
    assert polled["picked_model"] == "gemini-3.7-flash"
    assert polled["picked_effort"] == "high"


def test_an_unknown_level_is_refused_locally_at_the_route(tmp_path: Path, monkeypatch):
    """Direct callers get the same local validation as the tool-carrying menu (#284, #298)."""
    client, orch = _client(tmp_path, monkeypatch)

    answer = client.post("/api/project/model", json={"pick": "sonnet", "pick_effort": "not-a-level"})
    assert answer.status_code == 400
    assert "it accepts no effort" in answer.json()["error"]
    assert orch.project().control.snapshot().picked_effort is None


# ---------------------------------------------------------------- what the menu may offer


def test_an_alias_publishes_both_effort_lists_and_the_narrow_one_is_never_wider():
    """The server answers two questions because two are asked (#295, ADR-0049): which levels this
    alias keeps without tools, and which it keeps beside tools. Chat and Build read the second.

    Derived on `LlmAlias` rather than passed in, so the eight places that build one cannot publish a
    narrow list that disagrees with the wide one beside it — and the ninth, added later, gets it for
    free rather than shipping `[]`, which the menu would read as "this alias offers no levels".
    """
    from sage.resources.provider import LlmAlias, alias_reasoning_efforts

    def row(name: str) -> LlmAlias:
        return LlmAlias("id", name, name, None, ["chat"], {}, None,
                        alias_reasoning_efforts(name))

    # The one alias measured to refuse its own advertised levels beside tools.
    gpt = row("gpt-5.4")
    assert gpt.reasoning_efforts == ["none", "low", "medium", "high", "xhigh"]
    assert gpt.reasoning_efforts_with_tools == ["none"]

    # And one that keeps everything, so the narrowing is not simply "always shorter".
    gemini = row("domino/gemini-3.7-flash")
    assert gemini.reasoning_efforts_with_tools == gemini.reasoning_efforts

    # Never wider, for every alias the table knows — the invariant `router/models.py` holds its own
    # two tables to, kept here by construction rather than by a second comparison.
    for name in ("gpt-5.4", "domino/gpt-5.4", "domino/gemini-3.7-flash", "sonnet", "opus"):
        wide, narrow = row(name).reasoning_efforts, row(name).reasoning_efforts_with_tools
        assert set(narrow) <= set(wide), name


def test_an_unprobed_alias_gets_no_sage_effort_control():
    """Unknown aliases get no Sage-selected effort.

    That keeps the Build menu, save validation and send path on one local answer. Passing a gateway
    enum through here would offer levels that the local save path cannot validate.
    """
    from sage.resources.provider import LlmAlias, alias_efforts_with_tools

    published = ["low", "high"]
    assert alias_efforts_with_tools("nobody-probed-this", published) == []

    unprobed = LlmAlias("id", "nobody-probed-this", "N", None, ["chat"], {}, None, published)
    assert unprobed.reasoning_efforts_with_tools == []


def test_the_resources_listing_carries_both_lists(tmp_path: Path, monkeypatch):
    """The payload the Build menu actually reads. A field that exists on the dataclass and never
    reaches `/api/resources` narrows nothing — the browser would fall back to the enum and the chip
    would go on naming a level the shim drops."""
    _client(tmp_path, monkeypatch)
    orch = appmod.orchestrator

    rows = {a["name"]: a for a in orch.list_llm_aliases()}

    assert rows["gemini-3.7-flash"]["reasoning_efforts"] == ["low", "medium", "high", "max"]
    assert rows["gemini-3.7-flash"]["reasoning_efforts_with_tools"] == ["low", "medium", "high", "max"]
    # An alias that advertises none publishes an empty narrow list, not a missing one: over this
    # route the server always answers, and empty is the answer.
    assert rows["sonnet"]["reasoning_efforts_with_tools"] == []


def test_a_membership_row_carries_the_narrow_list_too(tmp_path: Path, monkeypatch):
    """The composer's OTHER alias source. `model_llm` rows come from the Domino listing when it has
    answered and from the project's membership file when it has not — and the second is the state
    the gateway-leg-refused case leaves a Workbench in.

    A field published on one producer and not the other is a feature that disappears on the fallback
    path, and worse: a consumer reading the missing field as an empty list refuses every level the
    alias actually takes. Three sites carry a membership row's fields, and all three had to learn
    this one.
    """
    _, orch = _client(tmp_path, monkeypatch)
    orch.add_project_resource({
        "id": "llm_alias:id-gemini", "kind": "model_llm", "name": "Gemini 3.7 Flash",
        "alias": "gemini-3.7-flash",
        "capabilities": ["chat"],
        "reasoning_efforts": ["low", "medium", "high", "max"],
        "reasoning_efforts_with_tools": ["low", "medium", "high", "max"],
    })

    (row,) = orch.list_project_resources()
    assert row["reasoning_efforts_with_tools"] == ["low", "medium", "high", "max"]


def test_binding_an_alias_joins_it_with_both_effort_lists(tmp_path: Path, monkeypatch):
    """The other door onto a membership row, and the one that does not go through the caller.

    `bind_llm_alias` builds the row from the project's own listing rather than from anything passed
    in — `_join_project_on_bind` writes it, and the catalogue dict is what it keeps. The returned
    value is the BINDING list, which deliberately carries none of these fields; the membership row
    is where they land, so that is what this reads.
    """
    _, orch = _client(tmp_path, monkeypatch)

    orch.bind_llm_alias("id-gemini")

    (row,) = [r for r in orch.list_project_resources() if r["id"] == "llm_alias:id-gemini"]
    assert row["reasoning_efforts"] == ["low", "medium", "high", "max"]
    assert row["reasoning_efforts_with_tools"] == ["low", "medium", "high", "max"]


def test_a_mentioned_alias_joins_with_both_effort_lists_too(tmp_path: Path, monkeypatch):
    """The third door onto a membership row. An `@` mention of a catalogue Resource joins it to the
    project, and `_join_project_on_mention` builds that row through `_MEMBERSHIP_ONLY_FIELDS`.

    Tested because the field list is shared: the same constant that decides what a mention writes
    decides what a chip does NOT carry, and a narrow list missing here produces exactly the defect
    the other two doors had — a row that exists without the field, read by the menu as a refusal of
    every level.
    """
    _, orch = _client(tmp_path, monkeypatch)
    listed = {a["name"]: a for a in orch.list_llm_aliases()}["gemini-3.7-flash"]

    joined = orch._join_project_on_mention({
        # `resourceId`, which is the key a mention row carries — `id` on one of these is the
        # CHIP's own id, and the join reads the Resource's.
        # `llm_alias`, the kind `_MEMBERSHIP_PARENT_KINDS` gates on — the browser's `model_llm` is
        # the row kind one layer up and is not what reaches this door.
        "resourceId": "llm_alias:id-gemini", "kind": "llm_alias", "name": "Gemini 3.7 Flash",
        "alias": listed["name"],
        "capabilities": listed["capabilities"],
        "reasoning_efforts": listed["reasoning_efforts"],
        "reasoning_efforts_with_tools": listed["reasoning_efforts_with_tools"],
    })
    assert joined is True

    (row,) = [r for r in orch.list_project_resources() if r["id"] == "llm_alias:id-gemini"]
    assert row["reasoning_efforts_with_tools"] == ["low", "medium", "high", "max"]


# ---------------------------------------------------------------- and it reaches the turn


def _control(pick: str | None, effort: str | None) -> ModelControl:
    control = ModelControl(mode=Mode.PLAN, phase=Phase.PLAN)
    control.pick(pick, effort)
    return control


def test_the_picked_level_reaches_the_wire(tmp_path: Path, monkeypatch):
    """Criterion 3, at the end of the path. A turn sent under the override is observably different
    from one sent without — the same request, the same model, one extra field.

    Tools on the request because every Build turn carries them, and because that is the shape the
    old guard dropped the field on for every alias (#282). Gemini is the alias measured to keep its
    levels alongside tools, so this is the pairing that can show the difference at all.
    """
    with_level = _sent(_control("gemini-3.7-flash", "high"), CATALOG, tools=TOOLS)
    without = _sent(_control("gemini-3.7-flash", None), CATALOG, tools=TOOLS)

    assert with_level["model"] == without["model"] == "gemini-3.7-flash"
    assert with_level["reasoning_effort"] == "high"
    assert "reasoning_effort" not in without


def test_a_level_the_resolved_alias_refuses_is_dropped_rather_than_sent(tmp_path: Path, monkeypatch):
    """The backstop the route leans on instead of validating. `sonnet` discards the field rather
    than honouring it, so it advertises none and the shim sends none — a 400 here would kill the
    whole turn, and the pick is worth more than the level (`shim/enforcement.py`)."""
    sent = _sent(_control("sonnet", "high"), CATALOG, tools=TOOLS)

    assert sent["model"] == "sonnet"
    assert "reasoning_effort" not in sent


# ---------------------------------------------------------------- and it survives a stall


class _OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


@pytest.fixture
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def test_the_nudge_escalation_gives_the_pick_back_with_its_level(tmp_path: Path, _no_waiting):
    """A turn that writes nothing borrows the pick for the retry, so it has to return both halves.

    This is a door the model menu cannot see. A restore that took only the model would erase the
    level the person chose on the first turn that stalled — silently, for the rest of the session,
    with the chip still reading exactly right. That is this ticket's own defect (a control whose
    setting is discarded without saying so) reached from the inside, which is why it is asserted
    here rather than left to the menu tests.

    The twin of this, on the phased path, is in `test_phased_build.py`; the two escalations are
    different code with the same capture-and-restore shape, and neither covers the other.
    """
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, [
        # Writes nothing, so the loop nudges — and with the strong fallback on, pins catalog.plan.
        Turn(text="Here is what I would do."),
        Turn(text="Built it.", writes={"src/App.tsx": "export default () => null\n"}),
    ])
    orch = Orchestrator(
        workspace_dir=ws,
        template=_template(tmp_path),
        gateway=None,
        catalog=CATALOG,
        project_id="Sage",
        feedback=_OkFeedback(),
        opencode_client=oc,
        resources=FakeResourceProvider(list(ALIASES)),
    )
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.IMPLEMENT)
    # Without this the first turn is swallowed by the plan gate and never reaches the nudge.
    project.record.write_settings({"skip_planning": True})
    project.control.pick("gemini-3.7-flash", "high")

    list(orch.build_stream("add a chart"))

    state = project.control.snapshot()
    assert state.picked_model == "gemini-3.7-flash"
    assert state.picked_effort == "high"


def test_clearing_the_override_returns_the_slot_to_its_assignment_level():
    """Criterion 5. The way back is the slot, and the slot has a level of its own — so clearing the
    override must not leave the mode running at the alias's default.

    This is the one place a "clear" could quietly mean two different things, and the wrong one
    looks identical on screen: the chip reads `(default)` either way.
    """
    catalog = ModelCatalog(
        sovereign_plan="sovereign-8b", sovereign_implement="sovereign-8b",
        sovereign_ask="sovereign-8b",
        plan="gemini-3.7-flash", implement="sonnet", ask="gpt-5.4",
        plan_effort="low",
    )

    assert _sent(_control("gemini-3.7-flash", "high"), catalog,
                 tools=TOOLS)["reasoning_effort"] == "high"

    control = _control("gemini-3.7-flash", "high")
    control.pick(None)

    # Plan's own assignment, not the level that was picked and not none.
    assert _sent(control, catalog, tools=TOOLS)["reasoning_effort"] == "low"
