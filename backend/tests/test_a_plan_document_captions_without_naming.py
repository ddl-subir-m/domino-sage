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

The name repair is one direct gateway call, not a second `sage-plan` turn (#555). A weak model on a
second turn followed the agent's system prompt ("produce the plan and nothing else") over the user
message asking for a name, and answered a paragraph; and the nested `arm_read_only` that second
turn took cleared the gated turn's own arming on its way out. So `ScriptedGateway` below answers
two callers by their system prompt: the scope classifier gets `word`, the repair gets `name`.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from sage.gateway.client import GatewayUpstreamError
from sage.orchestrator import handoff, service
from sage.orchestrator.service import _PLAN_NAME_SYSTEM, Orchestrator, _app_display_name
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
# What haiku writes before every tool call, as its own text part ahead of the plan (#555).
NARRATION = "I'll read the current app files to understand the structure before proposing the plan."
NOTHING_EXTRA = {"resources": False, "artifacts": False, "transcript": False}
CONVERSATION = "conv_build"


class OkFeedback:
    def check(self, path: Path):
        from sage.feedback.runner import FeedbackReport
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """Two callers reach this gateway straight, and the request's system prompt says which.

    The scope classifier gets `word` (BUILD or CHAT). The app-name repair sends `_PLAN_NAME_SYSTEM`
    and gets `name`: a string is answered as the model's text, an exception is raised as the
    transport's, and `None` holds the call open until `released` is set — the hung gateway the
    repair's timeout exists for.
    """

    def __init__(self) -> None:
        self.word = "CHAT"
        self.name: str | Exception | None = "Desk Exposure"
        self.requests: list[tuple[dict, object]] = []
        self.released = threading.Event()

    def repair_requests(self) -> list[tuple[dict, object]]:
        return [(request, labels) for request, labels in self.requests
                if _system_prompt(request) == _PLAN_NAME_SYSTEM]

    def route(self, request, labels):
        self.requests.append((request, labels))
        if _system_prompt(request) == _PLAN_NAME_SYSTEM:
            if self.name is None:
                self.released.wait()
                return
            if isinstance(self.name, Exception):
                raise self.name
            answer = self.name
        else:
            answer = self.word
        body = json.dumps({"choices": [{"delta": {"content": answer}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _system_prompt(request: dict) -> str:
    return next((m["content"] for m in request.get("messages", []) if m.get("role") == "system"), "")


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
    orch, gateway, _root = _orch(tmp, [Turn(text="A dashboard, then."), Turn(text=plan_md)])
    if repair is not None:
        gateway.name = repair
    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "build me a desk exposure dashboard"))
    orch.draft_handoff_plan(thread)
    orch.confirm_handoff(thread, NOTHING_EXTRA, {})
    return orch


def _gated(tmp: Path, plan_md: str, repair: str | None = None) -> tuple[Orchestrator, Path, str]:
    """A plan written by the Build gate, so the document is this app's live plan and an edit to it
    is copied back into the `plan.md` the ladder reads."""
    orch, gateway, root = _orch(tmp, [Turn(text=plan_md)])
    gateway.word = "BUILD"
    if repair is not None:
        gateway.name = repair
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


def test_a_handoff_plan_retries_an_invalid_contract_in_a_clean_session(tmp_path: Path):
    malformed = "# Desk Exposure\n\nA dashboard.\n\n## Plan\n1. Add the table.\n"
    orch, _gateway, _root = _orch(
        tmp_path, [Turn(text="A dashboard, then."), Turn(text=malformed), Turn(text=NAMED)])
    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "build me a desk exposure dashboard"))

    result = orch.draft_handoff_plan(thread)

    assert result["plan"].strip() == NAMED.strip()
    client = orch._oc_client
    assert client.interrupted == 1
    assert len(client.sessions) == 2  # The Thread session, then its clean planner recovery.
    recovery_prompt = client.prompts[-1]["text"]
    assert "required execution-plan structure" in recovery_prompt
    assert malformed not in recovery_prompt


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


# Every way a repair answer fails to be a name, plus the transport failing outright. Answers, not
# OpenCode turns: the repair no longer reaches OpenCode at all (#555).
BAD_NAMES = [
    "",
    "Dashboard",
    "A desk exposure dashboard for traders",
    "Desk Exposure\nHere is your app name.",
    "Desk Exposure.",
    "[Desk Exposure](https://x)",
    "Desk <em>Exposure</em>",
    "- Desk Exposure",
    "Desk ~~Exposure~~",
    "The Desk Dashboard",
    "A Desk Dashboard",
    "An Exposure Dashboard",
    GatewayUpstreamError(502, "https://gateway/v1/chat/completions", "planner unavailable"),
]


@pytest.mark.parametrize("repair", BAD_NAMES)
def test_direct_build_repair_failure_creates_no_plan_card(tmp_path: Path, repair):
    orch, gateway, _root = _orch(tmp_path, [Turn(text=UNNAMED)])
    gateway.word = "BUILD"
    gateway.name = repair

    events = list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))

    assert not any(e.get("type") == "plan-proposed" for e in events)
    assert "repair couldn't name it" in next(e for e in events if e["type"] == "error")["message"]
    assert next(e for e in events if e["type"] == "done")["decision"] == "plan title repair failed"
    assert orch.list_plan_docs() == []
    assert orch.project(start_preview=False).workspace.read_plan() is None
    assert len(orch._oc_client.prompts) == 1
    assert len(gateway.repair_requests()) == 1


@pytest.mark.parametrize("repair", BAD_NAMES)
def test_handoff_repair_failure_creates_no_planned_handoff(tmp_path: Path, repair):
    orch, gateway, _root = _orch(tmp_path, [Turn(text="A dashboard, then."), Turn(text=UNNAMED)])
    gateway.name = repair
    thread = orch.create_thread()["id"]
    list(orch.chat_stream(thread, "build me a desk exposure dashboard"))

    with pytest.raises(ValueError, match="repair couldn't name it"):
        orch.draft_handoff_plan(thread)

    assert (orch.get_thread(thread)["handoff"] or {}).get("status") != "planned"
    assert orch.list_plan_docs() == []
    assert len(orch._oc_client.prompts) == 2
    assert len(gateway.repair_requests()) == 1


@pytest.mark.parametrize("name", ["Desk Exposure", "Desk Exposure Dashboard", "Desk Risk Exposure Dashboard"])
def test_direct_build_repairs_once_and_preserves_the_existing_plan(tmp_path: Path, name: str):
    plan = "\n" + UNNAMED + "\n"
    orch, gateway, _root = _orch(tmp_path, [Turn(text=plan)])
    gateway.word = "BUILD"
    gateway.name = name

    events = list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))

    proposed = next(e for e in events if e["type"] == "plan-proposed")
    assert proposed["plan"] == f"# {name}\n\n" + plan.strip()
    workspace = orch.project(start_preview=False).workspace
    assert orch._app_row(workspace.app_id, workspace.app_id, {})["name"] == name
    # One OpenCode turn — the plan. The repair is a gateway call whose system prompt names the
    # task and whose user content is the plan, bounded and tagged as its own cost component.
    assert [p["agent"] for p in orch._oc_client.prompts] == ["sage-plan"]
    (request, labels), = gateway.repair_requests()
    assert request["messages"] == [{"role": "system", "content": _PLAN_NAME_SYSTEM},
                                   {"role": "user", "content": plan.strip()}]
    assert request["max_tokens"] == 32 and request["temperature"] == 0 and request["stream"] is True
    assert (labels.phase, labels.component) == ("plan", "repair")


def test_handoff_repairs_once_with_the_same_name_only_prompt(tmp_path: Path):
    orch = _handed_off(tmp_path, UNNAMED, repair="Desk Exposure")

    assert orch.project(start_preview=False).workspace.read_plan() == "# Desk Exposure\n\n" + UNNAMED.strip()
    assert [p["agent"] for p in orch._oc_client.prompts if p["agent"] == "sage-plan"] == ["sage-plan"]
    (request, _labels), = orch._gateway.repair_requests()
    assert request["messages"][1] == {"role": "user", "content": UNNAMED.strip()}


def test_named_plans_do_not_run_a_repair_pass(tmp_path: Path):
    direct, _plan_md, _plan_id = _gated(tmp_path / "direct", NAMED)
    chat = _handed_off(tmp_path / "chat", NAMED)

    assert len(direct._oc_client.prompts) == 1
    assert len(chat._oc_client.prompts) == 2
    assert direct._gateway.repair_requests() == []
    assert chat._gateway.repair_requests() == []


@pytest.mark.parametrize("path", ["direct", "handoff"])
def test_a_repair_call_that_never_answers_reports_a_planning_error(tmp_path: Path, monkeypatch,
                                                                  path: str):
    """The hung gateway. The wait is bounded, and running out of it is the same failure as a bad
    name: no card, no document, and the sentence that says to send the request again."""
    turns = [Turn(text=UNNAMED)] if path == "direct" else [
        Turn(text="A dashboard, then."), Turn(text=UNNAMED),
    ]
    orch, gateway, _root = _orch(tmp_path, turns)
    gateway.name = None
    monkeypatch.setattr(service, "_PLAN_NAME_TIMEOUT_S", 0.01)
    try:
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
        assert len(gateway.repair_requests()) == 1
    finally:
        gateway.released.set()


def test_a_plan_that_opens_on_a_section_is_not_offered_for_approval(tmp_path):
    shapeless = "## Problem & outcome\n\nNo desk sees its exposure.\n\n## Plan\n1. **A table** — Show it.\n"
    orch, gateway, _root = _orch(tmp_path, [Turn(text=shapeless), Turn(text=shapeless)])
    gateway.word = "BUILD"

    events = list(orch.build_stream(
        "build me a desk exposure dashboard", conversation=CONVERSATION
    ))

    assert orch.list_plan_docs() == []
    assert any(event.get("decision") == "invalid execution plan" for event in events)


def test_a_repaired_plan_archives_with_the_app_heading(tmp_path: Path):
    turns = [
        Turn(text=UNNAMED),
        Turn(writes={"src/App.tsx": "export default function App() { return <main /> }\n"}),
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


# ---- narration ahead of the heading (#555) -----------------------------------------------------


def test_narration_before_the_heading_does_not_hide_the_name(tmp_path: Path):
    """Haiku writes a sentence before every tool call, as its own text part ahead of the plan.
    `parse_sections` takes a `# ` heading as the title only when no prose precedes it, so the
    model's own name was demoted to an unknown heading and a repair ran that it never needed."""
    orch, gateway, _root = _orch(tmp_path, [Turn(prelude=NARRATION, text=NAMED)])
    gateway.word = "BUILD"

    events = list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))

    proposed = next(e for e in events if e["type"] == "plan-proposed")
    assert proposed["plan"] == NAMED.strip()
    doc = orch.read_plan_doc(orch.list_plan_docs()[0]["id"])
    assert doc["title"] == "Desk Exposure"
    assert doc["summary"] == "A desk exposure dashboard for exploring notional by desk, book and trader."
    assert len(orch._oc_client.prompts) == 1
    assert gateway.repair_requests() == []


def test_two_narration_sentences_and_no_heading_repair_once_and_retry_in_plain_words(tmp_path: Path):
    """No heading to find, so the preamble stays (a plan opening on a section is left exactly as
    it is) and the name is asked for once, at the gateway. The narration is then the summary and
    fails the one-sentence rule, and the clean retry is told so in the prompt's own words rather
    than as a validator key. (#555 wrote this case as `planContract` valid; under rule A, which
    drops a preamble only ahead of a `# ` heading, it cannot be — see the WORKER report.)"""
    narration = "I'll read the current app files first. Then I'll propose the plan."
    orch, gateway, _root = _orch(
        tmp_path, [Turn(prelude=narration, text=UNNAMED), Turn(text=NAMED)])
    gateway.word = "BUILD"

    events = list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))

    proposed = next(e for e in events if e["type"] == "plan-proposed")
    assert proposed["plan"] == NAMED.strip()
    assert len(gateway.repair_requests()) == 1
    prompts = orch._oc_client.prompts
    assert [p["agent"] for p in prompts] == ["sage-plan", "sage-plan"]
    assert "Write only a 2-4 word app name" not in prompts[1]["text"]
    assert "exactly one sentence under that heading saying what the app is" in prompts[1]["text"]
    assert "requires: summary" not in prompts[1]["text"]


def test_the_gated_turn_stays_armed_read_only_across_the_repair(tmp_path: Path, monkeypatch):
    """Root 3 of #555. The repair used to be a second `_run_sage_plan`, which armed `plan` again and
    DISARMED in its `finally` — clearing the live token the gated turn was still relying on, so
    every request the clean retry then made carried the implement block and the write tools."""
    seen: list[str] = []
    real = service.validate_execution_contract
    orch, gateway, _root = _orch(tmp_path, [Turn(text=UNNAMED)])
    gateway.word = "BUILD"
    project = orch.project(start_preview=False)

    def record_then_validate(plan_md: str):
        seen.append(project.control.snapshot().read_only_reason)
        return real(plan_md)

    monkeypatch.setattr(service, "validate_execution_contract", record_then_validate)

    events = list(orch.build_stream("build me a desk exposure dashboard", conversation=CONVERSATION))

    assert any(e.get("type") == "plan-proposed" for e in events)
    assert seen == ["plan"]
