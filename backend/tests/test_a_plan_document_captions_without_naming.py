"""A plan document's title is written or it is empty, and a caption is put on it at the door (#216).

The prompt rung came out of `_app_display_name`, and the sentence walked back in through the plan
document. A document's title was seeded with `plan_title` — the plan's cleaned first line — and a
stored title is not a caption. It becomes a NAME twice over:

  * `_open_app` writes it into the new app's `displayName` when a Chat handoff is confirmed, which
    is a real stored name that outranks every rung below it, and
  * the next edit to the document renders it back as the plan's `# ` heading, which `plan.md` then
    takes a copy of — so the app ladder reads a heading nobody wrote.

Publish sends that string to Domino as the deployed App's name, which is the whole reason #216
exists. So nothing is stored until somebody writes it, and the first line is put back on the way
out, where a card that wants a face still gets one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.orchestrator import handoff
from sage.orchestrator.service import Orchestrator, _app_display_name
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

# The live shape of a plan drafted before the shape asked for a heading: a sentence on line one.
UNNAMED = ("A desk exposure dashboard for exploring notional by desk, book and trader.\n\n"
           "## Plan\n1. **A desk table** — Show notional by desk.\n")
NAMED = ("# Desk Exposure\n\nA desk exposure dashboard.\n\n"
         "## Plan\n1. **A desk table** — Show notional by desk.\n")
# What `plan_title` makes of UNNAMED's first line, and what a card should still show.
CAPTION = "A desk exposure dashboard for exploring notional by desk, book and trader."

NOTHING_EXTRA = {"resources": False, "artifacts": False, "transcript": False}
CONVERSATION = "conv_build"


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """The scope classifier's one answer, switched by the test."""

    def __init__(self) -> None:
        self.word = "CHAT"

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.word}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)
    handoff._health.reset()
    yield
    handoff._health.reset()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building this app\n\nSage's rules go here.\n")
    return t


def _orch(tmp: Path, turns: list[Turn]) -> tuple[Orchestrator, ScriptedGateway, Path]:
    root = tmp / "mnt" / "code"
    gateway = ScriptedGateway()
    orch = Orchestrator(workspace_dir=root, template=_template(tmp), gateway=gateway,
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(),
                        opencode_client=FakeOpenCode(root, turns))
    return orch, gateway, root


def _handed_off(tmp: Path, plan_md: str) -> Orchestrator:
    """A plan drafted in Chat and confirmed into a NEW app, which is the door `_open_app` names."""
    orch, _gateway, _root = _orch(tmp, [Turn(text="A dashboard, then."), Turn(text=plan_md)])
    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "build me a desk exposure dashboard"))
    orch.draft_handoff_plan(thread)
    orch.confirm_handoff(thread, NOTHING_EXTRA, {})
    return orch


def _gated(tmp: Path, plan_md: str) -> tuple[Orchestrator, Path, str]:
    """A plan written by the Build gate, so the document is this app's live plan and an edit to it
    is copied back into the `plan.md` the ladder reads."""
    orch, gateway, root = _orch(tmp, [Turn(text=plan_md)])
    gateway.word = "BUILD"
    list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))
    app_id = orch.project(start_preview=False).workspace.app_id
    return orch, root / "apps" / app_id / ".sage" / "plan.md", orch.list_plan_docs()[0]["id"]


# ---- a confirmed handoff ----------------------------------------------------------------------


def test_a_handoff_from_an_unnamed_plan_leaves_the_new_app_a_placeholder(tmp_path: Path):
    """`_open_app` writes the document's title into `displayName`, which is a stored name and not a
    computed one — so a title seeded from the first line could not even be overtaken by a plan."""
    workspace = _handed_off(tmp_path, UNNAMED).project(start_preview=False).workspace

    assert workspace.display_name() == ""
    assert _app_display_name(workspace) == "Draft app 1"


def test_a_handoff_from_a_named_plan_gives_the_new_app_that_name(tmp_path: Path):
    """The other half: a heading IS written, so it is still the app's name, and stored as one."""
    workspace = _handed_off(tmp_path, NAMED).project(start_preview=False).workspace

    assert workspace.display_name() == "Desk Exposure"
    assert _app_display_name(workspace) == "Desk Exposure"


# ---- an edit to the plan page -------------------------------------------------------------------


def test_editing_an_unnamed_plan_does_not_write_its_first_line_back_as_a_heading(tmp_path: Path):
    """The round trip. Every edit renders the stored title back as `# <title>` and copies the
    markdown into `plan.md`, so a title that was a sentence made the ladder read a heading."""
    orch, plan_md, plan_id = _gated(tmp_path, UNNAMED)
    assert _app_display_name(orch.project(start_preview=False).workspace) == "Draft app 1"

    orch.patch_plan_doc(plan_id, {"summary": "A desk exposure dashboard, sorted by date."})

    assert "sorted by date" in plan_md.read_text()
    assert not any(line.startswith("# ") for line in plan_md.read_text().splitlines())
    assert _app_display_name(orch.project(start_preview=False).workspace) == "Draft app 1"


def test_editing_a_named_plan_keeps_the_heading_it_was_given(tmp_path: Path):
    """A written name survives the same round trip, which is what the title is stored for."""
    orch, plan_md, plan_id = _gated(tmp_path, NAMED)

    orch.patch_plan_doc(plan_id, {"summary": "A desk exposure dashboard, sorted by date."})

    assert plan_md.read_text().startswith("# Desk Exposure")
    assert _app_display_name(orch.project(start_preview=False).workspace) == "Desk Exposure"


# ---- and the card still has a face ---------------------------------------------------------------


def test_an_unnamed_plan_is_still_captioned_by_its_first_line(tmp_path: Path):
    """What the plan page and the plan list draw. The caption is unchanged — it is put on beside the
    title instead of into it, so nothing downstream can mistake it for a name."""
    orch, _plan_md, plan_id = _gated(tmp_path, UNNAMED)

    assert orch.read_plan_doc(plan_id)["caption"] == CAPTION
    assert [d["caption"] for d in orch.list_plan_docs()] == [CAPTION]


def test_a_plan_that_opens_on_a_section_is_captioned_the_way_the_plan_pin_captions_it(tmp_path):
    """The shape drift `_warn_if_shapeless` exists for, and the one input where reading the summary
    and reading the markdown disagree — a plan opening on `## ` leaves the summary empty, and a
    caption read off that says "App" while the plan pin, which reads the markdown, says otherwise.
    One document, two surfaces, and `refreshProjectPlan` sets them in one call so they cannot
    differ."""
    shapeless = "## Problem & outcome\n\nNo desk sees its exposure.\n\n## Plan\n1. **A table** — Show it.\n"
    orch, _plan_md, plan_id = _gated(tmp_path, shapeless)

    assert orch.read_plan_doc(plan_id)["caption"] == "Problem & outcome"
    assert orch.list_plan_docs()[0]["caption"] == "Problem & outcome"


def test_the_title_a_caption_stands_in_for_is_not_stored(tmp_path: Path):
    """The claim the whole change rests on: the record holds no name for a plan nobody named, so
    the two readers that turn a stored title into a real one find nothing to turn — and the rename
    box, which opens on `title`, does not offer a sentence back for one Enter."""
    orch, _plan_md, plan_id = _gated(tmp_path, UNNAMED)
    project = orch.project(start_preview=False)

    assert project.record.read_plan_doc(plan_id)["title"] == ""
    assert orch.read_plan_doc(plan_id)["title"] == ""
