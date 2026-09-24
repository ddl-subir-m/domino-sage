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
_BODY = ("A desk exposure dashboard for exploring notional by desk, book and trader.\n\n"
         "## Problem & outcome\nExposure is hard to review; the app makes it visible.\n\n"
         "## Who uses this\nThe desk risk analyst.\n\n"
         "## What it does\n- Shows notional by desk\n\n"
         "## Screens\n- **Desk table** — Shows notional by desk.\n\n"
         "## Done when\n- The preview shows the desk table.\n\n"
         "## Plan\n### 1. Desk table\n- Files — src/App.tsx\n"
         "- Do — Show notional by desk.\n- Done when — The preview shows the table.\n")
UNNAMED = _BODY
NAMED = "# Desk Exposure\n\n" + _BODY
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


def _handed_off(tmp: Path, plan_md: str, repair: str | None = None) -> Orchestrator:
    """A plan drafted in Chat and confirmed into a NEW app, which is the door `_open_app` names."""
    turns = [Turn(text="A dashboard, then."), Turn(text=plan_md)]
    if repair is not None:
        turns.append(Turn(text=repair))
    orch, _gateway, _root = _orch(tmp, turns)
    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "build me a desk exposure dashboard"))
    orch.draft_handoff_plan(thread)
    orch.confirm_handoff(thread, NOTHING_EXTRA, {})
    return orch


def _gated(tmp: Path, plan_md: str, repair: str | None = None) -> tuple[Orchestrator, Path, str]:
    """A plan written by the Build gate, so the document is this app's live plan and an edit to it
    is copied back into the `plan.md` the ladder reads."""
    turns = [Turn(text=plan_md)]
    if repair is not None:
        turns.append(Turn(text=repair))
    orch, gateway, root = _orch(tmp, turns)
    gateway.word = "BUILD"
    list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))
    app_id = orch.project(start_preview=False).workspace.app_id
    return orch, root / "apps" / app_id / ".sage" / "plan.md", orch.list_plan_docs()[0]["id"]


# ---- a confirmed handoff ----------------------------------------------------------------------


def test_a_handoff_from_an_unnamed_plan_repairs_the_new_apps_name(tmp_path: Path):
    """The repair pass writes the missing name before `_open_app` stores it on the new app."""
    workspace = _handed_off(tmp_path, UNNAMED, repair="Desk Exposure").project(
        start_preview=False).workspace

    assert workspace.display_name() == "Desk Exposure"
    assert _app_display_name(workspace) == "Desk Exposure"
    assert workspace.read_plan().startswith("# Desk Exposure")


def test_a_handoff_from_a_named_plan_gives_the_new_app_that_name(tmp_path: Path):
    """The other half: a heading IS written, so it is still the app's name, and stored as one."""
    workspace = _handed_off(tmp_path, NAMED).project(start_preview=False).workspace

    assert workspace.display_name() == "Desk Exposure"
    assert _app_display_name(workspace) == "Desk Exposure"


# ---- an edit to the plan page -------------------------------------------------------------------


def test_editing_a_repaired_plan_keeps_the_heading_the_planner_wrote(tmp_path: Path):
    """The round trip keeps the repaired title instead of using the old opening sentence."""
    orch, plan_md, plan_id = _gated(tmp_path, UNNAMED, repair="Desk Exposure")
    assert _app_display_name(orch.project(start_preview=False).workspace) == "Desk Exposure"

    orch.patch_plan_doc(plan_id, {"summary": "A desk exposure dashboard, sorted by date."})

    assert "sorted by date" in plan_md.read_text()
    assert plan_md.read_text().startswith("# Desk Exposure")
    assert _app_display_name(orch.project(start_preview=False).workspace) == "Desk Exposure"


def test_editing_a_named_plan_keeps_the_heading_it_was_given(tmp_path: Path):
    """A written name survives the same round trip, which is what the title is stored for."""
    orch, plan_md, plan_id = _gated(tmp_path, NAMED)

    orch.patch_plan_doc(plan_id, {"summary": "A desk exposure dashboard, sorted by date."})

    assert plan_md.read_text().startswith("# Desk Exposure")
    assert _app_display_name(orch.project(start_preview=False).workspace) == "Desk Exposure"


# ---- and the card still has a face ---------------------------------------------------------------


def test_a_repaired_plan_stores_the_heading_as_its_title(tmp_path: Path):
    """The durable plan document gets the repaired name before the card appears."""
    orch, _plan_md, plan_id = _gated(tmp_path, UNNAMED, repair="Desk Exposure")

    assert orch.read_plan_doc(plan_id)["title"] == "Desk Exposure"
    assert orch.project(start_preview=False).record.read_plan_doc(plan_id)["title"] == "Desk Exposure"
    assert [d["title"] for d in orch.list_plan_docs()] == ["Desk Exposure"]


@pytest.mark.parametrize("repair", [
    Turn(),
    Turn(text="Dashboard"),
    Turn(text="A desk exposure dashboard for traders"),
    Turn(text="Desk Exposure\nHere is your app name."),
    Turn(text="Desk Exposure."),
    Turn(text="[Desk Exposure](https://x)"),
    Turn(text="Desk <em>Exposure</em>"),
    Turn(text="- Desk Exposure"),
    Turn(text="Desk ~~Exposure~~"),
    Turn(text="The Desk Dashboard"),
    Turn(text="A Desk Dashboard"),
    Turn(text="An Exposure Dashboard"),
    Turn(error={"name": "APIError", "data": {"message": "planner unavailable"}}),
])
def test_direct_build_repair_failure_creates_no_plan_card(tmp_path: Path, repair: Turn):
    orch, gateway, _root = _orch(tmp_path, [Turn(text=UNNAMED), repair])
    gateway.word = "BUILD"

    events = list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))

    assert not any(e.get("type") == "plan-proposed" for e in events)
    assert "repair couldn't name it" in next(e for e in events if e["type"] == "error")["message"]
    assert next(e for e in events if e["type"] == "done")["decision"] == "plan title repair failed"
    assert orch.list_plan_docs() == []
    assert orch.project(start_preview=False).workspace.read_plan() is None
    assert len(orch._oc_client.prompts) == 2


@pytest.mark.parametrize("repair", [
    Turn(),
    Turn(text="Dashboard"),
    Turn(text="A desk exposure dashboard for traders"),
    Turn(text="Desk Exposure\nHere is your app name."),
    Turn(text="[Desk Exposure](https://x)"),
    Turn(text="Desk <em>Exposure</em>"),
    Turn(text="- Desk Exposure"),
    Turn(text="Desk ~~Exposure~~"),
    Turn(text="The Desk Dashboard"),
    Turn(text="A Desk Dashboard"),
    Turn(text="An Exposure Dashboard"),
    Turn(error={"name": "APIError", "data": {"message": "planner unavailable"}}),
])
def test_handoff_repair_failure_creates_no_planned_handoff(tmp_path: Path, repair: Turn):
    orch, _gateway, _root = _orch(
        tmp_path,
        [Turn(text="A dashboard, then."), Turn(text=UNNAMED), repair],
    )
    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "build me a desk exposure dashboard"))

    with pytest.raises(ValueError, match="repair couldn't name it"):
        orch.draft_handoff_plan(thread)

    assert (orch.get_thread(thread)["handoff"] or {}).get("status") != "planned"
    assert orch.list_plan_docs() == []
    assert len(orch._oc_client.prompts) == 3


@pytest.mark.parametrize("name", ["Desk Exposure", "Desk Exposure Dashboard", "Desk Risk Exposure Dashboard"])
def test_direct_build_repairs_once_and_preserves_the_existing_plan(tmp_path: Path, name: str):
    plan = "\n" + UNNAMED + "\n"
    orch, gateway, _root = _orch(tmp_path, [Turn(text=plan), Turn(text=name)])
    gateway.word = "BUILD"

    events = list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))

    proposed = next(e for e in events if e["type"] == "plan-proposed")
    assert proposed["plan"] == f"# {name}\n\n" + plan.strip()
    workspace = orch.project(start_preview=False).workspace
    assert orch._app_row(workspace.app_id, workspace.app_id, {})["name"] == name
    prompts = orch._oc_client.prompts
    assert len(prompts) == 2
    assert prompts[1]["agent"] == "sage-plan"
    assert "Write only a 2-4 word app name" in prompts[1]["text"]
    assert plan.strip() in prompts[1]["text"]


def test_handoff_repairs_once_with_the_same_name_only_prompt(tmp_path: Path):
    orch = _handed_off(tmp_path, UNNAMED, repair="Desk Exposure")

    assert orch.project(start_preview=False).workspace.read_plan() == "# Desk Exposure\n\n" + UNNAMED.strip()
    prompts = orch._oc_client.prompts
    assert len(prompts) == 3
    assert prompts[2]["agent"] == "sage-plan"
    assert "Write only a 2-4 word app name" in prompts[2]["text"]
    assert UNNAMED.strip() in prompts[2]["text"]


def test_named_plans_do_not_run_a_repair_pass(tmp_path: Path):
    direct, _plan_md, _plan_id = _gated(tmp_path / "direct", NAMED)
    chat = _handed_off(tmp_path / "chat", NAMED)

    assert len(direct._oc_client.prompts) == 1
    assert len(chat._oc_client.prompts) == 2


@pytest.mark.parametrize("path", ["direct", "handoff"])
def test_a_failed_repair_call_reports_a_planning_error(tmp_path: Path, monkeypatch, path: str):
    turns = [Turn(text=UNNAMED)] if path == "direct" else [
        Turn(text="A dashboard, then."), Turn(text=UNNAMED),
    ]
    orch, gateway, _root = _orch(tmp_path, turns)
    original = orch._run_sage_plan

    def fail_repair(project, prompt, session):
        if "Write only a 2-4 word app name" in prompt:
            raise ValueError("model call failed: planner unavailable")
        return original(project, prompt, session)

    monkeypatch.setattr(orch, "_run_sage_plan", fail_repair)
    if path == "direct":
        gateway.word = "BUILD"
        events = list(orch.build_stream("build me a desk dashboard", conversation=CONVERSATION))
        assert not any(e.get("type") == "plan-proposed" for e in events)
        assert "repair couldn't name it" in next(e for e in events if e["type"] == "error")["message"]
    else:
        thread = orch.create_thread()["id"]
        list(orch.chat_stream(thread, "build me a desk dashboard"))
        with pytest.raises(ValueError, match="repair couldn't name it"):
            orch.draft_handoff_plan(thread)
        assert (orch.get_thread(thread)["handoff"] or {}).get("status") != "planned"
    assert orch.list_plan_docs() == []


def test_a_plan_that_opens_on_a_section_is_not_offered_for_approval(tmp_path):
    shapeless = "## Problem & outcome\n\nNo desk sees its exposure.\n\n## Plan\n1. **A table** — Show it.\n"
    orch, gateway, _root = _orch(tmp_path, [Turn(text=shapeless), Turn(text="Desk Exposure")])
    gateway.word = "BUILD"

    events = list(orch.build_stream(
        "build me a desk exposure dashboard", conversation=CONVERSATION
    ))

    assert orch.list_plan_docs() == []
    assert any(event.get("decision") == "invalid execution plan" for event in events)


def test_a_repaired_plan_archives_with_the_app_heading(tmp_path: Path):
    turns = [
        Turn(text=UNNAMED),
        Turn(text="Desk Exposure"),
        Turn(writes={"src/App.tsx": "export default function App() { return null }\n"}),
    ]
    orch, gateway, _root = _orch(tmp_path, turns)
    gateway.word = "BUILD"
    list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))
    events = list(orch.approve_stream(conversation=CONVERSATION))

    assert next(e for e in events if e["type"] == "done")["ok"] is True
    archived = orch.project(start_preview=False).workspace.read_archived_plan() or ""
    assert archived.startswith("# Desk Exposure")
    workspace = orch.project(start_preview=False).workspace
    assert orch._app_row(workspace.app_id, workspace.app_id, {})["name"] == "Desk Exposure"
