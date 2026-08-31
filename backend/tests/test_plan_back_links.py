"""What the plan page offers as a way back, for each shape a plan can have (#54).

A plan's back-link has two ends and they are independent: the Conversation that produced it, and
the Built App it stands in. Since a Project holds many Built Apps neither end implies the other, so
the page has to read them separately — a plan may carry either, both, or neither. Before this the
page offered one button for both jobs, and pressing it on a plan that already had an app opened a
handoff sheet rather than the app.

Each test drives the real component and reads the route a click asks for, because the way back IS
the route. `test_the_ui_says_what_happened` is the prior art for the harness.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "plan_backlinks_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

_PLAN = {
    "id": "001", "title": "A desk exposure dashboard.", "version": 1, "status": "draft",
    "author": "u-me", "updatedAt": "2026-08-28T10:00:00Z", "summary": "", "sections": {},
    "comments": [], "approvals": [], "reviewers": [],
}

# The app the harness gives Build as the one already in the preview. A plan standing in THIS app
# has nowhere to send you from Build; a plan standing in any other one still does.
_OPEN_APP = "app_open"


def _page(*, origin: str = "", app: str = "") -> dict:
    """The plan on its own page, which is the only place `#/plan/<id>` mounts it."""
    return _mounted(origin=origin, app=app, variant="page", mode="plan")


def _sheet_in_build(*, origin: str = "", app: str = "", thread: str = "") -> dict:
    """The plan sheet beside a Build conversation, which is the other place a plan is read from and
    the one where the conversation behind it is a Build conversation."""
    return _mounted(origin=origin, app=app, variant="side", mode="build",
                    thread={"id": thread} if thread else None)


def _sheet_in_chat(*, origin: str = "", app: str = "", thread: str = "") -> dict:
    """The same sheet, beside a Chat conversation. Chat and Build mount one component with one
    variant, so anything Build's sheet alone offers has to be withheld here on purpose."""
    return _mounted(origin=origin, app=app, variant="side", mode="chat",
                    thread={"id": thread} if thread else None)


def _mounted(*, origin: str, app: str, variant: str, mode: str, thread=None) -> dict:
    """Drives the real component and returns what it drew and where each offer points.

    `variant` and `mode` travel together because the app pairs them: the page is only ever mounted
    on `#/plan/<id>`, and the sheet only from inside Chat or Build. Passing a pair the app cannot
    produce is how a test comes out green about behaviour nobody can reach."""
    plan = {**_PLAN, "originThreadId": origin, "appId": app}
    payload = {"plan": plan, "mode": mode, "variant": variant, "thread": thread}
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps(payload),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_plan_from_a_conversation_offers_the_way_back_to_it():
    """Criterion 4. The plan the Chat handoff wrote: a conversation behind it, no app yet, so the
    other offer is still the handoff that would build it."""
    page = _page(origin="thr_1")

    assert page["offers"] == ["conversation", "build"]
    assert "#/chat/thr_1" in page["routed"]
    assert page["buildDisabled"] is False


@needs_node
def test_a_plan_the_gate_wrote_offers_both_ends_and_they_go_to_different_places():
    """Criteria 4 and 6 together, on the shape this ticket creates. Both ends recorded, two offers,
    and the app one goes to the app rather than back through a handoff sheet."""
    page = _page(origin="thr_1", app="app_a")

    assert page["offers"] == ["conversation", "builder"]
    assert page["routed"] == ["#/chat/thr_1", "#/build?app=app_a"]


@needs_node
def test_the_way_back_to_a_conversation_lands_in_the_mode_you_are_reading_from():
    """The same Thread is a Chat conversation and a Build conversation, and a plan the gate wrote
    came from the Build half, whose turns Chat does not show. Read from Build's sheet, the way back
    opens it in Build. On its own page there is no mode to read, so it opens in Chat."""
    assert _sheet_in_build(origin="thr_1", app="app_a")["routed"][0] == "#/build/thr_1?app=app_open"
    assert _page(origin="thr_1", app="app_a")["routed"][0] == "#/chat/thr_1"


@needs_node
def test_opening_the_built_app_keeps_the_conversation_you_were_reading():
    """Opening the app is a way back INTO something, never a way to lose something. A route naming
    no conversation is how BuildMode is told to start a new one, so it clears the transcript — and
    this offer is read from the sheet sitting beside that very transcript."""
    sheet = _sheet_in_build(origin="thr_1", app="app_a", thread="thr_open")

    assert sheet["routed"][1] == "#/build/thr_open?app=app_a"


@needs_node
def test_a_plan_that_stands_in_an_app_with_no_conversation_still_offers_the_app():
    """Criterion 6's other half. Neither end implies the other, so a missing origin must not take
    the app link with it."""
    page = _page(app="app_a")

    assert page["offers"] == ["builder"]
    assert page["routed"] == ["#/build?app=app_a"]


@needs_node
def test_a_plan_with_neither_end_recorded_says_why_it_offers_no_way_back():
    """Criterion 8. The blank document the plan list hands you came from no conversation, so there
    is nothing to hand off from — and a disabled button that does not say why reads as broken."""
    page = _page()

    assert page["offers"] == ["build"]
    assert page["buildDisabled"] is True
    assert any("no conversation on record" in t for t in page["tooltips"])


@needs_node
def test_a_plan_offers_no_way_into_the_app_you_are_already_looking_at():
    """The sheet in Build stands beside the preview, so a plan whose app IS that preview has
    nowhere to send you and withholds the offer. Nowhere else does: the same plan on its own page
    still makes it, and so does a plan for another app in the same sheet (the test above)."""
    sheet = _sheet_in_build(origin="thr_1", app=_OPEN_APP, thread="thr_open")

    assert sheet["offers"] == ["conversation"]
    assert _page(origin="thr_1", app=_OPEN_APP)["offers"] == ["conversation", "builder"]


@needs_node
def test_build_offers_the_raw_file_behind_the_document():
    """The sheet in Build is the builder's copy: a toggle to the file the preview writes to, and the
    file itself under it. It asked for a variant nothing passed until now, so the tab existed and
    nobody could reach it."""
    sheet = _sheet_in_build(origin="thr_1", app="app_a", thread="thr_open")

    assert sheet["views"] == ["Preview", "Markdown"]
    assert sheet["raw"]["path"] == ".sage/plans/001/v1.md"
    assert "# A desk exposure dashboard." in sheet["raw"]["text"]


@needs_node
def test_chat_reads_the_document_and_never_the_file():
    """Chat mounts the same sheet, so the raw file is withheld by the mode and nothing else. The
    ways back are not: Chat is where "Open in Builder" earns its place."""
    sheet = _sheet_in_chat(origin="thr_1", app="app_a", thread="thr_open")

    assert sheet["views"] is None
    assert sheet["raw"] is None
    assert sheet["offers"] == ["conversation", "builder"]
