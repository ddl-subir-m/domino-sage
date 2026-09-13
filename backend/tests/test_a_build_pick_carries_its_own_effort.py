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
from sage.router.model_control import ModelControl
from sage.router.models import Mode, ModelCatalog, Phase
from sage.shim.enforcement import EnforcementShim

from .fake_opencode import FakeOpenCode, Turn

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

ALIASES = [
    LlmAlias("id-gemini", "gemini-3.7-flash", "Gemini 3.7 Flash", None, ["chat"], {}),
    LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {}),
]

TOOLS = [{"function": {"name": "read"}}]


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


def test_an_unknown_level_is_not_refused_at_the_route(tmp_path: Path, monkeypatch):
    """Deliberately unlike `set_chat_pick`, which raises, and unlike `set_catalog`, which refuses.

    Those two write values that OUTLIVE the act: the catalog's lands in
    `.sage/model_overrides.json`, shared with the Project and inherited by the next reader, so a
    bad one is a brick. A Build pick dies with this Sage Builder, and the send path re-checks it
    against the measured table for the alias that actually resolves — see the test below, where
    the level never reaches the wire. Refusing here would cost a gateway listing per pick to reach
    the same answer one layer earlier.
    """
    client, orch = _client(tmp_path, monkeypatch)

    _post(client, pick="sonnet", pick_effort="not-a-level")

    assert orch.project().control.snapshot().picked_effort == "not-a-level"


# ---------------------------------------------------------------- what the menu may offer


def test_an_alias_publishes_both_effort_lists_and_the_narrow_one_is_never_wider():
    """The server answers two questions because two are asked (#295, ADR-0049): which levels this
    alias advertises, and which it keeps when the request also carries function tools. Chat's chip
    reads the first, Build's menu the second, because every Build turn carries tools.

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


def test_an_unprobed_alias_keeps_the_enum_the_gateway_published():
    """The trap in narrowing by intersection. `reasoning_efforts_with_tools(name)` answers the
    whole measured row when an alias has no tool-shape entry — and for an alias nobody probed that
    row is EMPTY, so intersecting against it would hide every level the gateway actually published.

    So the narrowing reads the tool-shape table directly and returns the list untouched where there
    is no row, which is the same default the table itself takes.
    """
    from sage.resources.provider import LlmAlias, alias_efforts_with_tools

    published = ["low", "high"]
    assert alias_efforts_with_tools("nobody-probed-this", published) == published

    unprobed = LlmAlias("id", "nobody-probed-this", "N", None, ["chat"], {}, None, published)
    assert unprobed.reasoning_efforts_with_tools == published


def test_the_resources_listing_carries_both_lists(tmp_path: Path, monkeypatch):
    """The payload the Build menu actually reads. A field that exists on the dataclass and never
    reaches `/api/resources` narrows nothing — the browser would fall back to the enum and the chip
    would go on naming a level the shim drops."""
    _client(tmp_path, monkeypatch)
    orch = appmod.orchestrator

    rows = {a["name"]: a for a in orch.list_llm_aliases()}

    assert rows["gemini-3.7-flash"]["reasoning_efforts_with_tools"] == \
        rows["gemini-3.7-flash"]["reasoning_efforts"]
    assert "reasoning_efforts_with_tools" in rows["sonnet"]


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
