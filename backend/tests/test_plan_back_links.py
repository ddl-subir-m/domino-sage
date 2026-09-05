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


def _page(*, origin: str = "", app: str = "", origin_live: bool | None = None,
          archived: bool = False) -> dict:
    """The plan on its own page, which is the only place `#/plan/<id>` mounts it."""
    return _mounted(origin=origin, app=app, variant="page", mode="plan",
                    origin_live=origin_live, archived=archived)


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


def _mounted(*, origin: str, app: str, variant: str, mode: str, thread=None,
             origin_live: bool | None = None, archived: bool = False) -> dict:
    """Drives the real component and returns what it drew and where each offer points.

    `variant` and `mode` travel together because the app pairs them: the page is only ever mounted
    on `#/plan/<id>`, and the sheet only from inside Chat or Build. Passing a pair the app cannot
    produce is how a test comes out green about behaviour nobody can reach.

    `originLive` is the server's answer to "does that Conversation still exist" (#167), and it
    follows the origin unless a test says otherwise — which is exactly what the server sends for a
    plan nobody has deleted anything from."""
    plan = {**_PLAN, "originThreadId": origin, "appId": app, "archived": archived,
            "originLive": bool(origin) if origin_live is None else origin_live}
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
    opens it in Build. On its own page there is no mode to read, so it opens in Chat.

    Naming no app, since #139: a Conversation link stopped carrying whichever app happened to be
    open, so this one resolves the app `thr_1` itself bound. The claim here is the mode, and the
    mode is what it always was."""
    assert _sheet_in_build(origin="thr_1", app="app_a")["routed"][0] == "#/build/thr_1"
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


# ---- the conversation that is no longer there (#167) -------------------------------------------
#
# The document outlives the Conversation on purpose (ADR-0007), so all three controls below stay on
# a plan whose origin was deleted. What has to change is what they say and where they lead.


@needs_node
def test_a_plan_whose_conversation_was_deleted_offers_no_way_back_to_it():
    """Criterion 1. The link rendered on a non-empty id alone, so it went on routing to a Thread
    that answers 404 — a way back to nowhere reads as the app being broken."""
    page = _page(origin="thr_1", app="app_a", origin_live=False)

    assert "conversation" not in page["offers"]
    assert "#/chat/thr_1" not in page["routed"]
    # The way into the app is untouched: neither end of a plan's back-link implies the other.
    assert page["routed"] == ["#/build?app=app_a"]


@needs_node
def test_a_plan_whose_conversation_was_deleted_says_so_on_the_button_it_disables():
    """Criterion 1's other half. "Build this" was disabled only by an empty origin, so it was drawn
    live on a dead one and the press raised on the server."""
    page = _page(origin="thr_1", origin_live=False)

    assert page["buildDisabled"] is True
    assert any("was deleted" in t for t in page["tooltips"])


@needs_node
def test_a_plan_that_never_had_a_conversation_still_says_that_instead():
    """Criterion 2. Two states, two sentences: "there never was a conversation" means ask for this
    in one, and "the conversation was deleted" means that door is closed, start a new one. Widening
    the first must not swallow it."""
    page = _page()

    assert page["buildDisabled"] is True
    assert any("no conversation on record" in t for t in page["tooltips"])
    assert not any("deleted" in t for t in page["tooltips"])


# ---- putting the plan away -------------------------------------------------------------------
#
# The act lives here rather than on the panel row: the row is `noMenu: true` on purpose, and
# archiving is a judgement call about a document with approvals on it, so a path that shows you the
# document first is the right amount of friction.


@needs_node
def test_a_plan_can_be_put_away_from_its_own_page():
    """Criterion 6's other end. Before this there was no removal of any kind on a plan document —
    app-scoped `clear_plan_docs` aside — so an unwanted plan was permanent."""
    page = _page(origin="thr_1", app="app_a")

    assert page["archiveLabel"] == "Archive"
    assert page["archived"] == ["archive 001 archived=true"]


@needs_node
def test_an_archived_plan_offers_the_way_back_out_and_not_the_way_in_again():
    """One control, two words. Archive is reversible, and the document itself is where the person
    who wants it back arrives — through the plan card in the Conversation that produced it."""
    page = _page(origin="thr_1", app="app_a", archived=True)

    assert page["archiveLabel"] == "Unarchive"
    assert page["archived"] == ["archive 001 archived=false"]


@needs_node
def test_the_sheet_offers_it_too_because_that_is_where_an_archived_plan_is_reached():
    """Criterion 7 depends on this. The way back to an archived plan is the plan card in the
    Conversation that produced it, and "Open plan" there sets `planViewerId` rather than routing —
    so in Chat that card lands on the SHEET. Withholding the control here would leave Unarchive
    unreachable from the one surface that still shows an archived plan.

    Unlike "Build this again", which stays the full page's alone: that action spends a build and
    clears a review, and this one is metadata."""
    sheet = _sheet_in_chat(origin="thr_1", app="app_a", thread="thr_open")

    assert sheet["archiveLabel"] == "Archive"
    assert sheet["archived"] == ["archive 001 archived=true"]


@needs_node
def test_chat_reads_the_document_and_never_the_file():
    """Chat mounts the same sheet, so the raw file is withheld by the mode and nothing else. The
    ways back are not: Chat is where "Open in Builder" earns its place."""
    sheet = _sheet_in_chat(origin="thr_1", app="app_a", thread="thr_open")

    assert sheet["views"] is None
    assert sheet["raw"] is None
    assert sheet["offers"] == ["conversation", "builder"]
