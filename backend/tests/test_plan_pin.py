"""The plan the rail pins, and the route the panel reads it from.

The pin came in with the prototype shell against a mocked plan API and stayed wired to it, so it
said "No plan yet" beside a plan the transcript was showing. There is no structured plan artifact
behind Sage — there is `.sage/plan.md` and its archive — so this is what the pin gets.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sage.gateway.client import FakeGatewayClient
from sage.orchestrator.service import Orchestrator, _count_plan_steps
from sage.router.models import ModelCatalog

PLAN = (
    "A desk exposure dashboard.\n\n"
    "## Plan\n"
    "1. **Desk table** — Show notional by desk.\n"
    "2. **Chart** — Plot the daily move.\n"
    "3. **Filters** — Narrow by desk and date.\n\n"
    "## Open questions\n"
    "- Which desks count as rates?\n"
)


def _orch(tmp_path: Path) -> Orchestrator:
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("placeholder")
    (template / "package.json").write_text("{}")
    orch = Orchestrator(
        workspace_dir=tmp_path / "mnt" / "code",
        template=template,
        gateway=FakeGatewayClient(),
        catalog=ModelCatalog("sq", "sq", "sq", "p", "i", "a"),
        project_id="Sage",
    )
    orch.project(start_preview=False)
    return orch


# ---- what the pin reads --------------------------------------------------------------------------


def test_no_plan_reads_as_no_plan(tmp_path: Path):
    assert _orch(tmp_path).read_plan_pin() == {}


def test_a_live_plan_is_awaiting_approval(tmp_path: Path):
    orch = _orch(tmp_path)
    orch.project(start_preview=False).workspace.write_plan(PLAN)
    pin = orch.read_plan_pin()
    assert pin["title"] == "A desk exposure dashboard."
    assert pin["status"] == "awaiting"
    assert pin["steps"] == 3
    assert pin["markdown"].startswith("A desk exposure dashboard.")


def test_a_consumed_plan_is_what_the_app_was_built_from(tmp_path: Path):
    """A plan is a one-shot handoff: approving archives it. The app in the preview is still the
    thing it describes, so the pin keeps showing it — as `built`, not `awaiting`."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    ws.write_plan(PLAN)
    ws.archive_plan()
    pin = orch.read_plan_pin()
    assert pin["status"] == "built"
    assert pin["title"] == "A desk exposure dashboard."


def test_the_newest_archived_plan_wins(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    for text in ("First plan.\n", "Second plan.\n", "Third plan.\n"):
        ws.write_plan(text)
        ws.archive_plan()
    assert orch.read_plan_pin()["title"] == "Third plan."


def test_a_dismissed_plan_is_not_what_the_app_was_built_from(tmp_path: Path):
    """Cancel archives the plan too, so the newest archive can be one nobody built. The pin must not
    then say the app is "Working from" it."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    ws.write_plan(PLAN)
    ws.archive_plan()                       # built
    ws.write_plan("A plan nobody built.\n")
    ws.archive_plan(cancelled=True)         # dismissed from the card
    assert orch.read_plan_pin()["title"] == "A desk exposure dashboard."


def test_only_a_cancelled_plan_leaves_the_pin_empty(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    ws.write_plan(PLAN)
    ws.archive_plan(cancelled=True)
    assert orch.read_plan_pin() == {}


def test_the_cancel_route_archives_as_cancelled(tmp_path: Path, monkeypatch):
    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    ws = orch.project(start_preview=False).workspace
    ws.write_plan(PLAN)

    assert TestClient(appmod.control_app).post("/api/project/plan/cancel").json()["archived"] is True
    assert orch.read_plan_pin() == {}
    # Non-destructive, as it always was — git still has it, the pin just does not claim it.
    assert list((ws.path / ".sage" / "plans").glob("*-cancelled.md"))


def test_a_live_plan_outranks_the_archive(tmp_path: Path):
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    ws.write_plan("Old plan.\n")
    ws.archive_plan()
    ws.write_plan("New plan.\n")
    pin = orch.read_plan_pin()
    assert pin["title"] == "New plan."
    assert pin["status"] == "awaiting"


# ---- the step count ------------------------------------------------------------------------------


def test_steps_are_counted_under_the_plan_heading_only():
    """Not `parse_steps`, which is the phased-build parser: it requires a `Do`/`Done when` field per
    step, so an ordinary plan counts zero there. Open questions must not join in either."""
    assert _count_plan_steps(PLAN) == 3
    assert _count_plan_steps("Just a sentence.") == 0
    assert _count_plan_steps("## Plan\n1. One\n\n## Open questions\n1. Not a step\n") == 1


def test_a_bare_numbered_plan_still_counts():
    assert _count_plan_steps("An app.\n\n## Plan\n1. Do a thing\n2) Do another\n") == 2


# ---- through the route ---------------------------------------------------------------------------


def test_the_route_answers_empty_and_answers_the_plan(tmp_path: Path, monkeypatch):
    import sage.orchestrator.app as appmod

    orch = _orch(tmp_path)
    monkeypatch.setattr(appmod, "orchestrator", orch)
    client = TestClient(appmod.control_app)

    assert client.get("/api/project/plan").json() == {}

    orch.project(start_preview=False).workspace.write_plan(PLAN)
    body = client.get("/api/project/plan").json()
    assert body["status"] == "awaiting"
    assert body["steps"] == 3
    assert "Desk table" in body["markdown"]


# ---- the panel is wired to it ---------------------------------------------------------------------


def test_the_panel_reads_the_plan_from_state_not_a_resource_group():
    """`resourceGroups.plan` is a group no backend produces; the pin sat on it and always read
    empty. Anything that still reaches for it is the prototype wiring coming back."""
    panel = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
             / "components" / "resource-panel.js").read_text()
    assert "resourceGroups.plan" not in panel
    # `projectPlan`, not `activePlan`: the pin shows plan.md, and `activePlan` is the plan document.
    # They shared one key until plan documents were real, at which point loading a document would
    # have blanked the pin.
    assert "projectPlan" in panel
    assert "activePlan" not in panel


def test_the_pin_calls_a_real_endpoint():
    """`projectPlan` reads plan.md. The `plan`/`planMarkdown`/`createPlan` group beside it reads the
    plan document, and both are real now — the pin keeps its own route because it answers "what is
    this app built from", which outlasts any one document."""
    api = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "api.js").read_text()
    assert "projectPlan: () => request('/project/plan')" in api
    assert "async () => ({})" not in api.split("plans:")[1].split("handoff:")[0]
