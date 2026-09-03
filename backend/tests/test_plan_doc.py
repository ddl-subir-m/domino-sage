"""The plan document: the durable artifact a plan turn writes.

`.sage/plan.md` is the one-shot handoff a build consumes and archive_plan() moves aside. The
document under `.sage/plan-docs/` is the same plan, kept. The tests that matter most here are the
two that hold that split in place: the round trip, because markdown stays the source of truth and a
lossy parse would silently rewrite people's plans; and the approve test, because a document that
did not survive the build it fed would be no more durable than plan.md.
"""
from __future__ import annotations

from pathlib import Path

from sage.orchestrator.plan_steps import parse_steps
from sage.orchestrator.service import _count_plan_steps, _warn_if_shapeless
from sage.workspace import plan_doc
from sage.workspace.manager import ProjectRecord

PLAN = (
    "A desk exposure dashboard.\n\n"
    "## Problem & outcome\n"
    "Risk cannot see notional by desk before the morning call.\n\n"
    "## Who uses this\n"
    "The desk risk analyst.\n\n"
    "## What it does\n"
    "- Totals notional by desk\n"
    "- Charts the daily move\n\n"
    "## Screens\n"
    "- **Desk table** — one row per desk\n"
    "- **Move chart**\n\n"
    "## Not doing\n"
    "- No trade-level drill-down\n\n"
    "## Done when\n"
    "- The table totals match the source file\n\n"
    "## Plan\n"
    "1. **Desk table** — Show notional by desk.\n"
    "2. **Move chart** — Plot the daily move.\n\n"
    "## Open questions\n"
    "- Which desks count as rates?\n"
)

PHASED = (
    "A desk exposure dashboard.\n\n"
    "## Problem & outcome\n"
    "Risk cannot see notional by desk.\n\n"
    "## Plan\n"
    "### 1. Sample data\n"
    "- Files — src/data/desks.ts\n"
    "- Do — Define the desk fixture.\n"
    "- Done when — The module exports a typed array.\n\n"
    "### 2. Desk table\n"
    "- Files — src/components/DeskTable.tsx\n"
    "- Do — Add the table screen.\n"
    "- Done when — The preview renders the fixture.\n"
    "- Don't touch — src/data/desks.ts\n\n"
    "### 3. Move chart\n"
    "- Files — src/components/MoveChart.tsx\n"
    "- Do — Add the daily move chart.\n"
    "- Done when — The chart renders.\n"
)


def _record(tmp_path: Path) -> ProjectRecord:
    return ProjectRecord("Sage", tmp_path)


# ---- the round trip ----------------------------------------------------------------------------


def test_a_plan_survives_being_read_and_written_back():
    """The contract the whole document rests on. Sections are parsed out of the markdown and
    rendered back into it on every edit, so a lossy pass would quietly rewrite a plan nobody
    touched."""
    once = plan_doc.parse_sections(PLAN)
    rendered = plan_doc.render(once["summary"], once["sections"])
    twice = plan_doc.parse_sections(rendered)
    assert twice["sections"] == once["sections"]
    assert twice["summary"] == once["summary"]
    assert plan_doc.render(twice["summary"], twice["sections"]) == rendered


def test_the_sections_come_out_shaped_not_as_prose():
    parsed = plan_doc.parse_sections(PLAN)["sections"]
    assert parsed["problem"].startswith("Risk cannot see")
    assert parsed["outcomes"] == ["Totals notional by desk", "Charts the daily move"]
    assert parsed["screens"] == [
        {"name": "Desk table", "detail": "one row per desk"},
        {"name": "Move chart", "detail": ""},
    ]
    assert parsed["openQuestions"] == [{"text": "Which desks count as rates?", "resolved": False}]


def test_a_plan_missing_most_of_its_sections_still_parses():
    """Planners drift off the shape they are given. A plan with one heading is a worse document,
    not a failed one, and it still has to open."""
    parsed = plan_doc.parse_sections("Just a dashboard.\n\n## Plan\n1. **Table** — Show it.\n")
    assert parsed["summary"] == "Just a dashboard."
    assert parsed["sections"]["problem"] == ""
    assert parsed["sections"]["outcomes"] == []
    assert "1. **Table**" in parsed["sections"]["plan"]


def test_a_heading_nobody_recognises_is_kept_rather_than_dropped():
    parsed = plan_doc.parse_sections("A dashboard.\n\n## Risks\n- The feed is slow.\n")
    assert "The feed is slow." in parsed["sections"]["plan"]


def test_an_empty_section_is_left_out_instead_of_written_as_a_bare_heading():
    """The reason _drop_empty_questions exists: a heading with nothing under it reads as a section
    the user still has to fill in."""
    rendered = plan_doc.render("A dashboard.", {**plan_doc.empty_sections(), "users": "An analyst."})
    assert "## Who uses this" in rendered
    assert "## Not doing" not in rendered


# ---- what already reads the plan keeps reading it -----------------------------------------------


def test_the_build_steps_still_parse_out_of_a_document():
    """`## Plan` is kept verbatim so the phased executor's parser is untouched by the sections that
    now surround it."""
    parsed = plan_doc.parse_sections(PHASED)
    rendered = plan_doc.render(parsed["summary"], parsed["sections"])
    steps = parse_steps(rendered)
    assert [s.label for s in steps] == ["Sample data", "Desk table", "Move chart"]
    assert steps[1].files == ["src/components/DeskTable.tsx"]
    assert steps[1].dont_touch == ["src/data/desks.ts"]


def test_the_pins_step_count_still_counts_the_plan_and_nothing_else():
    """Bullets under the sections around `## Plan` must not join the count."""
    parsed = plan_doc.parse_sections(PLAN)
    assert _count_plan_steps(plan_doc.render(parsed["summary"], parsed["sections"])) == 2


# ---- storage -------------------------------------------------------------------------------


def test_a_document_is_created_with_its_sections_read_out(tmp_path: Path):
    doc = _record(tmp_path).create_plan_doc(PLAN, title="A desk exposure dashboard.", author="u-me")
    assert doc["id"] == "001"
    assert doc["version"] == 1
    assert doc["status"] == "draft"
    assert doc["author"] == "u-me"
    assert doc["sections"]["users"] == "The desk risk analyst."
    assert (tmp_path / ".sage" / "plan-docs" / "001" / "v001.md").is_file()


def test_an_edit_adds_a_version_and_leaves_the_one_before_it(tmp_path: Path):
    """Someone is reviewing the draft that is being edited. Overwriting it would rewrite the text
    their comments point at."""
    record = _record(tmp_path)
    doc = record.create_plan_doc(PLAN, title="A dashboard")
    sections = {**doc["sections"], "users": "The head of desk."}
    after = record.write_plan_doc_version(doc["id"], plan_doc.render(doc["summary"], sections))
    assert after["version"] == 2
    assert after["sections"]["users"] == "The head of desk."
    first = (tmp_path / ".sage" / "plan-docs" / "001" / "v001.md").read_text()
    assert "The desk risk analyst." in first


def test_a_comment_is_not_a_new_draft(tmp_path: Path):
    record = _record(tmp_path)
    doc = record.create_plan_doc(PLAN, title="A dashboard")
    after = record.patch_plan_doc_meta(doc["id"], status="in_review", reviewers=["u-a"])
    assert after["version"] == 1
    assert after["status"] == "in_review"


def test_an_id_that_could_climb_out_of_the_directory_is_refused(tmp_path: Path):
    record = _record(tmp_path)
    record.create_plan_doc(PLAN, title="A dashboard")
    assert record.read_plan_doc("../../etc/passwd") is None
    assert record.read_plan_doc_markdown("../..") is None
    assert record.patch_plan_doc_meta("../..", status="approved") is None


def test_documents_list_newest_first(tmp_path: Path):
    record = _record(tmp_path)
    record.create_plan_doc(PLAN, title="First")
    record.create_plan_doc(PLAN, title="Second")
    assert [d["id"] for d in record.list_plan_docs()] == ["002", "001"]


def test_a_workspace_with_no_documents_lists_none(tmp_path: Path):
    assert _record(tmp_path).list_plan_docs() == []


# ---- the routes ------------------------------------------------------------------------------


def _routed(tmp_path: Path, monkeypatch):
    """The real orchestrator on a temp workspace, bound to the app the Workbench calls."""
    from fastapi.testclient import TestClient

    import sage.orchestrator.app as appmod
    from sage.gateway.client import FakeGatewayClient
    from sage.orchestrator.service import Orchestrator
    from sage.router.models import ModelCatalog

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
    monkeypatch.setattr(appmod, "orchestrator", orch)
    return TestClient(appmod.control_app), orch


def test_the_plan_page_can_load_a_plan_the_gate_wrote(tmp_path: Path, monkeypatch):
    client, orch = _routed(tmp_path, monkeypatch)
    orch.project(start_preview=False).record.create_plan_doc(PLAN, title="A dashboard")

    doc = client.get("/api/plans/001")
    assert doc.status_code == 200
    assert doc.json()["sections"]["screens"][0]["name"] == "Desk table"

    listed = client.get("/api/plans")
    assert [d["id"] for d in listed.json()["items"]] == ["001"]

    raw = client.get("/api/plans/001/markdown")
    assert raw.json()["path"] == ".sage/plan-docs/001/v001.md"
    assert "## Plan" in raw.json()["content"]


def test_a_plan_that_does_not_exist_is_a_404_not_an_empty_page(tmp_path: Path, monkeypatch):
    client, _ = _routed(tmp_path, monkeypatch)
    assert client.get("/api/plans/404").status_code == 404
    assert client.get("/api/plans/404/markdown").status_code == 404
    assert client.patch("/api/plans/404", json={"sections": {}}).status_code == 404
    assert client.post("/api/plans/404/review", json={"action": "comment"}).status_code == 404


def test_editing_a_section_rewrites_the_file_and_keeps_the_draft_before_it(tmp_path: Path, monkeypatch):
    client, orch = _routed(tmp_path, monkeypatch)
    record = orch.project(start_preview=False).record
    record.create_plan_doc(PLAN, title="A dashboard")

    r = client.patch("/api/plans/001", json={"sections": {"users": "The head of desk."}})

    assert r.status_code == 200
    assert r.json()["version"] == 2
    assert r.json()["sections"]["users"] == "The head of desk."
    # The rest of the plan is untouched by an edit to one section.
    assert r.json()["sections"]["outcomes"] == ["Totals notional by desk", "Charts the daily move"]
    assert "The desk risk analyst." in (record.plan_docs_dir / "001" / "v001.md").read_text()


def test_a_plan_can_be_renamed_without_becoming_a_new_draft(tmp_path: Path, monkeypatch):
    """The title was the one part of a plan nobody could change: the model wrote the first line and
    the page, the transcript's card and the panel's pin all read it. A rename is metadata — the
    body is untouched and no version is added, because a version is what reviewers commented on."""
    client, orch = _routed(tmp_path, monkeypatch)
    record = orch.project(start_preview=False).record
    record.create_plan_doc(PLAN, title="A dashboard")

    r = client.patch("/api/plans/001", json={"title": "Desk exposure"})

    assert r.status_code == 200
    assert r.json()["title"] == "Desk exposure"
    assert r.json()["version"] == 1
    assert list((record.plan_docs_dir / "001").glob("v*.md")) == [
        record.plan_docs_dir / "001" / "v001.md"
    ]
    assert "The desk risk analyst." in (record.plan_docs_dir / "001" / "v001.md").read_text()


def test_the_plan_page_offers_the_rename(tmp_path: Path):
    """The route above has always taken a title; nothing on the page ever sent one. The control is
    the section pencil, on the heading, and it patches the title ALONE — a body in the same call
    would make the rename a new draft."""
    page = (Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"
            / "components" / "plan.js").read_text()
    assert "'Rename plan'" in page
    assert "SW.api.patchPlan(plan.id, { title: next })" in page
    # The pin reads the document once per load, so a rename has to tell it (`store.js`).
    assert "SW.store.reloadProjectPlan()" in page


def test_a_question_can_be_resolved_on_the_page(tmp_path: Path, monkeypatch):
    """Resolving is an edit to the body, so it does make a version — unlike a comment."""
    client, orch = _routed(tmp_path, monkeypatch)
    orch.project(start_preview=False).record.create_plan_doc(PLAN, title="A plan")
    r = client.patch("/api/plans/001", json={
        "sections": {"openQuestions": [{"text": "Which desks count as rates?", "resolved": True}]},
    })
    assert r.json()["sections"]["openQuestions"] == [
        {"text": "Which desks count as rates?", "resolved": True}
    ]


def test_review_records_comments_and_approvals_without_making_a_draft(tmp_path: Path, monkeypatch):
    client, orch = _routed(tmp_path, monkeypatch)
    orch.project(start_preview=False).record.create_plan_doc(PLAN, title="A plan")

    sent = client.post("/api/plans/001/review",
                       json={"action": "request", "reviewers": ["u-a"], "note": "look at screens"})
    assert sent.json()["status"] == "in_review"
    assert sent.json()["reviewers"] == ["u-a"]

    commented = client.post("/api/plans/001/review",
                            json={"action": "comment", "section": "screens", "text": "too many"})
    comment = commented.json()["comments"][0]
    assert comment["section"] == "screens" and comment["resolved"] is False

    resolved = client.post("/api/plans/001/review",
                           json={"action": "resolve", "commentId": comment["id"]})
    assert resolved.json()["comments"][0]["resolved"] is True

    approved = client.post("/api/plans/001/review", json={"action": "approve", "user": "u-a"})
    assert approved.json()["status"] == "approved"
    # None of it wrote a new version: a comment on a plan is not a new draft of that plan.
    assert approved.json()["version"] == 1


def test_approving_twice_is_one_approval(tmp_path: Path, monkeypatch):
    client, orch = _routed(tmp_path, monkeypatch)
    orch.project(start_preview=False).record.create_plan_doc(PLAN, title="A plan")
    client.post("/api/plans/001/review", json={"action": "approve", "user": "u-a"})
    r = client.post("/api/plans/001/review", json={"action": "approve", "user": "u-a"})
    assert len(r.json()["approvals"]) == 1


def test_members_are_empty_rather_than_an_error_when_there_is_no_directory(tmp_path: Path, monkeypatch):
    """Off Domino there are no collaborators to name. The plan page has to open anyway — it shows
    ids where it would show names, which beats a page that will not load.

    The payload grew when the People modal became its second caller, and `connected` is the field
    that keeps the two apart: this is the not-on-the-platform state, not a failed read. That
    distinction is the modal's, and the plan page reads what it always read.
    """
    client, _ = _routed(tmp_path, monkeypatch)
    r = client.get("/api/members")
    assert r.status_code == 200
    assert r.json()["members"] == [] and r.json()["directory"] == []
    assert r.json()["connected"] is False and r.json()["error"] == ""


def test_editing_the_live_plan_reaches_the_build_not_just_the_page(tmp_path: Path, monkeypatch):
    """The document is the source; plan.md is the copy the builder reads. An edit that stopped at
    the page would build the plan as it was before the edit, and the rail would keep counting the
    steps that are no longer there."""
    client, orch = _routed(tmp_path, monkeypatch)
    project = orch.project(start_preview=False)
    project.workspace.write_plan(PLAN)
    project.record.create_plan_doc(PLAN, title="A dashboard")

    client.patch("/api/plans/001", json={"sections": {
        "plan": "1. **Desk table** — Show notional by desk.\n"
                "2. **Move chart** — Plot the daily move.\n"
                "3. **Limit filter** — Show only desks over limit.\n",
    }})

    assert "Limit filter" in project.workspace.read_plan()
    assert client.get("/api/project/plan").json()["steps"] == 3


def test_editing_an_older_plan_does_not_become_the_thing_being_built(tmp_path: Path, monkeypatch):
    """A plan nobody is building must stay that way. Otherwise revisiting last week's plan would
    quietly hand it to the next approve."""
    client, orch = _routed(tmp_path, monkeypatch)
    project = orch.project(start_preview=False)
    project.record.create_plan_doc(PLAN, title="Old")
    project.workspace.write_plan(PLAN)
    project.record.create_plan_doc(PLAN, title="Live")

    client.patch("/api/plans/001", json={"sections": {"users": "Somebody else."}})

    assert "Somebody else." not in project.workspace.read_plan()


def test_an_edit_with_no_live_plan_touches_nothing_else(tmp_path: Path, monkeypatch):
    """After a build has consumed and archived the handoff, editing the document is just editing a
    document — it must not write a new plan.md and put the app back into "waiting for approval"."""
    client, orch = _routed(tmp_path, monkeypatch)
    project = orch.project(start_preview=False)
    project.record.create_plan_doc(PLAN, title="A dashboard")

    client.patch("/api/plans/001", json={"sections": {"users": "The head of desk."}})

    assert project.workspace.read_plan() is None


def test_a_plan_with_no_headings_is_logged(caplog):
    """The one thing nothing noticed. A plan can be perfectly good prose and still leave its
    document with eight empty sections, and until this line nothing anywhere said so."""
    with caplog.at_level("WARNING"):
        _warn_if_shapeless("plan gate", "I'm turning that background work into an app brief.\n")
    assert "plan gate" in caplog.text

    caplog.clear()
    with caplog.at_level("WARNING"):
        _warn_if_shapeless("plan gate", PLAN)
    assert caplog.text == ""
