"""The plan the rail pins, and the route the panel reads it from.

The pin came in with the prototype shell against a mocked plan API and stayed wired to it, so it
said "No plan yet" beside a plan the transcript was showing. There is no structured plan artifact
behind Sage — there is `.sage/plan.md` and its archive — so this is what the pin gets.
"""
from __future__ import annotations

import re
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


# ---- what a live plan still owes -----------------------------------------------------------------


def test_a_plan_nobody_has_built_from_owes_nothing(tmp_path: Path):
    """The ordinary live plan: written, waiting for approval, no build has touched it. 0 is what
    `read_plan_retry_step` means by "owes none", and the empty state reads it as "waiting"."""
    orch = _orch(tmp_path)
    orch.project(start_preview=False).workspace.write_plan(PLAN)

    assert orch.read_plan_pin()["retryStep"] == 0


def test_the_pin_says_which_step_a_stopped_build_left_the_plan_owing(tmp_path: Path):
    """A plan waiting for its first approval and a plan whose build died look identical on disk, and
    only this tells them apart (#172). The step is carried through so the empty state can say WHY
    the plan is still there — "nobody built it" and "a build stopped at step 4" are different news.

    Deliberately not paired with a total: `steps` counts numbered lines under the Plan heading and
    the phased parser counts briefs, so "step 4 of 3" is a reading this could produce."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    ws.write_plan(PLAN)
    ws.set_plan_retry_step(4)

    assert orch.read_plan_pin()["retryStep"] == 4


def test_a_consumed_plan_owes_nothing(tmp_path: Path):
    """Archiving is what a build that FINISHED does, and it clears the resume point on the way."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    ws.write_plan(PLAN)
    ws.set_plan_retry_step(2)
    ws.archive_plan()

    assert orch.read_plan_pin()["retryStep"] == 0


# ---- the name the pin says -----------------------------------------------------------------------


def _doc_for_the_live_plan(orch: Orchestrator, title: str) -> dict:
    """The document the gate writes beside plan.md, which is what the pin opens.

    Bound to plan.md the way the gate binds it. The pin reads which document its text came from off
    the app rather than off the document list (#176), so a document merely created beside an unbound
    plan.md is nobody's — which is the state of a workspace that predates plan documents.
    """
    project = orch.project(start_preview=False)
    doc = project.record.create_plan_doc(PLAN, title=title, app_id=project.workspace.app_id)
    project.workspace.write_plan(PLAN, doc["id"])
    return doc


def test_the_pin_says_what_the_document_is_called(tmp_path: Path):
    """The pin named the plan by plan.md's first line, which is what the model wrote and nothing
    can edit. The document's title is the one a person can change, so the pin reads that."""
    orch = _orch(tmp_path)
    doc = _doc_for_the_live_plan(orch, "A desk exposure dashboard.")

    orch.patch_plan_doc(doc["id"], {"title": "Desk exposure"})

    assert orch.read_plan_pin()["title"] == "Desk exposure"
    # The markdown behind it is untouched: renaming a document is not a new draft of it.
    assert orch.read_plan_pin()["markdown"].startswith("A desk exposure dashboard.")


def test_a_plan_with_no_document_still_has_a_name(tmp_path: Path):
    """A workspace whose plan predates plan documents has no title but the first line of the file,
    so that stays the fallback rather than the pin going blank."""
    orch = _orch(tmp_path)
    orch.project(start_preview=False).workspace.write_plan(PLAN)

    assert orch.read_plan_pin()["title"] == "A desk exposure dashboard."


def test_renaming_the_app_does_not_rename_its_plan(tmp_path: Path):
    """An app is not its plan. The same title is this document's heading and the transcript's plan
    card, on a document carrying comments and approvals, so a rename of the app leaves it alone."""
    orch = _orch(tmp_path)
    ws = orch.project(start_preview=False).workspace
    _doc_for_the_live_plan(orch, "A desk exposure dashboard.")

    orch.rename_app(ws.app_id, "Desk exposure")

    assert orch.read_plan_pin()["title"] == "A desk exposure dashboard."


def test_the_row_names_the_plan_and_not_the_app(tmp_path: Path):
    """Something has to carry the noun, or the title below reads as this app's name gone stale —
    which is exactly what it looks like once the app has been renamed away from its plan.

    The pinned card carried it in a label reading "Working from a plan". The plan is a row in the
    Project's list now (#151, ADR-0035), so the noun is the group heading over it — and the row's
    own subtitle names the app besides, which the pin never did. Read off the source, because what
    is asserted is that the words exist at all."""
    panel = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
             / "components" / "resource-panel.js").read_text()
    assert "'Plans'" in panel
    # And the row says whose plan it is, which is what lets a Project-scoped list hold an
    # app-scoped artifact honestly.
    assert "appName(plan.appId)" in panel


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
    # `projectPlan`, not `activePlan`: the first shows plan.md and the second is a plan DOCUMENT
    # the viewer has open. They shared one key until plan documents were real, at which point
    # loading a document would have blanked the pin. `activePlanId` is neither — it is the
    # Conversation's plan, which is what Chat marks where Build marks plan.md's — so the check is
    # on the whole word.
    assert "projectPlan" in panel
    assert re.search(r"\bactivePlan\b", panel) is None
    # The list itself is its own state, off `GET /api/plans`, and not a `resourceGroups` entry:
    # `collectTurnRefs` walks every group to resolve an @mention, so a plan filed in one would
    # quietly become a thing a turn could carry.
    assert "plans" in panel


def test_the_pin_calls_a_real_endpoint():
    """`projectPlan` reads plan.md. The `plan`/`planMarkdown`/`createPlan` group beside it reads the
    plan document, and both are real now — the pin keeps its own route because it answers "what is
    this app built from", which outlasts any one document."""
    api = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js" / "api.js").read_text()
    assert "projectPlan: () => request('/project/plan')" in api
    assert "async () => ({})" not in api.split("plans:")[1].split("handoff:")[0]


# ---- the pin's two halves name the same plan (#176) -------------------------------------------
#
# The card is built from a body and a name, and until #176 they were two independent lookups: the
# markdown off the workspace, the title and the link off the newest document naming the app. Nothing
# tied the two, so a document that outranked the one the markdown came from lent it its name.


def test_a_live_plan_reads_the_document_it_was_written_from(tmp_path: Path):
    """Not the newest document naming the app. A plan drafted in Chat after this one was written is
    newer and belongs to nobody here yet, and lending it its name is the whole of #176."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace
    doc = project.record.create_plan_doc(PLAN, title="Desk exposure", app_id=ws.app_id)
    ws.write_plan(PLAN, doc["id"])
    project.record.create_plan_doc("A risk heatmap.\n", title="Risk heatmap", app_id=ws.app_id)

    pin = orch.read_plan_pin()

    assert pin["title"] == "Desk exposure"
    assert pin["planId"] == doc["id"]


def test_an_archived_document_does_not_lend_its_name_to_the_built_plan(tmp_path: Path):
    """The report in #176. Archiving the document of a built plan drops it out of the app's
    candidates, and the markdown on disk is untouched — so the pin drew the built plan's text under
    whatever document was left standing, and its link opened that one."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace
    built = project.record.create_plan_doc(PLAN, title="Desk exposure", app_id=ws.app_id)
    ws.write_plan(PLAN, built["id"])
    ws.archive_plan()
    orch.archive_plan_doc(built["id"], True)
    project.record.create_plan_doc("A risk heatmap.\n", title="Risk heatmap", app_id=ws.app_id)

    pin = orch.read_plan_pin()

    assert pin["markdown"].startswith("A desk exposure dashboard.")
    assert pin["title"] == "Desk exposure"
    assert pin["planId"] == built["id"]


def test_an_archive_written_without_a_document_claims_none(tmp_path: Path):
    """A workspace that predates the marker, or a plan written off a bare CLI turn. The
    correspondence cannot be proven, so the pin names the plan from its own first line and offers no
    link — today's answer for a workspace with no documents at all, rather than the wrong one."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace
    ws.write_plan(PLAN)
    ws.archive_plan()
    project.record.create_plan_doc("A risk heatmap.\n", title="Risk heatmap", app_id=ws.app_id)

    pin = orch.read_plan_pin()

    assert pin["markdown"].startswith("A desk exposure dashboard.")
    assert pin["title"] == "A desk exposure dashboard."
    assert pin["planId"] == ""


def test_the_newest_built_archive_names_its_own_document(tmp_path: Path):
    """Two builds, two documents. `read_archived_plan` returns the newer file, so the marker read
    beside it has to be the newer one's — a marker keyed by anything but the file would drift."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace
    first = project.record.create_plan_doc(PLAN, title="Desk exposure", app_id=ws.app_id)
    ws.write_plan(PLAN, first["id"])
    ws.archive_plan()
    second = project.record.create_plan_doc("A risk heatmap.\n", title="Risk heatmap",
                                            app_id=ws.app_id)
    ws.write_plan("A risk heatmap.\n", second["id"])
    ws.archive_plan()

    pin = orch.read_plan_pin()

    assert pin["markdown"].startswith("A risk heatmap.")
    assert pin["title"] == "Risk heatmap"
    assert pin["planId"] == second["id"]


def test_a_partly_built_plan_a_new_request_replaced_can_still_be_named(tmp_path: Path):
    """Where #175 meets #176. That plan now archives PLAIN, so `read_archived_plan` returns it and
    the pin shows its text — which means the pin has to be able to name it, and can only do so if
    `archive_plan`'s marker gate fired. The gate keys on the filename rather than on the
    `superseded` flag exactly so that it does.

    The document keeps its `superseded` stamp throughout: the pin says what this app was built
    from, the document says what became of the plan, and neither is the other's answer."""
    orch = _orch(tmp_path)
    project = orch.project(start_preview=False)
    ws = project.workspace
    replaced = project.record.create_plan_doc(PLAN, title="Desk exposure", app_id=ws.app_id)
    ws.write_plan(PLAN, replaced["id"])
    ws.set_plan_retry_step(4)                     # phases 1-3 of 6 finished
    ws.archive_plan(superseded=True)
    project.record.patch_plan_doc_meta(replaced["id"], status="superseded")

    pin = orch.read_plan_pin()

    assert pin["markdown"].startswith("A desk exposure dashboard.")
    assert pin["title"] == "Desk exposure"
    assert pin["planId"] == replaced["id"]
    assert pin["status"] == "built"
    assert orch.read_plan_doc(replaced["id"])["status"] == "superseded"
